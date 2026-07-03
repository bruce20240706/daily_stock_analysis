# -*- coding: utf-8 -*-
"""
===================================
信号端点组装服务（M2a）
===================================

职责：
1. 把 M1 量价引擎 markers 映射为 API SignalMarker。
2. 用收敛后的单个 BuySignal 作"规则代表方向"，与 LLM 最新结论计算 consistency。
3. 处理 LLM 陈旧度（stale）与未识别（unknown）。

本服务为纯函数集合，不持有数据库/网络句柄，便于直测。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, List, Optional

from src.stock_analyzer import BuySignal
from src.report_language import infer_decision_type_from_advice

logger = logging.getLogger(__name__)

# LLM 结论超过该交易日数则视为陈旧；可由调用方覆盖
STALE_TRADING_DAYS_DEFAULT = 5

# 把无时区的本地日期统一按 Asia/Shanghai 解释（与前端 format.ts 约定一致）
_SHANGHAI_TZ = timezone(timedelta(hours=8))

# BuySignal(6 态) → 三向
_BUY_SIGNAL_DIRECTION = {
    BuySignal.STRONG_BUY: "bullish",
    BuySignal.BUY: "bullish",
    BuySignal.HOLD: "neutral",
    BuySignal.WAIT: "neutral",
    BuySignal.SELL: "bearish",
    BuySignal.STRONG_SELL: "bearish",
}

# infer_decision_type_from_advice 输出 → 三向
_ADVICE_DIRECTION = {
    "buy": "bullish",
    "hold": "neutral",
    "sell": "bearish",
}


def buy_signal_to_direction(signal: BuySignal) -> str:
    """把收敛后的 BuySignal 映射为 bullish/bearish/neutral。"""
    return _BUY_SIGNAL_DIRECTION.get(signal, "neutral")


def date_str_to_epoch_ms(date_str: str) -> int:
    """'YYYY-MM-DD' → epoch ms，统一按 Asia/Shanghai（三市场一致）。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_SHANGHAI_TZ)
    return int(dt.timestamp() * 1000)


