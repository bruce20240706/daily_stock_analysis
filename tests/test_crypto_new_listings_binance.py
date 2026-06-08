import data_provider.crypto_new_listings as nl


def test_binance_base_assets_filters_trading(monkeypatch):
    sample = {"symbols": [
        {"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
        {"symbol": "AAABTC", "baseAsset": "AAA", "quoteAsset": "BTC", "status": "TRADING", "isSpotTradingAllowed": True},
        {"symbol": "BBBUSDT", "baseAsset": "BBB", "quoteAsset": "USDT", "status": "BREAK", "isSpotTradingAllowed": True},
        {"symbol": "CCCUSDT", "baseAsset": "CCC", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": False},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_binance_spot_base_assets()
    assert set(out.keys()) == {"AAA"}
    assert out["AAA"][1] == "USDT"          # 代表对优先 USDT


def test_binance_non_dict_or_error_returns_empty(monkeypatch):
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: ["unexpected"])
    assert nl.fetch_binance_spot_base_assets() == {}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("451")))
    assert nl.fetch_binance_spot_base_assets() == {}
