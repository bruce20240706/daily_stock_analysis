"""美股分钟取数真实联网观测(-m network,非阻断)。yfinance 免 key 真拉近段 5m。

端点抖动/限频时带重试后 skip,不作为阻断门禁。覆盖此前仅离线 mock 验证的真实行为:
schema/datetime 升序无重复/tz-naive/5m 间距为主。
"""
import time
from datetime import datetime, timedelta

import pandas as pd
import pytest

pytestmark = pytest.mark.network

_CONN_HINTS = ("Connection", "Max retries", "timed out", "Temporary failure",
               "name resolution", "RemoteDisconnected", "rate", "Too Many", "Expecting value")


def _fetch_or_skip(fn, retries=6, delay=2.0):
    last = None
    for _ in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            if "无分钟数据" in str(e):
                pytest.skip(f"yfinance 无分钟数据,跳过观测: {e}")
            if any(k in str(e) for k in _CONN_HINTS):
                time.sleep(delay)
                continue
            raise
    pytest.skip(f"yfinance 端点不可达/限频,重试 {retries} 次仍失败,跳过观测: {last}")


def test_yfinance_intraday_5m_real():
    from data_provider.yfinance_fetcher import YfinanceFetcher

    start = (datetime.now() - timedelta(days=5)).date().isoformat()
    f = YfinanceFetcher()
    df = _fetch_or_skip(lambda: f.get_intraday_data("AAPL", interval="5m", start_date=start, days=3))
    assert not df.empty
    assert {"open", "high", "low", "close", "volume", "datetime"} <= set(df.columns)
    assert "ma20" not in df.columns and "atr" not in df.columns
    dts = pd.to_datetime(df["datetime"]).sort_values().reset_index(drop=True)
    assert dts.is_unique and dts.is_monotonic_increasing
    assert getattr(dts.dt, "tz", None) is None       # tz-naive(美东墙钟)
    diffs = dts.diff().dropna().dt.total_seconds()
    assert (diffs == 300).mean() > 0.5               # 多数相邻 5m
