# -*- coding: utf-8 -*-
"""量价信号引擎（M1）：纯函数，输入 OHLCV DataFrame，输出 VPSResult。

设计要点：
- 所有滚动量基元统一 shift(1)，防未来函数。
- swing pivot 左右各 k 根确认，天然滞后 k，OBV 背离 / VSA 高低点全部复用。
- 八法为穷尽且互斥的二维查表，每格必有归类（信号或 neutral 兜底）。
- B 类（VSA/Upthrust/Spring）强制降权：不进 consistency 投票、不驱动 price_lines、置信 <= low、top-k 限流。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.config import parse_env_float
from src.services.alert_indicators import normalize_ohlcv

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class VPSConfig:
    eps: float = 0.004                  # 价档 flat 判定半带宽（pct_chg 绝对值 <= eps）
    vol_low: float = 0.7                # 量档 low 上界（< vol_low）
    vol_shrink: float = 0.8             # shrink 上界
    vol_up: float = 1.2                 # normal 上界
    vol_high: float = 1.5               # up 上界 / high 下界（>= vol_high）
    swing_k: int = 3                    # swing pivot 左右确认根数
    vol_ma_window: int = 20             # 量基准窗口（交易 bar 数）
    breakout_window: int = 20           # 放量突破 high.rolling 窗口 N
    breakout_rel_vol: float = 2.0       # 放量突破 rel_vol 阈值
    pullback_rel_vol: float = 0.9       # 缩量回调段内 rel_vol 上界
    pullback_atr_mult: float = 3.0      # 缩量回调最大回撤 = ATR * 倍数
    atr_period: int = 14
    b_class_top_k: int = 2              # B 类每结果集限流 top-k
    # === 数字货币旁路阈值（M3-A）：不配置则回落到对应的日线口径默认值，非 crypto 行为字节一致 ===
    crypto_breakout_window: int = 20    # VPS_CRYPTO_BREAKOUT_WINDOW（默认 = breakout_window）
    crypto_atr_period: int = 14         # VPS_CRYPTO_ATR_PERIOD（默认 = atr_period）
    crypto_breakout_rel_vol: float = 2.0  # VPS_CRYPTO_BREAKOUT_REL_VOL（默认 = breakout_rel_vol）

    @classmethod
    def from_env(cls) -> "VPSConfig":
        return cls(
            eps=parse_env_float(os.getenv("VPS_PRICE_EPS"), 0.004, field_name="VPS_PRICE_EPS", minimum=0.0),
            vol_low=parse_env_float(os.getenv("VPS_VOL_LOW"), 0.7, field_name="VPS_VOL_LOW", minimum=0.0),
            vol_shrink=parse_env_float(os.getenv("VPS_VOL_SHRINK"), 0.8, field_name="VPS_VOL_SHRINK", minimum=0.0),
            vol_up=parse_env_float(os.getenv("VPS_VOL_UP"), 1.2, field_name="VPS_VOL_UP", minimum=0.0),
            vol_high=parse_env_float(os.getenv("VPS_VOL_HIGH"), 1.5, field_name="VPS_VOL_HIGH", minimum=0.0),
            swing_k=int(parse_env_float(os.getenv("VPS_SWING_K"), 3.0, field_name="VPS_SWING_K", minimum=1.0)),
            vol_ma_window=int(parse_env_float(os.getenv("VPS_VOL_MA_WINDOW"), 20.0, field_name="VPS_VOL_MA_WINDOW", minimum=5.0)),
            breakout_window=int(parse_env_float(os.getenv("VPS_BREAKOUT_WINDOW"), 20.0, field_name="VPS_BREAKOUT_WINDOW", minimum=2.0)),
            breakout_rel_vol=parse_env_float(os.getenv("VPS_BREAKOUT_REL_VOL"), 2.0, field_name="VPS_BREAKOUT_REL_VOL", minimum=1.0),
            pullback_rel_vol=parse_env_float(os.getenv("VPS_PULLBACK_REL_VOL"), 0.9, field_name="VPS_PULLBACK_REL_VOL", minimum=0.0),
            pullback_atr_mult=parse_env_float(os.getenv("VPS_PULLBACK_ATR_MULT"), 3.0, field_name="VPS_PULLBACK_ATR_MULT", minimum=0.5),
            atr_period=int(parse_env_float(os.getenv("VPS_ATR_PERIOD"), 14.0, field_name="VPS_ATR_PERIOD", minimum=2.0)),
            b_class_top_k=int(parse_env_float(os.getenv("VPS_B_CLASS_TOP_K"), 2.0, field_name="VPS_B_CLASS_TOP_K", minimum=1.0)),
            crypto_breakout_window=int(parse_env_float(os.getenv("VPS_CRYPTO_BREAKOUT_WINDOW"), 20.0, field_name="VPS_CRYPTO_BREAKOUT_WINDOW", minimum=2.0)),
            crypto_atr_period=int(parse_env_float(os.getenv("VPS_CRYPTO_ATR_PERIOD"), 14.0, field_name="VPS_CRYPTO_ATR_PERIOD", minimum=2.0)),
            crypto_breakout_rel_vol=parse_env_float(os.getenv("VPS_CRYPTO_BREAKOUT_REL_VOL"), 2.0, field_name="VPS_CRYPTO_BREAKOUT_REL_VOL", minimum=1.0),
        )

    @classmethod
    def for_market(cls, market: str | None) -> "VPSConfig":
        """返回适合指定市场的 VPSConfig 实例。

        crypto 市场：用 VPS_CRYPTO_* 值覆盖对应的主动计算字段（breakout_window、
        atr_period、breakout_rel_vol），其余字段保持 from_env() 默认。
        非 crypto（含 None、未知字符串）：直接返回 from_env()，行为字节一致。
        """
        base = cls.from_env()
        if market == "crypto":
            import dataclasses
            return dataclasses.replace(
                base,
                breakout_window=base.crypto_breakout_window,
                atr_period=base.crypto_atr_period,
                breakout_rel_vol=base.crypto_breakout_rel_vol,
            )
        return base


@dataclass(frozen=True)
class Pivot:
    index: int          # df 行号（已确认，滞后 k）
    timestamp: int      # epoch ms (Asia/Shanghai)
    price: float
    kind: str           # 'high' | 'low'


@dataclass(frozen=True)
class VPSignal:
    timestamp: int               # epoch ms (Asia/Shanghai)
    price: float
    anchor: str                  # 'low' | 'high' | 'close'
    direction: str               # 'bullish' | 'bearish' | 'neutral'
    signal_type: str
    confidence: str              # 'high' | 'medium' | 'low'
    is_daily_approx: bool
    is_anomalous: bool
    reason: str
    threshold: float | None
    observed_value: float | None


@dataclass(frozen=True)
class VPSResult:
    markers: list[VPSignal]
    status: str                  # 'ok' | 'degraded'
    degraded_reason: str | None


def _to_epoch_ms_shanghai(date_value) -> int:
    """日期/时间 → Asia/Shanghai 毫秒时间戳。

    纯日期(无时间分量)→ 当日午夜(日线语义不变);带时分秒(分钟 bar)→ 保留时分秒。
    用作信号触发对齐的 join 键:marker.timestamp 与 _last_ts(window) 两侧同函数同列值,
    绝对时区不影响相等性(分钟 bar 不再坍缩到午夜)。
    """
    has_time = False
    if isinstance(date_value, str):
        s = date_value.strip()
        if len(s) > 10 and ":" in s:
            try:
                dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
                has_time = True
            except ValueError:
                # 非空格分隔(如 ISO 'T'、带时区偏移)→ pandas 宽松解析保留时分;失败再退纯日期午夜
                ts = pd.to_datetime(s, errors="coerce")
                if ts is not None and not pd.isna(ts):
                    dt = ts.to_pydatetime()
                    has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
                else:
                    dt = datetime.strptime(s[:10], "%Y-%m-%d")
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
    elif isinstance(date_value, pd.Timestamp):
        dt = date_value.to_pydatetime()
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    elif isinstance(date_value, datetime):
        dt = date_value
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    else:
        dt = pd.Timestamp(date_value).to_pydatetime()
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    if dt.tzinfo is None:
        if has_time:
            dt = dt.replace(tzinfo=_SHANGHAI)
        else:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_SHANGHAI)
    return int(dt.timestamp() * 1000)


def _normalize(df, config: VPSConfig) -> tuple[pd.DataFrame, str | None]:
    """包裹 normalize_ohlcv：捕获 ValueError（缺列）、空数据返回 degraded reason。

    注意：窗口是否充足的检查在 compute_volume_price_signals 中执行，
    而非此处，以便 _compute_primitives 在小样本单元测试中仍可调用。
    """
    try:
        norm = normalize_ohlcv(df, required_columns=_REQUIRED_COLUMNS)
    except ValueError as exc:
        return pd.DataFrame(), str(exc)
    if norm.empty:
        return norm, "no closed daily data available"
    return norm, None


def _check_sufficient_window(norm: pd.DataFrame, config: VPSConfig) -> str | None:
    """检查 norm df 行数是否足以支撑所有滚动窗口计算；不足返回 degraded reason。"""
    min_bars = max(config.vol_ma_window, config.atr_period, config.breakout_window) + 1
    if len(norm) < min_bars:
        return f"insufficient window: need {min_bars} bars, got {len(norm)}"
    return None


def _compute_primitives(norm_df: pd.DataFrame, config: VPSConfig) -> pd.DataFrame:
    """在 norm_df 基础上追加量价派生列，全部采用 shift(1) 防未来函数。

    新增列：
    - spread: high - low
    - body: close - open
    - is_limit_bar: spread <= 0（一字板）
    - range_pos: (close - low) / spread，spread==0 时为 NaN
    - vol_ma: volume 的 vol_ma_window 滚动均值 shift(1)（不含当根）
    - rel_vol: volume / vol_ma，vol_ma<=0 或 NaN 时为 NaN
    - pct_chg: close 的 pct_change
    - ma5: close 的 5 日均值
    - ma20: close 的 20 日均值
    """
    prim = norm_df.copy()
    high = prim["high"].astype(float)
    low = prim["low"].astype(float)
    close = prim["close"].astype(float)
    open_ = prim["open"].astype(float)
    volume = prim["volume"].astype(float)

    spread = high - low
    prim["spread"] = spread
    prim["body"] = close - open_

    # 一字板：spread <= 0（涨跌停封板等）
    prim["is_limit_bar"] = (spread <= 0)

    # range_pos：spread==0 时不引入 eps 偏置，直接为 NaN（契约要求）
    range_pos = (close - low) / spread.where(spread > 0)
    prim["range_pos"] = range_pos

    # vol_ma 使用 shift(1)：t 时刻的量均不包含 t 自身的成交量
    vol_ma = volume.rolling(config.vol_ma_window).mean().shift(1)
    prim["vol_ma"] = vol_ma

    # rel_vol：vol_ma <= 0 或 NaN 时为 NaN（降权标记）
    rel_vol = volume / vol_ma.where(vol_ma > 0)
    prim["rel_vol"] = rel_vol

    prim["pct_chg"] = close.pct_change()
    prim["ma5"] = close.rolling(5).mean()
    prim["ma20"] = close.rolling(20).mean()
    return prim


def find_swing_pivots(series, k: int) -> list[Pivot]:
    """左右各 k 根严格确认的摆动高低点；天然滞后 k，不含未来函数。

    pivot at index i is confirmed only when bars i+1 … i+k all exist and
    are strictly lower (for high) / strictly higher (for low) than center.
    The loop range(k, n-k) guarantees right-side bars always exist before
    emitting a pivot — no lookahead.
    """
    values = pd.Series(series).astype(float).reset_index(drop=True)
    n = len(values)
    pivots: list[Pivot] = []
    if n < 2 * k + 1 or k < 1:
        return pivots
    for i in range(k, n - k):
        window = values.iloc[i - k:i + k + 1]
        center = values.iloc[i]
        left = window.iloc[:k]
        right = window.iloc[k + 1:]
        if center > left.max() and center > right.max():
            pivots.append(Pivot(index=i, timestamp=0, price=float(center), kind="high"))
        elif center < left.min() and center < right.min():
            pivots.append(Pivot(index=i, timestamp=0, price=float(center), kind="low"))
    return pivots


def _attach_pivot_timestamps(pivots: list[Pivot], norm_df: pd.DataFrame) -> list[Pivot]:
    """在拿到 norm_df 后将 find_swing_pivots 产出的 timestamp=0 回填为真实时间锚。"""
    return [
        Pivot(
            index=p.index,
            timestamp=_to_epoch_ms_shanghai(norm_df["date"].iloc[p.index]),
            price=p.price,
            kind=p.kind,
        )
        for p in pivots
    ]


def atr(df, period: int = 14) -> pd.Series:
    """Canonical Wilder ATR（全仓唯一定义，M2b 复用）。

    True Range 定义：
      TR[0] = high[0] - low[0]（首根无前收，仅用 H-L）
      TR[t] = max(high[t]-low[t], |high[t]-close[t-1]|, |low[t]-close[t-1]|)  for t >= 1

    Wilder 平滑：
      ATR[0 .. period-2] = NaN
      ATR[period-1]      = mean(TR[0 .. period-1])  （SMA seed）
      ATR[t]             = (ATR[t-1] * (period-1) + TR[t]) / period  for t >= period
    """
    frame = pd.DataFrame(df)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)

    # TR[0] = H-L（skipna 默认 True，使首行 NaN 分量被忽略，取 high-low）
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    # Wilder 平滑：SMA seed + 递推
    n = len(tr)
    atr_values = [float("nan")] * n
    if n >= period:
        # seed：前 period 根 TR 的均值
        atr_values[period - 1] = float(tr.iloc[:period].mean())
        # 递推
        for t in range(period, n):
            atr_values[t] = (atr_values[t - 1] * (period - 1) + float(tr.iloc[t])) / period

    return pd.Series(atr_values, index=tr.index)


# ---------------------------------------------------------------------------
# M2b-1: 价位反算器（entry / stop / target / risk_reward）
# ---------------------------------------------------------------------------

# 可配项（对应 .env.example 中的 VPS_ATR_MULT / VPS_RR_TARGET）
_DEFAULT_ATR_MULT = 1.5
_DEFAULT_RR_TARGET = 2.0
_PRICE_LEVEL_WINDOW = 20


@dataclass
class PriceLevels:
    """Back-calculated long-setup price levels (single authority, source=rule).

    entry        最贴近现价的支撑参考（MA20 与近20日低点中取较高的那个，且 <= 现价）
    stop         entry - atr_mult * ATR
    target       entry + rr_target * (entry - stop)
    risk_reward  (target - entry) / (entry - stop)，理论上等于 rr_target
    """

    entry: float | None
    stop: float | None
    target: float | None
    risk_reward: float | None


def _last_finite(series) -> float | None:
    """取 Series 末值并转 float；NaN / None / 空 Series 返回 None。"""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN guard（不依赖 math.isnan，兼容更广）
        return None
    return value


def derive_price_levels(
    df,
    *,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> PriceLevels:
    """Derive entry/stop/target/risk_reward from MA20 / 20-bar swing low / ATR.

    复用 M1 canonical atr()，与图上规则信号同源（同一 OHLCV DataFrame）。
    任何子项无法计算（窗口不足、ATR 为 NaN）时返回 None，不抛异常。

    公式：
      entry      = MA20 与 近20日低点中较高者（贴近现价的支撑）；需 <= 现价
      stop       = entry - atr_mult * ATR
      target     = entry + rr_target * (entry - stop)
      risk_reward = (target - entry) / (entry - stop)  [理论上 == rr_target]
    """
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return PriceLevels(entry=None, stop=None, target=None, risk_reward=None)

    close = df["close"].astype(float)
    current_price = _last_finite(close)

    # MA20：窗口不足时末值为 NaN，_last_finite 返回 None
    ma20 = _last_finite(close.rolling(_PRICE_LEVEL_WINDOW).mean())

    # 近20日最低点：需要 low 列且行数足够
    swing_low: float | None = None
    if "low" in df.columns and len(df) >= _PRICE_LEVEL_WINDOW:
        swing_low = _last_finite(df["low"].astype(float).rolling(_PRICE_LEVEL_WINDOW).min())

    # ATR：复用 M1 canonical atr()，窗口不足时末值为 NaN
    last_atr = _last_finite(atr(df))
    return _price_levels_scalar_core(
        ma20, swing_low, current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target
    )


# ---------------------------------------------------------------------------
# M2b-2: 价位合理性校验 + ATR 回退
# ---------------------------------------------------------------------------


def is_invalid_price_level(
    *,
    entry: float | None,
    stop: float | None,
    target: float | None,
    current_price: float | None,
) -> bool:
    """Risk-sanity check for back-calculated long levels (NEW, not the
    analyzer's internal _is_invalid_stop_loss closure).

    Returns True (invalid) when any of the following hold:
    - Any of entry/stop/target is None, non-finite, or <= 0
    - Monotonic ordering violated: stop < entry < target is not satisfied
    - entry > current_price (long entry cannot sit above current price)
    """
    for value in (entry, stop, target):
        if value is None or not math.isfinite(value) or value <= 0:
            return True
    # long-setup ordering: stop < entry < target
    if not (stop < entry < target):  # type: ignore[operator]
        return True
    # long entry must not sit above current price
    if current_price is not None and entry > current_price:  # type: ignore[operator]
        return True
    return False


def _fallback_atr_levels(
    current_price: float | None,
    last_atr: float | None,
    *,
    atr_mult: float,
    rr_target: float,
) -> PriceLevels:
    """ATR 回退：以 current_price 为 entry，ATR 重算 stop/target。

    ATR 不可用（None / <= 0）时 stop/target 置 None（隐藏价位线）。
    """
    if current_price is None or last_atr is None or last_atr <= 0:
        return PriceLevels(entry=current_price, stop=None, target=None, risk_reward=None)
    entry = current_price
    stop = entry - atr_mult * last_atr
    risk = entry - stop
    if risk <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    target = entry + rr_target * risk
    return PriceLevels(entry=entry, stop=stop, target=target, risk_reward=(target - entry) / risk)


# ---------------------------------------------------------------------------
# Task 3: 量价八法穷尽互斥查表
# 5 量档 × 3 价档 = 15 格，每格必有归类（信号或 neutral 兜底），无死区。
# ---------------------------------------------------------------------------

# 15-cell truth table: (vol_bucket, price_bucket) -> (signal_type, direction)
# 量增价升（up/high × up）须叠加 body>0 或 range_pos>0.5 才确认 bullish，
# 否则降级 neutral（"高开收阴放量" 派发陷阱）。
_VFX_TABLE: dict[tuple[str, str], tuple[str, str]] = {
    ("low",    "down"): ("vfx_shrink_down",  "bearish"),
    ("low",    "flat"): ("vfx_dry_flat",      "neutral"),
    ("low",    "up"):   ("vfx_shrink_up",     "bullish"),
    ("shrink", "down"): ("vfx_shrink_down",   "neutral"),
    ("shrink", "flat"): ("vfx_dry_flat",      "neutral"),
    ("shrink", "up"):   ("vfx_shrink_up",     "neutral"),
    ("normal", "down"): ("vfx_normal_down",   "neutral"),
    ("normal", "flat"): ("vfx_normal_flat",   "neutral"),
    ("normal", "up"):   ("vfx_normal_up",     "neutral"),
    ("up",     "down"): ("vfx_expand_down",   "bearish"),
    ("up",     "flat"): ("vfx_expand_flat",   "neutral"),
    ("up",     "up"):   ("vfx_expand_up",     "bullish"),
    ("high",   "down"): ("vfx_climax_down",   "bearish"),
    ("high",   "flat"): ("vfx_climax_flat",   "neutral"),
    ("high",   "up"):   ("vfx_climax_up",     "bullish"),
}

# 量增价升需要形态确认（body>0 或 range_pos>0.5），否则降级 neutral
_VFX_NEEDS_CONFIRM: frozenset[tuple[str, str]] = frozenset({
    ("up", "up"),
    ("high", "up"),
})


def _volume_bucket(rel_vol, config: VPSConfig) -> str | None:
    """将 rel_vol 映射到量档字符串（左闭右开，对称无缝）。

    None / NaN -> None（调用方负责处理异常路径）。
    边界：low<0.7 / shrink [0.7,0.8) / normal [0.8,1.2) / up [1.2,1.5) / high>=1.5
    """
    if rel_vol is None or (isinstance(rel_vol, float) and np.isnan(rel_vol)):
        return None
    if rel_vol < config.vol_low:
        return "low"
    if rel_vol < config.vol_shrink:
        return "shrink"
    if rel_vol < config.vol_up:
        return "normal"
    if rel_vol < config.vol_high:
        return "up"
    return "high"


def _price_bucket(pct_chg, config: VPSConfig) -> str | None:
    """将 pct_chg 映射到价档字符串。

    None / NaN -> None（调用方负责处理异常路径）。
    down: pct_chg < -eps / flat: |pct_chg| <= eps / up: pct_chg > eps
    """
    if pct_chg is None or (isinstance(pct_chg, float) and np.isnan(pct_chg)):
        return None
    if pct_chg < -config.eps:
        return "down"
    if pct_chg > config.eps:
        return "up"
    return "flat"


def classify_volume_pattern(rel_vol: float, pct_chg: float, atr_norm: float, config: VPSConfig) -> str:
    """量能形态分级：基于相对量比（rel_vol）、涨跌幅（pct_chg）和 ATR 归一化值（atr_norm）判定量能形态档位。

    返回值（穷尽互斥）：
    - 'climax_volume'    天量（rel_vol >= vol_high）且价格上涨
    - 'dry_up'           地量（rel_vol < vol_low），量能极度萎缩
    - 'shrink_pullback'  缩量回踩（rel_vol ∈ [vol_low, vol_shrink) 且 pct_chg < -eps
                         且 abs(pct_chg) <= _SHRINK_PULLBACK_ATR_K * atr_norm）
    - 'mild_expand'      温和放量（rel_vol ∈ [vol_up, vol_high) 且 pct_chg > eps）
    - 'normal'           常规（其余所有组合，含缩量但跌幅超出 ATR 边界的剧烈下跌）

    ATR 边界说明（shrink_pullback 核心守卫）：
    - _SHRINK_PULLBACK_ATR_K = 1.5：shrink_pullback 要求 abs(pct_chg) <= 1.5 * atr_norm。
      atr_norm = ATR / close，无量纲化后约等于该资产的"一个 ATR 当量的百分比跌幅"。
      若跌幅超过 1.5 倍 ATR 当量，表明是一根相对剧烈的下跌（非温和缩量回踩），
      此时 fall through 到 'normal'——atr_norm 由此成为决定性参数，而非仅供扩展。
    - k=1.5 选择依据：Wilder ATR 衡量"正常波动幅度"；1.5 倍是 1σ 波动的合理上界，
      既不会把正常日常小幅缩量下跌误排除，也能过滤暴跌误标缩量回踩。

    参数：
    - rel_vol:  当根成交量 / vol_ma，量比
    - pct_chg:  涨跌幅（小数，如 -0.02 = -2%）
    - atr_norm: ATR / close（无量纲化 ATR，与 pct_chg 同量纲可直接比较）
    - config:   VPSConfig，阈值全部从此读取，无魔法数字

    阈值来源（全部源自 VPSConfig 字段）：
    - vol_low    (default 0.7)  : 地量上界
    - vol_shrink (default 0.8)  : 缩量上界
    - vol_up     (default 1.2)  : 正常量上界
    - vol_high   (default 1.5)  : 放量下界 / 天量下界
    - eps        (default 0.004): 价格平坦判定半带宽
    """
    # ATR 守卫系数：shrink_pullback 要求 abs(pct_chg) <= k * atr_norm
    _SHRINK_PULLBACK_ATR_K = 1.5

    vbucket = _volume_bucket(rel_vol, config)
    pbucket = _price_bucket(pct_chg, config)

    # 天量（high vol）且上涨
    if vbucket == "high" and pbucket == "up":
        return "climax_volume"

    # 地量（low vol）：极度萎缩，无论价格方向
    if vbucket == "low":
        return "dry_up"

    # 缩量回踩（shrink vol + 下跌）且跌幅未超出 ATR 边界
    # abs(pct_chg) > k * atr_norm 表示剧烈下跌，fall through 到 normal
    if vbucket == "shrink" and pbucket == "down":
        if atr_norm > 0 and abs(pct_chg) <= _SHRINK_PULLBACK_ATR_K * atr_norm:
            return "shrink_pullback"
        # atr_norm <= 0（不可用）时保守地允许通过（向后兼容），跌幅过大则降为 normal

    # 温和放量（up vol + 上涨）
    if vbucket == "up" and pbucket == "up":
        return "mild_expand"

    # 其余所有组合：常规
    return "normal"


def _classify_vfx(
    *,
    rel_vol,
    pct_chg,
    body,
    range_pos,
    config: VPSConfig,
) -> VPSignal:
    """量价八法分类（纯函数）。

    穷尽且互斥：每个 (vol_bucket × price_bucket) 组合映射到唯一结果。
    rel_vol 为 None/NaN 或 pct_chg 为 None/NaN -> vfx_undefined neutral + is_anomalous。
    量增价升（up/high × up）需确认 body>0 或 range_pos>0.5，否则降级 neutral（派发陷阱）。
    """
    vbucket = _volume_bucket(rel_vol, config)
    pbucket = _price_bucket(pct_chg, config)

    # 异常路径：量比或涨跌幅不可用（vol_ma<=0/NaN 或一字板导致 pct_chg NaN）
    if vbucket is None or pbucket is None:
        return VPSignal(
            timestamp=0,
            price=0.0,
            anchor="close",
            direction="neutral",
            signal_type="vfx_undefined",
            confidence="low",
            is_daily_approx=True,
            is_anomalous=True,
            reason="量比或涨跌幅不可用（vol_ma<=0/NaN 或一字板）",
            threshold=None,
            observed_value=float(rel_vol) if rel_vol is not None else None,
        )

    # 查表（_VFX_TABLE 覆盖全部 15 格，不会 KeyError）
    signal_type, direction = _VFX_TABLE[(vbucket, pbucket)]

    # 量增价升确认规则：需 body>0 或 range_pos>0.5，否则降级 neutral（派发）
    if (vbucket, pbucket) in _VFX_NEEDS_CONFIRM:
        body_ok = body is not None and body > 0
        pos_ok = (
            range_pos is not None
            and not (isinstance(range_pos, float) and np.isnan(range_pos))
            and range_pos > 0.5
        )
        if not (body_ok or pos_ok):
            direction = "neutral"

    return VPSignal(
        timestamp=0,
        price=0.0,
        anchor="close",
        direction=direction,
        signal_type=signal_type,
        confidence="medium",
        is_daily_approx=True,
        is_anomalous=False,
        reason=f"量价八法：量档={vbucket}/价档={pbucket}",
        threshold=None,
        observed_value=float(rel_vol),
    )


def _detect_latest_vfx(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """最新 bar 量价八法落地：仅对当前 bar 出一个 B 类降权 vfx marker。

    只在分类为方向性（非 neutral、非 undefined/异常）时产出，避免每根 bar
    都挂一个低价值小点（不逐 bar 刷屏，仅surface 当前“识别一致/不一致”的量价态）。
    强制 confidence='low' + is_daily_approx=True ⇒ B 类降权：前端低透明度渲染，
    且不进 consistency 投票、不驱动 price_lines（consistency/price_lines 由收敛后的
    BuySignal / 反算器单独计算，与 markers 无关）。
    """
    if prim.empty:
        return []
    i = len(prim) - 1
    rel_vol = prim["rel_vol"].iloc[i]
    pct_chg = prim["pct_chg"].iloc[i]
    body = prim["body"].iloc[i]
    range_pos = prim["range_pos"].iloc[i]
    classified = _classify_vfx(
        rel_vol=rel_vol,
        pct_chg=pct_chg,
        body=body,
        range_pos=range_pos,
        config=config,
    )
    # 跳过 neutral / undefined（异常）：只 surface 方向性量价态
    if classified.direction == "neutral" or classified.is_anomalous:
        return []
    return [VPSignal(
        timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
        price=float(prim["close"].astype(float).iloc[i]),
        anchor="close",
        direction=classified.direction,
        signal_type=classified.signal_type,
        confidence="low",            # B 类降权硬上限
        is_daily_approx=True,        # 日线近似
        is_anomalous=False,
        reason=classified.reason,
        threshold=None,
        observed_value=classified.observed_value,
    )]


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume：方向 * 当日量的累积和。close.diff()==0 时贡献 0。"""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int) -> pd.Series:
    """Chaikin Money Flow：rolling(MFV之和) / rolling(volume之和)。
    high==low 时 MFM 无法计算，填 0（资金无方向），避免 NaN 传播到整窗口。
    rolling volume 为零时返回 NaN。
    """
    rng = (high - low).where((high - low) != 0)            # high==low → NaN
    mfm = ((close - low) - (high - close)) / rng           # Money Flow Multiplier
    mfv = mfm.fillna(0.0) * volume                         # Money Flow Volume
    roll_vol = volume.rolling(window).sum()
    return mfv.rolling(window).sum() / roll_vol.where(roll_vol != 0)


