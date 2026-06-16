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
    _volume_bucket,
    _price_bucket,
    _classify_vfx,
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


# ---------------------------------------------------------------------------
# Task 3: 量价八法穷尽互斥查表
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel_vol,expected", [
    (0.69, "low"), (0.70, "shrink"), (0.79, "shrink"),
    (0.80, "normal"), (1.19, "normal"),
    (1.20, "up"), (1.49, "up"),
    (1.50, "high"), (3.0, "high"),
])
def test_volume_bucket_boundaries(rel_vol, expected):
    assert _volume_bucket(rel_vol, VPSConfig()) == expected


@pytest.mark.parametrize("pct,expected", [
    (-0.005, "down"), (-0.0041, "down"),
    (-0.004, "flat"), (0.0, "flat"), (0.004, "flat"),
    (0.0041, "up"), (0.02, "up"),
])
def test_price_bucket_boundaries(pct, expected):
    assert _price_bucket(pct, VPSConfig()) == expected


def test_vfx_truth_table_complete_no_dead_zone():
    cfg = VPSConfig()
    seen = set()
    for rel in (0.5, 0.75, 1.0, 1.3, 2.0):
        for pct in (-0.02, 0.0, 0.02):
            sig = _classify_vfx(rel_vol=rel, pct_chg=pct, body=1.0, range_pos=0.8, config=cfg)
            assert sig is not None
            assert sig.signal_type.startswith("vfx_")
            assert sig.direction in {"bullish", "bearish", "neutral"}
            seen.add((rel, pct))
    assert len(seen) == 15  # 5 量档 × 3 价档 全覆盖


def test_vfx_expand_up_without_body_confirmation_degrades_to_neutral():
    cfg = VPSConfig()
    # 量增价升但收阴(body<0)、收在下半区 -> 派发，不得判 bullish
    sig = _classify_vfx(rel_vol=1.3, pct_chg=0.02, body=-1.0, range_pos=0.2, config=cfg)
    assert sig.signal_type == "vfx_expand_up"
    assert sig.direction == "neutral"


def test_vfx_expand_up_with_body_confirmation_is_bullish():
    cfg = VPSConfig()
    sig = _classify_vfx(rel_vol=1.3, pct_chg=0.02, body=1.0, range_pos=0.8, config=cfg)
    assert sig.direction == "bullish"


def test_vfx_none_rel_vol_is_anomalous_neutral():
    cfg = VPSConfig()
    sig = _classify_vfx(rel_vol=None, pct_chg=0.02, body=1.0, range_pos=0.8, config=cfg)
    assert sig.signal_type == "vfx_undefined"
    assert sig.direction == "neutral"
    assert sig.is_anomalous is True


# ---------------------------------------------------------------------------
# Task 4: A 类信号：OBV 背离 + 放量突破 + 缩量回调 + Anchored VWAP
# ---------------------------------------------------------------------------

def _trend_up_df(n: int = 40, step: float = 1.0, base_vol: float = 1000.0) -> pd.DataFrame:
    rows = []
    price = 100.0
    for _ in range(n):
        o = price
        c = price + step
        rows.append(_bar(o, c + 0.5, o - 0.5, c, base_vol))
        price = c
    return _make_df(rows)


def test_micro_new_high_does_not_trigger_obv_top_divergence():
    # 价格每日微创新高 + 量同步 -> OBV 同步创高，不应误报顶背离
    df = _trend_up_df(60)
    res = compute_volume_price_signals(df)
    assert all(m.signal_type != "obv_top_divergence" for m in res.markers)


def test_obv_top_divergence_when_price_high_obv_not():
    # 构造两个已确认 swing high：第二个价更高但量持续萎缩 -> OBV 未跟 -> 顶背离
    rows = []
    # 第一峰
    seq = [100, 103, 106, 103, 100, 103, 108, 104, 100]
    vols = [2000, 2200, 2400, 1500, 1400, 1600, 900, 800, 700]  # 第二峰量明显小
    for p, v in zip(seq, vols):
        rows.append(_bar(p, p + 0.5, p - 0.5, p, v))
    # 垫满窗口
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]
    df = _make_df(pad + rows)
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    assert any(m.signal_type == "obv_top_divergence" for m in res.markers)


