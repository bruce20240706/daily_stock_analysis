"""数字货币新上线发现（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB；差分/持久化/去重/富化/特性配置在 src 服务层。
仅读取通用传输旋钮 CRYPTO_FETCH_TIMEOUT_SECONDS / CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。
"""
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"


@dataclass
class NewListing:
    base: str
    quote: str
    symbol: str
    exchange: str
    listed_at: Optional[int]  # epoch ms；Binance 差分无上市时间时为 None
    source: str


# 与 CryptoExchangeBase._fetch_timeout 同义；保持本模块零跨层依赖，故就地复制而非 import。
def _fetch_timeout() -> float:
    raw = os.getenv("CRYPTO_FETCH_TIMEOUT_SECONDS")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return 10.0


def _fetch_max_retries() -> int:
    raw = os.getenv("CRYPTO_FETCH_MAX_RETRIES")
    if raw:
        try:
            v = int(raw)
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    return 0


def _http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> object:
    """GET JSON，复用 CRYPTO_FETCH_* 超时/重试语义（4xx 不重试）。失败抛 requests 异常。"""
    timeout = _fetch_timeout()
    max_retries = _fetch_max_retries()
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                raise
            if attempt >= max_retries:
                raise


def fetch_okx_instruments(window_days: int, now_ms: int) -> List[NewListing]:
    """OKX 现货 instruments：state=live 且 listTime 落在 (now-window, now]（排除未来）。"""
    out: List[NewListing] = []
    try:
        data = _http_get_json(OKX_INSTRUMENTS_URL, {"instType": "SPOT"})
    except Exception as e:
        logger.warning("[新上新-OKX] 抓取失败: %s", e)
        return out
    lo = now_ms - window_days * _DAY_MS
    items = (data.get("data") or []) if isinstance(data, dict) else []
    for it in items:
        if it.get("state") != "live":
            continue
        try:
            lt_ms = int(it.get("listTime"))
        except (TypeError, ValueError):
            continue
        if not (lo < lt_ms <= now_ms):
            continue
        base = (it.get("baseCcy") or "").upper()
        quote = (it.get("quoteCcy") or "").upper()
        if not base or not quote:
            continue
        out.append(NewListing(base=base, quote=quote,
                              symbol=it.get("instId") or f"{base}-{quote}",
                              exchange="okx", listed_at=lt_ms, source="okx"))
    return out
