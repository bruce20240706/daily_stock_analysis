"""Technical-indicator computation (pandas-ta wrapper).

Pure functions: given a normalized OHLCV frame, return it augmented with
indicator columns. No I/O. pandas / pandas_ta are lazy-imported.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

#: Curated indicator groups (trend / momentum / volume / volatility).
INDICATOR_GROUPS: dict[str, list[str]] = {
    "trend": ["sma", "ema", "macd", "adx", "psar", "supertrend"],
    "momentum": ["rsi", "kdj", "cci", "roc", "willr", "bias"],
    "volume": ["obv", "mfi", "pvr"],
    "volatility": ["bbands", "atr"],
}


def compute_indicators(df: "pd.DataFrame", groups: list[str] | None = None) -> "pd.DataFrame":
    """Return ``df`` with indicator columns for the requested groups appended.

    Args:
        df: normalized OHLCV frame (see ``data.base.BAR_COLUMNS``), date-ascending.
        groups: subset of :data:`INDICATOR_GROUPS` keys; ``None`` means all.

    Notes:
        Implementation will lazy-import ``pandas_ta`` and call e.g.
        ``df.ta.macd(append=True)``, ``df.ta.rsi(append=True)`` per group.
    """
    # TODO(phase-1): implement with pandas_ta; keep this pure (no I/O, no mutation
    # of the caller's frame - operate on a copy).
    raise NotImplementedError("compute_indicators")
