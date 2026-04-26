"""
Fix Loop: compile-error-driven iterative repair.

Two-layer approach:
  Layer 1 (Rule Engine): Fast regex-based fixes for common patterns.
  Layer 2 (LLM + RAG):  For errors that need semantic understanding.
                         Re-retrieves context via AgenticRAG so the LLM
                         gets fresh, error-targeted API information.

Usage:
    from core.fix_loop import fix_compile_errors

    fixed_code, success = await fix_compile_errors(
        code=code,
        errors=errors,
        context=rag_context,
        max_retries=3,
        agentic_rag=agentic_rag,
        target_class="Gson",
        method_signature="public <T> T fromJson(String json, Class<T> classOfT)",
    )
"""

import os
import re
import sys
from typing import List, Tuple, Optional, Dict, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import chat


# ============ Error Parsing ============

def parse_compile_errors(raw_output: str) -> List[dict]:
    """Parse Maven/javac compile errors into structured dicts.

    Returns list of:
        {"file": str, "line": int, "col": int, "message": str, "symbol": str|None}
    """
    errors = []
    # Maven format: [ERROR] /path/File.java:[line,col] message
    pattern = re.compile(
        r'\[ERROR\]\s+(.+?\.java):\[(\d+),(\d+)\]\s+(.*)'
    )
    for m in pattern.finditer(raw_output):
        filepath, line, col, msg = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        symbol = None
        # Extract symbol name from "cannot find symbol" errors
        sym_match = re.search(r'symbol:\s+(?:method|variable|class)\s+(\w+)', msg)
        if not sym_match:
            # Look ahead in the raw output for the symbol line
            pos = m.end()
            lookahead = raw_output[pos:pos + 200]
            sym_match = re.search(r'symbol:\s+(?:method|variable|class)\s+(\w+)', lookahead)
        if sym_match:
            symbol = sym_match.group(1)
        errors.append({
            "file": filepath,
            "line": line,
            "col": col,
            "message": msg.strip(),
            "symbol": symbol,
        })

    # javac format: /path/File.java:line: error: message
    if not errors:
        pattern2 = re.compile(
            r'(.+?\.java):(\d+):\s+error:\s+(.*)'
        )
        for m in pattern2.finditer(raw_output):
            filepath, line, msg = m.group(1), int(m.group(2)), m.group(3)
            symbol = None
            sym_match = re.search(r'symbol:\s+(?:method|variable|class)\s+(\w+)', msg)
            if sym_match:
                symbol = sym_match.group(1)
            errors.append({
                "file": filepath,
                "line": line,
                "col": 0,
                "message": msg.strip(),
                "symbol": symbol,
            })

    return errors


def classify_errors(errors: List[dict]) -> dict:
    """Classify errors into categories for targeted fixing.

    Returns dict with keys:
        "package_not_exist": list of package names
        "cannot_find_symbol": list of symbol names
        "unreported_exception": bool
        "incompatible_types": list of error dicts
        "assertThat_mismatch": bool  (Truth/JUnit assertThat type mismatch)
        "not_public": list of class names (package-private access)
        "illegal_character": bool (markdown residue)
        "brace_mismatch": bool
        "other": list of error dicts
    """
    result = {
        "package_not_exist": [],
        "cannot_find_symbol": [],
        "unreported_exception": False,
        "incompatible_types": [],
        "assertThat_mismatch": False,
        "not_public": [],
        "illegal_character": False,
        "brace_mismatch": False,
        "class_name_mismatch": [],   # class X is public, should be in file X.java
        "ambiguous_reference": [],    # reference to X is ambiguous
        "other": [],
    }

    for err in errors:
        msg = err["message"]
        if "package" in msg and "does not exist" in msg:
            pkg_match = re.search(r'package\s+([\w.]+)\s+does not exist', msg)
            if pkg_match:
                result["package_not_exist"].append(pkg_match.group(1))
        elif "cannot find symbol" in msg:
            if err["symbol"]:
                result["cannot_find_symbol"].append(err["symbol"])
            else:
                result["other"].append(err)
        elif "unreported exception" in msg:
            result["unreported_exception"] = True
        elif "incompatible types" in msg:
            result["incompatible_types"].append(err)
        elif "no suitable method found for assertThat" in msg:
            result["assertThat_mismatch"] = True
        elif "is not public" in msg and "cannot be accessed from outside package" in msg:
            # Extract the class name that is not public
            cls_match = re.search(r'(\w+)\s+is not public', msg)
            if cls_match:
                result["not_public"].append(cls_match.group(1))
        elif "illegal character" in msg:
            result["illegal_character"] = True
        elif "class, interface, enum, or record expected" in msg:
            result["brace_mismatch"] = True
        elif "is public, should be declared in a file named" in msg:
            # class GsonTest is public, should be declared in a file named GsonTest.java
            cls_match = re.search(r'class\s+(\w+)\s+is public', msg)
            if cls_match:
                result["class_name_mismatch"].append(cls_match.group(1))
        elif "reference to" in msg and "is ambiguous" in msg:
            result["ambiguous_reference"].append(err)
        else:
            result["other"].append(err)

    return result


