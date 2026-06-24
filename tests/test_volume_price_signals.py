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
    _divergence_strength_grade,
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


# ---------------------------------------------------------------------------
# M3-B1: CMF / MFI 量能指标纯函数
# ---------------------------------------------------------------------------

from src.services.volume_price_signals import _cmf, _mfi


def test_cmf_all_closes_at_high_is_positive():
    n = 30
    df = pd.DataFrame({"high": [10]*n, "low": [8]*n, "close": [10]*n, "volume": [1000]*n})
    s = _cmf(df["high"], df["low"], df["close"], df["volume"], window=20)
    assert s.iloc[-1] > 0  # 收在最高 → 资金流为正


def test_mfi_bounded_0_100():
    """MFI 结果应在 [0, 100] 内；使用正弦波 zigzag 确保大多数窗口同时含上涨和下跌 bar，
    断言是非空的（non-vacuous）——避免全上涨序列导致 dropna 结果为空。"""
    n = 60
    t = np.linspace(0, 4 * np.pi, n)
    close = pd.Series(100.0 + 10.0 * np.sin(t))  # 正弦波，含上涨和下跌
    df = pd.DataFrame({"high": close * 1.01, "low": close * 0.99, "close": close, "volume": [1000] * n})
    s = _mfi(df["high"], df["low"], df["close"], df["volume"], window=14)
    v = s.dropna()
    assert len(v) > 0, "dropna 结果不得为空：MFI 必须在非单调序列上产出有效值"
    assert ((v >= 0) & (v <= 100)).all(), f"MFI 值超出 [0,100]：{v[~((v >= 0) & (v <= 100))]}"


def test_mfi_all_up_window_is_100():
    """全上涨窗口（neg flow == 0, pos > 0）按标准 MFI 定义应返回 100.0，不得为 NaN。"""
    n = 20
    # 单调递增序列：每根 bar 的典型价格都高于前一根，neg flow 始终为 0
    close = pd.Series([float(100 + i) for i in range(n)])
    df = pd.DataFrame({"high": close + 1.0, "low": close - 1.0, "close": close, "volume": [1000] * n})
    s = _mfi(df["high"], df["low"], df["close"], df["volume"], window=14)
    # 最后一根落在全上涨窗口内，MFI 应为 100.0
    last = s.iloc[-1]
    assert last == pytest.approx(100.0), f"全上涨窗口 MFI 应为 100.0，实际 {last}"


def test_mfi_flat_window_is_nan():
    """完全平坦序列（pos == 0 且 neg == 0）：资金流向未定义，MFI 应保持 NaN。"""
    n = 20
    close = pd.Series([100.0] * n)
    df = pd.DataFrame({"high": close + 1.0, "low": close - 1.0, "close": close, "volume": [1000] * n})
    s = _mfi(df["high"], df["low"], df["close"], df["volume"], window=14)
    assert s.dropna().empty, "完全平坦序列 MFI 应全为 NaN（资金流向未定义）"


# ---------------------------------------------------------------------------
# M3-B2: 多源背离共振 + 强度分级
# ---------------------------------------------------------------------------

