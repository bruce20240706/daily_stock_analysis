"""crypto 纳入持仓回归。

持仓/成交语境放行 market="crypto"，且未显式指定 currency 时，计价币从交易对的
QUOTE 推断（BTC/USDT -> USDT），无法解析时回退 USDT（与“USDT 计价现货为主”一致）。
"""
import pytest

from src.services.portfolio_service import PortfolioService, VALID_MARKETS


def test_valid_markets_includes_crypto():
    assert "crypto" in VALID_MARKETS


def test_normalize_market_accepts_crypto():
    assert PortfolioService._normalize_market("crypto") == "crypto"
    assert PortfolioService._normalize_market(" CRYPTO ") == "crypto"


def test_normalize_market_rejects_unknown():
    with pytest.raises(ValueError):
        PortfolioService._normalize_market("forex")


def test_default_currency_for_crypto_from_quote():
    assert PortfolioService._default_currency_for_market("crypto", "BTC/USDT") == "USDT"
    assert PortfolioService._default_currency_for_market("crypto", "ETH/USDC") == "USDC"
    assert PortfolioService._default_currency_for_market("crypto", "BTC/USD") == "USD"


def test_default_currency_for_crypto_fallback_usdt():
    assert PortfolioService._default_currency_for_market("crypto", "") == "USDT"
    assert PortfolioService._default_currency_for_market("crypto") == "USDT"


def test_default_currency_existing_markets_unchanged():
    assert PortfolioService._default_currency_for_market("hk") == "HKD"
    assert PortfolioService._default_currency_for_market("us") == "USD"
    assert PortfolioService._default_currency_for_market("cn") == "CNY"