def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int) -> pd.Series:
    """Money Flow Index：基于典型价格与成交量的动量摆荡指标，范围 [0, 100]。

    标准定义：neg flow == 0 且 pos > 0（全上涨窗口）→ money_ratio → ∞ → MFI = 100.0。
    flat 窗口（pos == 0 且 neg == 0，无典型价格变化）→ MFI = NaN（流向未定义）。
    结果在 [0, 100] 内，不产生 inf / NaN（flat 窗口除外）。
    """
    tp = (high + low + close) / 3.0                        # typical price
    rmf = tp * volume                                      # raw money flow
    delta = tp.diff()
    pos = rmf.where(delta > 0, 0.0).rolling(window).sum()
    neg = rmf.where(delta < 0, 0.0).rolling(window).sum()
    mr = pos / neg.where(neg != 0)                         # neg==0 → NaN（暂用于计算）
    result = 100 - (100 / (1 + mr))
    # 全上涨窗口（neg==0 且 pos>0）：标准 MFI 定义为 100；flat 窗口（pos==0 且 neg==0）保留 NaN
    all_up = (neg == 0) & (pos > 0)
    result = result.where(~all_up, 100.0)
    return result


_DIV_CMF_WINDOW: int = 14   # CMF/MFI rolling 窗口（背离检测专用，与 VPSConfig 解耦）
_DIV_MFI_WINDOW: int = 14