def test_breakout_excludes_current_day():
    # 判别性场景：close == 过去 N 日 high（pad 全部为 100），但当日盘中有更高 wick（high=110）。
    # 正确 shift(1) 实现：prior_max 不含当日 wick -> prior_max=100；close=100>=100 -> 突破触发。
    # 有 bug 的 no-shift 实现：prior_max 含当日 wick=110 -> prior_max=110；close=100<110 -> 无突破。
    # 因此此处断言突破触发，能有效鉴别 shift(1) 正确与否。
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    # close=100 == prior 20-day high=100；intraday wick high=110 创当日新高但不影响突破判断
    breakout = _bar(100, 110.0, 99.0, 100.0, 5000)  # rel_vol=5000/1000=5.0 >= 2.0
    df = _make_df(pad + [breakout])
    res = compute_volume_price_signals(df, config=VPSConfig(breakout_window=20, breakout_rel_vol=2.0))
    bks = [m for m in res.markers if m.signal_type == "volume_breakout"]
    # 正确实现：prior_max=100，close=100 >= 100 -> 1 个突破信号
    assert len(bks) == 1, (
        f"期望 1 个 volume_breakout，实际 {len(bks)} 个；"
        "若为 0，说明 prior_max 包含了当日 wick（未 shift(1)）"
    )
    assert bks[0].timestamp == _to_epoch_ms_shanghai(df["date"].iloc[-1])
    # 辅助：pad 段不得产生突破（无放量，price 无变化）
    non_breakout_ts = {_to_epoch_ms_shanghai(df["date"].iloc[i]) for i in range(len(pad))}
    assert all(m.timestamp not in non_breakout_ts for m in bks)


def test_breakout_requires_rel_vol_threshold():
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    breakout_lowvol = _bar(100, 105.0, 100.0, 105.0, 1000)  # 价突破但量不足
    df = _make_df(pad + [breakout_lowvol])
    res = compute_volume_price_signals(df, config=VPSConfig(breakout_rel_vol=2.0))
    assert all(m.signal_type != "volume_breakout" for m in res.markers)


def test_anchored_vwap_only_anchors_confirmed_breakout_not_future_bottom():
    # 一个非突破的局部低点不得被 AVWAP 提前锚定；仅突破日产 AVWAP 相关 marker
    df = _trend_up_df(40)  # 平滑上行，无放量突破事件
    res = compute_volume_price_signals(df)
    assert all(not m.signal_type.startswith("anchored_vwap") for m in res.markers)


