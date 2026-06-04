"""baostock-backed DataSource (A-share historical fallback).

baostock requires an explicit login()/logout() session and is A-share only.
Lazy-imported inside methods.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from quantpick.core.types import Market
from quantpick.data.base import DataSource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.errors import Result
    import pandas as pd


class BaostockSource(DataSource):
    """A-share historical daily/fundamentals fallback (no HK)."""

    name = "baostock"

    def supports(self, market: Market) -> bool:
        return market == Market.A_SHARE

    def get_stock_list(self, market: Market) -> "Result[pd.DataFrame]":
        # TODO(phase-1): bs.login(); bs.query_all_stock(day); bs.logout()
        raise NotImplementedError("BaostockSource.get_stock_list")

    def get_daily_bars(
        self, code: str, start: date, end: date, adjust: str = "qfq"
    ) -> "Result[pd.DataFrame]":
        # TODO(phase-1): bs.query_history_k_data_plus(code, fields, start, end,
        #                frequency="d", adjustflag=...). Map code "600519" ->
        #                "sh.600519" / "sz.000001" for baostock.
        raise NotImplementedError("BaostockSource.get_daily_bars")

    def get_fundamentals(self, code: str) -> "Result[pd.DataFrame]":
        # TODO(phase-1): bs.query_profit_data / query_growth_data / query_balance_data
        raise NotImplementedError("BaostockSource.get_fundamentals")
