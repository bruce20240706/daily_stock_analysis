import data_provider.crypto_derivatives as cd


def _fake_okx(url, params=None, headers=None):
    if "funding-rate" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0000059888", "nextFundingTime": "1781049600000"}]}
    if "mark-price" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "markPx": "62669.5"}]}
    if "open-interest" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "2861888.58", "oiCcy": "28618.88", "oiUsd": "1793545573.08"}]}
    return {}


def test_fetch_perp_metrics_parses_all(monkeypatch):
    monkeypatch.setattr(cd, "_http_get_json", _fake_okx)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert abs(out["funding_rate"] - 0.0000059888) < 1e-12
    assert out["next_funding_time"] == 1781049600000
    assert out["mark_price"] == 62669.5
    assert out["open_interest"] == 2861888.58
    assert out["open_interest_usd"] == 1793545573.08
    assert out["source"] == "okx"


def test_fetch_perp_metrics_non_linear_quote_returns_empty(monkeypatch):
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(cd, "_http_get_json", spy)
    assert cd.fetch_perp_metrics("BTC", "USD") == {}
    assert called["n"] == 0


def test_fetch_perp_metrics_single_source_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "funding-rate" in url:
            raise RuntimeError("okx funding down")
        if "mark-price" in url:
            return {"data": [{"markPx": "62669.5"}]}
        if "open-interest" in url:
            return {"data": [{"oi": "100.0", "oiUsd": "6000000.0"}]}
        return {}
    monkeypatch.setattr(cd, "_http_get_json", partial)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert "funding_rate" not in out
    assert out["mark_price"] == 62669.5
    assert out["open_interest"] == 100.0
    assert out["source"] == "okx"


def test_fetch_perp_metrics_all_fail_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd.fetch_perp_metrics("BTC", "USDT") == {}
