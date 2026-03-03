import os
import numpy as np
import faiss
from typing import List, Dict, Optional
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent))
from rag.base_rag import BaseRAG
from rag.code_rag.java_parser import JavaMethodParser

# 导入LLM客户端
from llm.llm import client


class CodeRAG(BaseRAG):
    """代码RAG检索系统"""

    def __init__(self, llm=None, embeddings=None, index_path: Optional[str] = None):
        super().__init__(llm, embeddings, index_path)
        self.method_blocks = []
        self.embeddings_list = []

        # 加载已有索引
        if self.index_path and os.path.exists(self.index_path):
            self._load_index_from_file(self.index_path)

    def _save_index_to_file(self, index_path: str) -> None:
        """保存索引"""
        if not self.faiss_index:
            raise ValueError("索引未构建，无法保存")
        
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        faiss.write_index(self.faiss_index, index_path)

    def _load_index_from_file(self, index_path: str) -> None:
        """加载索引"""
        self.faiss_index = faiss.read_index(index_path)

    def load_documents(self, java_file_path: str) -> None:
        """加载Java文件并构建余弦相似度索引"""
        # 优先加载已有索引
        if self.index_path and os.path.exists(self.index_path):
            print(f"[√] 加载已有索引: {self.index_path}")
            self._load_index_from_file(self.index_path)
            # 加载索引后仍需解析Java文件获取方法块
            print(f"[√] 解析Java文件: {java_file_path}")
            parser = JavaMethodParser(java_file_path)
            self.method_blocks = parser.get_code_blocks()
            print(f"[√] 提取到 {len(self.method_blocks)} 个方法")
            return

        # 解析Java文件
        print(f"[√] 解析Java文件: {java_file_path}")
        parser = JavaMethodParser(java_file_path)
        self.method_blocks = parser.get_code_blocks()

        print(f"[√] 提取到 {len(self.method_blocks)} 个方法")

        # 生成嵌入向量
        if client:
            self.embeddings_list = []
            for i, block in enumerate(self.method_blocks):
                text_to_embed = block['method_signature'] + " \n" + block['code']

                try:
                    embed_response = client.embed_single(text_to_embed)
                    embedding = embed_response["data"][0]["embedding"]
                    self.embeddings_list.append(embedding)

                    if (i + 1) % 10 == 0:
                        print(f"  进度: {i + 1}/{len(self.method_blocks)}")
                except Exception as e:
                    print(f"[×] 嵌入生成失败: {e}")
                    continue

            print(f"[√] 向量生成完成，共 {len(self.embeddings_list)} 个向量")

            # 构建FAISS索引
            if self.embeddings_list:
                embedding_matrix = np.array(self.embeddings_list).astype('float32')

                # 使用L2距离的索引
                self.faiss_index = faiss.IndexFlatL2(embedding_matrix.shape[1])
                self.faiss_index.add(embedding_matrix)

                # 构建文档列表
                self.docs = []
                for block in self.method_blocks:
                    self.docs.append(block['method_signature'] + " \n" + block['code'])

                # 保存索引
                if self.index_path:
                    self._save_index_to_file(self.index_path)
                    print(f"[√] 索引已保存到: {self.index_path}")
            else:
                raise ValueError("没有生成有效的嵌入向量")
        else:
            raise ValueError("嵌入模型客户端未提供")

    def get_most_relevant_docs(self, query: str, top_k: int = 3) -> List[Dict]:
        """搜索最相关的方法体"""
        if not self.faiss_index:
            print("[×] 索引未加载")
            return []

        print(f"\n[→] 查询: {query}")

        # 生成查询向量
        try:
            embed_response = client.embed_single(query)
            query_embedding = np.array([embed_response["data"][0]["embedding"]]).astype('float32')
        except Exception as e:
            print(f"[×] 查询嵌入生成失败: {e}")
            return []

        # FAISS搜索
        distances, indices = self.faiss_index.search(query_embedding, top_k)

        # 返回结果
        results = []
        for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
            if idx < len(self.method_blocks):
                block = self.method_blocks[idx].copy()
                block['similarity_distance'] = float(distance)
                block['rank'] = i + 1
                results.append(block)

        return results

    def generate_answer(self, query: str, relevant_docs: List[Dict]) -> str:
        """生成回答"""
        if not self.llm:
            raise ValueError("LLM未提供，无法生成答案")

        # 构建包含方法签名和代码的文档文本
        docs_text = "\n\n".join([
            f"方法 {i+1}:\n  方法签名: {doc.get('method_signature', 'N/A')}\n  注释: {doc.get('comment', 'N/A')}\n  代码: {doc.get('code', 'N/A')[:500]}..."
            for i, doc in enumerate(relevant_docs)
        ])

        prompt = f"问题: {query}\n\n相关方法:\n{docs_text}\n\n请基于以上方法信息回答问题，在回答中提供相关方法的完整签名。"
        messages = [
            ("system", "You are a helpful assistant that answers questions based on given method signatures, documentation, and code. Always include the full method signature in your answer."),
            ("human", prompt),
        ]

        ai_msg = self.llm.invoke(messages)
        return ai_msg.content

    def print_results(self, results: List[Dict]):
        """打印检索结果"""
        print(f"\n[→] 找到 {len(results)} 个匹配的方法:\n")

        for i, result in enumerate(results, 1):
            print(f"{'='*60}")
            print(f"排名 #{result['rank']}")
            print(f"相似度距离: {result['similarity_distance']:.4f} (越小越相似)")
            print(f"方法名: {result['method_signature'][:50] if 'method_signature' in result else result.get('method_name', 'N/A')}")
            print(f"文件: {result['file']}:{result['start_line']}")
            print(f"\n方法体代码:\n{result['body'][:800]}...")
            print(f"{'='*60}\n")