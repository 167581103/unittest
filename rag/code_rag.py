"""
CodeRAG - 代码检索
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Optional
from tqdm import tqdm

from .tree_parser import JavaParser, CodeBlock, ClassInfo
from .vector_store import VectorStore
from llm import embed


class CodeRAG:
    """代码检索器"""
    
    def __init__(self, index_path: Optional[str] = None):
        self.index_path = index_path
        self.parser = JavaParser()
        self.store = VectorStore()
        self.blocks: List[CodeBlock] = []
        self.class_info: Dict[str, ClassInfo] = {}
        
        if index_path and os.path.exists(index_path):
            self._load(index_path)
    
    @property
    def index(self):
        """向后兼容：返回向量索引"""
        return self.store.index
    
    def build_index(self, project_dir: str, index_path: str, batch_size: int = 50) -> None:
        """构建代码索引"""
        print(f"[→] 开始构建索引: {project_dir}")
        
        # 1. 解析Java文件
        java_files = list(Path(project_dir).rglob("*.java"))
        print(f"[√] 找到 {len(java_files)} 个Java文件")
        
        for java_file in tqdm(java_files, desc="解析文件"):
            blocks, cls_info = self.parser.parse_file(str(java_file))
            self.blocks.extend(blocks)
            if cls_info:
                self.class_info[cls_info.name] = cls_info
        
        print(f"[√] 提取 {len(self.blocks)} 个代码块")
        print(f"[√] 解析 {len(self.class_info)} 个类定义")
        
        # 2. 生成向量
        vectors = []
        metadata = []
        failed_batches = []
        
        for i in tqdm(range(0, len(self.blocks), batch_size), desc="生成向量"):
            batch = self.blocks[i:i + batch_size]
            texts = [f"{b.type}: {b.signature}\n{b.code}" for b in batch]
            
            try:
                batch_vectors = embed(texts)
                vectors.extend(batch_vectors)
                metadata.extend([{'idx': i + j} for j in range(len(batch))])
            except Exception as e:
                print(f"  [×] 批次 {i} 失败: {e}")
                failed_batches.append((i, batch, texts))
                dim = len(vectors[0]) if vectors else 1024
                vectors.extend([[0.0] * dim] * len(batch))
                metadata.extend([{'idx': i + j} for j in range(len(batch))])
        
        # 重试失败的批次
        if failed_batches:
            print(f"  [!] 重试 {len(failed_batches)} 个失败批次...")
            for i, batch, texts in failed_batches:
                for j, text in enumerate(texts):
                    try:
                        vec = embed([text])
                        idx = i + j
                        if idx < len(vectors):
                            vectors[idx] = vec[0]
                    except:
                        pass
        
        # 3. 构建向量索引
        self.store.build(vectors, metadata)
        self._save(index_path)
        
        print(f"[√] 索引构建完成: {index_path}")
    
    def search(self, query: str, top_k: int = 5) -> List[tuple]:
        """搜索相关代码"""
        try:
            query_vector = embed([query])[0]
            results = self.store.search(query_vector, top_k)
            # 将 metadata 转换为实际的代码块
            return [(self.blocks[r[0]['idx']], r[1]) for r in results]
        except Exception as e:
            print(f"[×] 搜索失败: {e}")
            return []
    
    def get_class_info(self, class_name: str) -> Optional[ClassInfo]:
        """获取类信息"""
        return self.class_info.get(class_name)
    
    def get_block(self, idx: int) -> Optional[CodeBlock]:
        """获取代码块"""
        if 0 <= idx < len(self.blocks):
            return self.blocks[idx]
        return None
    
    def _save(self, path: str) -> None:
        """保存索引"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        # 保存向量索引
        self.store.save(path)
        
        # 保存代码块
        blocks_path = path + '.blocks'
        with open(blocks_path, 'w', encoding='utf-8') as f:
            json.dump([b.__dict__ for b in self.blocks], f, ensure_ascii=False)
        
        # 保存类信息
        class_path = path + '.class'
        with open(class_path, 'w', encoding='utf-8') as f:
            json.dump({k: v.__dict__ for k, v in self.class_info.items()}, f, ensure_ascii=False)
    
    def _load(self, path: str) -> None:
        """加载索引"""
        # 加载向量索引
        self.store.load(path)
        
        # 加载代码块
        blocks_path = path + '.blocks'
        if os.path.exists(blocks_path):
            with open(blocks_path, 'r', encoding='utf-8') as f:
                self.blocks = [CodeBlock(**b) for b in json.load(f)]
        
        # 加载类信息
        class_path = path + '.class'
        if os.path.exists(class_path):
            with open(class_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.class_info = {k: ClassInfo(**v) for k, v in data.items()}
        
        print(f"[√] 索引加载完成，共 {len(self.blocks)} 个代码块，{len(self.class_info)} 个类定义")
