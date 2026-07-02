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

from src.config import get_config, parse_env_float, parse_env_int
from src.core.trading_calendar import get_market_for_stock
from src.services.multi_period_resonance import resonance_from_daily
from src.services.signals_service import (
    build_signals_payload, buy_signal_to_direction, STALE_TRADING_DAYS_DEFAULT,
    compute_plan_quality, compute_marker_statuses,
)
from src.services.signal_hit_rate import resolve_marker_hit_fields
from src.services.stock_service import StockService
from src.services.volume_price_signals import (
    _DEFAULT_ATR_MULT, _DEFAULT_RR_TARGET, VPSConfig,
    compute_volume_price_signals, derive_price_levels,
)
from src.stock_analyzer import StockTrendAnalyzer
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

RESONANCE_DAILY_DAYS = 750  # 共振深抓日线天数（够月线 MA20 暖机）


def _augment_payload_finer_fields(payload: dict, rows: list) -> None:
    """编排层 compute-on-read：填 plan_quality（响应级）+ 各 rule marker 的 status（in-place）。"""
    payload["plan_quality"] = compute_plan_quality(
        payload.get("price_lines") or {}, payload.get("consistency", "unknown")
    )
    bar_dates = [str(r.get("date")) for r in rows]
    default_window = int(get_config().signal_backtest_horizon_bars)
    compute_marker_statuses(payload.get("markers") or [], bar_dates, default_window)


@dataclass
class BoardSignals:
    signals_payload: dict
    rule_direction: Optional[str]
    latest_close: Optional[float]
    name: Optional[str]
    market: Optional[str]
    resonance: str = "none"


def _infer_market(code: str) -> Optional[str]:
    c = (code or "").upper()
    if "/" in c:
        return "crypto"
    if c.startswith("HK") or c.endswith(".HK"):
        return "HK"
    if c[:6].isdigit():
        return "CN"
    return "US"


def build_signals_for_code(code: str, *, days: int = 120, interval: str = "1d") -> BoardSignals:
    """单股编排：取数（与 /history 同源）→ 引擎 → BuySignal → consistency → 命中率回填 → price_lines。

    interval：命中率回填读取的信号桶粒度；默认 '1d'（行为不变）。看板 K 线/标记仍按日线计算，
    仅把可信度（hit_rate/verified 等）从对应 interval 桶解析，供"分钟级可信度"查看。
    """
    # 延迟导入打破本特性自身引入的 endpoint↔service 循环：
    # 抽出 build_signals_for_code 后，单股端点(stocks.py)反过来调用本模块，
    # 而本编排又复用 stocks.py 的 build_price_lines/_elapsed_trading_days。
    # 这两个 helper 与 endpoint 无实质耦合，后续可下沉到 service 层以消除循环
    # (本次为控制改动面/避免 service→schema 依赖暂不迁移)。
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
            "resonance": "none",
        }
        return BoardSignals(signals_payload=payload, rule_direction=None,
                            latest_close=None, name=name, market=market, resonance="none")

    df = pd.DataFrame(rows)
    latest_bar_date = str(rows[-1].get("date"))
    _lc = rows[-1].get("close")
    latest_close = float(_lc) if _lc is not None else None

    engine_market = get_market_for_stock(code)
    engine_result = compute_volume_price_signals(df, config=VPSConfig.for_market(engine_market))

    rule_signal = None
    try:
        trend_result = StockTrendAnalyzer().analyze(df, code)
        rule_signal = getattr(trend_result, "buy_signal", None)
    except Exception as exc:
        logger.warning("规则代表方向计算失败 code=%s err=%s", code, exc)

    rule_dir = buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral"

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
        hit_fields_resolver=lambda st, c: resolve_marker_hit_fields(st, c, interval=interval),
    )

    atr_mult = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_ATR_MULT"), _DEFAULT_ATR_MULT,
                               field_name="KLINE_PRICE_LEVEL_ATR_MULT", minimum=0.1)
    rr_target = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_RR_TARGET"), _DEFAULT_RR_TARGET,
                                field_name="KLINE_PRICE_LEVEL_RR_TARGET", minimum=0.1)
    price_levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)
    payload["price_lines"] = build_price_lines(price_levels).model_dump()

    _augment_payload_finer_fields(payload, rows)

    # 多周期共振：仅对方向性(bullish/bearish)做一次独立深抓（门控）；hold/中性不抓；失败降级 none。
    resonance = "none"
    if rule_dir in ("bullish", "bearish"):
        try:
            deep = service.get_history_data(stock_code=code, period="daily", days=RESONANCE_DAILY_DAYS)
            deep_rows = deep.get("data", []) or []
            if deep_rows:
                resonance = resonance_from_daily(pd.DataFrame(deep_rows), rule_dir)
        except Exception as exc:
            logger.warning("共振计算失败 code=%s err=%s", code, exc)
            resonance = "none"

    payload["resonance"] = resonance

    return BoardSignals(
        signals_payload=payload,
        rule_direction=rule_dir,
        latest_close=latest_close, name=name, market=market,
        resonance=resonance,
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
            return {
                "hit_rate": m.get("hit_rate"),
                "hit_sample": m.get("hit_sample"),
                "verified": bool(m.get("verified", False)),
                "ci_low": m.get("ci_low"),
                "ci_high": m.get("ci_high"),
                "baseline_excess": m.get("baseline_excess"),
                "ci_low_corrected": m.get("ci_low_corrected"),
                "family_size": m.get("family_size"),
                "horizon_bars": m.get("horizon_bars"),
                "signal_status": m.get("status"),
            }
    return {"hit_rate": None, "hit_sample": None, "verified": False,
            "ci_low": None, "ci_high": None, "baseline_excess": None,
            "ci_low_corrected": None, "family_size": None,
            "horizon_bars": None, "signal_status": None}


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
        "plan_quality": payload.get("plan_quality"),
        "resonance": payload.get("resonance", "none"),
        "status": payload.get("status", "ok"), "degraded_reason": payload.get("degraded_reason"),
    }