def _divergence_strength_grade(sources_diverging: int, rel_divs: list[float]) -> str:
    """根据共振源数与平均归一化背离幅度映射强度档（weak / medium / strong）。

    rel_divs: 每个背离源的归一化背离量，各源已按自身量纲收敛到 [0, 1]：
      - OBV：|curr-prev| / max(|curr|, |prev|, 1)，有界相对量
      - CMF（[-1,1] 量纲）：|curr-prev| / 2，除以全域宽度
      - MFI（[0,100] 量纲）：|curr-prev| / 100
    规则：
      - k == 3 且平均幅度 > 0.15 → strong
      - k >= 2 且平均幅度 > 0.05 → medium
      - 其余                      → weak
    """
    avg = sum(rel_divs) / len(rel_divs) if rel_divs else 0.0
    if sources_diverging >= 3 and avg > 0.15:
        return "strong"
    if sources_diverging >= 2 and avg > 0.05:
        return "medium"
    return "weak"


def _detect_obv_divergence(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """多源量价背离检测（OBV + CMF + MFI）：仅对已确认 swing pivot 对比较，无未来函数。

    共振规则（emit-low 单源弱提示）：
      k == 3 → confidence="high"
      k == 2 → confidence="medium"
      k == 1 → confidence="low"（弱提示，不抑制）
      k == 0 → 不出 marker
    signal_type 保持 obv_top_divergence / obv_bottom_divergence（兼容 API / 看板契约）。
    reason 含强度档（weak/medium/strong），由背离幅度 × 源数映射。
    """
    close = prim["close"].astype(float).reset_index(drop=True)
    vol = prim["volume"].astype(float).reset_index(drop=True)
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)

    obv_series = _obv(close, vol)
    cmf_series = _cmf(high, low, close, vol, _DIV_CMF_WINDOW)
    mfi_series = _mfi(high, low, close, vol, _DIV_MFI_WINDOW)

    pivots = _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
    out: list[VPSignal] = []

    for kind, sig_type, cmp_price, cmp_ind, direction in (
        ("high", "obv_top_divergence",    lambda a, b: a > b, lambda a, b: a <= b, "bearish"),
        ("low",  "obv_bottom_divergence", lambda a, b: a < b, lambda a, b: a >= b, "bullish"),
    ):
        same = [p for p in pivots if p.kind == kind]
        for prev, curr in zip(same, same[1:]):
            if not cmp_price(curr.price, prev.price):
                continue

            # 各源在枢轴处的值（NaN 安全：NaN 不计入共振）
            obv_prev = float(obv_series.iloc[prev.index])
            obv_curr = float(obv_series.iloc[curr.index])
            cmf_prev = float(cmf_series.iloc[prev.index])
            cmf_curr = float(cmf_series.iloc[curr.index])
            mfi_prev = float(mfi_series.iloc[prev.index])
            mfi_curr = float(mfi_series.iloc[curr.index])

            obv_div  = cmp_ind(obv_curr, obv_prev) and not math.isnan(obv_curr) and not math.isnan(obv_prev)
            cmf_div  = cmp_ind(cmf_curr, cmf_prev) and not math.isnan(cmf_curr) and not math.isnan(cmf_prev)
            mfi_div  = cmp_ind(mfi_curr, mfi_prev) and not math.isnan(mfi_curr) and not math.isnan(mfi_prev)

            k = sum([obv_div, cmf_div, mfi_div])
            if k == 0:
                continue

            # 置信度：源数越多越高
            if k >= 3:
                confidence = "high"
            elif k >= 2:
                confidence = "medium"
            else:
                confidence = "low"   # 单源弱提示（emit-low 规则）

            # 强度档：按各源量纲归一化背离幅度 × 源数映射
            # OBV：无界累积量 → 有界相对量 |curr-prev|/max(|curr|,|prev|,1) ∈ [0,1]
            # CMF：[-1,1] 量纲 → |curr-prev|/2 ∈ [0,1]
            # MFI：[0,100] 量纲 → |curr-prev|/100 ∈ [0,1]
            rel_divs: list[float] = []
            if obv_div:
                denom_obv = max(abs(obv_curr), abs(obv_prev), 1.0)
                rel_divs.append(min(abs(obv_curr - obv_prev) / denom_obv, 1.0))
            if cmf_div:
                rel_divs.append(min(abs(cmf_curr - cmf_prev) / 2.0, 1.0))
            if mfi_div:
                rel_divs.append(min(abs(mfi_curr - mfi_prev) / 100.0, 1.0))
            grade = _divergence_strength_grade(k, rel_divs)

            sources_desc = "+".join(
                s for s, d in [("OBV", obv_div), ("CMF", cmf_div), ("MFI", mfi_div)] if d
            )
            # x 锚定确认 bar(curr.index+swing_k，背离可知日)，y 锚定枢轴极值——消除 k 根可视前视。
            conf_idx = curr.index + config.swing_k
            out.append(VPSignal(
                timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[conf_idx]),
                price=curr.price,
                anchor=kind,
                direction=direction,
                signal_type=sig_type,
                confidence=confidence,
                is_daily_approx=True,
                is_anomalous=False,
                reason=f"价格创新极值但量能指标未同步（{sources_desc} 背离，强度:{grade}）",
                threshold=float(obv_prev),
                observed_value=float(obv_curr),
            ))
    return out


