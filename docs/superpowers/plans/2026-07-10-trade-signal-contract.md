# Inc 0 — canonical TradeSignal 契约 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 定义 canonical `TradeSignal` pydantic 契约与两条只读纯函数构造器,证明它能被今天的数据填满,且**零接线**(不接 API / 报告 / 通知 / DB / config / 前端)。

**Architecture:** 防腐层。`src/schemas/trade_signal.py` 放纯 pydantic 模型 + 方向感知谓词,只依赖 stdlib / pydantic / 同包 schema;`src/services/trade_signal_builder.py` 放三个纯函数,从既有的 `PriceLevels`(规则路径)与 `SniperPoints`(LLM 路径)适配出 canonical 形状。四种既有价位拼写一个不动。呈现边界由 import 白名单 drift-lock 保证。

**Tech Stack:** Python 3.x、pydantic 2.13.4、pytest。无新依赖、无新配置项、无前端改动。

设计真源:`docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md`(commit `eadb1c53`)。本计划中所有代码均已在 scratchpad 原型中对真实 `derive_price_levels` / `parse_sniper_value` / pydantic 2.13.4 实跑通过。

## Global Constraints

- **零接线**:`api/`、`bot/`、`src/notification.py`、`templates/`、`src/storage.py`、`apps/`、`src/config.py`、`src/core/config_registry.py`、`.env.example` **一律不动**。Task 7 的 import 白名单会强制这一点。
- **唯一允许触碰的既有源文件**:`src/schemas/analysis_context_pack.py`(Task 1,零行为变化)。
- commit message:英文类型前缀 + 中文正文,**不加** `Co-Authored-By`,不加工具/agent 前缀,单条 `git commit -m`。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 用**扁平格式** `- [类型] 描述`,**禁止** `### 类目标题`。
- 工作目录 `/root/dsa-trade-signal`(worktree),分支 `feat/trade-signal-contract`。
- 跑测试前置 venv:`export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`。**禁止** `| tail`(会掩盖退出码)。
- `tests/__init__.py` 存在 → pytest prepend 模式已把仓库根塞进 `sys.path`,**不需要** `PYTHONPATH`。
- 基线:`ci_gate.sh` = `4113 passed`。预期只增不减,**现有测试零改**。
- 无前端改动 → **免 web-gate**。无 AI 协作资产改动 → 不跑 `check_ai_assets.py`。
- pytest 汇总行前有 `=` 前缀,`grep '^[0-9]\+ passed'` 匹配不到;要抓计数用 `grep -E '[0-9]+ passed'`。

## File Structure

| 动作 | 文件 | 职责 |
| --- | --- | --- |
| Modify | `src/schemas/analysis_context_pack.py:25,35` | ISO-8601 校验器提升为公开名 + 私有别名 |
| Create | `src/schemas/trade_signal.py` | 类型别名、`is_positive_finite`、`trade_levels_invalid`、`PriceZone` / `SignalEvidence` / `Invalidation` / `TradeSignal` |
| Create | `src/services/trade_signal_builder.py` | `build_from_price_levels` / `build_from_sniper_points` / `attach_evidence` |
| Create | `tests/test_trade_signal.py` | 测 1–12(schema 与校验器) |
| Create | `tests/test_trade_signal_builder.py` | 测 13–20(构造器 + 纯度) |
| Create | `tests/test_trade_signal_contract_locks.py` | 测 21–24(四条 drift-lock) |
| Create | `docs/trade-signal-contract.md` | 呈现契约 + canonical ↔ 遗留投影映射表 |
| Modify | `docs/CHANGELOG.md:12` | `[Unreleased]` 首行插入一条扁平条目 |

---

### Task 1: ISO-8601 校验器提升为公开名

`src/schemas/trade_signal.py` 需要复用 `analysis_context_pack.py` 里的 ISO-8601 校验器,但它当前是私有名 `_validate_iso8601_timestamp`。跨模块导入私有符号不可接受,复制一份则是平行实现。提升为公开名并保留私有别名,现有两处调用点(`:74`、`:90`)一行不改。

已核实:该符号只有 2 个内部调用点,模块无 `__all__`,无测试断言 `src.schemas.__all__`。

**Files:**
- Modify: `src/schemas/analysis_context_pack.py:25` 与 `:35` 之后
- Test: `tests/test_trade_signal.py`(本 task 只加 1 条;其余 11 条在 Task 3)

**Interfaces:**
- Consumes: 无
- Produces: `src.schemas.analysis_context_pack.validate_iso8601_timestamp(value: Optional[str]) -> Optional[str]` — `None` 直通;字符串必须含 `"T"` 且 `datetime.fromisoformat` 可解析(结尾 `Z` 先归一为 `+00:00`);否则 `raise ValueError("timestamp must be an ISO 8601 datetime string")`。私有别名 `_validate_iso8601_timestamp` 指向同一对象。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_trade_signal.py`:

```python
# -*- coding: utf-8 -*-
"""Inc 0 canonical TradeSignal 契约:schema 与校验器测试。

设计见 docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md。
"""

import pytest

from src.schemas import analysis_context_pack


def test_iso8601_validator_is_public_and_aliased():
    """公开名可导入,私有别名指向同一对象(现有引用零改)。"""
    public = analysis_context_pack.validate_iso8601_timestamp
    private = analysis_context_pack._validate_iso8601_timestamp
    assert public is private

    assert public(None) is None
    assert public("2026-07-10T00:00:00") == "2026-07-10T00:00:00"
    assert public("2026-07-10T00:00:00Z") == "2026-07-10T00:00:00Z"
    with pytest.raises(ValueError):
        public("2026-07-10")          # 裸日期无 'T'
    with pytest.raises(ValueError):
        public("not-a-timestamp-T")   # 含 'T' 但不可解析
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
cd /root/dsa-trade-signal
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
python -m pytest tests/test_trade_signal.py -v
```

Expected: FAIL — `AttributeError: module 'src.schemas.analysis_context_pack' has no attribute 'validate_iso8601_timestamp'`

- [ ] **Step 3: 改名 + 加别名**

`src/schemas/analysis_context_pack.py:25`,把

```python
def _validate_iso8601_timestamp(value: Optional[str]) -> Optional[str]:
```

改成

```python
def validate_iso8601_timestamp(value: Optional[str]) -> Optional[str]:
```

函数体一行不改。在其 `return value` 之后(原 `:35` 与 `:38` 之间的空行处)插入别名:

```python
# 私有别名:保持模块内既有引用(:74 / :90)与任何历史导入不变。
_validate_iso8601_timestamp = validate_iso8601_timestamp
```

模块内 `:74` 与 `:90` 两处 `return _validate_iso8601_timestamp(value)` **保持原样**——它们通过别名解析到同一函数。

- [ ] **Step 4: 跑测试确认通过,并确认既有 context-pack 测试没红**

```bash
python -m pytest tests/test_trade_signal.py -v
python -m pytest tests/test_analysis_context_pack_schema.py tests/test_analysis_context_pack_docs.py \
                 tests/test_analysis_context_pack_overview.py tests/test_analysis_context_pack_prompt.py -q
