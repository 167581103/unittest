"""
Agentic RAG - LLM-driven intelligent retrieval

Pipeline: LLM dependency analysis -> static analysis augmentation -> multi-strategy retrieval -> context assembly
"""

import json
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

    def __init__(self, index_path: str, test_dir: str = None, verbose: bool = True):
        self.rag = CodeRAG(index_path)
        self.test_dir = test_dir
        self.verbose = verbose
        # Simple in-memory cache: code_hash -> deps dict
        self._deps_cache: Dict[str, Dict[str, List[str]]] = {}

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        """Print a verbose log line."""
        if self.verbose:
            print(f"  [AgenticRAG] {msg}")

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

    def _build_class_section(self, cls: str, deps: Dict[str, List[str]]) -> List[str]:
        """Build the primary class structure section."""
        info = self.rag.class_info.get(cls)
        if not info:
            return []

        parts = [f"## {info.package}.{cls}"]

        if info.imports:
            parts.append(
                "\n### Imports\n" +
                "\n".join(f"import {i};" for i in info.imports[:15])
            )

        if info.fields:
            needed = set(deps.get("fields", []))
            # Include all fields if no specific ones requested, else filter
            fields = (
                [f for f in info.fields if any(n in f.get("name", "") for n in needed)]
                if needed else info.fields
            )
            if fields:
                parts.append(
                    "\n### Fields\n" +
                    "\n".join(f"  - {f['signature']}" for f in fields[:10])
                )

        if info.constants:
            parts.append(
                "\n### Constants\n" +
                "\n".join(f"  - {c['signature']}" for c in info.constants[:15])
            )

        if info.constructors:
            parts.append(
                "\n### Constructors\n" +
                "\n".join(f"  - {c['signature']}" for c in info.constructors[:5])
            )

        # List all public methods to prevent LLM hallucinating non-existent APIs
        if info.methods:
            public_methods = [
                m for m in info.methods if 'public' in m.get('signature', '')
            ]
            if public_methods:
                method_sigs = [
                    m.get('signature', '').split('{')[0].strip()
                    for m in public_methods[:30]
                ]
                parts.append(
                    "\n### Available Public Methods (ONLY use these)\n" +
                    "\n".join(f"  - {s}" for s in method_sigs)
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
        self, code: str, top_k: int, deps: Dict[str, List[str]], seen_sigs: Set[str]
    ) -> List[str]:
        """Semantic search to supplement with related code blocks."""
        parts = []
        dep_methods = set(deps.get("methods", []))
        results = self.rag.search(code, top_k=top_k)

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

        print("\n[Agentic RAG] 开始智能检索...")

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

        # 3d. Semantic search supplement
        self._log("语义搜索补充...")
        semantic_parts = self._build_semantic_sections(code, top_k, deps, seen_sigs)
        parts.extend(semantic_parts)

        # ── Step 4: Context size guard ────────────────────────────────────
        context = "\n".join(parts)
        if len(context) > self._CONTEXT_SOFT_LIMIT:
            self._log(
                f"⚠ 上下文超过软限制 ({len(context)} > {self._CONTEXT_SOFT_LIMIT} chars)，"
                "建议减少 top_k 或缩小检索范围"
            )

        # ── Step 5: Print retrieval summary ──────────────────────────────
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
