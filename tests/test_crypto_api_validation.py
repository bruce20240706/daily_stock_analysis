# -*- coding: utf-8 -*-
"""API 层 symbol 校验放行 crypto 交易对测试。"""
import pytest
from api.v1.endpoints.stocks import _validate_and_normalize_stock_code
from fastapi import HTTPException


def test_accepts_crypto():
    assert _validate_and_normalize_stock_code("BTC/USDT") == "BTC/USDT"
    assert _validate_and_normalize_stock_code("eth/usdt") == "ETH/USDT"


def test_rejects_unsupported_quote():
    with pytest.raises(HTTPException):
        _validate_and_normalize_stock_code("BTC/CNY")


def test_stock_still_valid():
    assert _validate_and_normalize_stock_code("600519") == "600519"
