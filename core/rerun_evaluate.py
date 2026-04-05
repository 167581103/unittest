"""
Re-run evaluation for all 5 experiments using already-generated test files.
Fixes: baseline_coverage collection, package-private class, pom.xml failOnWarning.

Usage:
    python core/rerun_evaluate.py
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.evaluator import TestEvaluator

MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
JACOCO_HOME = "/data/workspace/unittest/lib"
EXPERIMENT_DIR = "/data/workspace/unittest/experiment_results"
GENERATED_TESTS_DIR = os.path.join(EXPERIMENT_DIR, "generated_tests")
REPORTS_DIR = os.path.join(EXPERIMENT_DIR, "reports")

# Correct test class mapping (package + class name)
EXPERIMENTS = [
    {
        "id": "exp_01",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "method_name": "format",
        "test_file": os.path.join(GENERATED_TESTS_DIR, "ISO8601Utils_format_Test.java"),
        # Test class has no package declaration -> evaluator places it in default package
        # But we need it in the right package for baseline comparison
        "test_class": "com.google.gson.internal.bind.util.ISO8601Utils_format_Test",
        "baseline_test": "ISO8601UtilsTest",
    },
    {
        "id": "exp_02",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "method_name": "parse",
        "test_file": os.path.join(GENERATED_TESTS_DIR, "ISO8601Utils_parse_Test.java"),
        "test_class": "com.google.gson.internal.bind.util.ISO8601Utils_parse_Test",
        "baseline_test": "ISO8601UtilsTest",
    },
    {
        "id": "exp_03",
        "class_name": "SqlDateTypeAdapter",
        "full_class_name": "com.google.gson.internal.sql.SqlDateTypeAdapter",
        "method_name": "read",
        "test_file": os.path.join(GENERATED_TESTS_DIR, "SqlDateTypeAdapter_read_Test.java"),
        # Now has package declaration: com.google.gson.internal.sql
        "test_class": "com.google.gson.internal.sql.SqlDateTypeAdapter_read_Test",
        "baseline_test": "SqlTypesGsonTest",
    },
    {
        "id": "exp_04",
        "class_name": "TypeToken",
        "full_class_name": "com.google.gson.reflect.TypeToken",
        "method_name": "getParameterized",
        "test_file": os.path.join(GENERATED_TESTS_DIR, "TypeToken_getParameterized_Test.java"),
        "test_class": "com.google.gson.reflect.TypeToken_getParameterized_Test",
        "baseline_test": "TypeTokenTest",
    },
    {
        "id": "exp_05",
        "class_name": "JsonReader",
        "full_class_name": "com.google.gson.stream.JsonReader",
        "method_name": "nextLong",
        "test_file": os.path.join(GENERATED_TESTS_DIR, "JsonReader_nextLong_Test.java"),
        "test_class": "com.google.gson.stream.JsonReader_nextLong_Test",
        "baseline_test": "JsonReaderTest",
    },
]


def run_evaluate():
    print("\n" + "=" * 70)
    print("Re-run Evaluation (using existing generated test files)")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME,
    )

    all_results = []

    for exp in EXPERIMENTS:
        exp_id = exp["id"]
        print(f"\n{'='*60}")
        print(f"[{exp_id}] {exp['class_name']}.{exp['method_name']}()")
        print(f"{'='*60}")

        result = {
            "id": exp_id,
            "class_name": exp["class_name"],
            "full_class_name": exp["full_class_name"],
            "method_name": exp["method_name"],
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "error": None,
            "baseline_coverage": None,
            "new_coverage": None,
            "coverage_improvement": None,
            "compilation_success": False,
            "test_file": exp["test_file"],
            "report_file": None,
        }

        try:
            # Run evaluate (internally gets baseline + compiles + runs + measures coverage)
            report = evaluator.evaluate(
                test_file=exp["test_file"],
                test_class=exp["test_class"],
                target_class=exp["full_class_name"],
                target_method=exp["method_name"],
                baseline_test=exp["baseline_test"],
            )

            result["compilation_success"] = report.compilation_success

            # Collect baseline from report
            if report.baseline_coverage:
                result["baseline_coverage"] = {
                    "line_coverage": report.baseline_coverage.line_coverage,
                    "branch_coverage": report.baseline_coverage.branch_coverage,
                    "method_coverage": report.baseline_coverage.method_coverage,
                    "covered_lines": report.baseline_coverage.covered_lines,
                    "total_lines": report.baseline_coverage.total_lines,
                }

            # Collect new coverage from report
            if report.coverage:
                result["new_coverage"] = {
                    "line_coverage": report.coverage.line_coverage,
                    "branch_coverage": report.coverage.branch_coverage,
                    "method_coverage": report.coverage.method_coverage,
                    "covered_lines": report.coverage.covered_lines,
                    "total_lines": report.coverage.total_lines,
                }

            # Compute improvement
            if report.baseline_coverage and report.coverage:
                result["coverage_improvement"] = {
                    "line_coverage_delta": report.coverage.line_coverage - report.baseline_coverage.line_coverage,
                    "branch_coverage_delta": report.coverage.branch_coverage - report.baseline_coverage.branch_coverage,
                    "covered_lines_delta": report.coverage.covered_lines - report.baseline_coverage.covered_lines,
                }
                print(f"\n  [RESULT] Coverage improvement:")
                print(f"    Line:   {report.baseline_coverage.line_coverage:.1f}% -> {report.coverage.line_coverage:.1f}%  ({result['coverage_improvement']['line_coverage_delta']:+.1f}%)")
                print(f"    Branch: {report.baseline_coverage.branch_coverage:.1f}% -> {report.coverage.branch_coverage:.1f}%  ({result['coverage_improvement']['branch_coverage_delta']:+.1f}%)")
                print(f"    Lines:  {report.baseline_coverage.covered_lines} -> {report.coverage.covered_lines}  ({result['coverage_improvement']['covered_lines_delta']:+d})")

            result["success"] = report.compilation_success

        except Exception as e:
            import traceback
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            print(f"  EXCEPTION: {e}")

        # Save individual report
        report_file = os.path.join(REPORTS_DIR, f"{exp_id}_{exp['class_name']}_{exp['method_name']}_report_v2.json")
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        result["report_file"] = report_file
        print(f"  Report saved: {report_file}")

        all_results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    summary = {
        "experiment_time": datetime.now().isoformat(),
        "total_experiments": len(all_results),
        "successful": sum(1 for r in all_results if r["success"]),
        "failed": sum(1 for r in all_results if not r["success"]),
        "results": all_results,
    }

    print(f"\n{'ID':<8} {'Class':<22} {'Method':<22} {'Compile':<9} {'Baseline Line':<15} {'New Line':<12} {'Delta'}")
    print("-" * 95)
    for r in all_results:
        compile_ok = "OK" if r["compilation_success"] else "FAIL"
        baseline_line = f"{r['baseline_coverage']['line_coverage']:.1f}%" if r.get("baseline_coverage") else "N/A"
        new_line = f"{r['new_coverage']['line_coverage']:.1f}%" if r.get("new_coverage") else "N/A"
        delta = f"{r['coverage_improvement']['line_coverage_delta']:+.1f}%" if r.get("coverage_improvement") else "N/A"
        print(f"{r['id']:<8} {r['class_name']:<22} {r['method_name']:<22} {compile_ok:<9} {baseline_line:<15} {new_line:<12} {delta}")

    print(f"\nTotal: {summary['successful']}/{summary['total_experiments']} compiled successfully")

    summary_file = os.path.join(EXPERIMENT_DIR, "experiment_summary_v2.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {summary_file}")
    print("=" * 70)

    return summary


if __name__ == "__main__":
    run_evaluate()
