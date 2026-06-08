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
        if not sources:
            logger.warning("[新上新] 已启用但 CRYPTO_NEW_LISTING_SOURCES 为空，未发现任何源")
            return []

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
        current = nl.fetch_binance_spot_base_assets()
        if not current:                       # 抓取失败/空：不动基线、不报
            return []
        repo = self._repo
        if repo is None:
            from src.repositories.crypto_listing_repo import CryptoListingRepository
            repo = CryptoListingRepository()
        try:
            prior = repo.get_base_assets("binance")
            repo.save_base_assets("binance", set(current.keys()))
        except Exception as e:
            logger.warning("[新上新-Binance] 快照读写失败，跳过差分: %s", e)
            return []
        if prior is None:
            logger.info("[新上新-Binance] 首次播种基线 %d 个 base，本次不报新上", len(current))
            return []
        new_bases = set(current.keys()) - prior
        out: List[NewListing] = []
        for base in sorted(new_bases):
            symbol, quote = current[base]
            out.append(NewListing(base=base, quote=quote, symbol=symbol,
                                  exchange="binance", listed_at=None, source="binance"))
        return out

    def _dedupe_by_base(self, records: List[NewListing], now_ms: int) -> List[Dict[str, Any]]:
        by_base: Dict[str, List[NewListing]] = {}
        for r in records:
            by_base.setdefault(r.base, []).append(r)
        out: List[Dict[str, Any]] = []
        for base, group in by_base.items():
            natives = sorted([r for r in group if r.listed_at is not None], key=lambda r: r.listed_at)
            if natives:
                anchor = natives[0].listed_at
                # None-listed_at（Binance 差分项）无时间戳 → 一律并入 anchor（该 base 最早的已知上市）组
                close = [
                    r for r in group
                    if r.listed_at is None
                    or abs(r.listed_at - anchor) <= COLLISION_MERGE_HOURS * _HOUR_MS
                ]
                far = [r for r in group if r.listed_at is not None and abs(r.listed_at - anchor) > COLLISION_MERGE_HOURS * _HOUR_MS]
                out.append(self._merge(base, close, now_ms))
                # 已知局限：far 记录之间不再二次聚类，各自成行（当前 OKX+Coinbase 场景足够；
                # 多源规模化后如需更精细聚类再迭代）。
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

    _SUPPORTED_QUOTES = {"USDT", "USDC", "USD", "BUSD", "BTC", "ETH"}

    def _enrich(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:   # Task 8 实现
        out: List[Dict[str, Any]] = []
        for it in items:
            rec: Dict[str, Any] = {"base": it["base"], "exchanges": it["exchanges"], "pairs": it["pairs"]}
            if it.get("listed_at") is not None:
                rec["listed_at"] = it["listed_at"]
            quote = it.get("quote") or "USDT"
            if quote not in self._SUPPORTED_QUOTES:
                quote = "USDT"
            code = f'{it["base"]}/{quote}'
            if self.data_manager is not None:
                try:
                    q = self.data_manager.get_realtime_quote(code, log_final_failure=False)
                except Exception as e:
                    logger.info("[新上新] %s 富化失败: %s", code, e)
                    q = None
                if q is not None and getattr(q, "price", None) is not None:
                    rec["quote_pair"] = code
                    rec["price"] = float(q.price)
                    if getattr(q, "change_pct", None) is not None:
                        rec["change_pct"] = float(q.change_pct)
                    if getattr(q, "volume", None) is not None:
                        rec["volume"] = float(q.volume)
            out.append(rec)
        return out
