from src.core.market_review import (
    _resolve_market_review_regions,
    _VALID_MARKET_REVIEW_REGIONS,
    _get_market_review_text,
)


def test_crypto_is_valid_region():
    assert "crypto" in _VALID_MARKET_REVIEW_REGIONS


def test_resolve_crypto_single():
    assert _resolve_market_review_regions("crypto") == ["crypto"]


def test_resolve_both_excludes_crypto():
    # D3: both 必须保持 cn+hk+us，绝不包含 crypto
    assert _resolve_market_review_regions("both") == ["cn", "hk", "us"]


def test_resolve_comma_list_with_crypto_keeps_order():
    assert _resolve_market_review_regions("cn,crypto") == ["cn", "crypto"]


def test_crypto_title_present_zh_and_en():
    assert _get_market_review_text("zh")["crypto_title"].strip() != ""
    assert _get_market_review_text("en")["crypto_title"].strip() != ""
