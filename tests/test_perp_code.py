# -*- coding: utf-8 -*-
"""OKX 永续标的分类：BASE/QUOTE:PERP（线性 USDT/USDC）。"""
from data_provider.base import is_perp_code, parse_perp_code, is_crypto_like, is_crypto_code


def test_is_perp_code_linear():
    assert is_perp_code("BTC/USDT:PERP")
    assert is_perp_code("eth/usdc:perp")
    assert is_perp_code("BTC/USDT:perp")


def test_is_perp_code_rejects():
    assert not is_perp_code("BTC/USD:PERP")
    assert not is_perp_code("BTC/USDT")
    assert not is_perp_code("BTC-USDT-SWAP")
    assert not is_perp_code("600519")
    assert not is_perp_code("")
    assert not is_perp_code(None)
    assert not is_perp_code("/USDT:PERP")


def test_parse_perp_code():
    assert parse_perp_code("BTC/USDT:PERP") == ("BTC", "USDT")
    assert parse_perp_code("eth/usdc:perp") == ("ETH", "USDC")
    assert parse_perp_code("BTC/USDT") == (None, None)


def test_is_crypto_like():
    assert is_crypto_like("BTC/USDT")
    assert is_crypto_like("BTC/USDT:PERP")
    assert not is_crypto_like("600519")
    assert is_perp_code("BTC/USDT:PERP") and not is_crypto_code("BTC/USDT:PERP")
