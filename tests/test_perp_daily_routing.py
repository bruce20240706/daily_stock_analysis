# -*- coding: utf-8 -*-
"""perp 日线路由：选 crypto_perp 池（仅 OkxPerpetualFetcher），现货仍选 crypto 池。"""
from data_provider.base import DataFetcherManager


def _names(fetchers):
    return {f.name for f in fetchers}


def test_default_pool_includes_perp_fetcher():
    m = DataFetcherManager()
    assert "OkxPerpetualFetcher" in _names(m._get_fetchers_snapshot())


def test_filter_crypto_perp_keeps_only_perp_fetcher():
    m = DataFetcherManager()
    snap = m._get_fetchers_snapshot()
    perp = m._filter_daily_fetchers_for_market(snap, "crypto_perp")
    assert _names(perp) == {"OkxPerpetualFetcher"}


def test_filter_crypto_excludes_perp_fetcher():
    m = DataFetcherManager()
    snap = m._get_fetchers_snapshot()
    spot = m._filter_daily_fetchers_for_market(snap, "crypto")
    assert "OkxPerpetualFetcher" not in _names(spot)
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"}.issubset(_names(spot))
