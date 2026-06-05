"""Binance 现货公共行情 fetcher（免 API Key）。"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class BinanceFetcher(CryptoExchangeBase):
    name = "BinanceFetcher"
    priority = int(os.getenv("BINANCE_PRIORITY", "50"))
    MAX_LIMIT = 1000

    @property
    def _base_url(self) -> str:
        return os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "")

    def _request_klines(self, symbol: str, days: int) -> list:
        return self._http_get(
            f"{self._base_url}/api/v3/klines",
            {"symbol": symbol, "interval": "1d", "limit": self._days_to_limit(days)},
        )

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [openTime, open, high, low, close, volume, closeTime, quoteAssetVolume, ...]
        return pd.DataFrame(
            [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
              "close": r[4], "volume": r[5], "amount": r[7]} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        return self._http_get(f"{self._base_url}/api/v3/ticker/24hr", {"symbol": symbol})

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=f(raw.get("lastPrice")), change_pct=f(raw.get("priceChangePercent")),
            volume=f(raw.get("volume")), amount=f(raw.get("quoteVolume")),
            high=f(raw.get("highPrice")), low=f(raw.get("lowPrice")),
        )