```

Expected: 第一条 1 passed;第二条全 passed(零行为变化)。

- [ ] **Step 5: 提交**

```bash
git add src/schemas/analysis_context_pack.py tests/test_trade_signal.py
git commit -m "refactor: analysis_context_pack 的 ISO-8601 校验器提升为公开名 validate_iso8601_timestamp(保留私有别名,模块内两处引用与行为逐字不变;供 Inc 0 TradeSignal 契约复用,避免跨模块导入私有符号或复制第二份实现)"
```

---

### Task 2: schema 基座 —— 类型别名、共享谓词、三个子模型

`trade_levels_invalid` 是全增量最承重的纯函数:模型校验器与 LLM 构造器共用它,且它**兼管**「非有限 / `<= 0`」而不只管排序。原因见 spec §5.4:`parse_sniper_value` 的字符串入口无正数/有限守卫,会漏出 `0.0 / -5.0 / inf / nan`;若谓词只管排序,这些值会流进模型抛 `ValidationError` 而非 fail-closed 返回 `None`。

**Files:**
- Create: `src/schemas/trade_signal.py`
- Test: `tests/test_trade_signal.py`(追加)

**Interfaces:**
- Consumes: `validate_iso8601_timestamp`(Task 1)
- Produces:
  - `SignalMarket = Literal["cn","hk","us","crypto"]`、`SignalDirection = Literal["long","short"]`、`SignalConfidence = Literal["high","medium","low"]`、`SignalSource = Literal["rule","llm"]`、`SignalInterval = Literal["1d","1m","5m","15m","1h"]`
  - `RESERVED_SIGNAL_TYPE: str = "__baseline__"`
  - `Level = Annotated[float, Field(gt=0, allow_inf_nan=False)]`
  - `is_positive_finite(value: Any) -> bool`
  - `trade_levels_invalid(*, direction: str, zone_low, zone_high, stop, targets) -> bool`
  - `class PriceZone(BaseModel)` — `low: float`、`high: float`
  - `class SignalEvidence(BaseModel)` — `verified: bool` + 7 个 `Optional`
  - `class Invalidation(BaseModel)` — `price` / `valid_until` / `note`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_trade_signal.py`(在已有 import 下方补 import,并追加测试):

```python
from src.schemas.trade_signal import (
    Invalidation,
    PriceZone,
    RESERVED_SIGNAL_TYPE,
    SignalEvidence,
    is_positive_finite,
    trade_levels_invalid,
)

INF = float("inf")
NAN = float("nan")


def test_is_positive_finite():
    assert is_positive_finite(1) is True
    assert is_positive_finite(0.5) is True
    assert is_positive_finite(True) is False      # bool 是 int 子类,必须排除
    assert is_positive_finite(0) is False
    assert is_positive_finite(-1.0) is False
    assert is_positive_finite(INF) is False
    assert is_positive_finite(NAN) is False
    assert is_positive_finite(None) is False
    assert is_positive_finite("1.0") is False


@pytest.mark.parametrize("direction, zone_low, zone_high, stop, targets", [
    ("long", 11.0, 12.0, 7.0, [19.0]),
    ("long", 11.0, 12.0, 7.0, [19.0, 25.0]),
    ("long", 11.0, 11.0, 7.0, [19.0]),            # 退化点区间合法
    ("short", 30.0, 32.0, 40.0, [25.0]),
    ("short", 30.0, 32.0, 40.0, [25.0, 20.0]),
])
def test_trade_levels_valid(direction, zone_low, zone_high, stop, targets):
    assert trade_levels_invalid(direction=direction, zone_low=zone_low,
                                zone_high=zone_high, stop=stop, targets=targets) is False


@pytest.mark.parametrize("direction, zone_low, zone_high, stop, targets, why", [
    ("sideways", 11.0, 12.0, 7.0, [19.0], "未知 direction"),
    ("long", 11.0, 12.0, 7.0, [], "targets 为空"),
    ("long", 11.0, 12.0, 7.0, None, "targets 为 None"),
    ("long", None, 12.0, 7.0, [19.0], "zone_low 为 None"),
    ("long", 0.0, 12.0, 7.0, [19.0], "zone_low <= 0"),
    ("long", -5.0, 12.0, 7.0, [19.0], "zone_low 为负"),
    ("long", INF, 12.0, 7.0, [19.0], "zone_low 非有限"),
    ("long", 11.0, 12.0, 7.0, [NAN], "target 非有限"),
    ("long", 12.0, 11.0, 7.0, [19.0], "zone_low > zone_high"),
    ("long", 11.0, 12.0, 11.0, [19.0], "stop 未严格低于 zone_low"),
    ("long", 11.0, 12.0, 7.0, [12.0], "target 未严格高于 zone_high"),
    ("long", 11.0, 12.0, 7.0, [19.0, 19.0], "targets 非严格递增"),
    ("short", 30.0, 32.0, 32.0, [25.0], "stop 未严格高于 zone_high"),
    ("short", 30.0, 32.0, 40.0, [30.0], "target 未严格低于 zone_low"),
    ("short", 30.0, 32.0, 40.0, [25.0, 26.0], "targets 非严格递减"),
])
def test_trade_levels_invalid(direction, zone_low, zone_high, stop, targets, why):
    assert trade_levels_invalid(direction=direction, zone_low=zone_low,
                                zone_high=zone_high, stop=stop, targets=targets) is True, why


def test_price_zone():
    zone = PriceZone(low=11.0, high=12.0)
    assert (zone.low, zone.high) == (11.0, 12.0)
    assert PriceZone(low=11.0, high=11.0).low == 11.0          # 退化点区间
    with pytest.raises(ValueError):
        PriceZone(low=12.0, high=11.0)
    with pytest.raises(ValueError):
        PriceZone(low=0.0, high=12.0)
    with pytest.raises(ValueError):
        PriceZone(low=INF, high=INF)                            # gt=0 拦不住 inf
    with pytest.raises(ValueError):
        PriceZone(low=11.0, high=12.0, mid=11.5)                # extra="forbid"


def test_invalidation_requires_at_least_one_field():
    with pytest.raises(ValueError):
        Invalidation()
    assert Invalidation(price=18.0).price == 18.0
    assert Invalidation(valid_until="2026-08-01T15:00:00").note is None
    assert Invalidation(note="跌破颈线").price is None
    with pytest.raises(ValueError):
        Invalidation(price=INF)
    with pytest.raises(ValueError):
        Invalidation(price=0.0)


def test_invalidation_valid_until_is_iso8601():
    """valid_until 必须挂上 ISO-8601 校验器(只测 as_of 会漏掉这条)。"""
    with pytest.raises(ValueError):
        Invalidation(valid_until="2026-08-01")                  # 裸日期无 'T'
    assert Invalidation(valid_until="2026-08-01T15:00:00Z").valid_until.endswith("Z")
    assert Invalidation(valid_until=None, note="x").valid_until is None


def test_signal_evidence():
    ev = SignalEvidence(verified=False, hit_sample=37)
    assert ev.verified is False and ev.hit_sample == 37
    assert ev.ci_low is None and ev.family_size is None
    with pytest.raises(ValueError):
        SignalEvidence(verified=False, risk_metrics={"sharpe": 1.0})   # extra="forbid"
    with pytest.raises(ValueError):
        SignalEvidence()                                                # verified 必填


def test_reserved_signal_type_constant():
    assert RESERVED_SIGNAL_TYPE == "__baseline__"
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
python -m pytest tests/test_trade_signal.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.schemas.trade_signal'`

- [ ] **Step 3: 写实现**

创建 `src/schemas/trade_signal.py`:

```python
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
from typing import Annotated, Any, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.analysis_context_pack import validate_iso8601_timestamp

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
```

