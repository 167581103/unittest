"""
LLM模块 - 极简代码生成

功能：
1. 构建单元测试生成提示词
2. 调用LLM生成代码并写入文件
"""

import os
import re
import asyncio
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path

# litellm用于LLM调用
try:
    from litellm import acompletion, embedding
except ImportError:
    raise ImportError("请安装litellm: pip install litellm")


# ============ 配置 ============

def _load_config():
    """加载环境变量配置"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except:
        pass
    
    return {
        "model": os.getenv("CHAT_MODEL", "gpt-3.5-turbo"),
        "api_key": os.getenv("API_KEY"),
        "base_url": os.getenv("BASE_URL"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002"),
        "embedding_api_key": os.getenv("EMBEDDING_API_KEY") or os.getenv("API_KEY"),
        "embedding_base_url": os.getenv("EMBEDDING_BASE_URL") or os.getenv("BASE_URL"),
    }


CONFIG = _load_config()


# ============ 嵌入 ============

def embed(texts: List[str]) -> List[List[float]]:
    """
    获取文本嵌入向量
    
    Args:
        texts: 文本列表
        
    Returns:
        嵌入向量列表
    """
    params = {
        "model": CONFIG["embedding_model"],
        "input": texts,
        "api_key": CONFIG["embedding_api_key"],
    }
    if CONFIG["embedding_base_url"]:
        params["api_base"] = CONFIG["embedding_base_url"]
    
    response = embedding(**params)
    return [d["embedding"] for d in response["data"]]


def embed_single(text: str) -> List[float]:
    """获取单个文本的嵌入向量"""
    return embed([text])[0]


# ============ 聊天 ============

async def chat(prompt: str, system: str = None, **kwargs) -> str:
    """
    与LLM对话
    
    Args:
        prompt: 用户提示词
        system: 系统提示词
        **kwargs: 额外参数（temperature, max_tokens等）
        
    Returns:
        LLM生成的文本
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    params = {
        "model": CONFIG["model"],
        "messages": messages,
        "api_key": CONFIG["api_key"],
        **kwargs
    }
    if CONFIG["base_url"]:
        params["base_url"] = CONFIG["base_url"]
    
    response = await acompletion(**params)
    return response.choices[0].message.content


# ============ 提示词构建 ============

UNIT_TEST_SYSTEM_PROMPT = """你是一个专业的Java单元测试生成专家。

任务：为给定的Java方法生成高质量的JUnit 4单元测试。

要求：
1. 测试覆盖正常情况、边界条件和异常情况
2. 使用JUnit 4的@Test、@Before等注解
3. 使用Assert.assertEquals、Assert.assertTrue等进行断言
4. 测试方法命名清晰，如testMethodNameScenario
5. 包含必要的导入语句
6. 只使用上下文中提供的公共API，不要访问私有成员

上下文信息：

{context}
"""

UNIT_TEST_USER_PROMPT = """请为以下Java方法生成单元测试：

## 类名
{class_name}

## 完整类路径
上下文中已提供包名，必须使用完整路径。例如如果包名是 com.google.gson.stream，则：
- 被测类import: import com.google.gson.stream.{class_name};
- 相关类型import: import com.google.gson.stream.JsonToken; (如果JsonToken在相关类型定义中)

## 方法签名
{method_signature}

## 方法代码
```java
{method_code}
```

请生成完整的、可编译的JUnit 4测试类。
"""


def build_test_prompt(
    class_name: str,
    method_signature: str,
    method_code: str,
    context: str = ""
) -> tuple[str, str]:
    """
    构建单元测试生成提示词
    
    Args:
        class_name: 类名
        method_signature: 方法签名
        method_code: 方法完整代码
        context: 检索到的相关上下文
        
    Returns:
        (system_prompt, user_prompt)
    """
    system = UNIT_TEST_SYSTEM_PROMPT.format(context=context or "无相关上下文")
    user = UNIT_TEST_USER_PROMPT.format(
        class_name=class_name,
        method_signature=method_signature,
        method_code=method_code
    )
    return system, user


# ============ 代码生成 ============

def _extract_code(text: str) -> str:
    """从LLM响应中提取代码块"""
    # 尝试提取```java ... ```格式的代码
    pattern = r"```(?:java)?\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return "\n\n".join(matches)
    return text


async def generate_test(
    class_name: str,
    method_signature: str,
    method_code: str,
    output_path: str,
    context: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000
) -> Dict[str, any]:
    """
    生成单元测试并写入文件
    
    Args:
        class_name: 类名
        method_signature: 方法签名
        method_code: 方法代码
        output_path: 输出文件路径
        context: 相关上下文
        temperature: 温度参数
        max_tokens: 最大token数
        
    Returns:
        {"success": bool, "output_path": str, "error": str}
    """
    try:
        # 1. 构建提示词
        system, prompt = build_test_prompt(class_name, method_signature, method_code, context)
        
        # 2. 调用LLM
        response = await chat(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        # 3. 提取代码
        code = _extract_code(response)
        
        # 4. 写入文件
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(code)
        
        return {"success": True, "output_path": output_path, "error": ""}
        
    except Exception as e:
        return {"success": False, "output_path": "", "error": str(e)}


async def batch_generate(
    tasks: List[Dict],
    max_concurrent: int = 3
) -> List[Dict]:
    """
    批量生成单元测试
    
    Args:
        tasks: 任务列表，每个任务是generate_test的参数字典
        max_concurrent: 最大并发数
        
    Returns:
        结果列表
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def run_task(task):
        async with semaphore:
            return await generate_test(**task)
    
    results = await asyncio.gather(*[run_task(t) for t in tasks])
    return results


# ============ 便捷函数 ============

async def generate(
    prompt: str,
    system: str = None,
    output_path: str = None,
    extract_code: bool = True
) -> str:
    """
    通用生成函数
    
    Args:
        prompt: 提示词
        system: 系统提示词
        output_path: 输出文件路径（可选）
        extract_code: 是否提取代码块
        
    Returns:
        生成的文本
    """
    response = await chat(prompt=prompt, system=system)
    
    if extract_code:
        response = _extract_code(response)
    
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(response)
    
    return response


# ============ 测试 ============

if __name__ == "__main__":
    async def demo():
        # 测试嵌入
        print("=" * 50)
        print("测试嵌入")
        print("=" * 50)
        try:
            emb = embed_single("这是一个测试")
            print(f"✓ 嵌入维度: {len(emb)}")
        except Exception as e:
            print(f"✗ 嵌入失败: {e}")
        
        # 测试聊天
        print("\n" + "=" * 50)
        print("测试聊天")
        print("=" * 50)
        try:
            response = await chat("你好", "你是一个助手")
            print(f"✓ 响应: {response[:100]}...")
        except Exception as e:
            print(f"✗ 聊天失败: {e}")
        
        # 测试代码生成
        print("\n" + "=" * 50)
        print("测试代码生成")
        print("=" * 50)
        try:
            result = await generate_test(
                class_name="Calculator",
                method_signature="public int add(int a, int b)",
                method_code="public int add(int a, int b) { return a + b; }",
                output_path="/tmp/Test.java",
                context=""
            )
            if result["success"]:
                print(f"✓ 代码生成成功: {result['output_path']}")
            else:
                print(f"✗ 代码生成失败: {result['error']}")
        except Exception as e:
            print(f"✗ 代码生成失败: {e}")
    
    asyncio.run(demo())
