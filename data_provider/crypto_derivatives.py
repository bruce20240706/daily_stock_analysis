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
OKX_FUNDING_HISTORY_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
OKX_LS_ACCOUNT_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
OKX_LS_TOP_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
# 首页即自窗口右界(after=end_ms)向后翻，故 12 页约束的是"窗口跨度"(~400 天)而非"现在→窗口"距离；
# 实际 eval 窗口远小于此，正常不会截断；极端超界返回已采集部分（偏低估，fail-soft）。
_FUNDING_HISTORY_MAX_PAGES = 12  # 100 结算/页 ≈ 33 天/页
_LINEAR_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价（供 binance_derivatives 复用）


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
    """GET JSON，复用 CRYPTO_FETCH_* 超时/重试语义（4xx 不重试）。失败抛 requests 异常。供 binance_derivatives 复用。"""
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


def _okx_ratio(url: str, params: dict) -> Optional[float]:
    """GET OKX rubik 多空比端点，取最新一行 [ts, ratio] 的比值；失败/空/结构异常 → None（fail-soft）。"""
    try:
        data = _http_get_json(url, params)
    except Exception as e:
        logger.warning("[多空比] %s 抓取失败: %s", url, e)
        return None
    arr = data.get("data") if isinstance(data, dict) else None
    if isinstance(arr, list) and arr and isinstance(arr[0], (list, tuple)) and len(arr[0]) >= 2:
        return _to_float(arr[0][1])
    return None


def fetch_perp_metrics(base: str, quote: str) -> dict:
    """spot BASE/QUOTE → OKX 永续 BASE-QUOTE-SWAP；并发拉 5 个公共接口；presence-only；失败/不支持 → {}。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return {}
    inst = f"{base}-{quote}-SWAP"
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_fr = ex.submit(_okx_first, OKX_FUNDING_URL, {"instId": inst})
        f_mp = ex.submit(_okx_first, OKX_MARK_URL, {"instType": "SWAP", "instId": inst})
        f_oi = ex.submit(_okx_first, OKX_OI_URL, {"instId": inst})
        f_ls = ex.submit(_okx_ratio, OKX_LS_ACCOUNT_URL, {"ccy": base, "period": "5m"})
        f_lst = ex.submit(_okx_ratio, OKX_LS_TOP_URL, {"instId": inst, "period": "5m"})
        fr, mp, oi = f_fr.result(), f_mp.result(), f_oi.result()
        ls, lst = f_ls.result(), f_lst.result()
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
    if ls is not None:
        out["long_short_ratio"] = ls
    if lst is not None:
        out["long_short_ratio_top"] = lst
    if not out:  # OKX 整组全空（含全部失败）→ 整源降级 Binance（fapi）
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环（bd 顶层 import 本模块 helpers）
        return bd.fetch_perp_metrics(base, quote)            # 空时返回 {}，presence-only 契约保持
    out["source"] = "okx"
    return out


def _perp_row_for_symbol(symbol: str) -> dict:
    """单个 BASE/QUOTE 现货代码 → {symbol, funding_rate?, open_interest_usd?, long_short_ratio?, long_short_ratio_top?}；无可用字段/异常 → {}。"""
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
        if metrics.get("long_short_ratio") is not None:
            row["long_short_ratio"] = metrics["long_short_ratio"]
        if metrics.get("long_short_ratio_top") is not None:
            row["long_short_ratio_top"] = metrics["long_short_ratio_top"]
        if row:
            row["symbol"] = symbol
        return row
    except Exception as e:  # 单币失败不拖垮整篮子聚合（对齐 _okx_first 的 fail-soft 约定）
        logger.warning("[永续复盘] %s 处理失败，跳过: %s", symbol, e)
        return {}


def fetch_perp_market_snapshot(symbols: list) -> dict:
    """对一篮子现货代码并发取各自 OKX 永续指标，聚合复盘情绪（presence-only）。
    OI 加权平均资金费率 + 总未平仓量(USD) + OI 加权多空比（全市场/大户）+ 按 |funding| 降序 top5 明细。无数据 → {}。"""
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
    ls_num = ls_den = 0.0       # 全市场多空比 OI 加权
    lst_num = lst_den = 0.0     # 大户多空比 OI 加权
    for r in rows:
        fr = r.get("funding_rate")
        oi = r.get("open_interest_usd")
        if oi is not None:
            total_oi += oi
            has_oi = True
            if fr is not None and oi > 0:
                weighted_num += fr * oi
                weighted_den += oi
            if oi > 0:
                lsr = r.get("long_short_ratio")
                if lsr is not None:
                    ls_num += lsr * oi
                    ls_den += oi
                lsrt = r.get("long_short_ratio_top")
                if lsrt is not None:
                    lst_num += lsrt * oi
                    lst_den += oi
    if weighted_den > 0:
        out["avg_funding_rate"] = weighted_num / weighted_den
    if has_oi:
        out["total_open_interest_usd"] = total_oi
    if ls_den > 0:
        out["avg_long_short_ratio"] = ls_num / ls_den
    if lst_den > 0:
        out["avg_long_short_ratio_top"] = lst_num / lst_den
    coins = sorted(
        rows,
        key=lambda r: abs(r["funding_rate"]) if r.get("funding_rate") is not None else -1.0,
        reverse=True,
    )[:5]
    if coins:
        out["coins"] = coins
    return out


def fetch_funding_rate_history(base: str, quote: str, start_ms: int, end_ms: int) -> list:
    """OKX 永续 BASE-QUOTE-SWAP 在半开窗口 [start_ms, end_ms) 内的资金费率列表（fundingRate 小数）。
    自 after=end_ms 起向后分页（after=更早），按窗口过滤；非线性计价/参数非法/无数据 → []。fail-soft。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return []
    inst = f"{base}-{quote}-SWAP"
    rates: list = []
    cursor = int(end_ms)  # OKX after: 返回 fundingTime 早于该值的记录；自窗口右界起翻，预算用在窗口内
    for _ in range(_FUNDING_HISTORY_MAX_PAGES):
        params = {"instId": inst, "limit": "100", "after": str(cursor)}
        try:
            data = _http_get_json(OKX_FUNDING_HISTORY_URL, params)
        except Exception as e:
            logger.warning("[资金费历史] %s 抓取失败: %s", inst, e)
            break
        arr = data.get("data") if isinstance(data, dict) else None
        if not isinstance(arr, list) or not arr:
            break
        page_min_ts = None
        for item in arr:
            ts = _to_float(item.get("fundingTime"))
            fr = _to_float(item.get("fundingRate"))
            if ts is None:
                continue
            page_min_ts = ts if page_min_ts is None else min(page_min_ts, ts)
            if fr is not None and start_ms <= ts < end_ms:  # 半开 [start, end)：含 start、排除 end 边界结算
                rates.append(fr)
        if page_min_ts is None or page_min_ts <= start_ms or len(arr) < 100:
            break
        cursor = int(page_min_ts)
    if not rates:  # OKX 窗口内无数据/抓取失败 → 整源降级 Binance 同窗口
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环
        return bd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
    return rates
