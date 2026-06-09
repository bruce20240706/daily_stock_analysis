import data_provider.crypto_market_indicators as cmi
from src.services.crypto_market_indicator_service import CryptoMarketIndicatorService


class _Cfg:
    def __init__(self, enabled=True):
        self.crypto_market_indicators_enabled = enabled


def test_collect_merges_presence_only(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 56.0, "total_market_cap_usd": 2.0e12})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200})
    out = CryptoMarketIndicatorService(config=_Cfg()).collect()
    assert out["btc_dominance"] == 56.0
    assert out["total_market_cap_usd"] == 2.0e12
    assert out["fear_greed"] == {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200}


def test_collect_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 56.0})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "X", "timestamp": 1})
    assert CryptoMarketIndicatorService(config=_Cfg(enabled=False)).collect() == {}


def test_collect_single_source_failure_keeps_other(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 5, "classification": "Extreme Fear", "timestamp": 1})
    out = CryptoMarketIndicatorService(config=_Cfg()).collect()
    assert out == {"fear_greed": {"value": 5, "classification": "Extreme Fear", "timestamp": 1}}


def test_collect_both_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    assert CryptoMarketIndicatorService(config=_Cfg()).collect() == {}
