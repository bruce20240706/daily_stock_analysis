import pandas as pd
import pytest
from data_provider.resample import resample_ohlc


def _daily(dates, closes, *, opens=None, highs=None, lows=None, vols=None, amts=None):
    n = len(dates)
    return pd.DataFrame({
        "date": dates,
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes,
        "volume": vols if vols is not None else [1.0] * n,
        "amount": amts if amts is not None else [10.0] * n,
    })


def test_weekly_aggregation_ohlcv():
    df = _daily(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"],
        closes=[10, 11, 12, 13, 14, 20],
        opens=[10, 10, 10, 10, 10, 20], highs=[10, 11, 12, 13, 15, 21],
        lows=[9, 9, 9, 9, 9, 19], vols=[1, 1, 1, 1, 1, 5], amts=[100, 100, 100, 100, 100, 500],
    )
    out = resample_ohlc(df, "weekly")
    assert len(out) == 2
    wk1 = out.iloc[0]
    assert wk1["open"] == 10 and wk1["high"] == 15 and wk1["low"] == 9 and wk1["close"] == 14
    assert wk1["volume"] == 5 and wk1["amount"] == 500
    assert pd.Timestamp(wk1["date"]) == pd.Timestamp("2024-01-05")  # 该周最后交易日


def test_pct_chg_recomputed():
    out = resample_ohlc(_daily(["2024-01-01", "2024-01-08"], [100, 110]), "weekly")
    assert pd.isna(out.iloc[0]["pct_chg"])
    assert out.iloc[1]["pct_chg"] == pytest.approx(10.0)


def test_monthly_groups_by_calendar_month():
    out = resample_ohlc(_daily(["2024-01-31", "2024-02-01", "2024-02-29"], [10, 11, 12]), "monthly")
    assert len(out) == 2
    assert out.iloc[1]["close"] == 12
    assert pd.Timestamp(out.iloc[1]["date"]) == pd.Timestamp("2024-02-29")


def test_string_dates_and_missing_volume_amount():
    df = pd.DataFrame({"date": ["2024-01-01", "2024-01-08"],
                       "open": [10, 20], "high": [10, 20], "low": [10, 20], "close": [10, 20]})
    out = resample_ohlc(df, "weekly")
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    assert (out["volume"] == 0.0).all()


def test_empty_input_returns_empty_with_columns():
    out = resample_ohlc(pd.DataFrame(), "weekly")
    assert out.empty and list(out.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        resample_ohlc(_daily(["2024-01-01"], [10]), "hourly")


from data_provider.base import attach_ma_indicators


def test_attach_ma_matches_daily_formula():
    df = pd.DataFrame({"close": [10, 11, 12, 13, 14, 15], "volume": [1, 2, 3, 4, 5, 6]})
    out = attach_ma_indicators(df)
    assert out["ma5"].iloc[-1] == pytest.approx(sum([11, 12, 13, 14, 15]) / 5)
    assert "ma10" in out and "ma20" in out and "volume_ratio" in out
    assert out["volume_ratio"].iloc[0] == pytest.approx(1.0)  # 首根 shift NaN→1.0