def _detect_breakouts(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """放量突破检测：close >= 过去 N 日 high 最大值（shift(1) 不含当日）且 rel_vol >= 阈值。

    ATR 预计算优化：atr(prim, config.atr_period) 在循环外统一计算一次（O(n)），
    循环内通过 atr_series.iloc[i] 取当 bar 的 ATR 值。
    Wilder ATR 的递推定义保证：atr_series.iloc[i] == atr(prim.iloc[:i+1], period).iloc[-1]，
    两者语义完全等价，与原逐次切片计算结果一致。
    """
    high = prim["high"].astype(float)
    close = prim["close"].astype(float)
    # shift(1): prior max excludes current bar — no self-reference
    prior_max = high.rolling(config.breakout_window).max().shift(1)
    rel_vol = prim["rel_vol"]
    # 预计算完整 ATR 序列，避免循环内 O(n²) 逐次切片重算
    atr_series = atr(prim, config.atr_period)
    out: list[VPSignal] = []
    for i in range(len(prim)):
        pm = prior_max.iloc[i]
        rv = rel_vol.iloc[i]
        if pd.isna(pm) or pd.isna(rv):
            continue
        if close.iloc[i] >= pm and rv >= config.breakout_rel_vol:
            pct = float(prim["pct_chg"].iloc[i]) if not pd.isna(prim["pct_chg"].iloc[i]) else 0.0
            atr_val = _last_finite(atr_series.iloc[:i + 1])
            atr_norm = (atr_val / float(close.iloc[i])) if (atr_val is not None and float(close.iloc[i]) > 0) else 0.0
            vol_pattern = classify_volume_pattern(float(rv), pct, atr_norm, config)
            out.append(VPSignal(
                timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
                price=float(close.iloc[i]),
                anchor="close",
                direction="bullish",
                signal_type="volume_breakout",
                confidence="high",
                is_daily_approx=True,
                is_anomalous=False,
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）[量能形态:{vol_pattern}]",
                threshold=float(pm),
                observed_value=float(rv),
            ))
    return out


def _detect_shrink_pullback(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """缩量回调检测：上升趋势（ma5>ma20），最近确认 swing high 后缩量回调且幅度 < ATR 倍数。"""
    close = prim["close"].astype(float).reset_index(drop=True)
    ma5 = prim["ma5"]
    ma20 = prim["ma20"]
    rel_vol = prim["rel_vol"]
    atr_series = atr(prim, config.atr_period).reset_index(drop=True)
    highs = [
        p for p in _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
        if p.kind == "high"
    ]
    out: list[VPSignal] = []
    if not highs:
        return out
    last_high = highs[-1]
    i = len(prim) - 1
    if pd.isna(ma5.iloc[i]) or pd.isna(ma20.iloc[i]) or ma5.iloc[i] <= ma20.iloc[i]:
        return out  # 仅上升趋势
    drawdown = last_high.price - float(close.iloc[i])
    seg_rel = rel_vol.iloc[last_high.index + 1:i + 1].dropna()
    atr_now = float(atr_series.iloc[i])
    if (
        drawdown > 0
        and not seg_rel.empty
        and (seg_rel < config.pullback_rel_vol).all()
        and not np.isnan(atr_now)
        and drawdown < config.pullback_atr_mult * atr_now
    ):
        # 获取最新 bar 的量能形态档，注入 reason 语义
        curr_rv = float(rel_vol.iloc[i]) if not pd.isna(rel_vol.iloc[i]) else float("nan")
        curr_pct = float(prim["pct_chg"].iloc[i]) if not pd.isna(prim["pct_chg"].iloc[i]) else 0.0
        close_now = float(close.iloc[i])
        atr_norm_now = (atr_now / close_now) if close_now > 0 else 0.0
        if not math.isnan(curr_rv):
            vol_pattern = classify_volume_pattern(curr_rv, curr_pct, atr_norm_now, config)
            reason_str = f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）[量能形态:{vol_pattern}]"
        else:
            reason_str = f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）"
        out.append(VPSignal(
            timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
            price=float(close.iloc[i]),
            anchor="close",
            direction="bullish",
            signal_type="shrink_pullback",
            confidence="medium",
            is_daily_approx=True,
            is_anomalous=False,
            reason=reason_str,
            threshold=config.pullback_atr_mult * atr_now,
            observed_value=drawdown,
        ))
    return out


def _avwap_signal(prim: pd.DataFrame, i: int, sig_type: str, direction: str, level: float) -> VPSignal:
    return VPSignal(
        timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
        price=float(prim["close"].astype(float).iloc[i]),
        anchor="close",
        direction=direction,
        signal_type=sig_type,
        confidence="medium",
        is_daily_approx=True,
        is_anomalous=False,
        reason="锚定突破日 AVWAP 的重夺/失守",
        threshold=float(level),
        observed_value=float(prim["close"].astype(float).iloc[i]),
    )


def _anchored_vwap_signals(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """锚定 VWAP 信号：仅锚已确认放量突破日；重夺/失守按 edge-cross 当根触发。"""
    breakouts = _detect_breakouts(prim, config)
    if not breakouts:
        return []
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    volume = prim["volume"].astype(float).reset_index(drop=True)
    typical = (high + low + close) / 3.0
    ts = [_to_epoch_ms_shanghai(prim["date"].iloc[i]) for i in range(len(prim))]
    out: list[VPSignal] = []
    for b in breakouts:
        start = ts.index(b.timestamp)
        cum_pv = 0.0
        cum_v = 0.0
        vwap: list[float] = []
        for j in range(start, len(prim)):
            cum_pv += float(typical.iloc[j]) * float(volume.iloc[j])
            cum_v += float(volume.iloc[j])
            vwap.append(cum_pv / cum_v if cum_v > 0 else float("nan"))
        for off in range(1, len(vwap)):
            j = start + off
            prev_delta = float(close.iloc[j - 1]) - vwap[off - 1]
            curr_delta = float(close.iloc[j]) - vwap[off]
            if prev_delta < 0 <= curr_delta:
                out.append(_avwap_signal(prim, j, "anchored_vwap_reclaim", "bullish", vwap[off]))
            elif prev_delta >= 0 > curr_delta:
                out.append(_avwap_signal(prim, j, "anchored_vwap_loss", "bearish", vwap[off]))
    return out


def _detect_vsa_bars(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """VSA 单 bar 形态检测：No Demand / No Supply / Stopping-Climactic / Effort-vs-Result。

    全部输出 confidence='low'、is_daily_approx=True（B 类降权契约）。
    一字板（is_limit_bar）和量比缺失（rel_vol NaN）均跳过。
    """
    out: list[VPSignal] = []
    for i in range(len(prim)):
        rv = prim["rel_vol"].iloc[i]
        rp = prim["range_pos"].iloc[i]
        body = prim["body"].iloc[i]
        if pd.isna(rv) or bool(prim["is_limit_bar"].iloc[i]):
            continue  # 一字板/量比缺失 -> 排除
        ts = _to_epoch_ms_shanghai(prim["date"].iloc[i])
        price = float(prim["close"].astype(float).iloc[i])
        # No Demand：缩量上涨、收在上半区无力 -> 看空意味
        if rv < config.vol_shrink and body > 0 and not pd.isna(rp) and rp < 0.5:
            out.append(_vsa_signal(ts, price, "vsa_no_demand", "bearish", rv))
        # No Supply：缩量下跌、收在下半区无量承接 -> 看多意味
        elif rv < config.vol_shrink and body < 0 and not pd.isna(rp) and rp > 0.5:
            out.append(_vsa_signal(ts, price, "vsa_no_supply", "bullish", rv))
        # Stopping/Climactic：高量大幅波动后收回中部
        elif rv >= config.vol_high and not pd.isna(rp) and 0.3 <= rp <= 0.7:
            out.append(_vsa_signal(ts, price, "vsa_stopping", "neutral", rv))
        # Effort vs Result：高量但实体极小（努力无果）
        elif rv >= config.vol_high and abs(body) < (prim["spread"].iloc[i] * 0.2):
            out.append(_vsa_signal(ts, price, "vsa_effort_vs_result", "neutral", rv))
    return out


def _detect_vsa_bars_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """_detect_vsa_bars 的快速变体:hoist close.astype 出循环(消除 O(n²)),返回 (行号, marker)。
    与 _detect_vsa_bars 逐条等价。"""
    close = prim["close"].astype(float).reset_index(drop=True)
    rv_s = prim["rel_vol"].reset_index(drop=True)
    rp_s = prim["range_pos"].reset_index(drop=True)
    body_s = prim["body"].reset_index(drop=True)
    spread_s = prim["spread"].reset_index(drop=True)
    limit_s = prim["is_limit_bar"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    out: list[tuple[int, VPSignal]] = []
    for i in range(len(prim)):
        rv = rv_s.iloc[i]; rp = rp_s.iloc[i]; body = body_s.iloc[i]
        if pd.isna(rv) or bool(limit_s.iloc[i]):
            continue
        ts = _to_epoch_ms_shanghai(date_s.iloc[i])
        price = float(close.iloc[i])
        if rv < config.vol_shrink and body > 0 and not pd.isna(rp) and rp < 0.5:
            out.append((i, _vsa_signal(ts, price, "vsa_no_demand", "bearish", rv)))
        elif rv < config.vol_shrink and body < 0 and not pd.isna(rp) and rp > 0.5:
            out.append((i, _vsa_signal(ts, price, "vsa_no_supply", "bullish", rv)))
        elif rv >= config.vol_high and not pd.isna(rp) and 0.3 <= rp <= 0.7:
            out.append((i, _vsa_signal(ts, price, "vsa_stopping", "neutral", rv)))
        elif rv >= config.vol_high and abs(body) < (spread_s.iloc[i] * 0.2):
            out.append((i, _vsa_signal(ts, price, "vsa_effort_vs_result", "neutral", rv)))
    return out


def _vsa_signal(ts: int, price: float, sig_type: str, direction: str, rv: float) -> VPSignal:
    """构造 VSA B 类信号（降权标记）。"""
    return VPSignal(
        timestamp=ts, price=price, anchor="close", direction=direction,
        signal_type=sig_type, confidence="low", is_daily_approx=True,
        is_anomalous=False, reason="VSA 单 bar 形态（日线近似，低置信）",
        threshold=None, observed_value=float(rv),
    )


def _detect_upthrust_spring(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """Upthrust（假突破顶）与 Spring（假跌破底）检测。

    复用 find_swing_pivots 确认摆动点（左右各 k 根，天然滞后 k），不含未来函数。
    对每根 bar，只参照已确认的（index < i）前序 pivot，保证无前视偏差。
    输出 confidence='low'、is_daily_approx=True（B 类降权契约）。
    """
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    all_pivots = _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
    highs = [p for p in all_pivots if p.kind == "high"]
    lows = [p for p in all_pivots if p.kind == "low"]
    out: list[VPSignal] = []
    for i in range(len(prim)):
        ts = _to_epoch_ms_shanghai(prim["date"].iloc[i])
        prior_highs = [p for p in highs if p.index < i]
        prior_lows = [p for p in lows if p.index < i]
        if prior_highs and high.iloc[i] > prior_highs[-1].price and close.iloc[i] < prior_highs[-1].price:
            out.append(VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="high", direction="bearish",
                signal_type="upthrust", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假突破顶（Upthrust，日线近似）", threshold=float(prior_highs[-1].price),
                observed_value=float(high.iloc[i]),
            ))
        if prior_lows and low.iloc[i] < prior_lows[-1].price and close.iloc[i] > prior_lows[-1].price:
            out.append(VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="low", direction="bullish",
                signal_type="spring", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假跌破底（Spring，日线近似）", threshold=float(prior_lows[-1].price),
                observed_value=float(low.iloc[i]),
            ))
    return out


def _detect_upthrust_spring_causal_rows(
    prim: pd.DataFrame, config: VPSConfig
) -> list[tuple[int, VPSignal]]:
    """upthrust/spring 因果变体:对每根 i,仅用确认索引 center+swing_k ≤ i 的最近 pivot。
    单调指针沿 pivot(已按 center 升序)推进,O(n+p)。等价于原 _detect_upthrust_spring(prim[:i+1]) 末根。"""
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    k = config.swing_k
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, k), prim)
    highs = [p for p in pivots if p.kind == "high"]   # center 升序
    lows = [p for p in pivots if p.kind == "low"]
    out: list[tuple[int, VPSignal]] = []
    hi_ptr = 0; last_high = None
    lo_ptr = 0; last_low = None
    for i in range(len(prim)):
        while hi_ptr < len(highs) and highs[hi_ptr].index + k <= i:
            last_high = highs[hi_ptr]; hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr].index + k <= i:
            last_low = lows[lo_ptr]; lo_ptr += 1
        ts = _to_epoch_ms_shanghai(date_s.iloc[i])
        if last_high is not None and high.iloc[i] > last_high.price and close.iloc[i] < last_high.price:
            out.append((i, VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="high", direction="bearish",
                signal_type="upthrust", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假突破顶（Upthrust，日线近似）", threshold=float(last_high.price),
                observed_value=float(high.iloc[i]))))
        if last_low is not None and low.iloc[i] < last_low.price and close.iloc[i] > last_low.price:
            out.append((i, VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="low", direction="bullish",
                signal_type="spring", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假跌破底（Spring，日线近似）", threshold=float(last_low.price),
                observed_value=float(low.iloc[i]))))
    return out


