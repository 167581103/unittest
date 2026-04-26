#!/usr/bin/env python3
"""
测试GLM-4.6模型生成Java测试代码的能力
"""

import os
import asyncio
from llm.llm import generate_test

async def test_code_generation():
    print("=" * 50)
    print("测试 GLM-4.6 模型生成测试代码")
    print("=" * 50)
    
    # 测试生成简单的测试代码
    result = await generate_test(
        class_name="Calculator",
        method_signature="public int add(int a, int b)",
        method_code="return a + b;",
        output_path="/tmp/test_calculator.java",
        test_class_name="CalculatorTest",
        package_name="com.example"
    )
    
    print("生成结果:")
    print("成功:", result.get('success', False))
    
    if result.get('success'):
        output_path = result.get('output_path', '/tmp/test_calculator.java')
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                code = f.read()
            print("代码长度:", len(code))
            print("\n生成的代码:")
            print(code)
        else:
            print("文件未找到:", output_path)
    else:
        print("错误信息:", result.get('error', 'Unknown error'))
    
    print("\n测试完成！")

if __name__ == "__main__":
    asyncio.run(test_code_generation())
