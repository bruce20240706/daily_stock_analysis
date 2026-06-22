import pandas as pd
import pytest
from data_provider.base import attach_ma_indicators, BaseFetcher
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


def test_datetime_date_input_supported():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-08"]),
        "open": [10, 20], "high": [10, 20], "low": [10, 20], "close": [10, 20],
        "volume": [1, 1], "amount": [10, 10],
    })
    out = resample_ohlc(df, "weekly")
    assert len(out) == 2
    assert out.iloc[1]["close"] == 20


def test_attach_ma_matches_daily_formula():
    df = pd.DataFrame({"close": [10, 11, 12, 13, 14, 15], "volume": [1, 2, 3, 4, 5, 6]})
    out = attach_ma_indicators(df)
    assert out["ma5"].iloc[-1] == pytest.approx(sum([11, 12, 13, 14, 15]) / 5)
    assert "ma10" in out and "ma20" in out and "volume_ratio" in out
    assert out["volume_ratio"].iloc[0] == pytest.approx(1.0)  # 首根 shift NaN→1.0


# S1 regression: volume/amount 全为 None 时 groupby.sum() 返回 object dtype 0，
# 导致 attach_ma_indicators 内 volume/avg_volume_5.shift(1) 触发 ZeroDivisionError。
def test_resample_none_volume_no_zerodivision():
    """resample_ohlc 输出的 volume 列必须为 float64，不能是 object dtype。

    根因：groupby(...).sum() 对全 None object 列返回整数 0（object dtype），
    attach_ma_indicators 再做 float 除法时触发 ZeroDivisionError。
    修复：聚合前对 volume/amount 强制 pd.to_numeric(errors='coerce')→float64，
    确保结果为 float64（NaN-safe），attach_ma_indicators 不再触发 ZeroDivisionError。
    """
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02", "2024-01-08", "2024-01-09"],
        "open": [10.0, 11.0, 20.0, 21.0],
        "high": [10.0, 11.0, 20.0, 21.0],
        "low": [10.0, 11.0, 20.0, 21.0],
        "close": [10.0, 11.0, 20.0, 21.0],
        "volume": pd.array([None, None, None, None], dtype=object),
        "amount": pd.array([None, None, None, None], dtype=object),
    })
    out = resample_ohlc(df, "weekly")
    assert len(out) == 2
    # After fix: volume must be float64, not object
    assert out["volume"].dtype == float
    # attach_ma_indicators on the result must not raise ZeroDivisionError
    result = attach_ma_indicators(out)
    assert "volume_ratio" in result.columns


# T5: _calculate_indicators rounds ma5/ma10/ma20/volume_ratio to 2 decimals
class _MinimalFetcher(BaseFetcher):
    """Minimal concrete subclass for testing _calculate_indicators."""
    name = "_MinimalFetcher"

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return pd.DataFrame()

    def _normalize_data(self, df, stock_code):
        return df


def test_calculate_indicators_rounds_to_2_decimals():
    """BaseFetcher._calculate_indicators must round ma5/ma10/ma20/volume_ratio to 2 decimals."""
    fetcher = _MinimalFetcher()
    # Build a frame with values that produce >2 decimal places in rolling mean
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    vols = [3.0, 7.0, 2.0, 5.0, 8.0, 4.0]
    df = pd.DataFrame({
        "close": closes,
        "volume": vols,
    })
    result = fetcher._calculate_indicators(df)
    for col in ["ma5", "ma10", "ma20", "volume_ratio"]:
        if col in result.columns:
            series = result[col].dropna()
            for val in series:
                assert round(val, 2) == pytest.approx(val, abs=1e-9), (
                    f"{col} value {val} is not rounded to 2 decimals"
                )