**不要**在本 task 预先 import `DecisionAction`:它到 Task 3 才被用到,提前引入就是一个未使用 import。Task 3 会自己补这行。

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_trade_signal.py -v
```

Expected: PASS(约 28 项,含 parametrize 展开)

- [ ] **Step 5: 提交**

```bash
git add src/schemas/trade_signal.py tests/test_trade_signal.py
git commit -m "feat: 新增 TradeSignal 契约基座(类型别名、共享谓词 trade_levels_invalid、PriceZone/SignalEvidence/Invalidation)。谓词兼管非有限与非正数而不只管排序——parse_sniper_value 的字符串入口无守卫会漏出 0.0/-5.0/inf/nan,只管排序会让它们抛 ValidationError 而非 fail-closed;SignalEvidence 不收 risk_metrics/oos(两生产者形状不同且为描述性统计非选择判据);Invalidation 不做 price 与 entry_zone 的跨字段约束(会让「跌破 MA20 即失效」不可表达)"
```

---

### Task 3: `TradeSignal` 模型

**Files:**
- Modify: `src/schemas/trade_signal.py`(追加 `TradeSignal` 类)
- Test: `tests/test_trade_signal.py`(追加)

**Interfaces:**
- Consumes: Task 2 的全部产物
- Produces: `class TradeSignal(BaseModel)`,字段顺序与类型:
  - 身份:`code: str`、`market: SignalMarket`、`signal_type: str`、`interval: SignalInterval = "1d"`、`horizon_bars: int`、`as_of: str`、`source: SignalSource`
  - 战略 8 字段:`direction: SignalDirection`、`entry_zone: PriceZone`、`stop: float`、`targets: List[Level]`、`position_size: Optional[float] = None`、`confidence: SignalConfidence`、`invalidation: Invalidation`(`horizon` 拆为 `interval` + `horizon_bars`)
  - 投影与证据:`action: Optional[DecisionAction] = None`、`evidence: Optional[SignalEvidence] = None`
  - `risk_reward` 是普通 `@property`(**不是** `computed_field`),返回 `float`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_trade_signal.py`:

```python
from src.schemas.trade_signal import TradeSignal

BASE = dict(
    code="600519", market="cn", signal_type="breakout", interval="1d",
    horizon_bars=10, as_of="2026-07-10T00:00:00", confidence="high",
)


def _signal(**overrides):
    payload = dict(BASE)
    payload.setdefault("invalidation", Invalidation(note="结构破坏"))
    payload.update(overrides)
    return TradeSignal(**payload)


def _long(**overrides):
    # 先建 dict 再 update:直接写 `_signal(stop=7.0, **overrides)` 会在
    # `_long(stop=11.0)` 时抛 TypeError(stop 收到多个值)。
    payload = dict(source="rule", direction="long",
                   entry_zone=PriceZone(low=11.0, high=12.0), stop=7.0,
                   targets=[19.0])
    payload.update(overrides)
    return _signal(**payload)


def _short(**overrides):
    payload = dict(source="llm", direction="short",
                   entry_zone=PriceZone(low=30.0, high=32.0), stop=40.0,
                   targets=[25.0])
    payload.update(overrides)
    return _signal(**payload)


def test_valid_long_and_short():
    assert _long().direction == "long"
    assert _long(targets=[19.0, 25.0]).targets == [19.0, 25.0]
    assert _short().direction == "short"
    assert _short(targets=[25.0, 20.0]).targets == [25.0, 20.0]


@pytest.mark.parametrize("overrides", [
    dict(stop=11.0),
    dict(targets=[12.0]),
    dict(targets=[19.0, 19.0]),
], ids=["stop 未严格低于 zone.low", "targets[0] 未严格高于 zone.high", "targets 非严格递增"])
def test_long_ordering_violations(overrides):
    with pytest.raises(ValueError):
        _long(**overrides)


def test_long_zone_low_above_high():
    with pytest.raises(ValueError):
        PriceZone(low=12.0, high=11.0)


@pytest.mark.parametrize("overrides", [
    dict(stop=32.0),
    dict(targets=[30.0]),
    dict(targets=[25.0, 26.0]),
], ids=["stop 未严格高于 zone.high", "targets[0] 未严格低于 zone.low", "targets 非严格递减"])
def test_short_ordering_violations(overrides):
    with pytest.raises(ValueError):
        _short(**overrides)


@pytest.mark.parametrize("overrides", [
    dict(stop=INF),
    dict(stop=NAN),
    dict(stop=0.0),
    dict(targets=[INF]),
    dict(targets=[19.0, INF]),          # 第二个元素才是 inf
    dict(targets=[NAN]),
    dict(position_size=-1.0),
    dict(position_size=0.0),
    dict(position_size=INF),
])
def test_non_finite_or_non_positive_rejected(overrides):
    with pytest.raises(ValueError):
        _long(**overrides)


def test_position_size_allows_leverage_above_one():
    """单位是权益比例;perp L>1 回测真实存在,故不设 le=1。"""
    assert _long(position_size=2.5).position_size == 2.5


def test_extra_forbid_rejects_legacy_spelling():
    """旧拼写污染新契约必须不可表达。"""
    with pytest.raises(ValueError):
        _long(stop_loss=1.0)
    with pytest.raises(ValueError):
        _long(take_profit=19.0)


def test_reserved_signal_type_rejected():
    with pytest.raises(ValueError):
        _long(signal_type=RESERVED_SIGNAL_TYPE)


@pytest.mark.parametrize("raw, expected", [
    ("HK", "hk"), (" Hk ", "hk"), ("hk", "hk"), ("CRYPTO", "crypto"),
])
def test_market_is_normalized_to_lowercase(raw, expected):
    """引擎/回测小写 vs 看板 _infer_market 大写,在契约边界处掐掉。"""
    assert _long(market=raw).market == expected


def test_unknown_market_rejected():
    with pytest.raises(ValueError):
        _long(market="xx")


def test_as_of_and_valid_until_must_be_iso8601_datetime():
    with pytest.raises(ValueError):
        _long(as_of="2026-07-10")                       # 裸日期无 'T'
    assert _long(as_of="2026-07-10T00:00:00Z").as_of.endswith("Z")
    assert _long(invalidation=Invalidation(valid_until="2026-08-01T15:00:00")) is not None


def test_risk_reward_uses_worst_entry_edge():
    """long 用 zone.high、short 用 zone.low。样本必须 zone.low != zone.high,
    否则两端相等,断言对「取哪一端」无区分力。"""
    long_signal = _long(entry_zone=PriceZone(low=11.0, high=12.0), stop=7.0, targets=[19.0])
    assert long_signal.risk_reward == pytest.approx((19.0 - 12.0) / (12.0 - 7.0))   # 1.4
    assert long_signal.risk_reward != pytest.approx((19.0 - 11.0) / (11.0 - 7.0))   # 2.0(错误边)

    short_signal = _short(entry_zone=PriceZone(low=30.0, high=32.0), stop=40.0, targets=[25.0])
    assert short_signal.risk_reward == pytest.approx((30.0 - 25.0) / (40.0 - 30.0))  # 0.5
    assert short_signal.risk_reward != pytest.approx((32.0 - 25.0) / (40.0 - 32.0))  # 0.875(错误边)


def test_model_dump_round_trip_and_no_risk_reward_key():
    """risk_reward 必须是普通 @property:computed_field 会进 model_dump(),
    与 extra="forbid" 组合会让 round-trip 抛 ValidationError。"""
    signal = _long()
    dumped = signal.model_dump()
    assert "risk_reward" not in dumped
    assert TradeSignal(**dumped) == signal
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
python -m pytest tests/test_trade_signal.py -v
```

Expected: FAIL — `ImportError: cannot import name 'TradeSignal' from 'src.schemas.trade_signal'`

- [ ] **Step 3: 写实现**

先补两处 import(Task 2 刻意没提前引入,因为那时它们还是未使用 import):

```python
from typing import Annotated, Any, List, Literal, Optional, Sequence   # 补入 List
...
from src.schemas.decision_action import DecisionAction                 # 新增一行
```

`List` 供本 task 的 `targets: List[Level]` 使用,`DecisionAction` 供 `action` 字段使用。