# ============ Deterministic symbol/import completion ============

_JAVA_LANG_TYPES: Set[str] = {
    "String", "Object", "Class", "Enum", "Number", "Boolean", "Byte", "Short", "Integer",
    "Long", "Float", "Double", "Character", "Math", "System", "Exception", "RuntimeException",
    "IllegalArgumentException", "IllegalStateException", "UnsupportedOperationException",
    "AssertionError", "Throwable", "Void", "StringBuilder", "StringBuffer"
}

_COMMON_STD_IMPORTS: Dict[str, str] = {
    # java.io
    "BufferedReader": "java.io.BufferedReader",
    "BufferedWriter": "java.io.BufferedWriter",
    "File": "java.io.File",
    "FileInputStream": "java.io.FileInputStream",
    "FileOutputStream": "java.io.FileOutputStream",
    "IOException": "java.io.IOException",
    "InputStream": "java.io.InputStream",
    "OutputStream": "java.io.OutputStream",
    "Reader": "java.io.Reader",
    "StringReader": "java.io.StringReader",
    "StringWriter": "java.io.StringWriter",
    "Writer": "java.io.Writer",
    # java.lang.reflect
    "ParameterizedType": "java.lang.reflect.ParameterizedType",
    "Type": "java.lang.reflect.Type",
    # java.math
    "BigDecimal": "java.math.BigDecimal",
    "BigInteger": "java.math.BigInteger",
    # java.time
    "Duration": "java.time.Duration",
    "Instant": "java.time.Instant",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
    "ZoneId": "java.time.ZoneId",
    # java.util
    "ArrayDeque": "java.util.ArrayDeque",
    "ArrayList": "java.util.ArrayList",
    "Arrays": "java.util.Arrays",
    "Collection": "java.util.Collection",
    "Collections": "java.util.Collections",
    "Comparator": "java.util.Comparator",
    "Deque": "java.util.Deque",
    "HashMap": "java.util.HashMap",
    "HashSet": "java.util.HashSet",
    "Iterator": "java.util.Iterator",
    "LinkedHashMap": "java.util.LinkedHashMap",
    "LinkedHashSet": "java.util.LinkedHashSet",
    "LinkedList": "java.util.LinkedList",
    "List": "java.util.List",
    "Map": "java.util.Map",
    "Objects": "java.util.Objects",
    "Optional": "java.util.Optional",
    "Set": "java.util.Set",
    # java.util.concurrent
    "Callable": "java.util.concurrent.Callable",
    "ExecutorService": "java.util.concurrent.ExecutorService",
    "Executors": "java.util.concurrent.Executors",
    "Future": "java.util.concurrent.Future",
    "TimeUnit": "java.util.concurrent.TimeUnit",
    # java.util.concurrent.atomic
    "AtomicInteger": "java.util.concurrent.atomic.AtomicInteger",
    "AtomicLong": "java.util.concurrent.atomic.AtomicLong",
    # java.util.stream
    "Collectors": "java.util.stream.Collectors",
    "Stream": "java.util.stream.Stream",
    # JUnit 4
    "After": "org.junit.After",
    "Assume": "org.junit.Assume",
    "Before": "org.junit.Before",
    "Ignore": "org.junit.Ignore",
    "Rule": "org.junit.Rule",
    "Test": "org.junit.Test",
    # JUnit 4 rules/runners
    "ExpectedException": "org.junit.rules.ExpectedException",
    "TemporaryFolder": "org.junit.rules.TemporaryFolder",
    "RunWith": "org.junit.runner.RunWith",
    "Parameterized": "org.junit.runners.Parameterized",
}


def _extract_package_name(code: str) -> str:
    m = re.search(r'^\s*package\s+([\w.]+)\s*;', code, re.MULTILINE)
    return m.group(1) if m else ""


def _extract_existing_imports(code: str) -> Set[str]:
    # Only normal imports; static imports are handled separately.
    return set(re.findall(r'^\s*import\s+([\w.]+)\s*;', code, re.MULTILINE))


def _extract_declared_types(code: str) -> Set[str]:
    declared = set(re.findall(
        r'^\s*(?:public\s+|protected\s+|private\s+)?(?:abstract\s+|final\s+)?'
        r'(?:class|interface|enum|record)\s+([A-Z][A-Za-z0-9_]*)\b',
        code,
        re.MULTILINE,
    ))
    return declared


def _collect_symbol_candidates_from_code(code: str) -> List[str]:
    candidates: List[str] = []
    patterns = [
        r'\bnew\s+([A-Z][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\(',
        r'\b([A-Z][A-Za-z0-9_]*)\s*\.',
        r'\b([A-Z][A-Za-z0-9_]*)\s+[a-zA-Z_][A-Za-z0-9_]*\s*(?:=|;|,|\))',
        r'\(\s*([A-Z][A-Za-z0-9_]*)\s*\)',
    ]
    for pat in patterns:
        for sym in re.findall(pat, code):
            if len(sym) > 1:  # skip generic single-letter symbols like T/E
                candidates.append(sym)
    return candidates


