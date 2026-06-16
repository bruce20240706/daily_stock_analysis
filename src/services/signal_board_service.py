"""容器 C 信号看板编排（含 I/O）。复用 signals_service 纯函数 + L3 引擎/反算器。

注意：signals_service.py 为纯函数模块；本模块承载取数+DB+引擎的单股编排（build_signals_for_code）
与并发聚合（build_board），供单股 /signals 端点与看板 /signals/board 端点共用，避免平行实现。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.config import parse_env_float, parse_env_int
from src.services.signals_service import build_signals_payload, buy_signal_to_direction, STALE_TRADING_DAYS_DEFAULT
from src.services.signal_hit_rate import resolve_marker_hit_fields
from src.services.stock_service import StockService
from src.services.volume_price_signals import (
    _DEFAULT_ATR_MULT, _DEFAULT_RR_TARGET, VPSConfig,
    compute_volume_price_signals, derive_price_levels,
)
from src.stock_analyzer import StockTrendAnalyzer
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class BoardSignals:
    signals_payload: dict
    rule_direction: Optional[str]
    latest_close: Optional[float]
    name: Optional[str]
    market: Optional[str]


def _infer_market(code: str) -> Optional[str]:
    c = (code or "").upper()
    if "/" in c:
        return "crypto"
    if c.startswith("HK") or c.endswith(".HK"):
        return "HK"
    if c[:6].isdigit():
        return "CN"
    return "US"


def build_signals_for_code(code: str, *, days: int = 120) -> BoardSignals:
    """单股编排：取数（与 /history 同源）→ 引擎 → BuySignal → consistency → 命中率回填 → price_lines。"""
    # 延迟导入，避免与 endpoint 层辅助函数的潜在循环（沿用 apply_price_levels_to_guard 先例）
    from api.v1.endpoints.stocks import build_price_lines, _elapsed_trading_days

    service = StockService()
    history = service.get_history_data(stock_code=code, period="daily", days=days)
    rows = history.get("data", []) or []
    name = history.get("stock_name")
    market = _infer_market(code)

    if not rows:
        payload = {
            "status": "degraded", "markers": [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "unknown", "degraded_reason": "无可用历史数据",
        }
        return BoardSignals(signals_payload=payload, rule_direction=None,
                            latest_close=None, name=name, market=market)

    df = pd.DataFrame(rows)
    latest_bar_date = str(rows[-1].get("date"))
    _lc = rows[-1].get("close")
    latest_close = float(_lc) if _lc is not None else None

    engine_result = compute_volume_price_signals(df, config=VPSConfig.from_env())

    rule_signal = None
    try:
        trend_result = StockTrendAnalyzer().analyze(df, code)
        rule_signal = getattr(trend_result, "buy_signal", None)
    except Exception as exc:
        logger.warning("规则代表方向计算失败 code=%s err=%s", code, exc)

    llm_record = None
    try:
        llm_record = DatabaseManager.get_instance().get_latest_analysis_by_code(code)
    except Exception as exc:
        logger.warning("LLM 最新结论读取失败 code=%s err=%s", code, exc)

    trading_days_elapsed = _elapsed_trading_days(llm_record, rows)
    stale_threshold = parse_env_int(
        os.getenv("SIGNALS_STALE_TRADING_DAYS"), STALE_TRADING_DAYS_DEFAULT,
        field_name="SIGNALS_STALE_TRADING_DAYS", minimum=1,
    )

    payload = build_signals_payload(
        engine_result=engine_result, rule_signal=rule_signal,
        latest_bar_date=latest_bar_date, latest_close=latest_close,
        llm_record=llm_record, trading_days_elapsed=trading_days_elapsed,
        stale_threshold=stale_threshold, code=code,
        hit_fields_resolver=resolve_marker_hit_fields,
    )

    atr_mult = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_ATR_MULT"), _DEFAULT_ATR_MULT,
                               field_name="KLINE_PRICE_LEVEL_ATR_MULT", minimum=0.1)
    rr_target = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_RR_TARGET"), _DEFAULT_RR_TARGET,
                                field_name="KLINE_PRICE_LEVEL_RR_TARGET", minimum=0.1)
    price_levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)
    payload["price_lines"] = build_price_lines(price_levels).model_dump()

    return BoardSignals(
        signals_payload=payload,
        rule_direction=buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral",
        latest_close=latest_close, name=name, market=market,
    )


_ACTION_BY_DIRECTION = {"bullish": "buy", "bearish": "sell", "neutral": "hold"}
_BOARD_CACHE: dict = {}                  # (code, days) -> (ts_seconds, BoardEntry-dict)
_BOARD_CACHE_LOCK = threading.Lock()


def _llm_direction_from_markers(markers: list) -> Optional[str]:
    for m in markers:
        if m.get("source") == "llm":
            return m.get("direction")
    return None


def _key_signals_from_markers(markers: list) -> list:
    seen, out = set(), []
    for m in markers:
        if m.get("source") == "rule":
            st = m.get("signal_type")
            if st and st not in seen:
                seen.add(st)
                out.append(st)
    return out


def _hit_fields_from_markers(markers: list) -> dict:
    for m in markers:
        if m.get("source") == "rule":
            return {"hit_rate": m.get("hit_rate"), "hit_sample": m.get("hit_sample"),
                    "verified": bool(m.get("verified", False))}
    return {"hit_rate": None, "hit_sample": None, "verified": False}


def _entry_from_board_signals(code: str, bs: "BoardSignals") -> dict:
    payload = bs.signals_payload
    markers = payload.get("markers", []) or []
    action = "unavailable" if bs.rule_direction is None else _ACTION_BY_DIRECTION[bs.rule_direction]
    return {
        "code": code, "name": bs.name, "market": bs.market,
        "action_group": action, "rule_direction": bs.rule_direction,
        "llm_direction": _llm_direction_from_markers(markers),
        "consistency": payload.get("consistency", "unknown"),
        "key_signals": _key_signals_from_markers(markers),
        "price_lines": payload.get("price_lines", {"entry": None, "stop": None, "target": None}),
        "latest_close": bs.latest_close,
        **_hit_fields_from_markers(markers),
        "status": payload.get("status", "ok"), "degraded_reason": payload.get("degraded_reason"),
    }


def _degraded_entry(code: str, reason: str) -> dict:
    return {
        "code": code, "name": None, "market": _infer_market(code),
        "action_group": "unavailable", "rule_direction": None, "llm_direction": None,
        "consistency": "unknown", "key_signals": [],
        "price_lines": {"entry": None, "stop": None, "target": None},
        "latest_close": None, "hit_rate": None, "hit_sample": None, "verified": False,
        "status": "degraded", "degraded_reason": reason,
    }


def _compute_entry(code: str, *, days: int, refresh: bool, now_s: float, ttl_s: int) -> dict:
    cache_key = (code, days)
    if not refresh and ttl_s > 0:
        with _BOARD_CACHE_LOCK:
            hit = _BOARD_CACHE.get(cache_key)
            if hit and (now_s - hit[0]) < ttl_s:
                return hit[1]
    try:
        entry = _entry_from_board_signals(code, build_signals_for_code(code, days=days))
    except Exception as exc:
        logger.warning("看板单股计算失败 code=%s err=%s", code, exc)
        entry = _degraded_entry(code, "信号计算失败")
    with _BOARD_CACHE_LOCK:
        _BOARD_CACHE[cache_key] = (now_s, entry)
    return entry


def build_board(codes: list, *, days: int = 120, refresh: bool = False) -> dict:
    ttl_s = parse_env_int(os.getenv("SIGNALS_BOARD_CACHE_TTL_S"), 300,
                          field_name="SIGNALS_BOARD_CACHE_TTL_S", minimum=0)
    max_workers = parse_env_int(os.getenv("SIGNALS_BOARD_MAX_WORKERS"), 8,
                                field_name="SIGNALS_BOARD_MAX_WORKERS", minimum=1)
    now_s = time.time()
    entries: list = []
    if codes:
        workers = min(max_workers, len(codes))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="signal_board_") as ex:
            entries = list(ex.map(
                lambda c: _compute_entry(c, days=days, refresh=refresh, now_s=now_s, ttl_s=ttl_s),
                codes,
            ))
    counts = {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0}
    degraded_codes = []
    for e in entries:
        counts[e["action_group"]] = counts.get(e["action_group"], 0) + 1
        if e["status"] == "degraded":
            degraded_codes.append(e["code"])
    return {"as_of": int(now_s * 1000), "entries": entries, "counts": counts,
            "degraded_codes": degraded_codes}
