"""
Agentic RAG - LLM-driven intelligent retrieval

Pipeline: LLM dependency analysis -> static analysis augmentation -> multi-strategy retrieval -> context assembly
"""

import json
import os
import re
import hashlib
from typing import Dict, List, Tuple, Optional, Set
from functools import lru_cache

from .code_rag import CodeRAG

# Primitive Java types that don't need further lookup
_PRIMITIVES: Set[str] = {
    'void', 'int', 'long', 'boolean', 'String', 'double', 'float',
    'char', 'byte', 'short', 'Integer', 'Long', 'Boolean', 'Double',
    'Float', 'Character', 'Byte', 'Short', 'Object', 'Number',
    'List', 'Map', 'Set', 'Collection', 'Optional', 'Stream',
    'Iterator', 'Iterable', 'Comparable', 'Serializable',
}

# Generic wrapper patterns to strip before type lookup
_GENERIC_PATTERN = re.compile(r'<.*>')
_ARRAY_PATTERN = re.compile(r'\[\]')


def _normalize_type(t: str) -> str:
    """Strip generics/arrays and return the base type name."""
    t = _GENERIC_PATTERN.sub('', t).strip()
    t = _ARRAY_PATTERN.sub('', t).strip()
    # Handle fully-qualified names: take the last segment
    if '.' in t:
        t = t.split('.')[-1]
    return t


