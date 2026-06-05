"""DataSource interface: the contract every market-data backend implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING

from quantpick.core.errors import ErrorCode, Result
from quantpick.core.types import Market

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

#: Normalized daily-bar columns every source must produce.
BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


class DataSource(ABC):
    """A market-data backend (akshare / baostock / adata / ...).

    All methods return :class:`Result`; implementations must NOT raise on
    ordinary fetch failures (network, missing symbol), so the manager can fall
    back to the next source. Daily-bar frames use the :data:`BAR_COLUMNS`
    schema, sorted by ascending ``date``.
    """

    #: short stable id, e.g. "akshare"
    name: str = "base"

    @abstractmethod
    def supports(self, market: Market) -> bool:
        """Whether this source can serve the given market."""

    @abstractmethod
    def get_stock_list(self, market: Market) -> "Result[pd.DataFrame]":
        """Return the tradable universe for a market (code, name, ...)."""

    @abstractmethod
    def get_daily_bars(
        self, code: str, start: date, end: date, adjust: str = "qfq"
    ) -> "Result[pd.DataFrame]":
        """Return daily OHLCV bars for one symbol in the normalized schema."""

    @abstractmethod
    def get_fundamentals(self, code: str) -> "Result[pd.DataFrame]":
        """Return fundamental indicators (PE, PB, ROE, ...) for one symbol."""

    def get_money_flow(self, code: str) -> "Result[pd.DataFrame]":
        """Optional capital-flow data. Default: unsupported."""
        return Result.fail(
            ErrorCode.DATA_SOURCE_UNAVAILABLE, f"{self.name}: money flow unsupported"
        )
