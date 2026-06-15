# -*- coding: utf-8 -*-
"""Unit tests for the volume-price signal engine (M1)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import math
import numpy as np
import pandas as pd
import pytest

from src.services.volume_price_signals import (
    VPSConfig,
    VPSResult,
    VPSignal,
    Pivot,
    compute_volume_price_signals,
    find_swing_pivots,
    atr,
    _compute_primitives,
    _normalize,
    _to_epoch_ms_shanghai,
)

SH = ZoneInfo("Asia/Shanghai")


def _make_df(rows: list[dict], start: str = "2024-01-01") -> pd.DataFrame:
    """Build an OHLCV df with consecutive calendar dates (string date column)."""
    base = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i, r in enumerate(rows):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, **r})
    return pd.DataFrame(out)


def _bar(open_, high, low, close, volume) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _flat_series(n: int, price: float = 100.0, volume: float = 1000.0) -> pd.DataFrame:
    return _make_df([_bar(price, price + 1, price - 1, price, volume) for _ in range(n)])


def test_to_epoch_ms_uses_shanghai_midnight():
    ms = _to_epoch_ms_shanghai("2024-01-01")
    expected = int(datetime(2024, 1, 1, tzinfo=SH).timestamp() * 1000)
    assert ms == expected


def test_primitives_shift_one_no_lookahead():
    # vol_ma at row t must NOT include volume[t]
    df = _make_df([_bar(10, 11, 9, 10, v) for v in (100, 200, 300, 400, 500)])
    norm, reason = _normalize(df, VPSConfig())
    assert reason is None
    prim = _compute_primitives(norm, VPSConfig(vol_ma_window=2))
    # vol_ma[2] = mean(volume[0],volume[1]) = 150, NOT mean including volume[2]
    assert prim["vol_ma"].iloc[2] == pytest.approx(150.0)
    assert prim["rel_vol"].iloc[2] == pytest.approx(300.0 / 150.0)


def test_primitives_limit_bar_flagged_and_range_pos_none():
    df = _make_df([_bar(10, 10, 10, 10, 100)] * 3)  # 一字板 high==low
    norm, _ = _normalize(df, VPSConfig())
    prim = _compute_primitives(norm, VPSConfig())
    assert bool(prim["is_limit_bar"].iloc[-1]) is True
    assert pd.isna(prim["range_pos"].iloc[-1])


def test_primitives_rel_vol_none_when_vol_ma_zero():
    df = _make_df([_bar(10, 11, 9, 10, 0)] * 5)  # zero volume -> vol_ma 0
    norm, _ = _normalize(df, VPSConfig(vol_ma_window=2))
    prim = _compute_primitives(norm, VPSConfig(vol_ma_window=2))
    assert pd.isna(prim["rel_vol"].iloc[-1])


def test_normalize_missing_volume_returns_degraded_reason():
    df = pd.DataFrame({"date": ["2024-01-01"], "open": [1], "high": [1], "low": [1], "close": [1]})
    norm, reason = _normalize(df, VPSConfig())
    assert norm.empty
    assert reason is not None and "volume" in reason


def test_normalize_insufficient_window_returns_degraded_reason():
    # 窗口不足时 compute_volume_price_signals 应返回 degraded（insufficient 在主入口检查）
    df = _flat_series(5)  # fewer than vol_ma_window default 20
    result = compute_volume_price_signals(df, config=VPSConfig())
    assert result.status == "degraded"
    assert result.degraded_reason is not None and "insufficient" in result.degraded_reason.lower()


def test_find_swing_pivots_lags_by_k_no_lookahead():
    # V 形：低点在 index 3，k=2 需 index 5 才确认 -> pivot.index==3 但只有右侧 2 根确认后才产出
    closes = [10, 9, 8, 5, 8, 9, 10]
    s = pd.Series(closes)
    pivots = find_swing_pivots(s, k=2)
    lows = [p for p in pivots if p.kind == "low"]
    assert any(p.index == 3 and p.price == 5 for p in lows)
    # 最后 k 根不可能成为已确认 pivot（右侧确认不足）
    assert all(p.index <= len(closes) - 1 - 2 for p in pivots)


def test_find_swing_pivots_micro_new_high_is_not_pivot():
    # 每日微创新高（单调上升）-> 无 swing high pivot（永远没有右侧更低确认）
    s = pd.Series([float(i) for i in range(20)])
    pivots = find_swing_pivots(s, k=3)
    assert [p for p in pivots if p.kind == "high"] == []


def test_find_swing_pivots_detects_high_and_low():
    closes = [1, 2, 3, 2, 1, 2, 3, 4, 3, 2]
    pivots = find_swing_pivots(pd.Series(closes), k=2)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    assert any(p.index == 2 for p in highs)
    assert any(p.index == 4 for p in lows)


def test_atr_matches_wilder_manual():
    # Test data: H=[10,12,13,14], L=[8,9,11,12], C=[9,11,12,13], period=2
    # TR  = [2.0, 3.0, 2.0, 2.0]
    #   TR[0] = 10-8 = 2.0  (no prev close, use H-L only)
    #   TR[1] = max(12-9, |12-9|, |9-9|) = max(3,3,0) = 3.0
    #   TR[2] = max(13-11, |13-11|, |11-11|) = max(2,2,0) = 2.0
    #   TR[3] = max(14-12, |14-12|, |12-12|) = max(2,2,0) = 2.0
    # Canonical Wilder ATR (period=2):
    #   ATR[0] = NaN
    #   ATR[1] = (2.0+3.0)/2 = 2.5        (SMA seed)
    #   ATR[2] = (2.5*1 + 2.0) / 2 = 2.25
    #   ATR[3] = (2.25*1 + 2.0) / 2 = 2.125
    df = pd.DataFrame({
        "high": [10, 12, 13, 14],
        "low": [8, 9, 11, 12],
        "close": [9, 11, 12, 13],
    })
    out = atr(df, period=2)
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == pytest.approx(2.5)
    assert out.iloc[2] == pytest.approx(2.25)
    assert out.iloc[3] == pytest.approx(2.125)
    assert (out.dropna() > 0).all()
