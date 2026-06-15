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
from typing import Any, List, Optional

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


def _marker_from_vpsignal(sig: Any) -> dict:
    """把 M1 VPSignal 映射为 SignalMarker dict（source=rule）。

    时间锚直接透传 M1 `VPSignal.timestamp`（epoch ms，Asia/Shanghai），
    不经 date_str_to_epoch_ms（VPSignal 无 .date 字段；date_str_to_epoch_ms
    仅供 LLM 点的 'YYYY-MM-DD' latest_bar_date 用）。
    """
    return {
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
        # 以下 M2c 回填
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "as_of": None,
    }


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
        "as_of": as_of,
    }


def build_signals_payload(
    *,
    engine_result: Any,
    rule_signal: Optional[BuySignal],
    latest_bar_date: str,
    latest_close: Optional[float] = None,
    llm_record: Any,
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
) -> dict:
    """
    组装 /signals 响应 dict（端点据此构造 SignalsResponse）。

    - markers：引擎逐 bar rule markers + LLM 最新 1 点（若方向可识别）。
    - price_lines：M2a 全 null，M2b 填值。
    - consistency：用收敛后的单个 BuySignal 与 LLM 最新结论计算。
    - status/degraded_reason：透传引擎结果。
    """
    markers: List[dict] = [_marker_from_vpsignal(s) for s in (engine_result.markers or [])]

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

    return {
        "status": engine_result.status,
        "markers": markers,
        "price_lines": {"entry": None, "stop": None, "target": None},
        "consistency": consistency,
        "degraded_reason": engine_result.degraded_reason,
    }
