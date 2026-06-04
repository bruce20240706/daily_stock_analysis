from __future__ import annotations

from quantpick.core.errors import ErrorCode
from quantpick.strategies.loader import load_strategies, load_strategy_file


def test_load_example_strategies() -> None:
    strategies = load_strategies("config/strategies")
    assert "ma_trend" in strategies
    ma = strategies["ma_trend"]
    assert ma.top_n > 0
    assert ma.factor_weights      # non-empty


def test_load_missing_file_returns_error() -> None:
    res = load_strategy_file("config/strategies/__does_not_exist__.yaml")
    assert not res.ok
    assert res.code == ErrorCode.STRATEGY_NOT_FOUND
