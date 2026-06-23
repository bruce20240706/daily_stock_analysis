"""Binance 现货公共行情 fetcher（免 API Key）。"""
import os
import time

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

    def _request_klines(self, symbol: str, days: int, interval: str = "1d") -> list:
        if interval == "1d":
            limit = self._days_to_limit(days)
            return self._http_get(
                f"{self._base_url}/api/v3/klines",
                {"symbol": symbol, "interval": interval, "limit": min(limit, self.MAX_LIMIT)},
            )
        # 分钟/小时 K 线：计算总条数，单页内直接取；超过单页上限则正向翻页。
        limit = self._intraday_limit(days, interval)
        if limit <= self.MAX_LIMIT:
            return self._http_get(
                f"{self._base_url}/api/v3/klines",
                {"symbol": symbol, "interval": interval, "limit": limit},
            )
        return self._page_klines(symbol, interval, days, limit)

    @staticmethod
    def _now_ms() -> int:
        """当前时间（毫秒）。抽成方法便于测试时 monkeypatch，避免 wall-clock 不确定性。"""
        return int(time.time() * 1000)

    def _page_klines(self, symbol: str, interval: str, days: int, limit: int) -> list:
        """正向翻页拉取分钟 K 线（startTime 从 now-days 锚定，向前推进）。

        关键修复：必须带初始 startTime。不带 startTime 时 Binance 返回最近 N 根，
        page[-1] 为最新 bar，startTime=closeTime+1 会指向未来 → 下一页为空 → 提前退出。
        因此从 now-days*86400*1000 锚定起点，每页用上一页 closeTime+1 向前推进。
        """
        out: list = []
        start_ms = self._now_ms() - int(days) * 86400 * 1000
        # 保护性硬上限：避免端点行为异常导致死循环（按需要页数 + 余量）。
        max_pages = (limit // self.MAX_LIMIT) + 2
        for _ in range(max_pages):
            if len(out) >= limit:
                break
            page = self._http_get(
                f"{self._base_url}/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": self.MAX_LIMIT,
                    "startTime": start_ms,
                },
            )
            if not page:
                break
            out.extend(page)
            last_close_ms = int(page[-1][6])
            next_start = last_close_ms + 1
            # 终止保护：startTime 未向前推进（防御异常返回）则停止，避免死循环。
            if next_start <= start_ms:
                break
            start_ms = next_start
            if len(page) < self.MAX_LIMIT:
                break
        # 去重防御：相邻页 closeTime+1 已避免重叠，这里仅截断到请求条数。
        return out[:limit]

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
