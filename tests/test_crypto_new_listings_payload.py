# -*- coding: utf-8 -*-
"""
新币上新 payload 契约测试
- 完全离线，无网络，无 LLM
- 覆盖：additive 添加、空列表不添加、非 crypto 返回 []
"""
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_payload_includes_new_listings_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-08")
    nl = [{"base": "NEW", "exchanges": ["okx"], "pairs": ["NEW-USDT"], "listed_at": 123, "price": 1.0}]
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", new_listings=nl)
    assert payload["new_listings"] == nl


def test_payload_omits_new_listings_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-08")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", new_listings=[])
    assert "new_listings" not in payload


def test_non_crypto_get_new_listings_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_new_listings() == []