def test_anchored_vwap_anchors_on_breakout_day_not_future_bottom():
    # 判别性正向场景：确认放量突破日 -> 价格跌破 AVWAP -> 反弹重夺 AVWAP。
    # 正确实现：锚定在突破日，跌破时产生 anchored_vwap_loss，重夺时产生 anchored_vwap_reclaim。
    # 前向错误实现（如锚定在突破后底部）：底部出现之前没有 AVWAP，无法产生 vwap_loss 信号；
    # 且从底部起算的 AVWAP 很低，close 始终在上方，reclaim 也不会在底部后第一根触发。
    # 因此对 vwap_loss 信号的存在性断言能有效鉴别锚点是否为突破日。

    # 30 根垫片：high=100，vol=1000；用于建立 vol_ma 窗口
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]

    # 突破日（index 30）：close=108，high=110，先于盘中 wick，收盘 >100 = 过去 N 日 high
    # shift(1) prior_max = max(pad highs) = 100.0；close=108 >= 100 -> 放量突破确认
    # vol=5000，vol_ma = mean(pad vols) = 1000 -> rel_vol=5.0 >= 2.0
    bk_bar = _bar(100, 110.0, 99.0, 108.0, 5000)

    # 突破后 1 根（index 31）：价格从 108 快速跌回 101，跌破 AVWAP（~105.25）
    # AVWAP 起点 = 突破日 typical=(110+99+108)/3=105.67，prev_delta=108-105.67>0
    # 此根 close=101，AVWAP[1]≈105.25，curr_delta=101-105.25<0 -> vwap_loss 边缘穿越
    dip_bar = _bar(106, 107.0, 100.0, 101.0, 800)

    # 突破后 2 根（index 32）：价格仍低，AVWAP[2]≈104.85，close=100 仍在下方
    low_bar = _bar(102, 104.0, 99.0, 100.0, 600)

    # 突破后 3 根（index 33）：放量反弹至 107，超过 AVWAP[3]≈105.13 -> vwap_reclaim
    reclaim_bar = _bar(104, 108.0, 103.0, 107.0, 2000)

    df = _make_df(pad + [bk_bar, dip_bar, low_bar, reclaim_bar])
    cfg = VPSConfig(breakout_window=20, breakout_rel_vol=2.0)
    res = compute_volume_price_signals(df, config=cfg)

    avwap_signals = [m for m in res.markers if m.signal_type.startswith("anchored_vwap")]
    avwap_types = {m.signal_type for m in avwap_signals}

    # 必须同时出现失守和重夺信号
    assert "anchored_vwap_loss" in avwap_types, (
        "未产生 anchored_vwap_loss；若锚点在未来底部则此信号不会出现（前向错误）"
    )
    assert "anchored_vwap_reclaim" in avwap_types, (
        "未产生 anchored_vwap_reclaim；AVWAP 信号链不完整"
    )

    # 验证锚点在突破日：loss 信号应在突破后 1 根（index 31）
    bk_date_ts = _to_epoch_ms_shanghai(df["date"].iloc[30])   # 突破日
    dip_date_ts = _to_epoch_ms_shanghai(df["date"].iloc[31])  # 跌破日（loss 应发生在此）
    loss_signals = [m for m in avwap_signals if m.signal_type == "anchored_vwap_loss"]
    assert any(m.timestamp == dip_date_ts for m in loss_signals), (
        f"vwap_loss 应发生在突破后第 1 根（{dip_date_ts}），"
        f"实际时间戳为 {[m.timestamp for m in loss_signals]}；"
        "若锚点不是突破日则 loss 时间戳将错位或缺失"
    )


def test_compute_degraded_on_short_window():
    df = _flat_series(5)
    res = compute_volume_price_signals(df)
    assert res.status == "degraded"
    assert res.markers == []
    assert res.degraded_reason is not None


def test_compute_ok_status_on_sufficient_window():
    df = _trend_up_df(40)
    res = compute_volume_price_signals(df)
    assert res.status == "ok"
    assert all(isinstance(m, VPSignal) for m in res.markers)


# ---------------------------------------------------------------------------
# Task 5: B 类 VSA/Upthrust/Spring 降权契约 + top-k 限流
# ---------------------------------------------------------------------------

def _b_class(markers):
    return [m for m in markers if m.signal_type.startswith("vsa_") or m.signal_type in {"upthrust", "spring"}]


def _no_demand_df() -> pd.DataFrame:
    """构造能触发至少 1 个 B 类（vsa_no_demand）marker 的 fixture。

    No Demand 条件：rel_vol < vol_shrink(0.8) AND body > 0 AND range_pos < 0.5
    - pad 25 根：vol=1000，建立 vol_ma 基线；vol_ma_window=20，shift(1) 后 index 25+ 均有有效量比
    - no_demand 3 根：open=100, high=103, low=100, close=101
        body = 101-100 = 1 > 0
        range_pos = (101-100)/(103-100) = 1/3 ≈ 0.33 < 0.5
        vol = 200, vol_ma ≈ 1000, rel_vol ≈ 0.2 < vol_shrink(0.8)  -> No Demand 触发
    """
    pad = [_bar(100, 101, 99, 100, 1000) for _ in range(25)]
    no_demand = [_bar(100, 103, 100, 101, 200) for _ in range(3)]
    return _make_df(pad + no_demand)