def _df_with_only_obv_divergence() -> pd.DataFrame:
    """构造仅 OBV 背离、CMF/MFI 不背离的顶背离 fixture。

    设计要点：
    - 第一峰 (idx 31, 价格 106)：14-bar 窗口内有一根下跌日 (idx 29)，使 MFI < 100 且 CMF 较低。
    - 第一峰后：10 根"宽幅收高但 close 略低于前收"的 OBV 拖累棒（close < prev_close，
      high = close，low = close - 10，vol = 4000）→ OBV 方向 = -1 * 4000，CMF/MFI 不受影响。
    - 拖累棒全部落在第二峰 (idx 57) 的 14-bar 窗口 [idx 44..57] 之外，不污染 CMF/MFI。
    - 14 根小量上涨棒 + 第二峰 (108，close 贴近 bar 顶，宽幅高量) → CMF/MFI 窗口全正，
      CMF2 > CMF1，MFI2 = 100 > MFI1 < 100 → CMF/MFI 均不背离。
    - OBV 累积值：peak2 (−33800) << peak1 (5000) → OBV 背离。
    - 结论：k = 1（单源）。
    """
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(29)]
    # 第 29 根：下跌日，拉低 peak1 窗口的 MFI/CMF 基线
    down_before_peak1 = _bar(100, 100.5, 97.5, 98, 2000)
    # 第一峰区域 (idx 30-33)
    seq1 = [
        _bar(98,  101.5, 97.5, 101, 4000),    # idx 30: up
        _bar(101, 106.5, 100.5, 106, 3000),   # idx 31: PEAK1 price=106
        _bar(106, 106.5, 102.5, 103, 1500),   # idx 32: down confirm1
        _bar(103, 103.5,  99.5, 100, 1500),   # idx 33: down confirm2
    ]
    # 10 根 OBV 拖累棒 (idx 34-43)：
    # close = prev-0.5，high = close，low = close-10，vol = 4000
    # → OBV 方向 = -1（close < prev_close），MFM = 1.0（close 贴近 bar 顶）
    drag_bars = []
    p = 100.0
    for _ in range(10):
        c = p - 0.5
        drag_bars.append(_bar(p, c, c - 10.0, c, 4000))
        p = c
    # 14 根小量上涨棒 (idx 44-57)：价格从 ~95 线性回升至 108
    rise_bars = []
    price_step = (108.0 - p) / 14.0
    for _ in range(14):
        c = p + price_step
        rise_bars.append(_bar(p, c + 0.5, p - 0.2, c, 300))
        p = c
    # 第二峰 (idx 58)：price = 108 > 106，宽幅收顶，高量 → CMF/MFI 强
    peak2 = _bar(p, p + 1.0, p - 0.5, 108.0, 4000)
    conf1 = _bar(108.0, 108.5, 105.0, 105.5, 1000)
    conf2 = _bar(105.5, 106.0, 102.0, 102.5, 1000)
    return _make_df(pad + [down_before_peak1] + seq1 + drag_bars + rise_bars + [peak2, conf1, conf2])


def _df_with_obv_cmf_mfi_all_diverging() -> pd.DataFrame:
    """构造 OBV + CMF + MFI 全部顶背离的 fixture（复用既有 3 源背离 fixture）。

    第二峰量明显萎缩（vol: 2400→900），CMF 和 MFI 的 14-bar 窗口均因量能下滑而背离。
    """
    seq = [100, 103, 106, 103, 100, 103, 108, 104, 100]
    vols = [2000, 2200, 2400, 1500, 1400, 1600,  900,  800, 700]
    rows = [_bar(p, p + 0.5, p - 0.5, p, v) for p, v in zip(seq, vols)]
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]
    return _make_df(pad + rows)


def test_single_source_divergence_does_not_emit_high_confidence():
    """单源顶背离（仅 OBV 背离，CMF/MFI 不背离）必须不出 high 置信；
    emit-low 规则下应出现 low 置信的 obv_top_divergence 信号。"""
    df = _df_with_only_obv_divergence()
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    # 仅检查顶背离 marker（fixture 专门针对 top divergence，bottom divergence 可能有其他 k）
    top_div = [m for m in res.markers if m.signal_type == "obv_top_divergence"]
    assert len(top_div) > 0, (
        "单源 OBV 顶背离 fixture 必须至少产出 1 个 obv_top_divergence marker（emit-low 规则）；"
        "若为空说明单源被完全抑制——与本实现的 emit-low 选择不符"
    )
    assert all(m.confidence != "high" for m in top_div), (
        f"单源顶背离不得出 high 置信，实际 confidences={[m.confidence for m in top_div]}"
    )
    # emit-low 规则：单源出 low 置信弱提示
    assert all(m.confidence == "low" for m in top_div), (
        f"单源顶背离（emit-low 规则）必须出 low 置信，实际={[m.confidence for m in top_div]}"
    )


def test_multi_source_resonance_emits_higher_confidence_and_strength():
    """三源共振（OBV+CMF+MFI 全背离）必须出 high 置信，且 reason 包含强度档标签。"""
    df = _df_with_obv_cmf_mfi_all_diverging()
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    div = [m for m in res.markers if "divergence" in m.signal_type]
    assert len(div) > 0, "三源共振 fixture 必须产出至少 1 个 divergence marker"
    assert any(m.confidence == "high" for m in div), (
        f"三源共振必须出 high 置信，实际={[m.confidence for m in div]}"
    )
    # reason 中必须含强度档标签
    strength_labels = {"weak", "medium", "strong"}
    assert any(
        any(lbl in m.reason for lbl in strength_labels) for m in div
    ), (
        f"divergence marker 的 reason 必须含强度档（weak/medium/strong），"
        f"实际 reasons={[m.reason for m in div]}"
    )