def _degraded_entry(code: str, reason: str) -> dict:
    return {
        "code": code, "name": None, "market": _infer_market(code),
        "action_group": "unavailable", "rule_direction": None, "llm_direction": None,
        "consistency": "unknown", "key_signals": [],
        "price_lines": {"entry": None, "stop": None, "target": None},
        "latest_close": None, "hit_rate": None, "hit_sample": None, "verified": False,
        "ci_low": None, "ci_high": None, "baseline_excess": None,
        "ci_low_corrected": None, "family_size": None,
        "resonance": "none",
        "status": "degraded", "degraded_reason": reason,
    }


def _compute_entry(code: str, *, days: int, refresh: bool, now_s: float, ttl_s: int,
                   interval: str = "1d") -> dict:
    cache_key = (code, days, interval)   # interval 入键,避免 1d/5m 串桶
    if not refresh and ttl_s > 0:
        with _BOARD_CACHE_LOCK:
            hit = _BOARD_CACHE.get(cache_key)
            if hit and (now_s - hit[0]) < ttl_s:
                return hit[1]
    try:
        entry = _entry_from_board_signals(
            code, build_signals_for_code(code, days=days, interval=interval))
    except Exception as exc:
        logger.warning("看板单股计算失败 code=%s err=%s", code, exc)
        entry = _degraded_entry(code, "信号计算失败")
    # 只缓存成功(ok)结果，且仅在缓存启用(ttl_s>0)时写入：
    # - ttl_s==0 表示关闭缓存，不应留下永不读取的死条目；
    # - degraded/失败(含瞬时取数失败)不缓存，确保下次非 refresh 加载会重试、自愈，
    #   而不是把 unavailable 粘住一个 TTL。
    if ttl_s > 0 and entry["status"] == "ok":
        with _BOARD_CACHE_LOCK:
            _BOARD_CACHE[cache_key] = (now_s, entry)
    return entry


def build_board(codes: list, *, days: int = 120, refresh: bool = False,
                interval: str = "1d") -> dict:
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
                lambda c: _compute_entry(c, days=days, refresh=refresh, now_s=now_s,
                                         ttl_s=ttl_s, interval=interval),
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
