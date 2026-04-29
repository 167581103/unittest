"""
core/artifact_logger.py
────────────────────────────────────────────────────────────────────────
实验轨迹归档器（ArtifactLogger）

记录每个方法从 analyze → generate → prefix → eval → fixloop → final
全链路的中间产物，供论文写作/复盘使用。

目录结构：
  <root>/<scene>/<method_id>_<Class>_<method>/
    00_meta.json                      # 方法元信息 + 最终状态
    01_analysis.json                  # analyze_method 输出的 test_cases 设计
    02_skeleton.java                  # （两步式）LLM 生成的骨架
    03_generation_initial.java        # Phase1 产出的拼装完整测试
    04_prefix_roundN_before.java      # Prefix 每轮修改前
    04_prefix_roundN_after.java       # Prefix 每轮修改后
    04_prefix_roundN_stderr.txt       # Prefix 每轮编译错误
    04_prefix_roundN_diff.patch       # before → after 的 unified diff
    05_eval1_compile_stderr.txt       # 首次 evaluator.evaluate 编译错误（若有）
    06_fix_attempt_N_before.java      # FixLoop 第 N 轮修改前
    06_fix_attempt_N_stderr_before    # FixLoop 第 N 轮修改前的编译错误
    06_fix_attempt_N_after.java       # FixLoop 第 N 轮修改后
    06_fix_attempt_N_stderr_after     # FixLoop 第 N 轮修改后的编译错误
    06_fix_attempt_N_diff.patch
    99_FINAL.java                     # 最终落盘的测试代码
    TIMELINE.md                       # 人类可读的轨迹摘要

设计原则：
  1. 线程不安全（pipeline 是串行的 Phase2，Phase1 并发但每个方法独立目录，互不干扰）
  2. 所有写操作吞掉异常；记录失败不能中断主流程
  3. 若未配置 root，所有方法都是 no-op，开销 ~0
"""

from __future__ import annotations

import os
import json
import difflib
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List


