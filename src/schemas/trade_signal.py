# -*- coding: utf-8 -*-
"""Canonical TradeSignal 契约(actionable-signal 战略 Inc 0)。

设计见 docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md。
呈现边界与 canonical <-> 遗留投影映射表见 docs/trade-signal-contract.md。

**零接线**:本模块目前只允许被 src/services/trade_signal_builder.py 导入。
tests/test_trade_signal_contract_locks.py 的 import 白名单锁住这一点;
接线到任何 runtime 路径前,先读 docs/trade-signal-contract.md 的呈现边界。

本模块只依赖 stdlib / pydantic / 同包 schema,不得 import src.services / src.core
(层级倒挂)。故 RESERVED_SIGNAL_TYPE 与 SignalInterval 是字面量,由 drift-lock
钉死在各自的真源上。
"""

from __future__ import annotations

import math
from typing import Annotated, Any, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.analysis_context_pack import validate_iso8601_timestamp
from src.schemas.decision_action import DecisionAction

# get_market_for_stock(src/core/trading_calendar.py:110)的值域去掉 None。
# 不复用 MarketRegion(src/schemas/market_light.py:11):它无 crypto 成员,
# 且描述的是市场级 regime 而非个股信号的市场键。
SignalMarket = Literal["cn", "hk", "us", "crypto"]

# 持仓方向。观望 / 无信号 → 不产生 TradeSignal 对象(构造器返回 None)。
SignalDirection = Literal["long", "short"]

# 与 SignalMarker.confidence(api/v1/schemas/stocks.py:120)同 token,
# 使 is_high_confidence(src/phase_decision_guardrail.py:229)与 Inc 3 的封顶机制零改可用。
SignalConfidence = Literal["high", "medium", "low"]

# 与 SignalMarker.source 同 token。
SignalSource = Literal["rule", "llm"]

# 必须与 SUPPORTED_INTERVALS(src/core/intraday_backtest.py:14)同集合;由 drift-lock 锁定。
SignalInterval = Literal["1d", "1m", "5m", "15m", "1h"]

# 回测基线格子的保留哨兵 signal_type。真源:BASELINE_SIGNAL_TYPE
# (src/services/signal_backtest.py:37)。schema 层不能 import 回测模块,故字面量 + drift-lock。
RESERVED_SIGNAL_TYPE: str = "__baseline__"

# 价位元素约束:gt=0 拦不住 inf(inf > 0 为真),必须叠加 allow_inf_nan=False。
Level = Annotated[float, Field(gt=0, allow_inf_nan=False)]


def is_positive_finite(value: Any) -> bool:
    """value 是有限正数?bool 是 int 子类,显式排除。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def trade_levels_invalid(
    *,
    direction: str,
    zone_low: Optional[float],
    zone_high: Optional[float],
    stop: Optional[float],
    targets: Optional[Sequence[Optional[float]]],
) -> bool:
    """方向感知的价位判据(单一实现)。

    模型的 model_validator 与 build_from_sniper_points 的 fail-closed 前置检查
    共用本函数,因此它必须同时判定三件事:

    1. 任一值为 None / 非有限 / <= 0;
    2. zone_low > zone_high;
    3. 按 direction 的排序不变式违反:
       long : stop < zone_low <= zone_high < targets[0] < targets[1] < ...
       short: stop > zone_high >= zone_low > targets[0] > targets[1] > ...

    判据 (1) 不可省:构造器要在**构造模型之前**用它做 fail-closed 判定,那时值
    还是 parse_sniper_value 吐出的裸浮点,而该函数的字符串入口会漏出 0.0 / -5.0 /
    inf / nan(见 spec §2.4)。若谓词只管排序,这些值会流进模型抛 ValidationError
    而非返回 None,违反构造器的 fail-closed 契约。

    **不复用** is_invalid_price_level(src/services/volume_price_signals.py:425):
    它写死 long-setup 且额外要求 entry <= current_price,对 short 是错的。
    **不复用** claim_validation.validate_structure:它是 long-only、吃 sniper dict
    形状、缺失字段即跳过而非失败、返回报告而非二值判据(见 spec §8.1)。
    """
    if direction not in ("long", "short"):
        return True
    if not targets:
        return True
    for value in (zone_low, zone_high, stop, *targets):
        if not is_positive_finite(value):
            return True
    if zone_low > zone_high:
        return True
    if direction == "long":
        if not stop < zone_low:
            return True
        previous = zone_high
        for target in targets:
            if not target > previous:
                return True
            previous = target
    else:
        if not stop > zone_high:
            return True
        previous = zone_low
        for target in targets:
            if not target < previous:
                return True
            previous = target
    return False


class PriceZone(BaseModel):
    """入场区间。规则路径退化为一点(low == high);LLM 路径靠 secondary_buy 给出两端。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    low: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _low_not_above_high(self) -> "PriceZone":
        if self.low > self.high:
            raise ValueError("entry_zone.low must not exceed entry_zone.high")
        return self