def _df_with_obv_cmf_diverging_mfi_not() -> pd.DataFrame:
    """构造恰好 OBV + CMF 背离、MFI 不背离的顶背离 fixture（k=2）。

    关键设计约束（顶背离条件：cmp_ind = lambda a,b: a<=b，即 curr <= prev 才算背离）：
    - OBV 背离 (obv_curr <= obv_prev)：peak1 前大量上涨（OBV1 高）；peak2 前 OBV 被
      "宽幅大量向下"棒拖累，peak2 仅小量上涨，OBV2 << OBV1 ✓
    - CMF 背离 (cmf_curr <= cmf_prev)：peak2 的 14-bar 窗口（idx 51..64）含"负 MFM 大量"
      棒（open 高、high 很高、close 偏低于 bar 中位）→ CMF2 < CMF1 ✓
    - MFI 不背离 (mfi_curr > mfi_prev)：
      * peak1 窗口（idx 20..33）含 idx 30 大量下跌棒 → MFI1 < 100
      * peak2 窗口（idx 51..64）内 TP 每棒严格递增（虽然 MFM 负，但 TP 仍升）→ MFI 只看
        TP 方向（delta=tp[t]-tp[t-1]），TP 递增 → pos flow 计入 → MFI2 = 100 ✓

    "负 MFM 但 TP 递增" 棒设计：open=p, high=p+10(大 wick), low=p-1, close=p+0.5
      MFM = ((close-low)-(high-close))/(high-low)
            = ((0.5+1)-(10-0.5))/(10+1+1) ≈ (1.5-9.5)/11 ≈ -0.727 → 负 MFM → CMF 负贡献
      TP = (high+low+close)/3 = (p+10 + p-1 + p+0.5)/3 = p + 9.5/3 ≈ p + 3.17
      若 p 每棒递增，TP 也递增 → MFI pos flow → MFI2 = 100 ✓

    构造（pad=30, idx 30=大量下跌拉低 MFI1, idx 31-35=peak1区,
          idx 36-50=OBV 拖累期（在 peak2 窗口之外）,
          idx 51-63=负 MFM 但 TP 递增棒×13 + idx 64=peak2, idx 65-66=确认棒）：
    窗口：peak2=idx 64，CMF/MFI 14-bar 窗口 = idx 51..64 ✓
    """
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]

    # idx 30：大量下跌棒，进入 peak1 的 14-bar 窗口，使 MFI1 < 100
    down_before_peak1 = _bar(100, 100.5, 95.5, 96, 5000)

    # peak1 区域 (idx 31-35)：3 根大量上涨 + 峰顶 (close=112) + 2 根下跌确认
    peak1_zone = [
        _bar(96,  100.5,  95.5, 100, 5000),  # idx 31
        _bar(100, 106.5,  99.5, 106, 5000),  # idx 32
        _bar(106, 112.5, 105.5, 112, 5000),  # idx 33: PEAK1 price=112
        _bar(112, 112.5, 108.5, 109, 1000),  # idx 34: down confirm1
        _bar(109, 109.5, 105.5, 106, 1000),  # idx 35: down confirm2
    ]
    # peak1 窗口(idx 20..33)：含 idx30 大量下跌 → MFI1 < 100（neg flow 进入）

    # OBV 拖累期 (idx 36-50, 15 根)：close 贴底，大量 → OBV 大跌，不在 peak2 窗口内
    # 这使 OBV2 << OBV1（peak2 = idx 64, 窗口 idx 51..64，拖累棒在 36..50 之外）
    obv_drag = []
    p = 106.0
    step = (94.0 - p) / 15.0
    for _ in range(15):
        c = p + step
        obv_drag.append(_bar(p, p + 0.2, c - 0.2, c, 5000))  # close 贴底，OBV 大跌
        p = c
    # OBV 从 peak1 高位跌至极低（−75000）

    # 进入 peak2 14-bar 窗口的棒（idx 51-63, 13 根）:
    # "负 MFM 但 TP 递增"棒：open=p，high=p+10，low=p-1，close=p+0.5，每棒 p 递增 1.5
    # MFM = (1.5-9.5)/(10+1+1) ≈ -0.727 → CMF 负贡献（每棒大量 2000）
    # TP = (p+10+p-1+p+0.5)/3 = p+3.17，每棒 p 递增 → TP 递增 → MFI pos flow → MFI2=100
    # OBV：close (p+0.5) > prev_close (p)? 若 p 每棒+1.5，则 close[t]=p+0.5 > p-1=prev_open
    # 实际 close[t]=p+0.5，prev_close = 前棒 close = (p-1.5)+0.5 = p-1 → close[t]=p+0.5 > p-1 ✓
    # OBV 方向 +1（但量小 → OBV 增量小）
    neg_cmf_bars = []
    p = 94.0
    for _ in range(13):
        # bar: open=p, high=p+10, low=p-1, close=p+0.5, vol=2000
        neg_cmf_bars.append(_bar(p, p + 10.0, p - 1.0, p + 0.5, 2000))
        p += 1.5  # price steps up so TP increases

    # peak2 (idx 64, close=115 > 112, OBV 仅微增)
    peak2 = _bar(p, p + 10.0, p - 1.0, 115.0, 100)   # 极小量，OBV 微增，CMF 仍负
    conf1  = _bar(115.0, 115.5, 111.5, 112.0, 500)
    conf2  = _bar(112.0, 112.5, 108.5, 109.0, 500)

    return _make_df(pad + [down_before_peak1] + peak1_zone + obv_drag + neg_cmf_bars + [peak2, conf1, conf2])


