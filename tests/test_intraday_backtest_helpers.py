import pytest

from src.core.intraday_backtest import (
    SUPPORTED_INTERVALS,
    is_intraday_interval,
    bars_per_day,
    derive_window_bar_count,
    build_engine_version_tag,
    apply_round_trip_cost,
)


def test_is_intraday_interval():
    assert is_intraday_interval("5m") is True
    assert is_intraday_interval("1h") is True
    assert is_intraday_interval("1d") is False
    assert is_intraday_interval("bogus") is False


@pytest.mark.parametrize("interval,expected", [("1m", 1440), ("5m", 288), ("15m", 96), ("1h", 24)])
def test_bars_per_day(interval, expected):
    assert bars_per_day(interval) == expected


def test_bars_per_day_rejects_unknown():
    with pytest.raises(ValueError):
        bars_per_day("1d")      # 日线非分钟,不应走此函数
    with pytest.raises(ValueError):
        bars_per_day("7m")


def test_derive_window_bar_count():
    assert derive_window_bar_count(10, "5m") == 2880    # 10 * 288
    assert derive_window_bar_count(1, "1h") == 24


def test_build_engine_version_tag_combinations():
    assert build_engine_version_tag("v1", "1d", 1) == "v1"
    assert build_engine_version_tag("v1", "1d", 3) == "v1-x3"
    assert build_engine_version_tag("v1", "5m", 1) == "v1-5m"
    assert build_engine_version_tag("v1", "5m", 3) == "v1-5m-x3"


def test_apply_round_trip_cost_default_zero_is_noop():
    assert apply_round_trip_cost(12.5, 0.0, 0.0) == 12.5
    assert apply_round_trip_cost(None, 5.0, 5.0) is None


def test_apply_round_trip_cost_deducts_two_fills():
    # fee 5bp + slip 5bp 单边 → 一进一出 = 2*(5+5)bp = 20bp = 0.20%
    assert apply_round_trip_cost(12.5, 5.0, 5.0) == pytest.approx(12.3)


def test_supported_intervals_contains_daily_and_intraday():
    assert "1d" in SUPPORTED_INTERVALS
    assert {"1m", "5m", "15m", "1h"}.issubset(set(SUPPORTED_INTERVALS))
