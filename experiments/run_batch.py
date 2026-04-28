#!/usr/bin/env python3
"""
run_batch.py —— 批量实验主入口（归一化四阶段流水线）

标准流水线（对单个方法）：

  ┌─────────────────────── Phase 1 (并行 LLM) ───────────────────────┐
  │  STAGE 1  GEN      : analyze_method → generate_test              │
  │                      产出 /tmp/batch_generated/<Class>_<method>_<id>_Test.java│
  └──────────────────────────────────────────────────────────────────┘

  ┌─────────────────────── Phase 2 (串行 Eval) ──────────────────────┐
  │  STAGE 2  PREFIX   : 预编译 + 确定性修复（规则 + import 补全）     │
  │                      不调 LLM、幂等、最多 2 轮                   │
  │  STAGE 3  EVAL     : TestEvaluator.evaluate                      │
  │                      拿 baseline + 编译 + 跑测试 + JaCoCo 覆盖率 │
  │  STAGE 4  FIXLOOP  : 若 STAGE 3 仍编译失败 → LLM + RAG 修复循环   │
  │                      修通后重跑 STAGE 3 拿覆盖率                 │
  └──────────────────────────────────────────────────────────────────┘

  ┌─────────────────────── Phase 3 (汇总)   ─────────────────────────┐
  │  STAGE 5  REPORT   : 失败归因 + JSON + Markdown                  │
  └──────────────────────────────────────────────────────────────────┘

用法：
  # 默认两步式 + 所有候选方法
  python experiments/run_batch.py

  # 消融实验：一步式生成
  python experiments/run_batch.py --one-shot --suffix oneshot

  # 限定前 3 个方法做 smoke
  python experiments/run_batch.py --limit 3

  # 控制 LLM 并发数（默认 4）
  python experiments/run_batch.py --llm-concurrency 4
"""
import os
import re
import sys
import json
import time
import asyncio
import argparse
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.llm import analyze_method, generate_test, _fix_imports
from evaluation.evaluator import TestEvaluator
from core.project_config import load_project, list_projects
from core.fix_loop import (
    fix_compile_errors,
    parse_compile_errors,
    classify_errors,
    rule_fix,
    _auto_add_missing_imports,
)

# 下面全局变量由 main() 内部根据 --project 加载；这里先放占位，
# 其他模块 import 时会看到默认空值，不会影响正常运行。
PROJECT_DIR: str = ""
JACOCO_HOME = "/data/workspace/unittest/lib"
RAG_INDEX_PATH: str = ""
RAG_TEST_DIR: str = ""
RESULTS_DIR = Path("/data/workspace/unittest/experiment_results")
METHODS_YAML = Path(__file__).parent / "methods.yaml"
GENERATED_DIR = Path("/tmp/batch_generated")
_CFG = None  # ProjectConfig，在 main() 里赋值

# 延迟加载的共享 AgenticRAG（首次失败时加载一次，之后 fix loop 之间复用）
_shared_agentic_rag = None


# ══════════════ 统一的 Stage Banner 打印 ══════════════

_STAGE_ICONS = {
    "GEN":     "🧠",
    "PREFIX":  "🔧",
    "EVAL":    "📊",
    "FIXLOOP": "🩹",
    "REPORT":  "📝",
}


def _stage(stage: str, method_id: str, msg: str = "", status: str = ""):
    """统一的阶段日志前缀。所有 pipeline 阶段的打印都走这个函数。

    stage:   GEN / PREFIX / EVAL / FIXLOOP / REPORT
    status:  可选，例如 ✓ / ✗ / → / 空
    """
    icon = _STAGE_ICONS.get(stage, "·")
    tag = f"[{stage:<7s}]"
    mid = f"[{method_id}]"
    prefix = f"{icon} {tag}{mid}"
    if status:
        prefix = f"{prefix} {status}"
    if msg:
        print(f"  {prefix} {msg}")
    else:
        print(f"  {prefix}")


def _phase_banner(title: str):
    """Phase 级别的大横幅。"""
    print()
    print("━" * 70)
    print(title)
    print("━" * 70)


def _get_agentic_rag():
    """懒加载 AgenticRAG：只在第一次需要 FixLoop 时才初始化，避免白白开销"""
    global _shared_agentic_rag
    if _shared_agentic_rag is not None:
        return _shared_agentic_rag
    try:
        from rag import AgenticRAG
        print("[RAG] Loading AgenticRAG index for FixLoop...")
        _shared_agentic_rag = AgenticRAG(RAG_INDEX_PATH, test_dir=RAG_TEST_DIR)
        print("[RAG] Loaded.")
    except Exception as e:
        print(f"[RAG] Failed to load AgenticRAG (fix loop will skip re-retrieval): {e}")
        _shared_agentic_rag = False
    return _shared_agentic_rag if _shared_agentic_rag else None


# ══════════════ 失败归因规则 ══════════════

FAILURE_PATTERNS = [
    ("cannot_find_symbol",      r"cannot find symbol"),
    ("incompatible_types",      r"incompatible types"),
    ("reference_ambiguous",     r"reference to .* is ambiguous"),
    ("unreported_exception",    r"unreported exception"),
    ("package_not_exist",       r"package .* does not exist"),
    ("bad_operand_types",       r"bad operand types"),
    ("method_in_class_cannot_apply", r"method .* in class .* cannot be applied"),
    ("missing_return",          r"missing return statement"),
    ("reached_end_of_file",     r"reached end of file while parsing"),
    ("illegal_start_of_expr",   r"illegal start of (?:expression|type)"),
    ("generic_inference",       r"incompatible types.*inferred type"),
    ("class_not_found",         r"class .* not found"),
    ("syntax_error",            r"(?:';' expected|'\{' expected|'\}' expected|not a statement)"),
    # 新增：私有/受保护访问；Werror（被 <failOnWarning> 升级的警告）；deprecation
    ("private_access",          r"has (?:private|protected) access in"),
    ("werror_warning",          r"warnings found and -Werror specified"),
    ("deprecation_error",       r"has been deprecated"),
    ("abstract_not_instantiable", r"(?:is abstract; cannot be instantiated|cannot be instantiated because it is abstract)"),
]


