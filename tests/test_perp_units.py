# -*- coding: utf-8 -*-
"""perp 代码的成交量/额单位解析：(BASE, QUOTE)，不被 :PERP 污染。"""
from src.analyzer import _crypto_volume_amount_units


def test_perp_units():
    assert _crypto_volume_amount_units("BTC/USDT:PERP") == ("BTC", "USDT")


def test_spot_units_unchanged():
    assert _crypto_volume_amount_units("ETH/USDT") == ("ETH", "USDT")


def test_stock_units_none():
    assert _crypto_volume_amount_units("600519") == (None, None)
