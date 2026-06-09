import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_service import CryptoDerivativesService, attach_crypto_contracts


class _Cfg:
    def __init__(self, enabled=True):
        self.crypto_derivatives_enabled = enabled


def test_collect_returns_metrics_for_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"funding_rate": 0.0001, "mark_price": 60000.0, "source": "okx"})
    out = CryptoDerivativesService(config=_Cfg()).collect("BTC/USDT")
    assert out == {"funding_rate": 0.0001, "mark_price": 60000.0, "source": "okx"}


def test_collect_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    assert CryptoDerivativesService(config=_Cfg(enabled=False)).collect("BTC/USDT") == {}


def test_collect_non_crypto_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    assert CryptoDerivativesService(config=_Cfg()).collect("600519") == {}


def test_attach_sets_context_for_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 60000.0, "source": "okx"})
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert ctx["crypto_contracts"] == {"mark_price": 60000.0, "source": "okx"}


def test_attach_skips_non_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    ctx = {"code": "600519"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert "crypto_contracts" not in ctx


def test_attach_skips_when_empty_metrics(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {})
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert "crypto_contracts" not in ctx


def test_attach_swallows_fetch_error(monkeypatch):
    def boom(b, q):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "fetch_perp_metrics", boom)
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())  # 不抛
    assert "crypto_contracts" not in ctx
