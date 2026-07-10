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
