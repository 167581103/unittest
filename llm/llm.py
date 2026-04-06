"""
LLM模块 - 聊天、嵌入、测试生成（统一使用OpenAI客户端）
"""

import os
import re
import json
import time
from typing import List, Dict
from pathlib import Path

import yaml
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


# ============ 方法解读与测试用例设计 ============


async def analyze_method(
    class_name: str,
    method_signature: str,
    method_code: str,
    context: str = "",
    full_class_name: str = None,
) -> Dict:
    """Three-phase method analysis: understanding -> coverage -> test case design.

    Returns:
        {
            "method_understanding": str,   # Phase 1: what the method does
            "coverage_analysis": str,       # Phase 2: coverage points/surfaces
            "test_cases": list[dict],       # Phase 3: structured test case designs
            "test_cases_raw": str,          # Phase 3: raw LLM response
        }
    """
    full_class_name = full_class_name or class_name

    # ── Phase 1: Method Understanding ──────────────────────────────────
    print("  [LLM] Phase 1: Understanding method functionality...")
    understanding_prompt = PROMPTS["method_understanding"].format(
        full_class_name=full_class_name,
        method_signature=method_signature,
        method_code=method_code,
        context=context or "No context",
    )
    method_understanding = await chat(understanding_prompt, temperature=0.3, max_tokens=2000)
    print(f"  [LLM] Phase 1 done ({len(method_understanding)} chars)")

    # ── Phase 2: Coverage Analysis ─────────────────────────────────────
    print("  [LLM] Phase 2: Analyzing coverage points...")
    coverage_prompt = PROMPTS["coverage_analysis"].format(
        full_class_name=full_class_name,
        method_signature=method_signature,
        method_code=method_code,
        method_understanding=method_understanding,
        context=context or "No context",
    )
    coverage_analysis = await chat(coverage_prompt, temperature=0.3, max_tokens=2000)
    print(f"  [LLM] Phase 2 done ({len(coverage_analysis)} chars)")

    # ── Phase 3: Test Case Design ──────────────────────────────────────
    print("  [LLM] Phase 3: Designing test cases...")
    design_prompt = PROMPTS["test_case_design"].format(
        full_class_name=full_class_name,
        method_signature=method_signature,
        method_code=method_code,
        method_understanding=method_understanding,
        coverage_analysis=coverage_analysis,
        context=context or "No context",
    )
    test_cases_raw = await chat(design_prompt, temperature=0.4, max_tokens=4000)
    test_cases = _parse_test_cases(test_cases_raw)
    print(f"  [LLM] Phase 3 done ({len(test_cases)} test cases designed)")

    return {
        "method_understanding": method_understanding,
        "coverage_analysis": coverage_analysis,
        "test_cases": test_cases,
        "test_cases_raw": test_cases_raw,
    }


