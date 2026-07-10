# -*- coding: utf-8 -*-
"""Inc 0 TradeSignal 只读构造器测试。

规则路径喂**真实** derive_price_levels 的输出,LLM 路径喂**真实** parse_sniper_value,
以此证明契约能被今天的数据填满,而不是白板上的字段名。
"""

import pandas as pd
import pytest

from src.schemas.report_schema import SniperPoints
from src.schemas.trade_signal import Invalidation, TradeSignal
from src.services.trade_signal_builder import build_from_price_levels, build_from_sniper_points
from src.services.volume_price_signals import PriceLevels, derive_price_levels
from src.sniper_parsing import parse_sniper_value

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
