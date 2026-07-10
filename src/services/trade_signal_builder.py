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

from typing import Any, List, Mapping, Optional

from src.schemas.decision_action import DecisionAction
from src.schemas.report_schema import SniperPoints
from src.schemas.trade_signal import (
    Invalidation,
    PriceZone,
    SignalEvidence,
    TradeSignal,
    is_positive_finite,
    trade_levels_invalid,
)
from src.services.volume_price_signals import PriceLevels, is_invalid_price_level
from src.sniper_parsing import parse_sniper_value


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


def build_from_sniper_points(
    sniper: SniperPoints,
    *,
    code: str,
    market: str,
    signal_type: str,
    interval: str,
    horizon_bars: int,
    as_of: str,
    confidence: str,
    invalidation: Invalidation,
    action: Optional[DecisionAction] = None,
    evidence: Optional[SignalEvidence] = None,
) -> Optional[TradeSignal]:
    """LLM 路径:SniperPoints -> TradeSignal(long)。源数据不足以构成有效信号时返回 None。

    四个值一律走 parse_sniper_value(src/sniper_parsing.py:13),不另写解析。
    继承其行为:"18.50-19.00" -> 19.0(区间字符串塌缩到上界)。

    secondary_buy 是入场区间的第二端(可选);它的坏值只降级为退化点区间,不使
    整条信号作废。**筛选必须先于 min/max**,否则 min(19.0, -5.0) 会把 -5.0 选成
    区间下端。

    排序与正数/有限性由 trade_levels_invalid 一次判定,**不**用
    try/except ValidationError 吞异常:那会把「源数据不足」(应返回 None)与
    「调用方传参错误」(应抛异常)混为一谈。
    """
    ideal = parse_sniper_value(sniper.ideal_buy)
    secondary = parse_sniper_value(sniper.secondary_buy)
    stop = parse_sniper_value(sniper.stop_loss)
    target = parse_sniper_value(sniper.take_profit)

    if ideal is None or stop is None or target is None:
        return None

    if is_positive_finite(ideal) and is_positive_finite(secondary):
        zone_low, zone_high = min(ideal, secondary), max(ideal, secondary)
    else:
        zone_low = zone_high = ideal

    targets: List[float] = [target]
    if trade_levels_invalid(
        direction="long",
        zone_low=zone_low,
        zone_high=zone_high,
        stop=stop,
        targets=targets,
    ):
        return None

    return TradeSignal(
        code=code,
        market=market,
        signal_type=signal_type,
        interval=interval,
        horizon_bars=horizon_bars,
        as_of=as_of,
        source="llm",
        direction="long",
        entry_zone=PriceZone(low=zone_low, high=zone_high),
        stop=stop,
        targets=targets,
        confidence=confidence,
        invalidation=invalidation,
        action=action,
        evidence=evidence,
    )


_EVIDENCE_KEYS = frozenset({
    "verified", "hit_rate", "hit_sample", "ci_low", "ci_high",
    "baseline_excess", "ci_low_corrected", "family_size",
})


def attach_evidence(
    signal: TradeSignal,
    hit_fields: Optional[Mapping[str, Any]],
) -> TradeSignal:
    """把 resolve_marker_hit_fields 的输出映成 SignalEvidence,返回新的 TradeSignal。

    判别「有无证据」用 `hit_sample is not None`,**不是** `verified`:
    resolver 的 _none 哨兵(signal_hit_rate.py:117-120)是 hit_sample=None 且
    verified=False;而「有桶但未通过超额判定」也是 verified=False 却带真实样本。
    用 verified 判别会把后者一起丢掉。

    horizon 不匹配时 raise:把 5 根窗口的统计附到 10 根窗口的信号上是编程错误,
    不是缺数据,必须响,不静默。

    risk_metrics / oos 刻意丢弃(spec §5.6(5))。
    """
    if not hit_fields or hit_fields.get("hit_sample") is None:
        return signal

    horizon = hit_fields.get("horizon")
    if horizon is not None and int(horizon) != signal.horizon_bars:
        raise ValueError(
            f"evidence horizon {horizon} does not match signal.horizon_bars "
            f"{signal.horizon_bars}"
        )

    evidence = SignalEvidence(**{key: hit_fields.get(key) for key in _EVIDENCE_KEYS})
    return signal.model_copy(update={"evidence": evidence})
