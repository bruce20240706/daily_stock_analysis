"""多周期共振判定（纯逻辑，无 I/O）。

高周期趋势 = 均线排列（多头/空头）+ 收盘 vs MA20 确认（确认门，无阈值参数）。
共振 = 日线信号方向 × 高周期同向；周线门控、月线加强。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from data_provider.base import attach_ma_indicators
from data_provider.resample import resample_ohlc

RESONANCE_NONE = "none"
RESONANCE_WEEKLY = "weekly"
RESONANCE_WEEKLY_MONTHLY = "weekly_monthly"


def period_trend(df_period: pd.DataFrame) -> str:
    """高周期帧 → 趋势方向（取最后一根 bar）。

    bullish: MA5>MA10>MA20 且 close>=MA20；bearish: MA5<MA10<MA20 且 close<=MA20；
    其余（纠缠/收盘未确认/MA 缺失/历史不足）: neutral。
    """
    if df_period is None or df_period.empty:
        return "neutral"
    last = df_period.iloc[-1]
    ma5 = last.get("ma5")
    ma10 = last.get("ma10")
    ma20 = last.get("ma20")
    close = last.get("close")
    if any(v is None or pd.isna(v) for v in (ma5, ma10, ma20, close)):
        return "neutral"
    if ma5 > ma10 > ma20 and close >= ma20:
        return "bullish"
    if ma5 < ma10 < ma20 and close <= ma20:
        return "bearish"
    return "neutral"


def resonance_level(
    signal_direction: Optional[str],
    weekly_trend: str,
    monthly_trend: str,
) -> str:
    """日线信号方向 × 高周期趋势 → 共振档位（周线门控、月线加强）。"""
    if signal_direction not in ("bullish", "bearish"):
        return RESONANCE_NONE
    if weekly_trend != signal_direction:
        return RESONANCE_NONE
    if monthly_trend == signal_direction:
        return RESONANCE_WEEKLY_MONTHLY
    return RESONANCE_WEEKLY


def resonance_from_daily(
    df_daily: pd.DataFrame,
    signal_direction: Optional[str],
) -> str:
    """日线帧 + 信号方向 → 共振档位。非方向性信号直接返回 none（调用方据此跳过深抓）。"""
    if signal_direction not in ("bullish", "bearish"):
        return RESONANCE_NONE
    weekly = period_trend(attach_ma_indicators(resample_ohlc(df_daily, "weekly")))
    monthly = period_trend(attach_ma_indicators(resample_ohlc(df_daily, "monthly")))
    return resonance_level(signal_direction, weekly, monthly)
