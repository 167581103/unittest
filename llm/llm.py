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
from litellm import acompletion, embedding

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
    params = {
        "model": CONFIG["embedding_model"],
        "input": texts,
        "api_key": CONFIG["embedding_api_key"],
    }
    if CONFIG["embedding_base_url"]:
        params["api_base"] = CONFIG["embedding_base_url"]

    for i in range(retries):
        try:
            resp = embedding(**params)
            return [d["embedding"] for d in resp["data"]]
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
    matches = re.findall(r"```(?:java)?\n(.*?)```", text, re.DOTALL)
    return "\n\n".join(matches) if matches else text


async def generate_test(
    class_name: str,
    method_signature: str,
    method_code: str,
    output_path: str,
    context: str = "",
) -> Dict:
    """生成单元测试"""
    system = PROMPTS["test_system"].format(class_name=class_name, context=context or "无上下文")
    prompt = PROMPTS["test_user"].format(
        class_name=class_name,
        method_signature=method_signature,
        method_code=method_code,
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
