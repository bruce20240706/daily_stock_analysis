"""crypto 交易日历与市场识别测试。"""
from datetime import date
from src.core.trading_calendar import (
    is_market_open, get_open_markets_today, get_market_for_stock,
)


def test_crypto_market_for_stock():
    assert get_market_for_stock("BTC/USDT") == "crypto"
    assert get_market_for_stock("600519") == "cn"   # 回归
    assert get_market_for_stock("AAPL") == "us"      # 回归


def test_crypto_always_open():
    # 选一个周六（2026-06-06 是周六）
    assert is_market_open("crypto", date(2026, 6, 6)) is True
    assert is_market_open("crypto", date(2026, 1, 1)) is True  # 元旦


def test_open_markets_today_includes_crypto():
    assert "crypto" in get_open_markets_today()
