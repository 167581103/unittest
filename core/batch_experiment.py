"""
Batch Experiment - Run full pipeline on lowest-coverage methods
Generates unit tests for 5 low-coverage methods and collects all reports.

Usage:
    conda activate gp
    python core/batch_experiment.py
"""

import os
import sys
import asyncio
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import CodeRAG, AgenticRAG
from llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report

# ============ Config ============

PROJECT_DIR = "/data/workspace/unittest/data/project/gson/gson/src/main/java"
TEST_DIR = "/data/workspace/unittest/data/project/gson/gson/src/test/java"
MAVEN_PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
INDEX_PATH = "/tmp/gson_code_rag.index"
JACOCO_HOME = "/data/workspace/unittest/lib"

EXPERIMENT_DIR = "/data/workspace/unittest/experiment_results"
GENERATED_TESTS_DIR = os.path.join(EXPERIMENT_DIR, "generated_tests")
REPORTS_DIR = os.path.join(EXPERIMENT_DIR, "reports")


# ============ Target Methods (lowest coverage 0%) ============

TARGET_METHODS = [
    {
        "id": "exp_01",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "package": "com.google.gson.internal.bind.util",
        "method_signature": "public static String format(Date date, boolean millis, TimeZone tz)",
        "baseline_test": "ISO8601UtilsTest",
        "method_code": (
            "public static String format(Date date, boolean millis, TimeZone tz) {\n"
            "    Calendar calendar = new GregorianCalendar(tz, Locale.US);\n"
            "    calendar.setTime(date);\n"
            "    int capacity = \"yyyy-MM-ddThh:mm:ss\".length();\n"
            "    capacity += millis ? \".sss\".length() : 0;\n"
            "    capacity += tz.getRawOffset() == 0 ? \"Z\".length() : \"+hh:mm\".length();\n"
            "    StringBuilder formatted = new StringBuilder(capacity);\n"
            "    padInt(formatted, calendar.get(Calendar.YEAR), \"yyyy\".length());\n"
            "    formatted.append('-');\n"
            "    padInt(formatted, calendar.get(Calendar.MONTH) + 1, \"MM\".length());\n"
            "    formatted.append('-');\n"
            "    padInt(formatted, calendar.get(Calendar.DAY_OF_MONTH), \"dd\".length());\n"
            "    formatted.append('T');\n"
            "    padInt(formatted, calendar.get(Calendar.HOUR_OF_DAY), \"hh\".length());\n"
            "    formatted.append(':');\n"
            "    padInt(formatted, calendar.get(Calendar.MINUTE), \"mm\".length());\n"
            "    formatted.append(':');\n"
            "    padInt(formatted, calendar.get(Calendar.SECOND), \"ss\".length());\n"
            "    if (millis) {\n"
            "      formatted.append('.');\n"
            "      padInt(formatted, calendar.get(Calendar.MILLISECOND), \"sss\".length());\n"
            "    }\n"
            "    int offset = tz.getOffset(calendar.getTimeInMillis());\n"
            "    if (offset != 0) {\n"
            "      int hours = Math.abs((offset / (60 * 1000)) / 60);\n"
            "      int minutes = Math.abs((offset / (60 * 1000)) % 60);\n"
            "      formatted.append(offset < 0 ? '-' : '+');\n"
            "      padInt(formatted, hours, \"hh\".length());\n"
            "      formatted.append(':');\n"
            "      padInt(formatted, minutes, \"mm\".length());\n"
            "    } else {\n"
            "      formatted.append('Z');\n"
            "    }\n"
            "    return formatted.toString();\n"
            "  }"
        ),
    },
    {
        "id": "exp_02",
        "class_name": "ISO8601Utils",
        "full_class_name": "com.google.gson.internal.bind.util.ISO8601Utils",
        "package": "com.google.gson.internal.bind.util",
        "method_signature": "public static Date parse(String date, ParsePosition pos)",
        "baseline_test": "ISO8601UtilsTest",
        "method_code": (
            "public static Date parse(String date, ParsePosition pos) throws ParseException {\n"
            "    Exception fail = null;\n"
            "    try {\n"
            "      int offset = pos.getIndex();\n"
            "      int year = parseInt(date, offset, offset += 4);\n"
            "      if (checkOffset(date, offset, '-')) { offset += 1; }\n"
            "      int month = parseInt(date, offset, offset += 2);\n"
            "      if (checkOffset(date, offset, '-')) { offset += 1; }\n"
            "      int day = parseInt(date, offset, offset += 2);\n"
            "      int hour = 0, minutes = 0, seconds = 0, milliseconds = 0;\n"
            "      boolean hasT = checkOffset(date, offset, 'T');\n"
            "      if (!hasT && (date.length() <= offset)) {\n"
            "        Calendar calendar = new GregorianCalendar(year, month - 1, day);\n"
            "        calendar.setLenient(false);\n"
            "        pos.setIndex(offset);\n"
            "        return calendar.getTime();\n"
            "      }\n"
            "      if (hasT) {\n"
            "        hour = parseInt(date, offset += 1, offset += 2);\n"
            "        if (checkOffset(date, offset, ':')) { offset += 1; }\n"
            "        minutes = parseInt(date, offset, offset += 2);\n"
            "        if (checkOffset(date, offset, ':')) { offset += 1; }\n"
            "        if (date.length() > offset) {\n"
            "          char c = date.charAt(offset);\n"
            "          if (c != 'Z' && c != '+' && c != '-') {\n"
            "            seconds = parseInt(date, offset, offset += 2);\n"
            "            if (seconds > 59 && seconds < 63) { seconds = 59; }\n"
            "            if (checkOffset(date, offset, '.')) {\n"
            "              offset += 1;\n"
            "              int endOffset = indexOfNonDigit(date, offset + 1);\n"
            "              int parseEndOffset = Math.min(endOffset, offset + 3);\n"
            "              int fraction = parseInt(date, offset, parseEndOffset);\n"
            "              switch (parseEndOffset - offset) {\n"
            "                case 2: milliseconds = fraction * 10; break;\n"
            "                case 1: milliseconds = fraction * 100; break;\n"
            "                default: milliseconds = fraction;\n"
            "              }\n"
            "              offset = endOffset;\n"
            "            }\n"
            "          }\n"
            "        }\n"
            "      }\n"
            "      if (date.length() <= offset) { throw new IllegalArgumentException(\"No time zone indicator\"); }\n"
            "      TimeZone timezone = null;\n"
            "      char timezoneIndicator = date.charAt(offset);\n"
            "      if (timezoneIndicator == 'Z') {\n"
            "        timezone = TIMEZONE_UTC; offset += 1;\n"
            "      } else if (timezoneIndicator == '+' || timezoneIndicator == '-') {\n"
            "        String timezoneOffset = date.substring(offset);\n"
            "        timezoneOffset = timezoneOffset.length() >= 5 ? timezoneOffset : timezoneOffset + \"00\";\n"
            "        offset += timezoneOffset.length();\n"
            "        if (timezoneOffset.equals(\"+0000\") || timezoneOffset.equals(\"+00:00\")) {\n"
            "          timezone = TIMEZONE_UTC;\n"
            "        } else {\n"
            "          String timezoneId = \"GMT\" + timezoneOffset;\n"
            "          timezone = TimeZone.getTimeZone(timezoneId);\n"
            "        }\n"
            "      } else {\n"
            "        throw new IndexOutOfBoundsException(\"Invalid time zone indicator '\" + timezoneIndicator + \"'\");\n"
            "      }\n"
            "      Calendar calendar = new GregorianCalendar(timezone);\n"
            "      calendar.setLenient(false);\n"
            "      calendar.set(Calendar.YEAR, year);\n"
            "      calendar.set(Calendar.MONTH, month - 1);\n"
            "      calendar.set(Calendar.DAY_OF_MONTH, day);\n"
            "      calendar.set(Calendar.HOUR_OF_DAY, hour);\n"
            "      calendar.set(Calendar.MINUTE, minutes);\n"
            "      calendar.set(Calendar.SECOND, seconds);\n"
            "      calendar.set(Calendar.MILLISECOND, milliseconds);\n"
            "      pos.setIndex(offset);\n"
            "      return calendar.getTime();\n"
            "    } catch (IndexOutOfBoundsException | IllegalArgumentException e) {\n"
            "      fail = e;\n"
            "    }\n"
            "    String input = (date == null) ? null : ('\"' + date + '\"');\n"
            "    String msg = fail.getMessage();\n"
            "    if (msg == null || msg.isEmpty()) { msg = \"(\" + fail.getClass().getName() + \")\"; }\n"
            "    ParseException ex = new ParseException(\"Failed to parse date [\" + input + \"]: \" + msg, pos.getIndex());\n"
            "    ex.initCause(fail);\n"
            "    throw ex;\n"
            "  }"
        ),
    },
    {
        "id": "exp_03",
        "class_name": "SqlDateTypeAdapter",
        "full_class_name": "com.google.gson.internal.sql.SqlDateTypeAdapter",
        "package": "com.google.gson.internal.sql",
        "method_signature": "public java.sql.Date read(JsonReader in)",
        "baseline_test": "SqlTypesGsonTest",
        "method_code": (
            "public java.sql.Date read(JsonReader in) throws IOException {\n"
            "    if (in.peek() == JsonToken.NULL) {\n"
            "      in.nextNull();\n"
            "      return null;\n"
            "    }\n"
            "    String s = in.nextString();\n"
            "    synchronized (this) {\n"
            "      TimeZone originalTimeZone = format.getTimeZone();\n"
            "      try {\n"
            "        Date utilDate = format.parse(s);\n"
            "        return new java.sql.Date(utilDate.getTime());\n"
            "      } catch (ParseException e) {\n"
            "        throw new JsonSyntaxException(\n"
            "            \"Failed parsing '\" + s + \"' as SQL Date; at path \" + in.getPreviousPath(), e);\n"
            "      } finally {\n"
            "        format.setTimeZone(originalTimeZone);\n"
            "      }\n"
            "    }\n"
            "  }"
        ),
    },
    {
        "id": "exp_04",
        "class_name": "TypeToken",
        "full_class_name": "com.google.gson.reflect.TypeToken",
        "package": "com.google.gson.reflect",
        "method_signature": "public static TypeToken<?> getParameterized(Type rawType, Type... typeArguments)",
        "baseline_test": "TypeTokenTest",
        "method_code": (
            "public static TypeToken<?> getParameterized(Type rawType, Type... typeArguments) {\n"
            "    Objects.requireNonNull(rawType);\n"
            "    Objects.requireNonNull(typeArguments);\n"
            "    if (!(rawType instanceof Class)) {\n"
            "      throw new IllegalArgumentException(\"rawType must be a Class, but was \" + rawType);\n"
            "    }\n"
            "    Class<?> rawClass = (Class<?>) rawType;\n"
            "    TypeVariable<?>[] typeVariables = rawClass.getTypeParameters();\n"
            "    int expectedArgsCount = typeVariables.length;\n"
            "    int actualArgsCount = typeArguments.length;\n"
            "    if (actualArgsCount != expectedArgsCount) {\n"
            "      throw new IllegalArgumentException(rawClass.getName() + \" requires \" + expectedArgsCount + \" type arguments, but got \" + actualArgsCount);\n"
            "    }\n"
            "    return TypeToken.get($Gson$Types.newParameterizedTypeWithOwner(null, rawClass, typeArguments));\n"
            "  }"
        ),
    },
    {
        "id": "exp_05",
        "class_name": "JsonReader",
        "full_class_name": "com.google.gson.stream.JsonReader",
        "package": "com.google.gson.stream",
        "method_signature": "public long nextLong() throws IOException",
        "baseline_test": "JsonReaderTest",
        "method_code": (
            "public long nextLong() throws IOException {\n"
            "    int p = peeked;\n"
            "    if (p == PEEKED_NONE) { p = doPeek(); }\n"
            "    if (p == PEEKED_LONG) {\n"
            "      peeked = PEEKED_NONE;\n"
            "      pathIndices[stackSize - 1]++;\n"
            "      return peekedLong;\n"
            "    }\n"
            "    if (p == PEEKED_NUMBER) {\n"
            "      peekedString = new String(buffer, pos, peekedNumberLength);\n"
            "      pos += peekedNumberLength;\n"
            "    } else if (p == PEEKED_SINGLE_QUOTED || p == PEEKED_DOUBLE_QUOTED || p == PEEKED_UNQUOTED) {\n"
            "      if (p == PEEKED_UNQUOTED) {\n"
            "        peekedString = nextUnquotedValue();\n"
            "      } else {\n"
            "        peekedString = nextQuotedValue(p == PEEKED_SINGLE_QUOTED ? '\\'' : '\"');\n"
            "      }\n"
            "      try {\n"
            "        long result = Long.parseLong(peekedString);\n"
            "        peeked = PEEKED_NONE;\n"
            "        pathIndices[stackSize - 1]++;\n"
            "        return result;\n"
            "      } catch (NumberFormatException ignored) {}\n"
            "    } else {\n"
            "      throw unexpectedTokenError(\"a long\");\n"
            "    }\n"
            "    peeked = PEEKED_BUFFERED;\n"
            "    double asDouble = Double.parseDouble(peekedString);\n"
            "    long result = (long) asDouble;\n"
            "    if (result != asDouble) {\n"
            "      throw new NumberFormatException(\"Expected a long but was \" + peekedString + locationString());\n"
            "    }\n"
            "    peekedString = null;\n"
            "    peeked = PEEKED_NONE;\n"
            "    pathIndices[stackSize - 1]++;\n"
            "    return result;\n"
            "  }"
        ),
    },
]


