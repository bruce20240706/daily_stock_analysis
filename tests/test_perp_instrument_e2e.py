# -*- coding: utf-8 -*-
"""perp 标的端到端：路由→perp 日线→可技术分析；与现货链一致。"""
from data_provider.base import DataFetcherManager, is_perp_code
from src.core.trading_calendar import get_market_for_stock


def test_perp_daily_data_flows(monkeypatch):
    m = DataFetcherManager()
    fake = {"code": "0", "data": [
        ["1781049600000", "62000", "63000", "61000", "62500", "100", "6200000", "6250000", "1"],
        ["1780963200000", "61000", "62000", "60000", "61500", "120", "7200000", "7380000", "1"],
    ]}
    perp = m._get_fetcher_by_name("OkxPerpetualFetcher", capability="daily_data")
    assert perp is not None
    monkeypatch.setattr(perp, "_http_get", lambda url, params=None: fake)
    df, source = m.get_daily_data("BTC/USDT:PERP", days=5)
    assert source == "OkxPerpetualFetcher"
    assert get_market_for_stock("BTC/USDT:PERP") == "crypto"
    assert len(df) == 2 and df.iloc[-1]["close"] == 62500.0
    assert is_perp_code("BTC/USDT:PERP")
