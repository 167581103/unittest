"""
Cross-class batch test: test methods from different classes to validate
that the pipeline generalises beyond JsonReader.

Usage:
    python core/batch_test_cross_class.py
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
from core.fix_loop import fix_compile_errors, parse_compile_errors, classify_errors, rule_fix

# ============ Config ============

PROJECT_DIR = "/data/workspace/unittest/data/project/gson/gson/src/main/java"
TEST_DIR = "/data/workspace/unittest/data/project/gson/gson/src/test/java"
MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
INDEX_PATH = "/tmp/gson_code_rag.index"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUTPUT_DIR = "/tmp/generated_tests"
REPORT_DIR = "/tmp/test_reports"

# ============ Target methods ============
# Each entry: (full_class_name, method_name, baseline_test_class, start_line)
# start_line is used to disambiguate overloaded methods (0 = first match)

TARGETS = [
    # ── Gson (complex class, 1265 lines) ──
    {
        "full_class_name": "com.google.gson.Gson",
        "method_name": "fromJson",
        "baseline_test": "com.google.gson.GsonTest",
        "start_line": 1064,  # 50L, 64%
        "description": "fromJson(JsonReader, TypeToken<T>) - core deserialization",
    },
    {
        "full_class_name": "com.google.gson.Gson",
        "method_name": "toJson",
        "baseline_test": "com.google.gson.GsonTest",
        "start_line": 647,  # 30L, 0-75%
        "description": "toJson(Object, Type, JsonWriter) - core serialization",
    },
    {
        "full_class_name": "com.google.gson.Gson",
        "method_name": "getDelegateAdapter",
        "baseline_test": "com.google.gson.GsonTest",
        "start_line": 451,  # 30L, 88%
        "description": "getDelegateAdapter(TypeAdapterFactory, TypeToken) - adapter delegation",
    },
    {
        "full_class_name": "com.google.gson.Gson",
        "method_name": "getAdapter",
        "baseline_test": "com.google.gson.GsonTest",
        "start_line": 319,  # 60L, 91%
        "description": "getAdapter(TypeToken<T>) - type adapter lookup with caching",
    },
    # ── JsonReader (complex class, 1800+ lines) ──
    {
        "full_class_name": "com.google.gson.stream.JsonReader",
        "method_name": "nextLong",
        "baseline_test": "com.google.gson.stream.JsonReaderTest",
        "start_line": 1072,  # 44L, 65%
        "description": "nextLong() - read next JSON number as long",
    },
    {
        "full_class_name": "com.google.gson.stream.JsonReader",
        "method_name": "peek",
        "baseline_test": "com.google.gson.stream.JsonReaderTest",
        "start_line": 542,  # 38L, 87%
        "description": "peek() - look at next token type without consuming",
    },
    {
        "full_class_name": "com.google.gson.stream.JsonReader",
        "method_name": "nextDouble",
        "baseline_test": "com.google.gson.stream.JsonReaderTest",
        "start_line": 1029,  # 33L, 92%
        "description": "nextDouble() - read next JSON number as double",
    },
    # ── JsonWriter (836 lines) ──
    {
        "full_class_name": "com.google.gson.stream.JsonWriter",
        "method_name": "setFormattingStyle",
        "baseline_test": "com.google.gson.stream.JsonWriterTest",
        "start_line": 266,  # 18L, 82%
        "description": "setFormattingStyle(FormattingStyle) - configure output formatting",
    },
    {
        "full_class_name": "com.google.gson.stream.JsonWriter",
        "method_name": "nullValue",
        "baseline_test": "com.google.gson.stream.JsonWriterTest",
        "start_line": 667,  # 13L, 78%
        "description": "nullValue() - write JSON null",
    },
    {
        "full_class_name": "com.google.gson.stream.JsonWriter",
        "method_name": "value",
        "baseline_test": "com.google.gson.stream.JsonWriterTest",
        "start_line": 633,  # 27L, 94%
        "description": "value(Number) - write a numeric JSON value",
    },
    # ── JsonPrimitive (328 lines) ──
    {
        "full_class_name": "com.google.gson.JsonPrimitive",
        "method_name": "equals",
        "baseline_test": "com.google.gson.JsonPrimitiveTest",
        "start_line": 282,  # 30L, 89%
        "description": "equals(Object) - value equality with type coercion",
    },
    {
        "full_class_name": "com.google.gson.JsonPrimitive",
        "method_name": "hashCode",
        "baseline_test": "com.google.gson.JsonPrimitiveTest",
        "start_line": 261,  # 15L, 89%
        "description": "hashCode() - hash code with numeric normalization",
    },
]


# ============ Helpers ============

def extract_method_at_line(filepath: str, target_line: int) -> tuple:
    """Extract a method starting at or near target_line.

    Returns (signature, code, actual_start_line) or (None, None, 0).
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Search within ±5 lines of target
    for offset in range(0, 10):
        for direction in [0, -1, 1]:
            idx = target_line - 1 + direction * offset  # 0-based
            if idx < 0 or idx >= len(lines):
                continue
            stripped = lines[idx].strip()
            if stripped.startswith('public') and '(' in stripped:
                start = idx
                brace_depth = 0
                end = start
                for j in range(start, len(lines)):
                    brace_depth += lines[j].count('{') - lines[j].count('}')
                    if brace_depth == 0 and j > start:
                        end = j
                        break
                code = ''.join(lines[start:end + 1])
                sig = stripped.split('{')[0].strip()
                return sig, code, start + 1
    return None, None, 0


