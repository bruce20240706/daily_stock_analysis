"""门面 DataFetcherManager.get_intraday_data 放行 A股(cn 路由,Tushare 优先 akshare 兜底)。

通过桩 fetcher + monkeypatch `_intraday_fetchers_for` 验证:cn 命中 Tushare 即停、
Tushare 失败 failover 到 akshare;非 crypto/cn(美股)直接抛 DataFetchError;
另验证真实 `_intraday_fetchers_for` 对 cn 的排序(Tushare 在 akshare 之前)。
"""
import pandas as pd
import pytest

from data_provider.base import DataFetcherManager, DataFetchError


def _clear_cache():
    # 进程内分钟缓存默认 TTL=900s,跨用例同 key 会命中,需在断言路由前清空
    import data_provider.base as base_mod
    if hasattr(base_mod, "_INTRADAY_CACHE"):
        base_mod._INTRADAY_CACHE.clear()


def _df():
    return pd.DataFrame([{
        "code": "600519", "datetime": pd.Timestamp("2024-01-16 09:30:00"),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
        "amount": 1.0, "pct_chg": 0.0,
    }])


def test_cn_routes_tushare_first(monkeypatch):
    _clear_cache()
    mgr = DataFetcherManager()
    calls = []

    class _TS:
        name = "TushareFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            calls.append("ts"); return _df()

    class _AK:
        name = "AkshareFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            calls.append("ak"); return _df()

    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_TS(), _AK()])
    df, src = mgr.get_intraday_data("600519", interval="5m", days=1)
    assert src == "TushareFetcher" and calls == ["ts"]    # Tushare 命中即停


def test_cn_failover_to_akshare(monkeypatch):
    _clear_cache()
    mgr = DataFetcherManager()

    class _TS:
        name = "TushareFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            raise DataFetchError("no points")

    class _AK:
        name = "AkshareFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            return _df()

    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_TS(), _AK()])
    df, src = mgr.get_intraday_data("600519", interval="5m", days=1)
    assert src == "AkshareFetcher"


def test_non_crypto_non_cn_raises():
    with pytest.raises(DataFetchError):
        DataFetcherManager().get_intraday_data("AAPL", interval="5m", days=1)


def test_intraday_fetchers_for_cn_orders_tushare_before_akshare():
    """真实 _intraday_fetchers_for:cn 仅含实现了 get_intraday_data 的源,Tushare 排在 akshare 前。"""
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("600519")]
    # 仅 Tushare/Akshare 覆写了 get_intraday_data(Efinance/Pytdx/Baostock 用 BaseFetcher 默认,被剔除)
    assert set(names) <= {"TushareFetcher", "AkshareFetcher"}
    if "TushareFetcher" in names and "AkshareFetcher" in names:
        assert names.index("TushareFetcher") < names.index("AkshareFetcher")
    assert "AkshareFetcher" in names  # akshare 免 key 恒在
