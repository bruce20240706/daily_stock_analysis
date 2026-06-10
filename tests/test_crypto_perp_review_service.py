# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪服务：门控 + 读篮子 + 过滤非法代码 + 调聚合。"""
import types

import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_review_service import CryptoDerivativesReviewService


def _cfg(enabled=True, symbols="BTC/USDT,ETH/USDT,NOTACOIN"):
    return types.SimpleNamespace(
        crypto_derivatives_enabled=enabled,
        crypto_market_review_symbols=symbols,
    )


def test_disabled_returns_empty():
    out = CryptoDerivativesReviewService(config=_cfg(enabled=False)).collect()
    assert out == {}


def test_collect_parses_basket_filters_invalid_and_aggregates(monkeypatch):
    seen = {}
    def fake_snapshot(symbols):
        seen["symbols"] = symbols
        return {"avg_funding_rate": 0.0002, "total_open_interest_usd": 1000.0, "coins": []}
    monkeypatch.setattr(cd, "fetch_perp_market_snapshot", fake_snapshot)
    out = CryptoDerivativesReviewService(config=_cfg()).collect()
    assert seen["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert out["avg_funding_rate"] == 0.0002


def test_empty_basket_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_market_snapshot", lambda symbols: {})
    out = CryptoDerivativesReviewService(config=_cfg(symbols="")).collect()
    assert out == {}
