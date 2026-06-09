"""数字货币大盘宏观指标（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB；合并/presence-only/配置门控在 src 服务层。
仅读取通用传输旋钮 CRYPTO_FETCH_TIMEOUT_SECONDS / CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


# 与 crypto_new_listings 同义；保持本模块零跨层依赖，故就地复制而非 import。
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


def fetch_global_market() -> dict:
    """CoinGecko /global → 宏观聚合；presence-only（缺字段不塞）；失败/非 dict → {}。"""
    try:
        data = _http_get_json(GLOBAL_URL)
    except Exception as e:
        logger.warning("[大盘指标-global] 抓取失败: %s", e)
        return {}
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        return {}
    out: dict = {}
    mcp = d.get("market_cap_percentage")
    if isinstance(mcp, dict):
        btc = _to_float(mcp.get("btc"))
        eth = _to_float(mcp.get("eth"))
        if btc is not None:
            out["btc_dominance"] = btc
        if eth is not None:
            out["eth_dominance"] = eth
    tmc = d.get("total_market_cap")
    if isinstance(tmc, dict):
        usd = _to_float(tmc.get("usd"))
        if usd is not None:
            out["total_market_cap_usd"] = usd
    chg = _to_float(d.get("market_cap_change_percentage_24h_usd"))
    if chg is not None:
        out["market_cap_change_24h_pct"] = chg
    tv = d.get("total_volume")
    if isinstance(tv, dict):
        vol = _to_float(tv.get("usd"))
        if vol is not None:
            out["total_volume_usd"] = vol
    return out


FNG_URL = "https://api.alternative.me/fng/"


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_fear_greed() -> dict:
    """alternative.me /fng → 情绪；失败/空/字段缺失 → {}。

    注意：API 的 value / timestamp 为字符串，需 int() 解析；classification 原样取字符串。
    """
    try:
        data = _http_get_json(FNG_URL, params={"limit": 1})
    except Exception as e:
        logger.warning("[大盘指标-fng] 抓取失败: %s", e)
        return {}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return {}
    first = items[0]
    if not isinstance(first, dict):
        return {}
    value = _to_int(first.get("value"))
    classification = first.get("value_classification")
    timestamp = _to_int(first.get("timestamp"))
    # value=0（Extreme Fear）是合法值，故用 is None 判空；classification 空串视为缺失
    if value is None or not classification or timestamp is None:
        return {}
    return {"value": value, "classification": str(classification), "timestamp": timestamp}