def datetime_to_epoch_ms(dt: datetime) -> int:
    """LLM created_at（无时区默认本地库时间）→ epoch ms，按 Asia/Shanghai 解释。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_SHANGHAI_TZ)
    return int(dt.timestamp() * 1000)


def _advice_to_direction(advice: Any) -> Optional[str]:
    """复用现有 infer_decision_type_from_advice；未识别返回 None。"""
    if advice is None:
        return None
    text = str(advice).strip()
    if not text:
        return None
    # default 用一个哨兵以区分"识别为 hold" vs "无法识别"
    decision = infer_decision_type_from_advice(text, default="__unrecognized__")
    if decision == "__unrecognized__":
        return None
    return _ADVICE_DIRECTION.get(decision)


def compute_consistency(
    *,
    rule_direction: str,
    llm_advice: Any,
    llm_created_at: Optional[datetime],
    latest_bar_date: str,  # noqa: ARG001  保留以备 M2b/邻域扩展，本期不参与判定
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
) -> str:
    """
    consistency 单一定义：
    - 无 LLM 记录 / LLM 方向未识别 → unknown
    - LLM 超过 stale_threshold 个交易日 → stale
    - 两向相同（非 neutral）→ consistent
    - 一向 neutral、一向有方向 → divergent
    - 两向相反（bullish vs bearish）→ conflict
    - 其余（双 neutral）→ consistent
    """
    if llm_advice is None or llm_created_at is None:
        return "unknown"

    llm_direction = _advice_to_direction(llm_advice)
    if llm_direction is None:
        return "unknown"

    if trading_days_elapsed is not None and trading_days_elapsed > stale_threshold:
        return "stale"

    if rule_direction == llm_direction:
        return "consistent"

    pair = {rule_direction, llm_direction}
    if pair == {"bullish", "bearish"}:
        return "conflict"
    # 含 neutral 的不一致（divergent）
    return "divergent"


def _marker_from_vpsignal(
    sig: Any,
    *,
    code: Optional[str] = None,
    hit_fields_resolver: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """把 M1 VPSignal 映射为 SignalMarker dict（source=rule）。

    时间锚直接透传 M1 `VPSignal.timestamp`（epoch ms，Asia/Shanghai），
    不经 date_str_to_epoch_ms（VPSignal 无 .date 字段；date_str_to_epoch_ms
    仅供 LLM 点的 'YYYY-MM-DD' latest_bar_date 用）。

    若提供 hit_fields_resolver 且有 code，则用其回填 hit_rate/hit_sample/verified
    （M2c 命中率实证）；否则保留 M2a 默认（全 None / verified=False）。
    """
    marker = {
        "timestamp": int(sig.timestamp),
        "price": float(sig.price),
        "anchor": sig.anchor,
        "direction": sig.direction,
        "signal_type": sig.signal_type,
        "source": "rule",
        "confidence": sig.confidence,
        "is_daily_approx": bool(sig.is_daily_approx),
        "is_anomalous": bool(sig.is_anomalous),
        "reason": sig.reason,
        "threshold": sig.threshold,
        "observed_value": sig.observed_value,
        # 以下默认值；rule marker 经 resolver 回填（M2c/M3-A6）
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "ci_low": None,
        "ci_high": None,
        "baseline_excess": None,
        "as_of": None,
        "ci_low_corrected": None,
        "family_size": None,
        "horizon_bars": None,
        "status": None,
        "risk_metrics": None,
        "oos": None,
    }
    if hit_fields_resolver is not None and code:
        try:
            fields = hit_fields_resolver(sig.signal_type, code)
            marker["hit_rate"] = fields.get("hit_rate")
            marker["hit_sample"] = fields.get("hit_sample")
            marker["verified"] = bool(fields.get("verified", False))
            marker["ci_low"] = fields.get("ci_low")
            marker["ci_high"] = fields.get("ci_high")
            marker["baseline_excess"] = fields.get("baseline_excess")
            marker["horizon_bars"] = fields.get("horizon")
            marker["ci_low_corrected"] = fields.get("ci_low_corrected")
            marker["family_size"] = fields.get("family_size")
            marker["risk_metrics"] = fields.get("risk_metrics")
            marker["oos"] = fields.get("oos")
        except Exception:
            logger.warning(
                "resolve_marker_hit_fields 失败，跳过回填 signal_type=%s code=%s",
                sig.signal_type, code,
            )
    return marker


def _llm_marker(
    *,
    llm_record: Any,
    latest_bar_date: str,
    latest_close: Optional[float],
) -> Optional[dict]:
    """LLM 最新 1 点：锚定最新 bar（时间锚 + 价位锚到该 bar 收盘）、source=llm、as_of=结论生成时间。"""
    advice = getattr(llm_record, "operation_advice", None)
    direction = _advice_to_direction(advice)
    if direction is None:
        # 方向未识别则不画 LLM 点（consistency 另行标 unknown）
        return None
    created_at = getattr(llm_record, "created_at", None)
    as_of = datetime_to_epoch_ms(created_at) if created_at is not None else None
    return {
        "timestamp": date_str_to_epoch_ms(latest_bar_date),
        # 价位锚到最新 bar 的收盘，而非 0.0 占位（latest_close 缺失才回落 0.0）
        "price": float(latest_close) if latest_close is not None else 0.0,
        "anchor": "close",
        "direction": direction,
        "signal_type": "llm_advice",
        "source": "llm",
        "confidence": "medium",
        "is_daily_approx": False,
        "is_anomalous": False,
        "reason": f"LLM 最新结论：{advice}",
        "threshold": None,
        "observed_value": None,
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "ci_low": None,
        "ci_high": None,
        "baseline_excess": None,
        "ci_low_corrected": None,
        "family_size": None,
        "as_of": as_of,
        "horizon_bars": None,
        "status": None,
        "risk_metrics": None,
        "oos": None,
    }


def compute_plan_quality(price_lines: dict, consistency: str) -> Optional[str]:
    """交易计划质量(确定性,与可信度正交)。price_lines 全 null→None。"""
    entry = price_lines.get("entry") if isinstance(price_lines, dict) else None
    stop = price_lines.get("stop") if isinstance(price_lines, dict) else None
    target = price_lines.get("target") if isinstance(price_lines, dict) else None
    if entry is None and stop is None and target is None:
        return None
    if entry is None or stop is None or consistency == "conflict":
        return "low"
    if target is not None and consistency == "consistent":
        return "high"
    return "medium"


def compute_marker_statuses(markers: list, bar_dates: list, default_window: int) -> None:
    """in-place 给每条 source==rule marker 写 status(active/aging/expired);找不到 bar→None。"""
    ts_to_idx = {}
    for i, d in enumerate(bar_dates):
        try:
            ts_to_idx[date_str_to_epoch_ms(str(d))] = i
        except Exception:
            continue
    last_idx = len(bar_dates) - 1
    for m in markers:
        if m.get("source") != "rule":
            continue
        try:
            idx = ts_to_idx.get(int(m.get("timestamp")))
        except (TypeError, ValueError):
            # timestamp 缺失/非数值（实流恒为 epoch ms int）→ 无法定位 bar，状态不可计算
            idx = None
        if idx is None:
            m["status"] = None
            continue
        bars_since = last_idx - idx
        # horizon_bars 为 0/None/非正 都不是有效窗口，回退默认窗口
        hb = m.get("horizon_bars")
        w = hb if isinstance(hb, int) and hb > 0 else default_window
        if bars_since <= 0:
            m["status"] = "active"
        elif bars_since < w:
            m["status"] = "aging"
        else:
            m["status"] = "expired"


def build_signals_payload(
    *,
    engine_result: Any,
    rule_signal: Optional[BuySignal],
    latest_bar_date: str,
    latest_close: Optional[float] = None,
    llm_record: Any,
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
    code: Optional[str] = None,
    hit_fields_resolver: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """
    组装 /signals 响应 dict（端点据此构造 SignalsResponse）。

    - markers：引擎逐 bar rule markers + LLM 最新 1 点（若方向可识别）。
    - price_lines：M2a 全 null，M2b 填值。
    - consistency：用收敛后的单个 BuySignal 与 LLM 最新结论计算。
    - status/degraded_reason：透传引擎结果。
    """
    # 终审#9 + D7 修正：按 (signal_type, code) 复用 resolver，避免同键重复 SELECT。
    # M3-A6 起 resolve_marker_hit_fields 按 (signal_type, market) 查 signal_stats，
    # signal_type 改变聚合源——曾按 code-only 缓存导致同股非首个 signal_type 的
    # marker 错挂第一个 signal_type 的 hit_rate/verified 等全部字段（现役 bug，已修）。
    # 缓存仅存活于本次调用，不用 module-level/lru_cache（防跨请求陈旧数据）。
    effective_resolver = hit_fields_resolver
    if hit_fields_resolver is not None and code:
        _per_call_cache: dict = {}

        def effective_resolver(signal_type: str, resolver_code: str) -> dict:
            cache_key = (signal_type, resolver_code)
            if cache_key in _per_call_cache:
                return _per_call_cache[cache_key]
            fields = hit_fields_resolver(signal_type, resolver_code)
            _per_call_cache[cache_key] = fields
            return fields

    markers: List[dict] = [
        _marker_from_vpsignal(s, code=code, hit_fields_resolver=effective_resolver)
        for s in (engine_result.markers or [])
    ]

    llm_advice = getattr(llm_record, "operation_advice", None) if llm_record is not None else None
    llm_created_at = getattr(llm_record, "created_at", None) if llm_record is not None else None

    if llm_record is not None:
        llm_point = _llm_marker(
            llm_record=llm_record,
            latest_bar_date=latest_bar_date,
            latest_close=latest_close,
        )
        if llm_point is not None:
            markers.append(llm_point)

    rule_direction = buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral"
    consistency = compute_consistency(
        rule_direction=rule_direction,
        llm_advice=llm_advice,
        llm_created_at=llm_created_at,
        latest_bar_date=latest_bar_date,
        trading_days_elapsed=trading_days_elapsed,
        stale_threshold=stale_threshold,
    )

    # 按 signal_type 收敛风险画像：O(K) 而非逐 bar marker 携带（防载荷膨胀，D5）。
    # 首个非 None dict 者胜出（同型多 marker 的画像本就相同，取首条足够）。
    risk_by_type: dict = {}
    for m in markers:
        rm = m.get("risk_metrics")
        st = m.get("signal_type")
        if isinstance(rm, dict) and st and st not in risk_by_type:
            risk_by_type[st] = rm

    # 按 signal_type 收敛 OOS holdout 切分报告：同 risk_by_type 口径(O(K),D5 同构，Inc 1e)。
    oos_by_type: dict = {}
    for m in markers:
        rep = m.get("oos")
        st = m.get("signal_type")
        if isinstance(rep, dict) and st and st not in oos_by_type:
            oos_by_type[st] = rep

    return {
        "status": engine_result.status,
        "markers": markers,
        "price_lines": {"entry": None, "stop": None, "target": None},
        "consistency": consistency,
        "degraded_reason": engine_result.degraded_reason,
        "risk_metrics_by_signal_type": risk_by_type,
        "oos_by_signal_type": oos_by_type,
    }