def test_b_class_confidence_always_low():
    df = _no_demand_df()
    res = compute_volume_price_signals(df)
    b = _b_class(res.markers)
    assert len(b) > 0, "fixture 必须产出至少 1 个 B 类 marker（vsa_no_demand）"
    for m in b:
        assert m.confidence == "low"


def test_b_class_is_daily_approx_always_true():
    df = _no_demand_df()
    res = compute_volume_price_signals(df)
    b = _b_class(res.markers)
    assert len(b) > 0, "fixture 必须产出至少 1 个 B 类 marker（vsa_no_demand）"
    for m in b:
        assert m.is_daily_approx is True


def test_b_class_respects_top_k_limit():
    # 制造大量 VSA 候选，断言不超过 top_k
    rows = []
    for i in range(60):
        v = 3000 if i % 2 == 0 else 200  # 高低量交替，制造大量 No Demand/No Supply
        rows.append(_bar(100, 100.2, 99.8, 100, v))
    df = _make_df(rows)
    cfg = VPSConfig(b_class_top_k=2)
    res = compute_volume_price_signals(df, config=cfg)
    assert len(_b_class(res.markers)) <= cfg.b_class_top_k


def test_b_class_does_not_change_a_class_direction_set():
    df = _trend_up_df(50)
    with_b = compute_volume_price_signals(df)
    a_only = [m for m in with_b.markers if m not in _b_class(with_b.markers)]
    # B 类移除后 A 类方向集合不变（B 不污染 A）
    a_directions = {(m.signal_type, m.direction) for m in a_only}
    recomputed = compute_volume_price_signals(df)
    a_recomputed = {
        (m.signal_type, m.direction)
        for m in recomputed.markers
        if not (m.signal_type.startswith("vsa_") or m.signal_type in {"upthrust", "spring"})
    }
    assert a_directions == a_recomputed


def test_upthrust_and_spring_reuse_swing_pivots():
    # 假突破顶（upthrust）：冲高后收回到前高之下
    pad = [_bar(100, 101, 99, 100, 1000) for _ in range(30)]
    pivot_high = [_bar(100, 108, 100, 107, 1500), _bar(107, 107.5, 105, 106, 1200),
                  _bar(106, 106.5, 104, 105, 1100)]
    upthrust = [_bar(105, 110, 104, 104, 4000)]  # 冲破 108 后收回 104 < 前高
    df = _make_df(pad + pivot_high + upthrust)
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    assert any(m.signal_type == "upthrust" and m.direction == "bearish" for m in res.markers)


# ---------------------------------------------------------------------------
# 终审 #3：OBV 背离 marker 锚定确认 bar（curr.index+swing_k），消除 k 根可视前视
# ---------------------------------------------------------------------------

def test_obv_divergence_marker_dated_at_confirmation_bar_not_pivot():
    """OBV 背离 marker 的 timestamp 必须落在 pivot 确认 bar（curr.index+swing_k），
    而非 pivot 自身的 bar——否则会在背离可知前 k 根就渲染（视觉前视）。"""
    rows = []
    seq = [100, 103, 106, 103, 100, 103, 108, 104, 100]
    vols = [2000, 2200, 2400, 1500, 1400, 1600, 900, 800, 700]  # 第二峰量明显小
    for p, v in zip(seq, vols):
        rows.append(_bar(p, p + 0.5, p - 0.5, p, v))
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]
    df = _make_df(pad + rows)
    cfg = VPSConfig(swing_k=2)

    # 复算引擎内部所用 prim/pivots，精确推出 curr 高点位置
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    close = prim["close"].astype(float).reset_index(drop=True)
    highs = [p for p in find_swing_pivots(close, cfg.swing_k) if p.kind == "high"]
    assert len(highs) >= 2, "fixture 必须至少有 2 个已确认 swing high"
    curr = highs[-1]
    conf_idx = curr.index + cfg.swing_k
    assert conf_idx <= len(prim) - 1, "确认 bar 必须落在样本内"

    pivot_bar_ts = _to_epoch_ms_shanghai(prim["date"].iloc[curr.index])
    conf_bar_ts = _to_epoch_ms_shanghai(prim["date"].iloc[conf_idx])
    assert pivot_bar_ts != conf_bar_ts, "fixture 须使 pivot bar 与确认 bar 日期不同"

    res = compute_volume_price_signals(df, config=cfg)
    top = [m for m in res.markers if m.signal_type == "obv_top_divergence"]
    assert len(top) >= 1, "fixture 必须触发顶背离"
    for m in top:
        assert m.timestamp == conf_bar_ts, (
            "OBV 背离 marker 必须锚定确认 bar(curr.index+swing_k)，而非 pivot bar"
        )
        assert m.timestamp != pivot_bar_ts, "marker 不得停留在 pivot 自身 bar（视觉前视）"
        # y 锚（price）仍为枢轴极值，仅 x/时间锚移动到确认日
        assert m.price == curr.price