def class_to_source_path(full_class_name: str) -> str:
    """Convert a fully qualified class name to a source file path."""
    return os.path.join(PROJECT_DIR, full_class_name.replace('.', '/') + '.java')


def class_to_package(full_class_name: str) -> str:
    """Extract package name from fully qualified class name."""
    return '.'.join(full_class_name.split('.')[:-1])


async def run_pipeline_for_target(target: dict, evaluator: TestEvaluator,
                                  agentic_rag: AgenticRAG = None) -> dict:
    """Run the full pipeline for a single target method."""
    full_class = target["full_class_name"]
    class_name = full_class.split('.')[-1]
    method_name = target["method_name"]
    package_name = class_to_package(full_class)

    # Use a unique test class name (include start_line to disambiguate overloads)
    test_class_name = f"{class_name}_{method_name}_{target['start_line']}_Test"
    test_fqn = f"{package_name}.{test_class_name}"
    output_path = os.path.join(OUTPUT_DIR, f"{test_class_name}.java")

    # Extract source code
    source_file = class_to_source_path(full_class)
    sig, code, actual_line = extract_method_at_line(source_file, target["start_line"])

    result = {
        "class": full_class,
        "method": method_name,
        "description": target.get("description", ""),
        "signature": sig or "?",
        "lines": len(code.split('\n')) if code else 0,
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

    if not sig or not code:
        result["error"] = f"Could not extract method at line {target['start_line']}"
        return result

    print(f"  Source: {source_file}:{actual_line} ({result['lines']} lines)")

    try:
        # Step 1: Agentic RAG
        print(f"  [RAG] Retrieving context...")
        if agentic_rag is None:
            agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
        context = await agentic_rag.retrieve(
            code,
            target_class=class_name,
            top_k=3,
            method_signature=sig,
        )
        print(f"  [RAG] Context: {len(context)} chars")

        # Step 2: Analyze & design test cases
        print(f"  [LLM] Analyzing method...")
        analysis = await analyze_method(
            class_name=class_name,
            method_signature=sig,
            method_code=code,
            context=context,
            full_class_name=full_class,
        )
        result["test_count"] = len(analysis["test_cases"])
        print(f"  [LLM] Designed {result['test_count']} test cases")

        # Step 3: Generate test code
        print(f"  [GEN] Generating test...")
        gen_result = await generate_test(
            class_name=class_name,
            method_signature=sig,
            method_code=code,
            output_path=output_path,
            context=context,
            test_class_name=test_class_name,
            full_class_name=full_class,
            test_cases=analysis["test_cases"],
        )
        if not gen_result["success"]:
            result["error"] = f"Generation failed: {gen_result.get('error', '?')}"
            return result

        # Step 4: Evaluate (with fix loop on compile failure)
        print(f"  [EVAL] Evaluating...")
        report = evaluator.evaluate(
            test_file=output_path,
            test_class=test_fqn,
            target_class=full_class,
            target_method=method_name,
            baseline_test=target.get("baseline_test"),
        )

        # Step 4b: Fix Loop if compilation failed
        if not report.compilation_success:
            print(f"  [FIX] Compilation failed, attempting fix loop...")
            with open(output_path, 'r', encoding='utf-8') as f:
                current_code = f.read()

            # Try to get compile output by re-compiling
            # We need to copy the file first and compile
            evaluator._cleanup_old_generated_tests()
            evaluator._copy_test_file(output_path, test_fqn)
            _, compile_output = evaluator._compile_test_with_output(
                evaluator._actual_test_class or test_fqn
            )

            # Define a compile function for the fix loop
            def try_compile(fixed_code: str) -> tuple:
                # Write fixed code back
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(fixed_code)
                # Clean and re-copy
                evaluator._cleanup_old_generated_tests()
                evaluator._copy_test_file(output_path, test_fqn)
                actual_cls = evaluator._actual_test_class or test_fqn
                return evaluator._compile_test_with_output(actual_cls)

            fixed_code, fix_success, fix_log = await fix_compile_errors(
                code=current_code,
                compile_output=compile_output,
                context=context,
                max_retries=3,
                compile_fn=try_compile,
                code_rag=agentic_rag.rag if hasattr(agentic_rag, 'rag') else None,
            )

            for log_entry in fix_log:
                print(f"    {log_entry}")

            if fix_success:
                # Write the fixed code and re-evaluate fully
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(fixed_code)
                print(f"  [FIX] Fix succeeded! Re-evaluating...")
                report = evaluator.evaluate(
                    test_file=output_path,
                    test_class=test_fqn,
                    target_class=full_class,
                    target_method=method_name,
                    baseline_test=target.get("baseline_test"),
                )

        result["compile_success"] = report.compilation_success

        if report.compilation_success and report.coverage:
            result["line_coverage_after"] = report.coverage.line_coverage
            result["branch_coverage_after"] = report.coverage.branch_coverage
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
            result["error"] = "; ".join(report.errors[:3])

        print_report(report)

    except Exception as e:
        result["error"] = str(e)
        import traceback
        traceback.print_exc()

    return result


def print_summary(results: list):
    """Print a summary table."""
    print("\n" + "=" * 120)
    print("跨类批量测试总结")
    print("=" * 120)

    header = (f"{'Class':<18s} {'Method':<22s} {'Lines':>5s} {'OK':>4s} "
              f"{'Tests':>5s} {'Class Line':>18s} {'Class Branch':>18s} "
              f"{'Method Line':>20s} {'Method Branch':>20s}")
    print(header)
    print("-" * 120)

    for r in results:
        cls_short = r["class"].split(".")[-1]
        ok = "✓" if r["compile_success"] else "✗"

        def fmt(before, after):
            if before is None or after is None:
                return "N/A"
            diff = after - before
            return f"{before:.1f}→{after:.1f}({diff:+.1f})"

        cl = fmt(r["line_coverage_before"], r["line_coverage_after"])
        cb = fmt(r["branch_coverage_before"], r["branch_coverage_after"])
        ml = fmt(r["method_line_before"], r["method_line_after"])
        mb = fmt(r["method_branch_before"], r["method_branch_after"])

        print(f"{cls_short:<18s} {r['method']:<22s} {r['lines']:>5d} {ok:>4s} "
              f"{r['test_count']:>5d} {cl:>18s} {cb:>18s} {ml:>20s} {mb:>20s}")

        if r["error"] and not r["compile_success"]:
            print(f"  └─ {r['error'][:100]}")

    print("=" * 120)
    total = len(results)
    compiled = sum(1 for r in results if r["compile_success"])
    print(f"\n编译成功: {compiled}/{total} ({compiled/total*100:.0f}%)")


# ============ Main ============

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    print("=" * 60)
    print("跨类批量测试：验证 pipeline 通用性")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: {len(TARGETS)} 个方法，来自 {len(set(t['full_class_name'] for t in TARGETS))} 个类")
    print("=" * 60)

    evaluator = TestEvaluator(project_dir=MAVEN_PROJECT_DIR, jacoco_home=JACOCO_HOME)

    # Pre-load index once for all methods
    print(f"[→] Loading code index...")
    shared_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
    print(f"[→] Index loaded.\n")

    results = []
    for i, target in enumerate(TARGETS):
        cls_short = target["full_class_name"].split(".")[-1]
        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(TARGETS)}] {cls_short}.{target['method_name']}")
        print(f"  {target.get('description', '')}")
        print(f"{'='*60}")

        r = await run_pipeline_for_target(target, evaluator, agentic_rag=shared_rag)
        results.append(r)

    print_summary(results)

    # Save
    report_path = os.path.join(REPORT_DIR, "cross_class_report.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
