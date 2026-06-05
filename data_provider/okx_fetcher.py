"""OKX 现货公共行情 fetcher（免 API Key）。"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class OkxFetcher(CryptoExchangeBase):
    name = "OkxFetcher"
    priority = int(os.getenv("OKX_PRIORITY", "51"))
    MAX_LIMIT = 100  # 基础 candles 接口上限

    BASE_URL = "https://www.okx.com"

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "-")

    def _request_klines(self, symbol: str, days: int) -> list:
        data = self._http_get(
            f"{self.BASE_URL}/api/v5/market/candles",
            {"instId": symbol, "bar": "1D", "limit": self._days_to_limit(days)},
        )
        return (data or {}).get("data", [])

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        return pd.DataFrame(
            [{"date": int(r[0]), "open": r[1], "high": r[2], "low": r[3],
              "close": r[4], "volume": r[5], "amount": r[7]} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        data = self._http_get(f"{self.BASE_URL}/api/v5/market/ticker", {"instId": symbol})
        items = (data or {}).get("data", [])
        return items[0] if items else {}

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        last = f(raw.get("last"))
        open24 = f(raw.get("open24h"))
        pct = ((last - open24) / open24 * 100) if (last is not None and open24) else None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=last, change_pct=pct,
            volume=f(raw.get("vol24h")), amount=f(raw.get("volCcy24h")),
            high=f(raw.get("high24h")), low=f(raw.get("low24h")),
        )
