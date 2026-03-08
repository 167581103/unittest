"""
CodeRAG - 增强版代码检索模块（使用tree-sitter解析）

功能：
1. 离线：构建大型代码库的FAISS索引（方法 + 类结构）
2. 在线：检索与目标方法相关的代码上下文（相似方法 + 目标类结构）
"""

import os
import json
import faiss
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict

# 使用llm模块的嵌入功能
from llm import embed, embed_single

# tree-sitter用于Java代码解析
try:
    from tree_sitter import Language, Parser
    import tree_sitter_java as tsjava
    TS_AVAILABLE = True
except ImportError:
    TS_AVAILABLE = False
    print("[警告] tree-sitter未安装，使用正则解析")


@dataclass
class CodeBlock:
    """代码块"""
    type: str  # "method", "field", "constant", "import", "constructor"
    signature: str
    code: str
    comment: str
    file: str
    class_name: str
    start_line: int


@dataclass
class ClassInfo:
    """类信息"""
    name: str
    file: str
    package: str
    imports: List[str]
    fields: List[Dict]  # 字段列表
    constants: List[Dict]  # 常量列表
    constructors: List[Dict]  # 构造函数
    methods: List[Dict]  # 方法列表
    super_class: Optional[str] = None
    interfaces: List[str] = None
    
    def __post_init__(self):
        if self.interfaces is None:
            self.interfaces = []


