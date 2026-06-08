from datetime import datetime, timezone, timedelta
import data_provider.crypto_new_listings as nl


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_coinbase_new_at_window_and_dedup(monkeypatch):
    now = datetime(2026, 6, 8, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    sample = {"products": [
        {"product_id": "NEW-USD", "base_currency_id": "NEW", "quote_currency_id": "USD", "status": "online", "new_at": _iso(now - timedelta(days=2))},
        {"product_id": "NEW-USDC", "base_currency_id": "NEW", "quote_currency_id": "USDC", "status": "online", "new_at": _iso(now - timedelta(days=2))},
        {"product_id": "OLD-USD", "base_currency_id": "OLD", "quote_currency_id": "USD", "status": "online", "new_at": _iso(now - timedelta(days=40))},
        {"product_id": "NONE-USD", "base_currency_id": "NONE", "quote_currency_id": "USD", "status": "online", "new_at": None},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_coinbase_products(window_days=7, now_ms=now_ms)
    bases = [r.base for r in out]
    assert bases == ["NEW"]
    assert out[0].exchange == "coinbase" and out[0].source == "coinbase"


def test_coinbase_non_dict_or_error_returns_empty(monkeypatch):
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: ["unexpected"])
    assert nl.fetch_coinbase_products(7, 1) == []
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert nl.fetch_coinbase_products(7, 1) == []
