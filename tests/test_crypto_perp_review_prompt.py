# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪：收集方法 + prompt 事实块（zh/en，presence-only）。"""
import src.services.crypto_derivatives_review_service as svc_mod
from src.market_analyzer import MarketAnalyzer

PERP = {
    "avg_funding_rate": 0.00025,
    "total_open_interest_usd": 4000000000.0,
    "coins": [
        {"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000000000.0},
        {"symbol": "BTC/USDT", "funding_rate": 0.0001, "open_interest_usd": 1000000000.0},
    ],
}


def test_perp_prompt_block_zh_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(PERP, "zh")
    assert "永续情绪" in block
    assert "0.0250%" in block
    assert "ETH/USDT" in block
    assert "不得编造数据" in block


def test_perp_prompt_block_en_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(PERP, "en")
    assert "Perpetual Sentiment" in block
    assert "0.0250%" in block
    assert "do not invent data" in block


def test_perp_prompt_block_zero_funding_not_dropped():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(
        {"coins": [{"symbol": "BTC/USDT", "funding_rate": 0.0, "open_interest_usd": 100.0}]}, "zh")
    assert "0.0000%" in block  # 0 资金费率必须渲染，不被 falsy 丢弃


def test_perp_prompt_block_funding_na_path():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(
        {"coins": [{"symbol": "BTC/USDT", "open_interest_usd": 100.0}]}, "zh")
    assert "资金费率 N/A" in block


def test_perp_prompt_block_empty_when_no_data():
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_perp_sentiment_prompt_block({}, "zh") == ""


def test_perp_prompt_block_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_perp_sentiment_prompt_block(PERP, "zh") == ""


def test_non_crypto_get_perp_sentiment_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_perp_sentiment() == {}


def test_crypto_get_perp_sentiment_uses_service(monkeypatch):
    monkeypatch.setattr(
        svc_mod.CryptoDerivativesReviewService, "collect",
        lambda self: {"avg_funding_rate": 0.0001},
    )
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_perp_sentiment() == {"avg_funding_rate": 0.0001}
