"""
LLM模块 - 聊天、嵌入、测试生成
"""

import os
import re
import asyncio
import time
from typing import List, Dict
from pathlib import Path

import yaml
from litellm import acompletion
from openai import OpenAI

# ============ 配置 ============


def _load_config():
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


def _load_prompts():
    path = Path(__file__).parent / "prompts.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = _load_config()
PROMPTS = _load_prompts()


# ============ 嵌入 ============


def embed(texts: List[str], retries: int = 3) -> List[List[float]]:
    """获取嵌入向量"""
    client = OpenAI(
        api_key=CONFIG["embedding_api_key"],
        base_url=CONFIG["embedding_base_url"]
    )
    
    # 提取模型名称（去掉 openai/ 前缀）
    model = CONFIG["embedding_model"]
    if model.startswith("openai/"):
        model = model[7:]
    
    for i in range(retries):
        try:
            resp = client.embeddings.create(
                model=model,
                input=texts
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 ** i)
            else:
                raise e


# ============ 聊天 ============


async def chat(prompt: str, system: str = None, **kw) -> str:
    """与LLM对话"""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    params = {
        "model": CONFIG["model"],
        "messages": msgs,
        "api_key": CONFIG["api_key"],
        **kw,
    }
    if CONFIG["base_url"]:
        params["base_url"] = CONFIG["base_url"]

    resp = await acompletion(**params)
    return resp.choices[0].message.content


# ============ 测试生成 ============


def _extract_code(text: str) -> str:
    """提取代码块"""
    # 移除开头的代码块标记
    text = re.sub(r'^```(?:java)?\n?', '', text, flags=re.MULTILINE)
    # 移除结尾的代码块标记  
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    # 尝试提取代码块内容
    matches = re.findall(r"```(?:java)?\n(.*?)```", text, re.DOTALL)
    if matches:
        code = "\n\n".join(matches)
    else:
        code = text
    
    # 确保类定义闭合
    if code.count('{') > code.count('}'):
        code += '\n}'
    
    return code.strip()


async def generate_test(
    class_name: str,
    method_signature: str,
    method_code: str,
    output_path: str,
    context: str = "",
    test_class_name: str = None,
    full_class_name: str = None,
) -> Dict:
    """生成单元测试
    
    Args:
        class_name: 被测类简单名（如 JsonReader）
        method_signature: 方法签名
        method_code: 方法代码
        output_path: 输出路径
        context: RAG检索的上下文
        test_class_name: 生成的测试类名（如 JsonReader_skipValue_Test），默认为 {class_name}Test
        full_class_name: 被测类完整包名（如 com.google.gson.stream.JsonReader），用于import
    """
    test_class_name = test_class_name or f"{class_name}Test"
    system = PROMPTS["test_system"].format(
        class_name=class_name, 
        test_class_name=test_class_name,
        full_class_name=full_class_name or class_name,
        context=context or "无上下文"
    )
    prompt = PROMPTS["test_user"].format(
        class_name=class_name,
        test_class_name=test_class_name,
        method_signature=method_signature,
        method_code=method_code,
        full_class_name=full_class_name or class_name,
    )

    try:
        resp = await chat(prompt, system, temperature=0.7, max_tokens=2000)
        code = _extract_code(resp)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        return {"success": True, "output_path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}
