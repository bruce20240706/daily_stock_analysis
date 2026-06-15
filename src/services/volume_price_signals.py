# -*- coding: utf-8 -*-
"""量价信号引擎（M1）：纯函数，输入 OHLCV DataFrame，输出 VPSResult。

设计要点：
- 所有滚动量基元统一 shift(1)，防未来函数。
- swing pivot 左右各 k 根确认，天然滞后 k，OBV 背离 / VSA 高低点全部复用。
- 八法为穷尽且互斥的二维查表，每格必有归类（信号或 neutral 兜底）。
- B 类（VSA/Upthrust/Spring）强制降权：不进 consistency 投票、不驱动 price_lines、置信 <= low、top-k 限流。
"""

from __future__ import annotations

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
    b_class_confidence: str = "low"     # B 类置信硬上限

    @classmethod
    def from_env(cls) -> "VPSConfig":
        return cls(
            eps=parse_env_float(os.getenv("VPS_PRICE_EPS"), 0.004, field_name="VPS_PRICE_EPS", minimum=0.0),
            vol_low=parse_env_float(os.getenv("VPS_VOL_LOW"), 0.7, field_name="VPS_VOL_LOW", minimum=0.0),
            vol_shrink=parse_env_float(os.getenv("VPS_VOL_SHRINK"), 0.8, field_name="VPS_VOL_SHRINK", minimum=0.0),
            vol_up=parse_env_float(os.getenv("VPS_VOL_UP"), 1.2, field_name="VPS_VOL_UP", minimum=0.0),
            vol_high=parse_env_float(os.getenv("VPS_VOL_HIGH"), 1.5, field_name="VPS_VOL_HIGH", minimum=0.0),
            swing_k=int(parse_env_float(os.getenv("VPS_SWING_K"), 3.0, field_name="VPS_SWING_K", minimum=1.0)),
            breakout_window=int(parse_env_float(os.getenv("VPS_BREAKOUT_WINDOW"), 20.0, field_name="VPS_BREAKOUT_WINDOW", minimum=2.0)),
            breakout_rel_vol=parse_env_float(os.getenv("VPS_BREAKOUT_REL_VOL"), 2.0, field_name="VPS_BREAKOUT_REL_VOL", minimum=1.0),
        )


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
    """将日期值转换为 Asia/Shanghai 午夜的毫秒时间戳。"""
    if isinstance(date_value, str):
        dt = datetime.strptime(date_value[:10], "%Y-%m-%d")
    elif isinstance(date_value, pd.Timestamp):
        dt = date_value.to_pydatetime()
    elif isinstance(date_value, datetime):
        dt = date_value
    else:
        dt = pd.Timestamp(date_value).to_pydatetime()
    if dt.tzinfo is None:
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


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume：方向 * 当日量的累积和。close.diff()==0 时贡献 0。"""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def _detect_obv_divergence(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """OBV 顶底背离检测：仅对已确认 swing pivot 对比较，无未来函数。"""
    close = prim["close"].astype(float).reset_index(drop=True)
    obv_series = _obv(close, prim["volume"].astype(float).reset_index(drop=True))
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
    out: list[VPSignal] = []
    for kind, sig_type, cmp_price, cmp_obv, direction in (
        ("high", "obv_top_divergence",    lambda a, b: a > b, lambda a, b: a <= b, "bearish"),
        ("low",  "obv_bottom_divergence", lambda a, b: a < b, lambda a, b: a >= b, "bullish"),
    ):
        same = [p for p in pivots if p.kind == kind]
        for prev, curr in zip(same, same[1:]):
            price_extreme = cmp_price(curr.price, prev.price)
            obv_lagging = cmp_obv(float(obv_series.iloc[curr.index]), float(obv_series.iloc[prev.index]))
            if price_extreme and obv_lagging:
                out.append(VPSignal(
                    timestamp=curr.timestamp,
                    price=curr.price,
                    anchor=kind,
                    direction=direction,
                    signal_type=sig_type,
                    confidence="medium",
                    is_daily_approx=True,
                    is_anomalous=False,
                    reason="价格创新极值但 OBV 未同步（形态背离）",
                    threshold=float(obv_series.iloc[prev.index]),
                    observed_value=float(obv_series.iloc[curr.index]),
                ))
    return out


def _detect_breakouts(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """放量突破检测：close >= 过去 N 日 high 最大值（shift(1) 不含当日）且 rel_vol >= 阈值。"""
    high = prim["high"].astype(float)
    close = prim["close"].astype(float)
    # shift(1): prior max excludes current bar — no self-reference
    prior_max = high.rolling(config.breakout_window).max().shift(1)
    rel_vol = prim["rel_vol"]
    out: list[VPSignal] = []
    for i in range(len(prim)):
        pm = prior_max.iloc[i]
        rv = rel_vol.iloc[i]
        if pd.isna(pm) or pd.isna(rv):
            continue
        if close.iloc[i] >= pm and rv >= config.breakout_rel_vol:
            out.append(VPSignal(
                timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
                price=float(close.iloc[i]),
                anchor="close",
                direction="bullish",
                signal_type="volume_breakout",
                confidence="high",
                is_daily_approx=True,
                is_anomalous=False,
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）",
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
        out.append(VPSignal(
            timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
            price=float(close.iloc[i]),
            anchor="close",
            direction="bullish",
            signal_type="shrink_pullback",
            confidence="medium",
            is_daily_approx=True,
            is_anomalous=False,
            reason=f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）",
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

    status = "degraded" if degraded_reason else "ok"
    return VPSResult(markers=markers, status=status, degraded_reason=degraded_reason)