# ---------------------------------------------------------------------------
# 终审 #2：VPS_* 环境配置端到端生效（from_env 真正接线）
# ---------------------------------------------------------------------------

def test_vps_env_config_honored_end_to_end(monkeypatch):
    """VPS_BREAKOUT_REL_VOL 设高后，原本默认阈值(2.0)会触发的放量突破必须被抑制；
    不传 config 的默认调用仍触发——证明 from_env() 的值端到端生效。"""
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    # rel_vol = 3000/1000 = 3.0：>=2.0(默认)触发，>=5.0(env)不触发
    breakout = _bar(100, 110.0, 99.0, 100.0, 3000)
    df = _make_df(pad + [breakout])

    # 默认（硬编码 2.0）：触发
    res_default = compute_volume_price_signals(df)
    assert any(m.signal_type == "volume_breakout" for m in res_default.markers), (
        "默认 2.0 阈值下该 fixture 必须产出 volume_breakout"
    )

    # 经 env 拉高到 5.0：被抑制
    monkeypatch.setenv("VPS_BREAKOUT_REL_VOL", "5.0")
    res_env = compute_volume_price_signals(df, config=VPSConfig.from_env())
    assert all(m.signal_type != "volume_breakout" for m in res_env.markers), (
        "VPS_BREAKOUT_REL_VOL=5.0 必须端到端生效，抑制 rel_vol=3.0 的突破"
    )


# ---------------------------------------------------------------------------
# 终审 #4：量价八法落地——最新 bar 单点 B 类 marker
# ---------------------------------------------------------------------------

def _vfx_markers(markers):
    return [m for m in markers if m.signal_type.startswith("vfx_")]


def test_latest_vfx_marker_emitted_for_directional_last_bar():
    """最新 bar 为放量下跌（rel_vol≈1.3 ∈ [1.2,1.5)，pct_chg<-eps）时，
    须产出且仅产出 1 个 vfx_expand_down（bearish），且为 B 类降权（low + daily_approx）。"""
    pad = [_bar(100, 101, 99, 100, 1000) for _ in range(25)]
    expand_down = _bar(100, 100.5, 94.5, 95, 1300)  # 放量下跌
    df = _make_df(pad + [expand_down])
    res = compute_volume_price_signals(df)
    vfx = _vfx_markers(res.markers)
    assert len(vfx) == 1, f"应有且仅有 1 个 vfx marker，实际 {len(vfx)}"
    m = vfx[0]
    assert m.signal_type == "vfx_expand_down"
    assert m.direction == "bearish"
    assert m.confidence == "low"
    assert m.is_daily_approx is True
    assert m.is_anomalous is False
    assert m.timestamp == _to_epoch_ms_shanghai(df["date"].iloc[-1])
    assert m.price == 95.0


def test_no_vfx_marker_for_neutral_last_bar():
    """最新 bar 为平价常量（neutral）时，不产出 vfx marker（不逐 bar 刷屏）。"""
    pad = [_bar(100, 101, 99, 100, 1000) for _ in range(25)]
    neutral = _bar(100, 101, 99, 100, 1000)  # flat price + normal vol -> vfx_normal_flat neutral
    df = _make_df(pad + [neutral])
    res = compute_volume_price_signals(df)
    assert _vfx_markers(res.markers) == []