def _limit_b_class(b_markers: list[VPSignal], config: VPSConfig) -> list[VPSignal]:
    """按 observed_value 绝对值降序取 top-k，控制 B 类信号密度上限。"""
    ranked = sorted(
        b_markers,
        key=lambda m: abs(m.observed_value) if m.observed_value is not None else 0.0,
        reverse=True,
    )
    return ranked[:config.b_class_top_k]


def apply_price_levels_to_guard(
    result,
    df,
    *,
    fundamental_context=None,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> PriceLevels:
    """Derive price levels, write support/resistance/current_price into
    result.dashboard['data_perspective']['price_position'], then call the
    EXISTING stabilize_decision_with_structure guard. Returns the validated
    PriceLevels (single price-line authority, source=rule)."""
    levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)

    if result is not None and df is not None and "close" in getattr(df, "columns", []):
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        result.dashboard = dashboard
        dp = dashboard.get("data_perspective")
        if not isinstance(dp, dict):
            dp = {}
            dashboard["data_perspective"] = dp
        pp = dp.get("price_position")
        if not isinstance(pp, dict):
            pp = {}
            dp["price_position"] = pp

        current_price = _last_finite(df["close"].astype(float))
        if current_price is not None:
            pp.setdefault("current_price", current_price)
        # stop is the structural support the 文案护栏 reads; target the resistance.
        if levels.stop is not None:
            pp.setdefault("support_level", levels.stop)
        if levels.target is not None:
            pp.setdefault("resistance_level", levels.target)

    # Delayed import to avoid analyzer<->service top-level circular import.
    from src.analyzer import stabilize_decision_with_structure

    stabilize_decision_with_structure(
        result, trend_result=None, fundamental_context=fundamental_context
    )
    return levels


