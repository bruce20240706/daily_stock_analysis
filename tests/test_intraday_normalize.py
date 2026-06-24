"""分钟数据共享标准化 normalize_intraday_df(crypto + A股 通用)。

约定输入 df 已含 datetime 列(各 fetcher 先从自身源构建);本函数只做数值化/去空/
升序/pct_chg/选列,不算技术指标。
"""
import pandas as pd

from data_provider.intraday_normalize import normalize_intraday_df


def test_normalize_from_string_datetime():
    raw = pd.DataFrame([
        {"datetime": "2024-01-15 09:35:00", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100"},
        {"datetime": "2024-01-15 09:30:00", "open": "10", "high": "10.5", "low": "9.5", "close": "10", "volume": "120"},
    ])
    out = normalize_intraday_df(raw, "600519")
    assert list(out["datetime"]) == sorted(out["datetime"])      # 升序
    assert out.iloc[0]["close"] == 10.0 and out.iloc[1]["close"] == 10.5
    assert {"code", "datetime", "open", "high", "low", "close", "volume", "amount", "pct_chg"} == set(out.columns)
    assert "ma20" not in out.columns
    assert (out["code"] == "600519").all()


def test_normalize_drops_null_close_volume():
    raw = pd.DataFrame([
        {"datetime": "2024-01-15 09:30:00", "open": 10, "high": 11, "low": 9, "close": None, "volume": 100},
        {"datetime": "2024-01-15 09:31:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
    ])
    out = normalize_intraday_df(raw, "600519")
    assert len(out) == 1


def test_normalize_requires_datetime_column():
    import pytest
    raw = pd.DataFrame([{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    with pytest.raises(ValueError):
        normalize_intraday_df(raw, "600519")


def test_normalize_pct_chg_first_row_zero():
    raw = pd.DataFrame([
        {"datetime": "2024-01-15 09:30:00", "open": 10, "high": 11, "low": 9, "close": 10.0, "volume": 100},
        {"datetime": "2024-01-15 09:31:00", "open": 10, "high": 11, "low": 9, "close": 11.0, "volume": 100},
    ])
    out = normalize_intraday_df(raw, "600519")
    assert out.iloc[0]["pct_chg"] == 0.0
    assert round(out.iloc[1]["pct_chg"], 4) == 10.0
