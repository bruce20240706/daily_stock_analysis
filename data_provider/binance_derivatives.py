# -*- coding: utf-8 -*-
"""Binance fapi 衍生品备援（纯抓取，免 API Key）。

OKX 整源不可得时的兜底源（编排在 crypto_derivatives 的公开函数内）。分层纪律同
crypto_derivatives：不 import src.*、不碰 DB。注意响应**无包络**（premiumIndex/
openInterest 为裸 dict，futures/data 与 fundingRate 为裸数组），与 OKX 的
{"code":..,"data":[...]} 不同；futures/data 数组为升序（最新在末）。
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from data_provider.crypto_derivatives import _LINEAR_QUOTES, _http_get_json, _to_float

logger = logging.getLogger(__name__)

_PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
_OPEN_INTEREST_PATH = "/fapi/v1/openInterest"
_LS_GLOBAL_PATH = "/futures/data/globalLongShortAccountRatio"
_LS_TOP_PATH = "/futures/data/topLongShortAccountRatio"
_FUNDING_HISTORY_PATH = "/fapi/v1/fundingRate"
_FUNDING_HISTORY_LIMIT = 1000  # 单页上限 ≥ 窗口最大需求(3N≤360)；若返回行数==limit（截断迹象）仍用已得（fail-soft 低估）


def _fapi_base() -> str:
    """Binance fapi 基址；BINANCE_FAPI_BASE_URL 可覆盖（受限地区换镜像），默认官方域名。"""
    return (os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")


def _get_dict(path: str, params: dict) -> dict:
    """GET 裸 dict 端点（premiumIndex/openInterest）；失败/非 dict → {}（fail-soft）。"""
    try:
        data = _http_get_json(_fapi_base() + path, params)
    except Exception as e:
        logger.warning("[合约指标:binance] %s 抓取失败: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _get_latest_ratio(path: str, symbol: str) -> Optional[float]:
    """GET futures/data 多空比端点（裸数组升序，最新在末）；取末行 longShortRatio；失败/空/结构异常 → None。"""
    try:
        data = _http_get_json(_fapi_base() + path, {"symbol": symbol, "period": "5m", "limit": "1"})
    except Exception as e:
        logger.warning("[合约指标:binance] %s 抓取失败: %s", path, e)
        return None
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return _to_float(data[-1].get("longShortRatio"))
    return None


def fetch_perp_metrics(base: str, quote: str) -> dict:
    """BASE/QUOTE → Binance fapi BASEQUOTE 永续；4 路并发；presence-only；失败/不支持 → {}。
    OI 仅 USD 口径（openInterest×markPrice，基础币数量换算），永不填张数 open_interest（OKX 专属语义）。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return {}
    symbol = f"{base}{quote}"
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pi = ex.submit(_get_dict, _PREMIUM_INDEX_PATH, {"symbol": symbol})
        f_oi = ex.submit(_get_dict, _OPEN_INTEREST_PATH, {"symbol": symbol})
        f_ls = ex.submit(_get_latest_ratio, _LS_GLOBAL_PATH, symbol)
        f_lst = ex.submit(_get_latest_ratio, _LS_TOP_PATH, symbol)
        pi, oi_raw = f_pi.result(), f_oi.result()
        ls, lst = f_ls.result(), f_lst.result()
    out: dict = {}
    fr = _to_float(pi.get("lastFundingRate"))
    if fr is not None:
        out["funding_rate"] = fr
    mp = _to_float(pi.get("markPrice"))
    if mp is not None:
        out["mark_price"] = mp
    oi = _to_float(oi_raw.get("openInterest"))
    if oi is not None and mp is not None:
        out["open_interest_usd"] = oi * mp
    if ls is not None:
        out["long_short_ratio"] = ls
    if lst is not None:
        out["long_short_ratio_top"] = lst
    if out:
        out["source"] = "binance"
    return out


def fetch_funding_rate_history(base: str, quote: str, start_ms: int, end_ms: int) -> list:
    """Binance fapi 资金费历史，半开窗口 [start_ms, end_ms)。服务端 startTime/endTime 过滤 +
    客户端半开兜底（endTime 包含/排除语义无论哪种，最终结果一致）；单页 limit=1000 不分页；失败 → []。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return []
    symbol = f"{base}{quote}"
    params = {
        "symbol": symbol,
        "startTime": str(int(start_ms)),
        "endTime": str(int(end_ms)),
        "limit": str(_FUNDING_HISTORY_LIMIT),
    }
    try:
        data = _http_get_json(_fapi_base() + _FUNDING_HISTORY_PATH, params)
    except Exception as e:
        logger.warning("[资金费历史:binance] %s 抓取失败: %s", symbol, e)
        return []
    if not isinstance(data, list):
        return []
    rates: list = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = _to_float(item.get("fundingTime"))
        fr = _to_float(item.get("fundingRate"))
        if ts is None or fr is None:
            continue
        if start_ms <= ts < end_ms:  # 半开 [start, end)：与 OKX 路完全一致
            rates.append(fr)
    return rates