def test_two_source_divergence_emits_medium_confidence():
    """双源共振（k=2，OBV+CMF 背离，MFI 不背离）必须出 medium 置信，且不得出 high。

    验证顺序：
    1. 先通过引擎内部指标计算确认 fixture 确实产生 k=2（不是 k=3 或 k=1）。
    2. 再断言 divergence marker 的 confidence == "medium"。
    """
    df = _df_with_obv_cmf_diverging_mfi_not()
    cfg = VPSConfig(swing_k=2)

    # --- 内部验证：确认 k=2（OBV+CMF 背离，MFI 不背离）---
    from src.services.volume_price_signals import (
        _normalize, _compute_primitives, _obv, _cmf, _mfi,
        find_swing_pivots, _DIV_CMF_WINDOW, _DIV_MFI_WINDOW,
    )
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    close = prim["close"].astype(float).reset_index(drop=True)
    vol   = prim["volume"].astype(float).reset_index(drop=True)
    high  = prim["high"].astype(float).reset_index(drop=True)
    low_s = prim["low"].astype(float).reset_index(drop=True)

    obv_s = _obv(close, vol)
    cmf_s = _cmf(high, low_s, close, vol, _DIV_CMF_WINDOW)
    mfi_s = _mfi(high, low_s, close, vol, _DIV_MFI_WINDOW)

    highs = [p for p in find_swing_pivots(close, cfg.swing_k) if p.kind == "high"]
    assert len(highs) >= 2, f"fixture 须有至少 2 个 swing high，实际 {len(highs)}"
    prev_p, curr_p = highs[-2], highs[-1]
    assert curr_p.price > prev_p.price, "fixture 须使第二峰价格更高（顶背离前提）"

    obv_prev, obv_curr = float(obv_s.iloc[prev_p.index]), float(obv_s.iloc[curr_p.index])
    cmf_prev, cmf_curr = float(cmf_s.iloc[prev_p.index]), float(cmf_s.iloc[curr_p.index])
    mfi_prev, mfi_curr = float(mfi_s.iloc[prev_p.index]), float(mfi_s.iloc[curr_p.index])

    obv_div = obv_curr <= obv_prev
    cmf_div = cmf_curr <= cmf_prev
    mfi_div = mfi_curr <= mfi_prev and not math.isnan(mfi_curr) and not math.isnan(mfi_prev)
    k_actual = sum([obv_div, cmf_div, mfi_div])

    assert k_actual == 2, (
        f"fixture 须产生 k=2，实际 k={k_actual}；"
        f"obv_div={obv_div}({obv_prev:.1f}→{obv_curr:.1f}), "
        f"cmf_div={cmf_div}({cmf_prev:.4f}→{cmf_curr:.4f}), "
        f"mfi_div={mfi_div}({mfi_prev:.1f}→{mfi_curr:.1f})"
    )

    # --- 引擎断言：k=2 → confidence=="medium" ---
    res = compute_volume_price_signals(df, config=cfg)
    top_div = [m for m in res.markers if m.signal_type == "obv_top_divergence"]
    assert len(top_div) > 0, "k=2 fixture 须产出 obv_top_divergence marker"
    assert any(m.confidence == "medium" for m in top_div), (
        f"k=2 背离须出 medium 置信，实际={[m.confidence for m in top_div]}"
    )
    assert all(m.confidence != "high" for m in top_div), (
        f"k=2 背离不得出 high 置信，实际={[m.confidence for m in top_div]}"
    )


# ---------------------------------------------------------------------------
# Fix 2: k=1 底背离单源测试（OBV 背离，CMF/MFI 不背离）
# ---------------------------------------------------------------------------

