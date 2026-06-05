"""Buy / sell trigger rules (volume + momentum), evaluated within a regime.

Each rule inspects indicator-augmented bars and returns whether it fired plus a
0-1 contribution to the strength score. The engine runs buy rules for
candidates in an UP regime and sell rules for holdings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


@dataclass(slots=True)
class TriggerResult:
    name: str
    fired: bool
    contribution: float = 0.0      # 0-1, feeds the strength score


#: Planned rule names (documented for the engine and tests).
BUY_RULES = [
    "macd_golden_cross",
    "pullback_to_ma_with_volume",
    "breakout_high_with_volume",
]
SELL_RULES = [
    "macd_death_cross",
    "breakdown_below_ma_with_volume",
    "rsi_overbought_divergence",
    "trailing_stop_break",
]


def evaluate_buy(bars: "pd.DataFrame", cfg: dict) -> list[TriggerResult]:
    """Run BUY_RULES against the bars (needs MACD + volume + momentum)."""
    # TODO(signals): implement each rule in BUY_RULES.
    raise NotImplementedError("evaluate_buy")


def evaluate_sell(bars: "pd.DataFrame", cfg: dict) -> list[TriggerResult]:
    """Run SELL_RULES against the bars."""
    # TODO(signals): implement each rule in SELL_RULES.
    raise NotImplementedError("evaluate_sell")
