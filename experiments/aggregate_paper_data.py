#!/usr/bin/env python3
"""
experiments/aggregate_paper_data.py
────────────────────────────────────────────────────────────────────────
扫描 paper_final/summaries/ 目录下所有场次的 summary JSON，
聚合输出 6 张论文用 CSV + 1 份 Markdown 报告到 paper_final/aggregated/。

核心能力：
  1. 场次识别：根据子目录名（E1_gson / E1_gson_run2 / Z1_cl）自动归类
  2. 多轮聚合：同一"基础场次"（如 E1_gson 的 run1/run2/run3）自动算 mean±std
  3. 正交消融对比：E1 vs E2/E3/E4/E5 自动生成对比行
  4. 外部基线对比：Z1_* vs E1_* 自动生成对比
  5. baseline 一致性校验：所有场次在同一方法上的 baseline 必须相同（否则配对失效）

输出文件：
  aggregated/
      main_table.csv              # 主实验：E1 多轮 mean±std + 消融对照
      ablation_table.csv          # 消融实验：E1 vs E2/E3/E4/E5 逐项
      zeroshot_comparison.csv     # ZeroShot vs E1 外部对照
      per_method_detail.csv       # 每方法每场次所有指标（答辩抽查用）
      token_cost.csv              # token 消耗按 phase 拆分
      baseline_consistency.csv    # baseline 覆盖率一致性校验
      FINAL_REPORT.md             # 汇总 Markdown 报告

用法：
  python experiments/aggregate_paper_data.py
  python experiments/aggregate_paper_data.py --only gson     # 只聚合 gson
"""

from __future__ import annotations
import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
SUMMARIES = ROOT / "experiment_results" / "paper_final" / "summaries"
OUT = ROOT / "experiment_results" / "paper_final" / "aggregated"

# 场次名 → 基础场次 + run 编号
# E1_gson          -> ("E1_gson", 1)
# E1_gson_run2     -> ("E1_gson", 2)
# E1_gson_run3     -> ("E1_gson", 3)
# Z1_gson          -> ("Z1_gson", 1)
SCENE_RE = re.compile(r"^(?P<base>[A-Z]\d+_[a-z0-9\-]+?)(?:_run(?P<n>\d+))?$")

# 场次描述（用于报告可读性）
SCENE_DESC = {
    "E1": "Full System (two-step + RAG + Prefix + FixLoop)",
    "E2": "Ablation: One-shot generation (no two-step)",
    "E3": "Ablation: FixLoop disabled (--fix-retries 0)",
    "E4": "Ablation: Prefix disabled (--no-prefix)",
    "E5": "Ablation: RAG disabled (--no-rag)",
    "Z1": "Zero-shot LLM Baseline (no RAG / no two-step / no prefix / no FixLoop)",
}

# 项目标签
PROJ_DESC = {
    "gson": "gson",
    "cl": "commons-lang",
    "commons-lang": "commons-lang",
}


