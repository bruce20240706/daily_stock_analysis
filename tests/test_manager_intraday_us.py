"""门面 DataFetcherManager.get_intraday_data 放行美股(us → yfinance 单源)。

验证 us 经 _intraday_fetchers_for 仅保留 yfinance(override 过滤剔除 Longbridge/Finnhub/
AlphaVantage 等未覆写源);facade 路由命中 yfinance;非 crypto/cn/us(港股)抛 DataFetchError。
"""
import pandas as pd
import pytest

from data_provider.base import DataFetcherManager, DataFetchError


def _clear_cache():
    import data_provider.base as base_mod
    if hasattr(base_mod, "_INTRADAY_CACHE"):
        base_mod._INTRADAY_CACHE.clear()


def _df():
    return pd.DataFrame([{
        "code": "AAPL", "datetime": pd.Timestamp("2026-06-22 09:30:00"),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
        "amount": None, "pct_chg": 0.0,
    }])


def test_us_intraday_fetchers_only_yfinance():
    # 真实 _intraday_fetchers_for:默认(无 key)环境下 us 分钟源收敛为 yfinance 单源
    # (Longbridge/Finnhub/AlphaVantage 未配置/未覆写 get_intraday_data,均不入列)
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("AAPL")]
    assert names == ["YfinanceFetcher"]


def test_us_1m_fail_closed_at_facade():
    # 美股 1m fail-closed 应在门面层也成立:yfinance 1m 抛 NotImplementedError → 无可用结果
    # → DataFetchError(service 据此落 insufficient_data);且不发生网络请求(映射阶段即 fail)
    mgr = DataFetcherManager()
    with pytest.raises(DataFetchError):
        mgr.get_intraday_data("AAPL", interval="1m", days=3)


def test_us_facade_routes_to_yfinance(monkeypatch):
    _clear_cache()
    mgr = DataFetcherManager()

    class _YF:
        name = "YfinanceFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            return _df()

    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_YF()])
    df, src = mgr.get_intraday_data("AAPL", interval="5m", days=3)
    assert src == "YfinanceFetcher"


def test_unsupported_market_raises():
    with pytest.raises(DataFetchError):
        DataFetcherManager().get_intraday_data("HK00700", interval="5m", days=3)


def test_yfinance_excluded_from_cn_intraday():
    # yfinance 日线支持 cn,但其分钟仅服务 us;A股分钟路径必须只用 Tushare/akshare(契约不变)
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("600519")]
    assert "YfinanceFetcher" not in names
    assert set(names) <= {"TushareFetcher", "AkshareFetcher"}
