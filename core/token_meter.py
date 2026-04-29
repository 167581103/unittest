#!/usr/bin/env python3
"""token_meter.py —— 全进程共享的 LLM token 计量器。

目的：在不破坏现有 `chat()` 返回字符串签名的前提下，准确记录
每一次 LLM 调用的 prompt/completion token 数，并按"方法 + 阶段"
维度聚合，供 run_batch 写入实验 JSON。

核心设计：
  - 用 ContextVar 存 "当前方法 + 当前阶段"；协程天然隔离，
    asyncio.gather 并发跑多个方法时不会互相污染。
  - `record_usage()` 每次由 chat() 在拿到 response.usage 后调用，
    会向 GlobalAccumulator 的 (method_id, phase) 槽里累加。
  - run_batch 在每个方法开始前通过 `scoped(method_id, phase)` 上下文
    管理器切换作用域；结束时 `snapshot(method_id)` 取出该方法所有阶段的
    token 汇总。

约定：若 `method_id` 为 None，则记录到特殊桶 "__global__"；
不会抛异常干扰主流程（记账失败就 swallow）。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional

# ─── 当前执行作用域（每个协程一份）──────────────────────────────────
_CURRENT_METHOD: contextvars.ContextVar[Optional[str]] = \
    contextvars.ContextVar("_utg_method_id", default=None)
_CURRENT_PHASE: contextvars.ContextVar[str] = \
    contextvars.ContextVar("_utg_phase", default="unspecified")


@dataclass
class PhaseBucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> Dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class MethodMeter:
    by_phase: Dict[str, PhaseBucket] = field(default_factory=dict)

    def total(self) -> PhaseBucket:
        agg = PhaseBucket()
        for b in self.by_phase.values():
            agg.calls += b.calls
            agg.prompt_tokens += b.prompt_tokens
            agg.completion_tokens += b.completion_tokens
        return agg

    def as_dict(self) -> Dict:
        t = self.total()
        return {
            "total_calls": t.calls,
            "total_prompt_tokens": t.prompt_tokens,
            "total_completion_tokens": t.completion_tokens,
            "total_tokens": t.total_tokens,
            "by_phase": {k: v.as_dict() for k, v in self.by_phase.items()},
        }


class GlobalAccumulator:
    """进程级单例，保存 method_id -> MethodMeter 的全量统计。"""

    def __init__(self) -> None:
        self._data: Dict[str, MethodMeter] = {}
        self._lock = Lock()
        self._warnings: List[str] = []

    def record(self, method_id: Optional[str], phase: str,
               prompt_tokens: int, completion_tokens: int) -> None:
        key = method_id or "__global__"
        with self._lock:
            mm = self._data.setdefault(key, MethodMeter())
            pb = mm.by_phase.setdefault(phase or "unspecified", PhaseBucket())
            pb.calls += 1
            pb.prompt_tokens += int(prompt_tokens or 0)
            pb.completion_tokens += int(completion_tokens or 0)

    def snapshot(self, method_id: Optional[str]) -> Dict:
        key = method_id or "__global__"
        with self._lock:
            mm = self._data.get(key)
            if mm is None:
                return MethodMeter().as_dict()
            # 返回深拷贝，避免外部修改
            return mm.as_dict()

    def full_snapshot(self) -> Dict[str, Dict]:
        with self._lock:
            return {k: v.as_dict() for k, v in self._data.items()}

    def reset(self, method_id: Optional[str] = None) -> None:
        with self._lock:
            if method_id is None:
                self._data.clear()
            else:
                self._data.pop(method_id or "__global__", None)

    def note_warning(self, msg: str) -> None:
        self._warnings.append(msg)

    @property
    def warnings(self) -> List[str]:
        return list(self._warnings)


METER = GlobalAccumulator()


# ─── 公共 API ───────────────────────────────────────────────────────

def record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """由 llm.chat() 在拿到 response.usage 后调用。失败不抛。"""
    try:
        METER.record(
            _CURRENT_METHOD.get(),
            _CURRENT_PHASE.get(),
            prompt_tokens,
            completion_tokens,
        )
    except Exception as e:  # noqa: BLE001
        METER.note_warning(f"record_usage failed: {e}")


def snapshot(method_id: Optional[str]) -> Dict:
    return METER.snapshot(method_id)


def full_snapshot() -> Dict[str, Dict]:
    return METER.full_snapshot()


def reset(method_id: Optional[str] = None) -> None:
    METER.reset(method_id)


@contextmanager
def scoped(method_id: Optional[str], phase: str = "unspecified"):
    """设置当前协程的 method_id + phase；退出时自动还原。"""
    tok_m = _CURRENT_METHOD.set(method_id)
    tok_p = _CURRENT_PHASE.set(phase)
    try:
        yield
    finally:
        _CURRENT_METHOD.reset(tok_m)
        _CURRENT_PHASE.reset(tok_p)


@contextmanager
def phase(name: str):
    """只切换 phase，保留当前 method_id。用于在一个方法内部细分阶段。"""
    tok = _CURRENT_PHASE.set(name)
    try:
        yield
    finally:
        _CURRENT_PHASE.reset(tok)


def current_method() -> Optional[str]:
    return _CURRENT_METHOD.get()


def current_phase() -> str:
    return _CURRENT_PHASE.get()


# ─── 低层 API（给不方便用 with 上下文管理器的调用点）─────────────
# 外部可直接 set/reset ContextVar，用 try/finally 管理生命周期。
#   tok = set_scope(method_id="M01", phase="gen")
#   try: ...
#   finally: reset_scope(tok)
def set_scope(method_id: Optional[str], phase: str = "unspecified"):
    """设置作用域，返回 (method_token, phase_token)，供 reset_scope 还原。"""
    tok_m = _CURRENT_METHOD.set(method_id)
    tok_p = _CURRENT_PHASE.set(phase)
    return (tok_m, tok_p)


def reset_scope(tokens) -> None:
    tok_m, tok_p = tokens
    try:
        _CURRENT_PHASE.reset(tok_p)
    finally:
        _CURRENT_METHOD.reset(tok_m)
