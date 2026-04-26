"""
批量测试生成：对指定类的多个方法并发生成单元测试

使用示例：
    conda activate gp
    python core/batch_generate.py
    python core/batch_generate.py --methods nextBoolean nextDouble nextInt
    python core/batch_generate.py --concurrency 3
"""

import os
import sys
import asyncio
import json
import argparse
import re
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import CodeRAG, AgenticRAG
from llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report


# ============ 配置 ============

PROJECT_DIR = "/data/workspace/unittest/data/project/gson/gson/src/main/java"
TEST_DIR    = "/data/workspace/unittest/data/project/gson/gson/src/test/java"
MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
INDEX_PATH  = "/tmp/gson_code_rag.index"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUTPUT_DIR  = "/tmp/generated_tests"
REPORT_DIR  = "/tmp/test_reports"

SOURCE_FILE = (
    "/data/workspace/unittest/data/project/gson/gson/src/main/java"
    "/com/google/gson/stream/JsonReader.java"
)
PACKAGE     = "com.google.gson.stream"
CLASS_NAME  = "JsonReader"

# 目标方法列表（方法名 -> 源码行号）
# 优先选取逻辑复杂、分支多的方法
TARGET_METHODS = {
    "nextBoolean": 984,
    "nextString":  951,
    "nextName":    925,
    "nextDouble":  1029,
    "nextLong":    1072,
    "nextInt":     1309,
    "peek":        542,
    "skipValue":   1389,
    "nextNull":    1006,
    "hasNext":     533,
}


# ============ 工具函数 ============

def extract_method_body(source: str, start_line: int) -> str:
    """从 start_line 行开始提取完整方法体（匹配大括号）"""
    lines = source.split('\n')
    start_idx = start_line - 1
    brace_count = 0
    method_lines = []
    in_method = False
    for i in range(start_idx, min(start_idx + 300, len(lines))):
        line = lines[i]
        method_lines.append(line)
        brace_count += line.count('{') - line.count('}')
        if '{' in line:
            in_method = True
        if in_method and brace_count == 0:
            break
    return '\n'.join(method_lines)


def parse_method_signature(method_body: str) -> str:
    """从方法体第一行提取方法签名（去掉方法体）"""
    first_line = method_body.strip().split('\n')[0].strip()
    # 去掉末尾的 {
    sig = first_line.rstrip('{').strip()
    return sig


# ============ 单方法生成流程 ============

async def generate_for_method(
    method_name: str,
    start_line: int,
    source: str,
    semaphore: asyncio.Semaphore,
    evaluator: TestEvaluator,
) -> dict:
    """对单个方法执行：检索 -> 分析 -> 生成 -> 评估"""
    async with semaphore:
        print(f"\n{'='*55}")
        print(f"[{method_name}] 开始生成...")
        print(f"{'='*55}")

        method_code = extract_method_body(source, start_line)
        method_sig  = parse_method_signature(method_code)
        test_class  = f"{CLASS_NAME}_{method_name}_Test"
        full_class  = f"{PACKAGE}.{CLASS_NAME}"
        output_path = os.path.join(OUTPUT_DIR, f"{test_class}.java")

        result = {
            "method": method_name,
            "method_signature": method_sig,
            "output_path": output_path,
            "success": False,
            "compilation_success": False,
            "test_count": 0,
            "passed_count": 0,
            "line_coverage": 0.0,
            "branch_coverage": 0.0,
            "error": None,
        }

        try:
            # 步骤1：Agentic RAG 检索
            print(f"[{method_name}] → 检索上下文...")
            agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
            context = await agentic_rag.retrieve_by_agent(
                method_code,
                target_class=CLASS_NAME,
                top_k=3,
                method_signature=method_sig,
            )
            print(f"[{method_name}] ✓ 上下文长度：{len(context)} 字符")

            # 步骤2：LLM 分析方法，设计测试用例
            print(f"[{method_name}] → 分析方法，设计测试用例...")
            analysis = await analyze_method(
                class_name=CLASS_NAME,
                method_signature=method_sig,
                method_code=method_code,
                context=context,
                full_class_name=full_class,
            )
            tc_count = len(analysis["test_cases"])
            print(f"[{method_name}] ✓ 设计了 {tc_count} 个测试用例")
            for tc in analysis["test_cases"]:
                print(f"  [{tc.get('id','?')}] {tc.get('name','?')}: {tc.get('description','')}")

            # 步骤3：生成测试代码
            print(f"[{method_name}] → 生成测试代码...")
            gen = await generate_test(
                class_name=CLASS_NAME,
                method_signature=method_sig,
                method_code=method_code,
                output_path=output_path,
                context=context,
                test_class_name=test_class,
                full_class_name=full_class,
                test_cases=analysis["test_cases"],
            )
            if not gen["success"]:
                result["error"] = gen.get("error", "生成失败")
                print(f"[{method_name}] ✗ 生成失败：{result['error']}")
                return result

            print(f"[{method_name}] ✓ 测试文件：{output_path}")

            # 步骤4：评估（编译 + 运行 + 覆盖率）
            print(f"[{method_name}] → 评估测试...")
            report = evaluator.evaluate(
                test_file=output_path,
                test_class=f"{PACKAGE}.{test_class}",
                target_class=full_class,
                target_method=method_name,
            )

            result["success"]             = True
            result["compilation_success"] = report.compilation_success
            result["test_count"]          = len(report.test_results)
            result["passed_count"]        = sum(1 for t in report.test_results if t.passed)
            if report.coverage:
                result["line_coverage"]   = report.coverage.line_coverage
                result["branch_coverage"] = report.coverage.branch_coverage

            status = "✓" if report.compilation_success else "✗"
            print(
                f"[{method_name}] {status} 编译{'成功' if report.compilation_success else '失败'} | "
                f"测试 {result['passed_count']}/{result['test_count']} 通过 | "
                f"行覆盖率 {result['line_coverage']:.1f}% | "
                f"分支覆盖率 {result['branch_coverage']:.1f}%"
            )

        except Exception as e:
            result["error"] = str(e)
            print(f"[{method_name}] ✗ 异常：{e}")

        return result


