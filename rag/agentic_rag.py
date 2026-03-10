"""
Agentic RAG - LLM Agent驱动的智能代码检索

核心思想：
1. LLM Agent阅读目标方法代码
2. Agent智能识别需要的上下文（调用的方法、依赖的类型等）
3. Agent根据需求自主检索相关代码块
4. Agent整合所有上下文，生成最终的Prompt

优势：
- 智能分析：LLM理解代码语义，比规则更准确
- 精确检索：只返回真正需要的依赖
- 可解释性：Agent输出推理过程
"""

import os
import json
import re
from typing import Dict, List

from .code_rag import CodeRAG


class AgenticRAG:
    """Agentic RAG系统 - LLM Agent驱动的智能检索"""

    def __init__(self, index_path: str, test_dir: str = None):
        """
        初始化Agentic RAG

        Args:
            index_path: RAG索引路径
            test_dir: 测试代码目录（可选，用于检索API使用示例）
        """
        self.rag = CodeRAG(index_path)
        self.index = self.rag.index
        self.blocks = self.rag.blocks
        self.class_info = self.rag.class_info
        self.test_dir = test_dir

    async def analyze_dependencies(
        self, method_code: str, target_class: str
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

            # 提取JSON部分
            json_match = re.search(
                r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response, re.DOTALL
            )
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
                "reasoning": "Agent分析失败",
            }

    def _retrieve_test_examples(self, target_class: str, methods: List[str]) -> str:
        """
        检索现有测试中的API使用示例

        Args:
            target_class: 目标类名（如JsonReader）
            methods: 相关方法列表

        Returns:
            API使用示例文本
        """
        if not self.test_dir:
            return ""

        print(f"[→] Step 2.5: 检索现有测试中的API使用示例...")

        try:
            # 查找目标类的测试文件（排除Generated测试）
            test_files = []
            for root, dirs, files in os.walk(self.test_dir):
                for file in files:
                    # 查找包含目标类名的测试文件
                    class_simple_name = target_class.split('.')[-1]
                    if class_simple_name in file and "Test.java" in file and "Generated" not in file:
                        test_files.append(os.path.join(root, file))

            api_examples = []
            class_simple_name = target_class.split('.')[-1]
            class_var_pattern = class_simple_name[0].lower() + class_simple_name[1:]  # JsonReader -> jsonReader

            for test_file in test_files:
                try:
                    with open(test_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # 确保文件包含目标类
                    if class_simple_name not in content:
                        continue

                    # 查找定义了目标类变量的测试
                    # 匹配：JsonReader jsonReader = ... 或 JsonReader reader = ...
                    var_pattern = rf'{class_simple_name}\s+(\w+)\s*='
                    var_matches = re.findall(var_pattern, content)
                    if not var_matches:
                        continue

                    # 使用找到的变量名
                    var_names = var_matches[:3]  # 取前3个
                    print(f"  -> 在文件中找到变量: {var_names}")

                    # 提取测试方法
                    test_pattern = r'@\s*Test\s+(?:public\s+)?void\s+(\w+)\([^)]*\)\s*(?:throws\s+\w+\s*)?\{([^}]+(?:\{[^}]*\}[^}]*)*)\}'

                    for match in re.finditer(test_pattern, content, re.DOTALL):
                        test_name = match.group(1)
                        test_body = match.group(2)

                        # 检查是否使用了目标类的变量
                        uses_target_class = any(var_name in test_body for var_name in var_names)
                        if not uses_target_class:
                            continue

                        # 检查是否使用了相关API
                        relevant_apis = []
                        for method in methods + ['beginObject', 'beginArray', 'nextName', 'nextString', 'skipValue', 'endObject', 'endArray', 'peek']:
                            for var_name in var_names:
                                if f'{var_name}.{method}(' in test_body or f'{var_name}.{method}()' in test_body:
                                    relevant_apis.append(method)
                                    break

                        if not relevant_apis:
                            continue

                        # 提取关键的API调用（去除注释和空行）
                        lines = []
                        for line in test_body.split('\n'):
                            line_stripped = line.strip()
                            # 过滤注释和空行
                            if (line_stripped and
                                not line_stripped.startswith('//') and
                                not line_stripped.startswith('*') and
                                not line_stripped.startswith('@') and
                                not line_stripped.startswith('import') and
                                not line_stripped.startswith('/*')):
                                lines.append(line_stripped)

                        if len(lines) >= 3:  # 至少3行代码
                            # 限制长度，保留最重要的API调用
                            example_body = '\n'.join(lines[:10])
                            api_examples.append(f"### 示例: {test_name}\n```java\n{example_body}\n```\n")
                            if len(api_examples) >= 5:
                                break

                    if len(api_examples) >= 5:
                        break

                except Exception as e:
                    print(f"  -> 处理文件失败: {e}")
                    continue

            if api_examples:
                print(f"[√] 找到 {len(api_examples)} 个API使用示例")
                return "\n## API使用示例（来自现有测试）\n\n" + "\n".join(api_examples[:5])
            else:
                print(f"[!] 未找到API使用示例")
                return ""

        except Exception as e:
            print(f"[!] 检索API示例失败: {e}")
            return ""

    async def retrieve_by_agent(
        self, method_code: str, target_class: str, top_k: int = 5
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
        print(
            f"  - 需要的方法: {len(dependencies['needed_methods'])}个 {dependencies['needed_methods'][:5]}"
        )
        print(
            f"  - 需要的字段: {len(dependencies['needed_fields'])}个 {dependencies['needed_fields'][:5]}"
        )
        print(
            f"  - 需要的类型: {len(dependencies['needed_types'])}个 {dependencies['needed_types'][:5]}"
        )
        print(f"  - 推理: {dependencies['reasoning']}")

        # Step 2: 精确检索
        print("[→] Step 2: 精确检索依赖项...")
        context_parts = []

        # 2.1 检索目标类的结构
        if target_class in self.class_info:
            class_info = self.class_info[target_class]
            context_parts.append("## 目标类结构\n")
            context_parts.append(
                f"类名: {class_info.name}\n包名: {class_info.package}\n"
            )

            # 导入语句（帮助LLM理解依赖）
            if class_info.imports:
                context_parts.append("\n### 导入语句\n")
                for imp in class_info.imports[:15]:
                    context_parts.append(f"import {imp};")

            # 字段（Agent识别的需要字段）
            if dependencies["needed_fields"] and class_info.fields:
                context_parts.append("\n### 字段\n")
                for field in class_info.fields:
                    field_name = field.get("name", "")
                    if any(f in field_name for f in dependencies["needed_fields"]):
                        sig = field["signature"]
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
                    sig = ctor["signature"]
                    if len(sig) > 120:
                        sig = sig[:120] + "..."
                    context_parts.append(f"- {sig}")

            # 同包相关类型（枚举、接口等）
            if class_info.package:
                related_classes = []
                for class_name, info in self.class_info.items():
                    if (
                        info.package == class_info.package
                        and class_name != class_info.name
                        and len(info.methods) <= 10
                    ):
                        related_classes.append(info)

                if related_classes:
                    context_parts.append("\n### 相关类型定义\n")
                    for rel_info in related_classes[:5]:
                        context_parts.append(f"- {rel_info.package}.{rel_info.name}")

        # 2.2 检索Agent识别的方法实现（全局搜索）
        if dependencies["needed_methods"]:
            context_parts.append("\n## 依赖的方法实现\n")
            found_methods = []

            for method_name in dependencies["needed_methods"][:8]:
                # 全局搜索：先搜目标类，再搜其他类
                search_order = [target_class] if target_class in self.class_info else []
                search_order.extend([cls_name for cls_name in self.class_info if cls_name != target_class])

                found = False
                for cls_name in search_order:
                    class_methods = self.class_info[cls_name].methods
                    for method in class_methods:
                        if method_name in method["signature"]:
                            # 标注是否是目标类的方法
                            class_marker = " [目标类]" if cls_name == target_class else f" [{self.class_info[cls_name].package}]"
                            context_parts.append(f"\n### {method['signature']}{class_marker}\n")

                            code_preview = method.get("code", "")
                            if code_preview:
                                # 目标类显示代码，其他类只显示签名（避免过多上下文）
                                if cls_name == target_class:
                                    context_parts.append(
                                        f"```java\n{code_preview[:600]}\n```\n"
                                    )
                                else:
                                    comment = method.get("comment", "")
                                    if comment:
                                        context_parts.append(f"说明: {comment[:200]}\n")
                                    context_parts.append("（其他类方法，仅提供签名）\n")
                            else:
                                context_parts.append("（无代码）\n")

                            found_methods.append(method_name)
                            found = True
                            break
                    if found:
                        break

                if not found:
                    # 方法未找到，记录警告
                    context_parts.append(f"\n### {method_name}\n")
                    context_parts.append("（警告: 未找到该方法定义）\n")

            print(
                f"[√] 找到 {len(found_methods)}/{len(dependencies['needed_methods'])} 个方法实现"
            )

        # 2.3 检索Agent识别的类型
        if dependencies["needed_types"]:
            context_parts.append("\n## 依赖的类型定义\n")
            found_types = []

            for type_name in dependencies["needed_types"]:
                for cls_name, cls_info in self.class_info.items():
                    if (
                        cls_name == type_name
                        or cls_name.endswith(type_name)
                        or type_name in cls_name
                    ):
                        context_parts.append(
                            f"\n### {cls_info.package}.{cls_info.name}\n"
                        )

                        # 枚举常量
                        if cls_info.constants:
                            context_parts.append("常量:\n")
                            for const in cls_info.constants[:15]:
                                context_parts.append(f"- {const['signature']}")

                        # 简要说明
                        if cls_info.constants:
                            context_parts.append(
                                f"\n（枚举，包含{len(cls_info.constants)}个常量）"
                            )
                        elif len(cls_info.methods) <= 5:
                            context_parts.append(
                                f"\n（接口/抽象类，包含{len(cls_info.methods)}个方法）"
                            )

                        found_types.append(type_name)
                        break

            print(
                f"[√] 找到 {len(found_types)}/{len(dependencies['needed_types'])} 个类型定义"
            )

        # 2.5 检索API使用示例（从现有测试中）
        api_examples = self._retrieve_test_examples(
            target_class,
            dependencies["needed_methods"]
        )
        if api_examples:
            context_parts.append(api_examples)

        # 2.6 语义相似代码补充（可选，用于参考）
        if top_k > 0:
            context_parts.append("\n## 语义相似的代码（参考）\n")
            semantic_results = self.rag.search(method_code, top_k=min(top_k, 3))

            for i, (block, distance) in enumerate(semantic_results[:2], 1):
                # 避免重复已检索的内容
                signature = block.signature
                if not any(dep in signature for dep in dependencies["needed_methods"]):
                    context_parts.append(f"\n### 参考代码 {i}: {signature}\n")
                    context_parts.append(f"```java\n{block.code[:400]}\n```\n")

        print("[√] Agentic检索完成\n")
        return "\n".join(context_parts)


async def retrieve_context_agentic(
    query_method: str, index_path: str, target_class: str = "", top_k: int = 5
) -> str:
    """
    检索上下文的便捷函数（Agentic RAG）

    Args:
        query_method: 目标方法代码
        index_path: RAG索引路径
        target_class: 目标类名
        top_k: 语义相似代码补充数量

    Returns:
        格式化的上下文字符串
    """
    agentic_rag = AgenticRAG(index_path)
    return await agentic_rag.retrieve_by_agent(query_method, target_class, top_k)
