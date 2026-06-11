# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪：payload 透出 + prompt 注入。"""
from src.market_analyzer import MarketAnalyzer, MarketOverview

PERP = {"avg_funding_rate": 0.00025, "total_open_interest_usd": 4000000000.0,
        "coins": [{"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000000000.0}]}


def test_payload_includes_perp_sentiment_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment=PERP)
    assert payload["perp_sentiment"] == PERP


def test_payload_omits_perp_sentiment_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment={})
    assert "perp_sentiment" not in payload


def test_review_prompt_includes_perp_block():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    prompt = a._build_review_prompt(ov, [], None, PERP)
    assert "加密永续情绪" in prompt


def test_payload_perp_sentiment_passes_long_short_ratio():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    perp = {**PERP, "avg_long_short_ratio": 1.75, "avg_long_short_ratio_top": 1.10}
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment=perp)
    assert payload["perp_sentiment"]["avg_long_short_ratio"] == 1.75
    assert payload["perp_sentiment"]["avg_long_short_ratio_top"] == 1.10
