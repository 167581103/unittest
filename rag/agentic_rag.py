"""
Agentic RAG - LLM驱动智能检索

流程：LLM分析依赖 -> 检索上下文 -> 格式化返回
"""

import json
import re
from typing import Dict, List

from .code_rag import CodeRAG


class AgenticRAG:
    """Agentic RAG"""

    def __init__(self, index_path: str, test_dir: str = None):
        self.rag = CodeRAG(index_path)
        self.test_dir = test_dir  # 保留但不使用

    async def analyze_dependencies(self, code: str, cls: str) -> Dict[str, List[str]]:
        """LLM分析代码依赖"""
        from llm import chat, PROMPTS

        prompt = PROMPTS["deps_analysis"].format(cls=cls, code=code)
        try:
            resp = await chat(prompt, temperature=0.1)
            match = re.search(r'\{[^{}]*\}', resp, re.DOTALL)
            return json.loads(match.group()) if match else {"methods": [], "fields": [], "types": []}
        except:
            return {"methods": [], "fields": [], "types": []}

    def _find_method(self, name: str, target_cls: str) -> tuple:
        """查找方法定义"""
        for cls_name in [target_cls] + list(self.rag.class_info.keys()):
            if cls_name not in self.rag.class_info:
                continue
            for m in self.rag.class_info[cls_name].methods:
                if name in m.get("signature", ""):
                    return m, cls_name
        return None, None

    def _find_type(self, name: str) -> dict:
        """查找类型定义"""
        for cls_name, info in self.rag.class_info.items():
            if cls_name == name or cls_name.endswith("." + name):
                return info
        return None

    async def retrieve(self, code: str, cls: str = None, top_k: int = 3, target_class: str = None) -> str:
        """检索上下文"""
        # 兼容两种参数名
        cls = cls or target_class
        # 1. LLM分析依赖
        deps = await self.analyze_dependencies(code, cls)
        
        parts = []
        
        # 2. 类结构
        if cls in self.rag.class_info:
            info = self.rag.class_info[cls]
            parts.append(f"## {info.package}.{cls}")
            
            if info.imports:
                parts.append("\n### Imports\n" + "\n".join(f"import {i};" for i in info.imports[:10]))
            
            if info.fields:
                needed = deps.get("fields", [])
                fields = [f for f in info.fields if any(n in f.get("name", "") for n in needed)]
                if fields:
                    parts.append("\n### Fields\n" + "\n".join(f"- {f['signature']}" for f in fields[:5]))
            
            if info.constants:
                parts.append("\n### Constants\n" + "\n".join(f"- {c['signature']}" for c in info.constants[:10]))
            
            if info.constructors:
                parts.append("\n### Constructors\n" + "\n".join(f"- {c['signature']}" for c in info.constructors[:3]))

        # 3. 依赖方法
        for name in deps.get("methods", [])[:6]:
            method, owner = self._find_method(name, cls)
            if method:
                marker = "" if owner == cls else f" [{owner}]"
                parts.append(f"\n### {method['signature']}{marker}")
                if owner == cls and method.get("code"):
                    parts.append(f"```java\n{method['code'][:500]}\n```")

        # 4. 依赖类型
        for name in deps.get("types", [])[:4]:
            type_info = self._find_type(name)
            if type_info:
                parts.append(f"\n### {type_info.package}.{type_info.name}")
                if type_info.constants:
                    parts.append("Constants: " + ", ".join(c['signature'] for c in type_info.constants[:10]))

        # 5. 语义搜索补充
        for block, _ in self.rag.search(code, top_k=top_k)[:2]:
            sig = block.signature
            if not any(m in sig for m in deps.get("methods", [])):
                parts.append(f"\n### Related: {sig}\n```java\n{block.code[:400]}\n```")

        return "\n".join(parts)

    # 别名，保持向后兼容
    retrieve_by_agent = retrieve


async def retrieve_context_agentic(code: str, index_path: str, cls: str = "", top_k: int = 3) -> str:
    """便捷函数"""
    rag = AgenticRAG(index_path)
    return await rag.retrieve(code, cls, top_k)