def _df_with_only_obv_bottom_divergence() -> pd.DataFrame:
    """构造仅 OBV 底背离、CMF/MFI 不背离的底背离 fixture（k=1，bullish）。

    关键设计约束（底背离条件：cmp_ind = lambda a,b: a>=b，即 curr >= prev 才算背离）：
    - OBV 底背离 (obv_curr >= obv_prev)：valley1 的大量下跌使 OBV1 极低；大量反弹使
      OBV 升高；valley2 的下跌量极小 → OBV2 远高于 OBV1 ✓
    - CMF 不背离 (cmf_curr < cmf_prev)：valley2 的 14-bar 窗口全为大量下跌棒（MFM 负）
      → CMF2 强负；valley1 的窗口含部分上涨棒 → CMF1 < |CMF2| → CMF2 < CMF1 ✓
    - MFI 不背离 (mfi_curr < mfi_prev)：valley1 窗口含大量上涨棒（up_bar）→ MFI1 > 0；
      valley2 窗口全为下跌棒 → MFI2 = 0 < MFI1 ✓

    关键设计：valley2 的 14-bar MFI 窗口（idx [V2-13..V2]）必须全是下跌棒，
    且 valley1 的窗口含至少一根 TP 上涨棒（up_bar）。
    OBV 约束：大量反弹（recovery 5 根 × 10000 = +50000）远超 valley1 累积下跌，
    valley2 只用极小量（200）下跌 → OBV2 仍远高于 OBV1。
    """
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]

    # idx 30：大量上涨棒，进入 valley1 的 14-bar MFI 窗口，使 MFI1 > 0
    # （valley1 确认 bar 约为 idx 33，14-bar 窗口 = idx 20..33，含 idx 30）
    up_bar = _bar(100, 105.5, 99.5, 105, 8000)   # idx 30: 大量上涨 → MFI pos flow 大

    # valley1 区域：3 根极大量下跌 + 谷底 (close=88) + 2 根上涨确认
    # 大量下跌使 OBV1 极低（e.g. 每根 -8000 × 3 = -24000 + up_bar +8000 = OBV1 ≈ -16000）
    valley1_zone = [
        _bar(105, 105.5, 100.5, 101, 8000),  # idx 31: 下跌
        _bar(101, 101.5,  94.5,  95, 8000),  # idx 32: 下跌
        _bar(95,   95.5,  87.5,  88, 8000),  # idx 33: VALLEY1 price=88（极大量，OBV1极低）
        _bar(88,   93.5,  87.5,  93, 2000),  # idx 34: up confirm1
        _bar(93,   98.5,  92.5,  98, 2000),  # idx 35: up confirm2
    ]
    # valley1 窗口含 idx 30 大量上涨 → MFI1 > 0（不是全上涨也不是 100）

    # 大量反弹：5 根极高量上涨，把 OBV 从极低拉到极高
    # 反弹后 OBV 大幅高于 valley1 水平
    recovery = [
        _bar(98,  102.5,  97.5, 102, 10000),  # idx 36
        _bar(102, 106.5, 101.5, 106, 10000),  # idx 37
        _bar(106, 110.5, 105.5, 110, 10000),  # idx 38
        _bar(110, 114.5, 109.5, 114, 10000),  # idx 39
        _bar(114, 118.5, 113.5, 118, 10000),  # idx 40
    ]
    # 此时 OBV 约 = OBV_base + recovery_net ≈ +50000（极高）

    # 14 根大量下跌棒，构成 valley2 的 14-bar MFI/CMF 窗口（idx 41-54）：
    # close 贴底（MFM 强负）→ CMF2 强负、MFI2 = 0；
    # vol=5000（大量）→ OBV 从高位下跌 5000×14=-70000，OBV2 = 50000-70000+基线...
    # 需 OBV2 >= OBV1：OBV1 ≈ -16000（极低），recovery 后 ≈ +34000，fall: 5000×14=70000下跌
    # 最终 OBV2 ≈ 34000-70000 = -36000 < OBV1 ≈ -16000 → 不满足！
    # 解决：缩小 fall_bars vol（减小到 500，14×500=7000下跌）
    # OBV2 = 34000 - 7000 - 500 = 26500 >> OBV1 = -16000 ✓
    fall_bars = []
    p = 118.0
    step = (86.0 - p) / 14.0
    for _ in range(14):
        c = p + step
        # close 贴近 bar 底（MFM 负 → CMF2 负，MFI pos flow = 0）
        fall_bars.append(_bar(p, p + 0.2, c - 0.2, c, 500))
        p = c
    # valley2 的 14-bar 窗口 (idx 41..54)：全为下跌棒 → MFI2=0 < MFI1 > 0 ✓
    # CMF2 全负 < CMF1（CMF1 窗口含 up_bar 正流）✓

    # valley2 (close=85 < 88, vol=500) + 2 根上涨确认
    valley2 = _bar(p, p + 0.2, p - 0.2, 85.0, 500)   # VALLEY2 price=85 < 88
    conf1 = _bar(85.0, 89.5, 84.5, 89.0, 1000)
    conf2 = _bar(89.0, 93.5, 88.5, 93.0, 1000)

    return _make_df(pad + [up_bar] + valley1_zone + recovery + fall_bars + [valley2, conf1, conf2])


