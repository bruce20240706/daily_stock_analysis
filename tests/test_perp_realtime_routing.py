# -*- coding: utf-8 -*-
"""perp 实时路由：命中 okx_perp -> OkxPerpetualFetcher，不落 A 股源、不命中 spot OkxFetcher。"""
from unittest.mock import MagicMock
from data_provider.base import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


def test_perp_realtime_hits_perp_fetcher(monkeypatch):
    m = DataFetcherManager()
    calls = []
    def fake_get(name, capability=None):
        calls.append(name)
        f = MagicMock()
        f.name = name
        f.get_realtime_quote.return_value = UnifiedRealtimeQuote(
            code="BTC/USDT:PERP", name="BTC/USDT:PERP", source=RealtimeSource.FALLBACK,
            price=62500.0, change_pct=1.0)
        return f
    monkeypatch.setattr(m, "_get_fetcher_by_name", fake_get)
    q = m.get_realtime_quote("BTC/USDT:PERP")
    assert q is not None and q.price == 62500.0
    assert "OkxPerpetualFetcher" in calls           # 命中 perp fetcher
    assert "OkxFetcher" not in calls                # 不命中 spot