def _unwrap_rag_instance(rag_instance):
    if not rag_instance:
        return None
    if hasattr(rag_instance, 'class_info'):
        return rag_instance
    inner = getattr(rag_instance, 'rag', None)
    if inner and hasattr(inner, 'class_info'):
        return inner
    return None


def _resolve_symbol_from_project(symbol: str, rag_instance) -> Optional[str]:
    rag_core = _unwrap_rag_instance(rag_instance)
    if not rag_core:
        return None

    matches: List[str] = []

    # Fast path: class_info key exactly matches the simple name.
    info = rag_core.class_info.get(symbol)
    if info and getattr(info, 'package', None):
        matches.append(f"{info.package}.{symbol}")

    # Fallback: scan by ClassInfo.name and class_info key shape.
    for cls_name, cls_info in rag_core.class_info.items():
        simple = (getattr(cls_info, 'name', None) or cls_name.split('.')[-1]).split('.')[-1]
        if simple != symbol:
            continue

        pkg = getattr(cls_info, 'package', None)
        if pkg:
            matches.append(f"{pkg}.{simple}")
        elif '.' in cls_name:
            matches.append(cls_name)

    uniq = list(dict.fromkeys(matches))
    if len(uniq) == 1:
        return uniq[0]
    return None


def _auto_add_missing_imports(code: str, classified: dict, rag_instance=None) -> Tuple[str, List[str]]:
    package_name = _extract_package_name(code)
    existing_imports = _extract_existing_imports(code)
    declared_types = _extract_declared_types(code)

    # 1) Primary source: compiler-classified missing symbols
    symbols = list(classified.get("cannot_find_symbol", []))

    # 2) Fallback source: infer from code so we still work when compiler output
    # does not expose symbol lines completely.
    symbols.extend(_collect_symbol_candidates_from_code(code))

    # De-duplicate while preserving order
    ordered_symbols = list(dict.fromkeys(symbols))

    fqns_to_add: List[str] = []
    for sym in ordered_symbols:
        if not sym or sym in declared_types or sym in _JAVA_LANG_TYPES:
            continue

        # Already imported with any package
        if any(imp.endswith(f".{sym}") for imp in existing_imports):
            continue

        fqn = _resolve_symbol_from_project(sym, rag_instance)
        if not fqn:
            fqn = _COMMON_STD_IMPORTS.get(sym)

        if not fqn:
            continue

        # Same-package types do not need explicit import
        if package_name and fqn.startswith(package_name + "."):
            continue

        # java.lang does not need import
        if fqn.startswith("java.lang."):
            continue

        # Avoid ambiguous duplicate simple-name imports
        simple = fqn.split('.')[-1]
        if any(imp.endswith(f".{simple}") and imp != fqn for imp in existing_imports):
            continue

        fqns_to_add.append(fqn)

    fqns_to_add = list(dict.fromkeys(fqns_to_add))
    if not fqns_to_add:
        return code, []

    lines = code.split('\n')
    import_insert_pos = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith('import '):
            import_insert_pos = i

    added_statements = []
    if import_insert_pos is not None:
        insert_at = import_insert_pos + 1
        for fqn in fqns_to_add:
            stmt = f"import {fqn};"
            if stmt not in code:
                lines.insert(insert_at, stmt)
                insert_at += 1
                added_statements.append(stmt)
    else:
        pkg_pos = None
        for i, ln in enumerate(lines):
            if ln.strip().startswith('package '):
                pkg_pos = i
                break

        if pkg_pos is not None:
            insert_at = pkg_pos + 1
            for fqn in fqns_to_add:
                stmt = f"import {fqn};"
                if stmt not in code:
                    lines.insert(insert_at, stmt)
                    insert_at += 1
                    added_statements.append(stmt)
        else:
            for fqn in reversed(fqns_to_add):
                stmt = f"import {fqn};"
                if stmt not in code:
                    lines.insert(0, stmt)
                    added_statements.append(stmt)

    return '\n'.join(lines), added_statements


# ============ Layer 1: Rule-based fixes ============