class ArtifactLogger:
    """单个方法的轨迹记录器。每个方法一个实例。

    用法：
        logger = ArtifactLogger.create(root, scene, method_id, method_meta)
        logger.log_analysis(analysis_dict)
        logger.log_initial_code(code_str)
        logger.log_prefix_round(1, before, after, stderr)
        logger.log_fix_attempt(1, before, after, stderr_before, stderr_after, rag_context)
        logger.finalize(final_code, status="fix_success")
    """

    # no-op 模式：如果 root 为空，所有方法直接返回
    _NOOP = False

    def __init__(self, method_dir: Path, method_meta: Dict[str, Any]):
        self.method_dir = method_dir
        self.method_meta = method_meta
        self.timeline: List[str] = []
        self._events: List[Dict[str, Any]] = []
        self._start_ts = datetime.now().isoformat()
        self._prev_code: Optional[str] = None  # 用于算 diff

        if not self._NOOP:
            try:
                self.method_dir.mkdir(parents=True, exist_ok=True)
                self._write_meta()
            except Exception as e:
                print(f"[artifact] init failed for {method_dir}: {e}")

    # ──────────────────────────────────────────────────────────────
    # 工厂
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def create(cls, root: Optional[str], scene: str, method_id: str,
               method_meta: Dict[str, Any]) -> "ArtifactLogger":
        """创建 logger；root 为空则返回 no-op logger。"""
        if not root:
            return _NoopLogger()

        try:
            simple_class = method_meta.get("simple_class_name", "Unknown")
            method_name = method_meta.get("method_name", "unknown")
            # 目录名用 <ID>_<Class>_<method>，方便 ls 排序
            safe_name = f"{method_id}_{simple_class}_{method_name}"
            # 防御性处理：文件名里可能含特殊字符
            safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_"
                                for c in safe_name)
            method_dir = Path(root) / scene / safe_name
            return cls(method_dir, {**method_meta, "id": method_id, "scene": scene})
        except Exception as e:
            print(f"[artifact] create failed: {e}")
            return _NoopLogger()

    # ──────────────────────────────────────────────────────────────
    # 事件日志
    # ──────────────────────────────────────────────────────────────
    def _safe_write(self, filename: str, content: str):
        try:
            (self.method_dir / filename).write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"[artifact] write {filename} failed: {e}")

    def _write_meta(self):
        meta = {
            "start_ts": self._start_ts,
            **self.method_meta,
        }
        self._safe_write("00_meta.json",
                         json.dumps(meta, ensure_ascii=False, indent=2))

    def log_event(self, message: str):
        """添加一条时间线事件。"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.timeline.append(f"[{ts}] {message}")

    # ── Phase 1 ────────────────────────────────────────────────────
    def log_analysis(self, analysis: Dict[str, Any]):
        try:
            self._safe_write(
                "01_analysis.json",
                json.dumps(analysis, ensure_ascii=False, indent=2, default=str),
            )
            cases = analysis.get("test_cases") or []
            self.log_event(f"analyze_method: {len(cases)} test cases designed")
        except Exception as e:
            print(f"[artifact] log_analysis failed: {e}")

    def log_skeleton(self, skeleton_code: str):
        """可选：两步式生成的中间骨架。"""
        if skeleton_code:
            self._safe_write("02_skeleton.java", skeleton_code)
            self.log_event(f"skeleton generated ({len(skeleton_code)} chars)")

    def log_initial_code(self, code: str, gen_success: bool, gen_error: Optional[str] = None):
        """Phase1 产出的测试类（未经任何后处理）。"""
        self._safe_write("03_generation_initial.java", code or "")
        if gen_success:
            self.log_event(f"generate_test: ok ({len(code or '')} chars)")
        else:
            self.log_event(f"generate_test: FAILED — {gen_error}")
        self._prev_code = code or ""

    # ── Phase 2: Prefix ────────────────────────────────────────────
    def log_prefix_round(
        self,
        round_idx: int,
        before_code: str,
        after_code: str,
        compile_stderr: str,
        note: str = "",
    ):
        before_name = f"04_prefix_round{round_idx}_before.java"
        after_name = f"04_prefix_round{round_idx}_after.java"
        stderr_name = f"04_prefix_round{round_idx}_stderr.txt"
        diff_name = f"04_prefix_round{round_idx}_diff.patch"

        self._safe_write(before_name, before_code or "")
        self._safe_write(after_name, after_code or "")
        self._safe_write(stderr_name, compile_stderr or "")
        self._safe_write(diff_name, _unified_diff(before_code, after_code,
                                                   before_name, after_name))
        changed = before_code != after_code
        suffix = f" — {note}" if note else ""
        self.log_event(
            f"prefix round {round_idx}: changed={changed}{suffix}"
        )
        self._prev_code = after_code

    def log_prefix_summary(self, rounds: int, compile_ok: bool,
                            changes_applied: int, prefix_log: List[str]):
        """Prefix 阶段整体总结（不落代码，只落 log）。"""
        summary = {
            "rounds": rounds,
            "compile_ok": compile_ok,
            "changes_applied": changes_applied,
            "log": prefix_log or [],
        }
        self._safe_write("04_prefix_summary.json",
                         json.dumps(summary, ensure_ascii=False, indent=2))
        self.log_event(
            f"prefix DONE: rounds={rounds}, changes={changes_applied}, "
            f"compile_ok={compile_ok}"
        )

    # ── Phase 2: Eval-1 ────────────────────────────────────────────
    def log_eval1_stderr(self, stderr: str):
        """首次 evaluator 编译的 stderr（供归因）。"""
        if stderr:
            self._safe_write("05_eval1_compile_stderr.txt", stderr)
            self.log_event("eval-1 compile failed; FixLoop will start")
        else:
            self.log_event("eval-1 compile ok; skip FixLoop")

    # ── Phase 2: FixLoop ───────────────────────────────────────────
    def log_fix_attempt(
        self,
        attempt_idx: int,
        before_code: str,
        after_code: str,
        stderr_before: str,
        stderr_after: str,
        rag_context: Optional[str] = None,
        decision_note: str = "",
    ):
        before_name = f"06_fix_attempt_{attempt_idx}_before.java"
        after_name = f"06_fix_attempt_{attempt_idx}_after.java"

        self._safe_write(before_name, before_code or "")
        self._safe_write(after_name, after_code or "")
        self._safe_write(f"06_fix_attempt_{attempt_idx}_stderr_before.txt",
                         stderr_before or "")
        self._safe_write(f"06_fix_attempt_{attempt_idx}_stderr_after.txt",
                         stderr_after or "")
        self._safe_write(
            f"06_fix_attempt_{attempt_idx}_diff.patch",
            _unified_diff(before_code, after_code, before_name, after_name),
        )
        if rag_context:
            self._safe_write(f"06_fix_attempt_{attempt_idx}_rag_context.txt",
                             rag_context)

        # 错误行数变化，快速感知"有没有变好"
        n_err_before = _count_error_lines(stderr_before)
        n_err_after = _count_error_lines(stderr_after)
        ok = not stderr_after or "[ERROR]" not in stderr_after
        self.log_event(
            f"fix attempt {attempt_idx}: {n_err_before} → {n_err_after} errors"
            + (" ✓ compile passed" if ok else "")
            + (f" [{decision_note}]" if decision_note else "")
        )
        self._prev_code = after_code

    def log_fix_summary(self, fix_success: bool, n_attempts: int,
                         fix_log: List[str]):
        summary = {
            "fix_success": fix_success,
            "n_attempts": n_attempts,
            "fix_log": fix_log or [],
        }
        self._safe_write("06_fix_summary.json",
                         json.dumps(summary, ensure_ascii=False, indent=2))
        self.log_event(
            f"fix DONE: success={fix_success}, attempts={n_attempts}"
        )

    # ── Finalize ───────────────────────────────────────────────────
    def finalize(self, final_code: str, status: str,
                  coverage_info: Optional[Dict[str, Any]] = None):
        """写最终测试代码 + 更新 meta + 写 TIMELINE.md。

        status: "gen_failed" | "initial_ok" | "prefix_rescued"
                | "fix_success" | "fix_failed"
        """
        self._safe_write("99_FINAL.java", final_code or "")
        self.log_event(f"FINAL: status={status}")

        # 更新 meta
        try:
            meta_file = self.method_dir / "00_meta.json"
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["end_ts"] = datetime.now().isoformat()
            meta["status"] = status
            if coverage_info:
                meta["coverage"] = coverage_info
            meta_file.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[artifact] finalize meta update failed: {e}")

        # 写 TIMELINE
        try:
            lines = [f"# Timeline — {self.method_meta.get('id')} "
                     f"{self.method_meta.get('simple_class_name')}."
                     f"{self.method_meta.get('method_name')}",
                     "", f"Status: **{status}**", ""]
            for ev in self.timeline:
                lines.append(f"- {ev}")
            (self.method_dir / "TIMELINE.md").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        except Exception as e:
            print(f"[artifact] timeline write failed: {e}")


class _NoopLogger(ArtifactLogger):
    """空实现：未启用 --artifact-root 时使用，开销 ~0。"""

    # 跳过父类 __init__（避免建目录）
    def __init__(self):
        self.method_dir = None
        self.method_meta = {}
        self.timeline = []
        self._events = []
        self._start_ts = ""
        self._prev_code = None

    def log_analysis(self, *a, **kw): pass
    def log_skeleton(self, *a, **kw): pass
    def log_initial_code(self, *a, **kw): pass
    def log_prefix_round(self, *a, **kw): pass
    def log_prefix_summary(self, *a, **kw): pass
    def log_eval1_stderr(self, *a, **kw): pass
    def log_fix_attempt(self, *a, **kw): pass
    def log_fix_summary(self, *a, **kw): pass
    def log_event(self, *a, **kw): pass

    def finalize(self, *a, **kw): pass


# ══════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════

def _unified_diff(a: str, b: str, a_name: str = "before", b_name: str = "after") -> str:
    """生成 unified diff 文本。a/b 可能为 None 或空串。"""
    a_lines = (a or "").splitlines(keepends=True)
    b_lines = (b or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines, b_lines, fromfile=a_name, tofile=b_name, n=3,
    )
    return "".join(diff)


def _count_error_lines(stderr: Optional[str]) -> int:
    """粗略统计 [ERROR] / error: 行数。"""
    if not stderr:
        return 0
    n = 0
    for ln in stderr.split("\n"):
        s = ln.strip()
        if "[ERROR]" in s and ".java:" in s:
            n += 1
        elif "error:" in s.lower() and ".java:" in s:
            n += 1
    return n
