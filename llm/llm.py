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

    # ── 合并版：1次LLM调用完成理解+覆盖分析+用例设计 ────────────
    print("  [LLM] Analyzing method (understanding + coverage + test design)...")
    merged_prompt = PROMPTS["analyze_all_in_one"].format(
        full_class_name=full_class_name,
        method_signature=method_signature,
        method_code=method_code,
        context=context or "No context",
    )
    response = await chat(merged_prompt, temperature=0.4, max_tokens=4000)
    print(f"  [LLM] Analysis done ({len(response)} chars)")

    # 从合并响应中解析测试用例
    test_cases = _parse_test_cases(response)
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


# ============ 测试生成 ============


def _extract_code(text: str) -> str:
    """Extract Java code from LLM response (may be wrapped in markdown fences).

    注意：此函数只剥 markdown 围栏，不做大括号自动补全——
    因为那会掩盖模型被长度截断的情况。截断检测交给调用方语义层做。
    """
    matches = re.findall(r"```(?:java)?\n(.*?)```", text, re.DOTALL)
    code = "\n\n".join(matches) if matches else text
    code = re.sub(r'^```(?:java)?\s*$', '', code, flags=re.MULTILINE)
    code = re.sub(r'^```\s*$', '', code, flags=re.MULTILINE)
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
    # Replace Google Truth imports with JUnit
    code = re.sub(
        r'^import\s+static\s+com\.google\.common\.truth\.Truth\.assertThat;.*$',
        'import static org.junit.Assert.*;',
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
    """生成单元测试（骨架 + 逐方法 模式，规避长输出截断）

    生成策略：
      1. 先用一个小 prompt 只生成“测试类骨架”（package/imports/class/fields/@Before），
         输出量很小，不会被 max_tokens 截断。
      2. 对每个 test case 单独调用一次 LLM，每次只生成一个 @Test 方法，
         每次输出都很短（几十到一两百行），彻底规避截断。
      3. 把骨架里的占位符替换成所有拼接起来的 @Test 方法，再走 _fix_imports 落盘。

    对上游完全透明：签名与返回 dict 的结构不变。
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
        )
        print(f"  [LLM] Generating skeleton for {test_class_name}...")
        skeleton_resp = await chat(skeleton_prompt, temperature=0.3, max_tokens=2000)
        skeleton_code = _extract_code(skeleton_resp)

        # 确保占位符存在；若模型没写，就强行插入到最后一个 `}` 之前
        placeholder = "// __TEST_METHODS_PLACEHOLDER__"
        if placeholder not in skeleton_code:
            last_brace = skeleton_code.rfind("}")
            if last_brace == -1:
                return {
                    "success": False,
                    "error": "Skeleton generation failed: no class body found",
                    "truncated": True,
                }
            skeleton_code = (
                skeleton_code[:last_brace]
                + f"\n    {placeholder}\n"
                + skeleton_code[last_brace:]
            )

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
                )
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

        code = _fix_imports(code)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        return {
            "success": True,
            "output_path": output_path,
            "methods_generated": len(method_snippets),
            "methods_failed": failed_cases,
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