# ============ Helpers ============

def _cov_dict(cov, method_name):
    """Convert a CoverageReport to a serializable dict with target method info."""
    if cov is None:
        return None
    mc = cov.get_method_coverage(method_name)
    d = {
        "line_coverage": cov.line_coverage,
        "branch_coverage": cov.branch_coverage,
        "method_coverage": cov.method_coverage,
        "covered_lines": cov.covered_lines,
        "total_lines": cov.total_lines,
    }
    if mc:
        d["target_method_coverage"] = {
            "method_name": method_name,
            "line_coverage": mc.line_coverage,
            "branch_coverage": mc.branch_coverage,
            "covered_lines": mc.covered_lines,
            "total_lines": mc.total_lines,
        }
    return d


# ============ Pipeline ============

async def run_single_experiment(target: dict, evaluator: TestEvaluator) -> dict:
    """Run full pipeline for a single target method."""
    exp_id = target["id"]
    class_name = target["class_name"]
    method_sig = target["method_signature"]
    method_name = method_sig.split("(")[0].split()[-1]

    print(f"\n{'='*70}")
    print(f"[{exp_id}] {class_name}.{method_name}()")
    print(f"{'='*70}")

    result = {
        "id": exp_id,
        "class_name": class_name,
        "full_class_name": target["full_class_name"],
        "method_name": method_name,
        "method_signature": method_sig,
        "timestamp": datetime.now().isoformat(),
        "success": False,
        "error": None,
        "baseline_coverage": None,
        "new_coverage": None,
        "coverage_improvement": None,
        "compilation_success": False,
        "test_file": None,
        "report_file": None,
    }

    try:
        # Step 1: Ensure index exists
        if not os.path.exists(INDEX_PATH):
            print(f"[->] Building code index...")
            rag = CodeRAG()
            rag.build_index(PROJECT_DIR, INDEX_PATH, batch_size=50)
            print(f"  OK Index built: {INDEX_PATH}")
        else:
            print(f"[->] Using existing index: {INDEX_PATH}")

        # Step 2: Agentic RAG retrieval
        print(f"[->] Agentic RAG retrieval...")
        agentic_rag = AgenticRAG(INDEX_PATH, test_dir=TEST_DIR)
        context = await agentic_rag.retrieve_by_agent(
            target["method_code"],
            target_class=class_name,
            top_k=3,
            method_signature=method_sig,
        )
        print(f"  OK Context retrieved: {len(context)} chars")

        # Step 3: LLM method analysis & test case design
        print(f"[->] LLM method analysis & test case design...")
        analysis = await analyze_method(
            class_name=class_name,
            method_signature=method_sig,
            method_code=target["method_code"],
            context=context,
            full_class_name=target["full_class_name"],
        )
        print(f"  OK Method understanding: {len(analysis['method_understanding'])} chars")
        print(f"  OK Coverage analysis: {len(analysis['coverage_analysis'])} chars")
        print(f"  OK Test cases designed: {len(analysis['test_cases'])} cases")
        for tc in analysis["test_cases"]:
            print(f"    - [{tc.get('id', '?')}] {tc.get('name', '?')}: {tc.get('description', '')}")

        result["analysis"] = {
            "method_understanding": analysis["method_understanding"][:500] + "..." if len(analysis["method_understanding"]) > 500 else analysis["method_understanding"],
            "coverage_analysis": analysis["coverage_analysis"][:500] + "..." if len(analysis["coverage_analysis"]) > 500 else analysis["coverage_analysis"],
            "test_cases_count": len(analysis["test_cases"]),
            "test_cases": analysis["test_cases"],
        }

        # Step 4: Generate test based on test case design
        test_class_name = f"{class_name}_{method_name}_Test"
        test_file = os.path.join(GENERATED_TESTS_DIR, f"{test_class_name}.java")
        print(f"[->] Generating test based on {len(analysis['test_cases'])} designed cases: {test_class_name}")
        gen_result = await generate_test(
            class_name=class_name,
            method_signature=method_sig,
            method_code=target["method_code"],
            output_path=test_file,
            context=context,
            test_class_name=test_class_name,
            full_class_name=target["full_class_name"],
            package_name=target["package"],
            test_cases=analysis["test_cases"],
        )

        if not gen_result["success"]:
            result["error"] = f"Test generation failed: {gen_result['error']}"
            print(f"  FAIL {result['error']}")
            return result

        result["test_file"] = test_file
        print(f"  OK Test generated: {test_file}")

        # Step 4: Evaluate (clean env -> baseline -> compile -> run -> coverage)
        print(f"[->] Evaluating test...")
        test_class_full = f"{target['package']}.{test_class_name}"
        report = evaluator.evaluate(
            test_file=test_file,
            test_class=test_class_full,
            target_class=target["full_class_name"],
            target_method=method_name,
            baseline_test=target["baseline_test"],
        )

        result["compilation_success"] = report.compilation_success

        # Collect coverage data using helper
        effective_baseline = report.baseline_coverage
        result["baseline_coverage"] = _cov_dict(effective_baseline, method_name)
        result["new_coverage"] = _cov_dict(report.coverage, method_name)

        if effective_baseline:
            print(f"  OK Baseline: line={effective_baseline.line_coverage:.1f}%, branch={effective_baseline.branch_coverage:.1f}%")

        if effective_baseline and report.coverage:
            bmc = effective_baseline.get_method_coverage(method_name)
            nmc = report.coverage.get_method_coverage(method_name)
            result["coverage_improvement"] = {
                "line_coverage_delta": report.coverage.line_coverage - effective_baseline.line_coverage,
                "branch_coverage_delta": report.coverage.branch_coverage - effective_baseline.branch_coverage,
                "covered_lines_delta": report.coverage.covered_lines - effective_baseline.covered_lines,
                "target_method_line_delta": (nmc.line_coverage - bmc.line_coverage) if (nmc and bmc) else None,
                "target_method_branch_delta": (nmc.branch_coverage - bmc.branch_coverage) if (nmc and bmc) else None,
            }
            imp = result["coverage_improvement"]
            print(f"\n  Coverage improvement (class):")
            print(f"    Line:   {effective_baseline.line_coverage:.1f}% -> {report.coverage.line_coverage:.1f}%  ({imp['line_coverage_delta']:+.1f}%)")
            print(f"    Branch: {effective_baseline.branch_coverage:.1f}% -> {report.coverage.branch_coverage:.1f}%  ({imp['branch_coverage_delta']:+.1f}%)")
            if imp["target_method_line_delta"] is not None:
                print(f"  Coverage improvement (method [{method_name}]):")
                print(f"    Line:   {bmc.line_coverage:.1f}% -> {nmc.line_coverage:.1f}%  ({imp['target_method_line_delta']:+.1f}%)")
                print(f"    Branch: {bmc.branch_coverage:.1f}% -> {nmc.branch_coverage:.1f}%  ({imp['target_method_branch_delta']:+.1f}%)")

        # Step 5: Save individual report
        report_file = os.path.join(REPORTS_DIR, f"{exp_id}_{class_name}_{method_name}_report.json")
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        result["report_file"] = report_file
        print(f"  OK Report saved: {report_file}")

        result["success"] = report.compilation_success

    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f"  EXCEPTION: {e}")

    return result


