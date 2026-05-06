#!/usr/bin/env python3
"""
fill_paper_tables.py —— 一次性把论文 5.4 / 5.5 / 5.6 的数据表填好。

读取 experiment_results/experiment_summary_*.json，输出一份带完整数据
的 Markdown 报告（论文正文里可直接 copy-paste）。

约定：
  • 主实验 5.4 用 E1 的 run1 + run2（若存在 run3 则自动纳入）取均值
  • 消融 5.5 使用 Ex 的单次结果；对照列用 E1 均值
  • Z1 (ZeroShot) 若覆盖率为空则标记 "(数据重跑中)"
"""
from __future__ import annotations
import glob, json, os, re, statistics
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "experiment_results"
OUT_MD = RESULT_DIR / "paper_tables_filled.md"

def load_all() -> Dict[str, List[dict]]:
    """按场次别名聚合所有 summary.json。"""
    out: Dict[str, List[dict]] = {}
    for fp in sorted(glob.glob(str(RESULT_DIR / "experiment_summary_*.json"))):
        m = re.search(r"(E\d_(?:gson|cl)|Z\d_(?:gson|cl))(?:_run(\d))?", os.path.basename(fp))
        if not m:
            continue
        key = m.group(1)  # 如 E1_gson（不含 run 后缀）
        data = json.load(open(fp, encoding="utf-8"))
        data["_file"] = fp
        data["_run"] = int(m.group(2)) if m.group(2) else 1
        out.setdefault(key, []).append(data)
    # 每个 key 的 runs 按 _run 排序
    for k in out:
        out[k].sort(key=lambda d: d["_run"])
    return out

def mean(vals: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    if not xs: return None
    return round(statistics.fmean(xs), 2)

def msum(vals: List[Optional[int]]) -> int:
    return sum(v for v in vals if v is not None)

def fmt(v, suffix="", nd=2) -> str:
    if v is None: return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}{suffix}"
    return f"{v}{suffix}"

def aggregate(entries: List[dict]) -> dict:
    """对同一场次（可能有多 run）取均值/求和；若只有一次，直接返回该次数值。"""
    if not entries:
        return {}
    summaries = [e["summary"] for e in entries]
    keys_avg = [
        "gen_success_rate", "compile_success_rate",
        "avg_line_coverage_delta", "avg_branch_coverage_delta",
        "avg_target_method_line_delta", "avg_target_method_branch_delta",
        "avg_tokens_per_method",
    ]
    keys_avg_int = [
        "compile_success_initial", "prefix_rescued", "fix_rescued",
        "methods_line_improved", "methods_branch_improved",
        "total_llm_calls", "total_tokens",
    ]
    agg = {"_n_runs": len(entries), "total": summaries[0].get("total")}
    for k in keys_avg:
        agg[k] = mean([s.get(k) for s in summaries])
    for k in keys_avg_int:
        vs = [s.get(k) for s in summaries if s.get(k) is not None]
        agg[k] = round(sum(vs) / len(vs), 1) if vs else None
    return agg

def row(label: str, a: dict) -> str:
    if not a:
        return f"| {label} | (未生成) | | | | | | | | |"
    return (
        f"| {label} | {a.get('total','—')} | "
        f"{fmt(a.get('gen_success_rate'), '%', 1)} | "
        f"{fmt(a.get('compile_success_rate'), '%', 1)} | "
        f"{fmt(a.get('avg_line_coverage_delta'), '%')} | "
        f"{fmt(a.get('avg_branch_coverage_delta'), '%')} | "
        f"{fmt(a.get('avg_target_method_line_delta'), '%')} | "
        f"{fmt(a.get('avg_target_method_branch_delta'), '%')} | "
        f"{a.get('methods_line_improved','—')} | "
        f"{fmt(a.get('avg_tokens_per_method'), '', 0)} |"
    )

