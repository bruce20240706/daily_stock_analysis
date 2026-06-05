"""crypto 符号识别与规范化测试。"""
import pytest
from data_provider import is_crypto_code, SUPPORTED_QUOTES
from data_provider.base import normalize_stock_code


@pytest.mark.parametrize("code,expected", [
    ("BTC/USDT", True), ("eth/usdt", True), ("BTC/USDC", True), ("ETH/BTC", True),
    ("BTC/CNY", False),   # 不受支持的计价币
    ("BTCUSDT", False),   # 无斜杠不算 crypto
    ("AAPL", False), ("600519", False), ("00700", False), ("hk00700", False),
    ("", False), ("BTC/", False), ("/USDT", False), ("A/B/C", False),
])
def test_is_crypto_code(code, expected):
    assert is_crypto_code(code) is expected


def test_supported_quotes_contains_usdt():
    assert "USDT" in SUPPORTED_QUOTES


@pytest.mark.parametrize("raw,norm", [
    ("btc/usdt", "BTC/USDT"), ("BTC/USDT", "BTC/USDT"), (" eth/usdt ", "ETH/USDT"),
])
def test_normalize_keeps_crypto(raw, norm):
    assert normalize_stock_code(raw) == norm


@pytest.mark.parametrize("code,norm", [
    ("SH600519", "600519"), ("600519", "600519"), ("AAPL", "AAPL"), ("hk1810", "HK01810"),
])
def test_normalize_stock_unchanged(code, norm):
    # 回归：股票代码规范化行为不变
    assert normalize_stock_code(code) == norm


from data_provider.us_index_mapping import is_us_stock_code
from data_provider.base import _is_hk_market
from data_provider.akshare_fetcher import _is_hk_code


@pytest.mark.parametrize("code", ["BTC/USDT", "ETH/USDT", "eth/btc"])
def test_stock_predicates_reject_crypto(code):
    assert is_us_stock_code(code) is False
    assert _is_hk_market(code) is False
    assert _is_hk_code(code) is False
