#!/usr/bin/env python3
"""
简单的LLM接口测试应用
演示如何使用llm.py模块中的功能
"""

import asyncio
import sys
import os

# 添加当前目录到Python路径，以便导入llm模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from llm.llm import chat, get_embedding, client


async def test_chat_completion():
    """测试聊天补全功能"""
    print("\n=== 测试聊天补全功能 ===")
    
    try:
        # 简单对话，添加超时
        response = await asyncio.wait_for(
            chat("请用一句话介绍人工智能", 
                 system_prompt="你是一个专业的AI助手，回答简洁明了"),
            timeout=30  # 30秒超时
        )
        print(f"简单对话: {response}")
    except asyncio.TimeoutError:
        print("简单对话: 请求超时，可能是连接问题")
    except Exception as e:
        print(f"简单对话出错: {str(e)}")
    
    try:
        # 带历史记录的对话，添加超时
        history = [
            {"role": "user", "content": "什么是机器学习？"},
            {"role": "assistant", "content": "机器学习是人工智能的一个分支，它让计算机能够从数据中学习。"}
        ]
        response = await asyncio.wait_for(
            client.chat(
                "机器学习有哪些主要类型？",
                system="你是一个专业的AI助手",
                history=history
            ),
            timeout=30
        )
        print(f"带历史记录的对话: {response}")
    except asyncio.TimeoutError:
        print("带历史记录的对话: 请求超时")
    except Exception as e:
        print(f"带历史记录的对话出错: {str(e)}")


async def test_embedding():
    """测试文本嵌入功能"""
    print("\n=== 测试文本嵌入功能 ===")
    
    texts = [
        "人工智能是计算机科学的一个分支",
        "机器学习是人工智能的重要组成部分",
        "深度学习是机器学习的一种方法"
    ]
    
    try:
        # 获取单个文本的嵌入，添加超时
        single_embedding = await asyncio.wait_for(
            get_embedding("测试文本嵌入功能"),
            timeout=30
        )
        print(f"单个文本嵌入向量维度: {len(single_embedding)}")
        
        # 获取多个文本的嵌入，添加超时
        for text in texts:
            try:
                response = await asyncio.wait_for(
                    client.embed([text]),
                    timeout=30
                )
                # 从响应中提取嵌入向量
                try:
                    embedding = response["data"][0]["embedding"]
                    print(f"文本: {text[:20]}... 嵌入向量维度: {len(embedding)}")
                except (KeyError, IndexError, TypeError):
                    print(f"文本: {text[:20]}... 嵌入向量解析失败")
            except Exception as e:
                print(f"获取嵌入出错: {str(e)}")
    except asyncio.TimeoutError:
        print("嵌入测试: 请求超时")
    except Exception as e:
        print(f"嵌入测试出错: {e}")


async def main():
    """主测试函数"""
    print("开始测试LLM接口功能...")
    print(f"当前配置: 模型={client.config['model']}, 嵌入模型={client.config['embedding_model']}")
    
    # 运行各项测试
    await test_chat_completion()
    await test_embedding()
    
    print("\n测试完成!")


if __name__ == "__main__":
    # 运行测试
    asyncio.run(main())