# -*- coding: utf-8 -*-
"""OkxPerpetualFetcher：perp code -> OKX SWAP instId；复用 OKX candles/ticker 解析。"""
from data_provider.okx_perpetual_fetcher import OkxPerpetualFetcher


def test_to_exchange_symbol_swap():
    f = OkxPerpetualFetcher()
    assert f._to_exchange_symbol("BTC/USDT:PERP") == "BTC-USDT-SWAP"
    assert f._to_exchange_symbol("eth/usdc:perp") == "ETH-USDC-SWAP"


def test_get_daily_data_parses_swap_candles(monkeypatch):
    f = OkxPerpetualFetcher()
    fake = {"code": "0", "data": [
        ["1781049600000", "62000", "63000", "61000", "62500", "100", "6200000", "6250000", "1"],
        ["1780963200000", "61000", "62000", "60000", "61500", "120", "7200000", "7380000", "1"],
    ]}
    monkeypatch.setattr(f, "_http_get", lambda url, params=None: fake)
    # BaseFetcher.get_daily_data 返回纯 DataFrame；source 由 DataFetcherManager 用 fetcher.name 组装（见 C3 路由测试）
    df = f.get_daily_data("BTC/USDT:PERP", days=5)
    assert f.name == "OkxPerpetualFetcher"
    assert list(df["code"].unique()) == ["BTC/USDT:PERP"]
    assert set(["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]).issubset(df.columns)
    assert df.iloc[-1]["close"] == 62500.0


def test_realtime_quote_swap_ticker(monkeypatch):
    f = OkxPerpetualFetcher()
    fake = {"code": "0", "data": [{"last": "62500", "open24h": "61000", "vol24h": "100",
                                   "volCcy24h": "6200000", "high24h": "63000", "low24h": "60000"}]}
    monkeypatch.setattr(f, "_http_get", lambda url, params=None: fake)
    q = f.get_realtime_quote("BTC/USDT:PERP")
    assert q is not None and q.price == 62500.0 and q.code == "BTC/USDT:PERP"
