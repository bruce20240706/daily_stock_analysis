"""盘中/分钟级回测纯函数 helper。

仅承载无副作用的口径换算(interval↔minutes、窗口 bar 数派生)、engine_version 标签
构建与成本后处理,供 BacktestService / CLI / API 复用,便于单测。
"""
from __future__ import annotations

from typing import Optional

# interval → 每根 bar 的分钟数(crypto 7×24)
INTRADAY_INTERVAL_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}

# 允许集:'1d' 为日线(走既有路径),其余为分钟
SUPPORTED_INTERVALS: tuple[str, ...] = ("1d", "1m", "5m", "15m", "1h")

_MINUTES_PER_DAY = 1440  # crypto 24h


def is_intraday_interval(interval: str) -> bool:
    """True 当 interval 为受支持的分钟粒度(不含 '1d')。"""
    return interval in INTRADAY_INTERVAL_MINUTES


def bars_per_day(interval: str) -> int:
    """每自然日的 bar 根数(crypto 7×24)。非分钟 interval 抛 ValueError。"""
    minutes = INTRADAY_INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError(f"不支持的分钟 interval: {interval!r}")
    return _MINUTES_PER_DAY // minutes


def derive_window_bar_count(eval_window_days: int, interval: str) -> int:
    """日历窗口(天)→ 分钟 bar 切片长度。"""
    return int(eval_window_days) * bars_per_day(interval)


def build_engine_version_tag(base: str, interval: str, leverage: int) -> str:
    """行隔离标签:base [+ -{interval} 若非 1d] [+ -x{lev} 若 >1]。顺序固定。"""
    tag = str(base)
    if is_intraday_interval(interval):
        tag = f"{tag}-{interval}"
    if int(leverage) > 1:
        tag = f"{tag}-x{int(leverage)}"
    return tag


def apply_round_trip_cost(
    return_pct: Optional[float], fee_bps: float, slippage_bps: float
) -> Optional[float]:
    """对一进一出收益(百分比)扣减成本。默认 0 → 原样返回(行为不变)。

    一进一出 = 2 次成交,每次成本 = fee_bps + slippage_bps(基点,1bp=0.01%)。
    """
    if return_pct is None:
        return None
    if not fee_bps and not slippage_bps:
        return return_pct
    cost_pct = 2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0
    return return_pct - cost_pct
