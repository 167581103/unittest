import os
import json
import numpy as np
import faiss
from typing import List, Dict, Optional
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
from rag.base_rag import BaseRAG
from rag.code_rag.java_parser import JavaMethodParser

class CommentRAG(BaseRAG):
    """基于注释的代码RAG检索系统（余弦相似度版）"""

    def __init__(self, llm=None, embeddings=None, index_path: Optional[str] = None):
        super().__init__(llm, embeddings, index_path)
        self.embeddings_list = []
        self.metadata = {}  # comment -> method 的映射字典

        # 加载已有索引
        if self.index_path and os.path.exists(self.index_path):
            self._load_index_from_file(self.index_path)

    def _save_index_to_file(self, index_path: str) -> None:
        """保存索引和元数据"""
        if not self.faiss_index:
            raise ValueError("索引未构建，无法保存")

        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        faiss.write_index(self.faiss_index, index_path)

        # 保存元数据映射（comment -> method）
        metadata_path = index_path.replace('.index', '_metadata.json')
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, ensure_ascii=False)

    def _load_index_from_file(self, index_path: str) -> None:
        """加载索引和元数据"""
        self.faiss_index = faiss.read_index(index_path)

        # 加载元数据映射（comment -> method）
        metadata_path = index_path.replace('.index', '_metadata.json')
        with open(metadata_path, 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)

        # 构建文档列表
        self.docs = list(self.metadata.keys())

    def load_documents(self, java_file_path: str) -> None:
        """加载Java文件并构建余弦相似度索引"""
        # 优先加载已有索引
        if self.index_path and os.path.exists(self.index_path):
            self._load_index_from_file(self.index_path)
            return

        # 解析Java文件
        parser = JavaMethodParser(java_file_path)
        code_blocks = parser.get_code_blocks()

        # 生成嵌入向量并建立元数据映射
        if self.embeddings:
            self.embeddings_list = []
            self.docs = []
            self.metadata = {}  # comment -> method 的映射

            for block in code_blocks:
                text_to_embed = block['comment'] if block['comment'] != "No comment available" else block['method_signature']
                embed_response = self.embeddings.embed_text(text_to_embed)
                embedding = embed_response["data"][0]["embedding"]
                self.embeddings_list.append(embedding)

                # 建立文档列表
                self.docs.append(text_to_embed)

                # 建立元数据映射（comment -> method）
                self.metadata[text_to_embed] = block

            # 构建余弦相似度索引
            embedding_matrix = np.array(self.embeddings_list).astype('float32')
            faiss.normalize_L2(embedding_matrix)  # 向量归一化
            self.faiss_index = faiss.IndexFlatL2(embedding_matrix.shape[1])
            self.faiss_index.add(embedding_matrix)

            # 保存索引
            if self.index_path:
                self._save_index_to_file(self.index_path)
        else:
            raise ValueError("嵌入模型未提供")

    def get_most_relevant_docs(self, query: str, top_k: int = 3) -> List[Dict]:
        """检索最相关的文档，返回文档+对应的方法"""
        if not self.docs:
            raise ValueError("文档未加载，请先调用 load_documents()")

        # 生成查询向量并归一化
        embed_response = self.embeddings.embed_text(query)
        query_embedding = np.array(embed_response["data"][0]["embedding"], dtype='float32').reshape(1, -1)
        faiss.normalize_L2(query_embedding)

        # 余弦相似度检索
        distances, indices = self.faiss_index.search(query_embedding, top_k)
        valid_results = [(idx, distance) for distance, idx in zip(distances[0], indices[0]) if 0 <= idx < len(self.docs)]
        valid_results.sort(key=lambda x: x[1])

        # 返回文档+对应的方法
        results = []
        for idx, distance in valid_results[:top_k]:
            doc = self.docs[idx]
            method_info = self.metadata[doc]
            method_info['similarity_distance'] = float(distance)
            results.append(method_info)

        return results

    def generate_answer(self, query: str, relevant_docs: List[Dict]) -> str:
        """生成回答"""
        if not self.llm:
            raise ValueError("LLM未提供，无法生成答案")

        # 构建包含方法签名和注释的文档文本
        docs_text = "\n\n".join([
            f"方法 {i+1}:\n  方法签名: {doc['method_signature']}\n  文档说明: {doc['comment']}"
            for i, doc in enumerate(relevant_docs)
        ])

        prompt = f"问题: {query}\n\n相关方法:\n{docs_text}\n\n请基于以上方法信息回答问题，在回答中提供相关方法的完整签名。"
        messages = [
            ("system", "You are a helpful assistant that answers questions based on given method signatures and their documentation. Always include the full method signature in your answer."),
            ("human", prompt),
        ]

        ai_msg = self.llm.invoke(messages)
        return ai_msg.content

    def get_code_blocks_with_details(self, query: str, top_k: int = 3) -> List[Dict]:
        """获取带详细信息的检索结果（直接调用get_most_relevant_docs即可）"""
        results = self.get_most_relevant_docs(query, top_k)
        # 添加排名
        for i, result in enumerate(results):
            result['rank'] = i + 1
        return results