def mean_std(xs: list[float]) -> tuple[float, float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return (float("nan"), 0.0)
    if len(xs) == 1:
        return (xs[0], 0.0)
    return (statistics.mean(xs), statistics.pstdev(xs))


def fmt_ms(m: float, s: float, digits: int = 2) -> str:
    if math.isnan(m):
        return "-"
    return f"{m:.{digits}f} ± {s:.{digits}f}"


def parse_scene(name: str):
    """E1_gson -> ('E1', 'gson', 'E1_gson', 1)"""
    m = SCENE_RE.match(name)
    if not m:
        return None
    base = m.group("base")
    run = int(m.group("n") or 1)
    # 拆 exp_tag 和 project
    parts = base.split("_", 1)
    if len(parts) != 2:
        return None
    exp_tag, proj = parts
    return {"exp_tag": exp_tag, "project": proj, "base": base, "run": run}


def load_latest_summary(scene_dir: Path) -> dict | None:
    """加载场次目录下最新时间戳的 JSON"""
    jsons = sorted(scene_dir.glob("*.json"))
    if not jsons:
        return None
    latest = jsons[-1]  # 时间戳命名，字典序=时间序
    with latest.open(encoding="utf-8") as f:
        return {"path": latest, "timestamp": latest.stem, **json.load(f)}


def scan_summaries(only_proj: str | None = None) -> dict:
    """扫描并按 base 聚合"""
    scenes_by_base: dict[str, list] = defaultdict(list)
    for sub in sorted(SUMMARIES.iterdir()):
        if not sub.is_dir():
            continue
        info = parse_scene(sub.name)
        if info is None:
            continue
        if only_proj and info["project"] not in (only_proj, PROJ_DESC.get(only_proj, "")):
            continue
        payload = load_latest_summary(sub)
        if payload is None:
            continue
        payload["scene_name"] = sub.name
        payload.update(info)
        scenes_by_base[info["base"]].append(payload)

    # 每组按 run 排序
    for base in scenes_by_base:
        scenes_by_base[base].sort(key=lambda x: x["run"])
    return scenes_by_base


# ── 表 1：主实验（E1 多轮 mean±std） ─────────────────────────────────
MAIN_METRIC_KEYS = [
    ("gen_success_rate", "GenSuccess%"),
    ("compile_success_rate", "Compile%"),
    ("prefix_rescue_rate", "PrefixRescue%"),
    ("fix_rescue_rate", "FixRescue%"),
    ("avg_line_coverage_delta", "ΔLineCov%"),
    ("avg_branch_coverage_delta", "ΔBranchCov%"),
    ("avg_target_method_line_delta", "ΔMethodLine%"),
    ("avg_target_method_branch_delta", "ΔMethodBranch%"),
    ("avg_tokens_per_method", "Tokens/method"),
]


def write_main_table(scenes_by_base: dict) -> Path:
    """主实验表：E1_gson 和 E1_cl 的 mean±std"""
    out = OUT / "main_table.csv"
    rows = []
    for base, runs in scenes_by_base.items():
        if not base.startswith("E1_"):
            continue
        row = {
            "scene": base,
            "project": PROJ_DESC.get(runs[0]["project"], runs[0]["project"]),
            "n_runs": len(runs),
        }
        for k, label in MAIN_METRIC_KEYS:
            vs = [r["summary"].get(k) for r in runs]
            m, s = mean_std(vs)
            row[label] = fmt_ms(m, s, 2 if "%" in label else 0)
            row[f"{label}_mean"] = round(m, 3) if not math.isnan(m) else ""
            row[f"{label}_std"] = round(s, 3) if not math.isnan(s) else ""
        rows.append(row)

    if rows:
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return out


# ── 表 2：消融实验（E1 vs E2/E3/E4/E5） ───────────────────────────────
def write_ablation_table(scenes_by_base: dict) -> Path:
    out = OUT / "ablation_table.csv"
    rows = []
    for proj in ("gson", "cl"):
        e1_runs = scenes_by_base.get(f"E1_{proj}", [])
        if not e1_runs:
            continue
        for exp_tag in ("E1", "E2", "E3", "E4", "E5"):
            base = f"{exp_tag}_{proj}"
            runs = scenes_by_base.get(base, [])
            if not runs:
                continue
            row = {
                "project": PROJ_DESC.get(proj, proj),
                "exp": exp_tag,
                "desc": SCENE_DESC.get(exp_tag, ""),
                "n_runs": len(runs),
                "scene": base,
            }
            for k, label in MAIN_METRIC_KEYS:
                vs = [r["summary"].get(k) for r in runs]
                m, s = mean_std(vs)
                row[label] = fmt_ms(m, s, 2 if "%" in label else 0)
            # 计算相对 E1 的绝对差（只在覆盖率 Δ 上算）
            if exp_tag != "E1":
                e1_line = mean_std([r["summary"].get("avg_line_coverage_delta") for r in e1_runs])[0]
                cur_line = mean_std([r["summary"].get("avg_line_coverage_delta") for r in runs])[0]
                if not math.isnan(e1_line) and not math.isnan(cur_line):
                    row["Δvs E1 (ΔLineCov%)"] = f"{cur_line - e1_line:+.2f}"
                else:
                    row["Δvs E1 (ΔLineCov%)"] = ""
            else:
                row["Δvs E1 (ΔLineCov%)"] = "baseline"
            rows.append(row)

    if rows:
        # 收齐所有行的 key，统一列头
        all_keys = []
        for r in rows:
            for k in r:
                if k not in all_keys:
                    all_keys.append(k)
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in all_keys})
    return out