# ============ 批量主流程 ============

async def batch_pipeline(methods: list[str], concurrency: int = 2):
    """批量生成：并发度由 concurrency 控制"""
    print("\n" + "=" * 60)
    print(f"批量测试生成：{CLASS_NAME} 共 {len(methods)} 个方法")
    print(f"并发度：{concurrency}")
    print("=" * 60 + "\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    # 确保索引存在
    if not os.path.exists(INDEX_PATH):
        print("构建代码索引...")
        rag = CodeRAG()
        rag.build_index(PROJECT_DIR, INDEX_PATH, batch_size=50)
        print(f"✓ 索引构建完成：{INDEX_PATH}\n")
    else:
        print(f"✓ 索引已存在：{INDEX_PATH}\n")

    # 读取源文件
    with open(SOURCE_FILE, encoding='utf-8') as f:
        source = f.read()

    # 创建评估器（共享）
    evaluator = TestEvaluator(project_dir=MAVEN_PROJECT_DIR, jacoco_home=JACOCO_HOME)

    # 获取基准覆盖率
    print("获取基准覆盖率...")
    baseline = evaluator.get_baseline_coverage(
        target_class=f"{PACKAGE}.{CLASS_NAME}"
    )
    if baseline:
        print(f"✓ 基准：行 {baseline.line_coverage:.1f}%，分支 {baseline.branch_coverage:.1f}%\n")
    else:
        print("! 无法获取基准覆盖率\n")

    # 并发生成
    semaphore = asyncio.Semaphore(concurrency)
    tasks = []
    for method_name in methods:
        if method_name not in TARGET_METHODS:
            print(f"! 跳过未知方法：{method_name}")
            continue
        start_line = TARGET_METHODS[method_name]
        tasks.append(
            generate_for_method(method_name, start_line, source, semaphore, evaluator)
        )

    results = await asyncio.gather(*tasks, return_exceptions=False)

    # 汇总报告
    print("\n" + "=" * 60)
    print("批量生成完成 — 汇总报告")
    print("=" * 60)
    print(f"{'方法':<18} {'编译':^6} {'通过/总数':^10} {'行覆盖率':^10} {'分支覆盖率':^10}")
    print("-" * 60)

    summary = []
    for r in results:
        compile_ok = "✓" if r["compilation_success"] else "✗"
        tests_str  = f"{r['passed_count']}/{r['test_count']}"
        line_cov   = f"{r['line_coverage']:.1f}%"
        branch_cov = f"{r['branch_coverage']:.1f}%"
        print(f"{r['method']:<18} {compile_ok:^6} {tests_str:^10} {line_cov:^10} {branch_cov:^10}")
        summary.append(r)

    # 保存汇总 JSON
    report_path = os.path.join(REPORT_DIR, "batch_report.json")
    report_data = {
        "timestamp": datetime.now().isoformat(),
        "class": f"{PACKAGE}.{CLASS_NAME}",
        "baseline_coverage": {
            "line_coverage":   baseline.line_coverage   if baseline else None,
            "branch_coverage": baseline.branch_coverage if baseline else None,
        },
        "results": summary,
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"\n✓ 汇总报告已保存：{report_path}")

    return summary


# ============ 主程序 ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量生成单元测试")
    parser.add_argument(
        "--methods", nargs="+",
        default=list(TARGET_METHODS.keys()),
        help="要生成测试的方法名列表，默认全部"
    )
    parser.add_argument(
        "--concurrency", type=int, default=2,
        help="并发度（同时处理几个方法），默认 2"
    )
    args = parser.parse_args()

    asyncio.run(batch_pipeline(args.methods, args.concurrency))