def compute_volume_price_signals(df, *, config: VPSConfig | None = None) -> VPSResult:
    """主入口：输入 OHLCV DataFrame，输出 VPSResult，串联 A 类量价信号。"""
    cfg = config or VPSConfig()
    norm, reason = _normalize(df, cfg)
    if reason is not None:
        return VPSResult(markers=[], status="degraded", degraded_reason=reason)
    window_reason = _check_sufficient_window(norm, cfg)
    if window_reason is not None:
        return VPSResult(markers=[], status="degraded", degraded_reason=window_reason)
    prim = _compute_primitives(norm, cfg)

    degraded_reason = None
    if prim["rel_vol"].isna().all():
        degraded_reason = "rel_vol unavailable for all bars (vol_ma<=0/NaN)"

    markers: list[VPSignal] = []
    markers.extend(_detect_obv_divergence(prim, cfg))
    markers.extend(_detect_breakouts(prim, cfg))
    markers.extend(_detect_shrink_pullback(prim, cfg))
    markers.extend(_anchored_vwap_signals(prim, cfg))

    # B 类信号追加（A 类组装完毕后接入），top-k 限流防止图表过密
    b_markers = _detect_vsa_bars(prim, cfg) + _detect_upthrust_spring(prim, cfg)
    markers.extend(_limit_b_class(b_markers, cfg))

    # 量价八法最新 bar 单点（B 类降权）：恰好 1 个 marker，不受 VSA top-k 限流约束
    markers.extend(_detect_latest_vfx(prim, cfg))

    status = "degraded" if degraded_reason else "ok"
    return VPSResult(markers=markers, status=status, degraded_reason=degraded_reason)


