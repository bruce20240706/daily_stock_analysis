import data_provider.crypto_market_indicators as cmi


def test_fetch_global_market_parses_presence_only(monkeypatch):
    sample = {"data": {
        "market_cap_percentage": {"btc": 56.07, "eth": 8.98},
        "total_market_cap": {"usd": 2241017397766.0},
        "market_cap_change_percentage_24h_usd": -0.64,
        "total_volume": {"usd": 91620725291.0},
    }}
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: sample)
    out = cmi.fetch_global_market()
    assert out["btc_dominance"] == 56.07
    assert out["eth_dominance"] == 8.98
    assert out["total_market_cap_usd"] == 2241017397766.0
    assert out["market_cap_change_24h_pct"] == -0.64
    assert out["total_volume_usd"] == 91620725291.0


def test_fetch_global_market_non_dict_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: ["unexpected"])
    assert cmi.fetch_global_market() == {}


def test_fetch_global_market_missing_fields_presence_only(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": {"market_cap_percentage": {"btc": 50.0}}})
    assert cmi.fetch_global_market() == {"btc_dominance": 50.0}


def test_fetch_global_market_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(cmi, "_http_get_json", boom)
    assert cmi.fetch_global_market() == {}
