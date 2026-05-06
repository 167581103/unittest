"""
replay_isolated_coverage.py —— 孤立覆盖率重放脚本（论文核心评估工具）
=======================================================================

背景
----
早期实验（run_batch / aggregate_paper_data）统计的是 "生成测试 + 项目原有测试
并集" 的覆盖率 Delta，这在被测类已被项目自带测试堆到 95%+ 覆盖率时，我们生成
的测试再好也只能拿到很小的 Δ。这**不能**代表"我们生成的测试本身的质量"。

本脚本做的事
-----------
  1. 对 experiment_results/paper_final/artifacts/<scene>/<M??_Class_method>/ 下
     的每个方法：
       - 读取 00_meta.json 拿到 full_class_name / method_name
       - 读取 99_FINAL.java，抽出 test_class（包名 + public class 名）
  2. 以 "完全孤立" 方式评估：
       - 清理项目中可能残留的 *Generated* 测试
       - 把 99_FINAL.java 拷贝进 src/test/java（重命名为独特的 Generated 类名，
         避免与项目自带同名测试冲突）
       - mvn test-compile 编译
       - mvn test -Dtest=<只有我们这一个类>  + JaCoCo agent
       - 生成 XML 报告后提取：
            * target_method_line_cov / target_method_branch_cov  （主指标）
            * class_line_cov / class_branch_cov                 （副指标）
  3. 每个方法单独写 isolated_coverage.json 到 artifact 目录
  4. 汇总到两级 summary：
        experiment_results/paper_final/isolated/<scene>/summary.json
        experiment_results/paper_final/isolated_coverage_summary.json

主指标定义
----------
  - 目标方法孤立行覆盖率 = 只跑我们生成的测试时，JaCoCo 报告里目标方法的
    covered_lines / total_lines * 100%
  - 目标方法孤立分支覆盖率 = 同上，按分支
  - 类级孤立行/分支覆盖率 = 目标类整体（所有方法加总）

用法
----
    # 先激活 gp 环境
    conda activate gp

    # 只跑 E1 的 gson（冒烟验证）
    python experiments/replay_isolated_coverage.py --scenes E1_gson

    # 跑全部 scene（默认）
    python experiments/replay_isolated_coverage.py --all

    # 指定若干 scene
    python experiments/replay_isolated_coverage.py --scenes E1_gson E1_cl E5_gson E5_cl

注意
----
  1. 脚本**只读** artifact 目录（99_FINAL.java + meta），不会修改历史实验产物
  2. 对项目 src/test/java 的写入均用独特时间戳类名，结束后会清理
  3. 每个方法独立 mvn 调用，串行跑，约 30-60s/方法。E1 三轮 × (9+12) ≈ 63 个；
     加上 E2/E3/E4/E5/Zeroshot 大约共 200+ 方法，预计总耗时 2-3 小时
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -------- 让本脚本能 import 仓库内的模块 --------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.project_config import load_project, ProjectConfig  # noqa: E402
from evaluation.evaluator import TestEvaluator, CoverageReport  # noqa: E402


# ============================================================
# 配置
# ============================================================
ARTIFACTS_ROOT = ROOT / "experiment_results" / "paper_final" / "artifacts"
ISOLATED_ROOT = ROOT / "experiment_results" / "paper_final" / "isolated"
JACOCO_HOME = "/data/workspace/unittest/lib"
EXCLUDE_SCENES = {"_smoke_verified_1641"}  # 这些目录跳过


# ============================================================
# Scene → project 的映射
# ============================================================
def scene_to_project(scene: str) -> str:
    """从 scene 名推断项目（gson 或 commons-lang）。

    规则：
      - 含 "_gson"  → gson
      - 含 "_cl"    → commons-lang
      - zeroshot_*_Z?_gson / _cl 同理
    """
    s = scene.lower()
    if "_gson" in s:
        return "gson"
    if "_cl" in s:
        return "commons-lang"
    raise ValueError(f"无法从 scene 名 {scene!r} 推断项目（既不含 _gson 也不含 _cl）")


# ============================================================
# 解析 99_FINAL.java 拿 test_class（包名.类名）
# ============================================================
_CLASS_DECL_RE = re.compile(
    r"^\s*public\s+(?:abstract\s+|final\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_PACKAGE_DECL_RE = re.compile(r"^\s*package\s+([\w\.]+)\s*;", re.MULTILINE)


def parse_test_class(final_java_path: Path) -> Optional[str]:
    try:
        content = final_java_path.read_text(encoding="utf-8")
    except Exception:
        return None
    m_pkg = _PACKAGE_DECL_RE.search(content)
    m_cls = _CLASS_DECL_RE.search(content)
    if not m_cls:
        return None
    pkg = m_pkg.group(1) if m_pkg else ""
    cls = m_cls.group(1)
    return f"{pkg}.{cls}" if pkg else cls


# ============================================================
# 核心：对单个方法做孤立覆盖率评估
# ============================================================
@dataclass
class IsolatedCovResult:
    scene: str
    method_id: str
    target_class: str
    target_method: str
    test_class: str
    compile_success: bool
    run_success: bool
    # 目标方法孤立覆盖率（主指标）
    target_method_line_cov: Optional[float]
    target_method_branch_cov: Optional[float]
    target_method_covered_lines: Optional[int]
    target_method_total_lines: Optional[int]
    target_method_covered_branches: Optional[int]
    target_method_total_branches: Optional[int]
    # 类级孤立覆盖率（副指标）
    class_line_cov: Optional[float]
    class_branch_cov: Optional[float]
    class_covered_lines: Optional[int]
    class_total_lines: Optional[int]
    # 诊断
    duration_s: float
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def isolated_evaluate_one(
    evaluator: TestEvaluator,
    scene: str,
    method_dir: Path,
) -> IsolatedCovResult:
    """对一个方法 artifact 做孤立覆盖率评估。"""
    t0 = time.time()
    method_id = method_dir.name

    # 1. 读 meta
    meta_path = method_dir / "00_meta.json"
    final_java = method_dir / "99_FINAL.java"

    def _err(msg: str) -> IsolatedCovResult:
        return IsolatedCovResult(
            scene=scene, method_id=method_id,
            target_class="", target_method="", test_class="",
            compile_success=False, run_success=False,
            target_method_line_cov=None, target_method_branch_cov=None,
            target_method_covered_lines=None, target_method_total_lines=None,
            target_method_covered_branches=None, target_method_total_branches=None,
            class_line_cov=None, class_branch_cov=None,
            class_covered_lines=None, class_total_lines=None,
            duration_s=round(time.time() - t0, 2),
            error=msg,
        )

    if not meta_path.exists():
        return _err("missing 00_meta.json")
    if not final_java.exists():
        return _err("missing 99_FINAL.java")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _err(f"meta parse failed: {e}")

    target_class = meta.get("full_class_name") or meta.get("full_class") or ""
    target_method = meta.get("method_name") or meta.get("method") or ""
    if not target_class or not target_method:
        return _err("meta missing full_class_name/method_name")

    # status 可选：如果 meta 明确标记 compile_success=False，跳过
    cov_meta = meta.get("coverage") or {}
    if cov_meta.get("compile_success") is False:
        # 仍可尝试重放，但先记录一下
        pass

    # 2. 解析 test_class
    test_class = parse_test_class(final_java)
    if not test_class:
        return _err("cannot parse public class from 99_FINAL.java")

    # 3. 孤立跑
    try:
        # 3a. 清理历史 Generated 残留
        evaluator._cleanup_old_generated_tests()

        # 3b. 拷贝进项目（evaluator._copy_test_file 会重命名为 <Class>Generated<ts>）
        copy_ok = evaluator._copy_test_file(str(final_java), test_class)
        if not copy_ok:
            return _err("copy test file failed")
        actual_test_class = evaluator._actual_test_class or test_class

        # 3c. 编译（只编译测试）
        compile_ok = evaluator._compile_test(actual_test_class)
        result = IsolatedCovResult(
            scene=scene, method_id=method_id,
            target_class=target_class, target_method=target_method,
            test_class=actual_test_class,
            compile_success=bool(compile_ok), run_success=False,
            target_method_line_cov=None, target_method_branch_cov=None,
            target_method_covered_lines=None, target_method_total_lines=None,
            target_method_covered_branches=None, target_method_total_branches=None,
            class_line_cov=None, class_branch_cov=None,
            class_covered_lines=None, class_total_lines=None,
            duration_s=0.0,
        )
        if not compile_ok:
            result.error = "compile failed during replay"
            result.duration_s = round(time.time() - t0, 2)
            return result

        # 3d. 运行测试：**只跑我们这一个类**，绝不带 baseline 测试
        #     evaluator._run_test 会按类名生成 -Dtest=<SimpleName>
        evaluator._run_test([actual_test_class])

        # 3e. 解析覆盖率（JaCoCo exec → XML → target_class 节点）
        cov: Optional[CoverageReport] = evaluator._get_coverage_from_exec(
            evaluator.exec_file, target_class
        )
        if cov is None:
            result.error = "coverage parse failed"
            result.duration_s = round(time.time() - t0, 2)
            return result

        result.run_success = True
        # 类级
        result.class_line_cov = round(cov.line_coverage, 2)
        result.class_branch_cov = round(cov.branch_coverage, 2)
        result.class_covered_lines = cov.covered_lines
        result.class_total_lines = cov.total_lines

        # 目标方法级（聚合同名重载）
        mc = cov.get_method_coverage(target_method)
        if mc:
            result.target_method_line_cov = round(mc.line_coverage, 2)
            result.target_method_branch_cov = round(mc.branch_coverage, 2)
            result.target_method_covered_lines = mc.covered_lines
            result.target_method_total_lines = mc.total_lines
            result.target_method_covered_branches = mc.covered_branches
            result.target_method_total_branches = mc.total_branches
        else:
            result.error = "target method not found in coverage report"

        result.duration_s = round(time.time() - t0, 2)
        return result
    except Exception as e:
        tb = traceback.format_exc(limit=3)
        return _err(f"exception: {e}\n{tb}")
    finally:
        # 不论成功失败都清一次 Generated 残留，保持项目干净
        try:
            evaluator._cleanup_old_generated_tests()
        except Exception:
            pass


# ============================================================
# 批量：对一个 scene 下所有方法重放
# ============================================================
def replay_scene(scene: str, dry_run: bool = False, limit: int = 0) -> List[Dict]:
    """对一个 scene 做孤立覆盖率评估，返回每个方法的结果字典列表。"""
    scene_dir = ARTIFACTS_ROOT / scene
    if not scene_dir.is_dir():
        print(f"[skip] scene 目录不存在: {scene_dir}")
        return []

    project = scene_to_project(scene)
    cfg: ProjectConfig = load_project(project)

    evaluator = TestEvaluator(
        project_dir=cfg.project_dir,
        jacoco_home=JACOCO_HOME,
        module_name=cfg.module_name,
        java_home=cfg.java_home,
        surefire_arglines=cfg.surefire_arglines,
        mvn_extra_args=cfg.mvn_extra_args,
    )

    # 扫描方法目录（形如 M01_Foo_bar）
    method_dirs = sorted(
        [p for p in scene_dir.iterdir() if p.is_dir() and re.match(r"^M\d+_", p.name)]
    )
    if limit and limit > 0:
        method_dirs = method_dirs[:limit]

    results: List[Dict] = []
    print(f"\n{'=' * 70}")
    print(f"[scene] {scene}  project={project}  方法数={len(method_dirs)}")
    print(f"{'=' * 70}")

    for i, mdir in enumerate(method_dirs, 1):
        print(f"\n[{scene} {i}/{len(method_dirs)}] ▶ {mdir.name}")
        if dry_run:
            results.append({"scene": scene, "method_id": mdir.name, "dry_run": True})
            continue

        r = isolated_evaluate_one(evaluator, scene, mdir)
        d = r.to_dict()
        results.append(d)

        # 每方法单独写一份到 artifact 目录（方便溯源）
        try:
            (mdir / "isolated_coverage.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"  ! 写 isolated_coverage.json 失败: {e}")

        # 打印关键数字
        if r.error:
            print(f"  ✗ {r.error}")
        else:
            tm_l = r.target_method_line_cov
            tm_b = r.target_method_branch_cov
            cl_l = r.class_line_cov
            print(f"  ✓ 目标方法 line={tm_l}% branch={tm_b}%   类级 line={cl_l}%"
                  f"   耗时={r.duration_s}s")

    # 写 scene 汇总
    scene_out_dir = ISOLATED_ROOT / scene
    scene_out_dir.mkdir(parents=True, exist_ok=True)
    (scene_out_dir / "summary.json").write_text(
        json.dumps(_aggregate_scene(scene, project, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


def _aggregate_scene(scene: str, project: str, rs: List[Dict]) -> Dict:
    """统计 scene 级指标：成功率 + 覆盖率均值/中位数。"""
    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 2) if xs else None

    def _median(xs):
        xs = sorted([x for x in xs if x is not None])
        if not xs:
            return None
        n = len(xs)
        return round(xs[n // 2] if n % 2 == 1 else (xs[n // 2 - 1] + xs[n // 2]) / 2, 2)

    compile_ok = [r for r in rs if r.get("compile_success")]
    run_ok = [r for r in rs if r.get("run_success")]

    return {
        "scene": scene,
        "project": project,
        "total_methods": len(rs),
        "compile_success": len(compile_ok),
        "compile_success_rate": round(len(compile_ok) / len(rs) * 100, 2) if rs else 0.0,
        "run_success": len(run_ok),
        "run_success_rate": round(len(run_ok) / len(rs) * 100, 2) if rs else 0.0,
        # —— 主指标：目标方法级孤立覆盖率 ——
        "mean_target_method_line_cov": _mean([r.get("target_method_line_cov") for r in run_ok]),
        "median_target_method_line_cov": _median([r.get("target_method_line_cov") for r in run_ok]),
        "mean_target_method_branch_cov": _mean([r.get("target_method_branch_cov") for r in run_ok]),
        "median_target_method_branch_cov": _median([r.get("target_method_branch_cov") for r in run_ok]),
        # —— 副指标：目标类级孤立覆盖率 ——
        "mean_class_line_cov": _mean([r.get("class_line_cov") for r in run_ok]),
        "mean_class_branch_cov": _mean([r.get("class_branch_cov") for r in run_ok]),
        "results": rs,
    }


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="孤立覆盖率重放（只跑我们生成的测试）")
    parser.add_argument("--scenes", nargs="+", default=None,
                        help="指定 scene 列表，如 E1_gson E5_cl；默认跑全部")
    parser.add_argument("--all", action="store_true", help="跑 artifacts 下所有 scene")
    parser.add_argument("--dry-run", action="store_true", help="只枚举不实际跑 mvn")
    parser.add_argument("--limit", type=int, default=0,
                        help="每个 scene 最多跑前 N 个方法（冒烟用，0 = 不限制）")
    args = parser.parse_args()

    if args.scenes:
        scenes = args.scenes
    else:
        # 默认全部
        scenes = sorted(
            p.name for p in ARTIFACTS_ROOT.iterdir()
            if p.is_dir() and p.name not in EXCLUDE_SCENES
        )
    print(f"[replay] 将处理 {len(scenes)} 个 scene: {scenes}")

    ISOLATED_ROOT.mkdir(parents=True, exist_ok=True)
    all_results_by_scene: Dict[str, List[Dict]] = {}
    t0 = time.time()

    for scene in scenes:
        try:
            rs = replay_scene(scene, dry_run=args.dry_run, limit=args.limit)
            all_results_by_scene[scene] = rs
        except Exception as e:
            print(f"[FATAL] scene {scene} 崩了: {e}")
            traceback.print_exc()
            all_results_by_scene[scene] = []

    # 总汇总
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_duration_s": round(time.time() - t0, 2),
        "scenes": {
            scene: _aggregate_scene(scene, scene_to_project(scene), rs)
            for scene, rs in all_results_by_scene.items()
            if rs
        },
    }
    out = ROOT / "experiment_results" / "paper_final" / "isolated_coverage_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] 总结写入: {out}")
    print(f"[done] 总耗时: {summary['total_duration_s']}s")

    # 打印简表
    print("\n============ 孤立覆盖率汇总（目标方法级均值）============")
    print(f"{'Scene':<40} {'N':>4}  {'compile%':>9}  {'runOK%':>8}  {'line%':>7}  {'branch%':>8}")
    for scene, agg in summary["scenes"].items():
        print(f"{scene:<40} {agg['total_methods']:>4}  "
              f"{agg['compile_success_rate']:>8.1f}%  "
              f"{agg['run_success_rate']:>7.1f}%  "
              f"{(agg['mean_target_method_line_cov'] or 0):>6.1f}%  "
              f"{(agg['mean_target_method_branch_cov'] or 0):>7.1f}%")


if __name__ == "__main__":
    main()
