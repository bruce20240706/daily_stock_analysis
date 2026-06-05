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

    def _request_klines(self, symbol: str, days: int) -> list:
        return self._http_get(
            f"{self.BASE_URL}/products/{symbol}/candles", {"granularity": 86400}
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