def _parse_test_cases(raw: str) -> List[Dict]:
    """Parse the LLM response to extract the JSON test case array."""
    # Strategy 1: extract JSON code block
    block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
    if block_match:
        try:
            data = json.loads(block_match.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 2: find outermost JSON array
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    data = json.loads(raw[start:i + 1])
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass
                break

    print("  [LLM] Warning: failed to parse test cases JSON, returning empty list")
    return []


def format_test_cases_for_prompt(test_cases: List[Dict]) -> str:
    """Format structured test cases into a readable string for the generation prompt."""
    if not test_cases:
        return "No test cases designed."
    return json.dumps(test_cases, indent=2, ensure_ascii=False)


# ============ 测试生成 ============


def _extract_code(text: str) -> str:
    """Extract Java code from LLM response (may be wrapped in markdown fences)."""
    matches = re.findall(r"```(?:java)?\n(.*?)```", text, re.DOTALL)
    code = "\n\n".join(matches) if matches else text
    # Remove any remaining markdown fence lines
    code = re.sub(r'^```(?:java)?\s*$', '', code, flags=re.MULTILINE)
    code = re.sub(r'^```\s*$', '', code, flags=re.MULTILINE)
    # Fix brace balance
    if code.count('{') > code.count('}'):
        code += '\n}'
    elif code.count('}') > code.count('{'):
        # Remove trailing extra closing braces
        lines = code.rstrip().split('\n')
        while lines and lines[-1].strip() == '}' and code.count('}') > code.count('{'):
            lines.pop()
            code = '\n'.join(lines)
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
    """Scan generated code and fix common import issues.

    1. Replace AssertJ imports with JUnit Assert equivalents.
    2. Replace Google Truth assertThat with JUnit assertEquals where possible.
    3. Inject missing JUnit imports.
    """
    # ── Phase 0: Fix wrong assertion libraries ──────────────────────────
    # Replace AssertJ assertThat import
    code = re.sub(
        r'^import\s+static\s+org\.assertj\.core\.api\.Assertions\.\*;.*$',
        'import static org.junit.Assert.*;',
        code, flags=re.MULTILINE
    )
    code = re.sub(
        r'^import\s+static\s+org\.assertj\.core\.api\.Assertions\.assertThat;.*$',
        'import static org.junit.Assert.*;',
        code, flags=re.MULTILINE
    )
    # Remove AssertJ wildcard import
    code = re.sub(
        r'^import\s+org\.assertj\.core\.api\.Assertions;.*\n?',
        '',
        code, flags=re.MULTILINE
    )
    # Replace Google Truth import with JUnit
    code = re.sub(
        r'^import\s+static\s+com\.google\.common\.truth\.Truth\.assertThat;.*$',
        'import static org.junit.Assert.*;',
        code, flags=re.MULTILINE
    )

    # ── Phase 1: Convert assertThat(...).isEqualTo(...) to assertEquals ──
    # Pattern: assertThat(expr).isEqualTo(expected)
    code = re.sub(
        r'assertThat\((.+?)\)\.isEqualTo\((.+?)\);',
        r'assertEquals(\2, \1);',
        code
    )
    # assertThat(expr).isTrue() -> assertTrue(expr)
    code = re.sub(
        r'assertThat\((.+?)\)\.isTrue\(\);',
        r'assertTrue(\1);',
        code
    )
    # assertThat(expr).isFalse() -> assertFalse(expr)
    code = re.sub(
        r'assertThat\((.+?)\)\.isFalse\(\);',
        r'assertFalse(\1);',
        code
    )
    # assertThat(expr).isNull() -> assertNull(expr)
    code = re.sub(
        r'assertThat\((.+?)\)\.isNull\(\);',
        r'assertNull(\1);',
        code
    )
    # assertThat(expr).isNotNull() -> assertNotNull(expr)
    code = re.sub(
        r'assertThat\((.+?)\)\.isNotNull\(\);',
        r'assertNotNull(\1);',
        code
    )

    # ── Phase 2: Fix private helper references from exemplars ────────────
    # Replace reader("...") with new StringReader("...") — common pattern
    # from existing test exemplars that use a private helper method
    code = re.sub(
        r'(?<!new )(?<!\w)reader\(',
        r'new StringReader(',
        code
    )

    # ── Phase 2b: Fix missing throws IOException ─────────────────────────
    # If the test body calls methods that throw IOException but the test method
    # doesn't declare it, add 'throws IOException' (or 'throws Exception').
    # Heuristic: if code uses beginArray/beginObject/nextString/nextLong/nextDouble/etc.
    io_methods = ['beginArray', 'endArray', 'beginObject', 'endObject',
                  'nextString', 'nextName', 'nextLong', 'nextDouble', 'nextInt',
                  'nextBoolean', 'nextNull', 'peek', 'skipValue', 'hasNext',
                  'close', 'flush', 'value(', 'name(', 'jsonValue(']
    needs_throws = any(m in code for m in io_methods)
    if needs_throws:
        # Add 'throws Exception' to @Test methods that don't have it
        code = re.sub(
            r'(public\s+void\s+\w+\s*\(\s*\))\s*\{',
            r'\1 throws Exception {',
            code
        )
        # Don't double-add
        code = re.sub(
            r'throws\s+Exception\s+throws\s+Exception',
            r'throws Exception',
            code
        )
        code = re.sub(
            r'throws\s+IOException\s+throws\s+Exception',
            r'throws Exception',
            code
        )
        # Ensure IOException import
        if 'import java.io.IOException;' not in code:
            pass  # We'll use throws Exception which doesn't need IOException import

    # ── Phase 2c: Remove duplicate import lines ──────────────────────────
    seen_imports = set()
    new_lines = []
    for line in code.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import '):
            if stripped in seen_imports:
                continue
            seen_imports.add(stripped)
        new_lines.append(line)
    code = '\n'.join(new_lines)

    # ── Phase 3: Inject missing JUnit imports ────────────────────────────
    existing = set(re.findall(r'^import[^;]+;', code, re.MULTILINE))

    to_add = []
    for symbol, import_stmt in _JUNIT_IMPORT_MAP.items():
        if import_stmt in existing:
            continue
        # Match symbol as a standalone token (not inside a string or comment)
        pattern = r'(?<![\w.])' + re.escape(symbol) + r'(?![\w])'
        if re.search(pattern, code):
            to_add.append(import_stmt)

    # Also ensure StringReader is imported if used
    if 'StringReader' in code and 'import java.io.StringReader;' not in existing:
        to_add.append('import java.io.StringReader;')

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
    test_cases: List[Dict] = None,
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
        test_cases: 预先设计的测试用例列表（来自 analyze_method 的输出）
    """
    test_class_name = test_class_name or f"{class_name}Test"
    # Derive package from full_class_name if not provided
    if package_name is None and full_class_name and "." in full_class_name:
        package_name = ".".join(full_class_name.split(".")[:-1])
    package_name = package_name or ""

    # Format test cases for prompt
    test_cases_str = format_test_cases_for_prompt(test_cases) if test_cases else "No pre-designed test cases. Design appropriate test cases based on the method."

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
        test_cases=test_cases_str,
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
