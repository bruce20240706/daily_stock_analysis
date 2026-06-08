import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


def _cfg(**kw):
    c = Config._load_from_env()
    c.crypto_new_listing_enabled = kw.get("enabled", True)
    c.crypto_new_listing_window_days = kw.get("window", 7)
    c.crypto_new_listing_sources = kw.get("sources", "okx,coinbase")
    c.crypto_new_listing_max = kw.get("max", 20)
    return c


def test_disabled_returns_empty():
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(enabled=False))
    assert svc.discover(now_ms=1_000) == []


def test_merge_dedup_by_base(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("NEW", "USDT", "NEW-USDT", "okx", now - day, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products",
                        lambda w, n: [NewListing("NEW", "USD", "NEW-USD", "coinbase", now - day, "coinbase")])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg())
    out = svc.discover(now_ms=now)
    assert len(out) == 1
    assert set(out[0]["exchanges"]) == {"okx", "coinbase"}
    assert out[0]["listed_at"] == now - day


def test_collision_split_when_far_apart(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("X", "USDT", "X-USDT", "okx", now - day, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products",
                        lambda w, n: [NewListing("X", "USD", "X-USD", "coinbase", now - 100 * day, "coinbase")])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(window=200))
    out = svc.discover(now_ms=now)
    assert len(out) == 2


def test_max_cap(monkeypatch):
    now = 1_000_000_000_000
    items = [NewListing(f"C{i}", "USDT", f"C{i}-USDT", "okx", now - i, "okx") for i in range(5)]
    monkeypatch.setattr(nl, "fetch_okx_instruments", lambda w, n: items)
    monkeypatch.setattr(nl, "fetch_coinbase_products", lambda w, n: [])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(max=2))
    out = svc.discover(now_ms=now)
    assert len(out) == 2
