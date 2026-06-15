# -*- coding: utf-8 -*-
"""Tests for M2b price-level back-calculator and guard wiring."""

import numpy as np
import pandas as pd
import pytest

from src.services.volume_price_signals import (
    PriceLevels,
    derive_price_levels,
)


def _uptrend_df(n: int = 60) -> pd.DataFrame:
    """Deterministic gently-rising series with stable ATR (~1.0)."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    base = np.linspace(100.0, 130.0, n)
    high = base + 1.0
    low = base - 1.0
    close = base + 0.2
    open_ = base - 0.2
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_derive_price_levels_returns_ordered_levels_with_positive_rr() -> None:
    levels = derive_price_levels(_uptrend_df())

    assert isinstance(levels, PriceLevels)
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target is not None
    # stop below entry below target for a long setup
    assert levels.stop < levels.entry < levels.target
    # risk_reward = (target-entry)/(entry-stop) must be finite and positive
    assert levels.risk_reward is not None
    assert levels.risk_reward > 0
    expected_rr = (levels.target - levels.entry) / (levels.entry - levels.stop)
    assert levels.risk_reward == pytest.approx(expected_rr, rel=1e-6)


def test_derive_price_levels_stop_is_one_atr_band_below_entry() -> None:
    df = _uptrend_df()
    levels = derive_price_levels(df)
    # ATR of the constant-amplitude series is ~2.0 (high-low band); stop sits
    # atr_mult * ATR below entry. Assert the gap is a positive multiple of ATR,
    # not an arbitrary fixed percentage.
    from src.services.volume_price_signals import atr

    last_atr = float(atr(df).iloc[-1])
    assert last_atr > 0
    gap = levels.entry - levels.stop
    assert gap == pytest.approx(1.5 * last_atr, rel=1e-6)


def test_derive_price_levels_degraded_when_window_too_short() -> None:
    short_df = _uptrend_df(n=5)
    levels = derive_price_levels(short_df)
    # Insufficient window -> ATR unavailable -> stop/target hidden, entry may
    # still resolve from available closes but stop/target must be None.
    assert levels.stop is None
    assert levels.target is None
    assert levels.risk_reward is None


def test_is_invalid_price_level_flags_disordered_levels() -> None:
    from src.services.volume_price_signals import is_invalid_price_level

    # stop above entry is invalid for a long setup
    assert is_invalid_price_level(entry=100.0, stop=105.0, target=120.0, current_price=101.0) is True
    # target below entry is invalid
    assert is_invalid_price_level(entry=100.0, stop=95.0, target=99.0, current_price=101.0) is True
    # non-finite is invalid
    assert is_invalid_price_level(entry=100.0, stop=float("nan"), target=120.0, current_price=101.0) is True
    # well-ordered long levels are valid
    assert is_invalid_price_level(entry=100.0, stop=95.0, target=110.0, current_price=101.0) is False


def test_derive_price_levels_falls_back_to_atr_when_entry_above_current() -> None:
    """When swing/MA entry sits above current price (invalid long entry),
    the back-calculator must fall back to ATR-anchored levels off current price,
    never emit a stop above entry."""
    import numpy as np
    import pandas as pd

    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    # Sharp final drop so MA20 / 20D-low sit ABOVE the last close.
    # low = base + 0.5 ensures all lows are ABOVE the last close (110.0),
    # making both MA20 (~130.6) and 20D-min-of-low (~110.5) > current_price (110),
    # so the candidate entry (min of both) is above current_price and triggers fallback.
    base = np.concatenate([np.linspace(130.0, 132.0, n - 1), [110.0]])
    df = pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base + 1.0,
            "low": base + 0.5,
            "close": base,
            "volume": np.full(n, 1_000_000.0),
        }
    )

    levels = derive_price_levels(df)
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target is not None
    # fallback must restore long ordering
    assert levels.stop < levels.entry < levels.target
    # entry anchored on current price in fallback
    assert levels.entry == pytest.approx(float(df["close"].iloc[-1]), rel=1e-6)
