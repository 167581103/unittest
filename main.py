#!/usr/bin/env python3
"""
main.py —— 本仓库的统一 CLI 入口

把零散的"运行哪个脚本"收敛到一个命令下，让整个工程呈现为一个归一化的框架。

子命令：

  pick      选候选方法 → 写入 experiments/methods.yaml
  run       跑批量实验（Phase1 GEN → Phase2 PREFIX/EVAL/FIXLOOP → Phase3 REPORT）
  report    打印最新一次实验的 Markdown 报告
  web       启动 Web 演示 Dashboard（FastAPI + WebSocket）
  clean     清理 /tmp 下的生成产物与索引缓存（不会动 experiment_results/）

典型用法：

  # 1) 选方法
  python main.py pick --top 12

  # 2) 跑实验
  python main.py run --llm-concurrency 4

  # 3) 查看最新报告
  python main.py report

  # 4) 打开 Web Dashboard
  python main.py web
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "experiment_results"


# ══════════════ sub-commands ══════════════

def cmd_pick(args: argparse.Namespace) -> int:
    """转发到 experiments/pick_methods.py"""
    cmd = [sys.executable, "-u", str(ROOT / "experiments" / "pick_methods.py")]
    if args.project:
        cmd += ["--project", args.project]
    if args.top is not None:
        cmd += ["--top", str(args.top)]
    if args.extra:
        cmd += args.extra
    print(f"[main] → {' '.join(cmd)}")
    return subprocess.call(cmd)


def cmd_run(args: argparse.Namespace) -> int:
    """转发到 experiments/run_batch.py"""
    cmd = [sys.executable, "-u", str(ROOT / "experiments" / "run_batch.py")]
    if args.project:
        cmd += ["--project", args.project]
    if args.limit is not None:
        cmd += ["--limit", str(args.limit)]
    if args.llm_concurrency is not None:
        cmd += ["--llm-concurrency", str(args.llm_concurrency)]
    if args.one_shot:
        cmd += ["--one-shot"]
    if args.suffix:
        cmd += ["--suffix", args.suffix]
    if args.fix_retries is not None:
        cmd += ["--fix-retries", str(args.fix_retries)]
    if args.filter_unrunnable:
        cmd += ["--filter-unrunnable"]
    if args.extra:
        cmd += args.extra
    print(f"[main] → {' '.join(cmd)}")
    return subprocess.call(cmd)


def cmd_report(args: argparse.Namespace) -> int:
    """打印 experiment_results/ 下最新一次 *.md 报告。支持 --rerender 用方法级口径重点生成。"""
    if not RESULTS_DIR.is_dir():
        print(f"[main] ✗ 目录不存在：{RESULTS_DIR}")
        return 1
    if getattr(args, "rerender", False):
        # 直接跳到 rerender_report.py，拿最新 JSON 重生成 markdown
        cmd = [sys.executable, "-u", str(ROOT / "experiments" / "rerender_report.py"), "--latest"]
        print(f"[main] → {' '.join(cmd)}")
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        rendered = sorted(RESULTS_DIR.glob("experiment_summary_*.rerendered.md"))
        if rendered:
            latest = rendered[-1]
            print(f"[main] 重渲染报告：{latest}")
            print("=" * 70)
            print(latest.read_text(encoding="utf-8"))
        return 0
    md_files = sorted(RESULTS_DIR.glob("experiment_summary_*.md"))
    # 排除 .rerendered.md，避免默认看到重渲染版本
    md_files = [p for p in md_files if not p.name.endswith(".rerendered.md")]
    if not md_files:
        print(f"[main] ✗ {RESULTS_DIR} 下没有找到 experiment_summary_*.md")
        return 1
    latest = md_files[-1]
    print(f"[main] 最新报告：{latest}")
    print("=" * 70)
    print(latest.read_text(encoding="utf-8"))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """启动 web/server.py"""
    env = os.environ.copy()
    cmd = [sys.executable, "-m", "uvicorn", "web.server:app",
           "--host", args.host, "--port", str(args.port)]
    if args.reload:
        cmd += ["--reload"]
    print(f"[main] → {' '.join(cmd)}")
    return subprocess.call(cmd, env=env, cwd=str(ROOT))


def cmd_clean(args: argparse.Namespace) -> int:
    """清理 /tmp 下的生成物；不会动 experiment_results/"""
    import shutil
    import glob
    targets = [
        "/tmp/batch_generated",
        "/tmp/generated_tests",
        "/tmp/test_reports",
    ]
    # 所有项目的 jacoco exec（-jacoco.exec 后缀 + baseline/pick 产物）
    targets += glob.glob("/tmp/*-jacoco.exec")
    targets += glob.glob("/tmp/pick_methods_baseline*.exec")
    targets += glob.glob("/tmp/pick_methods_report*.xml")
    if args.drop_index:
        # 所有项目的 RAG 索引
        targets += glob.glob("/tmp/*_code_rag.index")
        targets += ["/tmp/code_rag.index"]

    for p in targets:
        pp = Path(p)
        if not pp.exists():
            continue
        try:
            if pp.is_dir():
                shutil.rmtree(pp)
            else:
                pp.unlink()
            print(f"[main] ✓ removed {pp}")
        except Exception as e:
            print(f"[main] ✗ failed to remove {pp}: {e}")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    """列出 data/projects.yaml 中定义的所有被测项目"""
    sys.path.insert(0, str(ROOT))
    from core.project_config import list_projects, load_project, PROJECTS_YAML
    import yaml
    print(f"配置文件: {PROJECTS_YAML}")
    if not PROJECTS_YAML.exists():
        print("  (不存在)")
        return 1
    data = yaml.safe_load(PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    active = data.get("active")
    print(f"当前 active: {active}")
    print("可用项目:")
    for n in list_projects():
        try:
            cfg = load_project(n)
            marker = " ← active" if n == active else ""
            mod = f"module={cfg.module_name}" if cfg.module_name else "单模块"
            print(f"  - {n:20s} {cfg.project_dir}  [{mod}]{marker}")
        except Exception as e:
            print(f"  - {n:20s} <加载失败: {e}>")
    return 0


# ══════════════ CLI ══════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="UT-Gen 统一入口（选方法 / 跑实验 / 看报告 / Web / 清理）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # pick
    sp = sub.add_parser("pick", help="选候选方法写入 experiments/methods.yaml")
    sp.add_argument("--project", default=None,
                    help="目标项目名（来自 data/projects.yaml），省略时用 active")
    sp.add_argument("--top", type=int, default=12, help="挨覆盖率最低的 N 个 public 方法")
    sp.add_argument("extra", nargs=argparse.REMAINDER,
                    help="透传给 experiments/pick_methods.py 的额外参数")
    sp.set_defaults(func=cmd_pick)

    # run
    sr = sub.add_parser("run", help="跑批量实验（analyze → generate → prefix → eval → fixloop）")
    sr.add_argument("--project", default=None,
                    help="目标项目名（来自 data/projects.yaml），省略时用 active")
    sr.add_argument("--limit", type=int, default=None, help="只跑前 N 个方法")
    sr.add_argument("--llm-concurrency", type=int, default=4, help="LLM 并发数")
    sr.add_argument("--one-shot", action="store_true", help="一步式生成（消融实验）")
    sr.add_argument("--suffix", default="", help="输出文件名后缀")
    sr.add_argument("--fix-retries", type=int, default=3,
                    help="FixLoop 最大重试轮数（0 = 禁用 FixLoop）")
    sr.add_argument("--filter-unrunnable", action="store_true",
                    help="运行前过滤 private 方法 / 仅私有构造器类的实例方法")
    sr.add_argument("extra", nargs=argparse.REMAINDER,
                    help="透传给 experiments/run_batch.py 的额外参数")
    sr.set_defaults(func=cmd_run)

    # report
    srp = sub.add_parser("report", help="打印最新一次实验的 Markdown 报告")
    srp.add_argument("--rerender", action="store_true",
                     help="以方法级覆盖率 Δ 为主指标重新渲染最新 JSON 报告")
    srp.set_defaults(func=cmd_report)

    # web
    sw = sub.add_parser("web", help="启动 Web Demo Dashboard")
    sw.add_argument("--host", default="0.0.0.0")
    sw.add_argument("--port", type=int, default=8080)
    sw.add_argument("--reload", action="store_true")
    sw.set_defaults(func=cmd_web)

    # clean
    sc = sub.add_parser("clean", help="清理 /tmp 下的生成产物与缓存")
    sc.add_argument("--drop-index", action="store_true",
                    help="一并删除已构建的 RAG 索引（下次要重建）")
    sc.set_defaults(func=cmd_clean)

    # projects
    spj = sub.add_parser("projects", help="列出 data/projects.yaml 中定义的被测项目")
    spj.set_defaults(func=cmd_projects)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