class CodeRAG:
    """
    增强版代码RAG检索器
    
    改进点：
    1. 索引类结构信息（字段、常量、导入、构造函数）
    2. 检索时提供完整的类上下文
    """
    
    def __init__(self, index_path: Optional[str] = None):
        self.index_path = index_path
        self.index = None
        self.blocks: List[CodeBlock] = []
        self.class_info: Dict[str, ClassInfo] = {}  # 类名 -> 类信息
        
        if index_path and os.path.exists(index_path):
            self._load_index(index_path)
    
    # ============ 离线：索引构建 ============
    
    def build_index(self, project_dir: str, index_path: str, batch_size: int = 50) -> None:
        """
        构建项目代码库的FAISS索引（包含类结构信息）
        
        Args:
            project_dir: Java项目根目录
            index_path: 索引保存路径
            batch_size: 批处理大小
        """
        print(f"[→] 开始构建索引: {project_dir}")
        
        # 1. 解析所有Java文件
        java_files = list(Path(project_dir).rglob("*.java"))
        print(f"[√] 找到 {len(java_files)} 个Java文件")
        
        # 2. 提取所有代码块（方法 + 类结构）
        all_blocks = []
        for java_file in java_files:
            blocks, class_info = self._parse_file_enhanced(str(java_file))
            all_blocks.extend(blocks)
            if class_info:
                self.class_info[class_info.name] = class_info
        
        print(f"[√] 提取 {len(all_blocks)} 个代码块")
        print(f"[√] 解析 {len(self.class_info)} 个类定义")
        
        # 3. 生成嵌入向量（批处理）
        embeddings = []
        for i in range(0, len(all_blocks), batch_size):
            batch = all_blocks[i:i + batch_size]
            texts = [f"{b.type}: {b.signature}\n{b.code}" for b in batch]
            
            try:
                batch_embeddings = embed(texts)
                embeddings.extend(batch_embeddings)
                
                if (i // batch_size + 1) % 10 == 0:
                    print(f"  进度: {min(i + batch_size, len(all_blocks))}/{len(all_blocks)}")
            except Exception as e:
                print(f"  [×] 批次 {i} 嵌入失败: {e}")
                dim = len(embeddings[0]) if embeddings else 1024
                embeddings.extend([[0.0] * dim] * len(batch))
        
        # 4. 构建FAISS索引
        embedding_matrix = np.array(embeddings).astype('float32')
        dim = embedding_matrix.shape[1]
        
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(embedding_matrix)
        self.blocks = all_blocks
        
        # 5. 保存索引和元数据
        self._save_index(index_path)
        print(f"[√] 索引构建完成，保存至: {index_path}")
    
    def _parse_file_enhanced(self, file_path: str) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        """
        使用tree-sitter解析Java文件，提取方法、字段、常量、导入等
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            return [], None
        
        blocks = []
        class_info = None
        
        # 使用tree-sitter解析
        if TS_AVAILABLE:
            try:
                blocks, class_info = self._parse_with_treesitter(file_path, content)
                if blocks:  # 如果解析成功
                    return blocks, class_info
            except Exception as e:
                print(f"  tree-sitter解析失败 {file_path}: {e}，使用正则回退")
        
        # 回退到正则解析
        return self._parse_with_regex(file_path, content)
    
    def _parse_with_treesitter(self, file_path: str, content: str) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        """使用tree-sitter解析Java代码"""
        blocks = []
        
        # 初始化parser
        parser = Parser(Language(tsjava.language()))
        tree = parser.parse(bytes(content, 'utf8'))
        root_node = tree.root_node
        
        # 提取包名
        package_name = ""
        for node in root_node.children:
            if node.type == "package_declaration":
                package_name = content[node.start_byte:node.end_byte].replace('package', '').replace(';', '').strip()
                break
        
        # 提取导入语句
        imports = []
        for node in root_node.children:
            if node.type == "import_declaration":
                imp_text = content[node.start_byte:node.end_byte].replace('import', '').replace(';', '').strip()
                imports.append(imp_text)
        
        # 查找类声明
        class_info = None
        class_name = None
        
        def find_classes(node):
            """递归查找类声明"""
            nonlocal class_info, class_name
            if node.type in ["class_declaration", "interface_declaration", "enum_declaration"]:
                # 提取类名
                for child in node.children:
                    if child.type == "identifier":
                        class_name = content[child.start_byte:child.end_byte]
                        break
                
                if class_name:
                    class_info = ClassInfo(
                        name=class_name,
                        file=file_path,
                        package=package_name,
                        imports=imports,
                        fields=[],
                        constants=[],
                        constructors=[],
                        methods=[]
                    )
                    
                    # 提取类体中的成员
                    for child in node.children:
                        if child.type == "class_body" or child.type == "enum_body":
                            self._extract_class_members(child, content, class_info, blocks, file_path, class_name)
            
            for child in node.children:
                find_classes(child)
        
        find_classes(root_node)
        return blocks, class_info
    
    def _extract_class_members(self, body_node, content: str, class_info: ClassInfo, blocks: List, file_path: str, class_name: str):
        """提取类体中的字段、方法、构造函数"""
        for child in body_node.children:
            # 提取字段声明
            if child.type == "field_declaration":
                field_text = content[child.start_byte:child.end_byte]
                line_num = content[:child.start_byte].count('\n') + 1
                
                # 检查是否为常量
                is_static = any(gc.type == "static" for gc in child.children)
                is_final = 'final' in field_text
                
                field_data = {
                    "signature": field_text.replace(';', '').strip(),
                    "name": "",
                    "value": None,
                    "is_private": 'private' in field_text
                }
                
                # 提取变量名
                for gc in child.children:
                    if gc.type == "variable_declarator":
                        for ggc in gc.children:
                            if ggc.type == "identifier":
                                field_data["name"] = content[ggc.start_byte:ggc.end_byte]
                                break
                            if ggc.type == "=":
                                field_data["value"] = content[gc.children[-1].start_byte:gc.children[-1].end_byte]
                        break
                
                if is_static and is_final:
                    class_info.constants.append(field_data)
                    block_type = "constant"
                else:
                    class_info.fields.append(field_data)
                    block_type = "field"
                
                blocks.append(CodeBlock(
                    type=block_type,
                    signature=field_data["signature"],
                    code=field_text,
                    comment="",
                    file=file_path,
                    class_name=class_name,
                    start_line=line_num
                ))
            
            # 提取方法声明
            elif child.type == "method_declaration":
                method_text = content[child.start_byte:child.end_byte]
                line_num = content[:child.start_byte].count('\n') + 1
                
                # 提取方法签名（不含方法体）
                sig_parts = []
                for gc in child.children:
                    if gc.type == "block":  # 方法体开始，停止
                        break
                    sig_parts.append(content[gc.start_byte:gc.end_byte])
                
                signature = ''.join(sig_parts).strip()
                
                method_data = {
                    "signature": signature,
                    "code": method_text,
                    "comment": ""
                }
                class_info.methods.append(method_data)
                
                blocks.append(CodeBlock(
                    type="method",
                    signature=signature,
                    code=method_text,
                    comment="",
                    file=file_path,
                    class_name=class_name,
                    start_line=line_num
                ))
            
            # 提取枚举常量
            elif child.type == "enum_constant":
                const_name = content[child.start_byte:child.end_byte]
                line_num = content[:child.start_byte].count('\n') + 1
                
                const_data = {
                    "signature": const_name,
                    "code": const_name,
                    "comment": "",
                    "name": const_name
                }
                class_info.constants.append(const_data)
                
                blocks.append(CodeBlock(
                    type="enum_constant",
                    signature=const_name,
                    code=const_name,
                    comment="",
                    file=file_path,
                    class_name=class_name,
                    start_line=line_num
                ))
            
            # 提取构造函数
            elif child.type == "constructor_declaration":
                ctor_text = content[child.start_byte:child.end_byte]
                line_num = content[:child.start_byte].count('\n') + 1
                
                sig_parts = []
                for gc in child.children:
                    if gc.type == "block":
                        break
                    sig_parts.append(content[gc.start_byte:gc.end_byte])
                
                signature = ''.join(sig_parts).strip()
                
                ctor_data = {
                    "signature": signature,
                    "code": ctor_text
                }
                class_info.constructors.append(ctor_data)
                
                blocks.append(CodeBlock(
                    type="constructor",
                    signature=signature,
                    code=ctor_text,
                    comment="",
                    file=file_path,
                    class_name=class_name,
                    start_line=line_num
                ))
    
    def _parse_with_regex(self, file_path: str, content: str) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        """使用正则表达式解析（回退方案）"""
        import re
        blocks = []
        class_info = None
        
        # 提取包名
        package_match = re.search(r'package\s+([\w.]+);', content)
        package = package_match.group(1) if package_match else ""
        
        # 提取导入语句
        imports = re.findall(r'import\s+([\w.]+(?:\.\*)?);', content)
        
        # 提取类定义
        class_pattern = r'(?:^|\{|;)\s*(public\s+)?\s*(class|interface|enum)\s+([A-Z]\w*)'
        class_match = re.search(class_pattern, content, re.MULTILINE)
        
        class_name = class_match.group(3) if class_match else None
        
        if class_name:
            class_info = ClassInfo(
                name=class_name,
                file=file_path,
                package=package,
                imports=imports,
                fields=[],
                constants=[],
                constructors=[],
                methods=[]
            )
        
        # 简单的方法提取
        method_pattern = r'(public\s+(?:static\s+)?[\w<>\[\]]+\s+\w+\s*\([^)]*\))\s*\{'
        for match in re.finditer(method_pattern, content):
            sig = match.group(1).strip()
            line_num = content[:match.start()].count('\n') + 1
            
            if class_info:
                class_info.methods.append({"signature": sig, "code": "", "comment": ""})
            
            blocks.append(CodeBlock(
                type="method",
                signature=sig,
                code=sig + " { ... }",
                comment="",
                file=file_path,
                class_name=class_name or "",
                start_line=line_num
            ))
        
        return blocks, class_info
    
    def _save_index(self, index_path: str) -> None:
        """保存索引和元数据（包含类信息）"""
        Path(index_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 保存FAISS索引
        faiss.write_index(self.index, index_path)
        
        # 保存元数据
        metadata = {
            "blocks": [
                {
                    "type": b.type,
                    "signature": b.signature,
                    "comment": b.comment,
                    "file": b.file,
                    "class_name": b.class_name,
                    "start_line": b.start_line,
                    "code_preview": b.code[:500]  # 只保存前500字符
                }
                for b in self.blocks
            ],
            "class_info": {k: asdict(v) for k, v in self.class_info.items()}
        }
        meta_path = index_path.replace('.index', '_metadata.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False)
    
    def _load_index(self, index_path: str) -> None:
        """加载索引和元数据（包含类信息）"""
        print(f"[→] 加载索引: {index_path}")
        
        # 加载FAISS索引
        self.index = faiss.read_index(index_path)
        
        # 加载元数据
        meta_path = index_path.replace('.index', '_metadata.json')
        with open(meta_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        self.blocks = [
            CodeBlock(
                type=b.get("type", "method"),
                signature=b["signature"],
                code=b.get("code_preview", ""),
                comment=b["comment"],
                file=b["file"],
                class_name=b.get("class_name", ""),
                start_line=b["start_line"]
            )
            for b in metadata["blocks"]
        ]
        
        # 加载类信息
        if "class_info" in metadata:
            for class_name, info_dict in metadata["class_info"].items():
                self.class_info[class_name] = ClassInfo(**info_dict)
        
        print(f"[√] 索引加载完成，共 {len(self.blocks)} 个代码块，{len(self.class_info)} 个类定义")
    
    # ============ 在线：检索 ============
    
    def search(self, query_method: str, top_k: int = 5) -> List[Dict]:
        """
        检索与查询方法相关的代码上下文
        
        Args:
            query_method: 要生成测试的目标方法代码
            top_k: 返回最相关的k个结果
            
        Returns:
            相关代码上下文列表
        """
        if self.index is None:
            raise ValueError("索引未加载，请先调用 build_index() 或提供 index_path")
        
        print(f"[→] 检索相关上下文...")
        
        # 生成查询向量
        try:
            query_embedding_vec = embed_single(query_method)
            query_embedding = np.array([query_embedding_vec]).astype('float32')
        except Exception as e:
            raise ValueError(f"查询嵌入生成失败: {e}")
        
        # FAISS搜索
        distances, indices = self.index.search(query_embedding, top_k)
        
        # 构建结果
        results = []
        for distance, idx in zip(distances[0], indices[0]):
            if 0 <= idx < len(self.blocks):
                block = self.blocks[idx]
                results.append({
                    "type": block.type,
                    "signature": block.signature,
                    "code": block.code,
                    "comment": block.comment,
                    "file": block.file,
                    "class_name": block.class_name,
                    "line": block.start_line,
                    "distance": float(distance)
                })
        
        print(f"[√] 找到 {len(results)} 个相关代码块")
        return results
    
    def get_context_for_prompt(self, query_method: str, target_class: str = "", top_k: int = 5) -> str:
        """
        获取格式化的上下文文本，用于Prompt构建（增强版）
        
        包含：
        1. 目标类的结构信息（字段、常量、导入、公共方法签名）
        2. 同包下的相关枚举类（如JsonToken）
        3. 语义相似的相关代码块
        
        Args:
            query_method: 目标方法代码
            target_class: 目标类名（用于获取类结构）
            top_k: 检索数量
            
        Returns:
            格式化的上下文字符串
        """
        context_parts = []
        
        # 1. 添加目标类的结构信息
        if target_class and target_class in self.class_info:
            class_info = self.class_info[target_class]
            context_parts.append("## 目标类结构信息\n")
            context_parts.append(f"类名: {class_info.name}")
            context_parts.append(f"包名: {class_info.package}")
            
            # 导入语句
            if class_info.imports:
                context_parts.append("\n### 导入语句")
                for imp in class_info.imports[:20]:
                    context_parts.append(f"import {imp};")
            
            # 公共方法签名（帮助LLM了解可用的API）
            public_methods = [m for m in class_info.methods if 'public ' in m['signature'] and 'static' not in m['signature']]
            if public_methods:
                context_parts.append("\n### 类公共方法（可用于测试）")
                for method in public_methods[:20]:
                    sig = method['signature']
                    # 清理签名格式
                    sig = sig.replace('public ', '').strip()
                    if '{' in sig:
                        sig = sig[:sig.index('{')].strip()
                    # 添加空格分隔返回类型和方法名
                    sig = sig.replace('boolean', 'boolean ').replace('void ', 'void ').replace('int ', 'int ')
                    sig = ' '.join(sig.split())  # 规范化空格
                    context_parts.append(f"- {sig}")
            
            # 构造函数（简化显示）
            if class_info.constructors:
                context_parts.append("\n### 构造函数")
                for ctor in class_info.constructors[:3]:
                    sig = ctor['signature']
                    if len(sig) > 100:
                        sig = sig[:100] + "..."
                    context_parts.append(f"{sig};")
            
            # 同包下的相关类（可能是枚举、接口等）
            if class_info.package:
                related_classes = []
                for class_name, info in self.class_info.items():
                    if (info.package == class_info.package and 
                        class_name != class_info.name and
                        len(info.methods) <= 10):  # 辅助类通常方法较少
                        related_classes.append(info)
                
                if related_classes:
                    context_parts.append("\n### 相关类型定义")
                    for rel_info in related_classes[:5]:
                        context_parts.append(f"- {rel_info.package}.{rel_info.name}")
            
            context_parts.append("")
        
        # 2. 检索语义相似的相关代码块
        results = self.search(query_method, top_k)
        
        if results:
            context_parts.append("## 相关代码上下文\n")
            for i, r in enumerate(results, 1):
                context_parts.append(f"### 相关代码 {i} [{r['type']}]")
                context_parts.append(f"文件: {r['file']}:{r['line']}")
                context_parts.append(f"签名: {r['signature']}")
                if r['comment']:
                    context_parts.append(f"注释: {r['comment'][:200]}")
                context_parts.append(f"```java\n{r['code'][:800]}\n```\n")
        
        return "\n".join(context_parts) if context_parts else ""


# ============ Agentic RAG ============

class AgenticRAG:
    """Agentic RAG系统 - LLM Agent驱动的智能检索"""
    
    def __init__(self, index_path: str):
        """
        初始化Agentic RAG
        
        Args:
            index_path: RAG索引路径
        """
        self.rag = CodeRAG(index_path)
        self.index = self.rag.index
        self.blocks = self.rag.blocks
        self.class_info = self.rag.class_info
    
    async def analyze_dependencies(
        self,
        method_code: str,
        target_class: str
    ) -> Dict[str, List[str]]:
        """
        让LLM Agent分析目标方法，识别生成测试时需要的依赖
        
        Args:
            method_code: 目标方法代码
            target_class: 目标类名
            
        Returns:
            {
                "needed_methods": ["peek", "doPeek", ...],
                "needed_fields": ["peeked", ...],
                "needed_types": ["JsonToken", ...],
                "reasoning": "需要这些信息是因为..."
            }
        """
        agent_prompt = f"""你是一个代码分析专家。分析以下Java方法，识别生成单元测试时需要了解的依赖信息。

目标类: {target_class}

目标方法代码:
```java
{method_code}
```

请识别：
1. 这个方法内部调用了哪些其他方法（在同一个类中）？
2. 这个方法使用了哪些字段/常量？
3. 这个方法依赖哪些外部类型（枚举、异常类、接口等）？

以JSON格式返回：
{{
    "needed_methods": ["方法名1", "方法名2", ...],
    "needed_fields": ["字段名1", "字段名2", ...],
    "needed_types": ["类型名1", "类型名2", ...],
    "reasoning": "简要说明为什么需要这些依赖（2-3句话）"
}}

注意：
- 只列出真正需要的依赖
- 不要包含Java标准库的类型（String, List, IOException等）
- 方法名不需要包含this.或super.前缀
- 专注于理解方法行为所需的上下文
"""

        from llm import chat
        try:
            response = await chat(prompt=agent_prompt, temperature=0.3)
            
            import json
            import re
            # 提取JSON部分
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return json.loads(response)
        except Exception as e:
            print(f"[警告] Agent依赖分析失败: {e}")
            print(f"[原始响应] {response[:200]}...")
            return {
                "needed_methods": [],
                "needed_fields": [],
                "needed_types": [],
                "reasoning": "Agent分析失败"
            }
    
    async def retrieve_by_agent(
        self,
        method_code: str,
        target_class: str,
        top_k: int = 5
    ) -> str:
        """
        Agentic检索流程：
        1. Agent分析依赖
        2. 精确检索依赖项
        3. 整合上下文
        
        Args:
            method_code: 目标方法代码
            target_class: 目标类名
            top_k: 语义相似代码补充数量
            
        Returns:
            格式化的上下文字符串
        """
        print("[Agentic RAG] 启动Agent驱动检索")
        
        # Step 1: Agent分析依赖
        print("[→] Step 1: Agent分析方法依赖...")
        dependencies = await self.analyze_dependencies(method_code, target_class)
        
        print(f"[√] Agent识别到的依赖:")
        print(f"  - 需要的方法: {len(dependencies['needed_methods'])}个 {dependencies['needed_methods'][:5]}")
        print(f"  - 需要的字段: {len(dependencies['needed_fields'])}个 {dependencies['needed_fields'][:5]}")
        print(f"  - 需要的类型: {len(dependencies['needed_types'])}个 {dependencies['needed_types'][:5]}")
        print(f"  - 推理: {dependencies['reasoning']}")
        
        # Step 2: 精确检索
        print("[→] Step 2: 精确检索依赖项...")
        context_parts = []
        
        # 2.1 检索目标类的结构
        if target_class in self.class_info:
            class_info = self.class_info[target_class]
            context_parts.append("## 目标类结构\n")
            context_parts.append(f"类名: {class_info.name}\n包名: {class_info.package}\n")
            
            # 导入语句（帮助LLM理解依赖）
            if class_info.imports:
                context_parts.append("\n### 导入语句\n")
                for imp in class_info.imports[:15]:
                    context_parts.append(f"import {imp};")
            
            # 字段（Agent识别的需要字段）
            if dependencies['needed_fields'] and class_info.fields:
                context_parts.append("\n### 字段\n")
                for field in class_info.fields:
                    field_name = field.get('name', '')
                    if any(f in field_name for f in dependencies['needed_fields']):
                        sig = field['signature']
                        if len(sig) > 150:
                            sig = sig[:150] + "..."
                        context_parts.append(f"- {sig}")
            
            # 常量（用于测试断言）
            if class_info.constants:
                context_parts.append("\n### 常量\n")
                for const in class_info.constants[:15]:
                    context_parts.append(f"- {const['signature']}")
            
            # 构造函数（用于测试初始化）
            if class_info.constructors:
                context_parts.append("\n### 构造函数\n")
                for ctor in class_info.constructors[:2]:
                    sig = ctor['signature']
                    if len(sig) > 120:
                        sig = sig[:120] + "..."
                    context_parts.append(f"- {sig}")
            
            # 同包相关类型（枚举、接口等）
            if class_info.package:
                related_classes = []
                for class_name, info in self.class_info.items():
                    if (info.package == class_info.package and
                        class_name != class_info.name and
                        len(info.methods) <= 10):
                        related_classes.append(info)
                
                if related_classes:
                    context_parts.append("\n### 相关类型定义\n")
                    for rel_info in related_classes[:5]:
                        context_parts.append(f"- {rel_info.package}.{rel_info.name}")
        
        # 2.2 检索Agent识别的方法实现
        if dependencies['needed_methods']:
            context_parts.append("\n## 依赖的方法实现\n")
            found_methods = []
            
            for method_name in dependencies['needed_methods'][:8]:
                # 在目标类中搜索
                if target_class in self.class_info:
                    class_methods = self.class_info[target_class].methods
                    for method in class_methods:
                        if method_name in method['signature']:
                            context_parts.append(f"\n### {method['signature']}\n")
                            code_preview = method.get('code', '')
                            if code_preview:
                                context_parts.append(f"```java\n{code_preview[:600]}\n```\n")
                            else:
                                context_parts.append("（无代码）\n")
                            found_methods.append(method_name)
                            break
            
            print(f"[√] 找到 {len(found_methods)}/{len(dependencies['needed_methods'])} 个方法实现")
        
        # 2.3 检索Agent识别的类型
        if dependencies['needed_types']:
            context_parts.append("\n## 依赖的类型定义\n")
            found_types = []
            
            for type_name in dependencies['needed_types']:
                for cls_name, cls_info in self.class_info.items():
                    if cls_name == type_name or cls_name.endswith(type_name) or type_name in cls_name:
                        context_parts.append(f"\n### {cls_info.package}.{cls_info.name}\n")
                        
                        # 枚举常量
                        if cls_info.constants:
                            context_parts.append("常量:\n")
                            for const in cls_info.constants[:15]:
                                context_parts.append(f"- {const['signature']}")
                        
                        # 简要说明
                        if cls_info.constants:
                            context_parts.append(f"\n（枚举，包含{len(cls_info.constants)}个常量）")
                        elif len(cls_info.methods) <= 5:
                            context_parts.append(f"\n（接口/抽象类，包含{len(cls_info.methods)}个方法）")
                        
                        found_types.append(type_name)
                        break
            
            print(f"[√] 找到 {len(found_types)}/{len(dependencies['needed_types'])} 个类型定义")
        
        # 2.4 语义相似代码补充（可选，用于参考）
        if top_k > 0:
            context_parts.append("\n## 语义相似的代码（参考）\n")
            semantic_results = self.rag.search(method_code, top_k=min(top_k, 3))
            
            for i, r in enumerate(semantic_results[:2], 1):
                # 避免重复已检索的内容
                signature = r['signature']
                if not any(dep in signature for dep in dependencies['needed_methods']):
                    context_parts.append(f"\n### 参考代码 {i}: {signature}\n")
                    context_parts.append(f"```java\n{r['code'][:400]}\n```\n")
        
        print("[√] Agentic检索完成\n")
        return "\n".join(context_parts)


# ============ 便捷函数 ============

def build_code_index(project_dir: str, index_path: str) -> None:
    """构建代码索引的便捷函数"""
    rag = CodeRAG()
    rag.build_index(project_dir, index_path)


def retrieve_context(query_method: str, index_path: str, target_class: str = "", top_k: int = 5) -> str:
    """检索上下文的便捷函数（传统RAG）"""
    rag = CodeRAG(index_path)
    return rag.get_context_for_prompt(query_method, target_class, top_k)


async def retrieve_context_agentic(query_method: str, index_path: str, target_class: str = "", top_k: int = 5) -> str:
    """检索上下文的便捷函数（Agentic RAG）"""
    agentic_rag = AgenticRAG(index_path)
    return await agentic_rag.retrieve_by_agent(query_method, target_class, top_k)


if __name__ == "__main__":
    # 测试示例
    import tempfile
    
    # 创建测试项目
    test_dir = tempfile.mkdtemp()
    test_file = os.path.join(test_dir, "Test.java")
    with open(test_file, 'w') as f:
        f.write("""
public class Test {
    public int add(int a, int b) {
        return a + b;
    }
    
    public int subtract(int a, int b) {
        return a - b;
    }
}
""")
    
    # 测试索引构建
    index_path = "/tmp/test_rag.index"
    rag = CodeRAG()
    rag.build_index(test_dir, index_path)
    
    # 测试检索
    query = "public int multiply(int a, int b) { return a * b; }"
    context = rag.get_context_for_prompt(query, top_k=2)
    print("\n检索结果:")
    print(context)
