import data_provider.crypto_new_listings as nl


def test_okx_parses_listtime_within_window(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    sample = {"data": [
        {"instId": "NEW-USDT", "baseCcy": "NEW", "quoteCcy": "USDT", "state": "live", "listTime": str(now - 2 * day)},
        {"instId": "OLD-USDT", "baseCcy": "OLD", "quoteCcy": "USDT", "state": "live", "listTime": str(now - 30 * day)},
        {"instId": "PRE-USDT", "baseCcy": "PRE", "quoteCcy": "USDT", "state": "preopen", "listTime": str(now - 1 * day)},
        {"instId": "FUT-USDT", "baseCcy": "FUT", "quoteCcy": "USDT", "state": "live", "listTime": str(now + 5 * day)},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_okx_instruments(window_days=7, now_ms=now)
    bases = {r.base for r in out}
    assert bases == {"NEW"}
    assert out[0].exchange == "okx" and out[0].listed_at == now - 2 * day


def test_okx_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(nl, "_http_get_json", boom)
    assert nl.fetch_okx_instruments(7, 1_000_000_000_000) == []
