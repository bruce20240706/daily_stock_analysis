"""Screener: orchestrates filter -> factor compute -> score -> rank."""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.core.config import AppConfig
from quantpick.core.logging import get_logger
from quantpick.data.manager import DataManager

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import ScoredStock
    from quantpick.strategies.base import Strategy

log = get_logger(__name__)


class Screener:
    """Runs one strategy end-to-end over the configured universe."""

    def __init__(self, config: AppConfig, data: DataManager | None = None) -> None:
        self.config = config
        self.data = data or DataManager(config.data)

    def run(self, strategy: "Strategy") -> "list[ScoredStock]":
        # TODO(phase-1):
        #   1) load + filter universe (screening.filters.apply_filters)
        #   2) per survivor: fetch bars (self.data), compute indicators + fundamentals,
        #      evaluate each weighted factor's raw value
        #   3) cross-sectionally normalize each factor column (factors.normalize)
        #   4) scorer.score_and_rank with strategy.factor_weights, take strategy.top_n
        raise NotImplementedError("Screener.run")