def classify_failure(stderr_text: str) -> List[str]:
    """把一大段编译 stderr 分类成若干标签"""
    if not stderr_text:
        return ["unknown"]
    tags = []
    for tag, pat in FAILURE_PATTERNS:
        if re.search(pat, stderr_text, re.IGNORECASE):
            tags.append(tag)
    return tags or ["other"]


# ══════════════ 数据结构 ══════════════

class MethodSpec:
    """对 methods.yaml 里一个条目的包装"""
    def __init__(self, raw: Dict):
        self.id: str = raw["id"]
        self.full_class_name: str = raw["full_class_name"]
        self.simple_class_name: str = raw["simple_class_name"]
        self.method_name: str = raw["method_name"]
        self.method_signature: str = raw["method_signature"]
        self.method_code: str = raw["method_code"]
        self.total_lines: int = raw.get("total_lines", 0)
        self.line_coverage: float = raw.get("line_coverage", 0.0)
        self.branch_coverage: float = raw.get("branch_coverage", 0.0)
        self.value: float = raw.get("value", 0.0)

    @property
    def package(self) -> str:
        return ".".join(self.full_class_name.split(".")[:-1])

    @property
    def test_class_name(self) -> str:
        return f"{self.simple_class_name}_{self.method_name}_{self.id}_Test"

    @property
    def full_test_class(self) -> str:
        return f"{self.package}.{self.test_class_name}"

    @property
    def output_file(self) -> str:
        return str(GENERATED_DIR / f"{self.simple_class_name}_{self.method_name}_{self.id}_Test.java")


# ══════════════ Phase 1：并行 LLM 生成 ══════════════

async def _gen_one(spec: MethodSpec, sem: asyncio.Semaphore, one_shot: bool) -> Dict:
    """单个方法走 analyze + generate，受 Semaphore 限流"""
    async with sem:
        t0 = time.time()
        result = {
            "id": spec.id,
            "full_class_name": spec.full_class_name,
            "method_name": spec.method_name,
            "output_file": spec.output_file,
            "test_class": spec.full_test_class,
            "mode": "one_shot" if one_shot else "two_step",
            "gen_success": False,
            "gen_error": None,
            "test_cases_count": 0,
            "methods_generated": 0,
            "gen_duration_s": 0.0,
        }
        try:
            _stage("GEN", spec.id, f"analyze: {spec.simple_class_name}.{spec.method_name}")
            _junit_ver = int(_CFG.junit_version) if _CFG else 4
            analysis = await analyze_method(
                class_name=spec.simple_class_name,
                method_signature=spec.method_signature,
                method_code=spec.method_code,
                context=_build_minimal_context(spec),
                full_class_name=spec.full_class_name,
                junit_version=_junit_ver,
            )
            cases = analysis.get("test_cases") or []
            result["test_cases_count"] = len(cases)
            if not cases:
                result["gen_error"] = "analyze_method produced 0 test cases"
                return result

            _stage("GEN", spec.id, f"generate: {len(cases)} cases, one_shot={one_shot}")
            gen = await generate_test(
                class_name=spec.simple_class_name,
                method_signature=spec.method_signature,
                method_code=spec.method_code,
                output_path=spec.output_file,
                context=_build_minimal_context(spec),
                test_class_name=spec.test_class_name,
                full_class_name=spec.full_class_name,
                package_name=spec.package,
                test_cases=cases,
                one_shot=one_shot,
                junit_version=_junit_ver,
            )
            result["gen_success"] = bool(gen.get("success"))
            result["gen_error"] = gen.get("error")
            result["methods_generated"] = gen.get("methods_generated", 0)
        except Exception as e:
            result["gen_error"] = f"exception: {e}\n{traceback.format_exc(limit=3)}"
        finally:
            result["gen_duration_s"] = round(time.time() - t0, 1)
        return result


def _build_minimal_context(spec: MethodSpec) -> str:
    """最小上下文：告诉 LLM 这个方法的所属类、包名，后续可接 RAG。"""
    return (
        f"Target class: {spec.full_class_name}\n"
        f"Target method signature: {spec.method_signature}\n"
        f"Package: {spec.package}\n"
        f"Method belongs to class: {spec.simple_class_name}\n"
        f"Use only public constructors and public methods of the target class "
        f"and standard Java/JUnit 4 APIs.\n"
        f"If the target class constructor is not public, do NOT instantiate it directly. "
        f"Prefer invoking tested behavior through accessible public/static entry points.\n"
    )


async def phase1_generate_all(specs: List[MethodSpec], concurrency: int, one_shot: bool) -> List[Dict]:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    tasks = [_gen_one(s, sem, one_shot) for s in specs]
    return await asyncio.gather(*tasks)


def _compile_for_prefix(spec: MethodSpec, evaluator: TestEvaluator):
    """把当前生成文件拷到工程并编译一次，返回 (success, output)。"""
    evaluator._cleanup_old_generated_tests()
    evaluator._copy_test_file(spec.output_file, spec.full_test_class)
    actual_cls = evaluator._actual_test_class or spec.full_test_class
    return evaluator._compile_test_with_output(actual_cls)


