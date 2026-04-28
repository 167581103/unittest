"""临时验证脚本：只跑 1 个方法（Gson.fromJson），验证 FixLoop 修复是否生效。"""
import os
import sys
import asyncio
from datetime import datetime

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)

from rag import AgenticRAG
from evaluation.evaluator import TestEvaluator
from core import batch_test_cross_class as batch


async def main():
    os.makedirs(batch.OUTPUT_DIR, exist_ok=True)
    os.makedirs(batch.REPORT_DIR, exist_ok=True)

    print("=" * 60)
    print(f"验证 FixLoop (单方法版)  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    only_target = batch.TARGETS[9]
    print(f"目标: {only_target['full_class_name']}.{only_target['method_name']}")
    print(f"  {only_target.get('description', '')}")

    evaluator = TestEvaluator(
        project_dir=batch.MAVEN_PROJECT_DIR,
        jacoco_home=batch.JACOCO_HOME,
    )

    print(f"\n[→] 启动前清理残留...")
    evaluator._cleanup_old_generated_tests()

    print(f"[→] Loading code index...")
    shared_rag = AgenticRAG(batch.INDEX_PATH, test_dir=batch.TEST_DIR)
    print(f"[→] Index loaded.\n")

    print(f"[→] 获取基准覆盖率...")
    baseline = evaluator.get_baseline_coverage(
        only_target["full_class_name"],
        only_target.get("baseline_test"),
    )

    result = await batch.run_pipeline_for_target(
        only_target,
        evaluator,
        agentic_rag=shared_rag,
        cached_baseline=baseline,
    )

    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)
    for k, v in (result or {}).items():
        s = str(v)
        if len(s) > 500:
            s = s[:500] + "...(truncated)"
        print(f"  {k}: {s}")


if __name__ == "__main__":
    asyncio.run(main())
