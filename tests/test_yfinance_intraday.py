"""YfinanceFetcher.get_intraday_data(美股分钟,免 key)。

sys.modules 注入 fake yfinance,验证 interval→yfinance 映射、类股 '.'→'-'、MultiIndex 拍平、
去时区(tz-naive 美东墙钟)、共享标准化,以及 1m fail-closed / 空结果抛 DataFetchError。
"""
import sys
import types

import pandas as pd
import pytest

from data_provider.yfinance_fetcher import YfinanceFetcher
from data_provider.base import DataFetchError


def _install_fake_yf(monkeypatch, captured, rows="default", tz="America/New_York"):
    fake_yf = types.SimpleNamespace()

    def _download(tickers, start=None, end=None, interval=None, auto_adjust=True, progress=False):
        captured.update(tickers=tickers, start=start, end=end, interval=interval)
        if rows == "default":
            idx = pd.to_datetime(["2026-06-22 09:30:00", "2026-06-22 09:35:00"]).tz_localize(tz)
            df = pd.DataFrame(
                {"Open": [100, 101], "High": [102, 103], "Low": [99, 100],
                 "Close": [101, 102], "Volume": [1000, 1100]},
                index=idx,
            )
            df.index.name = "Datetime"
            df.columns = pd.MultiIndex.from_product([df.columns, [tickers]])  # 新版 yfinance MultiIndex
            return df
        return pd.DataFrame(rows)

    fake_yf.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    return fake_yf


def test_yfinance_intraday_5m_normalize_and_tz_naive(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    f = YfinanceFetcher()
    df = f.get_intraday_data("AAPL", interval="5m", start_date="2026-06-22", end_date="2026-06-25", days=3)
    assert captured["interval"] == "5m" and captured["tickers"] == "AAPL"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert list(df["datetime"]) == sorted(df["datetime"])
    assert getattr(pd.to_datetime(df["datetime"]).dt, "tz", None) is None   # tz-naive(美东墙钟)
    assert "ma20" not in df.columns
    assert df.iloc[0]["close"] == 101.0


def test_yfinance_intraday_1h_maps_60m(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    YfinanceFetcher().get_intraday_data("AAPL", interval="1h", days=3)
    assert captured["interval"] == "60m"


def test_yfinance_intraday_class_share_dot_to_hyphen(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    YfinanceFetcher().get_intraday_data("BRK.B", interval="5m", days=3)
    assert captured["tickers"] == "BRK-B"   # Yahoo 用连字符


def test_yfinance_intraday_1m_fail_closed(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    with pytest.raises(NotImplementedError):
        YfinanceFetcher().get_intraday_data("AAPL", interval="1m", days=3)
    assert captured == {}   # 未触达 download(映射阶段 fail-closed)


def test_yfinance_intraday_empty_raises(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured, rows=[])
    with pytest.raises(DataFetchError):
        YfinanceFetcher().get_intraday_data("AAPL", interval="5m", days=3)