def _apply_deterministic_prefix(
    spec: MethodSpec,
    evaluator: TestEvaluator,
    max_rounds: int = 2,
) -> Dict:
    """在正式评估前执行确定性修复（规则 + 本地后处理，不调用 LLM）。"""
    result = {
        "attempted": False,
        "compile_success": False,
        "changes_applied": 0,
        "rounds": 0,
        "log": [],
        "last_compile_output": "",
    }

    try:
        with open(spec.output_file, "r", encoding="utf-8") as f:
            current_code = f.read()
    except Exception as e:
        result["log"].append(f"Prefix read failed: {e}")
        return result

    # ── Pre-compile import completion（不依赖编译错误）──
    # 在第一次编译之前就主动扫一遍代码里用到的符号（new Foo() / Foo.xxx / Foo v = ... / (Foo)x），
    # 结合项目 RAG 索引 + JDK 常用类表，把能补的 import 直接补上。
    # 这条路径对首次编译成功率提升最直接，避免白白进 FixLoop。
    rag_for_prefix = _get_agentic_rag()
    try:
        pre_added_code, pre_added_imports = _auto_add_missing_imports(
            current_code, classified={}, rag_instance=rag_for_prefix,
        )
    except Exception as e:
        pre_added_code, pre_added_imports = current_code, []
        result["log"].append(f"Pre-compile import completion failed: {e}")

    if pre_added_imports and pre_added_code != current_code:
        try:
            with open(spec.output_file, "w", encoding="utf-8") as f:
                f.write(pre_added_code)
            current_code = pre_added_code
            result["attempted"] = True
            result["changes_applied"] += 1
            result["log"].append(
                "Pre-compile: auto-added imports: " + ", ".join(pre_added_imports)
            )
        except Exception as e:
            result["log"].append(f"Pre-compile write failed: {e}")

    for round_idx in range(1, max_rounds + 1):
        result["rounds"] = round_idx
        success, compile_output = _compile_for_prefix(spec, evaluator)
        result["last_compile_output"] = compile_output or ""

        if success:
            result["compile_success"] = True
            if round_idx == 1:
                if pre_added_imports:
                    result["log"].append(
                        "Round 1: compile passed after pre-compile import completion"
                    )
                else:
                    result["log"].append("Round 1: compile passed without deterministic fixes")
            else:
                result["log"].append(f"Round {round_idx}: compile passed after deterministic fixes")
            return result

        errors = parse_compile_errors(compile_output or "")
        if not errors:
            result["log"].append(f"Round {round_idx}: compile failed but no parseable errors; stop prefix")
            return result

        classified = classify_errors(errors)
        _junit_ver = int(_CFG.junit_version) if _CFG else 4
        fixed_code, rule_fixes = rule_fix(current_code, classified, rag_instance=rag_for_prefix,
                                          junit_version=_junit_ver)
        normalized_code = _fix_imports(fixed_code, junit_version=_junit_ver)

        if rule_fixes:
            result["log"].append(f"Round {round_idx}: " + "; ".join(rule_fixes))
        if normalized_code != fixed_code:
            result["log"].append(f"Round {round_idx}: applied generic post-process (_fix_imports)")

        if normalized_code == current_code:
            result["log"].append(f"Round {round_idx}: no deterministic code change; stop prefix")
            return result

        try:
            with open(spec.output_file, "w", encoding="utf-8") as f:
                f.write(normalized_code)
            current_code = normalized_code
            result["attempted"] = True
            result["changes_applied"] += 1
        except Exception as e:
            result["log"].append(f"Round {round_idx}: write failed: {e}")
            return result

    # 最后一轮改完后再做一次验证，得到最新编译输出
    success, compile_output = _compile_for_prefix(spec, evaluator)
    result["last_compile_output"] = compile_output or ""
    result["compile_success"] = bool(success)
    if success:
        result["log"].append("Final check: compile succeeded after deterministic prefix")
    else:
        result["log"].append("Final check: still failing after deterministic prefix")
    return result


# ══════════════ Phase 2：串行评估 ══════════════