再在文件末尾追加:

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_trade_signal.py -v
```

Expected: PASS(约 55 项)

- [ ] **Step 5: 提交**

```bash
git add src/schemas/trade_signal.py tests/test_trade_signal.py
git commit -m "feat: 新增 canonical TradeSignal pydantic 模型(战略 8 字段;horizon 拆成 interval+horizon_bars 以定位 signal_stats 证据桶;market 边界处小写归一掐掉引擎与看板的大小写漂移;extra=forbid 令旧拼写污染不可表达;signal_type 拒绝回测保留哨兵;risk_reward 用普通 @property 取最差入场,避开 computed_field 与 extra=forbid 的 model_dump round-trip 陷阱)"
```

---

### Task 4: `build_from_price_levels`(规则路径)

规则路径的 fail-closed 判据直接调 `is_invalid_price_level`:它一次覆盖缺值、非有限、`<= 0`、排序违反、以及 `entry > current_price`。它写死 long-setup,而规则路径**本就**只产 long,故适用。

**Files:**
- Create: `src/services/trade_signal_builder.py`
- Test: `tests/test_trade_signal_builder.py`

**Interfaces:**
- Consumes: `TradeSignal` / `PriceZone` / `Invalidation` / `SignalEvidence`(Task 2、3);`PriceLevels` 与 `is_invalid_price_level`(`src/services/volume_price_signals.py:352` / `:425`)
- Produces:
  ```python
  def build_from_price_levels(
      levels: PriceLevels, *, code: str, market: str, signal_type: str, interval: str,
      horizon_bars: int, as_of: str, confidence: str, invalidation: Invalidation,
      current_price: Optional[float] = None, action: Optional[DecisionAction] = None,
      evidence: Optional[SignalEvidence] = None,
  ) -> Optional[TradeSignal]
  ```
  `source="rule"` 与 `direction="long"` 由函数自身固定,**不是参数**。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_trade_signal_builder.py`:

```python
# -*- coding: utf-8 -*-
"""Inc 0 TradeSignal 只读构造器测试。

规则路径喂**真实** derive_price_levels 的输出,LLM 路径喂**真实** parse_sniper_value,
以此证明契约能被今天的数据填满,而不是白板上的字段名。
"""

import pandas as pd
import pytest

from src.schemas.trade_signal import Invalidation, TradeSignal
from src.services.trade_signal_builder import build_from_price_levels
from src.services.volume_price_signals import PriceLevels, derive_price_levels

COMMON = dict(
    code="600519", market="cn", signal_type="breakout", interval="1d",
    horizon_bars=10, as_of="2026-07-10T00:00:00", confidence="high",
)


def _invalidation():
    return Invalidation(note="跌破颈线即失效")


def _ohlcv(bars: int) -> pd.DataFrame:
    """温和上行的合成日线,足够让 MA20 / 20 根 swing low / ATR 都可算且 entry <= 现价。"""
    close = [10.0 + 0.25 * i for i in range(bars)]
    return pd.DataFrame({
        "date": pd.date_range("2026-05-01", periods=bars, freq="D"),
        "open": [c - 0.10 for c in close],
        "high": [c + 0.30 for c in close],
        "low": [c - 0.30 for c in close],
        "close": close,
        "volume": [1_000_000] * bars,
    })


def test_rule_path_builds_from_real_derive_price_levels():
    frame = _ohlcv(30)
    current_price = float(frame["close"].iloc[-1])
    levels = derive_price_levels(frame)
    assert levels.entry is not None, "前置条件:30 根足以算出价位"

    signal = build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=current_price, **COMMON
    )

    assert isinstance(signal, TradeSignal)
    assert signal.source == "rule"
    assert signal.direction == "long"
    assert signal.entry_zone.low == signal.entry_zone.high == levels.entry   # 退化点区间
    assert signal.stop == levels.stop
    assert signal.targets == [levels.target]
    assert signal.position_size is None
    assert signal.evidence is None
    assert signal.risk_reward > 0


def test_rule_path_fail_closed_on_insufficient_window():
    """窗口不足 → derive_price_levels 返回全 None → 构造器返回 None,不抛异常。"""
    levels = derive_price_levels(_ohlcv(10))
    assert levels.entry is None
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=12.5, **COMMON
    ) is None


def test_rule_path_fail_closed_when_entry_above_current_price():
    levels = PriceLevels(entry=11.0, stop=7.0, target=19.0, risk_reward=2.0)
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=10.0, **COMMON
    ) is None
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=13.0, **COMMON
    ) is not None


@pytest.mark.parametrize("levels", [
    PriceLevels(entry=11.0, stop=None, target=19.0, risk_reward=None),
    PriceLevels(entry=11.0, stop=7.0, target=None, risk_reward=None),
    PriceLevels(entry=11.0, stop=12.0, target=19.0, risk_reward=None),   # stop 高于 entry
    PriceLevels(entry=11.0, stop=7.0, target=10.0, risk_reward=None),    # target 低于 entry
])
def test_rule_path_fail_closed_on_bad_levels(levels):
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=13.0, **COMMON
    ) is None
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.trade_signal_builder'`

- [ ] **Step 3: 写实现**

创建 `src/services/trade_signal_builder.py`:

```python
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

# SignalEvidence 声明的 8 个键。resolve_marker_hit_fields 还返回 horizon /
# risk_metrics / oos,前者用于一致性校验,后两者刻意丢弃(见 spec §5.6(5))。
_EVIDENCE_KEYS = (
    "verified",
    "hit_rate",
    "hit_sample",
    "ci_low",
    "ci_high",
    "baseline_excess",
    "ci_low_corrected",
    "family_size",
)


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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: PASS(7 项)

- [ ] **Step 5: 提交**

```bash
git add src/services/trade_signal_builder.py tests/test_trade_signal_builder.py
git commit -m "feat: 新增规则路径构造器 build_from_price_levels(PriceLevels -> TradeSignal,long 固定,entry_zone 为退化点区间;fail-closed 判据复用 is_invalid_price_level 因其 long-setup 语义恰好适用;levels.risk_reward 不读取,由公式独立重算保持单一来源;测试喂真实 derive_price_levels 输出而非手搓 PriceLevels)"
```

---

### Task 5: `build_from_sniper_points`(LLM 路径)

`secondary_buy` 是入场区间的第二端,LLM 一直在产,只是从没被组装过(还被 `templates/report_wechat.j2:50-54` 直接丢弃)。

**筛选顺序要紧**:必须先筛掉 `secondary` 的坏值再取 `min/max`,否则 `min(19.0, -5.0)` 会把 `-5.0` 选成区间下端。

**Files:**
- Modify: `src/services/trade_signal_builder.py`(追加)
- Test: `tests/test_trade_signal_builder.py`(追加)

**Interfaces:**
- Consumes: Task 4 的模块;`SniperPoints`(`src/schemas/report_schema.py:128`);`parse_sniper_value`(`src/sniper_parsing.py:13`)
- Produces:
  ```python
  def build_from_sniper_points(
      sniper: SniperPoints, *, code: str, market: str, signal_type: str, interval: str,
      horizon_bars: int, as_of: str, confidence: str, invalidation: Invalidation,
      action: Optional[DecisionAction] = None, evidence: Optional[SignalEvidence] = None,
  ) -> Optional[TradeSignal]
  ```
  `source="llm"`、`direction="long"` 由函数固定。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_trade_signal_builder.py`(顶部补 import):

