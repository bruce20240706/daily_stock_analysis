from src.config import Config


def test_indicators_default_enabled(monkeypatch):
    monkeypatch.delenv("CRYPTO_MARKET_INDICATORS_ENABLED", raising=False)
    cfg = Config._load_from_env()
    assert cfg.crypto_market_indicators_enabled is True


def test_indicators_env_disable(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "false")
    cfg = Config._load_from_env()
    assert cfg.crypto_market_indicators_enabled is False
