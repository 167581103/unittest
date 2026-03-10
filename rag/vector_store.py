"""
向量存储 - FAISS 索引管理
"""

import os
import json
import faiss
import numpy as np
from typing import List, Optional
from pathlib import Path


class VectorStore:
    """FAISS向量存储"""
    
    def __init__(self, index_path: Optional[str] = None):
        self.index_path = index_path
        self.index = None
        self.metadata = []
        
        if index_path and os.path.exists(index_path):
            self.load(index_path)
    
    def build(self, vectors: List[List[float]], metadata: List[dict]) -> None:
        """构建向量索引"""
        if not vectors:
            return
        
        matrix = np.array(vectors).astype('float32')
        dim = matrix.shape[1]
        
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(matrix)
        self.metadata = metadata
    
    def search(self, query_vector: List[float], top_k: int = 5) -> List[tuple]:
        """搜索相似向量"""
        if not self.index:
            return []
        
        query = np.array([query_vector]).astype('float32')
        distances, indices = self.index.search(query, top_k)
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.metadata):
                results.append((self.metadata[idx], distances[0][i]))
        
        return results
    
    def save(self, path: str) -> None:
        """保存索引和元数据"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        # 保存FAISS索引
        faiss.write_index(self.index, path)
        
        # 保存元数据
        meta_path = path + '.meta'
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f)
    
    def load(self, path: str) -> None:
        """加载索引和元数据"""
        # 加载FAISS索引
        self.index = faiss.read_index(path)
        
        # 加载元数据
        meta_path = path + '.meta'
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
