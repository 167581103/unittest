#!/usr/bin/env python3
"""rerender_report.py —— 以"方法级覆盖率 Delta"为主指标，重新渲染历史 JSON 报告为 Markdown。

背景：早期版本的 run_batch 只把"类整体覆盖率"写进 markdown 的主列。当被测类
baseline 已经很高（如 commons-lang 的 TypeUtils 85%+）时，为某个小方法补充
用例产生的改变会被全类数百行稀释为 +0.0%，给人"测试白写"的错觉。

本工具从既有 experiment_summary_*.json 中，尽量利用以下字段还原主指标：
    - target_method_line_cov       : eval 阶段目标方法行覆盖率
    - target_method_branch_cov     : eval 阶段目标方法分支覆盖率
    - target_method_baseline_*     : （新版 run_batch 直接写入）
    - target_method_line_cov_delta : （新版 run_batch 直接写入）
    - baseline_line_cov_from_pick  : pick 阶段看到的目标方法行覆盖率（可做 fallback）

用法：
    python experiments/rerender_report.py <path-to-json>          # 输出 *.rerendered.md
    python experiments/rerender_report.py <json> -o <out.md>      # 指定输出路径
    python experiments/rerender_report.py --latest                # 取 experiment_results/ 最新 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "experiment_results"


def _resolve_method_baseline(row: Dict) -> Optional[float]:
    """优先用 row 里直接存的方法级 baseline；否则 fallback 到 pick 阶段的覆盖率。"""
    v = row.get("target_method_baseline_line_cov")
    if v is not None:
        return float(v)
    v = row.get("baseline_line_cov_from_pick")
    if v is not None:
        return float(v)
    return None


def _resolve_method_baseline_branch(row: Dict) -> Optional[float]:
    """分支 baseline：目前只有新版 run_batch 会直接写入；pick 阶段的 branch 不一定被 carry 到 row，
    因此这里只尝试 row 内直接字段，找不到返回 None。"""
    v = row.get("target_method_baseline_branch_cov")
    if v is not None:
        return float(v)
    # 兜底：若历史 row 里夹带了 spec.branch_coverage（极少见），也认
    v = row.get("baseline_branch_cov_from_pick")
    if v is not None:
        return float(v)
    return None


def _method_line_delta(row: Dict) -> Optional[float]:
    v = row.get("target_method_line_cov_delta")
    if v is not None:
        return float(v)
    bl = _resolve_method_baseline(row)
    nw = row.get("target_method_line_cov")
    if bl is None or nw is None:
        return None
    return round(float(nw) - bl, 2)


def _method_branch_delta(row: Dict) -> Optional[float]:
    v = row.get("target_method_branch_cov_delta")
    if v is not None:
        return float(v)
    bl = _resolve_method_baseline_branch(row)
    nw = row.get("target_method_branch_cov")
    if bl is None or nw is None:
        return None
    return round(float(nw) - bl, 2)


def _summarize(rows: List[Dict]) -> Dict:
    total = len(rows)
    gen_ok = sum(1 for r in rows if r.get("gen_success"))
    compile_ok = sum(1 for r in rows if r.get("compile_success"))
    compile_initial = sum(1 for r in rows if r.get("compile_success_stage") == "initial")
    prefix_attempted = sum(1 for r in rows if r.get("deterministic_prefixed"))
    prefix_rescued = sum(1 for r in rows if r.get("compile_success_stage") == "after_prefix")
    fix_attempted = sum(1 for r in rows if r.get("fix_attempted"))
    fix_rescued = sum(1 for r in rows if r.get("fix_success"))

    cls_line_ds = [r.get("line_cov_delta") for r in rows if r.get("line_cov_delta") is not None]
    cls_branch_ds = [r.get("branch_cov_delta") for r in rows if r.get("branch_cov_delta") is not None]
    m_line_ds = [d for d in (_method_line_delta(r) for r in rows) if d is not None]
    m_branch_ds = [d for d in (_method_branch_delta(r) for r in rows) if d is not None]

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    improved_line = sum(1 for r in rows
                        if r.get("compile_success") and (_method_line_delta(r) or 0) > 0)
    improved_branch = sum(1 for r in rows
                          if r.get("compile_success") and (_method_branch_delta(r) or 0) > 0)

    tag_count: Dict[str, int] = {}
    for r in rows:
        if r.get("compile_success"):
            continue
        for t in (r.get("failure_tags") or []):
            tag_count[t] = tag_count.get(t, 0) + 1

    return {
        "total": total,
        "gen_ok": gen_ok,
        "compile_ok": compile_ok,
        "compile_initial": compile_initial,
        "prefix_attempted": prefix_attempted,
        "prefix_rescued": prefix_rescued,
        "fix_attempted": fix_attempted,
        "fix_rescued": fix_rescued,
        "avg_m_line": _avg(m_line_ds),
        "avg_m_branch": _avg(m_branch_ds),
        "avg_cls_line": _avg(cls_line_ds),
        "avg_cls_branch": _avg(cls_branch_ds),
        "methods_line_improved": improved_line,
        "methods_branch_improved": improved_branch,
        "n_methods_with_line_delta": len(m_line_ds),
        "n_methods_with_branch_delta": len(m_branch_ds),
        "tag_count": tag_count,
    }


def _pct_rate(n: int, d: int) -> str:
    return f"{(n / d * 100):.1f}" if d else "0.0"


def render_markdown(data: Dict) -> str:
    rows = data.get("results") or []
    meta = {
        "timestamp": data.get("timestamp") or "-",
        "mode": data.get("mode") or "-",
        "project": data.get("project") or data.get("project_name") or "-",
        "total_duration_s": data.get("total_duration_s"),
    }
    s = _summarize(rows)

    lines: List[str] = [
        "# 批量实验报告（重新渲染，以方法级覆盖率 Δ 为主指标）",
        "",
        f"- 生成时间: `{meta['timestamp']}`",
        f"- 模式: `{meta['mode']}`",
        f"- 项目: `{meta['project']}`",
        f"- 候选数: {s['total']}",
        f"- 生成成功: **{s['gen_ok']}/{s['total']}** ({_pct_rate(s['gen_ok'], s['total'])}%)",
        f"- 编译成功: **{s['compile_ok']}/{s['total']}** ({_pct_rate(s['compile_ok'], s['total'])}%)",
        f"  - 首次直接成功: {s['compile_initial']}",
        f"  - 确定性前置修复拾回: **{s['prefix_rescued']}/{s['prefix_attempted']}** "
        f"({_pct_rate(s['prefix_rescued'], s['prefix_attempted'])}%)",
        f"  - FixLoop 拾回: **{s['fix_rescued']}/{s['fix_attempted']}** "
        f"({_pct_rate(s['fix_rescued'], s['fix_attempted'])}%)",
        f"- ★ **平均目标方法行覆盖率提升: {s['avg_m_line']:+.2f}%** "
        f"（{s['methods_line_improved']}/{s['total']} 个方法有效提升；样本 n={s['n_methods_with_line_delta']}）",
        f"- ★ **平均目标方法分支覆盖率提升: {s['avg_m_branch']:+.2f}%** "
        f"（{s['methods_branch_improved']}/{s['total']} 个方法有效提升；样本 n={s['n_methods_with_branch_delta']}）",
        f"- 类整体行覆盖率平均提升（副指标）: {s['avg_cls_line']:+.2f}%",
        f"- 类整体分支覆盖率平均提升（副指标）: {s['avg_cls_branch']:+.2f}%",
        "",
        "> 主指标（★）衡量被测方法自身的覆盖率变化；类整体 Δ 在被测类已高覆盖的场景下会被稀释，仅作副指标。",
        "",
        "## 逐方法结果",
        "",
        "| # | 类 | 方法 | 生成 | 编译 | FixLoop | ★ 目标方法行覆盖率（前→后, Δ） | ★ 目标方法分支覆盖率（前→后, Δ） | 类整体行覆盖率（前→后, Δ） | 失败归因 |",
        "|---|----|------|------|------|---------|------------------------------|--------------------------------|-----------------------------|---------|",
    ]

    for i, r in enumerate(rows, 1):
        gen_icon = "✓" if r.get("gen_success") else "✗"
        cmp_icon = "✓" if r.get("compile_success") else "✗"
        if not r.get("fix_attempted"):
            fix_icon = "-"
        elif r.get("fix_success"):
            fix_icon = "✓ 拾回"
        else:
            fix_icon = "✗ 失败"

        m_bl = _resolve_method_baseline(r)
        m_nw = r.get("target_method_line_cov")
        m_d = _method_line_delta(r)
        if m_bl is not None and m_nw is not None and m_d is not None:
            m_line_str = f"{m_bl:.1f}% → {float(m_nw):.1f}% ({m_d:+.1f}%)"
        elif m_nw is not None:
            m_line_str = f"- → {float(m_nw):.1f}%"
        else:
            m_line_str = "-"

        mb_bl = _resolve_method_baseline_branch(r)
        mb_nw = r.get("target_method_branch_cov")
        mb_d = _method_branch_delta(r)
        if mb_bl is not None and mb_nw is not None and mb_d is not None:
            m_branch_str = f"{mb_bl:.1f}% → {float(mb_nw):.1f}% ({mb_d:+.1f}%)"
        elif mb_nw is not None:
            m_branch_str = f"- → {float(mb_nw):.1f}%"
        else:
            m_branch_str = "-"

        bl = r.get("baseline_line_cov")
        nw = r.get("new_line_cov")
        d = r.get("line_cov_delta")
        line_str = (f"{bl:.1f}% → {nw:.1f}% ({d:+.1f}%)"
                    if bl is not None and nw is not None and d is not None else "-")

        tags = ", ".join(r.get("failure_tags") or []) if not r.get("compile_success") else "-"
        cls = r.get("class") or "?"
        method = r.get("method") or "?"
        lines.append(
            f"| {i} | `{cls}` | `{method}` | {gen_icon} | {cmp_icon} | {fix_icon} | "
            f"{m_line_str} | {m_branch_str} | {line_str} | {tags} |"
        )

    if s["tag_count"]:
        lines += ["", "## 失败归因分布", "", "| 标签 | 次数 |", "|------|------|"]
        for t, n in sorted(s["tag_count"].items(), key=lambda x: -x[1]):
            lines.append(f"| `{t}` | {n} |")

    if meta.get("total_duration_s") is not None:
        lines += ["", f"- 总耗时: {meta['total_duration_s']}s"]

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", nargs="?", help="experiment_summary_*.json 路径")
    ap.add_argument("-o", "--output", help="输出 markdown 路径；默认 <json>.rerendered.md")
    ap.add_argument("--latest", action="store_true",
                    help=f"自动选取 {RESULTS_DIR} 中最新的 JSON 报告")
    args = ap.parse_args()

    if args.latest:
        candidates = sorted(RESULTS_DIR.glob("experiment_summary_*.json"))
        if not candidates:
            print(f"[rerender] 在 {RESULTS_DIR} 下没有找到 JSON 报告", file=sys.stderr)
            return 1
        in_path = candidates[-1]
    elif args.json_path:
        in_path = Path(args.json_path)
    else:
        ap.print_help()
        return 2

    if not in_path.exists():
        print(f"[rerender] 文件不存在: {in_path}", file=sys.stderr)
        return 1

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    md = render_markdown(data)

    out_path = Path(args.output) if args.output else in_path.with_suffix(".rerendered.md")
    out_path.write_text(md, encoding="utf-8")
    print(f"[rerender] ✓ {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
