"""数字货币新上线发现（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB；差分/持久化/去重/富化/特性配置在 src 服务层。
仅读取通用传输旋钮 CRYPTO_FETCH_TIMEOUT_SECONDS / CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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


COINBASE_PRODUCTS_URL = "https://api.coinbase.com/api/v3/brokerage/market/products"
_COINBASE_UA = {"User-Agent": "dsa-market-review/1.0"}


def _iso_to_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def fetch_coinbase_products(window_days: int, now_ms: int) -> List[NewListing]:
    """Coinbase Advanced Trade products：new_at 落在窗口内，按 base_currency_id 去重。"""
    out: List[NewListing] = []
    try:
        data = _http_get_json(COINBASE_PRODUCTS_URL, headers=_COINBASE_UA)
    except Exception as e:
        logger.warning("[新上新-Coinbase] 抓取失败: %s", e)
        return out
    products = (data.get("products") or []) if isinstance(data, dict) else []
    lo = now_ms - window_days * _DAY_MS
    seen_base = set()
    for p in products:
        ms = _iso_to_ms(p.get("new_at"))
        if ms is None or not (lo < ms <= now_ms):
            continue
        base = (p.get("base_currency_id") or "").upper()
        quote = (p.get("quote_currency_id") or "").upper()
        if not base or not quote or base in seen_base:
            continue
        seen_base.add(base)
        out.append(NewListing(base=base, quote=quote,
                              symbol=p.get("product_id") or f"{base}-{quote}",
                              exchange="coinbase", listed_at=ms, source="coinbase"))
    return out


BINANCE_EXCHANGEINFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"  # .vision 避 451


def fetch_binance_spot_base_assets() -> Dict[str, Tuple[str, str]]:
    """当前 Binance spot 在交易的 baseAsset → (代表 symbol, quote)，代表对优先 *USDT。

    纯抓取——不做差分、不碰 DB（差分在 src 服务层）。失败返回 {}。
    """
    out: Dict[str, Tuple[str, str]] = {}
    try:
        data = _http_get_json(BINANCE_EXCHANGEINFO_URL)
    except Exception as e:
        logger.warning("[新上新-Binance] exchangeInfo 抓取失败: %s", e)
        return out
    symbols = (data.get("symbols") or []) if isinstance(data, dict) else []
    for s in symbols:
        if s.get("status") != "TRADING" or not s.get("isSpotTradingAllowed"):
            continue
        base = (s.get("baseAsset") or "").upper()
        quote = (s.get("quoteAsset") or "").upper()
        if not base or not quote:
            continue
        if base not in out or quote == "USDT":   # 代表对优先 USDT
            out[base] = (s.get("symbol") or f"{base}{quote}", quote)
    return out
