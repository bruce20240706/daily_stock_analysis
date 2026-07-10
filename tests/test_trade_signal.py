# -*- coding: utf-8 -*-
"""Inc 0 canonical TradeSignal 契约:schema 与校验器测试。

设计见 docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md。
"""

import pytest

from src.schemas import analysis_context_pack
from src.schemas.trade_signal import (
    Invalidation,
    PriceZone,
    RESERVED_SIGNAL_TYPE,
    SignalEvidence,
    TradeSignal,
    is_positive_finite,
    trade_levels_invalid,
)

INF = float("inf")
NAN = float("nan")


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
