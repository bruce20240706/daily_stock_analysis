from src.config import Config


def test_market_review_region_accepts_crypto():
    assert Config._parse_market_review_region("crypto") == "crypto"
    assert Config._parse_market_review_region("CRYPTO") == "crypto"


def test_market_review_region_invalid_still_falls_back_cn():
    assert Config._parse_market_review_region("foobar") == "cn"


def test_crypto_basket_symbols_default_and_env_override(monkeypatch):
    monkeypatch.delenv("CRYPTO_MARKET_REVIEW_SYMBOLS", raising=False)
    cfg = Config._load_from_env()
    assert "BTC/USDT" in cfg.crypto_market_review_symbols
    assert "ETH/USDT" in cfg.crypto_market_review_symbols

    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "HYPE/USDT,SUI/USDT")
    cfg2 = Config._load_from_env()
    assert cfg2.crypto_market_review_symbols == "HYPE/USDT,SUI/USDT"