async def run_all_experiments():
    """Run all 5 experiments and collect a summary report."""
    print("\n" + "=" * 70)
    print("Batch Experiment: RAG -> LLM -> Test (5 Low-Coverage Methods)")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    os.makedirs(EXPERIMENT_DIR, exist_ok=True)
    os.makedirs(GENERATED_TESTS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    print(f"\nExperiment directory: {EXPERIMENT_DIR}")
    print(f"  generated_tests/  <- Java test files")
    print(f"  reports/          <- JSON evaluation reports")

    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME,
    )

    all_results = []
    for i, target in enumerate(TARGET_METHODS):
        print(f"\n[{i+1}/{len(TARGET_METHODS)}] Running: {target['id']} - {target['class_name']}.{target['method_signature'].split('(')[0].split()[-1]}()")
        result = await run_single_experiment(target, evaluator)
        all_results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)

    summary = {
        "experiment_time": datetime.now().isoformat(),
        "total_experiments": len(all_results),
        "successful": sum(1 for r in all_results if r["success"]),
        "failed": sum(1 for r in all_results if not r["success"]),
        "results": all_results,
    }

    print(f"\n{'ID':<8} {'Class':<22} {'Method':<22} {'Compile':<9} {'Line Delta':<12} {'Branch Delta'}")
    print("-" * 85)
    for r in all_results:
        compile_ok = "OK" if r["compilation_success"] else "FAIL"
        line_delta = f"{r['coverage_improvement']['line_coverage_delta']:+.1f}%" if r.get("coverage_improvement") else "N/A"
        branch_delta = f"{r['coverage_improvement']['branch_coverage_delta']:+.1f}%" if r.get("coverage_improvement") else "N/A"
        print(f"{r['id']:<8} {r['class_name']:<22} {r['method_name']:<22} {compile_ok:<9} {line_delta:<12} {branch_delta}")

    print(f"\nTotal: {summary['successful']}/{summary['total_experiments']} succeeded")

    summary_file = os.path.join(EXPERIMENT_DIR, "experiment_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {summary_file}")

    print(f"\nAll output files in: {EXPERIMENT_DIR}")
    for root, dirs, files in os.walk(EXPERIMENT_DIR):
        level = root.replace(EXPERIMENT_DIR, "").count(os.sep)
        indent = "  " * level
        print(f"{indent}{os.path.basename(root)}/")
        sub_indent = "  " * (level + 1)
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            size = os.path.getsize(fpath)
            print(f"{sub_indent}{fname}  ({size} bytes)")

    print("\n" + "=" * 70)
    print("Experiment complete!")
    print("=" * 70)
    return summary


if __name__ == "__main__":
    asyncio.run(run_all_experiments())
