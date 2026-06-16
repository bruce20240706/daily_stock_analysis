# -*- coding: utf-8 -*-
"""信号历史方向命中率回填(M2c)。

最小切片：复用已落库的 BacktestResult.direction_correct（由 BacktestEngine 前向
N bar 评估写入）按 code 聚合历史方向命中率与样本数，回填 SignalMarker 的
hit_rate / hit_sample / verified。命中率为历史统计，非未来保证；不做全量滚动回测。

聚合口径严格对齐 src/core/backtest_engine.py 的现有规约：
  分母 = direction_correct is not None 的样本数
  分子 = direction_correct is True 的样本数
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import get_config
from src.repositories.backtest_repo import BacktestRepository


@dataclass(frozen=True)
class HitRate:
    """单类信号的历史方向命中率聚合结果。"""

    hit_rate: Optional[float]  # 0.0~1.0，无样本则 None
    hit_sample: int            # direction_correct is not None 的样本数


def backfill_signal_hit_rate(signal_type: str, code: str) -> HitRate:
    """聚合某 code 的历史方向命中率。

    最小切片下 signal_type 不改变聚合源（统一取该 code 的 completed BacktestResult），
    保留入参以便后续按 signal_type 细分；当前仅用于契约稳定与日志归因。
    """
    repo = BacktestRepository()
    rows = repo.get_completed_results_for_code(code)

    # 终审#1：去重避免杠杆样本虚增。
    # get_completed_results_for_code 不按 engine_version 过滤，会返回同一底层观测的
    # 全部 engine_version 变体（杠杆回测写 v1 / v1-x{leverage}，唯一约束在
    # analysis_history_id + eval_window_days + engine_version）。direction_correct
    # 由 stock_return_pct vs direction_expected 推导，与杠杆无关，故同一
    # (analysis_history_id, eval_window_days) 下的多条变体是同一个方向观测，必须只计
    # 一次，否则永续/杠杆 code（及任何被多 engine_version 回测的 code）的 hit_sample /
    # verified 会被放大。不同 eval_window_days 是不同前向预测，仍各算一个观测。
    deduped: dict[tuple, object] = {}
    for r in rows:
        key = (r.analysis_history_id, r.eval_window_days)
        existing = deduped.get(key)
        # 优先保留 direction_correct 非 None 的代表行，避免完成的 base 行被同组的
        # None 变体遮蔽导致样本反而消失。
        if existing is None or (
            existing.direction_correct is None and r.direction_correct is not None
        ):
            deduped[key] = r
    rows = list(deduped.values())

    sample = sum(1 for r in rows if r.direction_correct is not None)
    if sample == 0:
        return HitRate(hit_rate=None, hit_sample=0)

    correct = sum(1 for r in rows if r.direction_correct is True)
    return HitRate(hit_rate=round(correct / sample, 4), hit_sample=sample)


def resolve_marker_hit_fields(signal_type: str, code: str) -> dict:
    """把命中率聚合结果映射为 SignalMarker 的 hit_rate/hit_sample/verified 字段。

    - 无样本：hit_rate=None, hit_sample=None（对齐 SignalMarker 契约「无样本则 null」），
      verified=False。
    - 有样本：hit_sample 达 signal_hit_verified_min_sample 阈值则 verified=True。
    """
    rate = backfill_signal_hit_rate(signal_type, code)

    if rate.hit_sample <= 0:
        return {"hit_rate": None, "hit_sample": None, "verified": False}

    config = get_config()
    min_sample = int(getattr(config, "signal_hit_verified_min_sample", 0) or 0)
    if min_sample <= 0:
        min_sample = int(getattr(config, "backtest_eval_window_days", 10))

    return {
        "hit_rate": rate.hit_rate,
        "hit_sample": rate.hit_sample,
        "verified": rate.hit_sample >= min_sample,
    }
