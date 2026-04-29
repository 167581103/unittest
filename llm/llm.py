"""
LLM模块 - 聊天、嵌入、测试生成（统一使用OpenAI客户端）
"""

import os
import re
import json
import time
import asyncio
from typing import List, Dict
from pathlib import Path

import yaml
from openai import OpenAI

# ============ 配置 ============


def _load_config():
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
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
    """与LLM对话（异步接口，内部使用同步客户端 + asyncio.to_thread 实现真并发）

    副作用：将本次请求的 usage（prompt_tokens / completion_tokens）通过
    `core.token_meter.record_usage()` 计入当前协程作用域（见 core/token_meter.py）。
    外部调用方无需改动——record_usage 是纯副作用、幂等、失败不抛。

    鲁棒性：内置最多 3 次重试（指数退避 2s→4s→8s），覆盖两类瞬时故障：
      1) OpenAI SDK 抛出的网络/限流/超时异常（RateLimitError、APITimeoutError、
         APIConnectionError、InternalServerError 等）
      2) 上游返回 2xx 但 content 为空串/全空白（常见于内容审核命中、context 超限、
         上游瞬时截断）；这类场景不会抛异常，调用方会拿到 "0 chars" 而无从感知。
    仅当所有重试均失败时：
      - 若最后一次是异常 → 抛出该异常（让调用方决定）
      - 若最后一次是空响应 → 返回该空串（保持原始契约，不隐藏问题）
    """
    client = _get_chat_client()

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    def _call():
        return client.chat.completions.create(
            model=CONFIG["model"],
            messages=msgs,
            **kw
        )

    max_attempts = 3
    last_exc: Exception = None
    last_content: str = ""

    for attempt in range(1, max_attempts + 1):
        try:
            # 把同步阻塞调用丢到线程池，释放事件循环，让 gather 真正并行
            resp = await asyncio.to_thread(_call)
        except Exception as e:
            last_exc = e
            if attempt < max_attempts:
                backoff = 2 ** attempt  # 2s, 4s, 8s
                print(f"  [LLM] chat() attempt {attempt}/{max_attempts} raised "
                      f"{type(e).__name__}: {e}; retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                continue
            # 最后一次仍失败：抛出
            raise

        # ── token 记账（不破坏 chat() 的字符串返回契约）─────────────
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                from core.token_meter import record_usage
                record_usage(
                    prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                )
        except Exception:
            # 记账失败绝不影响主流程
            pass

        # 提取 content，防御 SDK 返回结构异常
        try:
            content = resp.choices[0].message.content or ""
        except Exception as e:
            last_exc = e
            content = ""

        if content and content.strip():
            return content

        # 空响应：再试
        last_content = content
        if attempt < max_attempts:
            backoff = 2 ** attempt
            finish_reason = None
            try:
                finish_reason = resp.choices[0].finish_reason
            except Exception:
                pass
            print(f"  [LLM] chat() attempt {attempt}/{max_attempts} returned empty content "
                  f"(finish_reason={finish_reason}); retrying in {backoff}s...")
            await asyncio.sleep(backoff)

    # 所有重试均为空响应：返回最后一次的空串，保持原始契约
    return last_content


# ============ 方法解读与测试用例设计 ============


async def analyze_method(
    class_name: str,
    method_signature: str,
    method_code: str,
    context: str = "",
    full_class_name: str = None,
    junit_version: int = 4,
) -> Dict:
    """Merged method analysis: understanding + coverage + test case design in ONE LLM call.

    Returns:
        {
            "method_understanding": str,   # what the method does
            "coverage_analysis": str,       # coverage points/surfaces
            "test_cases": list[dict],       # structured test case designs
            "test_cases_raw": str,          # raw LLM response
        }
    """
    full_class_name = full_class_name or class_name

    # 导入 token_meter 的 phase() 上下文管理器，给本函数内的 chat 红框标阶段
    from core.token_meter import phase as _phase

    # ── 合并版：1次LLM调用完成理解+覆盖分析+用例设计；失败时再做一次 JSON-only 兑底 ────────────
    print("  [LLM] Analyzing method (understanding + coverage + test design)...")
    merged_prompt = PROMPTS["analyze_all_in_one"].format(
        full_class_name=full_class_name,
        method_signature=method_signature,
        method_code=method_code,
        context=context or "No context",
    )
    # Bigger token budget: task3 JSON must not be truncated after two long analysis sections.
    with _phase("analyze"):
        response = await chat(merged_prompt, temperature=0.4, max_tokens=8000)
    print(f"  [LLM] Analysis done ({len(response)} chars)")
    # 从合并响应中解析测试用例
    test_cases = _parse_test_cases(response)

    if not test_cases:
        print("  [LLM] Parsed 0 test cases, retrying with compact JSON-only prompt...")
        _junit_major = int(junit_version)
        _junit_label = f"JUnit{_junit_major}"
        retry_prompt = (
            "You are a Java unit test designer. Return ONLY a JSON array (no markdown, no explanation).\n"
            f"Design 5-10 runnable {_junit_label} test cases for the target method using only public APIs.\n"
            f"Target class: {full_class_name}\n"
            f"Method signature: {method_signature}\n"
            "Each item must include fields: id,name,description,category,priority,setup,input,expected_output,assertion_type,coverage_target.\n"
            "Name must be a valid Java method name starting with test.\n"
            "Source code:\n"
            "```java\n"
            f"{method_code}\n"
            "```\n"
            "Context:\n"
            f"{context or 'No context'}\n"
        )
        with _phase("analyze_retry"):
            retry_resp = await chat(retry_prompt, temperature=0.2, max_tokens=3000)
        retry_cases = _parse_test_cases(retry_resp)
        if retry_cases:
            test_cases = retry_cases
            # 记录原始响应，便于排查
            response = response + "\n\n==== RETRY(JSON-ONLY) ====\n" + retry_resp
            print(f"  [LLM] Retry recovered {len(test_cases)} test cases")

    print(f"  [LLM] Parsed {len(test_cases)} test cases")

    # 将响应拆分为理解和覆盖分析两部分（用于日志和兼容性）
    # 简单策略：按任务标题拆分
    method_understanding = ""
    coverage_analysis = ""

    parts = re.split(r'##\s*任务[23]', response, maxsplit=2)
    if len(parts) >= 3:
        method_understanding = parts[0].strip()
        # 从第二部分中提取覆盖分析
        cov_match = re.search(r'##\s*任务2[：:]\s*覆盖分析(.*?)(?=##\s*任务3|$)', response, re.DOTALL)
        coverage_analysis = cov_match.group(1).strip() if cov_match else parts[1].strip()
    else:
        method_understanding = response[:len(response)//2]
        coverage_analysis = response[len(response)//2:]

    return {
        "method_understanding": method_understanding,
        "coverage_analysis": coverage_analysis,
        "test_cases": test_cases,
        "test_cases_raw": response,
    }


def _parse_test_cases(raw: str) -> List[Dict]:
    """Parse the LLM response to extract the JSON test case array.

    Tolerant to:
      - fenced or bare JSON
      - trailing commas
      - unescaped newlines/tabs inside string literals (common LLM slip)
    """

    def _try_load(snippet: str):
        def _extract_cases(data):
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for k in ("test_cases", "cases", "tests", "data"):
                    v = data.get(k)
                    if isinstance(v, list):
                        return v
            return None

        # direct parse
        try:
            data = json.loads(snippet)
            out = _extract_cases(data)
            if out is not None:
                return out
        except json.JSONDecodeError:
            pass

        # remove trailing commas like ,] or ,}
        cleaned = re.sub(r',\s*([\]}])', r'\1', snippet)
        try:
            data = json.loads(cleaned)
            out = _extract_cases(data)
            if out is not None:
                return out
        except json.JSONDecodeError:
            pass

        # escape raw control chars inside strings (\n, \r, \t left un-escaped by the model)
        def _fix_string(m):
            body = m.group(0)
            body = body.replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n').replace('\t', '\\t')
            return body

        # Only mutate inside double-quoted strings
        fixed = re.sub(r'"(?:\\.|[^"\\])*"', _fix_string, cleaned, flags=re.DOTALL)
        try:
            data = json.loads(fixed)
            out = _extract_cases(data)
            if out is not None:
                return out
        except json.JSONDecodeError:
            pass

        # Fallback: YAML parser is more tolerant to single quotes / minor JSON issues.
        try:
            data = yaml.safe_load(cleaned)
            out = _extract_cases(data)
            if out is not None:
                return out
        except Exception:
            pass

        return None

    # Strategy 1: extract fenced JSON code block
    block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
    if block_match:
        out = _try_load(block_match.group(1).strip())
        if out is not None:
            return out

    # Strategy 2: find outermost JSON array in the raw response
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
                out = _try_load(raw[start:i + 1])
                if out is not None:
                    return out
                break

    # Dump raw response for offline inspection
    try:
        dump_path = '/tmp/analyze_raw.txt'
        with open(dump_path, 'w', encoding='utf-8') as f:
            f.write(raw)
        print(f"  [LLM] Warning: failed to parse test cases JSON, raw dumped to {dump_path}")
    except Exception:
        print("  [LLM] Warning: failed to parse test cases JSON, returning empty list")
    return []


# ============ 测试生成 ============


def _extract_code(text: str) -> str:
    """Extract Java code from LLM response (may be wrapped in markdown fences).

    注意：此函数只剥 markdown 围栏，不做大括号自动补全——
    因为那会掩盖模型被长度截断的情况。截断检测交给调用方语义层做。

    兼容如下边界情况（过去踩过坑）：
    - ```java\n...code...\n``` （规范闭合）
    - ```java\n...code...\n```（末尾无换行）
    - ```java ...code... ``` （单行围栏，无换行）
    - 代码后出现独立的 ``` 行（收尾围栏）
    - 行首/行尾残留的裸反引号
    """
    # 1) 首选：完整的 fenced block（允许闭合围栏与代码同行）
    matches = re.findall(r"```(?:java|Java|JAVA)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    code = "\n\n".join(matches) if matches else text

    # 2) 兜底清扫：任何仍然残留的整行围栏或尾部 ```
    code = re.sub(r'^\s*```(?:java|Java|JAVA)?\s*$', '', code, flags=re.MULTILINE)
    code = re.sub(r'^\s*```\s*$', '', code, flags=re.MULTILINE)
    # 3) 彻底清掉代码内部残留的裸反引号（LLM 偶尔会出现行内 ``` 残片）
    code = code.replace('```java', '').replace('```Java', '').replace('```JAVA', '')
    # 4) 行内孤立的三个反引号清掉（保守：只在不包含字母的行里清掉）
    lines = []
    for ln in code.split('\n'):
        stripped = ln.strip()
        if stripped in ('```', '``', '`'):
            continue
        lines.append(ln)
    code = '\n'.join(lines)
    return code.strip()


# ─── JUnit 4 symbol -> import 映射 ──────────────────────────────────
_JUNIT4_IMPORT_MAP = {
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

# ─── JUnit 5 (Jupiter) symbol -> import 映射 ────────────────────────
_JUNIT5_IMPORT_MAP = {
    "assertEquals":     "import static org.junit.jupiter.api.Assertions.assertEquals;",
    "assertNotEquals":  "import static org.junit.jupiter.api.Assertions.assertNotEquals;",
    "assertTrue":       "import static org.junit.jupiter.api.Assertions.assertTrue;",
    "assertFalse":      "import static org.junit.jupiter.api.Assertions.assertFalse;",
    "assertNull":       "import static org.junit.jupiter.api.Assertions.assertNull;",
    "assertNotNull":    "import static org.junit.jupiter.api.Assertions.assertNotNull;",
    "assertSame":       "import static org.junit.jupiter.api.Assertions.assertSame;",
    "assertNotSame":    "import static org.junit.jupiter.api.Assertions.assertNotSame;",
    "assertArrayEquals":"import static org.junit.jupiter.api.Assertions.assertArrayEquals;",
    "assertThrows":     "import static org.junit.jupiter.api.Assertions.assertThrows;",
    "fail":             "import static org.junit.jupiter.api.Assertions.fail;",
    "@Test":            "import org.junit.jupiter.api.Test;",
    "@BeforeEach":      "import org.junit.jupiter.api.BeforeEach;",
    "@AfterEach":       "import org.junit.jupiter.api.AfterEach;",
    "@BeforeAll":       "import org.junit.jupiter.api.BeforeAll;",
    "@AfterAll":        "import org.junit.jupiter.api.AfterAll;",
    "@Disabled":        "import org.junit.jupiter.api.Disabled;",
    # 为了兼容 LLM 误用 JUnit 4 注解，这里也把 @Before / @BeforeClass 映射到 JUnit 5 近义词；
    # 这些 key 不会真正被添加（实际代码里我们会把它们改写成 @BeforeEach 等），保留仅作声明
}

# 向后兼容：默认仍指向 JUnit 4 映射（外部若 import _JUNIT_IMPORT_MAP 不会挂掉）
_JUNIT_IMPORT_MAP = _JUNIT4_IMPORT_MAP


def _junit_import_map(junit_version: int = 4) -> Dict[str, str]:
    """Return the symbol -> import mapping for the given JUnit major version."""
    return _JUNIT5_IMPORT_MAP if int(junit_version) >= 5 else _JUNIT4_IMPORT_MAP


def _junit_profile_text(junit_version: int = 4) -> str:
    """Return the project-specific JUnit usage instructions to splice into prompts.

    Keeping this as a single text block lets us inject it into any prompt template
    via plain concatenation, without turning prompts.yaml into a giant conditional.
    """
    if int(junit_version) >= 5:
        return (
            "【目标项目使用 JUnit 5 (Jupiter)，请严格遵守以下 API 约定，禁止使用 JUnit 4 API】\n"
            "- 测试注解：import org.junit.jupiter.api.Test;  （不要用 org.junit.Test）\n"
            "- 生命周期：import org.junit.jupiter.api.BeforeEach; / AfterEach; / BeforeAll; / AfterAll;\n"
            "  ⚠️ 绝对不要使用 @Before / @After / @BeforeClass / @AfterClass（那是 JUnit 4）\n"
            "- 断言：import static org.junit.jupiter.api.Assertions.*;  （或按需静态导入 assertEquals 等）\n"
            "  ⚠️ 绝对不要使用 import static org.junit.Assert.*;\n"
            "- 异常断言优先使用 assertThrows(Exception.class, () -> ...)（JUnit 5 支持良好）\n"
            "- 测试方法和类可以是 package-private（不需要 public），但保持 public 也不会错\n"
            "- 不要使用 @RunWith / @Rule（那是 JUnit 4 的概念）\n"
        )
    # 默认：JUnit 4
    return (
        "【目标项目使用 JUnit 4，请严格遵守以下 API 约定，禁止使用 JUnit 5 API】\n"
        "- 测试注解：import org.junit.Test;\n"
        "- 生命周期：import org.junit.Before; / After; / BeforeClass; / AfterClass;\n"
        "  ⚠️ 绝对不要使用 @BeforeEach / @AfterEach / @BeforeAll / @AfterAll（那是 JUnit 5）\n"
        "- 断言：import static org.junit.Assert.*;  （或按需静态导入 assertEquals 等）\n"
        "  ⚠️ 绝对不要使用 import static org.junit.jupiter.api.Assertions.*;\n"
        "- 异常断言优先用 try-catch + fail 模式，避免 assertThrows（它在 JUnit 4.13 才有，4.12 不可用）\n"
        "- 测试类必须是 public，测试方法必须是 public\n"
    )


def _normalize_skeleton_code(skeleton_code: str, placeholder: str = "// __TEST_METHODS_PLACEHOLDER__") -> Dict:
    """规范化并校验 skeleton 输出；不做最小骨架兜底，校验失败由上层触发重试。"""
    code = (skeleton_code or "").strip()
    if not code:
        return {"ok": False, "code": "", "error": "empty skeleton"}

    if not re.search(r'\bclass\s+\w+', code):
        return {"ok": False, "code": "", "error": "no class declaration"}

    open_braces = code.count('{')
    close_braces = code.count('}')
    if open_braces != close_braces:
        return {
            "ok": False,
            "code": "",
            "error": f"brace mismatch: open={open_braces}, close={close_braces}",
        }

    if placeholder not in code:
        last_brace = code.rfind("}")
        if last_brace == -1:
            return {"ok": False, "code": "", "error": "no class body found"}
        code = code[:last_brace] + f"\n    {placeholder}\n" + code[last_brace:]

    return {"ok": True, "code": code, "error": ""}


def _build_skeleton_retry_prompt(
    class_name: str,
    test_class_name: str,
    full_class_name: str,
    package_name: str,
    method_signature: str,
    method_code: str,
    context: str,
    previous_output: str,
    placeholder: str = "// __TEST_METHODS_PLACEHOLDER__",
    junit_version: int = 4,
) -> str:
    """当 skeleton 输出不合格时，构造一个更强约束的重试提示。"""
    if int(junit_version) >= 5:
        _junit_req = (
            "3) Must include JUnit 5 (Jupiter) imports: org.junit.jupiter.api.Test,"
            " org.junit.jupiter.api.BeforeEach, static org.junit.jupiter.api.Assertions.*\n"
            "   ⚠️ Do NOT use any org.junit.Test / org.junit.Before / org.junit.Assert (JUnit 4)\n"
        )
        _junit_label = "JUnit 5 (Jupiter)"
    else:
        _junit_req = (
            "3) Must include JUnit 4 imports: org.junit.Test, org.junit.Before,"
            " static org.junit.Assert.*\n"
            "   ⚠️ Do NOT use any org.junit.jupiter.* (JUnit 5)\n"
        )
        _junit_label = "JUnit 4"

    return (
        f"You previously failed to generate a valid {_junit_label} skeleton. Regenerate from scratch.\n"
        "Return ONLY one Java code block and nothing else.\n"
        f"Target class: {full_class_name}\n"
        f"Method signature: {method_signature}\n"
        f"Test class name: {test_class_name}\n"
        f"Package: {package_name}\n\n"
        "Hard requirements:\n"
        f"1) First line must be exactly: package {package_name};\n"
        f"2) Must include: import {full_class_name};\n"
        f"{_junit_req}"
        f"4) Must declare: public class {test_class_name} {{ ... }}\n"
        "5) Do NOT generate any @Test methods\n"
        f"6) Must keep this exact placeholder inside class body: {placeholder}\n"
        "7) Use only public APIs from context\n"
        "8) Do not output markdown explanations\n\n"
        "Method source:\n"
        "```java\n"
        f"{method_code}\n"
        "```\n\n"
        "Context:\n"
        f"{context or 'No context'}\n\n"
        "Previous invalid output (for reference, do not copy blindly):\n"
        "```text\n"
        f"{previous_output}\n"
        "```\n"
    )


def _fix_imports(code: str, junit_version: int = 4) -> str:
    """Scan generated code and fix common import issues.

    1. Replace AssertJ imports with JUnit Assert equivalents (按 junit_version 选目标).
    2. Replace Google Truth assertThat with JUnit assertEquals where possible.
    3. Inject missing JUnit imports.
    4. 若 junit_version=5，还会把误用的 JUnit 4 API 纠正成 JUnit 5 Jupiter。
    """
    target_assert_star = (
        "import static org.junit.jupiter.api.Assertions.*;"
        if int(junit_version) >= 5
        else "import static org.junit.Assert.*;"
    )

    # ── Phase 0: Fix wrong assertion libraries ──────────────────────────
    # Replace AssertJ assertThat import
    code = re.sub(
        r'^import\s+static\s+org\.assertj\.core\.api\.Assertions\.\*;.*$',
        target_assert_star,
        code, flags=re.MULTILINE
    )
    code = re.sub(
        r'^import\s+static\s+org\.assertj\.core\.api\.Assertions\.assertThat;.*$',
        target_assert_star,
        code, flags=re.MULTILINE
    )
    # Remove AssertJ wildcard import
    code = re.sub(
        r'^import\s+org\.assertj\.core\.api\.Assertions;.*\n?',
        '',
        code, flags=re.MULTILINE
    )
    # Replace Google Truth imports with JUnit
    code = re.sub(
        r'^import\s+static\s+com\.google\.common\.truth\.Truth\.assertThat;.*$',
        target_assert_star,
        code, flags=re.MULTILINE
    )
    # Remove non-static Truth import (com.google.common.truth.Truth)
    code = re.sub(
        r'^import\s+com\.google\.common\.truth\.Truth;.*\n?',
        '',
        code, flags=re.MULTILINE
    )
    # Remove any other com.google.common.truth.* imports
    code = re.sub(
        r'^import\s+(?:static\s+)?com\.google\.common\.truth\..*\n?',
        '',
        code, flags=re.MULTILINE
    )

    # ── Truth 链式断言替换（具体规则先处理，通用规则后处理）──────────────
    # assertThat(exc).hasMessageThat().contains(sub)
    # -> assertTrue(exc.getMessage() != null && exc.getMessage().contains(sub))
    code = re.sub(
        r'assertThat\((\w+)\)\.hasMessageThat\(\)\.contains\((.+?)\);',
        r'assertTrue(\1.getMessage() != null && \1.getMessage().contains(\2));',
        code
    )
    # assertThat(exc).hasMessageThat().isEqualTo(msg)
    code = re.sub(
        r'assertThat\((\w+)\)\.hasMessageThat\(\)\.isEqualTo\((.+?)\);',
        r'assertEquals(\2, \1.getMessage());',
        code
    )
    # assertThat(exc).hasMessageThat().startsWith(prefix)
    code = re.sub(
        r'assertThat\((\w+)\)\.hasMessageThat\(\)\.startsWith\((.+?)\);',
        r'assertTrue(\1.getMessage() != null && \1.getMessage().startsWith(\2));',
        code
    )
    # assertThat(exc).hasMessageThat().endsWith(suffix)
    code = re.sub(
        r'assertThat\((\w+)\)\.hasMessageThat\(\)\.endsWith\((.+?)\);',
        r'assertTrue(\1.getMessage() != null && \1.getMessage().endsWith(\2));',
        code
    )
    # assertThat(expr).isEqualTo(expected) -> assertEquals(expected, expr)
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
    # assertThat(expr).contains(sub) -> assertTrue(expr.contains(sub))
    code = re.sub(
        r'assertThat\((.+?)\)\.contains\((.+?)\);',
        r'assertTrue(\1.contains(\2));',
        code
    )
    # assertThat(list).hasSize(n) -> assertEquals(n, list.size())
    code = re.sub(
        r'assertThat\((.+?)\)\.hasSize\((.+?)\);',
        r'assertEquals(\2, \1.size());',
        code
    )
    # assertThat(list).isEmpty() -> assertTrue(list.isEmpty())
    code = re.sub(
        r'assertThat\((.+?)\)\.isEmpty\(\);',
        r'assertTrue(\1.isEmpty());',
        code
    )
    # assertThat(list).isNotEmpty() -> assertFalse(list.isEmpty())
    code = re.sub(
        r'assertThat\((.+?)\)\.isNotEmpty\(\);',
        r'assertFalse(\1.isEmpty());',
        code
    )
    # assertThat(str).startsWith(prefix) -> assertTrue(str.startsWith(prefix))
    code = re.sub(
        r'assertThat\((.+?)\)\.startsWith\((.+?)\);',
        r'assertTrue(\1.startsWith(\2));',
        code
    )
    # assertThat(str).endsWith(suffix) -> assertTrue(str.endsWith(suffix))
    code = re.sub(
        r'assertThat\((.+?)\)\.endsWith\((.+?)\);',
        r'assertTrue(\1.endsWith(\2));',
        code
    )
    # assertThat(a).isGreaterThan(b) -> assertTrue(a > b)
    code = re.sub(
        r'assertThat\((.+?)\)\.isGreaterThan\((.+?)\);',
        r'assertTrue(\1 > \2);',
        code
    )
    # assertThat(a).isLessThan(b) -> assertTrue(a < b)
    code = re.sub(
        r'assertThat\((.+?)\)\.isLessThan\((.+?)\);',
        r'assertTrue(\1 < \2);',
        code
    )
    # assertThat(a).isInstanceOf(Clazz.class) -> assertTrue(a instanceof Clazz)
    code = re.sub(
        r'assertThat\((.+?)\)\.isInstanceOf\((\w+)\.class\);',
        r'assertTrue(\1 instanceof \2);',
        code
    )
    # Catch-all: remaining assertThat(...).xxx(...) -> comment placeholder
    code = re.sub(
        r'assertThat\((.+?)\)\.[a-zA-Z]+\([^;]*\);',
        r'// TODO: assertThat(\1) - manual assertion needed',
        code
    )

    # ── Phase 2: Fix private helper references from exemplars ────────────
    # Replace reader("...") with new StringReader("...") — common pattern
    # from existing test exemplars that use a private helper method.
    # Only replace call-site usages, NOT method declarations.
    # A call site looks like: `= reader("...")`  or  `(reader("...")`  or  `, reader("...")`
    # A declaration looks like: `Reader reader(` or `static Reader reader(`
    # Strategy: first remove any private helper `reader(...)` method declaration
    # from the generated code, then replace remaining call-site usages.
    # Step 1: Remove the private helper method body entirely
    code = re.sub(
        r'\n\s*private\s+static\s+\w+\s+reader\s*\([^)]*\)\s*\{[^}]*\}\n?',
        '\n',
        code
    )
    # Step 2: Replace call-site usages: reader("...") -> new StringReader("...")
    # Only when preceded by non-word chars (=, (, ,, space) to avoid method decls
    code = re.sub(
        r'(?<=[=(,\s])reader\((?=[^)]*["\'])',
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

    # ── Phase 2d: When target is JUnit 5, rewrite any JUnit 4 API usage ──
    # LLM 偶尔仍会输出 JUnit 4 的 import/注解；在 junit_version=5 的项目里这会直接
    # "cannot find symbol"，因此在这里做一次确定性改写。
    if int(junit_version) >= 5:
        # import org.junit.Test;  -> import org.junit.jupiter.api.Test;
        code = re.sub(
            r'^\s*import\s+org\.junit\.Test\s*;\s*$',
            'import org.junit.jupiter.api.Test;',
            code, flags=re.MULTILINE,
        )
        # import org.junit.Before; / After; / BeforeClass; / AfterClass;  -> JUnit 5 等价
        _LIFECYCLE_V4_TO_V5 = {
            'Before':      'BeforeEach',
            'After':       'AfterEach',
            'BeforeClass': 'BeforeAll',
            'AfterClass':  'AfterAll',
            'Ignore':      'Disabled',
        }
        for v4, v5 in _LIFECYCLE_V4_TO_V5.items():
            code = re.sub(
                rf'^\s*import\s+org\.junit\.{v4}\s*;\s*$',
                f'import org.junit.jupiter.api.{v5};',
                code, flags=re.MULTILINE,
            )
            # 注解同步改写：@Before -> @BeforeEach, @Ignore -> @Disabled 等
            code = re.sub(rf'@{v4}\b', f'@{v5}', code)
        # import static org.junit.Assert.*;  -> JUnit 5 Assertions.*
        code = re.sub(
            r'^\s*import\s+static\s+org\.junit\.Assert\.\*\s*;\s*$',
            'import static org.junit.jupiter.api.Assertions.*;',
            code, flags=re.MULTILINE,
        )
        # import static org.junit.Assert.<method>;  -> JUnit 5 equivalent
        code = re.sub(
            r'^\s*import\s+static\s+org\.junit\.Assert\.(\w+)\s*;\s*$',
            r'import static org.junit.jupiter.api.Assertions.\1;',
            code, flags=re.MULTILINE,
        )
        # 兜底：整行就是 import org.junit.XXX;（除 Test/Before/... 外的未知符号）
        # 为了不误伤 JUnit 5 内部路径，这里不做进一步改写，留给编译错误反馈驱动修复。

    # ── Phase 3: Inject missing JUnit imports ────────────────────────────
    existing = set(re.findall(r'^import[^;]+;', code, re.MULTILINE))

    to_add = []
    junit_map = _junit_import_map(junit_version)
    for symbol, import_stmt in junit_map.items():
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
    one_shot: bool = False,
    junit_version: int = 4,
) -> Dict:
    """生成单元测试。支持两种模式：

    - 两步式（默认，one_shot=False）：骨架 + 逐方法，规避长输出截断
      1. 先用小 prompt 只生成“测试类骨架”（package/imports/class/fields/@Before）
      2. 对每个 test case 单独调用 LLM，每次只生成一个 @Test 方法
      3. 骨架占位符替换为拼接后的所有 @Test 方法

    - 一步式（one_shot=True，消融实验用）：一次 LLM 调用生成完整测试类
      把所有 test_cases 塞进一个大 prompt，让 LLM 一次输出整个测试类。
      用来对比两步式的覆盖率/编译成功率差异。

    对上游返回 dict 的结构在两种模式下都保持一致。
    """
    test_class_name = test_class_name or f"{class_name}Test"
    if package_name is None and full_class_name and "." in full_class_name:
        package_name = ".".join(full_class_name.split(".")[:-1])
    package_name = package_name or ""

    if not test_cases:
        return {
            "success": False,
            "error": "test_cases is required (run analyze_method first to design test cases)",
        }

    # 项目特定的 JUnit 约定（拼接在 prompt 末尾，而非侵入 prompts.yaml）
    junit_profile = _junit_profile_text(junit_version)

    # ═══════════════════ 一步式生成（消融实验）═══════════════════
    # 分阶段记录 token（generate_oneshot / skeleton / per_method）
    from core.token_meter import phase as _phase
    if one_shot:
        try:
            print(f"  [LLM] One-shot generating full test class {test_class_name} (JUnit {int(junit_version)})...")
            oneshot_prompt = PROMPTS["test_oneshot"].format(
                class_name=class_name,
                test_class_name=test_class_name,
                full_class_name=full_class_name or class_name,
                package_name=package_name,
                method_signature=method_signature,
                method_code=method_code,
                context=context or "无上下文",
                test_cases=json.dumps(test_cases, ensure_ascii=False, indent=2),
            ) + "\n\n" + junit_profile
            # 一次性生成完整测试类，需要更大 token 预算
            with _phase("generate_oneshot"):
                resp = await chat(oneshot_prompt, temperature=0.4, max_tokens=6000)
            code = _extract_code(resp)

            has_class = bool(re.search(r'\bclass\s+\w+', code))
            if not has_class:
                return {
                    "success": False,
                    "error": "One-shot generation failed: no class declaration",
                    "truncated": True,
                }

            # 括号平衡粗修（同两步式逻辑）
            if code.count('{') > code.count('}'):
                code += '\n}'

            code = _fix_imports(code, junit_version=junit_version)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(code)

            # 统计实际生成的 @Test 方法数，便于和两步式对比
            tests_found = len(re.findall(r'@Test\b', code))
            return {
                "success": True,
                "output_path": output_path,
                "methods_generated": tests_found,
                "methods_failed": [],
                "mode": "one_shot",
            }
        except Exception as e:
            return {"success": False, "error": f"one_shot failed: {e}"}

    # ═══════════════════ 两步式生成（默认）═══════════════════
    try:
        # ── Step 1: 生成骨架 ─────────────────────────────────────────
        skeleton_prompt = PROMPTS["test_skeleton"].format(
            class_name=class_name,
            test_class_name=test_class_name,
            full_class_name=full_class_name or class_name,
            package_name=package_name,
            method_signature=method_signature,
            method_code=method_code,
            context=context or "无上下文",
        ) + "\n\n" + junit_profile
        print(f"  [LLM] Generating skeleton for {test_class_name} (JUnit {int(junit_version)})...")
        placeholder = "// __TEST_METHODS_PLACEHOLDER__"
        with _phase("generate_skeleton"):
            skeleton_resp = await chat(skeleton_prompt, temperature=0.3, max_tokens=2000)
        skeleton_code = _extract_code(skeleton_resp)

        norm = _normalize_skeleton_code(skeleton_code, placeholder=placeholder)
        if not norm["ok"]:
            print(f"  [LLM] Skeleton invalid ({norm['error']}), retrying with strict prompt...")
            retry_prompt = _build_skeleton_retry_prompt(
                class_name=class_name,
                test_class_name=test_class_name,
                full_class_name=full_class_name or class_name,
                package_name=package_name,
                method_signature=method_signature,
                method_code=method_code,
                context=context or "No context",
                previous_output=skeleton_resp,
                placeholder=placeholder,
                junit_version=junit_version,
            )
            with _phase("generate_skeleton_retry"):
                retry_resp = await chat(retry_prompt, temperature=0.1, max_tokens=2500)
            retry_code = _extract_code(retry_resp)
            norm_retry = _normalize_skeleton_code(retry_code, placeholder=placeholder)
            if not norm_retry["ok"]:
                return {
                    "success": False,
                    "error": f"Skeleton generation failed after retry: {norm_retry['error']}",
                    "truncated": True,
                }
            skeleton_code = norm_retry["code"]
        else:
            skeleton_code = norm["code"]

        # ── Step 2: 逐个 test case 生成 @Test 方法 ────────────────────
        method_snippets: List[str] = []
        failed_cases: List[str] = []
        for idx, case in enumerate(test_cases, 1):
            case_name = case.get("name", f"test_case_{idx}") if isinstance(case, dict) else f"test_case_{idx}"
            print(f"  [LLM] Generating test method {idx}/{len(test_cases)}: {case_name}")
            try:
                method_prompt = PROMPTS["test_single_method"].format(
                    class_name=class_name,
                    test_class_name=test_class_name,
                    full_class_name=full_class_name or class_name,
                    package_name=package_name,
                    method_signature=method_signature,
                    method_code=method_code,
                    skeleton=skeleton_code,
                    context=context or "无上下文",
                    test_case=json.dumps(case, ensure_ascii=False, indent=2),
                ) + "\n\n" + junit_profile
                with _phase("generate_per_method"):
                    m_resp = await chat(method_prompt, temperature=0.4, max_tokens=2000)
                m_code = _extract_single_method(m_resp)
                if m_code:
                    method_snippets.append(m_code)
                else:
                    failed_cases.append(case_name)
                    print(f"  [LLM] Warning: failed to extract method body for {case_name}")
            except Exception as e:
                failed_cases.append(case_name)
                print(f"  [LLM] Warning: case {case_name} failed: {e}")

        if not method_snippets:
            return {
                "success": False,
                "error": f"All test methods failed to generate (failed: {failed_cases})",
                "truncated": False,
            }

        # ── Step 3: 拼装骨架 + 所有测试方法 ──────────────────────────
        joined_methods = "\n\n    ".join(method_snippets)
        code = skeleton_code.replace(placeholder, joined_methods)

        # 基本完整性校验
        has_class = bool(re.search(r'\bclass\s+\w+', code))
        if not has_class:
            return {
                "success": False,
                "error": "Assembled code missing class declaration",
                "truncated": True,
            }
        # 粗暴修一下括号平衡
        if code.count('{') > code.count('}'):
            code += '\n}'

        code = _fix_imports(code, junit_version=junit_version)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        return {
            "success": True,
            "output_path": output_path,
            "methods_generated": len(method_snippets),
            "methods_failed": failed_cases,
            "mode": "two_step",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _extract_single_method(text: str) -> str:
    """从 LLM 响应中抽取一个 @Test 方法代码块。

    返回去除 markdown 围栏后的纯 Java 代码。若抽不到，则返回空字符串。
    """
    # 优先取 fenced code block
    matches = re.findall(r"```(?:java)?\n(.*?)```", text, re.DOTALL)
    if matches:
        body = "\n\n".join(matches).strip()
    else:
        body = text.strip()

    # 去掉可能出现的 package / import / class 声明（防止模型不听话）
    body = re.sub(r'^\s*package\s+[^;]+;\s*$', '', body, flags=re.MULTILINE)
    body = re.sub(r'^\s*import\s+[^;]+;\s*$', '', body, flags=re.MULTILINE)
    # 去除可能的 class 包裹：`public class Foo {  ...  }`
    cls_match = re.search(r'class\s+\w+\s*\{(.*)\}\s*$', body, re.DOTALL)
    if cls_match:
        body = cls_match.group(1).strip()

    # 必须包含 @Test 且大括号平衡，否则视为提取失败
    if '@Test' not in body:
        return ""
    if body.count('{') != body.count('}'):
        return ""

    return body.strip()


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
