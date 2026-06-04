"""Candlestick-pattern recognition (optional, via TA-Lib).

TA-Lib is an optional dependency; when it is not installed, callers should skip
pattern features rather than fail.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

#: Subset of TA-Lib CDL* patterns we care about (extend as needed).
DEFAULT_PATTERNS = [
    "CDLENGULFING",
    "CDLHAMMER",
    "CDLMORNINGSTAR",
    "CDLEVENINGSTAR",
    "CDLDOJI",
    "CDLSHOOTINGSTAR",
]


def talib_available() -> bool:
    """True if TA-Lib can be imported."""
    try:
        import talib  # noqa: F401

        return True
    except Exception:
        return False


def detect_patterns(df: "pd.DataFrame", patterns: list[str] | None = None) -> "pd.DataFrame":
    """Append candlestick-pattern signal columns to ``df`` (requires TA-Lib)."""
    # TODO(phase-2): lazy-import talib; for each name call
    # getattr(talib, name)(open, high, low, close) and append as a column.
    raise NotImplementedError("detect_patterns")