def test_bottom_single_source_obv_divergence_emits_low_confidence():
    """单源底背离（仅 OBV 背离，CMF/MFI 不背离，k=1）：
    - 产出 obv_bottom_divergence marker
    - confidence == "low"（emit-low 规则，k=1 弱提示）
    覆盖 _detect_obv_divergence 中 kind='low' / cmp_ind=lambda a,b: a>=b 的 >= 比较分支。
    """
    df = _df_with_only_obv_bottom_divergence()
    cfg = VPSConfig(swing_k=2)

    # --- 内部验证：确认 k=1（OBV 背离，CMF/MFI 不背离）---
    from src.services.volume_price_signals import (
        _normalize, _compute_primitives, _obv, _cmf, _mfi,
        find_swing_pivots, _DIV_CMF_WINDOW, _DIV_MFI_WINDOW,
    )
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    close = prim["close"].astype(float).reset_index(drop=True)
    vol   = prim["volume"].astype(float).reset_index(drop=True)
    high  = prim["high"].astype(float).reset_index(drop=True)
    low_s = prim["low"].astype(float).reset_index(drop=True)

    obv_s = _obv(close, vol)
    cmf_s = _cmf(high, low_s, close, vol, _DIV_CMF_WINDOW)
    mfi_s = _mfi(high, low_s, close, vol, _DIV_MFI_WINDOW)

    lows = [p for p in find_swing_pivots(close, cfg.swing_k) if p.kind == "low"]
    assert len(lows) >= 2, f"fixture 须有至少 2 个 swing low，实际 {len(lows)}"
    prev_p, curr_p = lows[-2], lows[-1]
    assert curr_p.price < prev_p.price, "fixture 须使第二谷价格更低（底背离前提）"

    obv_prev, obv_curr = float(obv_s.iloc[prev_p.index]), float(obv_s.iloc[curr_p.index])
    cmf_prev, cmf_curr = float(cmf_s.iloc[prev_p.index]), float(cmf_s.iloc[curr_p.index])
    mfi_prev, mfi_curr = float(mfi_s.iloc[prev_p.index]), float(mfi_s.iloc[curr_p.index])

    # 底背离：cmp_ind = lambda a, b: a >= b（curr >= prev 才算背离）
    obv_div = obv_curr >= obv_prev
    cmf_div = cmf_curr >= cmf_prev and not math.isnan(cmf_curr) and not math.isnan(cmf_prev)
    mfi_div = mfi_curr >= mfi_prev and not math.isnan(mfi_curr) and not math.isnan(mfi_prev)
    k_actual = sum([obv_div, cmf_div, mfi_div])

    assert k_actual == 1, (
        f"fixture 须产生 k=1（仅 OBV 底背离），实际 k={k_actual}；"
        f"obv_div={obv_div}({obv_prev:.1f}→{obv_curr:.1f}), "
        f"cmf_div={cmf_div}({cmf_prev:.4f}→{cmf_curr:.4f}), "
        f"mfi_div={mfi_div}({mfi_prev:.1f}→{mfi_curr:.1f})"
    )

    # --- 引擎断言：k=1 → obv_bottom_divergence，confidence=="low" ---
    res = compute_volume_price_signals(df, config=cfg)
    bot_div = [m for m in res.markers if m.signal_type == "obv_bottom_divergence"]
    assert len(bot_div) > 0, (
        "单源 OBV 底背离 fixture 须产出 obv_bottom_divergence marker（emit-low 规则）"
    )
    assert all(m.confidence == "low" for m in bot_div), (
        f"k=1 底背离须出 low 置信，实际={[m.confidence for m in bot_div]}"
    )


