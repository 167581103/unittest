"""
代码RAG抽象基类 - 定义RAG系统的标准接口
"""

import os
from abc import ABC, abstractmethod
from typing import List, Optional
from langchain_litellm import ChatLiteLLM
from llm.llm import Embedding


class BaseRAG(ABC):
    """RAG系统抽象基类"""

    def __init__(
        self,
        llm: Optional[ChatLiteLLM] = None,
        embeddings: Optional[Embedding] = None,
        index_path: Optional[str] = None
    ):
        """
        初始化 RAG 系统

        Args:
            llm: 大语言模型实例（用于生成答案）
            embeddings: 嵌入模型实例（用于计算向量）
            index_path: 可选的FAISS索引文件路径
        """
        self.llm = llm
        self.embeddings = embeddings
        self.index_path = index_path
        self.faiss_index = None
        self.docs = None
        self.doc_embeddings = None

    def load_index(self, index_path: Optional[str] = None) -> None:
        """
        从文件加载索引（公共方法）

        Args:
            index_path: FAISS索引文件路径，如果不提供则使用初始化时指定的路径
        """
        path = index_path or self.index_path
        if not path:
            raise ValueError("未指定索引路径")
        if not os.path.exists(path):
            raise FileNotFoundError(f"索引文件不存在: {path}")
        self._load_index_from_file(path)

    @abstractmethod
    def _load_index_from_file(self, index_path: str) -> None:
        """
        从文件加载索引（内部方法，子类实现）

        Args:
            index_path: FAISS索引文件路径
        """
        pass

    @abstractmethod
    def load_documents(self, documents: List[str]) -> None:
        """
        读取文档

        Args:
            documents: 文档列表
        """
        pass

    @abstractmethod
    def get_most_relevant_docs(self, query: str, top_k: int = 3) -> List[str]:
        """
        找到最相关内容

        Args:
            query: 查询文本
            top_k: 返回最相关的文档数量

        Returns:
            最相关的文档列表
        """
        pass

    @abstractmethod
    def generate_answer(self, query: str, relevant_docs: List[str]) -> str:
        """
        生成答案

        Args:
            query: 查询文本
            relevant_docs: 相关的文档列表

        Returns:
            生成的答案
        """
        pass
