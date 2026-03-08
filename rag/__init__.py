"""
RAG模块 - 代码检索（传统RAG + Agentic RAG）

功能：
1. 离线构建代码库索引
2. 传统RAG：基于向量相似度的语义检索
3. Agentic RAG：LLM Agent驱动的智能检索

使用示例：
    # 构建索引（离线，只需运行一次）
    from rag import CodeRAG
    rag = CodeRAG()
    rag.build_index("/path/to/project", "./rag.index")

    # 传统RAG检索
    rag = CodeRAG("./rag.index")
    context = rag.get_context_for_prompt(target_method, target_class="MyClass", top_k=5)

    # Agentic RAG检索（推荐）
    from rag import AgenticRAG
    agentic_rag = AgenticRAG("./rag.index")
    context = await agentic_rag.retrieve_by_agent(target_method, target_class="MyClass")
"""

from rag.code_rag import (
    CodeRAG,
    AgenticRAG,
    build_code_index,
    retrieve_context,
    retrieve_context_agentic
)

__all__ = [
    "CodeRAG",
    "AgenticRAG",
    "build_code_index",
    "retrieve_context",
    "retrieve_context_agentic"
]