# ---------------------------------------------------------------------------
# Fix 3: _divergence_strength_grade 单元测试（各源量纲归一化后阈值固定）
# ---------------------------------------------------------------------------

def test_divergence_strength_grade_bounded_inputs():
    """验证 _divergence_strength_grade 在各源归一化后的阈值语义（[0,1] 输入）。

    rel_divs 应为归一化后的值：
      - OBV：|curr-prev|/max(|curr|,|prev|,1) ∈ [0,1]
      - CMF：|curr-prev|/2 ∈ [0,1]
      - MFI：|curr-prev|/100 ∈ [0,1]

    规则：
      - k==3 且 avg > 0.15 → "strong"
      - k>=2 且 avg > 0.05 → "medium"
      - 其余              → "weak"
    """
    # strong：k=3，avg=(0.20+0.20+0.20)/3=0.20 > 0.15
    assert _divergence_strength_grade(3, [0.20, 0.20, 0.20]) == "strong"

    # 刚好不够 strong：k=3，avg=0.15 不严格 > 0.15 → medium（avg>0.05）
    assert _divergence_strength_grade(3, [0.15, 0.15, 0.15]) == "medium"

    # medium：k=2，avg=(0.10+0.10)/2=0.10 > 0.05
    assert _divergence_strength_grade(2, [0.10, 0.10]) == "medium"

    # weak：k=2，avg=(0.03+0.03)/2=0.03 <= 0.05
    assert _divergence_strength_grade(2, [0.03, 0.03]) == "weak"

    # weak：k=1，任何幅度（k<2，不满足 medium 条件）
    assert _divergence_strength_grade(1, [0.50]) == "weak"

    # weak：空 rel_divs
    assert _divergence_strength_grade(0, []) == "weak"

    # 边界：k=3，avg 刚好超过 0.15
    assert _divergence_strength_grade(3, [0.16, 0.16, 0.14]) == "strong"  # avg=0.1533>0.15

    # 归一化输入上界（值=1.0），k=2 → medium（avg=1.0 > 0.05）
    assert _divergence_strength_grade(2, [1.0, 1.0]) == "medium"


# ---------------------------------------------------------------------------
# M3-B3: 量能形态分级（classify_volume_pattern）边界测试
# ---------------------------------------------------------------------------

def test_volume_pattern_classification_boundaries():
    """验证 classify_volume_pattern 在各量档 × 价格方向组合下的边界输出。

    阈值对齐说明（均来自 VPSConfig 默认值，无魔法数字）：
    - climax_volume : rel_vol >= vol_high(1.5)，价格上涨(pct_chg > eps=0.004)
    - dry_up        : rel_vol <  vol_low(0.7)，量能极度萎缩
    - shrink_pullback: rel_vol ∈ [vol_low(0.7), vol_shrink(0.8))，pct_chg < -eps
                       且 abs(pct_chg) <= 1.5 * atr_norm（ATR 边界守卫）
    - mild_expand   : rel_vol ∈ [vol_up(1.2), vol_high(1.5))，pct_chg > eps
    - normal        : rel_vol ∈ [vol_shrink(0.8), vol_up(1.2))，pct_chg ≈ 0（flat）

    shrink_pullback ATR 守卫（k=1.5）：
    - atr_norm=0.5 时：abs(-0.02)=0.02 <= 1.5*0.5=0.75 → shrink_pullback（通过）
    - atr_norm=0.01 时：abs(-0.02)=0.02 > 1.5*0.01=0.015 → 落入 normal（atr_norm 决定性）
    """
    from src.services.volume_price_signals import classify_volume_pattern, VPSConfig
    c = VPSConfig()
    assert classify_volume_pattern(3.0, 0.05, 1.0, c) == "climax_volume"    # 天量+上涨
    assert classify_volume_pattern(0.5, -0.01, 0.3, c) == "dry_up"          # 地量
    assert classify_volume_pattern(0.7, -0.02, 0.5, c) == "shrink_pullback" # 缩量回踩（ATR边界内）
    assert classify_volume_pattern(1.3, 0.02, 0.6, c) == "mild_expand"      # 温和放量
    assert classify_volume_pattern(1.0, 0.0, 0.5, c) == "normal"            # 常规


