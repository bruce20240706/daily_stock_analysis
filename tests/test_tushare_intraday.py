"""TushareFetcher.get_intraday_data(HTTP stk_mins,A股分钟主源)。

mock `_api.stk_mins` 验证 interval→freq 映射、ts_code 转换、共享标准化(升序/数值化/
不算指标),以及空结果抛 DataFetchError、不支持 interval 抛 NotImplementedError。
"""
import pandas as pd
import pytest

from data_provider.base import DataFetchError
from data_provider.tushare_fetcher import TushareFetcher


def _fetcher_with_stub(monkeypatch, captured, rows=None):
    f = TushareFetcher()

    class _Api:
        def stk_mins(self, **params):
            captured.update(params)
            if rows is not None:
                return pd.DataFrame(rows)
            return pd.DataFrame([
                {"ts_code": "600519.SH", "trade_time": "2024-01-15 09:31:00",
                 "open": "1700", "high": "1705", "low": "1699", "close": "1702", "vol": "10", "amount": "1"},
                {"ts_code": "600519.SH", "trade_time": "2024-01-15 09:30:00",
                 "open": "1700", "high": "1703", "low": "1698", "close": "1700", "vol": "12", "amount": "1"},
            ])

    f._api = _Api()
    monkeypatch.setattr(f, "_check_rate_limit", lambda: None)
    return f


def test_tushare_intraday_freq_mapping_and_normalize(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    df = f.get_intraday_data("600519", interval="5m", start_date="2024-01-15", end_date="2024-01-16", days=1)
    assert captured["freq"] == "5min"                  # 1h→60min 等映射
    assert captured["ts_code"] == "600519.SH"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert list(df["datetime"]) == sorted(df["datetime"])     # 升序
    assert df.iloc[0]["close"] == 1700.0
    assert "ma20" not in df.columns


def test_tushare_intraday_1h_maps_60min(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    f.get_intraday_data("600519", interval="1h", days=1)
    assert captured["freq"] == "60min"


def test_tushare_intraday_start_end_datetime_suffix(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    f.get_intraday_data("600519", interval="5m", start_date="2024-01-15", end_date="2024-01-16", days=1)
    # 仅日期(len==10)补齐为带时分秒的 datetime,满足 stk_mins 的入参契约
    assert captured["start_date"] == "2024-01-15 09:00:00"
    assert captured["end_date"] == "2024-01-16 16:00:00"


def test_tushare_intraday_unsupported_interval_raises(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    with pytest.raises(NotImplementedError):
        f.get_intraday_data("600519", interval="3m", days=1)


def test_tushare_intraday_empty_raises(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured, rows=[])
    with pytest.raises(DataFetchError):
        f.get_intraday_data("600519", interval="5m", days=1)
