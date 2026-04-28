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
                                  agentic_rag: AgenticRAG = None,
                                  cached_baseline=None) -> dict:
    """Run the full pipeline for a single target method.
    
    Args:
        target: 目标方法配置
        evaluator: 评估器实例
        agentic_rag: 共享的RAG实例
        cached_baseline: 缓存的基准覆盖率（同类方法共享，避免重复Maven运行）
    """
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

    # ── 优化: 使用缓存的基准覆盖率 ──────────────────────────────────
    baseline_coverage = cached_baseline

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

        # Step 2: Analyze & design test cases (1次LLM调用，合并了3个Phase)
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
        test_cases_to_use = analysis["test_cases"]
        gen_result = await generate_test(
            class_name=class_name,
            method_signature=sig,
            method_code=code,
            output_path=output_path,
            context=context,
            test_class_name=test_class_name,
            full_class_name=full_class,
            test_cases=test_cases_to_use,
        )
        # ★ 截断重试：LLM 输出被 token 上限截断时，减少 test_cases 数量重试一次
        if not gen_result.get("success") and gen_result.get("truncated"):
            reduced = test_cases_to_use[:max(3, len(test_cases_to_use) // 2)]
            print(f"  [GEN] Output truncated, retrying with {len(reduced)} test cases (was {len(test_cases_to_use)})...")
            gen_result = await generate_test(
                class_name=class_name,
                method_signature=sig,
                method_code=code,
                output_path=output_path,
                context=context,
                test_class_name=test_class_name,
                full_class_name=full_class,
                test_cases=reduced,
            )
        if not gen_result["success"]:
            result["error"] = f"Generation failed: {gen_result.get('error', '?')}"
            # 即使生成失败，也记录基准覆盖率
            if baseline_coverage:
                result["line_coverage_before"] = baseline_coverage.line_coverage
                result["branch_coverage_before"] = baseline_coverage.branch_coverage
                mc_base = baseline_coverage.get_method_coverage(method_name)
                if mc_base:
                    result["method_line_before"] = mc_base.line_coverage
                    result["method_branch_before"] = mc_base.branch_coverage
            return result

        # Step 4: Evaluate (compile + run, reuse cached baseline)
        print(f"  [EVAL] Evaluating...")
        
        # 如果有缓存的基准覆盖率，使用简化评估流程
        if baseline_coverage is not None:
            report = _evaluate_with_cached_baseline(
                evaluator, output_path, test_fqn, full_class,
                method_name, baseline_coverage
            )
        else:
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
            evaluator._cleanup_old_generated_tests()
            evaluator._copy_test_file(output_path, test_fqn)
            _, compile_output = evaluator._compile_test_with_output(
                evaluator._actual_test_class or test_fqn
            )

            # Define a compile function for the fix loop
            def try_compile(fixed_code: str) -> tuple:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(fixed_code)
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
                agentic_rag=agentic_rag,
                target_class=full_class,
                method_signature=sig,
            )

            for log_entry in fix_log:
                print(f"    {log_entry}")

            if fix_success:
                # ── 优化: FixLoop成功后，直接跑测试+覆盖率，不再重跑baseline ──
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(fixed_code)
                print(f"  [FIX] Fix succeeded! Running tests (skipping baseline re-run)...")
                report = _evaluate_with_cached_baseline(
                    evaluator, output_path, test_fqn, full_class,
                    method_name, baseline_coverage
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


def _evaluate_with_cached_baseline(
    evaluator: TestEvaluator,
    test_file: str,
    test_class: str,
    target_class: str,
    target_method: str,
    cached_baseline,
) -> object:
    """使用缓存的基准覆盖率进行评估，跳过 get_baseline_coverage 的 Maven 运行。
    
    与 evaluator.evaluate() 的区别：
    - 跳过 Step 1 (get_baseline_coverage)，直接使用缓存
    - 其余步骤相同：复制文件 → 编译 → 运行测试 → 评估覆盖率
    """
    from evaluation.evaluator import EvaluationReport
    errors = []
    
    # 0. 清理残留
    print(f"[→] 清理残留的 Generated 测试文件...")
    evaluator._cleanup_old_generated_tests()

    # 1. 使用缓存的基准覆盖率（跳过Maven运行）
    baseline_coverage = cached_baseline
    if baseline_coverage:
        print(f"  ✓ 使用缓存的基准覆盖率: 行 {baseline_coverage.line_coverage:.1f}%, "
              f"分支 {baseline_coverage.branch_coverage:.1f}%")
    else:
        print(f"  ✗ 无缓存的基准覆盖率")

    # 2. 复制测试文件到项目
    print(f"\n[→] 复制测试文件: {test_file}")
    copy_success = evaluator._copy_test_file(test_file, test_class)
    if not copy_success:
        errors.append("复制测试文件失败")
        return EvaluationReport(
            test_file=test_file,
            target_class=target_class,
            target_method=target_method,
            test_results=[],
            coverage=None,
            baseline_coverage=baseline_coverage,
            compilation_success=False,
            errors=errors
        )
    
    actual_test_class = evaluator._actual_test_class or test_class

    # 3. 编译测试
    print(f"[→] 编译测试: {actual_test_class}")
    compile_success = evaluator._compile_test(actual_test_class)
    if not compile_success:
        errors.append("编译失败")
        return EvaluationReport(
            test_file=test_file,
            target_class=target_class,
            target_method=target_method,
            test_results=[],
            coverage=None,
            baseline_coverage=baseline_coverage,
            compilation_success=False,
            errors=errors
        )

    # 4. 运行测试
    class_name = target_class.split(".")[-1]
    baseline_test_class = class_name + "Test"
    baseline_full_class = ".".join(target_class.split(".")[:-1] + [baseline_test_class])
    if "." in test_class:
        baseline_full_class = ".".join(test_class.split(".")[:-1] + [baseline_test_class])
    test_classes_to_run = [baseline_full_class, actual_test_class]
    print(f"[→] 运行测试: {test_classes_to_run}")
    test_results = evaluator._run_test(test_classes_to_run)
    
    # 5. 评估覆盖率
    print(f"[→] 评估覆盖率: {target_class}")
    coverage = evaluator._get_coverage_from_exec(evaluator.exec_file, target_class)
    
    # 6. 对比覆盖率变化
    coverage_change = None
    if baseline_coverage and coverage:
        coverage_change = evaluator.compare_coverage(baseline_coverage, coverage)
        print(f"\n[→] 覆盖率变化:")
        print(f"  行覆盖率: {baseline_coverage.line_coverage:.1f}% → {coverage.line_coverage:.1f}% ({coverage_change['line_coverage_change']:+.1f}%)")
        print(f"  分支覆盖率: {baseline_coverage.branch_coverage:.1f}% → {coverage.branch_coverage:.1f}% ({coverage_change['branch_coverage_change']:+.1f}%)")
        print(f"  覆盖行数: {baseline_coverage.covered_lines} → {coverage.covered_lines} ({coverage_change['line_change']:+d})")
    
    return EvaluationReport(
        test_file=test_file,
        target_class=target_class,
        target_method=target_method,
        test_results=test_results,
        coverage=coverage,
        baseline_coverage=baseline_coverage,
        coverage_change=coverage_change,
        compilation_success=True,
        errors=errors
    )


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

    # ── 支持通过 ONLY_TARGETS 环境变量过滤要跑的方法 ──
    # 例如: ONLY_TARGETS="Gson.fromJson,JsonReader.peek"
    only = os.environ.get("ONLY_TARGETS", "").strip()
    global TARGETS
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        filtered = []
        for t in TARGETS:
            cls_short = t["full_class_name"].split(".")[-1]
            key = f"{cls_short}.{t['method_name']}"
            if key in wanted:
                filtered.append(t)
        if not filtered:
            print(f"[!] ONLY_TARGETS={only!r} 未匹配到任何目标，退出")
            return
        TARGETS = filtered
        _names = [f"{t['full_class_name'].split('.')[-1]}.{t['method_name']}" for t in TARGETS]
        print(f"[→] ONLY_TARGETS 过滤后: {_names}")

    print("=" * 60)
    print("跨类批量测试：验证 pipeline 通用性")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: {len(TARGETS)} 个方法，来自 {len(set(t['full_class_name'] for t in TARGETS))} 个类")
    print("=" * 60)

    evaluator = TestEvaluator(project_dir=MAVEN_PROJECT_DIR, jacoco_home=JACOCO_HOME)

    # ★ 启动前先清理项目里所有 *Generated*.java 残留文件，
    # 避免上次中断的 pipeline 遗留的坏测试让基准 Maven 直接失败。
    print(f"[→] 启动前清理上次残留的 Generated 测试文件...")
    evaluator._cleanup_old_generated_tests()

    # Pre-load index once for all methods
    print(f"[→] Loading code index...")
    shared_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
    print(f"[→] Index loaded.\n")

    # ── 优化1: 按类分组，同类方法共享基准覆盖率 ──────────────────────
    from collections import OrderedDict
    class_groups = OrderedDict()
    for target in TARGETS:
        key = (target["full_class_name"], target.get("baseline_test", ""))
        class_groups.setdefault(key, []).append(target)

    # 基准覆盖率缓存: full_class_name -> CoverageReport
    baseline_cache = {}

    results = []
    global_idx = 0
    for (full_class, baseline_test), targets in class_groups.items():
        class_name = full_class.split(".")[-1]
        print(f"\n{'#'*60}")
        print(f"## 类: {full_class} ({len(targets)} 个方法)")
        print(f"{'#'*60}")

        # 同类方法只获取一次基准覆盖率
        if full_class not in baseline_cache:
            print(f"[→] 获取基准覆盖率: {class_name} (只跑一次Maven)")
            bl_test = targets[0].get("baseline_test")
            baseline_cache[full_class] = evaluator.get_baseline_coverage(full_class, bl_test)
            if baseline_cache[full_class]:
                print(f"  ✓ 基准覆盖率已缓存: 行 {baseline_cache[full_class].line_coverage:.1f}%, "
                      f"分支 {baseline_cache[full_class].branch_coverage:.1f}%")
            else:
                print(f"  ✗ 基准覆盖率获取失败")
        else:
            print(f"[→] 使用缓存的基准覆盖率 (跳过Maven)")

        for target in targets:
            global_idx += 1
            cls_short = target["full_class_name"].split(".")[-1]
            print(f"\n{'='*60}")
            print(f"[{global_idx}/{len(TARGETS)}] {cls_short}.{target['method_name']}")
            print(f"  {target.get('description', '')}")
            print(f"{'='*60}")

            r = await run_pipeline_for_target(
                target, evaluator, agentic_rag=shared_rag,
                cached_baseline=baseline_cache.get(full_class),
            )
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
