"""akshare-backed DataSource (primary; A-share + HK).

akshare is lazy-imported inside methods, so importing this module is cheap and
does not require akshare to be installed.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from quantpick.core.types import Market
from quantpick.data.base import DataSource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.errors import Result
    import pandas as pd


class AkshareSource(DataSource):
    """Primary source. Covers A-share and (less completely) HK."""

    name = "akshare"

    def supports(self, market: Market) -> bool:
        return market in (Market.A_SHARE, Market.HK)

    def get_stock_list(self, market: Market) -> "Result[pd.DataFrame]":
        # TODO(phase-1):
        #   A_SHARE -> ak.stock_info_a_code_name() / ak.stock_zh_a_spot_em()
        #   HK      -> ak.stock_hk_spot_em()
        raise NotImplementedError("AkshareSource.get_stock_list")

    def get_daily_bars(
        self, code: str, start: date, end: date, adjust: str = "qfq"
    ) -> "Result[pd.DataFrame]":
        # TODO(phase-1):
        #   A_SHARE -> ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust)
        #   HK      -> ak.stock_hk_hist(symbol, period="daily", start_date, end_date, adjust)
        #   then rename/reorder to BAR_COLUMNS and sort by date asc.
        raise NotImplementedError("AkshareSource.get_daily_bars")

    def get_fundamentals(self, code: str) -> "Result[pd.DataFrame]":
        # TODO(phase-1): ak.stock_a_indicator_lg(symbol) (PE/PB/...) +
        #                ak.stock_financial_analysis_indicator(symbol) (ROE/growth)
        raise NotImplementedError("AkshareSource.get_fundamentals")