async def phase2_evaluate_all(specs: List[MethodSpec], gen_results: List[Dict],
                              fix_retries: int = 3) -> List[Dict]:
    """逐个跑确定性前置修复 + TestEvaluator + FixLoop（必须串行）

    流程：
      1. deterministic prefix：先编译一次，按编译错误做 rule_fix + _fix_imports（不调LLM）
      2. evaluator.evaluate(...)：拿 baseline + 编译 + 测试 + 覆盖率
      3. 若仍编译失败 → 调 fix_compile_errors（规则 + LLM + RAG），最多 fix_retries 次
      4. 若 Fix 成功，重新跑一遍测试拿覆盖率
      5. 最后记录 prefix/fix 日志与 failure_tags
    """
    evaluator = TestEvaluator(
        project_dir=PROJECT_DIR,
        jacoco_home=JACOCO_HOME,
        module_name=_CFG.module_name if _CFG else "gson",
        java_home=_CFG.java_home if _CFG else None,
        surefire_arglines=_CFG.surefire_arglines if _CFG else False,
        mvn_extra_args=_CFG.mvn_extra_args if _CFG else None,
    )
    eval_results: List[Dict] = []

    spec_by_id = {s.id: s for s in specs}

    for gen in gen_results:
        spec = spec_by_id[gen["id"]]
        eval_record = {
            "id": gen["id"],
            "compile_success": False,
            "compile_success_stage": None,  # "initial" | "after_prefix" | "after_fix" | None
            "deterministic_prefixed": False,
            "deterministic_prefix_success": False,
            "deterministic_prefix_changes": 0,
            "deterministic_prefix_log": [],
            "fix_attempted": False,
            "fix_success": False,
            "fix_log": [],
            "baseline_line_cov": None,
            "baseline_branch_cov": None,
            "new_line_cov": None,
            "new_branch_cov": None,
            "line_cov_delta": None,
            "branch_cov_delta": None,
            "target_method_line_cov": None,
            "target_method_branch_cov": None,
            # ─── 方法级覆盖率（主指标）────────────────────────────────
            # 类级覆盖率 Delta 在类已高覆盖的场景下易被稀释为 0.0，
            # 为了评估 “补充低覆盖率方法” 的效果，记录方法级 baseline/new/delta。
            "target_method_baseline_line_cov": None,
            "target_method_baseline_branch_cov": None,
            "target_method_line_cov_delta": None,
            "target_method_branch_cov_delta": None,
            "failure_tags": [],
            "eval_error": None,
            "eval_duration_s": 0.0,
        }
        t0 = time.time()

        if not gen["gen_success"]:
            eval_record["eval_error"] = f"skipped: gen failed ({gen['gen_error']})"
            eval_record["failure_tags"] = ["llm_gen_failed"]
            eval_record["eval_duration_s"] = round(time.time() - t0, 1)
            eval_results.append(eval_record)
            continue

        print()
        print("─" * 70)
        print(f"  ▶ [{spec.id}] {spec.simple_class_name}.{spec.method_name}")
        print("─" * 70)
        try:
            # ── STAGE 2: PREFIX ── 确定性前置修复（规则 + import 补全，不调 LLM） ─
            prefix = _apply_deterministic_prefix(spec, evaluator)
            eval_record["deterministic_prefixed"] = bool(prefix.get("attempted"))
            eval_record["deterministic_prefix_success"] = bool(prefix.get("compile_success"))
            eval_record["deterministic_prefix_changes"] = int(prefix.get("changes_applied") or 0)
            eval_record["deterministic_prefix_log"] = prefix.get("log") or []
            last_compile_output = prefix.get("last_compile_output", "")

            if eval_record["deterministic_prefixed"]:
                prefix_status = "✓" if eval_record["deterministic_prefix_success"] else "·"
                _stage(
                    "PREFIX", spec.id,
                    f"rounds={prefix.get('rounds', 0)}, "
                    f"changes={eval_record['deterministic_prefix_changes']}, "
                    f"compile_ok={eval_record['deterministic_prefix_success']}",
                    status=prefix_status,
                )

            # ── STAGE 3: EVAL ── baseline + compile + run + coverage ─
            _stage("EVAL", spec.id, "baseline + compile + run + coverage", status="→")
            report = evaluator.evaluate(
                test_file=spec.output_file,
                test_class=spec.full_test_class,
                target_class=spec.full_class_name,
                target_method=spec.method_name,
            )

            # ── STAGE 4: FIXLOOP ── 仅当 STAGE 3 仍编译失败时启动 ─
            if not report.compilation_success:
                _stage(
                    "FIXLOOP", spec.id,
                    f"评估阶段编译失败，启动修复循环（max_retries={fix_retries}）",
                    status="→",
                )
                eval_record["fix_attempted"] = True
                fixed_code, fix_success, fix_log, last_compile_output = await _run_fix_loop(
                    spec, evaluator, max_retries=fix_retries)
                eval_record["fix_success"] = fix_success
                eval_record["fix_log"] = fix_log

                if fix_success:
                    # Fix 通了 → 重新 evaluate 拿覆盖率。
                    with open(spec.output_file, "w", encoding="utf-8") as f:
                        f.write(fixed_code)
                    _stage("FIXLOOP", spec.id, "修复成功，重新评估拿覆盖率", status="✓")
                    report = evaluator.evaluate(
                        test_file=spec.output_file,
                        test_class=spec.full_test_class,
                        target_class=spec.full_class_name,
                        target_method=spec.method_name,
                    )
                    eval_record["compile_success_stage"] = "after_fix"
                else:
                    _stage("FIXLOOP", spec.id, "耗尽重试仍失败", status="✗")
            else:
                # 首次成功口径：
                # - initial: 未做prefix修改，直接通过
                # - after_prefix: 经过确定性prefix后通过（未进入FixLoop）
                if eval_record["deterministic_prefixed"]:
                    eval_record["compile_success_stage"] = "after_prefix"
                else:
                    eval_record["compile_success_stage"] = "initial"

            # ── STAGE 5: REPORT（本条记录）── 记录覆盖率（无论是否 fix）──
            eval_record["compile_success"] = bool(report.compilation_success)

            if report.baseline_coverage:
                eval_record["baseline_line_cov"] = round(report.baseline_coverage.line_coverage, 2)
                eval_record["baseline_branch_cov"] = round(report.baseline_coverage.branch_coverage, 2)
            if report.coverage:
                eval_record["new_line_cov"] = round(report.coverage.line_coverage, 2)
                eval_record["new_branch_cov"] = round(report.coverage.branch_coverage, 2)
                if report.baseline_coverage:
                    eval_record["line_cov_delta"] = round(
                        report.coverage.line_coverage - report.baseline_coverage.line_coverage, 2)
                    eval_record["branch_cov_delta"] = round(
                        report.coverage.branch_coverage - report.baseline_coverage.branch_coverage, 2)
                else:
                    # Fallback: 没有 baseline（目标类没有现成 XxxTest.java）
                    # 视为 baseline=0% 来计算增量，避免报表出现大量 "-"
                    eval_record["baseline_line_cov"] = 0.0
                    eval_record["baseline_branch_cov"] = 0.0
                    eval_record["baseline_is_fallback"] = True
                    eval_record["line_cov_delta"] = round(report.coverage.line_coverage, 2)
                    eval_record["branch_cov_delta"] = round(report.coverage.branch_coverage, 2)
                tgt = report.coverage.get_method_coverage(spec.method_name)
                if tgt:
                    eval_record["target_method_line_cov"] = round(tgt.line_coverage, 2)
                    eval_record["target_method_branch_cov"] = round(tgt.branch_coverage, 2)
                # 方法级 baseline 及 Delta（主指标）
                # 同名重载时，(name, desc) 唯一匹配目标说明单，否则回退到 name 匹配。
                base_mc = None
                if report.baseline_coverage:
                    if tgt is not None and getattr(tgt, "desc", None):
                        for mc in (report.baseline_coverage.method_coverages or []):
                            if mc.method_name == spec.method_name and getattr(mc, "desc", None) == tgt.desc:
                                base_mc = mc
                                break
                    if base_mc is None:
                        base_mc = report.baseline_coverage.get_method_coverage(spec.method_name)
                if base_mc is not None:
                    eval_record["target_method_baseline_line_cov"] = round(base_mc.line_coverage, 2)
                    eval_record["target_method_baseline_branch_cov"] = round(base_mc.branch_coverage, 2)
                    if tgt is not None:
                        eval_record["target_method_line_cov_delta"] = round(
                            tgt.line_coverage - base_mc.line_coverage, 2)
                        eval_record["target_method_branch_cov_delta"] = round(
                            tgt.branch_coverage - base_mc.branch_coverage, 2)
                elif tgt is not None:
                    # 没有 baseline 方法级数据时，回退到 pick 阶段的覆盖率作为 baseline（有 pick 值时再用）
                    pick_line = spec.line_coverage if spec.line_coverage is not None else None
                    pick_branch = spec.branch_coverage if spec.branch_coverage is not None else None
                    if pick_line is not None:
                        eval_record["target_method_baseline_line_cov"] = round(pick_line, 2)
                        eval_record["target_method_line_cov_delta"] = round(tgt.line_coverage - pick_line, 2)
                    if pick_branch is not None:
                        eval_record["target_method_baseline_branch_cov"] = round(pick_branch, 2)
                        eval_record["target_method_branch_cov_delta"] = round(tgt.branch_coverage - pick_branch, 2)

            # ── STAGE 5: REPORT（失败归因）── 用 fix loop 最后一次 stderr 分类 ──
            if not eval_record["compile_success"]:
                stderr_text = last_compile_output or _grab_compile_stderr(spec)
                # 只提取 [ERROR] 行，精简存储
                err_lines = [ln.strip() for ln in (stderr_text or "").split("\n")
                             if "[ERROR]" in ln and ".java:" in ln]
                snippet = "\n".join(err_lines[:30])
                eval_record["failure_tags"] = classify_failure(snippet)
                eval_record["compile_stderr_snippet"] = snippet[:2000]

        except Exception as e:
            eval_record["eval_error"] = f"{e}\n{traceback.format_exc(limit=3)}"
            eval_record["failure_tags"] = ["evaluator_exception"]
        finally:
            eval_record["eval_duration_s"] = round(time.time() - t0, 1)
        eval_results.append(eval_record)

    return eval_results


