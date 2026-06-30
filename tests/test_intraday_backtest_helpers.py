import pytest

from src.core.intraday_backtest import (
    SUPPORTED_INTERVALS,
    MARKET_TRADING_MINUTES,
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


def test_market_trading_minutes_table():
    assert MARKET_TRADING_MINUTES["crypto"] == 1440
    assert MARKET_TRADING_MINUTES["cn"] == 240


def test_bars_per_day_crypto_default_unchanged():
    assert bars_per_day("5m") == 288          # 1440//5,默认 market='crypto' 不变
    assert bars_per_day("1m") == 1440
    assert bars_per_day("1h") == 24


def test_bars_per_day_cn_market():
    assert bars_per_day("1m", "cn") == 240    # 240//1
    assert bars_per_day("5m", "cn") == 48
    assert bars_per_day("15m", "cn") == 16
    assert bars_per_day("1h", "cn") == 4      # 240//60


def test_bars_per_day_unknown_market_raises():
    with pytest.raises((KeyError, ValueError)):
        bars_per_day("5m", "jp")              # jp 未登记


def test_derive_window_bar_count_market_aware():
    assert derive_window_bar_count(10, "5m") == 2880        # crypto 默认
    assert derive_window_bar_count(10, "5m", "cn") == 480   # 10*48


def test_market_trading_minutes_us():
    assert MARKET_TRADING_MINUTES["us"] == 390


def test_bars_per_day_us_market():
    assert bars_per_day("5m", "us") == 78     # 390/5
    assert bars_per_day("15m", "us") == 26    # 390/15
    assert bars_per_day("1h", "us") == 7      # ceil(390/60)=7(末根半根)
    assert bars_per_day("1m", "us") == 390


def test_bars_per_day_ceil_keeps_crypto_cn_unchanged():
    # 整除场景 ceil==floor,既有市场值不变
    assert bars_per_day("5m") == 288 and bars_per_day("1m") == 1440 and bars_per_day("1h") == 24
    assert bars_per_day("5m", "cn") == 48 and bars_per_day("1h", "cn") == 4


def test_derive_window_bar_count_us():
    assert derive_window_bar_count(10, "5m", "us") == 780
    assert derive_window_bar_count(10, "1h", "us") == 70


def test_apply_round_trip_cost_sell_side_only():
    # 仅卖出单边(印花税 5bp) → 扣 5/100 = 0.05
    assert apply_round_trip_cost(10.0, 0.0, 0.0, sell_side_bps=5.0) == pytest.approx(9.95)


def test_apply_round_trip_cost_three_components():
    # 双边 fee 2 + slip 3 → 2*(2+3)/100 = 0.10;加卖出单边 5 → 0.05;共扣 0.15
    assert apply_round_trip_cost(10.0, 2.0, 3.0, sell_side_bps=5.0) == pytest.approx(9.85)


def test_apply_round_trip_cost_default_sell_side_byte_identical():
    # 不传 sell_side_bps:全 0 走早退守卫,字节级不变
    assert apply_round_trip_cost(10.0, 0.0, 0.0) == pytest.approx(10.0)
    # 与旧两参实现同值(7.7 - 2*(2+3)/100 = 7.6)
    assert apply_round_trip_cost(7.7, 2.0, 3.0) == pytest.approx(7.6)


def test_apply_round_trip_cost_none_passthrough_with_sell_side():
    assert apply_round_trip_cost(None, 0.0, 0.0, sell_side_bps=5.0) is None


def test_market_trading_minutes_hk():
    assert MARKET_TRADING_MINUTES["hk"] == 330


def test_bars_per_day_hk_market():
    assert bars_per_day("1m", "hk") == 330
    assert bars_per_day("5m", "hk") == 66      # (150+180)/5
    assert bars_per_day("15m", "hk") == 22
    assert bars_per_day("1h", "hk") == 6       # ceil(330/60)=6(早市末半根)


def test_derive_window_bar_count_hk():
    assert derive_window_bar_count(10, "5m", "hk") == 660   # 10*66
