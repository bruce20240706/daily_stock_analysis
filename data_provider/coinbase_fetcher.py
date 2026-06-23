"""Coinbase Exchange 现货公共行情 fetcher（免 API Key）。

注意：candles 列序为 [time, low, high, open, close, volume]，且无 quote volume，amount 置 None。
"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class CoinbaseFetcher(CryptoExchangeBase):
    name = "CoinbaseFetcher"
    priority = int(os.getenv("COINBASE_PRIORITY", "52"))
    MAX_LIMIT = 300

    BASE_URL = "https://api.exchange.coinbase.com"

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "-")

    # Coinbase granularity mapping (seconds)
    _GRANULARITY_MAP = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "1d": 86400,
    }

    def _request_klines(self, symbol: str, days: int, interval: str = "1d", start_ms: int = None) -> list:
        granularity = self._GRANULARITY_MAP.get(interval)
        if granularity is None:
            raise NotImplementedError(
                f"CoinbaseFetcher: interval={interval!r} not supported"
            )
        if interval != "1d":
            limit = self._intraday_limit(days, interval)
            if limit > self.MAX_LIMIT:
                raise NotImplementedError(
                    f"CoinbaseFetcher: interval={interval} days={days} 超出单页上限({self.MAX_LIMIT})，暂不支持分页"
                )
        return self._http_get(
            f"{self.BASE_URL}/products/{symbol}/candles", {"granularity": granularity}
        )

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [time(秒), low, high, open, close, volume]
        return pd.DataFrame(
            [{"date": int(r[0]) * 1000, "open": r[3], "high": r[2], "low": r[1],
              "close": r[4], "volume": r[5], "amount": None} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        return self._http_get(f"{self.BASE_URL}/products/{symbol}/ticker", {})

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=f(raw.get("price")), volume=f(raw.get("volume")), amount=None,
        )