```python
from src.schemas.report_schema import SniperPoints
from src.services.trade_signal_builder import build_from_sniper_points
from src.sniper_parsing import parse_sniper_value


def _sniper(**overrides):
    payload = dict(ideal_buy="19.00", secondary_buy=None,
                   stop_loss="16.00", take_profit="25.00")
    payload.update(overrides)
    return SniperPoints(**payload)


def test_llm_path_assembles_entry_zone_from_both_buy_points():
    """secondary_buy 是区间第二端,LLM 一直在产,只是从没被组装过。"""
    signal = build_from_sniper_points(
        _sniper(secondary_buy="17.80元"), invalidation=_invalidation(), **COMMON
    )
    assert isinstance(signal, TradeSignal)
    assert signal.source == "llm"
    assert signal.direction == "long"
    assert (signal.entry_zone.low, signal.entry_zone.high) == (17.8, 19.0)
    assert signal.stop == 16.0
    assert signal.targets == [25.0]


@pytest.mark.parametrize("secondary, why", [
    (None, "缺失"),
    ("-5", "parse_sniper_value('-5') -> -5.0,非正"),
    ("inf", "parse_sniper_value('inf') -> inf,非有限"),
    ("nan", "parse_sniper_value('nan') -> nan,非有限"),
])
def test_llm_path_degenerates_when_secondary_buy_is_unusable(secondary, why):
    """secondary_buy 是可选的区间下端而非必需价位:坏值只降级为退化点区间,不使信号作废。

    'inf' 一例不可省:若守卫漏掉 math.isfinite 写成 `secondary > 0`,则 inf > 0 为真,
    zone_high = max(19, inf) = inf,被 trade_levels_invalid 拒绝 → 返回 None。
    可契约要求它退化为一条**合法**信号。这个变异逃得过其余全部测试。
    'nan' 走另一条路(nan > 0 为假,恰好被 > 0 挡下),故只测 nan 抓不到该变异。
    """
    signal = build_from_sniper_points(
        _sniper(secondary_buy=secondary), invalidation=_invalidation(), **COMMON
    )
    assert signal is not None, why
    assert signal.entry_zone.low == signal.entry_zone.high == 19.0


@pytest.mark.parametrize("overrides, why", [
    (dict(stop_loss=None), "stop_loss 缺失"),
    (dict(stop_loss="20.00"), "stop_loss 高于 ideal_buy,排序违反"),
    (dict(ideal_buy="0"), "parse_sniper_value('0') -> 0.0 泄漏"),
    (dict(take_profit="inf"), "parse_sniper_value('inf') -> inf 泄漏"),
])
def test_llm_path_fail_closed_returns_none_without_raising(overrides, why):
    """fail-closed:返回 None,**不**抛异常(不得用 try/except ValidationError 吞)。"""
    assert build_from_sniper_points(
        _sniper(**overrides), invalidation=_invalidation(), **COMMON
    ) is None, why


def test_parse_sniper_value_inherited_behaviour_is_characterized():
    """锁住既有行为,防有人「顺手修」。构造器原样继承,不在本增量修改。"""
    assert parse_sniper_value("18.50-19.00") == 19.0    # 区间字符串塌缩到上界
    assert parse_sniper_value("0") == 0.0               # 字符串入口无 >0 守卫
    assert parse_sniper_value(0) is None                # 数值入口有 >0 守卫
    assert parse_sniper_value("inf") == float("inf")    # 字符串入口无有限性守卫
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: FAIL — `ImportError: cannot import name 'build_from_sniper_points'`

- [ ] **Step 3: 写实现**

在 `src/services/trade_signal_builder.py` 末尾追加(并在顶部 import 块加入 `from src.schemas.report_schema import SniperPoints`):

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: PASS(约 20 项)

- [ ] **Step 5: 提交**

```bash
git add src/services/trade_signal_builder.py tests/test_trade_signal_builder.py
git commit -m "feat: 新增 LLM 路径构造器 build_from_sniper_points(把 ideal_buy 与 secondary_buy 组装成 entry_zone 两端——该区间 LLM 一直在产但从无消费方;解析统一走共享 parse_sniper_value 不另写;secondary 坏值只退化为点区间不使信号作废,且筛选先于 min/max 否则 -5.0 会被选成区间下端;fail-closed 走 trade_levels_invalid 返回 None,不用 try/except ValidationError 吞异常;characterization 测试锁住 parse_sniper_value 的区间塌缩与守卫不对称)"
```

---

### Task 6: `attach_evidence` + 构造器纯度断言

判别「有无证据」用 `hit_sample is not None`,**不是** `verified`。理由:`resolve_marker_hit_fields` 的 `_none` 哨兵是 `hit_sample=None` 且 `verified=False`;而「有桶但未通过超额判定」也是 `verified=False` 却带真实样本。用 `verified` 判别会把后者一起丢掉。

**Files:**
- Modify: `src/services/trade_signal_builder.py`(追加)
- Test: `tests/test_trade_signal_builder.py`(追加)

**Interfaces:**
- Consumes: Task 5 的模块
- Produces: `def attach_evidence(signal: TradeSignal, hit_fields: Optional[Mapping[str, Any]]) -> TradeSignal`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_trade_signal_builder.py`(顶部补 `import ast`、`from pathlib import Path`、`from src.services.trade_signal_builder import attach_evidence`):

```python
REPO_ROOT = Path(__file__).resolve().parents[1]

# resolve_marker_hit_fields(src/services/signal_hit_rate.py:117-120)的缺桶哨兵
RESOLVER_NONE_SENTINEL = {
    "hit_rate": None, "hit_sample": None, "verified": False, "ci_low": None,
    "ci_high": None, "baseline_excess": None, "horizon": None,
    "ci_low_corrected": None, "family_size": None, "risk_metrics": None, "oos": None,
}

# 有桶但未通过超额判定:verified=False 却带真实样本。八个字段值互不相同以锁住串位。
RESOLVER_REAL_BUCKET = {
    "hit_rate": 0.61, "hit_sample": 37, "verified": False, "ci_low": 0.52,
    "ci_high": 0.71, "baseline_excess": 0.09, "horizon": 10,
    "ci_low_corrected": 0.48, "family_size": 23,
    "risk_metrics": {"sharpe": 1.0}, "oos": {"train": 1},
}


def _llm_signal():
    return build_from_sniper_points(_sniper(), invalidation=_invalidation(), **COMMON)


def test_attach_evidence_no_hit_fields():
    assert attach_evidence(_llm_signal(), None).evidence is None


def test_attach_evidence_resolver_none_sentinel_means_no_stats():
    assert attach_evidence(_llm_signal(), RESOLVER_NONE_SENTINEL).evidence is None


def test_attach_evidence_real_bucket_survives_verified_false():
    """判别式是 hit_sample is not None,不是 verified。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert evidence is not None
    assert evidence.verified is False
    assert evidence.hit_sample == 37


def test_attach_evidence_maps_every_field_by_sentinel():
    """八个字段值互不相同:任何一对串位都必红。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert evidence.hit_rate == 0.61
    assert evidence.hit_sample == 37
    assert evidence.ci_low == 0.52
    assert evidence.ci_high == 0.71
    assert evidence.baseline_excess == 0.09
    assert evidence.ci_low_corrected == 0.48
    assert evidence.family_size == 23
    assert evidence.verified is False


def test_attach_evidence_drops_risk_metrics_and_oos():
    """两生产者形状不同且为描述性统计,不进契约。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert not hasattr(evidence, "risk_metrics")
    assert not hasattr(evidence, "oos")


def test_attach_evidence_raises_on_horizon_mismatch():
    """把 5 根窗口的统计附到 10 根窗口的信号上是编程错误,必须响,不静默。"""
    mismatched = {**RESOLVER_REAL_BUCKET, "horizon": 5}
    with pytest.raises(ValueError, match="horizon"):
        attach_evidence(_llm_signal(), mismatched)


def test_attach_evidence_returns_new_object():
    original = _llm_signal()
    updated = attach_evidence(original, RESOLVER_REAL_BUCKET)
    assert updated is not original
    assert original.evidence is None


def test_builder_module_declares_no_config_or_db_dependency():
    """构造器纯度。

    **不**用 monkeypatch 打 get_config:它根本不在本模块的调用路径上,打一个永不被
    调的桩,任何实现都能通过——那是恒真测试。改测「这个文件 import 了什么」这个
    可判定的事实。
    """
    source = (REPO_ROOT / "src" / "services" / "trade_signal_builder.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)

    # 前缀 + 末段精确匹配,不用子串:子串判据里的 "_repo" 迟早会误伤
    # "src.schemas.report_schema" 这类合法名字。
    forbidden_prefixes = ("src.config", "src.storage", "src.repositories", "sqlalchemy")
    forbidden_names = {"get_config"}
    offenders = sorted(
        name for name in imported
        if name.startswith(forbidden_prefixes) or name.rsplit(".", 1)[-1] in forbidden_names
    )
    assert not offenders, f"构造器不得依赖 config / DB:{offenders}"
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: FAIL — `ImportError: cannot import name 'attach_evidence'`

- [ ] **Step 3: 写实现**

在 `src/services/trade_signal_builder.py` 末尾追加:

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_trade_signal_builder.py -v
```