class SignalEvidence(BaseModel):
    """样本外统计证据。字段拼写逐字对齐 SignalMarker(api/v1/schemas/stocks.py:126-133)。

    刻意**不收** risk_metrics / oos:二者形状因生产者而异(链路A 带 note、链路B 带
    excluded/interval/horizon),且是描述性统计而非选择判据——只有 verified 是。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    verified: bool
    hit_rate: Optional[float] = None
    hit_sample: Optional[int] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    baseline_excess: Optional[float] = None
    ci_low_corrected: Optional[float] = None
    family_size: Optional[int] = None


class Invalidation(BaseModel):
    """信号失效条件(区别于 stop:stop 是已入场后的止损,本结构是信号本身作废)。

    price 的方向语义由 TradeSignal.direction 决定:
    long → 收盘价 <= price 即失效;short → 收盘价 >= price 即失效。

    刻意**不做** price 与 entry_zone 的跨字段约束:「跌破 MA20 即失效」这类合法前提
    完全可能落在区间内部,过度约束会让合法场景不可表达。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    valid_until: Optional[str] = None   # 完整 ISO-8601 datetime(必须含 'T')
    note: Optional[str] = None          # 结构性前提,不参与机器判定

    @field_validator("valid_until")
    @classmethod
    def _valid_until_iso8601(cls, value: Optional[str]) -> Optional[str]:
        return validate_iso8601_timestamp(value)

    @model_validator(mode="after")
    def _at_least_one_present(self) -> "Invalidation":
        if self.price is None and self.valid_until is None and self.note is None:
            raise ValueError(
                "invalidation requires at least one of price / valid_until / note"
            )
        return self


class TradeSignal(BaseModel):
    """canonical 可执行信号契约(战略 §L1:72 的 8 字段)。

    身份四元组 (signal_type, market, interval, horizon_bars) 与 signal_stats 的
    自然键对齐,evidence 靠它定位统计桶。故 horizon 拆成 interval + horizon_bars:
    单独一个 horizon 无法定位证据桶。

    **零接线**:见模块 docstring 与 docs/trade-signal-contract.md。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- 身份 ---
    code: str = Field(min_length=1)
    market: SignalMarket
    signal_type: str = Field(min_length=1)
    interval: SignalInterval = "1d"
    horizon_bars: int = Field(gt=0)
    as_of: str                     # 完整 ISO-8601 datetime;日线写 T00:00:00
    source: SignalSource

    # --- 战略 :72 钦定的 8 字段(horizon 拆为 interval + horizon_bars)---
    direction: SignalDirection
    entry_zone: PriceZone
    stop: float = Field(gt=0, allow_inf_nan=False)
    targets: List[Level] = Field(min_length=1)
    # 单位 = 权益比例;允许 > 1 表示杠杆(perp L>1 回测真实存在),故不设 le=1。
    # None = 尚未定量(Inc 5 的 sizing 填)。
    position_size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    confidence: SignalConfidence
    invalidation: Invalidation

    # --- 投影与证据 ---
    action: Optional[DecisionAction] = None       # 八态投影,不新增词表
    evidence: Optional[SignalEvidence] = None     # None = 无历史统计路径(short 侧必然如此)

    @field_validator("market", mode="before")
    @classmethod
    def _normalize_market(cls, value: Any) -> Any:
        """引擎/回测/signal_stats 用小写,看板 _infer_market 用大写;此处归一。"""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("signal_type")
    @classmethod
    def _reject_reserved_signal_type(cls, value: str) -> str:
        if value == RESERVED_SIGNAL_TYPE:
            raise ValueError(
                f"signal_type must not be the reserved sentinel {RESERVED_SIGNAL_TYPE!r}"
            )
        return value

    @field_validator("as_of")
    @classmethod
    def _as_of_iso8601(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)

    @model_validator(mode="after")
    def _levels_consistent(self) -> "TradeSignal":
        if trade_levels_invalid(
            direction=self.direction,
            zone_low=self.entry_zone.low,
            zone_high=self.entry_zone.high,
            stop=self.stop,
            targets=self.targets,
        ):
            raise ValueError(
                "trade levels violate the direction-aware ordering invariant "
                f"(direction={self.direction})"
            )
        return self

    @property
    def risk_reward(self) -> float:
        """报酬风险比,取**最差入场**(long 用 zone.high、short 用 zone.low)。

        普通 @property 而非 computed_field:后者会进入 model_dump(),与
        extra="forbid" 组合会让 TradeSignal(**s.model_dump()) round-trip 抛
        ValidationError。分母由 _levels_consistent 保证严格为正,故恒有限。
        """
        if self.direction == "long":
            entry = self.entry_zone.high
            return (self.targets[0] - entry) / (entry - self.stop)
        entry = self.entry_zone.low
        return (entry - self.targets[0]) / (self.stop - entry)
