"""
Fix Loop: compile-error-driven iterative repair.

Two-layer approach:
  Layer 1 (Rule Engine): Fast regex-based fixes for common patterns.
  Layer 2 (LLM + RAG):  For errors that need semantic understanding.

Usage:
    from core.fix_loop import fix_compile_errors

    fixed_code, success = await fix_compile_errors(
        code=code,
        errors=errors,
        context=rag_context,
        max_retries=3,
    )
"""

import os
import re
import sys
from typing import List, Tuple, Optional

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


# ============ Layer 1: Rule-based fixes ============

def rule_fix(code: str, classified: dict) -> Tuple[str, List[str]]:
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

    # Fix 5: Convert assertThat(...) to assertEquals/assertTrue when assertThat doesn't compile
    if classified.get("assertThat_mismatch"):
        # assertThat(expr).isEqualTo(expected) -> assertEquals(expected, expr)
        code = re.sub(
            r'assertThat\((.+?)\)\.isEqualTo\((.+?)\);',
            r'assertEquals(\2, \1);',
            code
        )
        # assertThat(expr).isTrue() -> assertTrue(expr)
        code = re.sub(
            r'assertThat\((.+?)\)\.isTrue\(\);',
            r'assertTrue(\1);',
            code
        )
        # assertThat(expr).isFalse() -> assertFalse(expr)
        code = re.sub(
            r'assertThat\((.+?)\)\.isFalse\(\);',
            r'assertFalse(\1);',
            code
        )
        # assertThat(expr).isNull() -> assertNull(expr)
        code = re.sub(
            r'assertThat\((.+?)\)\.isNull\(\);',
            r'assertNull(\1);',
            code
        )
        # assertThat(expr).isNotNull() -> assertNotNull(expr)
        code = re.sub(
            r'assertThat\((.+?)\)\.isNotNull\(\);',
            r'assertNotNull(\1);',
            code
        )
        # assertThat(expr).isInstanceOf(Cls.class) -> assertTrue(expr instanceof Cls)
        code = re.sub(
            r'assertThat\((.+?)\)\.isInstanceOf\((.+?)\.class\);',
            r'assertTrue(\1 instanceof \2);',
            code
        )
        # assertThat(() -> expr).isInstanceOf(Exception.class) -> assertThrows pattern
        # This is a complex case, convert to assertThrows
        code = re.sub(
            r'assertThat\(\(\)\s*->\s*(.+?)\)\.isInstanceOf\((.+?)\.class\);',
            r'assertThrows(\2.class, () -> \1);',
            code
        )
        # Remove remaining assertThat imports that might conflict
        code = re.sub(
            r'^import\s+static\s+com\.google\.common\.truth\.Truth\.assertThat;.*$\n?',
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
        fixes.append("Converted assertThat() calls to JUnit Assert equivalents")

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
Fix the compile errors in the code above. Rules:
1. Only fix the errors listed above. Do not change test logic.
2. If a method/class does not exist, either remove the test that uses it or replace with the correct API from the API information above.
3. Do NOT use org.assertj. Use org.junit.Assert (assertEquals, assertTrue, assertThrows, etc.).
4. Do NOT use private helper methods from other test classes.
5. Do NOT access package-private or internal classes. Only use public API.
6. Ensure all test methods declare 'throws Exception' if they call methods that throw checked exceptions.
7. Pay attention to constructor signatures — use the exact parameters shown in the API information.
8. Return ONLY the complete fixed Java file, no explanations.

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

    # Extract code from response
    matches = re.findall(r"```(?:java)?\n(.*?)```", resp, re.DOTALL)
    if matches:
        return matches[-1].strip()  # Take the last code block (the fixed version)
    return resp.strip()


# ============ Main Fix Loop ============

async def fix_compile_errors(
    code: str,
    compile_output: str,
    context: str = "",
    max_retries: int = 3,
    compile_fn=None,
    code_rag=None,
) -> Tuple[str, bool, List[str]]:
    """Iterative compile-error fix loop.

    Args:
        code: The Java test code that failed to compile.
        compile_output: Raw Maven/javac output containing error messages.
        context: RAG context with correct API information.
        max_retries: Maximum number of fix iterations.
        compile_fn: Optional callable(code) -> (success: bool, output: str)
                    for re-compiling after each fix. If None, only one fix pass is done.
        code_rag: Optional CodeRAG instance for symbol resolution.

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
        current_code, rule_fixes = rule_fix(current_code, classified)
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

        # Layer 2: LLM fix — only if there are hard errors worth fixing
        if n_hard > 0:
            # Resolve symbols from RAG
            api_info = ""
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
            if all_symbols and code_rag:
                api_info = resolve_symbols_from_rag(all_symbols, code_rag)
                if api_info:
                    fix_log.append(f"  RAG resolved: {len(api_info)} chars of API info")

            print(f"  [Fix Loop] Applying LLM fix ({n_hard} hard errors)...")
            fix_log.append(f"  LLM fix applied")
            current_code = await llm_fix(current_code, errors, context,
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
            # No hard errors and rules didn't fully fix it — nothing more we can do
            fix_log.append(f"Attempt {attempt}: No hard errors to fix with LLM, giving up")
            break

    fix_log.append(f"Fix loop done after {min(attempt, max_retries)} attempts")
    return current_code, False, fix_log