async def _run_fix_loop(spec: MethodSpec, evaluator: TestEvaluator,
                       max_retries: int = 3):
    """在 spec.output_file 上跑 FixLoop；返回 (fixed_code, success, fix_log, last_output)"""
    # 拿到首次失败的编译输出（evaluator 不直接暴露，这里重跑一次拿 stderr）
    with open(spec.output_file, "r", encoding="utf-8") as f:
        current_code = f.read()

    evaluator._cleanup_old_generated_tests()
    evaluator._copy_test_file(spec.output_file, spec.full_test_class)
    _, compile_output = evaluator._compile_test_with_output(
        evaluator._actual_test_class or spec.full_test_class)

    def try_compile(fixed_code: str):
        # 写回原 output_path，再拷到工程的测试目录下，编译
        with open(spec.output_file, "w", encoding="utf-8") as f:
            f.write(fixed_code)
        evaluator._cleanup_old_generated_tests()
        evaluator._copy_test_file(spec.output_file, spec.full_test_class)
        actual_cls = evaluator._actual_test_class or spec.full_test_class
        return evaluator._compile_test_with_output(actual_cls)

    agentic_rag = _get_agentic_rag()
    _junit_ver = int(_CFG.junit_version) if _CFG else 4
    fixed_code, success, fix_log = await fix_compile_errors(
        code=current_code,
        compile_output=compile_output,
        context="",  # 初始没 RAG，fix loop 内部会再检索
        max_retries=max_retries,
        compile_fn=try_compile,
        agentic_rag=agentic_rag,
        target_class=spec.full_class_name,
        method_signature=spec.method_signature,
        junit_version=_junit_ver,
    )

    # fix_log 中包含最后一次尝试的信息；拿最后一次编译输出用于归因
    # 如果 fix 中途走到最后一轮 LLM 后没再调 compile_fn，这里手动补一次
    if not success:
        try:
            _, last_output = try_compile(fixed_code)
            compile_output = last_output
        except Exception:
            pass

    return fixed_code, success, fix_log, compile_output

def _grab_compile_stderr(spec: MethodSpec) -> str:
    """编译失败时再跑一遍编译，拿到 stderr 用来归因（evaluator 的 API 没直接暴露）"""
    import subprocess
    env = (_CFG.build_env() if _CFG else os.environ.copy())
    cmd = ["mvn", "test-compile"]
    if _CFG:
        cmd += _CFG.mvn_module_args()
    cmd += ["-DskipTests", "-Dmaven.compiler.failOnWarning=false"]
    if _CFG:
        cmd += list(_CFG.mvn_extra_args or [])
    try:
        res = subprocess.run(
            cmd, cwd=PROJECT_DIR, env=env,
            capture_output=True, text=True, timeout=120,
        )
        combined = (res.stdout or "") + (res.stderr or "")
        # 优先抓 javac 错误行 [ERROR] /xxx.java:[12,34] msg
        lines = [ln.strip() for ln in combined.split("\n")
                 if "[ERROR]" in ln and ".java:" in ln]
        if lines:
            return "\n".join(lines[:30])
        # 兜底：抓任何 [ERROR] 行（RAT / enforcer / 插件失败等）
        fallback = [ln.strip() for ln in combined.split("\n")
                    if ln.strip().startswith("[ERROR]") and ln.strip() != "[ERROR]"]
        return "\n".join(fallback[:30])
    except Exception:
        return ""


# ══════════════ Phase 4：汇总输出 ══════════════

def _merge_results(specs: List[MethodSpec], gen: List[Dict], eval_: List[Dict]) -> List[Dict]:
    gen_by = {r["id"]: r for r in gen}
    eval_by = {r["id"]: r for r in eval_}
    rows = []
    for s in specs:
        row = {
            "id": s.id,
            "class": s.simple_class_name,
            "full_class": s.full_class_name,
            "method": s.method_name,
            "baseline_line_cov_from_pick": s.line_coverage,  # pick 阶段的覆盖率
            "method_total_lines": s.total_lines,
            **gen_by.get(s.id, {}),
            **eval_by.get(s.id, {}),
        }
        rows.append(row)
    return rows


def _write_json(rows: List[Dict], out_path: Path, meta: Dict):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "summary": _summary(rows),
        "results": rows,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[json] ✓ {out_path}")


