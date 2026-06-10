# -*- coding: utf-8 -*-
"""perp 标的被识别为 crypto 市场 → 24/7 日历。"""
from datetime import date
from src.core.trading_calendar import get_market_for_stock, is_market_open, infer_market_phase, MarketPhase


def test_perp_market_is_crypto():
    assert get_market_for_stock("BTC/USDT:PERP") == "crypto"
    assert get_market_for_stock("BTC/USDT") == "crypto"


def test_perp_is_24x7():
    assert is_market_open("crypto", date(2026, 6, 6)) is True
    assert infer_market_phase("crypto") == MarketPhase.INTRADAY
