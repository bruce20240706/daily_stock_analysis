import data_provider.crypto_market_indicators as cmi
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_payload_includes_market_indicators_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    ind = {"btc_dominance": 56.07, "fear_greed": {"value": 10, "classification": "Extreme Fear", "timestamp": 1}}
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=ind)
    assert payload["market_indicators"] == ind


def test_payload_omits_market_indicators_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators={})
    assert "market_indicators" not in payload


def test_non_crypto_get_market_indicators_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_market_indicators() == {}


def test_crypto_get_market_indicators_uses_service(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 50.0})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_market_indicators() == {"btc_dominance": 50.0}
