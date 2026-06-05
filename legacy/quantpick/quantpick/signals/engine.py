"""SignalEngine: trend filter -> triggers -> strength + levels -> Signal."""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

    from quantpick.core.types import Market
    from quantpick.signals.base import Signal

log = get_logger(__name__)


class SignalEngine:
    """Produces a Signal for one stock from indicator-augmented bars.

    Config is the parsed ``config/signals.yaml`` (trend / buy / sell / levels).
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def buy_signal(
        self, code: str, name: str, market: "Market", bars: "pd.DataFrame"
    ) -> "Signal":
        # TODO(signals): classify_regime; if UP, evaluate_buy and aggregate
        # strength from ADX + fired rules + volume/momentum; compute_levels;
        # otherwise return a HOLD signal.
        raise NotImplementedError("SignalEngine.buy_signal")

    def sell_signal(
        self, code: str, name: str, market: "Market", bars: "pd.DataFrame"
    ) -> "Signal":
        # TODO(signals): evaluate_sell within DOWN / overbought / breakdown;
        # also flag stop or target hits; otherwise HOLD.
        raise NotImplementedError("SignalEngine.sell_signal")