# ── 表 3：ZeroShot 对照 ───────────────────────────────────────────────
def write_zeroshot_table(scenes_by_base: dict) -> Path:
    out = OUT / "zeroshot_comparison.csv"
    rows = []
    for proj in ("gson", "cl"):
        z = scenes_by_base.get(f"Z1_{proj}", [])
        e1 = scenes_by_base.get(f"E1_{proj}", [])
        if not z and not e1:
            continue
        for base_name, runs in [(f"Z1_{proj}", z), (f"E1_{proj}", e1)]:
            if not runs:
                rows.append({"project": PROJ_DESC.get(proj, proj), "scene": base_name, "status": "MISSING"})
                continue
            row = {"project": PROJ_DESC.get(proj, proj), "scene": base_name, "status": "OK",
                   "n_runs": len(runs)}
            for k, label in MAIN_METRIC_KEYS:
                vs = [r["summary"].get(k) for r in runs]
                m, s = mean_std(vs)
                row[label] = fmt_ms(m, s, 2 if "%" in label else 0)
            rows.append(row)

    if rows:
        with out.open("w", encoding="utf-8", newline="") as f:
            keys = sorted({k for r in rows for k in r})
            # 维持合理列顺序
            head = ["project", "scene", "status", "n_runs"] + [l for _, l in MAIN_METRIC_KEYS]
            head = [h for h in head if h in keys] + [k for k in keys if k not in head]
            w = csv.DictWriter(f, fieldnames=head)
            w.writeheader()
            for r in rows:
                w.writerow({h: r.get(h, "") for h in head})
    return out


# ── 表 4：方法级明细 ────────────────────────────────────────────────
METHOD_COLS = [
    "scene", "run", "method_id", "full_class", "method",
    "gen_success", "compile_success", "compile_stage",
    "prefix_rescued", "fix_success",
    "baseline_line_cov", "new_line_cov", "line_cov_delta",
    "baseline_branch_cov", "new_branch_cov", "branch_cov_delta",
    "target_method_line_cov_delta", "target_method_branch_cov_delta",
    "test_cases_count", "methods_generated",
    "failure_tags", "gen_duration_s", "eval_duration_s",
]


def write_per_method_detail(scenes_by_base: dict) -> Path:
    out = OUT / "per_method_detail.csv"
    rows = []
    for base, runs in scenes_by_base.items():
        for payload in runs:
            for r in payload.get("results", []):
                rows.append({
                    "scene": base,
                    "run": payload["run"],
                    "method_id": r.get("id"),
                    "full_class": r.get("full_class") or r.get("full_class_name"),
                    "method": r.get("method") or r.get("method_name"),
                    "gen_success": r.get("gen_success"),
                    "compile_success": r.get("compile_success"),
                    "compile_stage": r.get("compile_success_stage"),
                    "prefix_rescued": r.get("deterministic_prefix_success"),
                    "fix_success": r.get("fix_success"),
                    "baseline_line_cov": r.get("baseline_line_cov"),
                    "new_line_cov": r.get("new_line_cov"),
                    "line_cov_delta": r.get("line_cov_delta"),
                    "baseline_branch_cov": r.get("baseline_branch_cov"),
                    "new_branch_cov": r.get("new_branch_cov"),
                    "branch_cov_delta": r.get("branch_cov_delta"),
                    "target_method_line_cov_delta": r.get("target_method_line_cov_delta"),
                    "target_method_branch_cov_delta": r.get("target_method_branch_cov_delta"),
                    "test_cases_count": r.get("test_cases_count"),
                    "methods_generated": r.get("methods_generated"),
                    "failure_tags": ";".join(r.get("failure_tags", []) or []),
                    "gen_duration_s": r.get("gen_duration_s"),
                    "eval_duration_s": r.get("eval_duration_s"),
                })
    if rows:
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=METHOD_COLS)
            w.writeheader()
            w.writerows(rows)
    return out


# ── 表 5：token 消耗拆分 ───────────────────────────────────────────
def write_token_cost(scenes_by_base: dict) -> Path:
    out = OUT / "token_cost.csv"
    rows = []
    for base, runs in scenes_by_base.items():
        for payload in runs:
            s = payload.get("summary", {})
            by_phase = s.get("tokens_by_phase", {}) or {}
            row = {
                "scene": base,
                "run": payload["run"],
                "total_prompt": s.get("total_prompt_tokens"),
                "total_completion": s.get("total_completion_tokens"),
                "total_tokens": s.get("total_tokens"),
                "total_llm_calls": s.get("total_llm_calls"),
                "avg_tokens_per_method": s.get("avg_tokens_per_method"),
                "tokens_per_new_covered_line": s.get("tokens_per_new_covered_line"),
            }
            for phase, d in by_phase.items():
                row[f"{phase}_calls"] = d.get("calls")
                row[f"{phase}_prompt"] = d.get("prompt")
                row[f"{phase}_completion"] = d.get("completion")
            rows.append(row)
    if rows:
        all_keys = []
        for r in rows:
            for k in r:
                if k not in all_keys:
                    all_keys.append(k)
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys)
            w.writeheader()
            w.writerows(rows)
    return out


