"""adata-backed DataSource (A-share, multi-source resilient fallback).

adata fuses several upstream providers with proxy rotation for high
availability. A-share only. Lazy-imported inside methods.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from quantpick.core.types import Market
from quantpick.data.base import DataSource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.errors import Result
    import pandas as pd


class AdataSource(DataSource):
    """A-share resilient fallback."""

    name = "adata"

    def supports(self, market: Market) -> bool:
        return market == Market.A_SHARE

    def get_stock_list(self, market: Market) -> "Result[pd.DataFrame]":
        # TODO(phase-1): adata.stock.info.all_code()
        raise NotImplementedError("AdataSource.get_stock_list")

    def get_daily_bars(
        self, code: str, start: date, end: date, adjust: str = "qfq"
    ) -> "Result[pd.DataFrame]":
        # TODO(phase-1): adata.stock.market.get_market(stock_code, start_date, k_type, adjust_type)
        raise NotImplementedError("AdataSource.get_daily_bars")

    def get_fundamentals(self, code: str) -> "Result[pd.DataFrame]":
        # TODO(phase-1): adata.stock.finance.* (where available)
        raise NotImplementedError("AdataSource.get_fundamentals")
