#!/usr/bin/env python3
"""
examples/external_integration.py
────────────────────────────────────────────────────────────────────
演示：外部系统如何**仅依赖核心层**（core + llm + rag）调用本项目的
测试生成方法论，而不碰任何 Maven / JaCoCo / 实验报告相关代码。

本文件刻意只 import 下述 3 个核心模块：
    - llm.llm.analyze_method           （方法理解 + 用例设计）
    - llm.llm.generate_test            （两步式骨架 + 单方法生成）
    - core.fix_loop.fix_compile_errors （规则 + RAG + LLM 修复循环）

外部运行时（IDE 插件、CI、自定义沙箱、…）只需提供一个
compile_fn(code) -> (success, output) 回调，用来在你自己的环境里
重新编译一次测试代码。其他一概不需要。

运行方式：
    python examples/external_integration.py

它会：
    1) 伪造一个"编译器"（这里用 javac 也行，用任何静态分析工具都行）
    2) 让核心层生成并自动修复一个 JUnit 测试
    3) 打印最终得到的 Java 代码

本脚本**故意不 import** evaluation.* / experiments.*，
以此证明：核心层与评估层是解耦的。
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

# ── 只从核心层 import ─────────────────────────────────────────────
from llm.llm import analyze_method, generate_test          # noqa: E402
from core.fix_loop import fix_compile_errors               # noqa: E402


# ── 示例被测代码（一个最简单的 Java 方法）────────────────────────
TARGET_CLASS = "com.example.Calc"
TARGET_METHOD_SIG = "public static int divide(int a, int b)"
TARGET_METHOD_CODE = """\
public static int divide(int a, int b) {
    if (b == 0) {
        throw new IllegalArgumentException("b must not be zero");
    }
    return a / b;
}
"""


# ── 外部运行时提供的"编译回调" ────────────────────────────────────
# 我们在这里用系统的 javac 做一次真实编译，演示"核心层不关心
# 运行时是什么"——你的回调可以换成 Gradle、Bazel、IDE 插件的内置
# compilation task，甚至可以是某个在线 Java compile REST API。
def make_compile_fn():
    """返回一个 compile_fn(code) -> (success, output)。

    该函数用临时目录 + javac 来编译一个独立的 .java 文件；
    不真正跑测试，只验证"能过编译"。
    """
    javac = shutil.which("javac")
    if not javac:
        raise RuntimeError(
            "No javac on PATH; this demo needs a JDK. "
            "External integrators can plug ANY compile backend here."
        )
    junit_jar = _find_any_junit_jar()

    def compile_fn(code: str) -> Tuple[bool, str]:
        # 从代码里扒出 public class XXX 做文件名
        m = re.search(r'public\s+class\s+(\w+)', code)
        if not m:
            return False, "No public class declaration"
        cls = m.group(1)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"{cls}.java"
            src.write_text(code, encoding="utf-8")
            cmd = [javac, "-d", tmp]
            if junit_jar:
                cmd += ["-cp", junit_jar]
            cmd += [str(src)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            combined = (r.stdout or "") + (r.stderr or "")
            return r.returncode == 0, combined

    return compile_fn


def _find_any_junit_jar() -> str:
    """尝试在常见位置找一个 JUnit 4 jar；找不到也不致命（demo 仍可演示流程）。"""
    candidates = [
        "~/.m2/repository/junit/junit/4.13.2/junit-4.13.2.jar",
        "~/.m2/repository/junit/junit/4.12/junit-4.12.jar",
    ]
    for c in candidates:
        p = Path(c).expanduser()
        if p.exists():
            hamcrest = next(p.parent.parent.parent.parent.glob(
                "org/hamcrest/hamcrest-core/*/hamcrest-core-*.jar"), None)
            if hamcrest:
                return f"{p}:{hamcrest}"
            return str(p)
    return ""


# ── 主流程 ──────────────────────────────────────────────────────
async def run():
    print("=" * 70)
    print("  External-integration demo: core-layer only, NO evaluation/ deps")
    print("=" * 70)
    out_dir = Path(tempfile.mkdtemp(prefix="utgen_demo_"))
    out_file = out_dir / "CalcTest.java"

    # Step 1: 用核心层的 analyze_method 设计测试用例
    print("\n[1/3] analyze_method ...")
    analysis = await analyze_method(
        class_name="Calc",
        method_signature=TARGET_METHOD_SIG,
        method_code=TARGET_METHOD_CODE,
        full_class_name=TARGET_CLASS,
        junit_version=4,
    )
    cases = analysis.get("test_cases") or []
    print(f"      designed {len(cases)} test cases")

    # Step 2: 用核心层的 generate_test 产出 Java 源
    print("\n[2/3] generate_test ...")
    gen = await generate_test(
        class_name="Calc",
        method_signature=TARGET_METHOD_SIG,
        method_code=TARGET_METHOD_CODE,
        output_path=str(out_file),
        test_class_name="CalcTest",
        full_class_name=TARGET_CLASS,
        package_name="com.example",
        test_cases=cases,
        junit_version=4,
    )
    if not gen.get("success"):
        print(f"      generation failed: {gen.get('error')}")
        return
    print(f"      generated @ {out_file} ({gen.get('methods_generated')} @Test methods)")

    # Step 3: 让核心层的 fix_compile_errors 跑一次修复闭环
    print("\n[3/3] fix_compile_errors ...")
    compile_fn = make_compile_fn()
    code = out_file.read_text(encoding="utf-8")
    ok, initial_out = compile_fn(code)
    if ok:
        print("      initial code compiled — no fix needed.")
        return
    print(f"      initial compile failed; invoking fix loop...")

    fixed, success, fix_log = await fix_compile_errors(
        code=code,
        compile_output=initial_out,
        context="",                         # 可选 RAG 上下文
        max_retries=3,
        compile_fn=compile_fn,              # ★ 关键：外部 runtime 注入
        target_class=TARGET_CLASS,
        method_signature=TARGET_METHOD_SIG,
        junit_version=4,
    )
    print("\n      fix log:")
    for ln in fix_log:
        print(f"        · {ln}")
    print(f"\n      final status: {'✓ COMPILE OK' if success else '✗ still failing'}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run())