Expected: PASS(约 28 项)

- [ ] **Step 5: 提交**

```bash
git add src/services/trade_signal_builder.py tests/test_trade_signal_builder.py
git commit -m "feat: 新增 attach_evidence(resolve_marker_hit_fields 输出 -> SignalEvidence)。判别有无证据用 hit_sample is not None 而非 verified:resolver 的缺桶哨兵与「有桶但未通过超额判定」都是 verified=False,后者带真实样本不该被丢;horizon 不匹配时抛错不静默;risk_metrics/oos 丢弃。构造器纯度改为 AST 依赖面断言,不再 monkeypatch 一个永不被调的 get_config(恒真测试)"
```

---

### Task 7: 四条 drift-lock

**Files:**
- Create: `tests/test_trade_signal_contract_locks.py`

**Interfaces:**
- Consumes: Task 2–6 的全部产物;`SUPPORTED_INTERVALS`(`src/core/intraday_backtest.py:14`);`BASELINE_SIGNAL_TYPE`(`src/services/signal_backtest.py:37`)
- Produces: 无(纯测试)

已核实前置事实:改动前全仓非测试 `.py` 中**没有任何文件**含子串 `trade_signal`,故白名单在 Task 2 之后才会有命中。

- [ ] **Step 1: 写测试(本 task 的实现已在 Task 2–6 完成,故测试应直接通过——先跑一遍确认它真能红)**

创建 `tests/test_trade_signal_contract_locks.py`:

```python
# -*- coding: utf-8 -*-
"""Inc 0 TradeSignal 契约的四条 drift-lock。

三条 canonical-derived(两边来源不同,非 tautology),一条 import 白名单(锁零接线)。
"""

from pathlib import Path
from typing import get_args

import pytest

from src.core.intraday_backtest import SUPPORTED_INTERVALS
from src.schemas.trade_signal import RESERVED_SIGNAL_TYPE, Invalidation, SignalInterval
from src.services.signal_backtest import BASELINE_SIGNAL_TYPE
from src.services.trade_signal_builder import build_from_price_levels
from src.services.volume_price_signals import PriceLevels

REPO_ROOT = Path(__file__).resolve().parents[1]

# 扫描时跳过的目录。tests/ 必须跳过(本文件自身就满篇 trade_signal)。
_SKIP_DIR_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "tests", "build", "dist", ".worktrees",
}

# 当前允许提及 TradeSignal 的**全部**非测试模块。
# 接线到任何 runtime 路径前,先读 docs/trade-signal-contract.md 的呈现边界;
# 修改本白名单是一个显式、可 diff、可审计的动作。
ALLOWED_MODULES = {
    "src/schemas/trade_signal.py",
    "src/services/trade_signal_builder.py",
}


def _non_test_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if any(part in _SKIP_DIR_PARTS for part in relative.parts):
            continue
        yield relative.as_posix(), path


def test_trade_signal_import_allowlist():
    """零接线不变式:TradeSignal 不被任何 runtime 路径消费。

    合规档 (a) 自用/内部 下,信号只能呈现为「分析结论」而非「操作指令」。Inc 0 靠
    「压根不呈现」满足这条边界。白名单强于黑名单:它连 main.py、scripts/ 以及任何
    没人预先想到的路径一并管住。
    """
    hits = set()
    for relative, path in _non_test_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "trade_signal" in text:
            hits.add(relative)

    # 阳性对照:扫描器必须活着。没有这条,当扫描逻辑写坏(路径拼错、编码异常吞掉
    # 全部文件、rglob 模式失效)时 hits 会是空集,而空集永远 ⊆ 白名单,这条 lock
    # 就悄无声息地永远绿。
    assert "src/services/trade_signal_builder.py" in hits, "扫描器失效:阳性对照未命中"

    unexpected = sorted(hits - ALLOWED_MODULES)
    assert not unexpected, (
        "TradeSignal 已被接线到白名单之外的模块:"
        f"{unexpected};接线前请先读 docs/trade-signal-contract.md 的呈现边界"
    )


def test_signal_interval_matches_supported_intervals():
    """SignalInterval 是新拼写,由本 lock 钉死在回测模块的规范集合上。"""
    assert set(get_args(SignalInterval)) == set(SUPPORTED_INTERVALS)


def test_reserved_signal_type_pinned_to_backtest_sentinel():
    """schema 层不能 import 回测模块(层级倒挂),故 "__baseline__" 是字面量。"""
    assert RESERVED_SIGNAL_TYPE == BASELINE_SIGNAL_TYPE


def test_rule_path_field_mapping_sentinels():
    """哨兵值互不相同、互不为倍数:兄弟字段串位、漏字段必红。

    risk_reward 的哨兵取 99.0 而非真值 2.0:若取 2.0,则「构造器错误地读了
    levels.risk_reward」与「按公式重算」给出同一个数,断言失去区分力。
    """
    levels = PriceLevels(entry=11.0, stop=7.0, target=19.0, risk_reward=99.0)
    signal = build_from_price_levels(
        levels,
        code="600519",
        market="cn",
        signal_type="breakout",
        interval="1d",
        horizon_bars=10,
        as_of="2026-07-10T00:00:00",
        confidence="high",
        invalidation=Invalidation(note="哨兵"),
        current_price=13.0,
    )
    assert signal is not None
    assert signal.entry_zone.low == 11.0
    assert signal.entry_zone.high == 11.0
    assert signal.stop == 7.0
    assert signal.targets == [19.0]
    assert signal.risk_reward == pytest.approx(2.0)   # (19-11)/(11-7);证明 99.0 被无视
```

- [ ] **Step 2: 跑测试确认四条都过**

```bash
python -m pytest tests/test_trade_signal_contract_locks.py -v
```

Expected: PASS(4 项)

- [ ] **Step 3: 验证每条 lock 真能红(变异测试,验证后必须还原)**

逐条制造变异,确认对应 lock 变红,然后**用文件备份还原,不要用 `git checkout --`**(会冲掉未提交实现):

