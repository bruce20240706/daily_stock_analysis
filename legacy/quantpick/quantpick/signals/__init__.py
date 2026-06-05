"""Signal (timing) layer: trend filter, triggers, price levels, engine.

Produces actionable BUY / SELL / HOLD recommendations with a strength score and
reference price levels - the "when" that complements screening's "which".
"""
from __future__ import annotations

from quantpick.signals.base import PriceLevels, Regime, Signal, SignalDirection
from quantpick.signals.engine import SignalEngine
from quantpick.signals.levels import build_levels, compute_levels, stop_from_atr
from quantpick.signals.trend import classify_regime
from quantpick.signals.triggers import BUY_RULES, SELL_RULES, TriggerResult, evaluate_buy, evaluate_sell

__all__ = [
    "PriceLevels",
    "Regime",
    "Signal",
    "SignalDirection",
    "SignalEngine",
    "build_levels",
    "compute_levels",
    "stop_from_atr",
    "classify_regime",
    "BUY_RULES",
    "SELL_RULES",
    "TriggerResult",
    "evaluate_buy",
    "evaluate_sell",
]
