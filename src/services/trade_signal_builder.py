# -*- coding: utf-8 -*-
"""TradeSignal 只读构造器(actionable-signal 战略 Inc 0)。

三个纯函数:零 I/O、零 config 读取、零 DB 访问、零日志。
把仓库里**已经存在**的两种信号载荷适配成 canonical 形状,四种既有价位拼写一个不动:

  PriceLevels(规则路径,src/services/volume_price_signals.py:352)  -> build_from_price_levels
  SniperPoints(LLM 路径,src/schemas/report_schema.py:128)          -> build_from_sniper_points

`source` 与 `direction` 由构造器自身固定,不是参数:它们描述的正是这条信号从哪条
路径来、朝哪一边。两条路径今天都只产 long——PriceLevels 是 long-setup,SniperPoints
是买入点 + 止盈。short 有 schema、有校验器、有测试,但无生产者(见 spec §13.1)。

设计见 docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md。
"""

from __future__ import annotations

from typing import Optional

from src.schemas.decision_action import DecisionAction
from src.schemas.trade_signal import (
    Invalidation,
    PriceZone,
    SignalEvidence,
    TradeSignal,
)
from src.services.volume_price_signals import PriceLevels, is_invalid_price_level


def build_from_price_levels(
    levels: PriceLevels,
    *,
    code: str,
    market: str,
    signal_type: str,
    interval: str,
    horizon_bars: int,
    as_of: str,
    confidence: str,
    invalidation: Invalidation,
    current_price: Optional[float] = None,
    action: Optional[DecisionAction] = None,
    evidence: Optional[SignalEvidence] = None,
) -> Optional[TradeSignal]:
    """规则路径:PriceLevels -> TradeSignal(long)。源数据不足以构成有效信号时返回 None。

    fail-closed 判据直接调 is_invalid_price_level(volume_price_signals.py:425):
    它一次覆盖缺值、非有限、<= 0、排序违反、以及 entry > current_price。它写死
    long-setup,而本路径本就只产 long,故适用(short 路径不得复用它)。

    entry_zone 是**退化点区间**(low == high == levels.entry):规则路径只有一个
    入场标量。真正的两端区间只有 LLM 路径(靠 secondary_buy)才有。

    levels.risk_reward **不读取**:TradeSignal.risk_reward 由公式独立算出,单一来源
    不双写。tests/test_trade_signal_contract_locks.py 用哨兵值 99.0 锁住这一点。
    """
    if is_invalid_price_level(
        entry=levels.entry,
        stop=levels.stop,
        target=levels.target,
        current_price=current_price,
    ):
        return None

    entry = float(levels.entry)
    return TradeSignal(
        code=code,
        market=market,
        signal_type=signal_type,
        interval=interval,
        horizon_bars=horizon_bars,
        as_of=as_of,
        source="rule",
        direction="long",
        entry_zone=PriceZone(low=entry, high=entry),
        stop=float(levels.stop),
        targets=[float(levels.target)],
        confidence=confidence,
        invalidation=invalidation,
        action=action,
        evidence=evidence,
    )
