"""A股分钟取数真实联网观测测试(-m network,非阻断)。

akshare(东财)免 key,默认真拉;Tushare 仅当 TUSHARE_TOKEN 存在时真拉一条,否则 skip。
端点不可达/抖动/限频时 skip,不作为阻断门禁。覆盖此前仅离线 mock 验证的真实行为:
分钟 schema/datetime 升序无重复、5m 间距为主、历史 start_date 锚定。
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import pytest

pytestmark = pytest.mark.network

_CONN_HINTS = ("Connection", "Max retries", "timed out", "Temporary failure",
               "name resolution", "ConnectionError", "RemoteDisconnected", "限频", "频繁")


def _fetch_or_skip(fn, retries=6, delay=2.0):
    """东财免费端点抖动频繁(常需多次重连),连接类异常重试数次后再 skip;
    业务类'无分钟数据'直接 skip;其余真实异常照常抛出(不掩盖契约问题)。"""
    last = None
    for _ in range(retries):
        try:
            return fn()
        except Exception as e:  # 主要是 requests.ConnectionError / 东财抖动
            last = e
            if "无分钟数据" in str(e):
                pytest.skip(f"A股分钟无数据,跳过观测: {e}")
            if any(k in str(e) for k in _CONN_HINTS):
                time.sleep(delay)
                continue
            raise
    pytest.skip(f"A股分钟端点不可达/限频,重试 {retries} 次仍失败,跳过观测: {last}")


def _assert_intraday_shape(df: pd.DataFrame):
    assert not df.empty
    assert {"open", "high", "low", "close", "volume", "datetime"} <= set(df.columns)
    assert "ma20" not in df.columns and "atr" not in df.columns  # 分钟跳过技术指标
    dts = pd.to_datetime(df["datetime"]).sort_values().reset_index(drop=True)
    assert dts.is_unique and dts.is_monotonic_increasing
    diffs = dts.diff().dropna().dt.total_seconds()
    # 同一交易日内绝大多数相邻 bar 间隔 300s(跨午休/跨日的少数大间隔可接受)
    assert (diffs == 300).mean() > 0.5


def test_akshare_intraday_5m_real(monkeypatch):
    """akshare 免 key 真拉贵州茅台近段 5m:schema/升序/5m 间距。"""
    from data_provider.akshare_fetcher import AkshareFetcher

    start = (datetime.now() - timedelta(days=15)).date().isoformat()
    f = AkshareFetcher()
    df = _fetch_or_skip(
        lambda: f.get_intraday_data("600519", interval="5m", start_date=start, days=3)
    )
    _assert_intraday_shape(df)


def test_tushare_intraday_5m_real_if_token():
    """Tushare 仅当配置 token 时真拉一条;否则 skip(无 token / 积分不足)。"""
    if not os.getenv("TUSHARE_TOKEN"):
        pytest.skip("未配置 TUSHARE_TOKEN,跳过 Tushare 分钟联网观测")

    from data_provider.tushare_fetcher import TushareFetcher

    f = TushareFetcher()
    if not f.is_available():
        pytest.skip("Tushare 数据源不可用(token 无效),跳过")
    start = (datetime.now() - timedelta(days=15)).date().isoformat()
    df = _fetch_or_skip(
        lambda: f.get_intraday_data("600519", interval="5m", start_date=start, days=3)
    )
    _assert_intraday_shape(df)
