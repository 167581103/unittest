#!/usr/bin/env python3
"""
简单测试GLM-4.6模型是否正常工作
"""

import os
import asyncio
from llm.llm import chat

async def test_model():
    print("=" * 50)
    print("测试 GLM-4.6 模型响应")
    print("=" * 50)
    
    # 测试简单的对话
    response = await chat("你好，请简单介绍一下你自己。")
    
    print("模型响应:")
    print(response)
    print("\n测试完成！")

if __name__ == "__main__":
    asyncio.run(test_model())
