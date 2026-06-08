# -*- coding: utf-8 -*-
"""crypto 新上线发现服务（编排层）。

职责：读 config、调度 data_provider 纯抓取源、Binance base-asset 差分（Task 7）、
按 base 去重 + 同名碰撞防护 + 窗口/截断、行情富化（Task 8）。data_provider 保持纯抓取。
"""
import logging
import time
from typing import Any, Dict, List, Optional

import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.config import get_config

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000
COLLISION_MERGE_HOURS = 72   # 跨所同 base 上市时间在此窗口内才合并，否则视为可能不同项目


class CryptoNewListingService:
    def __init__(self, data_manager=None, repo=None, config=None):
        self.config = config or get_config()
        self.data_manager = data_manager   # DataFetcherManager，用于富化（Task 8）
        self._repo = repo                   # CryptoListingRepository（Task 7），惰性

    def discover(self, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        if not getattr(self.config, "crypto_new_listing_enabled", True):
            return []
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        window = int(getattr(self.config, "crypto_new_listing_window_days", 7))
        sources = [s.strip().lower() for s in (getattr(self.config, "crypto_new_listing_sources", "") or "").split(",") if s.strip()]

        records: List[NewListing] = []
        if "okx" in sources:
            records += nl.fetch_okx_instruments(window, now)
        if "coinbase" in sources:
            records += nl.fetch_coinbase_products(window, now)
        if "binance" in sources:
            records += self._binance_new(now)

        merged = self._dedupe_by_base(records, now)
        merged.sort(key=lambda r: r["_sort"], reverse=True)
        max_n = int(getattr(self.config, "crypto_new_listing_max", 20))
        capped = merged[:max_n]
        dropped = len(merged) - len(capped)
        if dropped > 0:
            logger.info("[新上新] 截断 %d 条（max=%d）", dropped, max_n)
        return self._enrich(capped)

    def _binance_new(self, now_ms: int) -> List[NewListing]:   # Task 7 实现
        return []

    def _dedupe_by_base(self, records: List[NewListing], now_ms: int) -> List[Dict[str, Any]]:
        by_base: Dict[str, List[NewListing]] = {}
        for r in records:
            by_base.setdefault(r.base, []).append(r)
        out: List[Dict[str, Any]] = []
        for base, group in by_base.items():
            natives = sorted([r for r in group if r.listed_at is not None], key=lambda r: r.listed_at)
            if natives:
                anchor = natives[0].listed_at
                close = [r for r in group if r.listed_at is None or abs(r.listed_at - anchor) <= COLLISION_MERGE_HOURS * _HOUR_MS]
                far = [r for r in group if r.listed_at is not None and abs(r.listed_at - anchor) > COLLISION_MERGE_HOURS * _HOUR_MS]
                out.append(self._merge(base, close, now_ms))
                for r in far:
                    out.append(self._merge(base, [r], now_ms))
            else:
                out.append(self._merge(base, group, now_ms))
        return out

    @staticmethod
    def _merge(base: str, group: List[NewListing], now_ms: int) -> Dict[str, Any]:
        natives = [r.listed_at for r in group if r.listed_at is not None]
        listed_at = min(natives) if natives else None
        quote = next((r.quote for r in group if r.quote), "USDT")
        return {
            "base": base,
            "exchanges": sorted({r.exchange for r in group}),
            "pairs": sorted({r.symbol for r in group}),
            "listed_at": listed_at,
            "quote": quote,
            "_sort": listed_at if listed_at is not None else now_ms,
        }

    def _enrich(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:   # Task 8 实现
        out = []
        for it in items:
            rec = {k: v for k, v in it.items() if k != "_sort" and not (k == "listed_at" and v is None)}
            rec.pop("quote", None)
            out.append(rec)
        return out