def main() -> int:
    data = load_all()
    lines: List[str] = []
    p = lines.append

    p("# 论文 5.4 / 5.5 / 5.6 数据表（自动填充）\n")
    p(f"_生成时间：{__import__('time').strftime('%F %T')}_\n")

    # ══════════════ 5.4 主实验 ══════════════
    p("## 表 5-4-A  主实验端到端结果（E1：完整系统）\n")
    p("| 场次 | 样本数 | 生成成功率 | 编译成功率 | 类·行Δ | 类·分支Δ | 方法·行Δ | 方法·分支Δ | 行覆盖提升方法数 | 平均 tokens/方法 |")
    p("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    e1g = aggregate(data.get("E1_gson", []))
    e1c = aggregate(data.get("E1_cl", []))
    gson_runs = len(data.get("E1_gson", []))
    cl_runs = len(data.get("E1_cl", []))
    p(row(f"Gson（{gson_runs} 轮均值）", e1g))
    p(row(f"Commons-Lang（{cl_runs} 轮均值）", e1c))
    p("")

    # ══════════════ 5.4.B 基线对比（vs Z1 ZeroShot） ══════════════
    p("## 表 5-4-B  与 Zero-shot 基线对比\n")
    z1g = aggregate(data.get("Z1_gson", []))
    z1c = aggregate(data.get("Z1_cl", []))
    z1_broken = (z1g and z1g.get("avg_line_coverage_delta") is None)
    p("| 方法 | 项目 | 编译成功率 | 行覆盖 Δ | 分支覆盖 Δ | 平均 tokens/方法 |")
    p("|---|---|---:|---:|---:|---:|")
    p(f"| Zero-shot 基线 | Gson | {fmt(z1g.get('compile_success_rate'), '%', 1) if z1g else '—'} | "
      f"{fmt(z1g.get('avg_line_coverage_delta'), '%') if z1g else '—'} | "
      f"{fmt(z1g.get('avg_branch_coverage_delta'), '%') if z1g else '—'} | "
      f"{fmt(z1g.get('avg_tokens_per_method'), '', 0) if z1g else '—'} |")
    p(f"| Zero-shot 基线 | Commons-Lang | {fmt(z1c.get('compile_success_rate'), '%', 1) if z1c else '—'} | "
      f"{fmt(z1c.get('avg_line_coverage_delta'), '%') if z1c else '—'} | "
      f"{fmt(z1c.get('avg_branch_coverage_delta'), '%') if z1c else '—'} | "
      f"{fmt(z1c.get('avg_tokens_per_method'), '', 0) if z1c else '—'} |")
    p(f"| **本系统（E1）** | Gson | {fmt(e1g.get('compile_success_rate'), '%', 1)} | "
      f"{fmt(e1g.get('avg_line_coverage_delta'), '%')} | "
      f"{fmt(e1g.get('avg_branch_coverage_delta'), '%')} | "
      f"{fmt(e1g.get('avg_tokens_per_method'), '', 0)} |")
    p(f"| **本系统（E1）** | Commons-Lang | {fmt(e1c.get('compile_success_rate'), '%', 1)} | "
      f"{fmt(e1c.get('avg_line_coverage_delta'), '%')} | "
      f"{fmt(e1c.get('avg_branch_coverage_delta'), '%')} | "
      f"{fmt(e1c.get('avg_tokens_per_method'), '', 0)} |")
    if z1_broken:
        p("\n> ⚠️ Z1 首轮数据因 evaluator 配置问题未采集到覆盖率，已修复，正在补跑。上表 Z1 行将在重跑完成后填充。")
    p("")

    # ══════════════ 5.5 消融实验 ══════════════
    def make_ablation(title: str, ablation_key: str, ablation_name: str, project_suffix: str, proj_label: str):
        p(f"### {title}（{proj_label}）\n")
        e1 = aggregate(data.get(f"E1_{project_suffix}", []))
        ex = aggregate(data.get(f"{ablation_key}_{project_suffix}", []))
        p("| 配置 | 编译成功率 | 类·行Δ | 类·分支Δ | 方法·行Δ | 方法·分支Δ | LLM 调用数 | 平均 tokens/方法 |")
        p("|---|---:|---:|---:|---:|---:|---:|---:|")
        p(f"| 完整系统（E1 均值） | {fmt(e1.get('compile_success_rate'), '%', 1)} | "
          f"{fmt(e1.get('avg_line_coverage_delta'), '%')} | "
          f"{fmt(e1.get('avg_branch_coverage_delta'), '%')} | "
          f"{fmt(e1.get('avg_target_method_line_delta'), '%')} | "
          f"{fmt(e1.get('avg_target_method_branch_delta'), '%')} | "
          f"{fmt(e1.get('total_llm_calls'), '', 0)} | "
          f"{fmt(e1.get('avg_tokens_per_method'), '', 0)} |")
        p(f"| {ablation_name} | {fmt(ex.get('compile_success_rate'), '%', 1)} | "
          f"{fmt(ex.get('avg_line_coverage_delta'), '%')} | "
          f"{fmt(ex.get('avg_branch_coverage_delta'), '%')} | "
          f"{fmt(ex.get('avg_target_method_line_delta'), '%')} | "
          f"{fmt(ex.get('avg_target_method_branch_delta'), '%')} | "
          f"{fmt(ex.get('total_llm_calls'), '', 0)} | "
          f"{fmt(ex.get('avg_tokens_per_method'), '', 0)} |")
        p("")

    p("## 表 5-5-A  两步式生成消融（E1 vs E2 一步式）\n")
    make_ablation("一步式 vs 两步式", "E2", "一步式（E2：去掉 analyze）", "gson", "Gson")
    make_ablation("一步式 vs 两步式", "E2", "一步式（E2：去掉 analyze）", "cl", "Commons-Lang")

    p("## 表 5-5-B  Fix-Loop 消融（E1 vs E3 关闭 FixLoop）\n")
    make_ablation("关闭 Fix-Loop", "E3", "关闭 Fix-Loop（E3）", "gson", "Gson")
    make_ablation("关闭 Fix-Loop", "E3", "关闭 Fix-Loop（E3）", "cl", "Commons-Lang")

    p("## 表 5-5-C  Deterministic Prefix 消融（E1 vs E4 关闭 prefix）\n")
    make_ablation("关闭确定性前缀修复", "E4", "关闭 Prefix（E4）", "gson", "Gson")
    make_ablation("关闭确定性前缀修复", "E4", "关闭 Prefix（E4）", "cl", "Commons-Lang")

    p("## 表 5-5-D  Agentic RAG 消融（E1 vs E5 关闭 RAG）\n")
    make_ablation("关闭 Agentic RAG", "E5", "无 RAG（E5）", "gson", "Gson")
    make_ablation("关闭 Agentic RAG", "E5", "无 RAG（E5）", "cl", "Commons-Lang")

    # ══════════════ 5.6 FixLoop 阶梯 ══════════════
    p("## 表 5-6  Fix-Loop 编译通过率阶梯（E1 完整系统）\n")
    p("| 项目 | 样本数 | 初始编译通过 | + Prefix 规则修复 | + LLM FixLoop | 最终编译通过率 |")
    p("|---|---:|---:|---:|---:|---:|")
    for proj_key, proj_label in [("E1_gson", "Gson"), ("E1_cl", "Commons-Lang")]:
        a = aggregate(data.get(proj_key, []))
        if not a:
            p(f"| {proj_label} | (未生成) | | | | |")
            continue
        n = a.get("total", 0)
        ic = a.get("compile_success_initial")
        pr = a.get("prefix_rescued")
        fr = a.get("fix_rescued")
        final_ok_rate = a.get("compile_success_rate")
        def pct(x): return "—" if x is None else f"{x}/{n} = {100*x/n:.1f}%"
        p(f"| {proj_label} | {n} | {pct(ic)} | "
          f"{fmt(pr, '', 1) if pr is not None else '—'} 个 | "
          f"{fmt(fr, '', 1) if fr is not None else '—'} 个 | "
          f"{fmt(final_ok_rate, '%', 1)} |")
    p("")

    # ══════════════ 正文关键数字速查 ══════════════
    p("## 正文关键数字速查（复制到论文段落）\n")
    p("> 下列数字可以直接引用到论文正文相应段落。\n")
    for key, label in [("E1_gson", "E1·Gson"), ("E1_cl", "E1·CommonsLang")]:
        a = aggregate(data.get(key, []))
        if not a: continue
        p(f"**{label}（{a['_n_runs']} 轮均值）**："
          f"编译成功率 {fmt(a.get('compile_success_rate'),'%',1)}，"
          f"类行覆盖率平均提升 {fmt(a.get('avg_line_coverage_delta'),'%')}，"
          f"类分支覆盖率平均提升 {fmt(a.get('avg_branch_coverage_delta'),'%')}，"
          f"焦点方法行覆盖率平均提升 {fmt(a.get('avg_target_method_line_delta'),'%')}，"
          f"焦点方法分支覆盖率平均提升 {fmt(a.get('avg_target_method_branch_delta'),'%')}；"
          f"平均每方法消耗 {fmt(a.get('avg_tokens_per_method'),'',0)} tokens。\n")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 已生成：{OUT_MD}")
    print(f"   字节数：{OUT_MD.stat().st_size}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
