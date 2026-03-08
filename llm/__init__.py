"""
LLM模块 - 极简代码生成

功能：
1. 嵌入：获取文本嵌入向量
2. 聊天：与LLM对话
3. 代码生成：生成单元测试并写入文件

使用示例：
    import asyncio
    from llm import generate_test, embed_single
    
    # 生成测试
    async def main():
        result = await generate_test(
            class_name="Calculator",
            method_signature="public int add(int a, int b)",
            method_code="public int add(int a, int b) { return a + b; }",
            output_path="./CalculatorTest.java",
            context=""  # 可选：检索到的上下文
        )
        print(result)
    
    asyncio.run(main())
"""

from llm.llm import (
    # 嵌入
    embed,
    embed_single,
    
    # 聊天
    chat,
    
    # 提示词构建
    build_test_prompt,
    
    # 代码生成
    generate_test,
    batch_generate,
    generate,
)

__all__ = [
    "embed", "embed_single",
    "chat",
    "build_test_prompt",
    "generate_test", "batch_generate", "generate",
]
