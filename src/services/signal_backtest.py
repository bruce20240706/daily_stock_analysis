# -*- coding: utf-8 -*-
"""信号规则级三重门回测（纯函数，bar-interval 无关）。

对历史逐 bar 因果重跑信号规则 + derive_price_levels，
前瞻 horizon 根 bar 判 赢/输/平（三重门）。

设计要点：
- 因果约束：评估 bar t 仅使用 df.iloc[:t+1]，绝无未来函数。
- 信号触发判定：marker.timestamp == _last_ts(window)，即"新触发于当前 bar"。
  与引擎语义一致：A 类 detector 均在最末 bar 或确认 bar 出点（如 OBV 背离确认 bar），
  timestamp 均通过 _to_epoch_ms_shanghai(date) 生成，_last_ts 取 window 末行相同转换。
- 基线对照：baseline 每个有效入场 bar 均入场一次（全体 bar 基准），signal_type 固定为 __baseline__。
- sample = win + loss（expired 被排除在胜率分母外，因未触及止损）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from src.services.volume_price_signals import (
    VPSConfig,
    _to_epoch_ms_shanghai,
    compute_volume_price_signals,
    derive_price_levels,
)

BASELINE_SIGNAL_TYPE = "__baseline__"


@dataclass(frozen=True)
class SignalOutcome:
    """单次信号触发的三重门回测结果。

    signal_type  信号类型（与 VPSignal.signal_type 对应；baseline 固定 '__baseline__'）
    market       市场标识（'cn' / 'hk' / 'us' 等，透传自调用方）
    outcome      'win' | 'loss' | 'expired'
    """

    signal_type: str
    market: str
    outcome: str


def classify_triple_barrier(
    forward_bars: List[dict],
    *,
    stop: float,
    target: float,
) -> str:
    """三重门分类：逐根前瞻判断止盈/止损/到期。

    规则（长仓视角）：
    - 同根 bar 同时触及 target 和 stop → 保守判 loss
    - 先触 target（high >= target）→ win
    - 先破 stop（low <= stop）→ loss
    - 到期未触任何门 → expired

    Args:
        forward_bars: 触发 bar 之后的前瞻数据，每个元素须含 'high'/'low'/'close'。
        stop:         止损价（long 仓 low <= stop 触发）。
        target:       止盈价（long 仓 high >= target 触发）。

    Returns:
        'win' | 'loss' | 'expired'
    """
    for bar in forward_bars:
        hit_target = bar["high"] >= target
        hit_stop = bar["low"] <= stop
        if hit_target and hit_stop:
            return "loss"   # 同 bar 两触：保守判 loss
        if hit_stop:
            return "loss"
        if hit_target:
            return "win"
    return "expired"


def _bars_as_dicts(df: pd.DataFrame) -> List[dict]:
    """将 DataFrame 子集转为 classify_triple_barrier 所需的 dict list。"""
    return df[["high", "low", "close"]].to_dict("records")


def _last_ts(window: pd.DataFrame) -> int:
    """返回 window 最末 bar 的上海午夜毫秒时间戳（与引擎出点口径一致）。

    引擎中所有 detector 均通过 _to_epoch_ms_shanghai(prim['date'].iloc[i]) 生成
    VPSignal.timestamp，此处以相同函数处理 window.iloc[-1]['date']，保证对齐。
    无论 date 列是字符串还是 pd.Timestamp，_to_epoch_ms_shanghai 均可处理。
    """
    return _to_epoch_ms_shanghai(window.iloc[-1]["date"])


def _eval(
    df: pd.DataFrame,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig],
    all_bars: bool,
    min_history: int,
) -> List[SignalOutcome]:
    """内部：逐 bar 因果走查，产出 SignalOutcome 列表。

    Args:
        df:          完整历史 OHLCV DataFrame（含 date 列）。
        market:      市场标识，透传至 SignalOutcome。
        horizon:     前瞻 bar 数（含）。
        config:      VPSConfig，None 时使用默认值。
        all_bars:    True → baseline 模式（每个有效 bar 均入场）；
                     False → 信号模式（仅当前 bar 触发的 bullish 信号入场）。
        min_history: 进入评估前所需最小历史 bar 数（因果预热）。

    Returns:
        SignalOutcome 列表。
    """
    cfg = config or VPSConfig.from_env()
    df = df.reset_index(drop=True)
    out: List[SignalOutcome] = []
    n = len(df)

    # 注意：末尾若干 bar 的前瞻窗口会被截断至剩余可用 bar 数（不补零）。
    for t in range(min_history, n - 1):  # 至少留 1 根前瞻 bar
        # 因果窗口：仅使用 ≤t 数据
        window = df.iloc[: t + 1]

        # 推导价位：需 stop/target 均可用
        levels = derive_price_levels(window)
        if levels.stop is None or levels.target is None:
            continue

        # 前瞻序列：[t+1, t+horizon]（取不到则截断，不补 0）
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue

        if all_bars:
            # baseline：每个有效 bar 均产生一条记录
            out.append(
                SignalOutcome(
                    signal_type=BASELINE_SIGNAL_TYPE,
                    market=market,
                    outcome=classify_triple_barrier(
                        fwd, stop=levels.stop, target=levels.target
                    ),
                )
            )
        else:
            # 信号模式：仅收集本 bar 新触发的 bullish 信号
            res = compute_volume_price_signals(window, config=cfg)
            last_bar_ts = _last_ts(window)
            triggered = {
                m.signal_type
                for m in res.markers
                if m.direction == "bullish" and m.timestamp == last_bar_ts
            }
            for sig_type in triggered:
                out.append(
                    SignalOutcome(
                        signal_type=sig_type,
                        market=market,
                        outcome=classify_triple_barrier(
                            fwd, stop=levels.stop, target=levels.target
                        ),
                    )
                )

    return out


def evaluate_signal_outcomes(
    df,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig] = None,
    min_history: int = 40,
) -> List[SignalOutcome]:
    """逐 bar 因果重跑信号规则，返回所有触发信号的三重门结果。

    仅收录 direction=='bullish' 且触发于当前 bar（timestamp 与 window 末行对齐）的信号。
    expired 结果保留在输出中；胜率分母（sample）= win + loss 由调用方计算。

    Args:
        df:          完整历史 OHLCV DataFrame（date/open/high/low/close/volume）。
        market:      市场标识（透传至 SignalOutcome.market）。
        horizon:     前瞻 bar 数上限。
        config:      VPSConfig，None 时使用默认值。
        min_history: 进入评估前所需最小历史 bar 数。

    Returns:
        SignalOutcome 列表，可能为空。
    """
    return _eval(
        df,
        market=market,
        horizon=horizon,
        config=config,
        all_bars=False,
        min_history=min_history,
    )


def evaluate_baseline_outcomes(
    df,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig] = None,
    min_history: int = 40,
) -> List[SignalOutcome]:
    """全体 bar 基准回测：每个有效入场 bar 均产生一条 __baseline__ 记录。

    用于与 evaluate_signal_outcomes 对比，衡量信号相对于随机入场的超额。
    signal_type 固定为 '__baseline__'（BASELINE_SIGNAL_TYPE）。

    Args:
        df:          完整历史 OHLCV DataFrame（date/open/high/low/close/volume）。
        market:      市场标识（透传至 SignalOutcome.market）。
        horizon:     前瞻 bar 数上限。
        config:      VPSConfig，None 时用于 derive_price_levels 默认值（baseline 不跑信号规则）。
        min_history: 进入评估前所需最小历史 bar 数。

    Returns:
        SignalOutcome 列表，数量 >= evaluate_signal_outcomes 的触发数。
    """
    return _eval(
        df,
        market=market,
        horizon=horizon,
        config=config,
        all_bars=True,
        min_history=min_history,
    )