```bash
cp src/schemas/trade_signal.py /tmp/ts_backup.py

# 变异 A:白名单 —— 让一个非白名单模块提及它
#   用一次性探针文件,**不要**改任何既有源文件:Global Constraints 写明
#   唯一允许触碰的既有源文件是 src/schemas/analysis_context_pack.py。
echo "# trade_signal" > src/_lock_probe.py
python -m pytest tests/test_trade_signal_contract_locks.py::test_trade_signal_import_allowlist -q
# Expected: FAIL,报出 src/_lock_probe.py
rm -f src/_lock_probe.py
git status --porcelain src/ | grep . && echo "警告:src/ 有残留" || echo "还原 OK"

# 变异 B:阳性对照 —— 把扫描根指向一个空目录,断言仍会红
#   (改 _SKIP_DIR_PARTS 加入 "src" 即可模拟扫描器失效)
sed -i 's/"tests", "build", "dist", ".worktrees",/"tests", "build", "dist", ".worktrees", "src",/' tests/test_trade_signal_contract_locks.py
python -m pytest tests/test_trade_signal_contract_locks.py::test_trade_signal_import_allowlist -q
# Expected: FAIL "扫描器失效:阳性对照未命中"
sed -i 's/"tests", "build", "dist", ".worktrees", "src",/"tests", "build", "dist", ".worktrees",/' tests/test_trade_signal_contract_locks.py

# 变异 C:interval 集合
sed -i 's/SignalInterval = Literal\["1d", "1m", "5m", "15m", "1h"\]/SignalInterval = Literal["1d", "1m", "5m", "15m"]/' src/schemas/trade_signal.py
python -m pytest tests/test_trade_signal_contract_locks.py::test_signal_interval_matches_supported_intervals -q
# Expected: FAIL
cp /tmp/ts_backup.py src/schemas/trade_signal.py

# 变异 D:保留哨兵
sed -i 's/RESERVED_SIGNAL_TYPE = "__baseline__"/RESERVED_SIGNAL_TYPE = "__base__"/' src/schemas/trade_signal.py
python -m pytest tests/test_trade_signal_contract_locks.py::test_reserved_signal_type_pinned_to_backtest_sentinel -q
# Expected: FAIL
cp /tmp/ts_backup.py src/schemas/trade_signal.py

# 变异 E:哨兵映射 —— 让构造器错误地读 levels.risk_reward
#   (手改 build_from_price_levels 里的 risk_reward property 无法直接改;
#    改为把 targets 写成 [float(levels.stop)] 制造串位)
cp src/services/trade_signal_builder.py /tmp/tsb_backup.py
sed -i 's/targets=\[float(levels.target)\],/targets=[float(levels.entry) * 2],/' src/services/trade_signal_builder.py
python -m pytest tests/test_trade_signal_contract_locks.py::test_rule_path_field_mapping_sentinels -q
# Expected: FAIL(targets == [22.0] != [19.0])
cp /tmp/tsb_backup.py src/services/trade_signal_builder.py

# 全部还原后确认干净
python -m pytest tests/test_trade_signal_contract_locks.py -q
git status --porcelain
rm -f /tmp/ts_backup.py /tmp/tsb_backup.py
```

Expected:五个变异各自让对应 lock 变红;还原后 4 项全过,`git status --porcelain` 只显示新测试文件未跟踪。

- [ ] **Step 4: 提交**

```bash
git add tests/test_trade_signal_contract_locks.py
git commit -m "test: 新增 TradeSignal 契约四条 drift-lock(import 白名单锁零接线并配阳性对照防扫描器失效后空集恒绿、SignalInterval 钉死 SUPPORTED_INTERVALS、RESERVED_SIGNAL_TYPE 钉死 BASELINE_SIGNAL_TYPE、规则路径哨兵映射用 risk_reward=99.0 使「不读 levels.risk_reward 而重算」可测);五个变异逐条验证过各 lock 真能红"
```

---

### Task 8: 呈现契约文档 + CHANGELOG

**Files:**
- Create: `docs/trade-signal-contract.md`
- Modify: `docs/CHANGELOG.md:12`(在 `## [Unreleased]` 下方插入首条)

**Interfaces:**
- Consumes: 全部
- Produces: 无代码

- [ ] **Step 1: 写呈现契约文档**

创建 `docs/trade-signal-contract.md`:

````markdown
# TradeSignal 契约

canonical 可执行信号契约。定义在 `src/schemas/trade_signal.py`,只读构造器在 `src/services/trade_signal_builder.py`。

设计文档:`docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md`

## 1. 当前状态:零接线

`TradeSignal` 目前**不被任何 runtime 路径消费**。它不接 API、不接报告、不接通知、不落库、不加配置项、不动前端。

`tests/test_trade_signal_contract_locks.py::test_trade_signal_import_allowlist` 用 import 白名单强制这一点:全仓非测试 `.py` 中提及 `trade_signal` 的模块必须 ⊆

```
src/schemas/trade_signal.py
src/services/trade_signal_builder.py
```

**接线前先读第 2 节。** 修改白名单是一个显式、可 diff、可审计的动作。

## 2. 呈现边界(合规约束)

当前合规定位是 **(a) 自用 / 内部**(`docs/strategy-actionable-signal-system.md` §3,2026-07-03 拍板,可升级)。在此档下:

1. `TradeSignal` 的任何用户可见渲染必须以「**分析结论**」措辞出现,**不得**表述为「操作指令」。
2. **不得**为 `TradeSignal` 新增任何对外信号分发 / 推送渠道。既有 `src/notification.py` 已被战略 §3 判定为「自用推送,不构成对外分发」,不在此限。
3. 升级到 (b) 持牌 或 (c) 教育免责档之前,以上两条持续生效。

Inc 0 靠「压根不呈现」满足这条边界。Inc 4 接线时,本节即为呈现规则。

## 3. 字段

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `code` | `str` | 标的代码 |
| `market` | `"cn" \| "hk" \| "us" \| "crypto"` | 小写归一(边界处 `.strip().lower()`) |
| `signal_type` | `str` | 自由字符串;拒绝保留哨兵 `"__baseline__"` |
| `interval` | `"1d" \| "1m" \| "5m" \| "15m" \| "1h"` | 与 `SUPPORTED_INTERVALS` 同集合 |
| `horizon_bars` | `int > 0` | 与 `signal_stats.horizon` 同量纲;`evidence` 靠它定位统计桶 |
| `as_of` | `str` | 完整 ISO-8601 datetime(必须含 `T`);日线写 `T00:00:00` |
| `source` | `"rule" \| "llm"` | 由构造器固定 |
| `direction` | `"long" \| "short"` | 持仓方向。观望 / 无信号 → 不产生对象 |
| `entry_zone` | `PriceZone{low, high}` | 入场区间;规则路径退化为一点 |
| `stop` | `float > 0` | 已入场后的止损 |
| `targets` | `list[float]`,非空、严格单调 | 多个止盈目标 |
| `position_size` | `Optional[float] > 0` | 权益比例;允许 > 1 表示杠杆。`None` = 尚未定量 |
| `confidence` | `"high" \| "medium" \| "low"` | 定性判据;与 `SignalMarker.confidence` 同 token |
| `invalidation` | `Invalidation{price, valid_until, note}` | 信号本身作废的条件,至少一项非空 |
| `action` | `Optional[DecisionAction]` | 八态投影,不新增词表 |
| `evidence` | `Optional[SignalEvidence]` | 样本外统计。`None` = 无历史统计路径 |
| `risk_reward` | `float`(只读 `@property`) | 取最差入场:`long` 用 `zone.high`、`short` 用 `zone.low` |

方向感知的排序不变式:

```
long : stop < entry_zone.low <= entry_zone.high < targets[0] < targets[1] < ...
short: stop > entry_zone.high >= entry_zone.low > targets[0] > targets[1] > ...
```

`invalidation.price` 的方向语义由 `direction` 决定:`long` → 收盘价 ≤ `price` 即失效;`short` → 收盘价 ≥ `price` 即失效。

## 4. canonical ↔ 遗留投影映射表

`TradeSignal` 是 canonical。以下四种是**遗留投影**,本增量一个不动;构造器是唯一的适配入口。

| canonical | `PriceLevels` | `SniperPoints` | `PriceLines` | `key_levels` |
| --- | --- | --- | --- | --- |
| `entry_zone.low` | `entry` | `min(ideal_buy, secondary_buy)` | `entry` | `support` |
| `entry_zone.high` | `entry` | `max(ideal_buy, secondary_buy)` | `entry` | — |
| `stop` | `stop` | `stop_loss` | `stop` | `stop_loss` |
| `targets[0]` | `target` | `take_profit` | `target` | `resistance` |
| `risk_reward` | `risk_reward`(既有,`build_price_lines` 处丢弃) | — | — | — |
| `direction` | 隐含 `long` | 隐含 `long` | 隐含 `long` | — |

位置:`PriceLevels` = `src/services/volume_price_signals.py:352`;`SniperPoints` = `src/schemas/report_schema.py:128`;`PriceLines` = `api/v1/schemas/stocks.py:139`;`key_levels` = `src/agent/protocols.py:139`。

`key_levels` 仅供对照(agent 路径),不为它写构造器。

## 5. 继承来的解析行为

LLM 路径的四个价位一律走 `parse_sniper_value`(`src/sniper_parsing.py:13`)。它的既有行为原样继承,**不在本增量修改**:

