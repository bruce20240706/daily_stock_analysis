"""AkshareFetcher.get_intraday_data(stock_zh_a_hist_min_em,免费 A股分钟兜底)。

通过 sys.modules 注入 fake akshare,验证 interval→period 映射、中文列重命名、
共享标准化,以及不支持 interval 抛 NotImplementedError、空结果抛 DataFetchError。
"""
import sys
import types

import pandas as pd
import pytest

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetchError


def _install_fake_ak(monkeypatch, captured, rows=None):
    fake_ak = types.SimpleNamespace()

    def _min(symbol, period, start_date, end_date, adjust):
        captured.update(symbol=symbol, period=period, start_date=start_date,
                        end_date=end_date, adjust=adjust)
        if rows is not None:
            return pd.DataFrame(rows)
        return pd.DataFrame([
            {"时间": "2024-01-15 09:31:00", "开盘": 1700, "收盘": 1702, "最高": 1705,
             "最低": 1699, "成交量": 10, "成交额": 1},
            {"时间": "2024-01-15 09:30:00", "开盘": 1700, "收盘": 1700, "最高": 1703,
             "最低": 1698, "成交量": 12, "成交额": 1},
        ])

    fake_ak.stock_zh_a_hist_min_em = _min
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    return fake_ak


def test_akshare_intraday_period_mapping_and_normalize(monkeypatch):
    captured = {}
    _install_fake_ak(monkeypatch, captured)
    f = AkshareFetcher()
    df = f.get_intraday_data("600519", interval="5m", start_date="2024-01-15", end_date="2024-01-16", days=1)
    assert captured["period"] == "5"            # 1h→'60'
    assert captured["symbol"] == "600519"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert df.iloc[0]["close"] == 1700.0 and list(df["datetime"]) == sorted(df["datetime"])
    assert "ma20" not in df.columns


def test_akshare_intraday_1h_maps_60(monkeypatch):
    captured = {}
    _install_fake_ak(monkeypatch, captured)
    f = AkshareFetcher()
    f.get_intraday_data("600519", interval="1h", days=1)
    assert captured["period"] == "60"


def test_akshare_intraday_unsupported_interval_raises(monkeypatch):
    captured = {}
    _install_fake_ak(monkeypatch, captured)
    f = AkshareFetcher()
    with pytest.raises(NotImplementedError):
        f.get_intraday_data("600519", interval="3m", days=1)


def test_akshare_intraday_1m_fail_closed(monkeypatch):
    """1m 故意不兜底:东财 period='1' 走 trends2(ndays=5、忽略 start/end),
    历史窗口会取回最近 5 日错数据,故 akshare 对 1m 直接抛 NotImplementedError(仅 Tushare 支持 1m)。"""
    captured = {}
    _install_fake_ak(monkeypatch, captured)
    f = AkshareFetcher()
    with pytest.raises(NotImplementedError):
        f.get_intraday_data("600519", interval="1m", start_date="2024-01-15", days=1)
    assert captured == {}  # 未触达东财调用,fail-closed 于映射阶段


def test_akshare_intraday_empty_raises(monkeypatch):
    captured = {}
    _install_fake_ak(monkeypatch, captured, rows=[])
    f = AkshareFetcher()
    with pytest.raises(DataFetchError):
        f.get_intraday_data("600519", interval="5m", days=1)
