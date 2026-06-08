from src.config import Config


def test_new_listing_defaults(monkeypatch):
    for k in ("CRYPTO_NEW_LISTING_ENABLED", "CRYPTO_NEW_LISTING_WINDOW_DAYS",
              "CRYPTO_NEW_LISTING_SOURCES", "CRYPTO_NEW_LISTING_MAX"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_enabled is True
    assert cfg.crypto_new_listing_window_days == 7
    assert cfg.crypto_new_listing_sources == "okx,coinbase"
    assert cfg.crypto_new_listing_max == 20


def test_new_listing_env_override(monkeypatch):
    monkeypatch.setenv("CRYPTO_NEW_LISTING_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_WINDOW_DAYS", "14")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_SOURCES", "okx,coinbase,binance")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_MAX", "5")
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_enabled is False
    assert cfg.crypto_new_listing_window_days == 14
    assert cfg.crypto_new_listing_sources == "okx,coinbase,binance"
    assert cfg.crypto_new_listing_max == 5


def test_new_listing_invalid_int_falls_back(monkeypatch):
    monkeypatch.setenv("CRYPTO_NEW_LISTING_WINDOW_DAYS", "notanint")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_MAX", "")
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_window_days == 7
    assert cfg.crypto_new_listing_max == 20