def _summary(rows: List[Dict]) -> Dict:
    total = len(rows)
    gen_ok = sum(1 for r in rows if r.get("gen_success"))
    compile_ok = sum(1 for r in rows if r.get("compile_success"))
    compile_ok_initial = sum(1 for r in rows if r.get("compile_success_stage") == "initial")
    compile_ok_after_prefix = sum(1 for r in rows if r.get("compile_success_stage") == "after_prefix")
    prefix_attempted = sum(1 for r in rows if r.get("deterministic_prefixed"))
    prefix_rescued = sum(1 for r in rows if r.get("compile_success_stage") == "after_prefix")
    fix_attempted = sum(1 for r in rows if r.get("fix_attempted"))
    fix_rescued = sum(1 for r in rows if r.get("fix_success"))  # 被 FixLoop 拾回的
    deltas_line = [r["line_cov_delta"] for r in rows if r.get("line_cov_delta") is not None]
    deltas_branch = [r["branch_cov_delta"] for r in rows if r.get("branch_cov_delta") is not None]
    avg_line = round(sum(deltas_line) / len(deltas_line), 2) if deltas_line else 0.0
    avg_branch = round(sum(deltas_branch) / len(deltas_branch), 2) if deltas_branch else 0.0

    # 主指标：方法级覆盖率 Delta（类级 Delta 很容易被高覆盖率的类稀释为 0）
    m_deltas_line = [r["target_method_line_cov_delta"] for r in rows
                     if r.get("target_method_line_cov_delta") is not None]
    m_deltas_branch = [r["target_method_branch_cov_delta"] for r in rows
                       if r.get("target_method_branch_cov_delta") is not None]
    avg_m_line = round(sum(m_deltas_line) / len(m_deltas_line), 2) if m_deltas_line else 0.0
    avg_m_branch = round(sum(m_deltas_branch) / len(m_deltas_branch), 2) if m_deltas_branch else 0.0
    # 只统计编译成功且实际增加 (>0) 的方法数，用于答辩史的“有效提升覆盖的方法比例”
    improved_line = sum(1 for r in rows
                        if r.get("compile_success") and (r.get("target_method_line_cov_delta") or 0) > 0)
    improved_branch = sum(1 for r in rows
                          if r.get("compile_success") and (r.get("target_method_branch_cov_delta") or 0) > 0)

    # 失败原因计数（只统计 fix 后仍失败的）
    tag_count: Dict[str, int] = {}
    for r in rows:
        if r.get("compile_success"):
            continue
        for t in (r.get("failure_tags") or []):
            tag_count[t] = tag_count.get(t, 0) + 1

    return {
        "total": total,
        "gen_success": gen_ok,
        "gen_success_rate": round(gen_ok / total * 100, 1) if total else 0,
        "compile_success": compile_ok,
        "compile_success_rate": round(compile_ok / total * 100, 1) if total else 0,
        "compile_success_initial": compile_ok_initial,
        "compile_success_after_prefix": compile_ok_after_prefix,
        "prefix_attempted": prefix_attempted,
        "prefix_rescued": prefix_rescued,
        "prefix_rescue_rate": round(prefix_rescued / prefix_attempted * 100, 1) if prefix_attempted else 0.0,
        "fix_attempted": fix_attempted,
        "fix_rescued": fix_rescued,
        "fix_rescue_rate": round(fix_rescued / fix_attempted * 100, 1) if fix_attempted else 0.0,
        "avg_line_coverage_delta": avg_line,
        "avg_branch_coverage_delta": avg_branch,
        # 方法级主指标
        "avg_target_method_line_delta": avg_m_line,
        "avg_target_method_branch_delta": avg_m_branch,
        "methods_line_improved": improved_line,
        "methods_branch_improved": improved_branch,
        "failure_tag_count": tag_count,
    }


