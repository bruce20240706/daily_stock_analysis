"""Signal-layer types: direction, regime, price levels, and the Signal record."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from quantpick.core.types import Market


class SignalDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Regime(str, Enum):
    """Trend regime from the trend filter (the signal gate)."""

    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass(slots=True)
class PriceLevels:
    """Reference action levels: entry, stop-loss, take-profit."""

    entry: float
    stop: float
    target: float

    @property
    def risk_reward(self) -> float:
        """Reward-to-risk ratio; 0 when risk is non-positive."""
        risk = self.entry - self.stop
        if risk <= 0:
            return 0.0
        return (self.target - self.entry) / risk


@dataclass(slots=True)
class Signal:
    """A timing recommendation for one stock on one day."""

    code: str
    name: str
    market: Market
    direction: SignalDirection
    strength: float                                    # 0-100
    regime: Regime
    reasons: list[str] = field(default_factory=list)   # trigger rule names that fired
    levels: PriceLevels | None = None
