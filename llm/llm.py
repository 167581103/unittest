"""
LLM模块 - 聊天、嵌入、测试生成（统一使用OpenAI客户端）
"""

import os
import re
import time
from typing import List, Dict
from pathlib import Path

import yaml
from openai import OpenAI

# ============ 配置 ============b


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

# 统一的OpenAI客户端
_chat_client = None
_embed_client = None


def _get_chat_client():
    global _chat_client
    if _chat_client is None:
        _chat_client = OpenAI(
            api_key=CONFIG["api_key"],
            base_url=CONFIG["base_url"]
        )
    return _chat_client


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(
            api_key=CONFIG["embedding_api_key"],
            base_url=CONFIG["embedding_base_url"]
        )
    return _embed_client


# ============ 嵌入 ============


def embed(texts: List[str], retries: int = 3) -> List[List[float]]:
    """获取嵌入向量"""
    client = _get_embed_client()
    model = CONFIG["embedding_model"]
    
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
    """与LLM对话（异步接口，内部使用同步客户端）"""
    client = _get_chat_client()
    
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    resp = client.chat.completions.create(
        model=CONFIG["model"],
        messages=msgs,
        **kw
    )
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


# JUnit4 symbol -> import statement mapping
_JUNIT_IMPORT_MAP = {
    "assertEquals":    "import static org.junit.Assert.assertEquals;",
    "assertNotEquals": "import static org.junit.Assert.assertNotEquals;",
    "assertTrue":      "import static org.junit.Assert.assertTrue;",
    "assertFalse":     "import static org.junit.Assert.assertFalse;",
    "assertNull":      "import static org.junit.Assert.assertNull;",
    "assertNotNull":   "import static org.junit.Assert.assertNotNull;",
    "assertSame":      "import static org.junit.Assert.assertSame;",
    "assertNotSame":   "import static org.junit.Assert.assertNotSame;",
    "assertArrayEquals": "import static org.junit.Assert.assertArrayEquals;",
    "assertThrows":    "import static org.junit.Assert.assertThrows;",
    "fail":            "import static org.junit.Assert.fail;",
    "@Test":           "import org.junit.Test;",
    "@Before":         "import org.junit.Before;",
    "@After":          "import org.junit.After;",
    "@BeforeClass":    "import org.junit.BeforeClass;",
    "@AfterClass":     "import org.junit.AfterClass;",
    "@Ignore":         "import org.junit.Ignore;",
}


def _fix_imports(code: str) -> str:
    """Scan generated code and inject missing JUnit imports."""
    existing = set(re.findall(r'^import[^;]+;', code, re.MULTILINE))

    to_add = []
    for symbol, import_stmt in _JUNIT_IMPORT_MAP.items():
        if import_stmt in existing:
            continue
        # Match symbol as a standalone token (not inside a string or comment)
        pattern = r'(?<![\w.])' + re.escape(symbol) + r'(?![\w])'
        if re.search(pattern, code):
            to_add.append(import_stmt)

    if not to_add:
        return code

    # Insert after the last existing import line
    last_import = list(re.finditer(r'^import[^;]+;', code, re.MULTILINE))
    if last_import:
        insert_pos = last_import[-1].end()
        addition = "\n" + "\n".join(sorted(to_add))
        return code[:insert_pos] + addition + code[insert_pos:]

    # No imports at all: insert after package declaration
    pkg_match = re.search(r'^package[^;]+;', code, re.MULTILINE)
    if pkg_match:
        insert_pos = pkg_match.end()
        addition = "\n\n" + "\n".join(sorted(to_add))
        return code[:insert_pos] + addition + code[insert_pos:]

    # Fallback: prepend
    return "\n".join(sorted(to_add)) + "\n\n" + code


async def generate_test(
    class_name: str,
    method_signature: str,
    method_code: str,
    output_path: str,
    context: str = "",
    test_class_name: str = None,
    full_class_name: str = None,
    package_name: str = None,
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
    # Derive package from full_class_name if not provided
    if package_name is None and full_class_name and "." in full_class_name:
        package_name = ".".join(full_class_name.split(".")[:-1])
    package_name = package_name or ""
    system = PROMPTS["test_system"].format(
        class_name=class_name,
        test_class_name=test_class_name,
        full_class_name=full_class_name or class_name,
        package_name=package_name,
        context=context or "无上下文"
    )
    prompt = PROMPTS["test_user"].format(
        class_name=class_name,
        test_class_name=test_class_name,
        method_signature=method_signature,
        method_code=method_code,
        full_class_name=full_class_name or class_name,
        package_name=package_name,
    )

    try:
        resp = await chat(prompt, system, temperature=0.7, max_tokens=4000)
        code = _extract_code(resp)
        code = _fix_imports(code)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        return {"success": True, "output_path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============ 批量生成 ============


async def batch_generate(tasks: List[Dict]) -> List[Dict]:
    """批量生成测试
    
    Args:
        tasks: 任务列表，每个任务包含 generate_test 的参数
    
    Returns:
        结果列表
    """
    results = []
    for task in tasks:
        result = await generate_test(**task)
        results.append(result)
    return results