def _write_markdown(rows: List[Dict], out_path: Path, meta: Dict):
    s = _summary(rows)
    lines = [
        f"# 批量实验报告",
        "",
        f"- 生成时间: `{meta['timestamp']}`",
        f"- 模式: `{meta['mode']}`",
        f"- 候选数: {s['total']}",
        f"- 生成成功: **{s['gen_success']}/{s['total']}** ({s['gen_success_rate']}%)",
        f"- 编译成功: **{s['compile_success']}/{s['total']}** ({s['compile_success_rate']}%)",
        f"  - 首次直接成功（无任何前置改写）: {s['compile_success_initial']}",
        f"  - 确定性前置修复拾回: **{s['prefix_rescued']}/{s['prefix_attempted']}** ({s['prefix_rescue_rate']}%拾回率)",
        f"  - FixLoop 拾回: **{s['fix_rescued']}/{s['fix_attempted']}** ({s['fix_rescue_rate']}%拾回率)",
        f"- ★ **平均目标方法行覆盖率提升: {s['avg_target_method_line_delta']:+.2f}%** "
        f"（{s['methods_line_improved']}/{s['total']} 个方法有效提升）",
        f"- ★ **平均目标方法分支覆盖率提升: {s['avg_target_method_branch_delta']:+.2f}%** "
        f"（{s['methods_branch_improved']}/{s['total']} 个方法有效提升）",
        f"- 类整体行覆盖率平均提升（副指标）: {s['avg_line_coverage_delta']:+.2f}%",
        f"- 类整体分支覆盖率平均提升（副指标）: {s['avg_branch_coverage_delta']:+.2f}%",
        "",
        "> 主指标（★）衡量的是被测方法自身的覆盖率变化；类整体 Delta 在被测类已高覆盖的场景下会被稀释，仅作副指标。",
        "",
        "## 逐方法结果",
        "",
        "| # | 类 | 方法 | 生成 | 编译 | FixLoop | ★ 目标方法行覆盖率（前→后, Δ） | ★ 目标方法分支覆盖率（前→后, Δ） | 类整体行覆盖率（前→后, Δ） | 失败归因 |",
        "|---|----|------|------|------|---------|------------------------------|--------------------------------|-----------------------------|---------|",
    ]
    for i, r in enumerate(rows, 1):
        gen_icon = "✓" if r.get("gen_success") else "✗"
        cmp_icon = "✓" if r.get("compile_success") else "✗"
        # FixLoop 状态
        if not r.get("fix_attempted"):
            fix_icon = "-"
        elif r.get("fix_success"):
            fix_icon = "✓ 拾回"
        else:
            fix_icon = "✗ 失败"
        # 方法级主列（行 × 分支）
        m_bl = r.get("target_method_baseline_line_cov")
        m_nw = r.get("target_method_line_cov")
        m_d = r.get("target_method_line_cov_delta")
        m_line_str = (f"{m_bl:.1f}% → {m_nw:.1f}% ({m_d:+.1f}%)"
                      if m_bl is not None and m_nw is not None and m_d is not None else
                      (f"- → {m_nw:.1f}%" if m_nw is not None else "-"))
        mb_bl = r.get("target_method_baseline_branch_cov")
        mb_nw = r.get("target_method_branch_cov")
        mb_d = r.get("target_method_branch_cov_delta")
        m_branch_str = (f"{mb_bl:.1f}% → {mb_nw:.1f}% ({mb_d:+.1f}%)"
                        if mb_bl is not None and mb_nw is not None and mb_d is not None else
                        (f"- → {mb_nw:.1f}%" if mb_nw is not None else "-"))
        # 类整体行覆盖率（副列）
        bl = r.get("baseline_line_cov")
        nw = r.get("new_line_cov")
        d = r.get("line_cov_delta")
        line_str = (f"{bl:.1f}% → {nw:.1f}% ({d:+.1f}%)"
                    if bl is not None and nw is not None and d is not None else "-")
        tags = ", ".join(r.get("failure_tags") or []) if not r.get("compile_success") else "-"
        lines.append(
            f"| {i} | `{r['class']}` | `{r['method']}` | {gen_icon} | {cmp_icon} | {fix_icon} | "
            f"{m_line_str} | {m_branch_str} | {line_str} | {tags} |"
        )

    if s["failure_tag_count"]:
        lines += [
            "",
            "## 失败归因分布",
            "",
            "| 标签 | 次数 |",
            "|------|------|",
        ]
        for tag, cnt in sorted(s["failure_tag_count"].items(), key=lambda x: -x[1]):
            lines.append(f"| `{tag}` | {cnt} |")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[md] ✓ {out_path}")


# ══════════════ main ══════════════

def _is_private_signature(sig: str) -> bool:
    s = (sig or "").strip()
    return bool(re.search(r'\bprivate\b', s))


def _is_static_signature(sig: str) -> bool:
    s = (sig or "").strip()
    return bool(re.search(r'\bstatic\b', s))


def _read_source_for_class(full_class_name: str) -> str:
    top = (full_class_name or "").split("$")[0]
    if not top:
        return ""
    rel = top.replace(".", "/") + ".java"
    # 根据项目配置定位 src/main/java
    if _CFG:
        src = Path(_CFG.src_main_java) / rel
    else:
        # fallback：默认 gson 多模块结构
        src = Path(PROJECT_DIR) / "gson" / "src" / "main" / "java" / rel
    if not src.exists():
        return ""
    try:
        return src.read_text(encoding="utf-8")
    except Exception:
        return ""


def _class_only_has_private_constructors(full_class_name: str) -> bool:
    source = _read_source_for_class(full_class_name)
    if not source:
        return False

    simple_class = full_class_name.split(".")[-1].split("$")[0]
    cleaned = re.sub(r'/\*.*?\*/', ' ', source, flags=re.DOTALL)
    cleaned = re.sub(r'//.*', ' ', cleaned)

    ctor_pattern = re.compile(
        rf'(?m)^\s*(public|protected|private)?\s*{re.escape(simple_class)}\s*\('
    )
    visibilities = []
    for m in ctor_pattern.finditer(cleaned):
        visibilities.append((m.group(1) or "package").strip())

    if not visibilities:
        return False
    return all(v == "private" for v in visibilities)


def load_specs(
    yaml_path: Path,
    limit: Optional[int] = None,
    filter_unrunnable: bool = False,
) -> List[MethodSpec]:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw_list = data.get("methods") or []

    if not filter_unrunnable:
        specs = [MethodSpec(r) for r in raw_list]
        if limit:
            specs = specs[:limit]
        return specs

    filtered: List[Dict] = []
    dropped_private = 0
    dropped_private_ctor = 0
    private_ctor_cache: Dict[str, bool] = {}

    for r in raw_list:
        sig = r.get("method_signature", "")
        cls = r.get("full_class_name", "")

        # 可选兜底：老 methods.yaml 可能包含 private 方法，运行前过滤。
        if _is_private_signature(sig):
            dropped_private += 1
            continue

        # 可选兜底：类若仅有 private 构造器，且目标是实例方法，通常会触发 private access 编译失败。
        is_static = _is_static_signature(sig)
        if not is_static:
            if cls not in private_ctor_cache:
                private_ctor_cache[cls] = _class_only_has_private_constructors(cls)
            if private_ctor_cache[cls]:
                dropped_private_ctor += 1
                continue

        filtered.append(r)

    if dropped_private:
        print(f"[spec] 过滤掉 {dropped_private} 个 private 方法候选（来自旧 methods.yaml）")
    if dropped_private_ctor:
        print(f"[spec] 过滤掉 {dropped_private_ctor} 个仅 private 构造器类的实例方法候选")

    specs = [MethodSpec(r) for r in filtered]
    if limit:
        specs = specs[:limit]
    return specs


