"""
Batch test: find low-coverage methods and generate tests for each.

Usage:
    python core/batch_test.py
"""

import os
import sys
import asyncio
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import AgenticRAG
from llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report

# ============ Config ============

PROJECT_DIR = "/data/workspace/unittest/data/project/gson/gson/src/main/java"
TEST_DIR = "/data/workspace/unittest/data/project/gson/gson/src/test/java"
MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
INDEX_PATH = "/tmp/gson_code_rag.index"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUTPUT_DIR = "/tmp/generated_tests"
REPORT_DIR = "/tmp/test_reports"

TARGET_CLASS = "com.google.gson.stream.JsonReader"
SOURCE_FILE = os.path.join(PROJECT_DIR, "com/google/gson/stream/JsonReader.java")

# ============ Helpers ============

def extract_method_source(filepath: str, method_name: str) -> tuple:
    """Extract a method's source code and signature from a Java file.

    Returns (signature, code) or (None, None) if not found.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match method declaration containing the target name
        if method_name + '(' in stripped and any(
            stripped.startswith(mod) for mod in ('public', 'private', 'protected', '@')
        ):
            # Handle annotations: skip to actual declaration
            start = i
            if stripped.startswith('@'):
                for j in range(i + 1, min(i + 5, len(lines))):
                    if any(lines[j].strip().startswith(m) for m in ('public', 'private', 'protected')):
                        start = j
                        stripped = lines[j].strip()
                        break

            # Only match public methods (testable)
            if not stripped.startswith('public'):
                continue

            # Find method end by brace counting
            brace_depth = 0
            end = start
            for j in range(start, len(lines)):
                brace_depth += lines[j].count('{') - lines[j].count('}')
                if brace_depth == 0 and j > start:
                    end = j
                    break

            code = ''.join(lines[start:end + 1])
            sig = stripped.split('{')[0].strip()
            return sig, code

    return None, None


async def run_pipeline_for_method(
    method_name: str,
    method_signature: str,
    method_code: str,
    evaluator: TestEvaluator,
) -> dict:
    """Run the full pipeline for a single method and return results."""
    class_name = TARGET_CLASS.split('.')[-1]
    test_class_name = f"{class_name}_{method_name}_Test"
    output_path = os.path.join(OUTPUT_DIR, f"{test_class_name}.java")

    result = {
        "method": method_name,
        "signature": method_signature,
        "compile_success": False,
        "test_count": 0,
        "line_coverage_before": None,
        "line_coverage_after": None,
        "branch_coverage_before": None,
        "branch_coverage_after": None,
        "method_line_before": None,
        "method_line_after": None,
        "method_branch_before": None,
        "method_branch_after": None,
        "error": None,
    }

    try:
        # Step 1: Agentic RAG retrieval
        print(f"\n  [RAG] Retrieving context...")
        agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
        context = await agentic_rag.retrieve(
            method_code,
            target_class=class_name,
            top_k=3,
            method_signature=method_signature,
        )
        print(f"  [RAG] Context: {len(context)} chars")

        # Step 2: Analyze method & design test cases
        print(f"  [LLM] Analyzing method...")
        analysis = await analyze_method(
            class_name=class_name,
            method_signature=method_signature,
            method_code=method_code,
            context=context,
            full_class_name=TARGET_CLASS,
        )
        result["test_count"] = len(analysis["test_cases"])
        print(f"  [LLM] Designed {result['test_count']} test cases")

        # Step 3: Generate test code
        print(f"  [GEN] Generating test...")
        gen_result = await generate_test(
            class_name=class_name,
            method_signature=method_signature,
            method_code=method_code,
            output_path=output_path,
            context=context,
            test_class_name=test_class_name,
            full_class_name=TARGET_CLASS,
            test_cases=analysis["test_cases"],
        )
        if not gen_result["success"]:
            result["error"] = f"Generation failed: {gen_result.get('error', '?')}"
            return result

        # Step 4: Evaluate
        print(f"  [EVAL] Evaluating...")
        report = evaluator.evaluate(
            test_file=output_path,
            test_class=f"com.google.gson.stream.{test_class_name}",
            target_class=TARGET_CLASS,
            target_method=method_name,
        )

        result["compile_success"] = report.compilation_success

        if report.compilation_success and report.coverage:
            result["line_coverage_after"] = report.coverage.line_coverage
            result["branch_coverage_after"] = report.coverage.branch_coverage

            # Method-level coverage
            mc = report.coverage.get_method_coverage(method_name)
            if mc:
                result["method_line_after"] = mc.line_coverage
                result["method_branch_after"] = mc.branch_coverage

        if report.baseline_coverage:
            result["line_coverage_before"] = report.baseline_coverage.line_coverage
            result["branch_coverage_before"] = report.baseline_coverage.branch_coverage

            mc_base = report.baseline_coverage.get_method_coverage(method_name)
            if mc_base:
                result["method_line_before"] = mc_base.line_coverage
                result["method_branch_before"] = mc_base.branch_coverage

        if report.errors:
            result["error"] = "; ".join(report.errors)

        # Print detailed report
        print_report(report)

    except Exception as e:
        result["error"] = str(e)
        import traceback
        traceback.print_exc()

    return result


def print_summary(results: list):
    """Print a summary table of all method results."""
    print("\n" + "=" * 100)
    print("批量测试总结")
    print("=" * 100)

    header = f"{'Method':<25s} {'Compile':>8s} {'Tests':>6s} {'Class Line':>18s} {'Class Branch':>18s} {'Method Line':>18s} {'Method Branch':>18s}"
    print(header)
    print("-" * 100)

    for r in results:
        compile_str = "✓" if r["compile_success"] else "✗"
        tests_str = str(r["test_count"])

        def fmt_change(before, after):
            if before is None or after is None:
                return "N/A"
            diff = after - before
            return f"{before:.1f}→{after:.1f}({diff:+.1f})"

        cl = fmt_change(r["line_coverage_before"], r["line_coverage_after"])
        cb = fmt_change(r["branch_coverage_before"], r["branch_coverage_after"])
        ml = fmt_change(r["method_line_before"], r["method_line_after"])
        mb = fmt_change(r["method_branch_before"], r["method_branch_after"])

        print(f"{r['method']:<25s} {compile_str:>8s} {tests_str:>6s} {cl:>18s} {cb:>18s} {ml:>18s} {mb:>18s}")

        if r["error"] and not r["compile_success"]:
            print(f"  └─ Error: {r['error'][:80]}")

    print("=" * 100)

    # Stats
    total = len(results)
    compiled = sum(1 for r in results if r["compile_success"])
    print(f"\n编译成功: {compiled}/{total}")


# ============ Main ============

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    print("=" * 60)
    print("批量测试：自动发现低覆盖率方法并生成测试")
    print("=" * 60)

    # Step 0: Get baseline coverage and find low-coverage methods
    evaluator = TestEvaluator(project_dir=MAVEN_PROJECT_DIR, jacoco_home=JACOCO_HOME)
    baseline = evaluator.get_baseline_coverage(TARGET_CLASS)
    if not baseline:
        print("✗ 无法获取基准覆盖率")
        return

    low_methods = evaluator.find_low_coverage_methods(baseline, threshold=95.0)
    print(f"\n低覆盖率方法 (< 95%):")
    for mc in low_methods:
        print(f"  {mc.method_name:<35s} line={mc.covered_lines}/{mc.total_lines} ({mc.line_coverage:.0f}%)")

    # Filter to public methods only (we can only test public methods directly)
    # Extract source and check if public
    testable_methods = []
    for mc in low_methods:
        sig, code = extract_method_source(SOURCE_FILE, mc.method_name)
        if sig and code:
            testable_methods.append({
                "name": mc.method_name,
                "signature": sig,
                "code": code,
                "baseline_line": mc.line_coverage,
                "baseline_branch": mc.branch_coverage,
            })
        else:
            print(f"  ⊘ {mc.method_name}: private/protected, skipping")

    if not testable_methods:
        print("\n✗ 没有可测试的低覆盖率公共方法")
        return

    print(f"\n可测试的公共方法: {len(testable_methods)}")
    for m in testable_methods:
        print(f"  → {m['name']} ({m['signature'][:60]}...)")

    # Run pipeline for each method
    results = []
    for i, method in enumerate(testable_methods):
        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(testable_methods)}] 测试方法: {method['name']}")
        print(f"  签名: {method['signature']}")
        print(f"  基准覆盖率: line={method['baseline_line']:.0f}%, branch={method['baseline_branch']:.0f}%")
        print(f"{'='*60}")

        r = await run_pipeline_for_method(
            method_name=method["name"],
            method_signature=method["signature"],
            method_code=method["code"],
            evaluator=evaluator,
        )
        results.append(r)

    # Print summary
    print_summary(results)

    # Save results
    report_path = os.path.join(REPORT_DIR, "batch_report.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "target_class": TARGET_CLASS,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
