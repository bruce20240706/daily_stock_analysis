"""Reference price levels: entry / stop-loss / take-profit.

    stop   = max(entry - stop_atr_mult * ATR, recent swing low)
    target = entry + target_rr * (entry - stop)

ATR is used here only as a volatility ruler for sizing the stop - it does NOT
affect signal direction (direction comes from trend + volume + momentum).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.signals.base import PriceLevels

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


def stop_from_atr(entry: float, atr: float, swing_low: float, atr_mult: float = 2.0) -> float:
    """Stop = max(entry - atr_mult * ATR, swing_low). Pure and testable."""
    return max(entry - atr_mult * atr, swing_low)


def build_levels(
    entry: float,
    atr: float,
    swing_low: float,
    atr_mult: float = 2.0,
    target_rr: float = 2.0,
) -> PriceLevels:
    """Compose entry/stop/target from ATR + swing low + reward:risk. Pure."""
    stop = stop_from_atr(entry, atr, swing_low, atr_mult)
    risk = max(entry - stop, 0.0)
    target = entry + target_rr * risk
    return PriceLevels(entry=entry, stop=stop, target=target)


def compute_levels(bars: "pd.DataFrame", cfg: dict) -> PriceLevels:
    """Extract entry / ATR / swing-low from bars, then build levels."""
    # TODO(signals): latest close (entry), ATR (indicators), min low over
    # cfg["swing_lookback"] (swing_low), then call build_levels(...).
    raise NotImplementedError("compute_levels")
