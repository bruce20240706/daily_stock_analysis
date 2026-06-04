"""Indicator layer: pure technical-indicator and candlestick-pattern functions."""
from __future__ import annotations

from quantpick.indicators.compute import INDICATOR_GROUPS, compute_indicators
from quantpick.indicators.patterns import DEFAULT_PATTERNS, detect_patterns, talib_available

__all__ = [
    "INDICATOR_GROUPS",
    "compute_indicators",
    "DEFAULT_PATTERNS",
    "detect_patterns",
    "talib_available",
]
