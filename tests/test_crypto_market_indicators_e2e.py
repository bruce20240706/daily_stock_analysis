# -*- coding: utf-8 -*-
"""端到端（离线，无网络/无 LLM）：crypto 复盘 payload 经服务链路含 market_indicators。"""
import pytest
import data_provider.crypto_market_indicators as cmi
from src.config import Config
from src.market_analyzer import MarketAnalyzer, MarketOverview


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """每个测试前后重置配置单例，确保 monkeypatched 环境变量生效。"""
    Config.reset_instance()
    yield
    Config.reset_instance()


def test_crypto_review_payload_contains_market_indicators(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "true")
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {
        "btc_dominance": 56.07, "eth_dominance": 8.98,
        "total_market_cap_usd": 2241017397766.0, "market_cap_change_24h_pct": -0.64,
        "total_volume_usd": 91620725291.0,
    })
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "Extreme Fear", "timestamp": 1})

    a = MarketAnalyzer(region="crypto")
    indicators = a._get_crypto_market_indicators()
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=indicators)

    mi = payload["market_indicators"]
    assert mi["btc_dominance"] == 56.07
    assert mi["total_market_cap_usd"] == 2241017397766.0
    assert mi["fear_greed"]["classification"] == "Extreme Fear"


def test_crypto_review_payload_omits_when_both_sources_empty(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "true")
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    a = MarketAnalyzer(region="crypto")
    indicators = a._get_crypto_market_indicators()
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=indicators)
    assert "market_indicators" not in payload
