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
