# tests/test_signal_eval_vectorization.py
import numpy as np
import pandas as pd
import pytest
from src.services.volume_price_signals import derive_price_levels, derive_price_levels_series


def _synthetic_df(n, seed):
    rng = np.random.default_rng(seed)
    close = 50 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.uniform(1e3, 5e3, n)
    dates = pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


def test_vsa_rows_equiv_original():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_vsa_bars, _detect_vsa_bars_rows)
    df = _synthetic_df(120, seed=7)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    ref = _detect_vsa_bars(prim, cfg)
    got = _detect_vsa_bars_rows(prim, cfg)
    assert [s for _, s in got] == ref
    # 行号正确:marker.timestamp 来自该行 date
    for i, s in got:
        from src.services.volume_price_signals import _to_epoch_ms_shanghai
        assert s.timestamp == _to_epoch_ms_shanghai(prim["date"].iloc[i])


def test_price_levels_series_matches_per_window():
    df = _synthetic_df(80, seed=1)
    series = derive_price_levels_series(df)
    assert len(series) == len(df)
    for t in range(len(df)):
        ref = derive_price_levels(df.iloc[: t + 1])
        got = series[t]
        assert got.entry == pytest.approx(ref.entry) if ref.entry is not None else got.entry == ref.entry
        assert got.stop == pytest.approx(ref.stop) if ref.stop is not None else got.stop == ref.stop
        assert got.target == pytest.approx(ref.target) if ref.target is not None else got.target == ref.target


def test_upthrust_spring_causal_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_upthrust_spring,
        _detect_upthrust_spring_causal_rows, _to_epoch_ms_shanghai)
    df = _synthetic_df(150, seed=3)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_upthrust_spring_causal_rows(prim, cfg)
    # 参照:逐窗 [0:i+1] 末根
    ref: list[tuple[int, str]] = []
    for i in range(len(prim)):
        sub = prim.iloc[: i + 1]
        last_ts = _to_epoch_ms_shanghai(sub["date"].iloc[-1])
        for m in _detect_upthrust_spring(sub, cfg):
            if m.timestamp == last_ts:
                ref.append((i, m.signal_type))
    assert [(i, s.signal_type) for i, s in causal] == ref


def test_upthrust_spring_causal_excludes_unconfirmed_pivot():
    # 构造一个 center 落在 (i-k, i] 的 pivot:因果变体不得在 bar i 用它(F1)
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_upthrust_spring_causal_rows)
    cfg = VPSConfig(swing_k=2)
    # 24 根:制造一个低点 pivot center=20(confirm@22),并在 bar 21 试图触发 spring
    close = [10.0] * 24
    low = [10.0] * 24
    low[20] = 5.0; close[20] = 9.0          # 低点
    low[21] = 4.0; close[21] = 9.5          # bar21 跌破前低收回 → 若用未确认 pivot20 会误产 spring
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=24, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": [100.0] * 24})
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_upthrust_spring_causal_rows(prim, cfg)
    # bar21: pivot20 的确认索引 = 20+2 = 22 > 21 → 不可用 → bar21 无 spring
    assert not any(i == 21 and s.signal_type == "spring" for i, s in causal)


def test_vfx_all_bars_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_latest_vfx,
        _detect_vfx_all_bars_rows)
    df = _synthetic_df(100, seed=11)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    got = _detect_vfx_all_bars_rows(prim, cfg)
    ref: list[tuple[int, str, str]] = []
    for i in range(len(prim)):
        for m in _detect_latest_vfx(prim.iloc[: i + 1], cfg):
            ref.append((i, m.signal_type, m.confidence))
    assert [(i, s.signal_type, s.confidence) for i, s in got] == ref
    assert all(s.confidence == "low" for _, s in got)


