"""crypto 数据源超时/重试可配置回归。

- CRYPTO_FETCH_TIMEOUT_SECONDS 覆盖请求超时（默认沿用类属性 timeout）。
- CRYPTO_FETCH_MAX_RETRIES 覆盖额外重试次数（默认 0，保持单次请求 + 快速 fallback）。
- 仅对网络/超时/5xx 等瞬时错误重试；4xx（如 451 地区限制）不重试，立即 fallback。
"""
import pytest
import requests

from data_provider.crypto_base import CryptoExchangeBase


def _fetcher():
    return CryptoExchangeBase()


def test_default_timeout_and_retries():
    f = _fetcher()
    assert f._fetch_timeout() == f.timeout  # 默认沿用类属性（10）
    assert f._fetch_max_retries() == 0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("CRYPTO_FETCH_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("CRYPTO_FETCH_MAX_RETRIES", "3")
    f = _fetcher()
    assert f._fetch_timeout() == 25.0
    assert f._fetch_max_retries() == 3


def test_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("CRYPTO_FETCH_TIMEOUT_SECONDS", "abc")
    monkeypatch.setenv("CRYPTO_FETCH_MAX_RETRIES", "-1")
    f = _fetcher()
    assert f._fetch_timeout() == f.timeout
    assert f._fetch_max_retries() == 0


def test_http_get_default_no_retry(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.RequestException):
        _fetcher()._http_get("http://x", {})
    assert calls["n"] == 1  # 默认 max_retries=0：仅一次


def test_http_get_retries_transient(monkeypatch):
    monkeypatch.setenv("CRYPTO_FETCH_MAX_RETRIES", "2")
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("transient")
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    assert _fetcher()._http_get("http://x", {}) == {"ok": True}
    assert calls["n"] == 3  # 2 次失败 + 第 3 次成功


def test_http_get_no_retry_on_4xx(monkeypatch):
    monkeypatch.setenv("CRYPTO_FETCH_MAX_RETRIES", "3")
    calls = {"n": 0}

    class _Resp:
        status_code = 451

        def raise_for_status(self):
            err = requests.HTTPError("451 region blocked")
            err.response = self
            raise err

        def json(self):
            return {}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(requests.HTTPError):
        _fetcher()._http_get("http://x", {})
    assert calls["n"] == 1  # 4xx 不重试，立即 fallback
