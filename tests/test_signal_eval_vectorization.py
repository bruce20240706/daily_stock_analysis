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