# ---------------------------------------------------------------------------
# Task 2: scalar core + 序列版价位(向量化基础)
# ---------------------------------------------------------------------------


def _scalar_or_none(value) -> "float | None":
    """单值取 float;NaN/None/不可转 → None(镜像 _last_finite 的标量语义)。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _price_levels_scalar_core(
    ma20: "float | None",
    swing_low: "float | None",
    current_price: "float | None",
    last_atr: "float | None",
    *,
    atr_mult: float,
    rr_target: float,
) -> PriceLevels:
    """derive_price_levels 的标量内核(逐 bar 复用);entry/回退分支与原实现字节一致。"""
    entry_candidates = [c for c in (ma20, swing_low) if c is not None]
    if current_price is not None:
        below = [c for c in entry_candidates if c <= current_price]
        entry = max(below) if below else (min(entry_candidates) if entry_candidates else None)
    else:
        entry = max(entry_candidates) if entry_candidates else None

    if entry is None or last_atr is None or last_atr <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    stop = entry - atr_mult * last_atr
    risk = entry - stop
    if risk <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    target = entry + rr_target * risk
    risk_reward = (target - entry) / risk
    candidate = PriceLevels(entry=entry, stop=stop, target=target, risk_reward=risk_reward)
    if is_invalid_price_level(entry=candidate.entry, stop=candidate.stop,
                              target=candidate.target, current_price=current_price):
        return _fallback_atr_levels(current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target)
    return candidate


def derive_price_levels_series(
    df,
    *,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> "list[PriceLevels]":
    """derive_price_levels 的全 df 序列版(raw 空间,逐 bar O(1) 取值)。

    与 derive_price_levels(df.iloc[:t+1]) 逐 bar 等价:rolling/atr 因果且位置稳定,
    series.iloc[t] == _last_finite(window[:t+1] 对应序列)。硬编码 20/14,不接 config。
    """
    n = len(df) if df is not None else 0
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return [PriceLevels(entry=None, stop=None, target=None, risk_reward=None) for _ in range(n)]
    close = df["close"].astype(float).reset_index(drop=True)
    ma20_series = close.rolling(_PRICE_LEVEL_WINDOW).mean()
    swing_series = (
        df["low"].astype(float).reset_index(drop=True).rolling(_PRICE_LEVEL_WINDOW).min()
        if "low" in df.columns else None
    )
    atr_arr = atr(df).reset_index(drop=True)   # 默认 period=14,与 config 解耦
    out: "list[PriceLevels]" = []
    for t in range(n):
        current_price = _scalar_or_none(close.iloc[t])
        ma20 = _scalar_or_none(ma20_series.iloc[t])
        swing_low = None
        if swing_series is not None and (t + 1) >= _PRICE_LEVEL_WINDOW:
            swing_low = _scalar_or_none(swing_series.iloc[t])
        last_atr = _scalar_or_none(atr_arr.iloc[t])
        out.append(_price_levels_scalar_core(
            ma20, swing_low, current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target))
    return out


# ---------------------------------------------------------------------------
# Task 5: _detect_shrink_pullback_causal_rows(last-bar → per-bar + O(1) 段增量)
# ---------------------------------------------------------------------------


def _detect_shrink_pullback_causal_rows(
    prim: pd.DataFrame, config: VPSConfig
) -> list[tuple[int, VPSignal]]:
    """逐 bar 因果变体:bar i 的输出 == _detect_shrink_pullback(prim[:i+1]) 的末根判定。

    段内全低于阈值检查使用前缀增量 O(1):
      - nonnan_prefix[j]:prim[0:j] 中非 NaN rel_vol 的累计个数
      - last_violation_prefix[j]:prim[0:j+1] 中最近一个 rel_vol>=pullback_rel_vol 的行号(-1=无)
    段 [anchor, i] 非空且无违例 ⟺ seg_nonnan>0 且 last_violation_prefix[i] < anchor
    其中 anchor = last_high.index + 1。
    """
    close = prim["close"].astype(float).reset_index(drop=True)
    ma5 = prim["ma5"].reset_index(drop=True)
    ma20 = prim["ma20"].reset_index(drop=True)
    rel_vol = prim["rel_vol"].reset_index(drop=True)
    pct = prim["pct_chg"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    atr_series = atr(prim, config.atr_period).reset_index(drop=True)
    k = config.swing_k
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, k), prim)
    highs = [p for p in pivots if p.kind == "high"]
    out: list[tuple[int, VPSignal]] = []
    hi_ptr = 0
    last_high = None
    # 前缀:nonnan 累计计数 + 最近违例行号
    nonnan_prefix = [0] * (len(prim) + 1)
    last_violation_idx = -1
    last_violation_prefix = [-1] * len(prim)
    for j in range(len(prim)):
        rv = rel_vol.iloc[j]
        nonnan_prefix[j + 1] = nonnan_prefix[j] + (0 if pd.isna(rv) else 1)
        if (not pd.isna(rv)) and rv >= config.pullback_rel_vol:
            last_violation_idx = j
        last_violation_prefix[j] = last_violation_idx
    for i in range(len(prim)):
        while hi_ptr < len(highs) and highs[hi_ptr].index + k <= i:
            last_high = highs[hi_ptr]
            hi_ptr += 1
        if last_high is None:
            continue
        if pd.isna(ma5.iloc[i]) or pd.isna(ma20.iloc[i]) or ma5.iloc[i] <= ma20.iloc[i]:
            continue
        drawdown = last_high.price - float(close.iloc[i])
        anchor = last_high.index + 1          # 段 [anchor, i]
        if anchor > i:
            continue
        seg_nonnan = nonnan_prefix[i + 1] - nonnan_prefix[anchor]
        seg_ok = (seg_nonnan > 0) and (last_violation_prefix[i] < anchor)
        atr_now = float(atr_series.iloc[i])
        if (drawdown > 0 and seg_ok and not np.isnan(atr_now)
                and drawdown < config.pullback_atr_mult * atr_now):
            curr_rv = float(rel_vol.iloc[i]) if not pd.isna(rel_vol.iloc[i]) else float("nan")
            curr_pct = float(pct.iloc[i]) if not pd.isna(pct.iloc[i]) else 0.0
            close_now = float(close.iloc[i])
            atr_norm_now = (atr_now / close_now) if close_now > 0 else 0.0
            if not math.isnan(curr_rv):
                vp = classify_volume_pattern(curr_rv, curr_pct, atr_norm_now, config)
                reason_str = (
                    f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）[量能形态:{vp}]"
                )
            else:
                reason_str = f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）"
            out.append((i, VPSignal(
                timestamp=_to_epoch_ms_shanghai(date_s.iloc[i]),
                price=float(close.iloc[i]),
                anchor="close",
                direction="bullish",
                signal_type="shrink_pullback",
                confidence="medium",
                is_daily_approx=True,
                is_anomalous=False,
                reason=reason_str,
                threshold=config.pullback_atr_mult * atr_now,
                observed_value=drawdown,
            )))
    return out


# ---------------------------------------------------------------------------
# Task 6: _detect_vfx_all_bars_rows(vfx 全 bar 变体,复刻 _detect_latest_vfx 字段覆写)
# ---------------------------------------------------------------------------


def _detect_vfx_all_bars_rows(
    prim: pd.DataFrame, config: VPSConfig
) -> list[tuple[int, VPSignal]]:
    """vfx 全 bar 变体:每根用当根 causal primitive 跑 _classify_vfx,复刻 _detect_latest_vfx 字段覆写。
    与逐窗 _detect_latest_vfx(prim[:i+1]) 逐根等价。neutral/anomalous 跳过。"""
    if prim.empty:
        return []
    close = prim["close"].astype(float).reset_index(drop=True)
    rv_s = prim["rel_vol"].reset_index(drop=True)
    pct_s = prim["pct_chg"].reset_index(drop=True)
    body_s = prim["body"].reset_index(drop=True)
    rp_s = prim["range_pos"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    out: list[tuple[int, VPSignal]] = []
    for i in range(len(prim)):
        classified = _classify_vfx(
            rel_vol=rv_s.iloc[i], pct_chg=pct_s.iloc[i],
            body=body_s.iloc[i], range_pos=rp_s.iloc[i], config=config)
        if classified.direction == "neutral" or classified.is_anomalous:
            continue
        out.append((i, VPSignal(
            timestamp=_to_epoch_ms_shanghai(date_s.iloc[i]), price=float(close.iloc[i]),
            anchor="close", direction=classified.direction, signal_type=classified.signal_type,
            confidence="low", is_daily_approx=True, is_anomalous=False,
            reason=classified.reason, threshold=None, observed_value=classified.observed_value)))
    return out