class AgenticRAG:
    """Agentic RAG with multi-strategy intelligent retrieval."""

    # Maximum characters for a single method body in context
    _METHOD_BODY_LIMIT = 800
    # Maximum characters for a related semantic block
    _SEMANTIC_BODY_LIMIT = 600
    # Maximum total context characters before truncation warning
    _CONTEXT_SOFT_LIMIT = 12_000

    def __init__(self, index_path: str, test_dir: str = None, verbose: bool = False):
        self.rag = CodeRAG(index_path)
        self.test_dir = test_dir
        self.verbose = verbose
        # Simple in-memory cache: code_hash -> deps dict
        self._deps_cache: Dict[str, Dict[str, List[str]]] = {}
        # Cache for test exemplars
        self._test_exemplar_cache: Dict[str, str] = {}
        # Cache for detected test framework info
        self._test_framework_cache: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        """Print a verbose log line."""
        if self.verbose:
            print(f"  [AgenticRAG] {msg}")

    # ------------------------------------------------------------------
    # LLM query rewrite
    # ------------------------------------------------------------------

    async def rewrite_query(self, code: str, cls: str, method_signature: str = "") -> str:
        """Use LLM to rewrite method code into a focused retrieval query.

        This improves semantic search quality by converting raw code into
        a concise, keyword-rich query that captures the method's intent,
        key API calls, and relevant types.

        Returns the rewritten query string, or falls back to a heuristic
        extraction if LLM fails.
        """
        from llm import chat, PROMPTS

        # Cache by (cls, method_signature) hash
        cache_key = hashlib.md5(f"qr:{cls}:{method_signature}:{code[:200]}".encode()).hexdigest()
        if cache_key in self._deps_cache:
            cached = self._deps_cache[cache_key]
            if isinstance(cached, str):
                self._log(f"命中query改写缓存: {cached[:80]}")
                return cached

        # Heuristic fallback: extract method calls and class name
        def _heuristic_query() -> str:
            calls = re.findall(r'\.([a-zA-Z][a-zA-Z0-9_]+)\s*\(', code)
            unique_calls = list(dict.fromkeys(calls))[:8]
            sig_words = re.findall(r'[A-Za-z][A-Za-z0-9_]+', method_signature or "")
            parts = ([cls] if cls else []) + sig_words[:3] + unique_calls
            return " ".join(dict.fromkeys(parts))[:200]

        if "query_rewrite" not in PROMPTS:
            return _heuristic_query()

        try:
            self._log("调用LLM改写检索query...")
            prompt = PROMPTS["query_rewrite"].format(
                cls=cls,
                method_signature=method_signature or "",
                code=code[:1500],
            )
            from core.token_meter import phase as _phase
            with _phase("rag_query_rewrite"):
                resp = await chat(prompt, temperature=0.1, max_tokens=100)
            # Clean up: take first non-empty line
            query = next(
                (line.strip() for line in resp.strip().splitlines() if line.strip()),
                ""
            )
            # Remove any markdown/quotes
            query = re.sub(r'^[`"\']|[`"\']$', '', query).strip()
            if not query or len(query) < 5:
                query = _heuristic_query()
            self._log(f"改写后query: {query[:100]}")
            self._deps_cache[cache_key] = query
            return query
        except Exception as e:
            self._log(f"query改写失败，使用启发式: {e}")
            return _heuristic_query()

    # ------------------------------------------------------------------
    # LLM dependency analysis
    # ------------------------------------------------------------------

    async def analyze_dependencies(self, code: str, cls: str) -> Dict[str, List[str]]:
        """Use LLM to extract methods / fields / types the code depends on."""
        from llm import chat, PROMPTS

        # Cache by (cls, code) hash to avoid redundant LLM calls
        cache_key = hashlib.md5(f"{cls}:{code}".encode()).hexdigest()
        if cache_key in self._deps_cache:
            self._log("命中依赖分析缓存")
            return self._deps_cache[cache_key]

        self._log("调用LLM分析依赖...")
        prompt = PROMPTS["deps_analysis"].format(cls=cls, code=code)
        empty = {"methods": [], "fields": [], "types": []}
        try:
            from core.token_meter import phase as _phase
            with _phase("rag_deps_analysis"):
                resp = await chat(prompt, temperature=0.1)
            deps = self._parse_deps_response(resp)
            self._log(
                f"LLM分析结果: methods={deps.get('methods', [])}, "
                f"fields={deps.get('fields', [])}, types={deps.get('types', [])}"
            )
            self._deps_cache[cache_key] = deps
            return deps
        except Exception as e:
            self._log(f"LLM分析失败: {e}")
            return empty

    @staticmethod
    def _parse_deps_response(resp: str) -> Dict[str, List[str]]:
        """
        Robustly parse the LLM response into a deps dict.
        Tries multiple strategies in order:
          1. Extract the first JSON object (possibly nested)
          2. Extract a JSON code block
          3. Return empty fallback
        """
        # Strategy 1: find outermost JSON object
        depth = 0
        start = None
        for i, ch in enumerate(resp):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = resp[start:i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return {
                                "methods": list(data.get("methods", [])),
                                "fields": list(data.get("fields", [])),
                                "types": list(data.get("types", [])),
                            }
                    except json.JSONDecodeError:
                        pass
                    break

        # Strategy 2: JSON code block
        block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', resp)
        if block_match:
            try:
                data = json.loads(block_match.group(1))
                if isinstance(data, dict):
                    return {
                        "methods": list(data.get("methods", [])),
                        "fields": list(data.get("fields", [])),
                        "types": list(data.get("types", [])),
                    }
            except json.JSONDecodeError:
                pass

        return {"methods": [], "fields": [], "types": []}

    # ------------------------------------------------------------------
    # Static analysis helpers
    # ------------------------------------------------------------------

    def _find_method(self, name: str, target_cls: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        Find a method definition by name.
        Search order: target class first, then all other classes.
        Supports partial match on method name (ignoring parameters).
        """
        search_order = [target_cls] + [
            k for k in self.rag.class_info.keys() if k != target_cls
        ]
        for cls_name in search_order:
            info = self.rag.class_info.get(cls_name)
            if info is None:
                continue
            for m in info.methods:
                sig = m.get("signature", "")
                # Extract bare method name from signature for exact match
                bare = sig.split('(')[0].split()[-1] if '(' in sig else sig
                if bare == name or name in sig:
                    return m, cls_name
        return None, None

    def _find_type(self, name: str) -> Optional[object]:
        """Find a class/type definition by simple or fully-qualified name."""
        name = _normalize_type(name)
        if not name or name in _PRIMITIVES:
            return None
        # Exact match first
        if name in self.rag.class_info:
            return self.rag.class_info[name]
        # Suffix match (e.g. "MyDto" matches "com.example.MyDto")
        for cls_name, info in self.rag.class_info.items():
            if cls_name.endswith("." + name) or cls_name.split('.')[-1] == name:
                return info
        return None

    def _extract_types_from_code(self, code: str, cls: str, method_signature: str = None) -> List[str]:
        """
        Statically extract all relevant types from code:
          - Return types of called methods
          - Parameter types from the method signature
          - Local variable type declarations
          - Field types referenced in the class
        """
        types: Set[str] = set()

        # 1. Return types of called methods
        called_methods = re.findall(r'\.(\w+)\s*\(', code)
        self._log(f"检测到方法调用: {called_methods[:15]}")
        for method_name in called_methods:
            method, _ = self._find_method(method_name, cls)
            if method:
                ret = _normalize_type(method.get("return_type", ""))
                if ret and ret not in _PRIMITIVES:
                    types.add(ret)
                    self._log(f"  {method_name}() 返回类型: {ret}")

        # 2. Parameter types from method signature
        if method_signature:
            for t in self._extract_param_types(method_signature):
                nt = _normalize_type(t)
                if nt and nt not in _PRIMITIVES:
                    types.add(nt)

        # 3. Local variable declarations: "TypeName varName" or "TypeName<...> varName"
        local_decls = re.findall(
            r'\b([A-Z][A-Za-z0-9_]*(?:<[^>]+>)?)\s+[a-z_][A-Za-z0-9_]*\s*[=;(,]',
            code
        )
        for t in local_decls:
            nt = _normalize_type(t)
            if nt and nt not in _PRIMITIVES:
                types.add(nt)

        # 4. Cast expressions: (TypeName)
        cast_types = re.findall(r'\(([A-Z][A-Za-z0-9_]*)\)', code)
        for t in cast_types:
            nt = _normalize_type(t)
            if nt and nt not in _PRIMITIVES:
                types.add(nt)

        # 5. Field types from the target class
        if cls in self.rag.class_info:
            for f in self.rag.class_info[cls].fields:
                ft = _normalize_type(f.get("type", ""))
                if ft and ft not in _PRIMITIVES:
                    types.add(ft)

        return list(types)

    @staticmethod
    def _extract_param_types(method_signature: str) -> List[str]:
        """Extract parameter types from a method signature string."""
        if '(' not in method_signature:
            return []
        params_part = method_signature.split('(', 1)[1].rsplit(')', 1)[0]
        if not params_part.strip():
            return []
        param_types = []
        for param in params_part.split(','):
            param = param.strip()
            if not param:
                continue
            # Handle annotations: @NotNull SomeType varName
            parts = [p for p in param.split() if not p.startswith('@')]
            if len(parts) >= 2:
                param_types.append(parts[-2])  # type is second-to-last token
            elif parts:
                param_types.append(parts[0])
        return param_types

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_method_signature(method: Dict) -> str:
        """Build a clean method signature from parsed components.

        Avoids using the raw 'signature' field which may contain annotations
        like @SuppressWarnings({...}) that break split('{') truncation.
        Instead, reconstructs: [public|protected] [static] ReturnType name(params)
        """
        mods = method.get('modifiers', [])
        # Keep only access modifiers and 'static'/'final'/'abstract'/'synchronized'
        clean_mods = [m for m in mods if m in ('public', 'protected', 'private',
                                                'static', 'final', 'abstract',
                                                'synchronized', 'default')]
        ret = method.get('return_type', '') or ''
        name = method.get('name', '')
        params = method.get('params', [])
        param_str = ', '.join(params)
        parts = clean_mods + ([ret] if ret else []) + [f"{name}({param_str})"]
        return ' '.join(parts)

    def _build_class_section(self, cls: str, deps: Dict[str, List[str]]) -> List[str]:
        """Build the primary class structure section with visibility annotations."""
        info = self.rag.class_info.get(cls)
        if not info:
            return []

        parts = [f"## {info.package}.{cls}"]

        if info.imports:
            parts.append(
                "\n### Imports\n" +
                "\n".join(f"import {i};" for i in info.imports[:15])
            )

        # Determine if all fields/constants are private (common in well-encapsulated classes)
        all_fields_private = True
        if info.fields:
            needed = set(deps.get("fields", []))
            fields = (
                [f for f in info.fields if any(n in f.get("name", "") for n in needed)]
                if needed else info.fields
            )
            if fields:
                parts.append(
                    "\n### Fields\n" +
                    "\n".join(f"  - {f['signature']}" for f in fields[:10])
                )
                # Check visibility
                for f in fields:
                    sig = f.get('signature', '')
                    if 'public' in sig or 'protected' in sig:
                        all_fields_private = False

        if info.constants:
            parts.append(
                "\n### Constants\n" +
                "\n".join(f"  - {c['signature']}" for c in info.constants[:15])
            )
            for c in info.constants:
                sig = c.get('signature', '')
                if 'public' in sig or 'protected' in sig:
                    all_fields_private = False

        # Add visibility warning
        if all_fields_private and (info.fields or info.constants):
            parts.append(
                "\n### ⚠️ VISIBILITY WARNING\n"
                "All fields and constants in this class are **private**.\n"
                "They CANNOT be accessed from subclasses, test classes, or via reflection in tests.\n"
                "You MUST test this class through its **public API only** (constructors + public methods).\n"
                "Do NOT create Mock subclasses that try to set private fields.\n"
                "Instead, construct test inputs via the public constructor and feed appropriate data through the public API."
            )

        if info.constructors:
            parts.append(
                "\n### Constructors\n" +
                "\n".join(f"  - {c['signature']}" for c in info.constructors[:5])
            )

        # List all public methods to prevent LLM hallucinating non-existent APIs
        if info.methods:
            public_methods = [
                m for m in info.methods if 'public' in m.get('modifiers', [])
            ]
            if public_methods:
                # Use clean signatures (no annotations, no body)
                method_sigs = [
                    self._clean_method_signature(m)
                    for m in public_methods[:30]
                ]
                # Deduplicate (same overload may appear from different annotations)
                seen = set()
                unique_sigs = []
                for s in method_sigs:
                    if s not in seen:
                        seen.add(s)
                        unique_sigs.append(s)
                parts.append(
                    "\n### Available Public Methods (ONLY use these)\n" +
                    "\n".join(f"  - {s}" for s in unique_sigs)
                )

        return parts

    def _build_method_sections(
        self, deps: Dict[str, List[str]], cls: str, seen_sigs: Set[str]
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """Build sections for each dependent method."""
        parts = []
        log: Dict[str, List[str]] = {"found": [], "not_found": []}

        for name in deps.get("methods", [])[:8]:
            method, owner = self._find_method(name, cls)
            if not method:
                log["not_found"].append(f"方法: {name}")
                continue

            sig = method.get('signature', name)
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)

            marker = "" if owner == cls else f"  [{owner}]"
            parts.append(f"\n### Method: {sig}{marker}")

            body = method.get("code", "")
            if body:
                truncated = body[:self._METHOD_BODY_LIMIT]
                suffix = "\n  // ... (truncated)" if len(body) > self._METHOD_BODY_LIMIT else ""
                parts.append(f"```java\n{truncated}{suffix}\n```")

            log["found"].append(f"方法: {name} (owner={owner})")

        return parts, log

    def _build_type_sections(
        self, all_types: List[str], seen_types: Set[str]
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """Build sections for each dependent type."""
        parts = []
        log: Dict[str, List[str]] = {"found": [], "not_found": []}

        for name in all_types[:8]:
            norm = _normalize_type(name)
            if not norm or norm in _PRIMITIVES or norm in seen_types:
                continue
            seen_types.add(norm)

            type_info = self._find_type(norm)
            if not type_info:
                log["not_found"].append(f"类型: {norm}")
                continue

            fqn = f"{type_info.package}.{type_info.name}" if type_info.package else type_info.name
            parts.append(f"\n### Type: {fqn}")

            if type_info.constants:
                const_lines = [
                    f"  - {type_info.name}.{c.get('name', c.get('signature', ''))}"
                    for c in type_info.constants[:20]
                ]
                parts.append("Constants:\n" + "\n".join(const_lines))

            if type_info.fields:
                field_lines = [
                    f"  - {f.get('signature', '')}"
                    for f in type_info.fields[:10]
                ]
                parts.append("Fields:\n" + "\n".join(field_lines))

            if type_info.methods:
                method_sigs = [
                    m.get('signature', '').split('{')[0].strip()
                    for m in type_info.methods[:10]
                ]
                parts.append("Methods:\n" + "\n".join(f"  - {s}" for s in method_sigs))

            log["found"].append(f"类型: {norm} -> {fqn}")

        return parts, log

    def _build_semantic_sections(
        self, query: str, top_k: int, deps: Dict[str, List[str]], seen_sigs: Set[str]
    ) -> List[str]:
        """Semantic search to supplement with related code blocks.

        Args:
            query: The (optionally LLM-rewritten) search query string.
            top_k: Maximum number of semantic results to include.
            deps: Dependency dict from LLM analysis.
            seen_sigs: Already-included method signatures (to avoid duplicates).
        """
        parts = []
        dep_methods = set(deps.get("methods", []))
        results = self.rag.search(query, top_k=top_k)

        added = 0
        for block, score in results:
            if added >= top_k:
                break
            sig = block.signature
            if sig in seen_sigs:
                continue
            # Skip if already covered by explicit method retrieval
            bare = sig.split('(')[0].split()[-1] if '(' in sig else sig
            if bare in dep_methods:
                continue
            seen_sigs.add(sig)

            body = block.code[:self._SEMANTIC_BODY_LIMIT]
            suffix = "\n  // ... (truncated)" if len(block.code) > self._SEMANTIC_BODY_LIMIT else ""
            score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
            parts.append(
                f"\n### Semantic Match (score={score_str}): {sig}\n"
                f"```java\n{body}{suffix}\n```"
            )
            added += 1

        return parts

    # ------------------------------------------------------------------
    # Test framework detection
    # ------------------------------------------------------------------

    def detect_test_framework(self) -> Dict[str, str]:
        """Auto-detect the test framework and assertion library used by the project.

        Scans existing test files' import statements to determine:
        - test_framework: 'junit4', 'junit5', 'testng', etc.
        - assertion_lib: 'junit_assert', 'assertj', 'truth', 'hamcrest', etc.
        - assertion_import: the actual import statement to use

        Returns a dict with keys: test_framework, assertion_lib, assertion_import, assertion_style.
        """
        if self._test_framework_cache is not None:
            return self._test_framework_cache

        import glob

        result = {
            "test_framework": "junit4",
            "assertion_lib": "junit_assert",
            "assertion_import": "import static org.junit.Assert.*;",
            "assertion_style": "assertEquals(expected, actual)",
        }

        if not self.test_dir:
            self._test_framework_cache = result
            return result

        # Scan up to 10 test files
        test_files = glob.glob(
            os.path.join(self.test_dir, "**", "*Test*.java"), recursive=True
        )[:10]

        # Count import occurrences
        import_counts: Dict[str, int] = {
            "junit4": 0,       # org.junit.Test
            "junit5": 0,       # org.junit.jupiter
            "testng": 0,       # org.testng
            "assertj": 0,      # org.assertj
            "truth": 0,        # com.google.common.truth
            "hamcrest": 0,     # org.hamcrest
            "junit_assert": 0, # org.junit.Assert
        }

        for filepath in test_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except OSError:
                continue

            if 'org.junit.Test' in content or 'org.junit.Assert' in content:
                import_counts["junit4"] += 1
            if 'org.junit.jupiter' in content:
                import_counts["junit5"] += 1
            if 'org.testng' in content:
                import_counts["testng"] += 1
            if 'org.assertj' in content:
                import_counts["assertj"] += 1
            if 'com.google.common.truth' in content:
                import_counts["truth"] += 1
            if 'org.hamcrest' in content:
                import_counts["hamcrest"] += 1
            if 'org.junit.Assert' in content:
                import_counts["junit_assert"] += 1

        # Determine test framework
        fw_candidates = [("junit4", import_counts["junit4"]),
                         ("junit5", import_counts["junit5"]),
                         ("testng", import_counts["testng"])]
        fw_candidates.sort(key=lambda x: x[1], reverse=True)
        if fw_candidates[0][1] > 0:
            result["test_framework"] = fw_candidates[0][0]

        # Determine assertion library
        assert_candidates = [
            ("truth", import_counts["truth"],
             "import static com.google.common.truth.Truth.assertThat;",
             "assertThat(actual).isEqualTo(expected)"),
            ("assertj", import_counts["assertj"],
             "import static org.assertj.core.api.Assertions.assertThat;",
             "assertThat(actual).isEqualTo(expected)"),
            ("hamcrest", import_counts["hamcrest"],
             "import static org.hamcrest.MatcherAssert.assertThat;\nimport static org.hamcrest.Matchers.*;",
             "assertThat(actual, is(expected))"),
            ("junit_assert", import_counts["junit_assert"],
             "import static org.junit.Assert.*;",
             "assertEquals(expected, actual)"),
        ]
        assert_candidates.sort(key=lambda x: x[1], reverse=True)
        if assert_candidates[0][1] > 0:
            result["assertion_lib"] = assert_candidates[0][0]
            result["assertion_import"] = assert_candidates[0][2]
            result["assertion_style"] = assert_candidates[0][3]

        self._test_framework_cache = result
        self._log(f"检测到测试框架: {result['test_framework']}, 断言库: {result['assertion_lib']}")
        return result

    # ------------------------------------------------------------------
    # Test exemplar & usage example retrieval (multi-layer)
    # ------------------------------------------------------------------

    # Shared constants for exemplar retrieval
    _MAX_EXEMPLARS = 3
    _MAX_EXEMPLAR_CHARS = 1500
    _MAX_METHOD_CHARS = 600

    @staticmethod
    def _extract_method_name(method_signature: str) -> str:
        """Extract bare method name from a full signature string."""
        if '(' in method_signature:
            before_paren = method_signature.split('(')[0]
            parts = before_paren.split()
            return parts[-1] if parts else ""
        return ""

    def _build_test_exemplar_section(self, cls: str, method_signature: str) -> List[str]:
        """Multi-layer retrieval for test exemplars and usage examples.

        Layer 1 (Test Patterns):  Scan test_dir for existing tests of the target method.
        Layer 2 (Usage Examples): Search the code index for call-sites of the target method.
        Layer 3 (Sibling Tests): Scan test_dir for tests of *other* methods in the same class
                                  to provide a general testing template.

        Each layer is tried in order; as soon as enough exemplars are found, later layers
        are skipped.  Results from all contributing layers are merged into a single context
        section with clear labels.
        """
        method_name = self._extract_method_name(method_signature)
        if not method_name:
            return []

        # Check cache
        cache_key = f"{cls}:{method_name}"
        if cache_key in self._test_exemplar_cache:
            cached = self._test_exemplar_cache[cache_key]
            return [cached] if cached else []

        parts: List[str] = []          # context section fragments
        total_found = 0                # total exemplar count across layers
        budget = self._MAX_EXEMPLAR_CHARS  # remaining char budget

        # ── Layer 1: Test Patterns (from test_dir) ───────────────────────
        if self.test_dir:
            test_exemplars = self._search_test_methods(cls, method_name)
            if test_exemplars:
                selected, used_chars = self._select_within_budget(test_exemplars, budget)
                budget -= used_chars
                total_found += len(selected)
                parts.append(self._format_test_pattern_section(cls, selected))
                self._log(f"Layer1 测试模式: 找到 {len(selected)} 个 ({used_chars} chars)")

        # ── Layer 2: Usage Examples (from code index) ────────────────────
        if total_found < self._MAX_EXEMPLARS:
            usage_exemplars = self._search_usage_examples(cls, method_name)
            if usage_exemplars:
                remaining = self._MAX_EXEMPLARS - total_found
                selected, used_chars = self._select_within_budget(
                    usage_exemplars[:remaining], budget
                )
                budget -= used_chars
                total_found += len(selected)
                parts.append(self._format_usage_example_section(cls, method_name, selected))
                self._log(f"Layer2 调用范例: 找到 {len(selected)} 个 ({used_chars} chars)")

        # ── Layer 3: Sibling Tests (other methods in same test class) ────
        if total_found == 0 and self.test_dir:
            sibling_exemplars = self._search_sibling_tests(cls, method_name)
            if sibling_exemplars:
                selected, used_chars = self._select_within_budget(
                    sibling_exemplars[:self._MAX_EXEMPLARS], budget
                )
                total_found += len(selected)
                parts.append(self._format_sibling_test_section(cls, selected))
                self._log(f"Layer3 同类测试模板: 找到 {len(selected)} 个 ({used_chars} chars)")

        if not parts:
            self._log(f"三层检索均未找到 {method_name}() 的示例")
            self._test_exemplar_cache[cache_key] = ""
            return []

        section = "\n".join(parts)
        self._test_exemplar_cache[cache_key] = section
        return [section]

    # ── Layer helpers ─────────────────────────────────────────────────────

    def _search_test_methods(self, cls: str, method_name: str) -> List[str]:
        """Layer 1: Find @Test methods in test_dir that call the target method."""
        import glob

        test_files = glob.glob(
            os.path.join(self.test_dir, "**", f"{cls}Test.java"), recursive=True
        )
        if not test_files:
            test_files = glob.glob(
                os.path.join(self.test_dir, "**", f"*{cls}*Test*.java"), recursive=True
            )
        if not test_files:
            return []

        return self._extract_test_methods_from_files(
            test_files[:2], method_name, self._MAX_EXEMPLARS
        )

    def _search_usage_examples(self, cls: str, method_name: str) -> List[str]:
        """Layer 2: Search the code index for methods that call the target method.

        Scans all indexed code blocks for call-sites like `.methodName(` and
        returns the surrounding method bodies as usage examples.
        """
        exemplars: List[str] = []
        call_pattern = f".{method_name}("
        # Also match unqualified calls: methodName(
        bare_pattern = f"{method_name}("

        for block in self.rag.blocks:
            if len(exemplars) >= self._MAX_EXEMPLARS:
                break
            # Skip the target method's own definition
            if block.class_name == cls and method_name in block.signature:
                continue
            # Skip test code (we handle that in Layer 1)
            # Use path-based check to avoid false positives (e.g. project path containing 'unittest')
            if "Test" in block.class_name or "/src/test/" in block.file or "/test/" in block.file.split("/src/")[-1]:
                continue
            code = block.code
            if call_pattern in code or (block.class_name == cls and bare_pattern in code):
                # Trim to budget
                trimmed = code[:self._MAX_METHOD_CHARS]
                suffix = "\n  // ... (truncated)" if len(code) > self._MAX_METHOD_CHARS else ""
                exemplars.append(
                    f"// {block.class_name}.{block.signature.split('(')[0].split()[-1] if '(' in block.signature else block.signature}\n"
                    f"{trimmed}{suffix}"
                )

        return exemplars

    def _search_sibling_tests(self, cls: str, method_name: str) -> List[str]:
        """Layer 3: Find @Test methods for OTHER methods of the same class.

        When no test exists for the target method, these sibling tests show
        the general testing pattern (how to construct the object, navigate
        state, make assertions) for the same class.
        """
        import glob

        test_files = glob.glob(
            os.path.join(self.test_dir, "**", f"{cls}Test.java"), recursive=True
        )
        if not test_files:
            test_files = glob.glob(
                os.path.join(self.test_dir, "**", f"*{cls}*Test*.java"), recursive=True
            )
        if not test_files:
            return []

        # Get all short test methods that do NOT call the target method
        # (those would have been found by Layer 1)
        return self._extract_test_methods_from_files(
            test_files[:1], None, self._MAX_EXEMPLARS,
            exclude_method=method_name
        )

    # ── Shared utilities ──────────────────────────────────────────────────

    @staticmethod
    def _extract_helper_methods(content: str) -> Dict[str, str]:
        """Extract non-@Test, non-public helper methods from a test file.

        Returns a dict mapping method_name -> method_body for private/package-private
        helper methods that test methods might call.
        """
        helpers: Dict[str, str] = {}
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            # Skip @Test methods, public methods, class declarations, etc.
            if stripped.startswith('@Test') or stripped.startswith('public class'):
                i += 1
                continue
            # Match private/package-private helper method declarations
            # e.g. "private Reader reader(String s) {"
            # e.g. "Reader reader(String s) {"
            m = re.match(
                r'\s*(?:private|protected|static|final|\s)*'
                r'[\w<>\[\]?]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
                lines[i]
            )
            if m and not stripped.startswith('public '):
                method_name = m.group(1)
                # Skip constructors and standard methods
                if method_name in ('if', 'for', 'while', 'switch', 'try', 'catch'):
                    i += 1
                    continue
                start = i
                brace_depth = lines[i].count('{') - lines[i].count('}')
                i += 1
                while i < len(lines) and brace_depth > 0:
                    brace_depth += lines[i].count('{') - lines[i].count('}')
                    i += 1
                helpers[method_name] = '\n'.join(lines[start:i])
                continue
            i += 1
        return helpers

    @staticmethod
    def _find_helper_calls_in_exemplar(exemplar: str, helpers: Dict[str, str]) -> List[str]:
        """Find which helper methods from the test class are called in an exemplar.

        Returns list of helper method names that appear as calls in the exemplar.
        """
        called = []
        for name in helpers:
            # Match method call pattern: name( but not new Name( or .name(
            # We want bare calls like reader("...") which are helper calls
            pattern = rf'(?<!\w)(?<!\.)(?<!new ){re.escape(name)}\s*\('
            if re.search(pattern, exemplar):
                called.append(name)
        return called

    def _annotate_exemplar_with_helpers(
        self, exemplar: str, helpers: Dict[str, str], called_helpers: List[str]
    ) -> str:
        """Annotate an exemplar with information about helper methods it uses.

        Strategy:
        - If the helper is short (<=5 lines), inline it as a comment above the exemplar.
        - If the helper is longer, add a warning comment explaining what it does.
        """
        if not called_helpers:
            return exemplar

        annotations = []
        for name in called_helpers:
            helper_code = helpers[name]
            helper_lines = helper_code.strip().split('\n')
            if len(helper_lines) <= 5:
                # Short helper: inline the full definition
                annotations.append(
                    f"// NOTE: '{name}()' is a private helper in the original test class:\n"
                    f"// {helper_code.strip()}\n"
                    f"// In your generated test, replace '{name}(...)' with the equivalent inline code."
                )
            else:
                # Long helper: just describe it
                first_line = helper_lines[0].strip()
                annotations.append(
                    f"// NOTE: '{name}()' is a private helper in the original test class: {first_line}\n"
                    f"// Do NOT call '{name}()' directly. Inline its logic or use the equivalent public API."
                )

        annotation_block = '\n'.join(annotations)
        return f"{annotation_block}\n{exemplar}"

    def _extract_test_methods_from_files(
        self,
        files: List[str],
        target_method: Optional[str],
        max_count: int,
        exclude_method: str = None,
    ) -> List[str]:
        """Extract @Test methods from Java files.

        Args:
            files: Java file paths to scan.
            target_method: If set, only return methods whose body contains this string.
                           If None, return any short test method.
            max_count: Maximum number of methods to return.
            exclude_method: If set, skip methods whose body contains this string.

        The extracted exemplars are annotated with information about any private
        helper methods they call, so the LLM knows not to copy them blindly.
        """
        exemplars: List[str] = []

        for filepath in files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except OSError:
                continue

            # Pre-extract helper methods from this file
            helpers = self._extract_helper_methods(content)

            lines = content.split('\n')
            in_test = False
            current: List[str] = []
            brace_depth = 0
            found_target = False

            for line in lines:
                stripped = line.strip()

                if stripped.startswith('@Test'):
                    in_test = True
                    current = [line]
                    brace_depth = 0
                    found_target = (target_method is None)  # accept all if no filter
                    continue

                if in_test:
                    current.append(line)
                    brace_depth += line.count('{') - line.count('}')

                    if target_method and target_method in line:
                        found_target = True

                    # Method ended
                    if brace_depth <= 0 and '{' in ''.join(current):
                        method_text = '\n'.join(current)
                        should_exclude = (
                            exclude_method and exclude_method in method_text
                        )
                        if (found_target
                                and len(current) > 2
                                and len(method_text) < self._MAX_METHOD_CHARS
                                and not should_exclude):
                            # Annotate with helper method info
                            called = self._find_helper_calls_in_exemplar(
                                method_text, helpers
                            )
                            if called:
                                method_text = self._annotate_exemplar_with_helpers(
                                    method_text, helpers, called
                                )
                            exemplars.append(method_text)
                        in_test = False
                        current = []

                        if len(exemplars) >= max_count:
                            break

            if len(exemplars) >= max_count:
                break

        return exemplars

    @staticmethod
    def _select_within_budget(
        items: List[str], budget: int
    ) -> Tuple[List[str], int]:
        """Select items that fit within a character budget."""
        selected: List[str] = []
        used = 0
        for item in items:
            if used + len(item) > budget:
                break
            selected.append(item)
            used += len(item)
        return selected, used

    # ── Section formatters ────────────────────────────────────────────────

    @staticmethod
    def _format_test_pattern_section(cls: str, exemplars: List[str]) -> str:
        """Format Layer 1 results: existing test patterns."""
        return (
            "\n### 📋 Existing Test Patterns (FOLLOW THIS STYLE)\n"
            f"The following are real test methods from the project's existing test suite for `{cls}`.\n"
            "**You MUST follow the same testing pattern**: construct the object via its public constructor,\n"
            "navigate to the correct state using public API calls, then call the target method and assert.\n"
            "Do NOT use Mock classes, subclasses, or reflection.\n\n"
            "```java\n"
            + "\n\n".join(exemplars)
            + "\n```"
        )

    @staticmethod
    def _format_usage_example_section(
        cls: str, method_name: str, exemplars: List[str]
    ) -> str:
        """Format Layer 2 results: usage examples from the codebase."""
        return (
            f"\n### 📌 Usage Examples of `{cls}.{method_name}()` in the Codebase\n"
            "The following code snippets show how other parts of the codebase call this method.\n"
            "Use these to understand the correct way to set up the object and invoke the method.\n"
            "Do NOT copy internal implementation details; focus on the public API call patterns.\n\n"
            "```java\n"
            + "\n\n".join(exemplars)
            + "\n```"
        )

    @staticmethod
    def _format_sibling_test_section(cls: str, exemplars: List[str]) -> str:
        """Format Layer 3 results: sibling test templates."""
        return (
            f"\n### 📎 Test Templates for `{cls}` (other methods)\n"
            f"No existing tests were found for the target method, but the following tests for\n"
            f"other methods of `{cls}` show the general testing pattern (object construction,\n"
            "state navigation, assertion style). **Adapt this pattern** for the target method.\n"
            "Do NOT use Mock classes, subclasses, or reflection.\n\n"
            "```java\n"
            + "\n\n".join(exemplars)
            + "\n```"
        )

    # ------------------------------------------------------------------
    # Main retrieval entry point
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        code: str,
        cls: str = None,
        top_k: int = 3,
        target_class: str = None,
        method_signature: str = None,
    ) -> str:
        """Retrieve and assemble context for the given code snippet."""
        cls = cls or target_class or ""

        self._log("开始智能检索...")

        # ── Step 1: LLM dependency analysis ──────────────────────────────
        deps = await self.analyze_dependencies(code, cls)

        # ── Step 2: Static type extraction ───────────────────────────────
        self._log("静态分析类型依赖...")
        static_types = self._extract_types_from_code(code, cls, method_signature)
        all_types = list(dict.fromkeys(
            static_types + deps.get("types", [])  # preserve order, deduplicate
        ))
        self._log(f"合并后需检索的类型: {all_types}")

        # ── Step 3: Assemble context sections ────────────────────────────
        parts: List[str] = []
        seen_sigs: Set[str] = set()
        seen_types: Set[str] = set()
        retrieval_log: Dict[str, List[str]] = {"found": [], "not_found": []}

        # 3a. Primary class structure
        if cls:
            class_parts = self._build_class_section(cls, deps)
            if class_parts:
                parts.extend(class_parts)
                retrieval_log["found"].append(f"类定义: {cls}")
            else:
                retrieval_log["not_found"].append(f"类定义: {cls}")

        # 3a+. Overload disambiguation: when the target method has overloads,
        #       add a clear section telling the LLM which exact overload is under test.
        if cls and method_signature:
            method_name = self._extract_method_name(method_signature)
            info = self.rag.class_info.get(cls)
            if info and method_name:
                overloads = [
                    m for m in info.methods
                    if m.get('name') == method_name and 'public' in m.get('modifiers', [])
                ]
                if len(overloads) > 1:
                    overload_sigs = [self._clean_method_signature(m) for m in overloads]
                    # Deduplicate
                    seen_ol = set()
                    unique_overloads = []
                    for s in overload_sigs:
                        if s not in seen_ol:
                            seen_ol.add(s)
                            unique_overloads.append(s)
                    section = (
                        f"\n### ⚠️ OVERLOAD DISAMBIGUATION for `{method_name}`\n"
                        f"This class has **{len(unique_overloads)} overloads** of `{method_name}`. "
                        f"The **target method under test** is:\n"
                        f"  **→ {method_signature}**\n\n"
                        f"All overloads:\n" +
                        "\n".join(f"  {'→ ' if method_signature.strip() in s or s in method_signature.strip() else '  '}{s}" for s in unique_overloads) +
                        f"\n\n**Your tests MUST call the exact target overload above.** "
                        f"Pay attention to parameter types to avoid ambiguous method references."
                    )
                    parts.append(section)
                    self._log(f"重载消歧: {method_name} 有 {len(unique_overloads)} 个重载")
                    retrieval_log["found"].append(f"重载消歧: {method_name} ({len(unique_overloads)} overloads)")

        # 3b. Dependent methods
        method_parts, method_log = self._build_method_sections(deps, cls, seen_sigs)
        parts.extend(method_parts)
        retrieval_log["found"].extend(method_log["found"])
        retrieval_log["not_found"].extend(method_log["not_found"])

        # 3c. Dependent types
        type_parts, type_log = self._build_type_sections(all_types, seen_types)
        parts.extend(type_parts)
        retrieval_log["found"].extend(type_log["found"])
        retrieval_log["not_found"].extend(type_log["not_found"])

        # 3d. Semantic search supplement (with LLM-rewritten query)
        self._log("语义搜索补充（LLM query改写）...")
        semantic_query = await self.rewrite_query(code, cls, method_signature or "")
        self._log(f"语义搜索query: {semantic_query[:100]}")
        semantic_parts = self._build_semantic_sections(semantic_query, top_k, deps, seen_sigs)
        parts.extend(semantic_parts)

        # 3e. Test exemplar & usage examples (multi-layer retrieval)
        if method_signature:
            self._log("多层检索: 测试模式 / 调用范例 / 同类模板...")
            exemplar_parts = self._build_test_exemplar_section(cls, method_signature)
            if exemplar_parts:
                parts.extend(exemplar_parts)
                retrieval_log["found"].append("测试示例/调用范例 (multi-layer exemplars)")

        # 3f. Test framework & assertion library detection
        fw_info = self.detect_test_framework()
        fw_section = (
            "\n### 🔧 Project Test Framework\n"
            f"- Test framework: **{fw_info['test_framework']}**\n"
            f"- Assertion library: **{fw_info['assertion_lib']}**\n"
            f"- Import: `{fw_info['assertion_import']}`\n"
            f"- Style: `{fw_info['assertion_style']}`\n"
            f"\n**You MUST use the assertion library above.** Do NOT use other assertion libraries.\n"
        )
        parts.insert(0, fw_section)  # Insert at the beginning for high visibility
        retrieval_log["found"].append(f"测试框架: {fw_info['test_framework']}/{fw_info['assertion_lib']}")

        # ── Step 4: Context size guard ────────────────────────────────────
        context = "\n".join(parts)
        if len(context) > self._CONTEXT_SOFT_LIMIT:
            self._log(
                f"⚠ 上下文超过软限制 ({len(context)} > {self._CONTEXT_SOFT_LIMIT} chars)，"
                "建议减少 top_k 或缩小检索范围"
            )

        # ── Step 5: Print retrieval summary ──────────────────────────────
        if self.verbose:
            print("\n[Agentic RAG] 检索总结:")
            print(f"  ✓ 找到: {len(retrieval_log['found'])} 项")
            for item in retrieval_log["found"]:
                print(f"    - {item}")
            if retrieval_log["not_found"]:
                print(f"  ✗ 未找到: {len(retrieval_log['not_found'])} 项")
                for item in retrieval_log["not_found"]:
                    print(f"    - {item}")
            print(f"  📄 上下文总长度: {len(context)} chars")

        return context

    # Backward-compatible alias
    retrieve_by_agent = retrieve


async def retrieve_context_agentic(
    code: str, index_path: str, cls: str = "", top_k: int = 3
) -> str:
    """Convenience function for one-shot agentic retrieval."""
    rag = AgenticRAG(index_path)
    return await rag.retrieve(code, cls, top_k)
