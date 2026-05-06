#!/usr/bin/env python3
"""
run_zeroshot_baseline.py —— 论文对照实验：Zero-shot LLM Baseline
────────────────────────────────────────────────────────────────────────
定义（严格对齐论文 5.4 节 Zero-shot Baseline）：

    "代码给 AI，问 AI，AI 给代码，然后贴过来编译运行"

具体地：
  • 输入：与主实验完全同一份冻结方法集（methods.yaml.<project>.frozen_for_paper）
  • Prompt：极简 —— 只给【类全名 + 方法签名 + 方法源码】，要求生成完整 JUnit4 测试类
  • 不做：
      - ❌ 不调用 AgenticRAG / CodeRAG（没有任何上下文注入）
      - ❌ 不做两步式生成（不分 skeleton + per-method，直接一次问一次答）
      - ❌ 不做 deterministic prefix（不运行 import/rule 前置修复）
      - ❌ 不跑 FixLoop（编译失败就是失败，不再请 LLM 修）
  • 做：
      - ✓ 单次 chat() 生成整个测试类
      - ✓ 复用 TestEvaluator 跑 baseline / 编译 / mvn test + JaCoCo / 覆盖率对比
      - ✓ 输出与主实验格式一致的 experiment_summary_zeroshot_<ts>_<suffix>.json

用法：
    python experiments/run_zeroshot_baseline.py \
        --project gson \
        --methods-yaml experiments/methods.yaml.gson.frozen_for_paper \
        --suffix Z1_gson

    # 快速冒烟（只跑前 2 个方法）：
    python experiments/run_zeroshot_baseline.py --project gson --limit 2 --suffix smoke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# 允许以包方式导入本仓库模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.project_config import load_project, ProjectConfig  # noqa: E402
from core import token_meter  # noqa: E402
from evaluation.evaluator import TestEvaluator  # noqa: E402
from llm.llm import chat  # noqa: E402


# ══════════════ Prompt 模板（刻意保持极简） ══════════════
#
# 设计理念（对标 ChatTester / ChatGPT baseline in ChatUniTest 论文 Table 2）：
#   不提供 RAG 上下文、不提供类 skeleton、不提供构造参数提示。
#   LLM 能看到的信息 ≈ "一位人类工程师只从 IDE 里 Ctrl+C 这一个方法"时拥有的信息。
# 公平起见（宽松版）：除方法体外，还告知【完整类全名 + 方法签名 + 所在包名】，
#   避免 LLM 因不知道类名而生成完全无法编译的 import。
#
ZEROSHOT_SYSTEM = (
    "你是 Java 单元测试专家。请直接根据下面给出的方法代码，"
    "生成一个完整可编译的 JUnit 4 测试类。"
)

ZEROSHOT_PROMPT_TEMPLATE = """请为下面这个 Java 方法生成 JUnit 4 单元测试类。

【被测类全名】 {full_class}
【被测方法签名】 {method_sig}

【方法源码】
```java
{method_code}
```

要求：
1. 只输出一个 ```java ... ``` 代码块，里面是完整的可编译测试类；不要多余解释。
2. 测试类放在与被测类同一个包下，类名为 `{simple_class}Test_Zeroshot`。
3. 使用 JUnit 4：`import org.junit.Test;`、`import static org.junit.Assert.*;`；
   禁止使用 AssertJ / Google Truth / JUnit 5 / `assertThrows`。