- `"18.50-19.00"` → `19.0`。区间字符串塌缩到**上界**(取最后一个数)。
- 守卫不对称:数值入口有 `> 0` 守卫,**字符串入口没有**。`"0"` → `0.0`,`"-5"` → `-5.0`,`"inf"` → `inf`,`"nan"` → `nan`。

因此 `parse_sniper_value` 返回非 `None` **不蕴含**返回值为有限正数。构造器用 `trade_levels_invalid` 自守。

## 6. 已知缺口

1. **`short` 有 schema、有方向感知校验器、有测试,但无构造器、无 evidence 路径。** 今天没有任何生产者产出空头价位;且回测在 `volume_price_signals.py:1598` 丢弃所有非 bullish 信号,`signal_stats` 里永远只有多头。填它是 Inc 4 的事。
2. **规则路径的 `entry_zone` 是退化点区间**(`low == high`)。真正的两端区间只有 LLM 路径(靠 `secondary_buy`)才有。
3. **`invalidation` 是构造器的必填入参。** Inc 0 不发明失效语义:规则路径今天不产出任何可判定的失效条件。
4. **无 `tradability` 槽位。** A股 涨跌停 / T+1 / 最小手数在代码里**不存在**(只活在 `src/market_context.py` 的 LLM prompt 散文里);`ggt_eligible` 是标的属性而非信号属性。契约绝不声称引擎没实现的约束。Inc 5 追加。
5. **`derive_price_levels` 的 20 / 14 窗口硬编码**,不受 `VPSConfig` 的 crypto / interval 覆盖影响。`TradeSignal` 原样继承。

## 7. 扩展点

| 增量 | 追加内容 | 破坏性 |
| --- | --- | --- |
| Inc 4 决策收敛层 | `TradeSignal` 的真实生产者(含 short) | 无(新增调用方) |
| Inc 5 仓位 / 制度可执行性 | 填 `position_size`;追加 `tradability: Optional[...] = None` | 无(追加可空字段) |
| Inc 6 生命周期追踪 | 持久化 + 状态机 + 失效告警 | 需新建表 |

字段命名判别原则:**战略 `docs/strategy-actionable-signal-system.md:72` 钦定的字段必须存在,即使当前没有生产者;钦定之外的推测性字段一律不留槽位。**
````

- [ ] **Step 2: 追加 CHANGELOG 条目**

在 `docs/CHANGELOG.md` 的 `## [Unreleased]` 与其下第一条之间(即当前第 12 行之前)插入**一行**,保持扁平格式,**不加** `### 类目标题`:

```markdown
- [新功能] 新增 canonical TradeSignal 契约(`src/schemas/trade_signal.py`)与两条只读构造器(`src/services/trade_signal_builder.py`):统一「方向/入场区间/止损/目标位/仓位/置信度/时效/失效条件」八字段,止损与目标沿用既有 ATR 波动率自适应价位,LLM 路径首次把 `ideal_buy` 与 `secondary_buy` 组装成入场区间两端。当前为零接线内部契约,不接入 API/报告/通知/持久化,不新增配置项,不改变任何现有载荷;呈现边界与已知缺口见 `docs/trade-signal-contract.md`。
```

- [ ] **Step 3: 核对文档里的命令、路径、行号与实际仓库一致**

```bash
cd /root/dsa-trade-signal
test -f src/schemas/trade_signal.py && test -f src/services/trade_signal_builder.py && echo "源文件 OK"
grep -n 'class SniperPoints' src/schemas/report_schema.py
grep -n 'class PriceLevels' src/services/volume_price_signals.py
grep -n 'class PriceLines' api/v1/schemas/stocks.py
grep -n 'key_levels' src/agent/protocols.py | head -1
grep -n 'def parse_sniper_value' src/sniper_parsing.py
grep -c '^### ' docs/CHANGELOG.md   # [Unreleased] 段内不得新增类目标题
```

Expected:源文件存在;`report_schema.py:128`、`volume_price_signals.py:352`、`stocks.py:139`、`protocols.py:139`、`sniper_parsing.py:13`;`### ` 计数与改动前一致。

- [ ] **Step 4: 跑全量门禁**

```bash
cd /root/dsa-trade-signal
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
./scripts/ci_gate.sh
echo "ci_gate exit=$?"
```

Expected:`exit=0`,`==> backend-gate: all checks passed`,pytest 计数 ≥ 4113 + 新增(约 4113 + 87),flake8 critical = 0。**不要** `| tail`。

- [ ] **Step 5: 提交**

```bash
git add docs/trade-signal-contract.md docs/CHANGELOG.md
git commit -m "docs: 新增 TradeSignal 呈现契约文档(零接线不变式与 import 白名单、合规 (a) 自用档下的呈现边界、canonical 与四种遗留投影的映射表、parse_sniper_value 继承行为、五条已知缺口、Inc 4/5/6 扩展点与字段命名判别原则),CHANGELOG 扁平条目一行"
```

---

## Self-Review

**1. Spec coverage**

| spec 章节 | 落在哪个 task |
| --- | --- |
| §4 分层落位 | Task 2(schema)/ Task 4(services) |
| §5.1 类型别名 | Task 2 |
| §5.2 子模型 | Task 2 |
| §5.3 `TradeSignal` | Task 3 |
| §5.4 `trade_levels_invalid` | Task 2 |
| §5.5 `risk_reward` `@property` | Task 3 |
| §5.6 五条克制 | Task 2(3、5)/ Task 3(2、4 的判别原则写进 Task 8 文档) |
| §5.7 `market` 归一 | Task 3 |
| §5.8 ISO-8601 | Task 1 + Task 2 + Task 3 |
| §6.1 `build_from_price_levels` | Task 4 |
| §6.2 `build_from_sniper_points` | Task 5 |
| §6.3 `attach_evidence` | Task 6 |
| §6.4 单一判据 | Task 2(定义)+ Task 5(调用) |
| §7 映射表 | Task 8 |
| §8 / §8.1 不复用理由 | Task 2 的 docstring + Task 8 文档 |
| §9 轻触既有文件 | Task 1 |
| §10.1–10.4 四条 drift-lock | Task 7 |
| §11.1 测 1–12 | Task 1–3 |
| §11.2 测 13–20 | Task 4–6 |
| §11.3 测 21–24 | Task 7 |
| §11.4 门禁 | Task 8 Step 4 |
| §12 验收 | Task 8 Step 3–4 |
| §13.1 已知缺口 | Task 8 文档 §6 |

无遗漏。

**2. Placeholder scan**:无 TBD / TODO / 「类似 Task N」/ 「加上适当的错误处理」。每个代码步骤都给出完整可运行代码。

**3. Type consistency**

- `trade_levels_invalid(*, direction, zone_low, zone_high, stop, targets)` —— Task 2 定义,Task 3 与 Task 5 按同一签名调用。
- `is_positive_finite(value)` —— Task 2 定义,Task 5 调用。
- `build_from_price_levels(levels, *, ..., current_price=None, ...)` —— Task 4 定义,Task 7 按同一签名调用。
- `build_from_sniper_points(sniper, *, ...)` —— Task 5 定义,Task 6 的 `_llm_signal()` 调用。
- `attach_evidence(signal, hit_fields)` —— Task 6 定义。
- `RESERVED_SIGNAL_TYPE` / `SignalInterval` —— Task 2 定义,Task 3、Task 7 引用。
- `Invalidation` / `PriceZone` / `SignalEvidence` / `TradeSignal` —— Task 2、3 定义,Task 4–7 引用。
- 测试辅助 `COMMON` / `_invalidation()` / `_sniper()` / `_ohlcv()` 在 `tests/test_trade_signal_builder.py` 内定义一次,Task 5、6 复用;`BASE` / `_signal()` / `_long()` / `_short()` 在 `tests/test_trade_signal.py` 内定义一次,Task 3 复用。

一致。
