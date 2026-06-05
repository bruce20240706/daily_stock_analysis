"""crypto 市场画像与 LLM 市场指引测试。"""
from src.core.market_profile import get_profile, CRYPTO_PROFILE
from src.market_context import detect_market, get_market_guidelines, get_market_role


def test_crypto_profile():
    p = get_profile("crypto")
    assert p is CRYPTO_PROFILE
    assert p.region == "crypto" and p.has_market_stats is False


def test_detect_market_crypto():
    assert detect_market("BTC/USDT") == "crypto"
    assert detect_market("600519") == "cn"   # 回归
    assert detect_market("AAPL") == "us"      # 回归


def test_crypto_guidelines_present():
    g = get_market_guidelines("BTC/USDT", lang="zh")
    assert "数字货币" in g or "加密" in g
    assert get_market_role("BTC/USDT", lang="zh")  # 非空
