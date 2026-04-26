#!/usr/bin/env python3
"""
Smoke test: 验证骨架+逐方法生成流程走通真实 LLM 调用，
并用 TestEvaluator 在真实 gson 项目里编译+运行+算覆盖率。
"""
import os
import asyncio
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report


PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUT_PATH = "/tmp/smoke_JsonWriter_value_Test.java"

METHOD_CODE = """public JsonWriter value(Number value) throws IOException {
    if (value == null) {
        return nullValue();
    }

    String string = value.toString();
    if (string.equals("-Infinity") || string.equals("Infinity") || string.equals("NaN")) {
        throw new IllegalArgumentException(
            "Numeric values must be finite, but was " + value);
    }

    beforeValue();
    out.append(string);
    return this;
}"""


async def main():
    t0 = time.time()
    print("=" * 60)
    print("Step 1: analyze_method")
    print("=" * 60)

    analysis = await analyze_method(
        class_name="JsonWriter",
        method_signature="public JsonWriter value(Number value)",
        method_code=METHOD_CODE,
        context="Available Public Methods:\n- nullValue(): JsonWriter\n- beforeValue(): void\n",
        full_class_name="com.google.gson.stream.JsonWriter",
    )
    cases = analysis["test_cases"]
    print(f"[analyze] {len(cases)} cases")
    for c in cases:
        print(f"  - {c.get('name')} :: {c.get('category')}")

    print("\n" + "=" * 60)
    print("Step 2: generate_test")
    print("=" * 60)
    result = await generate_test(
        class_name="JsonWriter",
        method_signature="public JsonWriter value(Number value)",
        method_code=METHOD_CODE,
        output_path=OUT_PATH,
        context="Available Public Methods:\n- nullValue(): JsonWriter\n- beforeValue(): void\n",
        test_class_name="JsonWriter_value_Test",
        full_class_name="com.google.gson.stream.JsonWriter",
        package_name="com.google.gson.stream",
        test_cases=cases,
    )
    print(f"[generate] {result}")
    if not result.get("success"):
        return 2

    with open(OUT_PATH, encoding="utf-8") as f:
        code = f.read()
    print(f"\n[file] {OUT_PATH} ({len(code)} chars)")
    print(f"[check] brace_ok={code.count('{') == code.count('}')} "
          f"@Test={code.count('@Test')} 耗时={time.time() - t0:.1f}s")

    print("\n" + "=" * 60)
    print("Step 3: Evaluate (compile + run + coverage)")
    print("=" * 60)
    evaluator = TestEvaluator(project_dir=PROJECT_DIR, jacoco_home=JACOCO_HOME)
    report = evaluator.evaluate(
        test_file=OUT_PATH,
        test_class="com.google.gson.stream.JsonWriter_value_Test",
        target_class="com.google.gson.stream.JsonWriter",
        target_method="value",
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
