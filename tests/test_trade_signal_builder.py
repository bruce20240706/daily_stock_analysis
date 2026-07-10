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