def rule_fix(code: str, classified: dict, rag_instance=None) -> Tuple[str, List[str]]:
    """Apply rule-based fixes. Returns (fixed_code, list_of_fixes_applied)."""
    fixes = []

    # Fix 1: Remove non-existent package imports
    for pkg in classified["package_not_exist"]:
        # Remove the import line
        pattern = re.compile(
            rf'^import\s+(?:static\s+)?{re.escape(pkg)}\..*?;.*$\n?',
            re.MULTILINE
        )
        if pattern.search(code):
            code = pattern.sub('', code)
            fixes.append(f"Removed import for non-existent package: {pkg}")

    # Fix 2: Add throws Exception to test methods missing it
    if classified["unreported_exception"]:
        code = re.sub(
            r'(public\s+void\s+\w+\s*\(\s*\))\s*\{',
            r'\1 throws Exception {',
            code
        )
        # Avoid double throws
        code = re.sub(r'throws\s+\w+\s+throws\s+Exception', 'throws Exception', code)
        fixes.append("Added 'throws Exception' to test methods")

    # Fix 3: Fix brace mismatch
    if classified["brace_mismatch"]:
        open_count = code.count('{')
        close_count = code.count('}')
        if close_count > open_count:
            # Remove trailing extra braces
            lines = code.rstrip().split('\n')
            while lines and lines[-1].strip() == '}' and code.count('}') > code.count('{'):
                lines.pop()
                code = '\n'.join(lines)
            fixes.append(f"Removed {close_count - open_count} extra closing braces")
        elif open_count > close_count:
            code += '\n' * (open_count - close_count) + '}'
            fixes.append(f"Added {open_count - close_count} missing closing braces")

    # Fix 4: Remove markdown residue
    if classified["illegal_character"]:
        code = re.sub(r'^```(?:java)?\s*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'^```\s*$', '', code, flags=re.MULTILINE)
        fixes.append("Removed markdown code fence residue")

    # Fix 5: Convert assertThat(...) to JUnit assertions when assertThat doesn't compile
    if classified.get("assertThat_mismatch"):
        # Use DOTALL + optional whitespace so multi-line fluent assertions are also handled.
        # assertThat(expr).isEqualTo(expected) -> assertEquals(expected, expr)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isEqualTo\((.*?)\)\s*;',
            r'assertEquals(\2, \1);',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).contains(expected) -> assertTrue(expr.contains(expected))
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*contains\((.*?)\)\s*;',
            r'assertTrue(\1.contains(\2));',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).isTrue() -> assertTrue(expr)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isTrue\(\)\s*;',
            r'assertTrue(\1);',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).isFalse() -> assertFalse(expr)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isFalse\(\)\s*;',
            r'assertFalse(\1);',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).isNull() -> assertNull(expr)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isNull\(\)\s*;',
            r'assertNull(\1);',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).isNotNull() -> assertNotNull(expr)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isNotNull\(\)\s*;',
            r'assertNotNull(\1);',
            code,
            flags=re.DOTALL
        )
        # assertThat(expr).isInstanceOf(Cls.class) -> assertTrue(expr instanceof Cls)
        code = re.sub(
            r'assertThat\((.*?)\)\s*\.\s*isInstanceOf\((.*?)\.class\)\s*;',
            r'assertTrue(\1 instanceof \2);',
            code,
            flags=re.DOTALL
        )
        # assertThat(() -> expr).isInstanceOf(Exception.class) -> assertThrows(Exception.class, () -> expr)
        code = re.sub(
            r'assertThat\(\(\)\s*->\s*(.*?)\)\s*\.\s*isInstanceOf\((.*?)\.class\)\s*;',
            r'assertThrows(\2.class, () -> \1);',
            code,
            flags=re.DOTALL
        )
        # Remove remaining assertThat imports that might conflict
        code = re.sub(
            r'^import\s+static\s+com\.google\.common\.truth\.Truth\.assertThat;.*$\n?',
            '',
            code, flags=re.MULTILINE
        )
        code = re.sub(
            r'^import\s+static\s+org\.assertj\.core\.api\.Assertions\.assertThat;.*$\n?',
            '',
            code, flags=re.MULTILINE
        )
        # Ensure JUnit Assert import
        if 'import static org.junit.Assert.*;' not in code:
            code = re.sub(
                r'(^package\s+[\w.]+;)',
                r'\1\nimport static org.junit.Assert.*;',
                code, count=1, flags=re.MULTILINE
            )
        fixes.append("Converted assertThat() fluent assertions to JUnit Assert equivalents")

    # Fix 6: Remove imports/usages of package-private classes
    for cls_name in classified.get("not_public", []):
        # Remove import lines referencing this class
        code = re.sub(
            rf'^import\s+(?:static\s+)?[\w.]*\.{re.escape(cls_name)}[\w.]*;.*$\n?',
            '',
            code, flags=re.MULTILINE
        )
        # Remove static imports of fields/methods from this class
        code = re.sub(
            rf'^import\s+static\s+[\w.]*\.{re.escape(cls_name)}\.\w+;.*$\n?',
            '',
            code, flags=re.MULTILINE
        )
        fixes.append(f"Removed references to package-private class: {cls_name}")

    # Fix 7: For cannot_find_symbol, try removing the offending import lines
    for sym in classified["cannot_find_symbol"]:
        import_pattern = re.compile(
            rf'^import\s+.*\b{re.escape(sym)}\b.*;.*$\n?',
            re.MULTILINE
        )
        if import_pattern.search(code):
            code = import_pattern.sub('', code)
            fixes.append(f"Removed import with unknown symbol: {sym}")

    # Fix 8: Fix class name mismatch (LLM renamed the class)
    for wrong_name in classified.get("class_name_mismatch", []):
        # Find the expected class name from the file — it should match the
        # test_class_name that was requested.  We look for the 'public class'
        # declaration and see if it differs from what the file expects.
        # The rule: replace the wrong class name with the one that appears
        # after 'public class' in the code (which _copy_test_file will rename anyway).
        # But the real fix is: if the code says 'public class GsonTest' but the
        # file is supposed to be Gson_fromJson_1064_Test, rename it back.
        # We can't know the correct name here, but we CAN detect and flag it.
        # For now, just log it — the _copy_test_file already handles renaming.
        fixes.append(f"Detected class name mismatch: {wrong_name} (will be renamed by copy)")

    # Fix 9: Deduplicate imports
    seen = set()
    new_lines = []
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import '):
            if stripped in seen:
                continue
            seen.add(stripped)
        new_lines.append(line)
    if len(new_lines) < len(code.split('\n')):
        code = '\n'.join(new_lines)
        fixes.append("Removed duplicate imports")

    # Fix 10: incompatible_types — fix wrong variable type in assignments
    # e.g. "Gson result = gson.serializeNulls();" when serializeNulls() returns boolean
    for err in classified.get("incompatible_types", []):
        msg = err.get("message", "")
        line_no = err.get("line", 0)
        # Pattern: "incompatible types: boolean cannot be converted to Gson"
        type_match = re.search(
            r'incompatible types:\s+(\S+)\s+cannot be converted to\s+(\S+)', msg
        )
        if type_match and line_no > 0:
            actual_type = type_match.group(1)   # e.g. "boolean"
            wrong_type = type_match.group(2)    # e.g. "Gson"
            lines = code.split('\n')
            if line_no <= len(lines):
                target_line = lines[line_no - 1]
                # Try to fix: "WrongType var = expr;" -> "ActualType var = expr;"
                fixed_line = re.sub(
                    rf'\b{re.escape(wrong_type)}\b(\s+\w+\s*=)',
                    f'{actual_type}\\1',
                    target_line,
                    count=1
                )
                if fixed_line != target_line:
                    lines[line_no - 1] = fixed_line
                    code = '\n'.join(lines)
                    fixes.append(
                        f"Fixed type mismatch at line {line_no}: "
                        f"{wrong_type} -> {actual_type}"
                    )

    # Fix 11: Auto-add missing imports using a generic resolver.
    # Resolution order: project class index -> source scan -> common std/JUnit map.
    code, added_imports = _auto_add_missing_imports(
        code,
        classified,
        rag_instance=rag_instance,
    )
    if added_imports:
        fixes.append(f"Auto-added missing imports: {added_imports}")

    # Fix 12: Resolve 'reference to X is ambiguous' caused by duplicate
    # static imports (e.g. both 'import static Assert.*;' and
    # 'import static Assert.assertEquals;').
    # Strategy: if a wildcard import already covers the symbol, remove the
    # specific import to eliminate the ambiguity.
    if classified.get("ambiguous_reference"):
        # Extract ambiguous symbol names from messages
        ambiguous_syms = set()
        for err in classified["ambiguous_reference"]:
            m = re.search(r'reference to (\w+) is ambiguous', err.get("message", ""))
            if m:
                ambiguous_syms.add(m.group(1))
        if ambiguous_syms:
            # Check which wildcard imports are present
            wildcard_pkgs = re.findall(
                r'^import\s+static\s+([\w.]+)\.\*;', code, re.MULTILINE
            )
            new_lines = []
            removed = []
            for ln in code.split('\n'):
                stripped = ln.strip()
                # Check if this is a specific static import for an ambiguous symbol
                m = re.match(r'^import\s+static\s+([\w.]+)\.(\w+);', stripped)
                if m:
                    pkg, sym = m.group(1), m.group(2)
                    if sym in ambiguous_syms:
                        # Only remove if a wildcard from the same package exists
                        if any(pkg == wp for wp in wildcard_pkgs):
                            removed.append(stripped)
                            continue  # drop this line
                new_lines.append(ln)
            if removed:
                code = '\n'.join(new_lines)
                fixes.append(f"Removed specific static imports to resolve ambiguity: {removed}")

    return code, fixes


# ============ RAG-based symbol resolution ============

def resolve_symbols_from_rag(symbols: List[str], code_rag) -> str:
    """Look up unknown symbols in the RAG class_info to find correct API.

    Args:
        symbols: List of symbol names that caused 'cannot find symbol' errors.
        code_rag: A CodeRAG instance with loaded class_info.

    Returns:
        A string with correct API information for each resolved symbol.
    """
    if not code_rag or not hasattr(code_rag, 'class_info'):
        return ""

    resolved = []
    for sym in set(symbols):
        # Strategy 1: Check if symbol is a class name
        if sym in code_rag.class_info:
            info = code_rag.class_info[sym]
            lines = [f"Class '{sym}' (package: {info.package}):"]
            if info.constructors:
                for c in info.constructors:
                    sig = c.get('signature', sym + '()')
                    lines.append(f"  Constructor: {sig}")
            if info.methods:
                pub_methods = [m for m in info.methods
                               if 'public' in m.get('modifiers', [])]
                if pub_methods:
                    for pm in pub_methods[:15]:
                        ret = pm.get('return_type', '')
                        params = ', '.join(pm.get('params', []))
                        lines.append(f"  public {ret} {pm['name']}({params})")
            resolved.append('\n'.join(lines))
            continue

        # Strategy 2: Check if symbol is a method name in any class
        found_in = []
        for cls_name, info in code_rag.class_info.items():
            for m in info.methods:
                if m['name'] == sym:
                    mods = ' '.join(m.get('modifiers', []))
                    found_in.append(f"  {cls_name}.{sym}: {mods} {m.get('signature', sym + '()')}")
            for c in info.constructors:
                if c['name'] == sym:
                    mods = ' '.join(c.get('modifiers', []))
                    found_in.append(f"  {cls_name} constructor: {mods} {c.get('signature', sym + '()')}")
        if found_in:
            resolved.append(f"Symbol '{sym}' found in:\n" + '\n'.join(found_in[:5]))

    return '\n\n'.join(resolved) if resolved else ""


# ============ Layer 2: LLM-based fix ============

_FIX_PROMPT = """You are a Java test code fixer. The following test code failed to compile.

## Compile Errors
{errors}

## Current Code
```java
{code}
```

## Correct API Information (from project source code)
{api_info}

## Additional Context
{context}

## Instructions
Fix ALL compile errors in the code above. Rules:
1. Fix every error listed above — do not leave any error unaddressed.
2. If a method/class does not exist, either remove the test that uses it or replace with the correct API from the API information above.
3. Do NOT use org.assertj. Use org.junit.Assert (assertEquals, assertTrue, assertThrows, etc.).
4. Do NOT use private helper methods from other test classes.
5. Do NOT access package-private or internal classes. Only use public API.
6. Ensure all test methods declare 'throws Exception' if they call methods that throw checked exceptions.
7. Pay attention to constructor signatures — use the exact parameters shown in the API information.
8. If you are unsure about an API, remove the test case rather than guessing.
9. Return ONLY the complete fixed Java file, no explanations.

```java
"""


async def llm_fix(code: str, errors: List[dict], context: str = "",
                  api_info: str = "") -> str:
    """Use LLM to fix compile errors that rules can't handle."""
    error_text = "\n".join(
        f"Line {e['line']}: {e['message']}" + (f" (symbol: {e['symbol']})" if e['symbol'] else "")
        for e in errors[:10]  # Limit to 10 errors
    )

    prompt = _FIX_PROMPT.format(
        errors=error_text,
        code=code,
        api_info=api_info or "No API information available.",
        context=context[:3000] if context else "No additional context.",
    )

    resp = await chat(prompt, temperature=0.3, max_tokens=4000)

    # ── Extract Java code from LLM response (robust parser) ──
    # 1) Preferred: full fenced code block ```java ... ```
    matches = re.findall(r"```(?:java|Java|JAVA)?\s*\n(.*?)```", resp, re.DOTALL)
    if matches:
        # take the longest block (the fixed version should be the full file)
        extracted = max(matches, key=len).strip()
    else:
        # 2) Unclosed/malformed fence: take everything after the first ```
        m = re.search(r"```(?:java|Java|JAVA)?\s*\n(.*)", resp, re.DOTALL)
        if m:
            extracted = m.group(1).strip()
            # strip any trailing stray backticks
            extracted = re.sub(r"`+\s*$", "", extracted).strip()
        else:
            extracted = resp.strip()

    # 3) Sanity check: if we somehow got markdown/backticks in output,
    # drop everything before the first 'package' or 'import' keyword.
    if "`" in extracted or extracted.lstrip().startswith(("#", "Here", "The ", "I ", "This ")):
        pkg_idx = extracted.find("package ")
        imp_idx = extracted.find("import ")
        cls_idx = extracted.find("class ")
        candidates = [i for i in (pkg_idx, imp_idx, cls_idx) if i >= 0]
        if candidates:
            extracted = extracted[min(candidates):].strip()
        # remove any remaining stray backticks (shouldn't appear in legal Java)
        extracted = extracted.replace("`", "")

    # 4) Final fallback: if the result doesn't look like Java at all, return
    # the original code so we don't introduce worse corruption.
    if not re.search(r"\b(class|interface|enum)\s+\w+", extracted):
        return code

    return extracted


def _needs_rag_retrieval(classified: dict, errors: List[dict]) -> Tuple[bool, str]:
    """Decide whether Layer-2 RAG re-retrieval is worth it.

    Pure-syntax errors (illegal chars, brace mismatch, EOF while parsing,
    class-name vs filename mismatch, ';' expected, etc.) do NOT benefit from
    RAG — they are local textual issues. For those we let the LLM fix from
    the error messages alone, which is faster and avoids polluting the
    prompt with irrelevant API info.

    Returns (need_rag, reason).
    """
    # Hard "needs RAG" signals: API/type-level errors
    api_level = (
        len(classified.get("cannot_find_symbol", []))
        + len(classified.get("incompatible_types", []))
        + len(classified.get("ambiguous_reference", []))
        + len(classified.get("not_public", []))
    )
    if api_level > 0:
        return True, f"API-level errors detected ({api_level})"

    # Pure syntax / structural errors → no RAG
    if classified.get("illegal_character") or classified.get("brace_mismatch"):
        return False, "pure syntax error (illegal char / brace mismatch)"
    if classified.get("class_name_mismatch"):
        return False, "class-name/filename mismatch (pure structural)"

    # Scan 'other' for syntax patterns
    syntax_markers = (
        "reached end of file", "';' expected", "'{' expected", "'}' expected",
        "class, interface", "not a statement", "unclosed", "illegal start of",
        "expected", "<identifier> expected",
    )
    other = classified.get("other", [])
    if other and all(
        any(mk in e.get("message", "") for mk in syntax_markers) for e in other
    ):
        return False, "all remaining errors are syntactic"

    # Unreported exception alone — handled by rule layer, doesn't need RAG
    if classified.get("unreported_exception") and not other:
        return False, "only unreported exception (rule-handled)"

    # Default: if we can't tell, do RAG (safer)
    return True, "unclassified errors — RAG by default"


def _build_error_annotated_code(code: str, errors: List[dict], limit: int = 10) -> str:
    """Annotate the failing code with compile-error comments so AgenticRAG's
    LLM dependency analyser can focus on error-related dependencies instead
    of re-deriving them from the whole method body.

    Produces a header like:

        // ===== COMPILE ERRORS TO FIX =====
        // Line 42: cannot find symbol — method getAdapter (symbol: getAdapter)
        // Line 58: incompatible types: String cannot be converted to TypeToken
        // =================================
        <original code>
    """
    if not errors:
        return code
    header_lines = ["// ===== COMPILE ERRORS TO FIX ====="]
    for e in errors[:limit]:
        ln = e.get("line", 0)
        msg = (e.get("message", "") or "").strip()
        sym = e.get("symbol")
        suffix = f" (symbol: {sym})" if sym else ""
        header_lines.append(f"// Line {ln}: {msg}{suffix}")
    header_lines.append("// =================================")
    return "\n".join(header_lines) + "\n" + code


# ============ Main Fix Loop ============

async def fix_compile_errors(
    code: str,
    compile_output: str,
    context: str = "",
    max_retries: int = 3,
    compile_fn=None,
    code_rag=None,
    agentic_rag=None,
    target_class: str = "",
    method_signature: str = "",
) -> Tuple[str, bool, List[str]]:
    """Iterative compile-error fix loop.

    Args:
        code: The Java test code that failed to compile.
        compile_output: Raw Maven/javac output containing error messages.
        context: RAG context with correct API information.
        max_retries: Maximum number of fix iterations.
        compile_fn: Optional callable(code) -> (success: bool, output: str)
                    for re-compiling after each fix. If None, only one fix pass is done.
        code_rag: Optional CodeRAG instance for symbol resolution (legacy, prefer agentic_rag).
        agentic_rag: Optional AgenticRAG instance for intelligent re-retrieval on errors.
        target_class: Target class name for re-retrieval context.
        method_signature: Target method signature for re-retrieval context.

    Returns:
        (fixed_code, success, fix_log)
    """
    fix_log = []
    current_code = code
    current_output = compile_output
    prev_error_count = float('inf')

    for attempt in range(1, max_retries + 1):
        print(f"  [Fix Loop] Attempt {attempt}/{max_retries}")

        # Parse and classify errors
        errors = parse_compile_errors(current_output)
        if not errors:
            fix_log.append(f"Attempt {attempt}: No parseable errors in output")
            if "BUILD FAILURE" in current_output or "error" in current_output.lower():
                errors = [{"file": "?", "line": 0, "col": 0,
                           "message": "Unknown compile error", "symbol": None}]
            else:
                break

        # Early termination: if error count isn't decreasing, stop
        if len(errors) >= prev_error_count and attempt > 1:
            fix_log.append(f"Attempt {attempt}: Error count not decreasing "
                           f"({len(errors)} >= {prev_error_count}), giving up")
            break
        prev_error_count = len(errors)

        classified = classify_errors(errors)
        n_rule_fixable = (
            len(classified['package_not_exist'])
            + (1 if classified['unreported_exception'] else 0)
            + (1 if classified['assertThat_mismatch'] else 0)
            + len(classified.get('not_public', []))
            + (1 if classified['brace_mismatch'] else 0)
            + (1 if classified['illegal_character'] else 0)
            + len(classified['incompatible_types'])
        )
        n_hard = (
            len(classified['cannot_find_symbol'])
            + len(classified.get('ambiguous_reference', []))
            + len(classified['other'])
        )
        fix_log.append(f"Attempt {attempt}: {len(errors)} errors "
                       f"(rule-fixable={n_rule_fixable}, hard={n_hard})")

        # Layer 1: Rule-based fixes (always apply)
        rag_instance = code_rag or agentic_rag
        current_code, rule_fixes = rule_fix(current_code, classified, rag_instance=rag_instance)
        if rule_fixes:
            fix_log.append(f"  Rule fixes: {'; '.join(rule_fixes)}")

        # If rules were applied, try compiling BEFORE calling LLM
        if rule_fixes and compile_fn:
            success, current_output = compile_fn(current_code)
            if success:
                fix_log.append(f"Attempt {attempt}: ✓ Rules alone fixed it!")
                return current_code, True, fix_log
            # Re-parse to see what's left
            errors = parse_compile_errors(current_output)
            if not errors:
                fix_log.append(f"Attempt {attempt}: ✗ Still failing (unparseable)")
                continue
            classified = classify_errors(errors)
            n_hard = (
                len(classified['cannot_find_symbol'])
                + len(classified['incompatible_types'])
                + len(classified.get('ambiguous_reference', []))
                + len(classified['other'])
            )
            fix_log.append(f"  After rules: {len(errors)} errors remain (hard={n_hard})")
            if len(errors) == 0:
                break

        # Layer 2: LLM fix — call LLM whenever there are remaining errors
        # (not just for "hard" errors — let LLM judge what to do)
        remaining_errors = parse_compile_errors(current_output) if compile_fn else errors
        if not remaining_errors:
            remaining_errors = errors

        if remaining_errors:
            # ── Decision: does this batch of errors actually need RAG? ──
            re_classified = classify_errors(remaining_errors)
            need_rag, reason = _needs_rag_retrieval(re_classified, remaining_errors)
            print(f"  [Fix Loop] RAG decision: need_rag={need_rag} ({reason})")
            fix_log.append(f"  RAG decision: {need_rag} — {reason}")

            # ── Re-retrieval: let AgenticRAG analyze the failing code +
            # compile errors and retrieve fresh, targeted context. We
            # annotate the code with the error messages so the LLM
            # dependency analyser focuses on error-relevant symbols. ──
            new_context = ""
            if need_rag and agentic_rag and hasattr(agentic_rag, 'retrieve'):
                try:
                    print(f"  [Fix Loop] Re-retrieving context with AgenticRAG (error-driven)...")
                    annotated_code = _build_error_annotated_code(
                        current_code, remaining_errors
                    )
                    new_context = await agentic_rag.retrieve(
                        annotated_code,
                        target_class=target_class,
                        method_signature=method_signature,
                        top_k=3,
                    )
                    if new_context:
                        fix_log.append(
                            f"  AgenticRAG re-retrieved: {len(new_context)} chars"
                        )
                except Exception as e:
                    fix_log.append(f"  AgenticRAG re-retrieval failed: {e}")
            elif not need_rag:
                fix_log.append("  Skipped AgenticRAG (errors are non-semantic)")

            # Fallback: legacy CodeRAG symbol resolution (for when agentic_rag
            # is unavailable or returned nothing, AND we still want RAG)
            api_info = ""
            if need_rag and not new_context:
                all_symbols = list(set(
                    classified["cannot_find_symbol"]
                    + classified.get("not_public", [])
                ))
                for err in classified["incompatible_types"]:
                    type_match = re.search(r'cannot be converted to ([\w.]+)', err.get('message', ''))
                    if type_match:
                        all_symbols.append(type_match.group(1).split('.')[-1])
                for err in classified.get("ambiguous_reference", []):
                    ref_match = re.search(r'reference to (\w+) is ambiguous', err.get('message', ''))
                    if ref_match:
                        all_symbols.append(ref_match.group(1))
                rag_instance = code_rag or (agentic_rag.rag if agentic_rag and hasattr(agentic_rag, 'rag') else None)
                if all_symbols and rag_instance:
                    api_info = resolve_symbols_from_rag(all_symbols, rag_instance)
                    if api_info:
                        fix_log.append(f"  RAG resolved: {len(api_info)} chars of API info")

            # Merge: new_context from AgenticRAG replaces the stale context;
            # api_info from legacy resolution supplements it if present.
            effective_context = new_context or context

            print(f"  [Fix Loop] Applying LLM fix ({len(remaining_errors)} remaining errors)...")
            fix_log.append(f"  LLM fix applied ({len(remaining_errors)} errors)")
            current_code = await llm_fix(current_code, remaining_errors, effective_context,
                                         api_info=api_info)
            from llm.llm import _fix_imports
            current_code = _fix_imports(current_code)

            # Compile after LLM fix
            if compile_fn:
                success, current_output = compile_fn(current_code)
                if success:
                    fix_log.append(f"Attempt {attempt}: ✓ Compile succeeded!")
                    return current_code, True, fix_log
                fix_log.append(f"Attempt {attempt}: ✗ Still failing after LLM fix")
        else:
            # No remaining errors after rule fixes and no compile_fn to verify
            # Let LLM do a final pass to ensure correctness
            fix_log.append(f"Attempt {attempt}: No remaining errors detected, skipping LLM")
            break

    fix_log.append(f"Fix loop done after {min(attempt, max_retries)} attempts")
    return current_code, False, fix_log
