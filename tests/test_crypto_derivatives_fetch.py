import data_provider.crypto_derivatives as cd


def _fake_okx(url, params=None, headers=None):
    if "long-short-account-ratio-contract-top-trader" in url:
        return {"code": "0", "data": [["1700000300000", "0.80"]]}
    if "long-short-account-ratio" in url:
        return {"code": "0", "data": [["1700000300000", "1.20"]]}
    if "funding-rate" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0000059888"}]}
    if "mark-price" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "markPx": "62669.5"}]}
    if "open-interest" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "2861888.58", "oiCcy": "28618.88", "oiUsd": "1793545573.08"}]}
    return {}


def test_fetch_perp_metrics_parses_all(monkeypatch):
    monkeypatch.setattr(cd, "_http_get_json", _fake_okx)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert abs(out["funding_rate"] - 0.0000059888) < 1e-12
    assert "next_funding_time" not in out
    assert out["mark_price"] == 62669.5
    assert out["open_interest"] == 2861888.58
    assert out["open_interest_usd"] == 1793545573.08
    assert out["long_short_ratio"] == 1.20       # 全市场（通用端点）
    assert out["long_short_ratio_top"] == 0.80   # 大户（top-trader 端点）；两值不同 → 锁 url 前缀匹配顺序，防误路由
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


def test_okx_ratio_parses_latest_row(monkeypatch):
    monkeypatch.setattr(
        cd, "_http_get_json",
        lambda url, params=None, headers=None: {"code": "0", "data": [["1700000300000", "1.23"], ["1700000000000", "1.10"]]},
    )
    assert abs(cd._okx_ratio(cd.OKX_LS_ACCOUNT_URL, {"ccy": "BTC", "period": "5m"}) - 1.23) < 1e-12


def test_okx_ratio_structural_anomalies_return_none(monkeypatch):
    for payload in [{}, {"data": None}, {"data": []}, {"data": [["onlyts"]]}, {"data": "x"}, {"data": [123]}, {"data": [["ts", "abc"]]}]:
        monkeypatch.setattr(cd, "_http_get_json", lambda url, params=None, headers=None, p=payload: p)
        assert cd._okx_ratio(cd.OKX_LS_TOP_URL, {}) is None


def test_okx_ratio_http_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd._okx_ratio(cd.OKX_LS_ACCOUNT_URL, {}) is None


def test_fetch_perp_metrics_single_ls_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "long-short-account-ratio-contract-top-trader" in url:
            raise RuntimeError("okx top ls down")
        if "long-short-account-ratio" in url:
            return {"data": [["1700000300000", "1.20"]]}
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        if "mark-price" in url:
            return {"data": [{"markPx": "62669.5"}]}
        if "open-interest" in url:
            return {"data": [{"oi": "100.0", "oiUsd": "6000000.0"}]}
        return {}
    monkeypatch.setattr(cd, "_http_get_json", partial)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert out["long_short_ratio"] == 1.20
    assert "long_short_ratio_top" not in out      # 大户路失败 → 缺省
    assert out["funding_rate"] == 0.0001
    assert out["source"] == "okx"
