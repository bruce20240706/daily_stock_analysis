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

    sample = sum(1 for r in rows if r.direction_correct is not None)
    if sample == 0:
        return HitRate(hit_rate=None, hit_sample=0)

    correct = sum(1 for r in rows if r.direction_correct is True)
    return HitRate(hit_rate=round(correct / sample, 4), hit_sample=sample)
