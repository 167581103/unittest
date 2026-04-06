"""
完整流程：嵌入 -> Agentic检索 -> 生成 -> 评估 -> 输出报告

使用示例：
    conda activate gp
    python core/generate_test_pipeline.py
"""

import os
import sys
import asyncio
import json
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import CodeRAG, AgenticRAG
from llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report


# ============ 配置 ============

PROJECT_DIR = "/data/workspace/unittest/data/project/gson/gson/src/main/java"
TEST_DIR = "/data/workspace/unittest/data/project/gson/gson/src/test/java"
MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
INDEX_PATH = "/tmp/gson_code_rag.index"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUTPUT_DIR = "/tmp/generated_tests"
REPORT_DIR = "/tmp/test_reports"


# ============ 目标方法 ============

TARGET_METHOD = '''public long nextLong() throws IOException {
    int p = peeked;
    if (p == PEEKED_NONE) {
      p = doPeek();
    }

    if (p == PEEKED_LONG) {
      peeked = PEEKED_NONE;
      pathIndices[stackSize - 1]++;
      return peekedLong;
    }

    if (p == PEEKED_NUMBER) {
      peekedString = new String(buffer, pos, peekedNumberLength);
      pos += peekedNumberLength;
    } else if (p == PEEKED_SINGLE_QUOTED || p == PEEKED_DOUBLE_QUOTED || p == PEEKED_UNQUOTED) {
      if (p == PEEKED_UNQUOTED) {
        peekedString = nextUnquotedValue();
      } else {
        peekedString = nextQuotedValue(p == PEEKED_SINGLE_QUOTED ? '\\'' : '"');
      }
      try {
        long result = Long.parseLong(peekedString);
        peeked = PEEKED_NONE;
        pathIndices[stackSize - 1]++;
        return result;
      } catch (NumberFormatException ignored) {
        // Fall back to parse as a double below.
      }
    } else {
      throw unexpectedTokenError("a long");
    }

    peeked = PEEKED_BUFFERED;
    double asDouble = Double.parseDouble(peekedString);
    long result = (long) asDouble;
    if (result != asDouble) {
      throw new NumberFormatException("Expected a long but was " + peekedString + locationString());
    }
    peekedString = null;
    peeked = PEEKED_NONE;
    pathIndices[stackSize - 1]++;
    return result;
  }'''


# ============ 核心流程 ============

async def step1_build_index():
    """步骤1：构建代码库索引（嵌入）"""
    print("=" * 60)
    print("步骤1：构建代码库索引（嵌入）")
    print("=" * 60)
    
    rag = CodeRAG()
    rag.build_index(PROJECT_DIR, INDEX_PATH, batch_size=50)
    
    print(f"\n✓ 索引构建完成：{INDEX_PATH}\n")
    return rag


async def step2_retrieve_context():
    """步骤2：Agentic RAG智能检索"""
    print("=" * 60)
    print("步骤2：Agentic RAG智能检索")
    print("=" * 60)

    agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
    context = await agentic_rag.retrieve_by_agent(
        TARGET_METHOD, 
        target_class="JsonReader", 
        top_k=3,
        method_signature="public long nextLong() throws IOException"
    )

    print(f"✓ 检索完成，上下文长度：{len(context)} 字符\n")
    return context


async def step2_5_analyze_method(context: str):
    """步骤2.5：LLM解读方法功能 -> 分析覆盖点 -> 设计测试用例"""
    print("=" * 60)
    print("步骤2.5：LLM方法解读与测试用例设计")
    print("=" * 60)

    analysis = await analyze_method(
        class_name="JsonReader",
        method_signature="public long nextLong() throws IOException",
        method_code=TARGET_METHOD,
        context=context,
        full_class_name="com.google.gson.stream.JsonReader",
    )

    # Display results
    print("\n--- 方法功能解读 ---")
    print(analysis["method_understanding"][:800])
    if len(analysis["method_understanding"]) > 800:
        print("... (truncated)")

    print("\n--- 覆盖点分析 ---")
    print(analysis["coverage_analysis"][:800])
    if len(analysis["coverage_analysis"]) > 800:
        print("... (truncated)")

    print(f"\n--- 测试用例设计 ({len(analysis['test_cases'])} 个用例) ---")
    for tc in analysis["test_cases"]:
        tc_id = tc.get('id', '?')
        tc_name = tc.get('name', '?')
        tc_desc = tc.get('description', '')
        tc_cat = tc.get('category', '')
        tc_pri = tc.get('priority', '')
        print(f"  [{tc_id}] {tc_name} ({tc_cat}/{tc_pri}): {tc_desc}")

    print(f"\n✓ 方法解读与测试用例设计完成\n")
    return analysis


