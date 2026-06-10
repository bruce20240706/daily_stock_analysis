# -*- coding: utf-8 -*-
"""perp 标的也注入 funding/mark/OI（门控放宽 + perp 解析）。"""
import types
import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_service import CryptoDerivativesService, attach_crypto_contracts


def _cfg(enabled=True):
    return types.SimpleNamespace(crypto_derivatives_enabled=enabled)


def test_collect_perp_code(monkeypatch):
    seen = {}
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: seen.update(base=base, quote=quote) or {"funding_rate": 0.0001, "source": "okx"})
    out = CryptoDerivativesService(config=_cfg()).collect("BTC/USDT:PERP")
    assert (seen["base"], seen["quote"]) == ("BTC", "USDT")
    assert out["funding_rate"] == 0.0001


def test_collect_spot_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: seen.update(base=base, quote=quote) or {"source": "okx"})
    CryptoDerivativesService(config=_cfg()).collect("ETH/USDT")
    assert (seen["base"], seen["quote"]) == ("ETH", "USDT")


def test_collect_non_crypto_empty():
    out = CryptoDerivativesService(config=_cfg()).collect("600519")
    assert out == {}


def test_attach_perp(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: {"mark_price": 62500.0})
    ctx = {"code": "BTC/USDT:PERP"}
    attach_crypto_contracts(ctx, config=_cfg())
    assert ctx["crypto_contracts"]["mark_price"] == 62500.0