4. 异常断言用 try-catch + `fail()` 模式。
5. 不要使用 Mockito、PowerMock 等第三方 mock 框架。
"""


# ══════════════ 工具函数 ══════════════


_JAVA_BLOCK_RE = re.compile(r"```(?:java)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_java_code(text: str) -> str:
    """从 LLM 回复里取第一个 ```java ... ``` 代码块；没找到就原样返回去掉前后空白。"""
    if not text:
        return ""
    m = _JAVA_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    # 退化：整段当作代码
    return text.strip()


def ensure_package_decl(code: str, package: str) -> str:
    """LLM 有时会漏掉 package 行；补上，且确保只出现一次。"""
    if not package:
        return code
    if re.search(r"^\s*package\s+" + re.escape(package) + r"\s*;", code, re.MULTILINE):
        return code
    # 删掉其他错误的 package 行
    code = re.sub(r"^\s*package\s+[\w\.]+\s*;\s*\n?", "", code, count=1, flags=re.MULTILINE)
    return f"package {package};\n\n{code}"


def load_methods(yaml_path: Path, limit: Optional[int] = None) -> List[Dict]:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    methods = data.get("methods") or []
    if limit:
        methods = methods[:limit]
    return methods


# ══════════════ 主流程 ══════════════


async def generate_one(spec: Dict) -> Dict:
    """对单个方法调 LLM 一次，返回生成结果字典。"""
    full_class = spec["full_class_name"]
    simple_class = spec["simple_class_name"]
    method_sig = spec["method_signature"]
    method_code = spec["method_code"]

    prompt = ZEROSHOT_PROMPT_TEMPLATE.format(
        full_class=full_class,
        simple_class=simple_class,
        method_sig=method_sig,
        method_code=method_code,
    )

    # 按方法 id 记账，phase 叫 "zeroshot_generate"
    tok = token_meter.set_scope(spec["id"], "zeroshot_generate")
    t0 = time.time()
    try:
        try:
            raw = await chat(prompt, system=ZEROSHOT_SYSTEM)
        except Exception as e:
            return {
                "id": spec["id"],
                "full_class": full_class,
                "gen_success": False,
                "gen_error": f"{type(e).__name__}: {e}",
                "llm_raw": "",
                "test_code": "",
                "duration_s": round(time.time() - t0, 2),
            }
    finally:
        token_meter.reset_scope(tok)

    code = extract_java_code(raw)
    if not code:
        return {
            "id": spec["id"],
            "full_class": full_class,
            "gen_success": False,
            "gen_error": "empty_response",
            "llm_raw": raw,
            "test_code": "",
            "duration_s": round(time.time() - t0, 2),
        }

    # 推导包名
    package = ".".join(full_class.split(".")[:-1])
    code = ensure_package_decl(code, package)

    return {
        "id": spec["id"],
        "full_class": full_class,
        "gen_success": True,
        "llm_raw": raw,
        "test_code": code,
        "duration_s": round(time.time() - t0, 2),
    }


def evaluate_one(spec: Dict, gen: Dict, evaluator: TestEvaluator, out_dir: Path) -> Dict:
    """编译 + 跑测试 + 拿覆盖率。不跑 FixLoop，不做 prefix。"""
    record: Dict = {
        "id": spec["id"],
        "full_class_name": spec["full_class_name"],
        "simple_class_name": spec["simple_class_name"],
        "method_name": spec["method_name"],
        "method_signature": spec["method_signature"],
        "gen_success": gen["gen_success"],
        "gen_error": gen.get("gen_error"),
        "gen_duration_s": gen.get("duration_s"),
        "compile_success": False,
        "test_run": False,
        "baseline_line_cov": None,
        "baseline_branch_cov": None,
        "new_line_cov": None,
        "new_branch_cov": None,
        "line_cov_delta": None,
        "branch_cov_delta": None,
        "target_method_line_cov_before": None,
        "target_method_line_cov_after": None,
        "target_method_line_delta": None,
        "target_method_branch_cov_before": None,
        "target_method_branch_cov_after": None,
        "target_method_branch_delta": None,
        "errors": [],
    }

    if not gen["gen_success"]:
        record["errors"].append(gen.get("gen_error", "gen_failed"))
        return record

    # 写临时 .java 文件
    tmp_dir = Path("/tmp/zeroshot_generated")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    test_class_simple = f"{spec['simple_class_name']}Test_Zeroshot"
    test_file = tmp_dir / f"{test_class_simple}_{spec['id']}.java"
    test_file.write_text(gen["test_code"], encoding="utf-8")

    # 同时把原始产物归档到 out_dir
    (out_dir / f"{spec['id']}.java").write_text(gen["test_code"], encoding="utf-8")
    (out_dir / f"{spec['id']}.raw.txt").write_text(gen.get("llm_raw", ""), encoding="utf-8")

    package = ".".join(spec["full_class_name"].split(".")[:-1])
    test_class_full = f"{package}.{test_class_simple}" if package else test_class_simple

    try:
        report = evaluator.evaluate(
            test_file=str(test_file),
            test_class=test_class_full,
            target_class=spec["full_class_name"],
            target_method=spec["method_name"],
        )
    except Exception as e:
        record["errors"].append(f"evaluate_exception: {type(e).__name__}: {e}")
        return record

    record["compile_success"] = bool(report.compilation_success)
    record["errors"].extend(report.errors or [])

    if report.baseline_coverage is not None:
        record["baseline_line_cov"] = round(report.baseline_coverage.line_coverage, 2)
        record["baseline_branch_cov"] = round(report.baseline_coverage.branch_coverage, 2)

    if report.coverage is not None:
        record["test_run"] = True
        record["new_line_cov"] = round(report.coverage.line_coverage, 2)
        record["new_branch_cov"] = round(report.coverage.branch_coverage, 2)
        if report.baseline_coverage is not None:
            record["line_cov_delta"] = round(
                report.coverage.line_coverage - report.baseline_coverage.line_coverage, 2
            )
            record["branch_cov_delta"] = round(
                report.coverage.branch_coverage - report.baseline_coverage.branch_coverage, 2
            )

    # 方法级覆盖率：从 method_coverages 里找 target_method
    def _find_method_cov(cov_report) -> Optional[object]:
        if cov_report is None:
            return None
        mlist = getattr(cov_report, "method_coverages", None) or []
        for mc in mlist:
            if mc.method_name == spec["method_name"]:
                return mc
        return None

    before_mc = _find_method_cov(report.baseline_coverage)
    after_mc = _find_method_cov(report.coverage)
    if before_mc is not None:
        record["target_method_line_cov_before"] = round(before_mc.line_coverage, 2)
        record["target_method_branch_cov_before"] = round(before_mc.branch_coverage, 2)
    if after_mc is not None:
        record["target_method_line_cov_after"] = round(after_mc.line_coverage, 2)
        record["target_method_branch_cov_after"] = round(after_mc.branch_coverage, 2)
    if before_mc is not None and after_mc is not None:
        record["target_method_line_delta"] = round(
            after_mc.line_coverage - before_mc.line_coverage, 2
        )
        record["target_method_branch_delta"] = round(
            after_mc.branch_coverage - before_mc.branch_coverage, 2
        )

    # token 汇总（本方法）
    snap = token_meter.snapshot(spec["id"])
    record["tokens"] = {
        "prompt": snap.get("prompt", 0),
        "completion": snap.get("completion", 0),
        "total": snap.get("total", 0),
        "calls": snap.get("calls", 0),
    }

    return record


def summarize(records: List[Dict]) -> Dict:
    total = len(records)
    gen_ok = sum(1 for r in records if r["gen_success"])
    compile_ok = sum(1 for r in records if r["compile_success"])
    test_ran = sum(1 for r in records if r["test_run"])

    def _avg(key: str) -> Optional[float]:
        vals = [r[key] for r in records if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _count_positive(key: str) -> int:
        return sum(1 for r in records if (r.get(key) or 0) > 0)

    total_prompt = sum((r.get("tokens") or {}).get("prompt", 0) for r in records)
    total_completion = sum((r.get("tokens") or {}).get("completion", 0) for r in records)
    total_calls = sum((r.get("tokens") or {}).get("calls", 0) for r in records)

    return {
        "total": total,
        "gen_success": gen_ok,
        "gen_success_rate": round(gen_ok / total * 100, 1) if total else 0.0,
        "compile_success": compile_ok,
        "compile_success_rate": round(compile_ok / total * 100, 1) if total else 0.0,
        "test_run": test_ran,
        "test_run_rate": round(test_ran / total * 100, 1) if total else 0.0,
        "avg_line_coverage_delta": _avg("line_cov_delta"),
        "avg_branch_coverage_delta": _avg("branch_cov_delta"),
        "avg_target_method_line_delta": _avg("target_method_line_delta"),
        "avg_target_method_branch_delta": _avg("target_method_branch_delta"),
        "methods_line_improved": _count_positive("line_cov_delta"),
        "methods_branch_improved": _count_positive("branch_cov_delta"),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_llm_calls": total_calls,
        "avg_tokens_per_method": round((total_prompt + total_completion) / total, 1) if total else 0.0,
    }


def render_markdown(summary: Dict, records: List[Dict], meta: Dict) -> str:
    lines = []
    lines.append(f"# Zero-shot Baseline 实验报告  ({meta['timestamp']})\n")
    lines.append(f"- project: **{meta['project']}**")
    lines.append(f"- methods yaml: `{meta['methods_yaml']}`")
    lines.append(f"- suffix: `{meta.get('suffix', '')}`")
    lines.append(f"- total duration: **{meta['total_duration_s']:.1f}s**\n")

    lines.append("## 汇总指标\n")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 方法总数 | {summary['total']} |")
    lines.append(f"| 生成成功率 | {summary['gen_success']}/{summary['total']} = {summary['gen_success_rate']}% |")
    lines.append(f"| 编译成功率 | {summary['compile_success']}/{summary['total']} = {summary['compile_success_rate']}% |")
    lines.append(f"| 测试运行率 | {summary['test_run']}/{summary['total']} = {summary['test_run_rate']}% |")
    lines.append(f"| 平均类级行覆盖率 Δ | {summary['avg_line_coverage_delta']} |")
    lines.append(f"| 平均类级分支覆盖率 Δ | {summary['avg_branch_coverage_delta']} |")
    lines.append(f"| 平均方法级行覆盖率 Δ | {summary['avg_target_method_line_delta']} |")
    lines.append(f"| 平均方法级分支覆盖率 Δ | {summary['avg_target_method_branch_delta']} |")
    lines.append(f"| 覆盖提升方法数 (行) | {summary['methods_line_improved']}/{summary['total']} |")
    lines.append(f"| 总 tokens | {summary['total_tokens']} (prompt {summary['total_prompt_tokens']} / completion {summary['total_completion_tokens']}) |")
    lines.append(f"| 平均 tokens/方法 | {summary['avg_tokens_per_method']} |\n")

    lines.append("## 方法级明细\n")
    lines.append("| id | 类 | 方法 | 编译 | 行Δ | 分支Δ | 方法行Δ | tokens |")
    lines.append("| --- | --- | --- | :---: | :---: | :---: | :---: | :---: |")
    for r in records:
        tok = (r.get("tokens") or {}).get("total", 0)
        lines.append(
            f"| {r['id']} | {r['simple_class_name']} | `{r['method_name']}` | "
            f"{'✓' if r['compile_success'] else '✗'} | "
            f"{r.get('line_cov_delta')} | {r.get('branch_cov_delta')} | "
            f"{r.get('target_method_line_delta')} | {tok} |"
        )
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> int:
    project_cfg: ProjectConfig = load_project(args.project)
    print(f"[zeroshot] project = {project_cfg.name}  dir = {project_cfg.project_dir}")

    methods_yaml = Path(args.methods_yaml).resolve()
    if not methods_yaml.exists():
        print(f"✗ methods yaml 不存在: {methods_yaml}", file=sys.stderr)
        return 2
    specs = load_methods(methods_yaml, limit=args.limit)
    if not specs:
        print(f"✗ 方法列表为空: {methods_yaml}", file=sys.stderr)
        return 2
    print(f"[zeroshot] 共 {len(specs)} 个方法待跑")

    # 为本次实验建产物目录
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = args.suffix or f"zeroshot_{project_cfg.name}"
    out_dir = ROOT / "experiment_results" / "paper_final" / "artifacts" / f"zeroshot_{ts}_{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[zeroshot] artifact dir = {out_dir}")

    # —— Phase 1：并发生成（有并发限制，避免撑爆 API）——
    sem = asyncio.Semaphore(args.llm_concurrency)

    async def _gen_one_with_sem(s: Dict) -> Dict:
        async with sem:
            print(f"[zeroshot] GEN  {s['id']}  {s['simple_class_name']}.{s['method_name']}")
            return await generate_one(s)

    t0 = time.time()
    gen_results: List[Dict] = await asyncio.gather(*[_gen_one_with_sem(s) for s in specs])
    t_gen = time.time() - t0
    print(f"[zeroshot] Phase1 生成完成，用时 {t_gen:.1f}s")

    # —— Phase 2：串行评估（必须串行，mvn 不能并发）——
    # ★ 关键：必须显式传 jacoco_home，否则会用 evaluator 里一个不存在的默认路径
    #   (/home/juu/unittest/lib/jacoco-0.8.14)，导致 -javaagent 注入失败、mvn 子进程
    #   JVM 直接启动崩溃，表现为 baseline_coverage / 测试运行全部失败、覆盖率为空。
    evaluator = TestEvaluator(
        project_dir=project_cfg.project_dir,
        jacoco_home="/data/workspace/unittest/lib",
        module_name=project_cfg.module_name,
        java_home=project_cfg.java_home,
        surefire_arglines=project_cfg.surefire_arglines,
        mvn_extra_args=project_cfg.mvn_extra_args,
    )

    records: List[Dict] = []
    gen_by_id = {g["id"]: g for g in gen_results}
    t_eval_start = time.time()
    for i, spec in enumerate(specs, 1):
        print(f"\n[zeroshot] EVAL [{i}/{len(specs)}] {spec['id']}  "
              f"{spec['simple_class_name']}.{spec['method_name']}")
        rec = evaluate_one(spec, gen_by_id[spec["id"]], evaluator, out_dir)
        records.append(rec)
        print(f"  → compile={rec['compile_success']}  "
              f"lineΔ={rec.get('line_cov_delta')}  "
              f"branchΔ={rec.get('branch_cov_delta')}")

    t_eval = time.time() - t_eval_start
    total_dur = time.time() - t0

    summary = summarize(records)
    meta = {
        "timestamp": ts,
        "mode": "zeroshot",
        "project": project_cfg.name,
        "methods_yaml": str(methods_yaml),
        "suffix": suffix,
        "llm_concurrency": args.llm_concurrency,
        "phase1_duration_s": round(t_gen, 1),
        "phase2_duration_s": round(t_eval, 1),
        "total_duration_s": round(total_dur, 1),
    }

    # 落盘 JSON + Markdown，与主实验一致的命名
    out_json = ROOT / "experiment_results" / f"experiment_summary_zeroshot_{ts}_{suffix}.json"
    out_md = ROOT / "experiment_results" / f"experiment_summary_zeroshot_{ts}_{suffix}.md"

    out_json.write_text(
        json.dumps({"meta": meta, "summary": summary, "results": records},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out_md.write_text(render_markdown(summary, records, meta), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"✅ Zero-shot Baseline 完成  | 方法={summary['total']}  "
          f"编译={summary['compile_success_rate']}%  "
          f"行Δ={summary['avg_line_coverage_delta']}  "
          f"分支Δ={summary['avg_branch_coverage_delta']}")
    print(f"   JSON: {out_json}")
    print(f"   MD  : {out_md}")
    print(f"   产物: {out_dir}")
    print("=" * 70)
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zero-shot LLM baseline 实验")
    p.add_argument("--project", default=None,
                   help="项目名（如 gson / commons-lang），省略用 active")
    p.add_argument("--methods-yaml", default=None,
                   help="方法集 YAML；省略时按 project 自动推导 "
                        "experiments/methods.yaml.<project>.frozen_for_paper")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 个方法（调试用）")
    p.add_argument("--llm-concurrency", type=int, default=4, help="LLM 并发数")
    p.add_argument("--suffix", default="", help="输出文件名后缀（如 Z1_gson）")
    args = p.parse_args(argv)

    if args.methods_yaml is None:
        # 基于 project 自动推导冻结 yaml 路径
        proj = args.project or os.environ.get("UTG_PROJECT") or "gson"
        # commons-lang 的冻结文件用 "cl" 别名
        short = "cl" if proj.startswith("commons-lang") else proj
        candidate = ROOT / "experiments" / f"methods.yaml.{short}.frozen_for_paper"
        if not candidate.exists():
            # 兜底到 live yaml
            candidate = ROOT / "experiments" / "methods.yaml"
        args.methods_yaml = str(candidate)

    return args


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
