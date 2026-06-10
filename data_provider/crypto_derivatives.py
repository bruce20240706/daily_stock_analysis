"""crypto 永续合约指标（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB。仅读取 CRYPTO_FETCH_TIMEOUT_SECONDS /
CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。数据源：OKX 公共接口（永续 SWAP）。
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"
OKX_MARK_URL = "https://www.okx.com/api/v5/public/mark-price"
OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"
_LINEAR_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价


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


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _okx_first(url: str, params: dict) -> dict:
    """GET OKX 公共接口，返回 data[0] dict；失败/空 → {}（不抛，供并发各路独立降级）。"""
    try:
        data = _http_get_json(url, params)
    except Exception as e:
        logger.warning("[合约指标] %s 抓取失败: %s", url, e)
        return {}
    arr = data.get("data") if isinstance(data, dict) else None
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        return arr[0]
    return {}


def fetch_perp_metrics(base: str, quote: str) -> dict:
    """spot BASE/QUOTE → OKX 永续 BASE-QUOTE-SWAP；并发拉 3 个公共接口；presence-only；失败/不支持 → {}。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return {}
    inst = f"{base}-{quote}-SWAP"
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_fr = ex.submit(_okx_first, OKX_FUNDING_URL, {"instId": inst})
        f_mp = ex.submit(_okx_first, OKX_MARK_URL, {"instType": "SWAP", "instId": inst})
        f_oi = ex.submit(_okx_first, OKX_OI_URL, {"instId": inst})
        fr, mp, oi = f_fr.result(), f_mp.result(), f_oi.result()
    out: dict = {}
    fr_v = _to_float(fr.get("fundingRate"))
    if fr_v is not None:
        out["funding_rate"] = fr_v
    mp_v = _to_float(mp.get("markPx"))
    if mp_v is not None:
        out["mark_price"] = mp_v
    oi_v = _to_float(oi.get("oi"))
    if oi_v is not None:
        out["open_interest"] = oi_v
    oiusd = _to_float(oi.get("oiUsd"))
    if oiusd is not None:
        out["open_interest_usd"] = oiusd
    if out:
        out["source"] = "okx"
    return out


def _perp_row_for_symbol(symbol: str) -> dict:
    """单个 BASE/QUOTE 现货代码 → {symbol, funding_rate?, open_interest_usd?}；无可用字段/异常 → {}。"""
    try:
        base, sep, quote = (symbol or "").upper().partition("/")
        if not sep:
            return {}
        metrics = fetch_perp_metrics(base, quote)  # 模块内全局引用，便于测试 monkeypatch
        row: dict = {}
        if metrics.get("funding_rate") is not None:
            row["funding_rate"] = metrics["funding_rate"]
        if metrics.get("open_interest_usd") is not None:
            row["open_interest_usd"] = metrics["open_interest_usd"]
        if row:
            row["symbol"] = symbol
        return row
    except Exception as e:  # 单币失败不拖垮整篮子聚合（对齐 _okx_first 的 fail-soft 约定）
        logger.warning("[永续复盘] %s 处理失败，跳过: %s", symbol, e)
        return {}


def fetch_perp_market_snapshot(symbols: list) -> dict:
    """对一篮子现货代码并发取各自 OKX 永续指标，聚合复盘情绪（presence-only）。
    OI 加权平均资金费率 + 总未平仓量(USD) + 按 |funding| 降序 top5 明细。无数据 → {}。"""
    if not symbols:
        return {}
    rows: list = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for row in ex.map(_perp_row_for_symbol, symbols):
            if row:
                rows.append(row)
    if not rows:
        return {}
    out: dict = {}
    weighted_num = 0.0
    weighted_den = 0.0
    total_oi = 0.0
    has_oi = False
    for r in rows:
        fr = r.get("funding_rate")
        oi = r.get("open_interest_usd")
        if oi is not None:
            total_oi += oi
            has_oi = True
            if fr is not None and oi > 0:
                weighted_num += fr * oi
                weighted_den += oi
    if weighted_den > 0:
        out["avg_funding_rate"] = weighted_num / weighted_den
    if has_oi:
        out["total_open_interest_usd"] = total_oi
    coins = sorted(
        rows,
        key=lambda r: abs(r["funding_rate"]) if r.get("funding_rate") is not None else -1.0,
        reverse=True,
    )[:5]
    if coins:
        out["coins"] = coins
    return out
