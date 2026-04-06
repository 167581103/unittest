"""
Re-run evaluation only (skip LLM generation).
Uses already-generated test files in experiment_results/generated_tests/.

Usage:
    python core/rerun_eval.py
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.evaluator import TestEvaluator

MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
JACOCO_HOME       = "/data/workspace/unittest/lib"
EXPERIMENT_DIR    = "/data/workspace/unittest/experiment_results"
GENERATED_DIR     = os.path.join(EXPERIMENT_DIR, "generated_tests")
REPORTS_DIR       = os.path.join(EXPERIMENT_DIR, "reports")

TARGET_METHODS = [
    {
        "id": "exp_01",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "package": "com.google.gson.internal.bind.util",
        "method_name": "format",
        "baseline_test": "ISO8601UtilsTest",
        "test_file": os.path.join(GENERATED_DIR, "ISO8601Utils_format_Test.java"),
    },
    {
        "id": "exp_02",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "package": "com.google.gson.internal.bind.util",
        "method_name": "parse",
        "baseline_test": "ISO8601UtilsTest",
        "test_file": os.path.join(GENERATED_DIR, "ISO8601Utils_parse_Test.java"),
    },
    {
        "id": "exp_03",
        "class_name": "SqlDateTypeAdapter",
        "full_class_name": "com.google.gson.internal.sql.SqlDateTypeAdapter",
        "package": "com.google.gson.internal.sql",
        "method_name": "read",
        "baseline_test": "SqlTypesGsonTest",
        "test_file": os.path.join(GENERATED_DIR, "SqlDateTypeAdapter_read_Test.java"),
    },
    {
        "id": "exp_04",
        "class_name": "TypeToken",
        "full_class_name": "com.google.gson.reflect.TypeToken",
        "package": "com.google.gson.reflect",
        "method_name": "getParameterized",
        "baseline_test": "TypeTokenTest",
        "test_file": os.path.join(GENERATED_DIR, "TypeToken_getParameterized_Test.java"),
    },
    {
        "id": "exp_05",
        "class_name": "JsonReader",
        "full_class_name": "com.google.gson.stream.JsonReader",
        "package": "com.google.gson.stream",
        "method_name": "nextLong",
        "baseline_test": "JsonReaderTest",
        "test_file": os.path.join(GENERATED_DIR, "JsonReader_nextLong_Test.java"),
    },
]


def _cov_dict(cov, method_name):
    """Convert a CoverageReport to a serializable dict."""
    if cov is None:
        return None
    mc = cov.get_method_coverage(method_name)
    d = {
        "line_coverage":    cov.line_coverage,
        "branch_coverage":  cov.branch_coverage,
        "method_coverage":  cov.method_coverage,
        "covered_lines":    cov.covered_lines,
        "total_lines":      cov.total_lines,
    }
    if mc:
        d["target_method_coverage"] = {
            "method_name":     mc.method_name,
            "line_coverage":   mc.line_coverage,
            "branch_coverage": mc.branch_coverage,
            "covered_lines":   mc.covered_lines,
            "total_lines":     mc.total_lines,
        }
    return d


def run_one(target: dict, evaluator: TestEvaluator) -> dict:
    exp_id      = target["id"]
    class_name  = target["class_name"]
    method_name = target["method_name"]
    test_file   = target["test_file"]

    print(f"\n{'='*70}")
    print(f"[{exp_id}] {class_name}.{method_name}()")
    print(f"{'='*70}")

    if not os.path.exists(test_file):
        print(f"  ✗ Test file not found: {test_file}")
        return {"id": exp_id, "class_name": class_name, "method_name": method_name,
                "success": False, "error": "test file missing"}

    test_class_name = f"{class_name}_{method_name}_Test"
    test_class_full = f"{target['package']}.{test_class_name}"

    report = evaluator.evaluate(
        test_file=test_file,
        test_class=test_class_full,
        target_class=target["full_class_name"],
        target_method=method_name,
        baseline_test=target["baseline_test"],
    )

    baseline_cov = report.baseline_coverage
    new_cov      = report.coverage

    improvement = None
    if baseline_cov and new_cov:
        bmc = baseline_cov.get_method_coverage(method_name)
        nmc = new_cov.get_method_coverage(method_name)
        improvement = {
            "line_coverage_delta":        new_cov.line_coverage   - baseline_cov.line_coverage,
            "branch_coverage_delta":      new_cov.branch_coverage - baseline_cov.branch_coverage,
            "covered_lines_delta":        new_cov.covered_lines   - baseline_cov.covered_lines,
            "target_method_line_delta":   (nmc.line_coverage   - bmc.line_coverage)   if (nmc and bmc) else None,
            "target_method_branch_delta": (nmc.branch_coverage - bmc.branch_coverage) if (nmc and bmc) else None,
        }

    result = {
        "id":                   exp_id,
        "class_name":           class_name,
        "full_class_name":      target["full_class_name"],
        "method_name":          method_name,
        "timestamp":            datetime.now().isoformat(),
        "compilation_success":  report.compilation_success,
        "baseline_coverage":    _cov_dict(baseline_cov, method_name),
        "new_coverage":         _cov_dict(new_cov, method_name),
        "coverage_improvement": improvement,
        "success":              report.compilation_success,
        "errors":               report.errors,
    }

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"{exp_id}_{class_name}_{method_name}_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  ✓ Report saved: {report_path}")

    return result


def main():
    print("\n" + "="*70)
    print("Re-run Evaluation (no LLM generation)")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME,
    )

    all_results = [run_one(t, evaluator) for t in TARGET_METHODS]

    # Summary table
    print("\n" + "="*90)
    print("SUMMARY")
    print("="*90)

    def fmt(v, suffix="%"):
        return f"{v:.1f}{suffix}" if v is not None else "N/A"

    print(f"{'ID':<8} {'Class':<22} {'Method':<20} {'OK':<5} {'Base Line%':>10} {'New Line%':>10} {'ΔLine':>7}")
    print("-"*90)
    for r in all_results:
        ok  = "✅" if r.get("compilation_success") else "❌"
        bc  = r.get("baseline_coverage") or {}
        nc  = r.get("new_coverage") or {}
        imp = r.get("coverage_improvement") or {}
        print(f"{r['id']:<8} {r['class_name']:<22} {r['method_name']:<20} {ok:<5} "
              f"{fmt(bc.get('line_coverage')):>10} {fmt(nc.get('line_coverage')):>10} "
              f"{fmt(imp.get('line_coverage_delta')):>7}")

    print("="*90)

    summary = {
        "experiment_time": datetime.now().isoformat(),
        "total": len(all_results),
        "succeeded": sum(1 for r in all_results if r.get("compilation_success")),
        "results": all_results,
    }
    summary_path = os.path.join(EXPERIMENT_DIR, "experiment_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
