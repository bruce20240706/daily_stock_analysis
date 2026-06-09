import data_provider.crypto_market_indicators as cmi


def test_fetch_fear_greed_parses_string_value(monkeypatch):
    sample = {"data": [{"value": "10", "value_classification": "Extreme Fear", "timestamp": "1780963200"}]}
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: sample)
    assert cmi.fetch_fear_greed() == {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200}


def test_fetch_fear_greed_empty_data_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": []})
    assert cmi.fetch_fear_greed() == {}


def test_fetch_fear_greed_missing_field_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": [{"value": "10"}]})
    assert cmi.fetch_fear_greed() == {}


def test_fetch_fear_greed_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cmi, "_http_get_json", boom)
    assert cmi.fetch_fear_greed() == {}