async def step3_generate_test(context: str, output_path: str, test_cases=None):
    """步骤3：根据测试用例设计生成单元测试"""
    print("=" * 60)
    print("步骤3：根据测试用例设计生成单元测试")
    print("=" * 60)
    
    result = await generate_test(
        class_name="JsonReader",
        method_signature="public long nextLong() throws IOException",
        method_code=TARGET_METHOD,
        output_path=output_path,
        context=context,
        test_class_name="JsonReader_nextLong_Test",
        full_class_name="com.google.gson.stream.JsonReader",
        test_cases=test_cases,
    )
    
    if result["success"]:
        print(f"✓ 测试生成成功：{result['output_path']}")
        
        # 显示生成的代码
        with open(output_path, 'r', encoding='utf-8') as f:
            code = f.read()
        print(f"\n生成的代码（前500字符）：\n{code[:500]}...")
    else:
        print(f"✗ 测试生成失败：{result['error']}")
    
    return result


async def step4_evaluate(test_file: str):
    """步骤4：评估测试（编译、运行、覆盖率）"""
    print("=" * 60)
    print("步骤4：评估测试")
    print("=" * 60)
    
    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME
    )
    
    report = evaluator.evaluate(
        test_file=test_file,
        test_class="com.google.gson.stream.JsonReaderTest",
        target_class="com.google.gson.stream.JsonReader",
        target_method="nextLong"
    )
    
    return report


def step5_save_report(report, output_path: str, baseline_coverage=None):
    """步骤5：保存评估报告（包含覆盖率对比）"""
    print("=" * 60)
    print("步骤5：保存评估报告")
    print("=" * 60)

    # 打印报告
    print_report(report)

    # 保存JSON报告
    report_data = {
        "timestamp": datetime.now().isoformat(),
        "test_file": report.test_file,
        "target_class": report.target_class,
        "target_method": report.target_method,
        "compilation_success": report.compilation_success,
        "errors": report.errors,
        "coverage": {
            "line_coverage": report.coverage.line_coverage if report.coverage else 0,
            "branch_coverage": report.coverage.branch_coverage if report.coverage else 0,
            "method_coverage": report.coverage.method_coverage if report.coverage else 0,
            "covered_lines": report.coverage.covered_lines if report.coverage else 0,
            "total_lines": report.coverage.total_lines if report.coverage else 0
        } if report.coverage else None,
        "test_count": len(report.test_results),
        "passed_count": sum(1 for t in report.test_results if t.passed)
    }

    # 添加基准覆盖率和对比信息
    if baseline_coverage:
        report_data["baseline_coverage"] = {
            "line_coverage": baseline_coverage.line_coverage,
            "branch_coverage": baseline_coverage.branch_coverage,
            "method_coverage": baseline_coverage.method_coverage,
            "covered_lines": baseline_coverage.covered_lines,
            "total_lines": baseline_coverage.total_lines
        }

        # 计算提升
        if report.coverage:
            report_data["coverage_improvement"] = {
                "line_coverage": report.coverage.line_coverage - baseline_coverage.line_coverage,
                "branch_coverage": report.coverage.branch_coverage - baseline_coverage.branch_coverage,
                "method_coverage": report.coverage.method_coverage - baseline_coverage.method_coverage,
                "covered_lines": report.coverage.covered_lines - baseline_coverage.covered_lines
            }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print(f"\n✓ 报告已保存：{output_path}")

    # 打印对比信息
    if baseline_coverage and report.coverage:
        line_imp = report.coverage.line_coverage - baseline_coverage.line_coverage
        branch_imp = report.coverage.branch_coverage - baseline_coverage.branch_coverage
        print(f"\n覆盖率提升：")
        print(f"  - 行覆盖率：{baseline_coverage.line_coverage:.1f}% → {report.coverage.line_coverage:.1f}% ({line_imp:+.1f}%)")
        print(f"  - 分支覆盖率：{baseline_coverage.branch_coverage:.1f}% → {report.coverage.branch_coverage:.1f}% ({branch_imp:+.1f}%)")

    return report_data


