# -*- coding: utf-8 -*-
"""Binance fapi 衍生品备援模块单测（全离线，monkeypatch bd._http_get_json）。"""
import data_provider.binance_derivatives as bd


def _fake_fapi(url, params=None, headers=None):
    if "premiumIndex" in url:
        return {"symbol": "BTCUSDT", "markPrice": "62000.0", "lastFundingRate": "0.0001"}
    if "/fapi/v1/openInterest" in url:
        return {"symbol": "BTCUSDT", "openInterest": "10.5"}
    if "topLongShortAccountRatio" in url:
        return [{"longShortRatio": "0.70"}, {"longShortRatio": "0.80"}]   # 升序，最新在末
    if "globalLongShortAccountRatio" in url:
        return [{"longShortRatio": "1.10"}, {"longShortRatio": "1.20"}]
    return {}


def test_fetch_perp_metrics_parses_all(monkeypatch):
    monkeypatch.setattr(bd, "_http_get_json", _fake_fapi)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert abs(out["funding_rate"] - 0.0001) < 1e-12
    assert out["mark_price"] == 62000.0
    assert abs(out["open_interest_usd"] - 10.5 * 62000.0) < 1e-6   # 基础币数量×标记价
    assert "open_interest" not in out                              # 张数字段 OKX 专属，永不填
    assert out["long_short_ratio"] == 1.20                         # 升序取末元素（最新）
    assert out["long_short_ratio_top"] == 0.80
    assert out["source"] == "binance"


def test_non_linear_quote_zero_calls(monkeypatch):
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(bd, "_http_get_json", spy)
    assert bd.fetch_perp_metrics("BTC", "USD") == {}
    assert bd.fetch_perp_metrics("", "USDT") == {}
    assert bd.fetch_funding_rate_history("BTC", "USD", 0, 1) == []
    assert called["n"] == 0


def test_oi_usd_requires_both_oi_and_mark(monkeypatch):
    def no_premium(url, params=None, headers=None):
        if "/fapi/v1/openInterest" in url:
            return {"openInterest": "10.5"}
        raise RuntimeError("premiumIndex down")
    monkeypatch.setattr(bd, "_http_get_json", no_premium)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert "open_interest_usd" not in out   # 缺 markPrice 不换算
    assert "funding_rate" not in out


def test_single_route_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "topLongShortAccountRatio" in url:
            raise RuntimeError("top ls down")
        return _fake_fapi(url, params, headers)
    monkeypatch.setattr(bd, "_http_get_json", partial)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert "long_short_ratio_top" not in out
    assert out["long_short_ratio"] == 1.20
    assert out["source"] == "binance"


def test_all_fail_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bd, "_http_get_json", boom)
    assert bd.fetch_perp_metrics("BTC", "USDT") == {}


def test_ratio_structural_anomalies(monkeypatch):
    for payload in [[], [123], [{"foo": 1}], {"not": "a list"}]:
        monkeypatch.setattr(bd, "_http_get_json", lambda url, params=None, headers=None, p=payload: p if "LongShortAccountRatio" in url else {})
        out = bd.fetch_perp_metrics("BTC", "USDT")
        assert "long_short_ratio" not in out
        assert "long_short_ratio_top" not in out


def test_funding_history_half_open_window(monkeypatch):
    def fake(url, params=None, headers=None):
        assert "/fapi/v1/fundingRate" in url
        assert params["startTime"] == "1000" and params["endTime"] == "4000"
        return [
            {"fundingTime": "500", "fundingRate": "0.005"},    # < start，排除
            {"fundingTime": "1000", "fundingRate": "0.0001"},  # == start，包含
            {"fundingTime": "2000", "fundingRate": "0.0002"},
            {"fundingTime": "4000", "fundingRate": "0.0004"},  # == end，半开排除
        ]
    monkeypatch.setattr(bd, "_http_get_json", fake)
    assert bd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000) == [0.0001, 0.0002]


def test_funding_history_failures_and_anomalies(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bd, "_http_get_json", boom)
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []
    monkeypatch.setattr(bd, "_http_get_json", lambda *a, **k: {"not": "a list"})
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []
    monkeypatch.setattr(bd, "_http_get_json", lambda *a, **k: [{"fundingTime": "abc", "fundingRate": "0.1"}, 42])
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []


def test_fapi_base_env_override(monkeypatch):
    seen = {}
    def spy(url, params=None, headers=None):
        seen["url"] = url
        raise RuntimeError("stop")
    monkeypatch.setenv("BINANCE_FAPI_BASE_URL", "https://mirror.example.com/")
    monkeypatch.setattr(bd, "_http_get_json", spy)
    bd.fetch_funding_rate_history("BTC", "USDT", 0, 1)
    assert seen["url"].startswith("https://mirror.example.com/fapi/v1/fundingRate")