async def _async_main(args):
    if not METHODS_YAML.exists():
        print(f"✗ 找不到 {METHODS_YAML}，请先运行：python experiments/pick_methods.py")
        return 1

    specs = load_specs(
        METHODS_YAML,
        limit=args.limit,
        filter_unrunnable=args.filter_unrunnable,
    )
    if not specs:
        print("✗ methods.yaml 为空")
        return 1

    print("=" * 70)
    print(f"批量实验：{len(specs)} 个方法 | mode={'one_shot' if args.one_shot else 'two_step'}"
          f" | LLM 并发={args.llm_concurrency} | fix_retries={args.fix_retries}")
    print("=" * 70)
    for s in specs:
        print(f"  {s.id}  {s.simple_class_name}.{s.method_name}  "
              f"(行数={s.total_lines}, 覆盖率={s.line_coverage}%)")
    print()

    t_total = time.time()

    # ── Phase 1: 并行 LLM 生成 ──
    _phase_banner("Phase 1 / STAGE 1: GEN — 并行 LLM 生成（analyze + generate）")
    t0 = time.time()
    gen_results = await phase1_generate_all(
        specs, concurrency=args.llm_concurrency, one_shot=args.one_shot)
    print(f"[Phase1] 完成，耗时 {time.time() - t0:.1f}s；"
          f"成功 {sum(1 for g in gen_results if g['gen_success'])}/{len(gen_results)}")

    # ── Phase 2: 串行评估 ──
    _phase_banner("Phase 2 / STAGE 2-4: PREFIX → EVAL → FIXLOOP（串行，必须独占 Maven 目录）")
    t0 = time.time()
    eval_results = await phase2_evaluate_all(specs, gen_results, fix_retries=args.fix_retries)
    print(f"[Phase2] 完成，耗时 {time.time() - t0:.1f}s；"
          f"编译成功 {sum(1 for e in eval_results if e['compile_success'])}/{len(eval_results)}"
          f"（前置修复拾回 {sum(1 for e in eval_results if e.get('compile_success_stage') == 'after_prefix')}，"
          f"FixLoop 拾回 {sum(1 for e in eval_results if e.get('fix_success'))}）")

    # ── Phase 3 / STAGE 5: REPORT ──
    _phase_banner("Phase 3 / STAGE 5: REPORT — 失败归因 + 汇总 JSON / Markdown")
    rows = _merge_results(specs, gen_results, eval_results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.suffix}" if args.suffix else ""
    mode_tag = "oneshot" if args.one_shot else "twostep"

    json_path = RESULTS_DIR / f"experiment_summary_{mode_tag}_{timestamp}{suffix}.json"
    md_path = RESULTS_DIR / f"experiment_summary_{mode_tag}_{timestamp}{suffix}.md"

    meta = {
        "timestamp": timestamp,
        "mode": "one_shot" if args.one_shot else "two_step",
        "llm_concurrency": args.llm_concurrency,
        "total_methods": len(specs),
        "total_duration_s": round(time.time() - t_total, 1),
    }
    _write_json(rows, json_path, meta)
    _write_markdown(rows, md_path, meta)

    # 终端打印 summary
    s = _summary(rows)
    print()
    print("=" * 70)
    print(f"汇总")
    print("=" * 70)
    print(f"  生成成功率: {s['gen_success']}/{s['total']} ({s['gen_success_rate']}%)")
    print(f"  编译成功率: {s['compile_success']}/{s['total']} ({s['compile_success_rate']}%)")
    print(f"    - 首次直接成功（无任何前置改写）: {s['compile_success_initial']}")
    print(f"    - 确定性前置修复拾回: {s['prefix_rescued']}/{s['prefix_attempted']} ({s['prefix_rescue_rate']}% 拾回率)")
    print(f"    - FixLoop 拾回: {s['fix_rescued']}/{s['fix_attempted']} ({s['fix_rescue_rate']}% 拾回率)")
    print(f"  ★ 平均目标方法行覆盖率提升:   {s['avg_target_method_line_delta']:+.2f}%  ({s['methods_line_improved']}/{s['total']} 个方法有效提升)")
    print(f"  ★ 平均目标方法分支覆盖率提升: {s['avg_target_method_branch_delta']:+.2f}%  ({s['methods_branch_improved']}/{s['total']} 个方法有效提升)")
    print(f"  类整体行覆盖率提升（副指标）:   {s['avg_line_coverage_delta']:+.2f}%")
    print(f"  类整体分支覆盖率提升（副指标）: {s['avg_branch_coverage_delta']:+.2f}%")
    if s["failure_tag_count"]:
        print(f"  失败归因:")
        for t, n in sorted(s["failure_tag_count"].items(), key=lambda x: -x[1]):
            print(f"    - {t}: {n}")
    print(f"  报告: {md_path}")
    print(f"  总耗时: {meta['total_duration_s']}s")
    return 0


def main():
    global _CFG, PROJECT_DIR, RAG_INDEX_PATH, RAG_TEST_DIR

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None,
                        help="目标项目名（来自 data/projects.yaml），省略则用 active 字段")
    parser.add_argument("--list-projects", action="store_true",
                        help="列出所有可用的项目并退出")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个方法")
    parser.add_argument("--llm-concurrency", type=int, default=4, help="LLM 并发数")
    parser.add_argument("--one-shot", action="store_true", help="一步式生成（消融实验）")
    parser.add_argument("--suffix", default="", help="输出文件名后缀")
    parser.add_argument("--fix-retries", type=int, default=3,
                        help="FixLoop 最大重试轮数（0 = 禁用 FixLoop）")
    parser.add_argument(
        "--filter-unrunnable",
        action="store_true",
        help="运行前过滤明显不可运行样本（private 方法/仅 private 构造器实例方法）",
    )
    args = parser.parse_args()

    if args.list_projects:
        for n in list_projects():
            print(n)
        return 0

    # 加载项目配置 → 写入全局
    _CFG = load_project(args.project)
    PROJECT_DIR = _CFG.project_dir
    RAG_INDEX_PATH = _CFG.rag_index
    RAG_TEST_DIR = _CFG.src_test_java
    print(f"[project] 使用项目: {_CFG.name}  ({PROJECT_DIR})"
          + (f"  module={_CFG.module_name}" if _CFG.module_name else "  (单模块)"))

    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