def test_shrink_pullback_atr_norm_is_decisive():
    """atr_norm 对 shrink_pullback 判定具有决定性：
    相同的 rel_vol（缩量档）和 pct_chg（下跌），仅 atr_norm 不同时结果必须分叉。

    场景：rel_vol=0.75（∈ [vol_low(0.7), vol_shrink(0.8))），pct_chg=-0.02（下跌）
    - atr_norm=0.5（足够大）：abs(-0.02)=0.02 <= 1.5*0.5=0.75 → shrink_pullback
    - atr_norm=0.01（极小）：abs(-0.02)=0.02 > 1.5*0.01=0.015 → 跌幅超出 ATR 边界，非温和缩量回踩
      此时 fall through 到 normal，证明 atr_norm 是决定性参数，而非仅上下文保留字段。
    """
    from src.services.volume_price_signals import classify_volume_pattern, VPSConfig
    c = VPSConfig()
    # 同一 rel_vol / pct_chg，atr_norm 充足 → shrink_pullback
    assert classify_volume_pattern(0.75, -0.02, 0.5, c) == "shrink_pullback"
    # 同一 rel_vol / pct_chg，atr_norm 极小（跌幅 >> 1.5 * atr_norm）→ 非 shrink_pullback
    result = classify_volume_pattern(0.75, -0.02, 0.01, c)
    assert result != "shrink_pullback", (
        f"atr_norm=0.01 时 abs(pct_chg)=0.02 > 1.5*0.01=0.015，"
        f"不应判定为 shrink_pullback，实际返回 '{result}'"
    )
    assert result == "normal", (
        f"跌幅超出 ATR 边界时应 fall through 到 normal，实际返回 '{result}'"
    )


# ---------------------------------------------------------------------------
# Task B4: VPSConfig.for_market — crypto 量价参数差异化
# ---------------------------------------------------------------------------

def test_for_market_crypto_uses_crypto_values(monkeypatch):
    """market='crypto' 时，for_market 返回的 config 应将主动字段替换为 crypto_* 值。"""
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    monkeypatch.setenv("VPS_CRYPTO_ATR_PERIOD", "7")
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_REL_VOL", "1.5")
    cfg = VPSConfig.for_market("crypto")
    assert cfg.breakout_window == 30
    assert cfg.atr_period == 7
    assert cfg.breakout_rel_vol == pytest.approx(1.5)


def test_for_market_non_crypto_equals_from_env(monkeypatch):
    """market='cn'（或任意非 crypto）时，for_market 结果与 from_env() 字节一致。"""
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW", "25")
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    cfg_market = VPSConfig.for_market("cn")
    cfg_env = VPSConfig.from_env()
    assert cfg_market == cfg_env


def test_for_market_unknown_falls_back(monkeypatch):
    """market=None 或 'unknown' 时，for_market 回落到 from_env()（等价）。"""
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW", "18")
    cfg_none = VPSConfig.for_market(None)
    cfg_unknown = VPSConfig.for_market("unknown")
    cfg_env = VPSConfig.from_env()
    assert cfg_none == cfg_env
    assert cfg_unknown == cfg_env


# === 链路B 分钟化:_to_epoch_ms_shanghai 分钟分辨率感知(B-T1)===
def test_to_epoch_date_only_is_midnight_unchanged():
    """纯日期(字符串/午夜 Timestamp/显式 00:00:00)→ 当日午夜,日线语义不变。"""
    a = _to_epoch_ms_shanghai("2026-06-22")
    assert _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22")) == a   # 午夜 Timestamp 与日期串一致
    assert _to_epoch_ms_shanghai("2026-06-22 00:00:00") == a        # 显式午夜 = 日期串


def test_to_epoch_minute_preserves_time():
    """分钟 bar 保留时分秒,不坍缩到午夜——信号触发对齐在分钟粒度上成立的前提。"""
    t0935 = _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22 09:35:00"))
    t0940 = _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22 09:40:00"))
    midnight = _to_epoch_ms_shanghai("2026-06-22")
    assert t0935 != t0940 and t0935 != midnight        # 分钟 bar 不坍缩到午夜
    assert (t0940 - t0935) == 5 * 60 * 1000            # 5 分钟差
    assert _to_epoch_ms_shanghai("2026-06-22 09:35:00") == t0935   # 带时间字符串同样保留


def test_to_epoch_iso_t_separated_string_preserves_time():
    """ISO 'T' 分隔的分钟字符串也保留时分(不坍缩到午夜)——硬化字符串解析路径。"""
    t_space = _to_epoch_ms_shanghai("2026-06-22 09:35:00")
    midnight = _to_epoch_ms_shanghai("2026-06-22")
    assert _to_epoch_ms_shanghai("2026-06-22T09:35:00") == t_space   # 'T' 与空格等价
    assert _to_epoch_ms_shanghai("2026-06-22T09:35:00") != midnight  # 不坍缩到午夜
