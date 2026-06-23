"""get_intraday_data 真实联网观测测试(-m network,非阻断)。

默认走 Binance 公共数据域 data-api.binance.vision(规避主站 api.binance.com 的地区
限制 451;仓库已在 .env.example 与 docs/crypto-guide.md 文档化该 fallback)。端点不
可达/抖动时重试数次后 skip,不作为阻断门禁。覆盖此前仅离线 mock 验证的真实行为:
分钟 K 线 schema/精度、历史窗口锚定、多页分页。
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

pytestmark = pytest.mark.network

_PUBLIC_BINANCE = "https://data-api.binance.vision"
_CONN_HINTS = ("name resolution", "Connection", "Max retries", "timed out", "451", "Temporary failure")


def _fetcher(monkeypatch):
    # 主站部分地区 451;统一切公共数据域(已文档化的 fallback),除非外部已显式设置
    monkeypatch.setenv("BINANCE_BASE_URL", os.getenv("BINANCE_BASE_URL", _PUBLIC_BINANCE))
    from data_provider.binance_fetcher import BinanceFetcher
    return BinanceFetcher()


def _fetch_or_skip(fn, retries=5, delay=2.0):
    last = None
    for _ in range(retries):
        try:
            return fn()
        except Exception as e:  # 主要是 requests.ConnectionError / DNS 抖动
            last = e
            if any(k in str(e) for k in _CONN_HINTS):
                time.sleep(delay)
                continue
            raise
    pytest.skip(f"Binance 公共端点不可达,跳过观测: {last}")


def test_intraday_5m_real_ohlc_and_precision(monkeypatch):
    """真实 5m K 线:返回纯 OHLCV+datetime(无指标),时分精度完整,5 分钟间距。"""
    f = _fetcher(monkeypatch)
    df = _fetch_or_skip(lambda: f.get_intraday_data("BTC/USDT", interval="5m", days=1))
    assert not df.empty
    assert {"open", "high", "low", "close", "volume", "datetime"} <= set(df.columns)
    assert "ma20" not in df.columns and "atr" not in df.columns  # 分钟跳过技术指标
    dts = pd.to_datetime(df["datetime"]).sort_values().reset_index(drop=True)
    assert dts.nunique() == len(dts)  # 全部时间戳不同(无 datetime 精度坍缩)
    midnight = int(((dts.dt.hour == 0) & (dts.dt.minute == 0) & (dts.dt.second == 0)).sum())
    assert midnight <= 1  # 至多 1 根恰逢 00:00,而非全部坍缩到午夜
    diffs = dts.diff().dropna().dt.total_seconds()
    assert (diffs == 300).mean() > 0.9  # 绝大多数相邻 bar 间隔 300s


def test_intraday_historical_anchor(monkeypatch):
    """start_date(历史日期)锚定:首根 bar 落在该日附近,而非最近 N 天。"""
    f = _fetcher(monkeypatch)
    start = (datetime.now(timezone.utc) - timedelta(days=10)).date()
    df = _fetch_or_skip(
        lambda: f.get_intraday_data("BTC/USDT", interval="5m", start_date=start.isoformat(), days=1)
    )
    first = pd.to_datetime(df["datetime"]).min()
    assert abs((first.date() - start).days) <= 2  # 锚定历史 start_date
    assert (datetime.now(timezone.utc).date() - first.date()).days >= 7  # 不是 now-days 最近窗口


def test_intraday_multipage_paging(monkeypatch):
    """1m × 1 天 = 1440 根 > 单请求 1000 → 触发多页;装配后单调、无页边界重复。"""
    f = _fetcher(monkeypatch)
    df = _fetch_or_skip(lambda: f.get_intraday_data("BTC/USDT", interval="1m", days=1))
    assert len(df) > 1000
    dts = pd.to_datetime(df["datetime"]).sort_values()
    assert dts.is_unique and dts.is_monotonic_increasing