def test_shrink_pullback_causal_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_shrink_pullback,
        _detect_shrink_pullback_causal_rows, _to_epoch_ms_shanghai)
    # 上升趋势 + 缩量回调,确保有 shrink_pullback 触发
    n = 120
    close = list(50 + np.linspace(0, 20, n))
    for j in range(60, 70):  # 一段缩量回调
        close[j] = close[59] - (j - 59) * 0.2
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.3 for c in close], "low": [c - 0.3 for c in close],
                       "close": close, "volume": [3000.0] * 60 + [500.0] * 10 + [3000.0] * (n - 70)})
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_shrink_pullback_causal_rows(prim, cfg)
    ref: list[tuple[int, float]] = []
    for i in range(len(prim)):
        sub = prim.iloc[: i + 1]
        last_ts = _to_epoch_ms_shanghai(sub["date"].iloc[-1])
        for m in _detect_shrink_pullback(sub, cfg):
            if m.timestamp == last_ts:
                ref.append((i, round(m.observed_value, 6)))
    assert [(i, round(s.observed_value, 6)) for i, s in causal] == ref


def test_a_class_rows_equiv_original():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize,
        _detect_breakouts, _detect_breakouts_rows,
        _anchored_vwap_signals, _anchored_vwap_signals_rows,
        _detect_obv_divergence, _detect_obv_divergence_rows)
    df = _synthetic_df(200, seed=5)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    # 无重复时间戳 → 行号变体与原逐条等价(signal_type/timestamp/observed_value)
    def _key(ms): return [(m.timestamp, m.signal_type, round(m.observed_value, 6)) for m in ms]
    assert _key([s for _, s in _detect_breakouts_rows(prim, cfg)]) == _key(_detect_breakouts(prim, cfg))
    assert _key([s for _, s in _anchored_vwap_signals_rows(prim, cfg)]) == _key(_anchored_vwap_signals(prim, cfg))
    assert _key([s for _, s in _detect_obv_divergence_rows(prim, cfg)]) == _key(_detect_obv_divergence(prim, cfg))


# ─── Task 8: _streaming_topk_kept ─────────────────────────────────────────────

def _mk(ov, direction="bullish", stype="spring"):
    from src.services.volume_price_signals import VPSignal
    return VPSignal(timestamp=0, price=0.0, anchor="low", direction=direction,
                    signal_type=stype, confidence="low", is_daily_approx=True,
                    is_anomalous=False, reason="", threshold=None, observed_value=ov)


def test_streaming_topk_cross_block_tie_prefers_vsa_then_bar():
    # F2: abs 相等(=85),k=1。block0(VSA)优先于 block1(spring),即使 VSA 的 bar 更大
    from src.services.volume_price_signals import _streaming_topk_kept
    spring = _mk(85.0, "bullish", "spring")        # bar30, block1
    vsa = _mk(85.0, "bullish", "vsa_no_supply")    # bar50, block0
    items = [(30, 1, spring), (50, 0, vsa)]
    kept = _streaming_topk_kept(items, k=1)
    # 在 bar50 的池[0:50]={spring@30, vsa@50},键 (-85,0,50)<(-85,1,30) → vsa 胜
    assert id(vsa) in kept
    # spring@30 在它自己的 bar30 池[0:30]={spring@30} 是 top-1 → 也保留(冻结)
    assert id(spring) in kept


def test_streaming_topk_same_bar_multi():
    # F3: 同 bar 两 marker,k=1,只保留 abs 大的那个
    from src.services.volume_price_signals import _streaming_topk_kept
    big = _mk(10.0, "bullish", "spring")           # bar5
    small = _mk(3.0, "bearish", "upthrust")        # bar5
    items = [(5, 1, big), (5, 1, small)]
    kept = _streaming_topk_kept(items, k=1)
    assert id(big) in kept and id(small) not in kept


def test_streaming_topk_pool_smaller_than_k():
    from src.services.volume_price_signals import _streaming_topk_kept
    a = _mk(1.0)
    b = _mk(2.0)
    kept = _streaming_topk_kept([(1, 0, a), (2, 0, b)], k=5)
    assert id(a) in kept and id(b) in kept


def test_streaming_topk_freeze_earlier_bar_evicted_later_still_kept():
    from src.services.volume_price_signals import _streaming_topk_kept
    early = _mk(5.0)    # bar1
    big1 = _mk(100.0)   # bar3
    big2 = _mk(101.0)   # bar4
    # k=1: bar1 池={early} → early 是 top1 → 保留;后续被 big 挤出,仍算保留
    kept = _streaming_topk_kept([(1, 0, early), (3, 0, big1), (4, 0, big2)], k=1)
    assert id(early) in kept
