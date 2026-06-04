"""Trading-calendar helpers for A-share and HK markets.

Skeleton: the exchange-calendar-backed implementation is a TODO. Signatures and
a weekend-only stopgap are defined so callers can be written against them now.
"""
from __future__ import annotations

from datetime import date, timedelta

from quantpick.core.types import Market


class TradingCalendar:
    """Answers trading-day questions for a market.

    The concrete implementation will load the exchange calendar (A-share via
    akshare ``tool_trade_date_hist_sina``; HK calendar TBD) and cache it.
    """

    def __init__(self, market: Market) -> None:
        self.market = market
        self._days: set[date] | None = None  # lazily loaded, cached

    def _load(self) -> set[date]:
        # TODO(phase-1): load the real exchange calendar (lazy-import akshare).
        raise NotImplementedError("TradingCalendar._load not implemented yet")

    def is_trading_day(self, day: date) -> bool:
        # TODO(phase-1): consult the loaded calendar. Weekend check is a stopgap.
        return day.weekday() < 5

    def latest_trading_day(self, ref: date | None = None) -> date:
        """Most recent trading day on or before ``ref`` (today if None)."""
        # TODO(phase-1): respect exchange holidays.
        day = ref or date.today()
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def previous_trading_day(self, ref: date) -> date:
        day = ref - timedelta(days=1)
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day
