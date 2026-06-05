"""Trend filter: classify each stock's regime (UP / DOWN / RANGE).

The regime gates signals: buy triggers fire only in UP; sell triggers favor
DOWN / breakdown; RANGE suppresses trend-following signals to cut noise.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.signals.base import Regime

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


def classify_regime(
    bars: "pd.DataFrame",
    fast: int = 20,
    slow: int = 60,
    adx_min: float = 25.0,
) -> Regime:
    """Classify trend regime from MA alignment + ADX strength.

    Rules to implement::

        ADX < adx_min                                              -> RANGE
        close > MA(slow) and MA(fast) > MA(slow) and ADX >= adx_min -> UP
        close < MA(slow) and MA(fast) < MA(slow) and ADX >= adx_min -> DOWN
        otherwise                                                  -> RANGE
    """
    # TODO(signals): needs MA + ADX from indicators.compute_indicators.
    raise NotImplementedError("classify_regime")