# ── 表 6：baseline 一致性校验 ────────────────────────────────────────
def write_baseline_consistency(scenes_by_base: dict) -> Path:
    """同一个方法在不同场次下，baseline_line_cov/branch_cov 是否一致。
    若不一致则说明样本漂移（配对实验失效），需要排查。"""
    out = OUT / "baseline_consistency.csv"
    # (project, method_id) -> list[(scene, run, baseline_line, baseline_branch)]
    table: dict[tuple, list] = defaultdict(list)
    for base, runs in scenes_by_base.items():
        for payload in runs:
            for r in payload.get("results", []):
                key = (payload["project"], r.get("id"), r.get("full_class") or r.get("full_class_name"),
                       r.get("method") or r.get("method_name"))
                table[key].append({
                    "scene": base,
                    "run": payload["run"],
                    "baseline_line_cov": r.get("baseline_line_cov"),
                    "baseline_branch_cov": r.get("baseline_branch_cov"),
                })

    rows = []
    for (proj, mid, cls, mname), recs in table.items():
        line_vals = [x["baseline_line_cov"] for x in recs if x["baseline_line_cov"] is not None]
        br_vals = [x["baseline_branch_cov"] for x in recs if x["baseline_branch_cov"] is not None]
        line_ok = (max(line_vals) - min(line_vals) < 0.01) if line_vals else True
        br_ok = (max(br_vals) - min(br_vals) < 0.01) if br_vals else True
        rows.append({
            "project": PROJ_DESC.get(proj, proj),
            "method_id": mid,
            "class": cls,
            "method": mname,
            "n_scenes": len(recs),
            "baseline_line_min": min(line_vals) if line_vals else "",
            "baseline_line_max": max(line_vals) if line_vals else "",
            "baseline_line_consistent": line_ok,
            "baseline_branch_min": min(br_vals) if br_vals else "",
            "baseline_branch_max": max(br_vals) if br_vals else "",
            "baseline_branch_consistent": br_ok,
            "scenes": ";".join(f"{x['scene']}#{x['run']}" for x in recs),
        })
    rows.sort(key=lambda x: (x["project"], x["method_id"] or ""))

    if rows:
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return out


