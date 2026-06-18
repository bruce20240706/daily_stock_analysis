import pandas as pd
import pytest
from src.services.multi_period_resonance import (
    period_trend, resonance_level, resonance_from_daily,
)


def _pf(ma5, ma10, ma20, close):
    return pd.DataFrame([{"ma5": ma5, "ma10": ma10, "ma20": ma20, "close": close}])


def test_period_trend_bullish_needs_arrangement_and_close_confirm():
    assert period_trend(_pf(12, 11, 10, 13)) == "bullish"


def test_period_trend_arrangement_but_close_below_ma20_is_neutral():
    assert period_trend(_pf(12, 11, 10, 9)) == "neutral"


def test_period_trend_bearish():
    assert period_trend(_pf(8, 9, 10, 7)) == "bearish"


def test_period_trend_tangled_and_nan_and_empty_are_neutral():
    assert period_trend(_pf(10, 12, 11, 13)) == "neutral"
    assert period_trend(_pf(float("nan"), 11, 10, 13)) == "neutral"
    assert period_trend(pd.DataFrame()) == "neutral"


@pytest.mark.parametrize("direction,weekly,monthly,expected", [
    ("bullish", "bullish", "bullish", "weekly_monthly"),
    ("bullish", "bullish", "neutral", "weekly"),
    ("bullish", "neutral", "bullish", "none"),    # 周线门控
    ("bullish", "bearish", "bullish", "none"),
    ("bearish", "bearish", "bearish", "weekly_monthly"),
    ("bearish", "bearish", "neutral", "weekly"),
    ("neutral", "bullish", "bullish", "none"),
    (None, "bullish", "bullish", "none"),
])
def test_resonance_level(direction, weekly, monthly, expected):
    assert resonance_level(direction, weekly, monthly) == expected


def test_resonance_from_daily_neutral_skips():
    df = pd.DataFrame({"date": ["2024-01-01"], "open": [1], "high": [1], "low": [1], "close": [1]})
    assert resonance_from_daily(df, "neutral") == "none"


def test_resonance_from_daily_uptrend_is_weekly_monthly():
    dates = pd.date_range("2022-01-03", periods=400, freq="B").strftime("%Y-%m-%d").tolist()
    closes = [100 + i for i in range(400)]
    df = pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": [1] * 400, "amount": [1] * 400})
    assert resonance_from_daily(df, "bullish") == "weekly_monthly"
    assert resonance_from_daily(df, "bearish") == "none"