async def full_pipeline():
    """完整流程：嵌入 -> 检索 -> 生成 -> 评估 -> 报告"""
    print("\n" + "=" * 60)
    print("完整流程：Agentic RAG驱动的单元测试生成与评估")
    print("=" * 60 + "\n")
    
    # 准备输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    
    # 步骤1：构建索引（如果不存在）
    if not os.path.exists(INDEX_PATH):
        await step1_build_index()
    else:
        print(f"✓ 索引已存在：{INDEX_PATH}\n")
    
    # 步骤2：Agentic RAG检索
    context = await step2_retrieve_context()
    
    # 显示检索到的上下文摘要
    print("-" * 60)
    print("检索到的上下文（前1500字符）：")
    print("-" * 60)
    print(context[:1500] if len(context) > 1500 else context)
    print("-" * 60 + "\n")
    
    # 步骤2.5a：LLM方法解读与测试用例设计
    analysis = await step2_5_analyze_method(context)

    # 步骤2.5b：获取基准覆盖率
    print("=" * 60)
    print("步骤2.5b：获取基准覆盖率")
    print("=" * 60)
    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME
    )
    baseline_coverage = evaluator.get_baseline_coverage(
        target_class="com.google.gson.stream.JsonReader"
    )
    if baseline_coverage:
        print(f"  ✓ 基准覆盖率: 行 {baseline_coverage.line_coverage:.1f}%, 分支 {baseline_coverage.branch_coverage:.1f}%")
    else:
        print("  ! 无法获取基准覆盖率")
    print()
    
    # 步骤3：根据测试用例设计生成测试
    test_output_path = os.path.join(OUTPUT_DIR, "JsonReader_nextLong_Test.java")
    gen_result = await step3_generate_test(context, test_output_path, test_cases=analysis["test_cases"])
    
    if not gen_result["success"]:
        print("\n✗ 流程终止：测试生成失败")
        return None
    
    # 步骤4：评估测试（包含新测试）
    report = await step4_evaluate(test_output_path)
    
    # 步骤5：保存报告（内部已打印覆盖率对比）
    report_path = os.path.join(REPORT_DIR, "evaluation_report.json")
    report_data = step5_save_report(report, report_path, baseline_coverage)
    
    # 总结
    print("\n" + "=" * 60)
    print("流程完成 - 总结")
    print("=" * 60)
    print(f"✓ 测试文件：{test_output_path}")
    print(f"✓ 评估报告：{report_path}")
    print(f"✓ 编译状态：{'成功' if report.compilation_success else '失败'}")
    print(f"✓ 测试用例数：{len(analysis['test_cases'])}")
    if report.coverage:
        print(f"✓ 行覆盖率：{report.coverage.line_coverage:.1f}%")
        print(f"✓ 分支覆盖率：{report.coverage.branch_coverage:.1f}%")
    print("=" * 60)
    
    return {
        "test_file": test_output_path,
        "report": report,
        "report_data": report_data,
        "baseline_coverage": baseline_coverage,
        "analysis": analysis,
    }


# ============ 便捷函数 ============

def quick_generate(
    method_code: str,
    class_name: str,
    method_signature: str,
    package: str,
    output_path: str
):
    """
    快速生成单元测试（完整流程）

    Args:
        method_code: 目标方法代码
        class_name: 类名
        method_signature: 方法签名
        package: 包名
        output_path: 输出路径
    """
    async def run():
        # 1. 确保索引存在
        if not os.path.exists(INDEX_PATH):
            print("[→] 构建索引...")
            rag = CodeRAG()
            rag.build_index(PROJECT_DIR, INDEX_PATH)

        # 2. Agentic RAG检索上下文
        print("[→] Agentic RAG检索...")
        agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
        context = await agentic_rag.retrieve_by_agent(
            method_code, 
            target_class=class_name, 
            top_k=3,
            method_signature=method_signature
        )

        # 2.5. LLM方法解读与测试用例设计
        print("[→] LLM方法解读与测试用例设计...")
        method_name = method_signature.split()[1].split('(')[0] if '(' in method_signature else method_signature.split()[-1]
        test_class_name = f"{class_name}_{method_name}_Test"
        full_class_name = f"{package}.{class_name}"

        analysis = await analyze_method(
            class_name=class_name,
            method_signature=method_signature,
            method_code=method_code,
            context=context,
            full_class_name=full_class_name,
        )
        print(f"✓ 测试用例设计完成：{len(analysis['test_cases'])} 个用例")
        for tc in analysis["test_cases"]:
            print(f"  - [{tc.get('id', '?')}] {tc.get('name', '?')}: {tc.get('description', '')}")

        # 3. 根据测试用例设计生成测试
        print("[→] 根据测试用例设计生成测试...")
        
        result = await generate_test(
            class_name=class_name,
            method_signature=method_signature,
            method_code=method_code,
            output_path=output_path,
            context=context,
            test_class_name=test_class_name,
            full_class_name=full_class_name,
            test_cases=analysis["test_cases"],
        )

        if not result["success"]:
            print(f"✗ 生成失败：{result['error']}")
            return result

        # 3.5. 获取基准覆盖率
        print("[→] 获取基准覆盖率...")
        evaluator = TestEvaluator(
            project_dir=MAVEN_PROJECT_DIR,
            jacoco_home=JACOCO_HOME
        )
        baseline_coverage = evaluator.get_baseline_coverage(
            target_class=f"{package}.{class_name}"
        )

        if baseline_coverage:
            print(f"✓ 基准覆盖率：行 {baseline_coverage.line_coverage:.1f}%, 分支 {baseline_coverage.branch_coverage:.1f}%")

        # 4. 评估测试（包含新测试）
        print("[→] 评估新测试...")
        test_class = f"{package}.{test_class_name}"
        report = evaluator.evaluate(
            test_file=output_path,
            test_class=test_class,
            target_class=full_class_name,
            target_method=method_name
        )

        # 5. 打印报告
        print_report(report)

        return {
            "success": True,
            "test_file": output_path,
            "report": report,
            "baseline_coverage": baseline_coverage
        }

    return asyncio.run(run())


# ============ 主程序 ============

if __name__ == "__main__":
    # 运行完整流程
    asyncio.run(full_pipeline())