# ── 最终 Markdown 报告 ────────────────────────────────────────────────
def write_final_report(scenes_by_base: dict) -> Path:
    out = OUT / "FINAL_REPORT.md"
    lines = []
    lines.append(f"# 论文实验数据汇总报告")
    lines.append(f"\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("> 本文件由 `experiments/aggregate_paper_data.py` 自动生成。所有原始 summary JSON 位于 `paper_final/summaries/`。\n")

    lines.append("## 1. 场次概览\n")
    lines.append("| 基础场次 | 项目 | 说明 | 轮次数 | 方法数 |")
    lines.append("|---|---|---|---|---|")
    for base in sorted(scenes_by_base):
        runs = scenes_by_base[base]
        exp = base.split("_", 1)[0]
        proj = runs[0]["project"]
        n_m = runs[0]["summary"].get("total")
        lines.append(f"| `{base}` | {PROJ_DESC.get(proj, proj)} | {SCENE_DESC.get(exp, '')} | {len(runs)} | {n_m} |")

    # 主结果（E1）
    lines.append("\n## 2. 主实验结果（E1 全功能系统，多轮 mean ± std）\n")
    lines.append("| 场次 | 项目 | Runs | GenSucc% | Compile% | PrefixRescue% | FixRescue% | ΔLineCov% | ΔBranchCov% | ΔMethodLine% | ΔMethodBranch% | Tokens/method |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for base in ("E1_gson", "E1_cl"):
        runs = scenes_by_base.get(base, [])
        if not runs:
            lines.append(f"| `{base}` | - | 0 | MISSING | MISSING | MISSING | MISSING | MISSING | MISSING | MISSING |")
            continue
        proj = PROJ_DESC.get(runs[0]["project"], runs[0]["project"])
        cols = []
        for k, _ in MAIN_METRIC_KEYS:
            vs = [r["summary"].get(k) for r in runs]
            m, s = mean_std(vs)
            cols.append(fmt_ms(m, s, 2))
        lines.append(f"| `{base}` | {proj} | {len(runs)} | " + " | ".join(cols) + " |")

    # 消融实验
    lines.append("\n## 3. 消融实验（vs E1 全功能系统）\n")
    for proj in ("gson", "cl"):
        if not scenes_by_base.get(f"E1_{proj}"):
            continue
        lines.append(f"\n### 3.{'1' if proj=='gson' else '2'} 项目：{PROJ_DESC.get(proj, proj)}\n")
        lines.append("| 场次 | 说明 | Compile% | ΔLineCov% | ΔBranchCov% | Δvs E1 |")
        lines.append("|---|---|---|---|---|---|")
        e1_line = mean_std([r["summary"].get("avg_line_coverage_delta")
                            for r in scenes_by_base[f"E1_{proj}"]])[0]
        for exp_tag in ("E1", "E2", "E3", "E4", "E5"):
            runs = scenes_by_base.get(f"{exp_tag}_{proj}", [])
            if not runs:
                continue
            c = mean_std([r["summary"].get("compile_success_rate") for r in runs])
            l = mean_std([r["summary"].get("avg_line_coverage_delta") for r in runs])
            b = mean_std([r["summary"].get("avg_branch_coverage_delta") for r in runs])
            diff = f"{l[0]-e1_line:+.2f}" if exp_tag != "E1" else "baseline"
            lines.append(f"| `{exp_tag}_{proj}` | {SCENE_DESC.get(exp_tag, '')} | "
                         f"{fmt_ms(*c,2)} | {fmt_ms(*l,2)} | {fmt_ms(*b,2)} | {diff} |")

    # ZeroShot 对照
    if any(b.startswith("Z1_") for b in scenes_by_base):
        lines.append("\n## 4. Zero-shot 基线对照\n")
        lines.append("| 项目 | 方案 | Compile% | ΔLineCov% | ΔBranchCov% | Tokens/method |")
        lines.append("|---|---|---|---|---|---|")
        for proj in ("gson", "cl"):
            for base, label in [(f"Z1_{proj}", "Zero-shot"), (f"E1_{proj}", "Ours (E1)")]:
                runs = scenes_by_base.get(base, [])
                if not runs:
                    continue
                c = mean_std([r["summary"].get("compile_success_rate") for r in runs])
                l = mean_std([r["summary"].get("avg_line_coverage_delta") for r in runs])
                b = mean_std([r["summary"].get("avg_branch_coverage_delta") for r in runs])
                t = mean_std([r["summary"].get("avg_tokens_per_method") for r in runs])
                lines.append(f"| {PROJ_DESC.get(proj, proj)} | {label} (`{base}`) | "
                             f"{fmt_ms(*c,2)} | {fmt_ms(*l,2)} | {fmt_ms(*b,2)} | {fmt_ms(*t,0)} |")

    # baseline 一致性摘要
    lines.append("\n## 5. Baseline 一致性校验\n")
    lines.append("对同一方法，不同场次下 JaCoCo 基准覆盖率应完全一致（配对实验前提）。详见 `baseline_consistency.csv`。\n")

    # 附录：文件清单
    lines.append("\n## 6. 文件清单\n")
    lines.append("| 文件 | 说明 |")
    lines.append("|---|---|")
    lines.append("| `main_table.csv` | 主实验表（E1 多轮 mean±std） |")
    lines.append("| `ablation_table.csv` | 消融实验表（E1 vs E2~E5） |")
    lines.append("| `zeroshot_comparison.csv` | Zero-shot 基线对照 |")
    lines.append("| `per_method_detail.csv` | 每方法 × 每场次的完整指标 |")
    lines.append("| `token_cost.csv` | Token 消耗按 phase 拆分 |")
    lines.append("| `baseline_consistency.csv` | Baseline 覆盖率一致性校验 |")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("gson", "cl"), default=None,
                    help="只聚合某个项目")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    scenes_by_base = scan_summaries(args.only)

    print(f"[aggregate] 扫到 {len(scenes_by_base)} 个基础场次：")
    for base in sorted(scenes_by_base):
        print(f"  {base}: {len(scenes_by_base[base])} 轮")

    p1 = write_main_table(scenes_by_base);          print(f"  ✓ {p1.relative_to(ROOT)}")
    p2 = write_ablation_table(scenes_by_base);      print(f"  ✓ {p2.relative_to(ROOT)}")
    p3 = write_zeroshot_table(scenes_by_base);      print(f"  ✓ {p3.relative_to(ROOT)}")
    p4 = write_per_method_detail(scenes_by_base);   print(f"  ✓ {p4.relative_to(ROOT)}")
    p5 = write_token_cost(scenes_by_base);          print(f"  ✓ {p5.relative_to(ROOT)}")
    p6 = write_baseline_consistency(scenes_by_base);print(f"  ✓ {p6.relative_to(ROOT)}")
    p7 = write_final_report(scenes_by_base);        print(f"  ✓ {p7.relative_to(ROOT)}")
    print(f"\n✅ 汇总完成。查看报告: {p7.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
