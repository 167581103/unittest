"""
Evaluation模块 - 测试执行与覆盖率评估

使用示例：
    from evaluation import TestEvaluator, print_report
    
    evaluator = TestEvaluator(
        project_dir="/path/to/gson",
        jacoco_home="/path/to/jacoco"
    )
    
    report = evaluator.evaluate(
        test_file="/tmp/Test.java",
        test_class="com.example.Test",
        target_class="com.example.Target",
        target_method="targetMethod"
    )
    
    print_report(report)
"""

from evaluation.evaluator import (
    TestEvaluator,
    TestResult,
    CoverageReport,
    EvaluationReport,
    print_report
)

__all__ = [
    "TestEvaluator",
    "TestResult", 
    "CoverageReport",
    "EvaluationReport",
    "print_report"
]
