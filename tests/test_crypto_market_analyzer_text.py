from src.market_analyzer import MarketAnalyzer


def _crypto_analyzer():
    return MarketAnalyzer(region="crypto")


def test_crypto_region_not_downgraded_to_cn():
    a = _crypto_analyzer()
    assert a.region == "crypto"
    assert a.profile.region == "crypto"


def test_crypto_scope_name_zh():
    a = _crypto_analyzer()
    assert a._get_market_scope_name("zh") == "加密货币市场"


def test_crypto_turnover_unit_is_quote_currency():
    a = _crypto_analyzer()
    label = a._get_turnover_unit_label()
    assert "亿" not in label and "CNY" not in label
    assert "计价币" in label


def test_crypto_turnover_value_is_raw_not_billions():
    a = _crypto_analyzer()
    # 12,000,000,000 quote-ccy should render as raw thousands-separated, not "12.00" (billions)
    out = a._format_turnover_value(12_000_000_000.0)
    assert out == "12,000,000,000"
    assert a._format_turnover_value(0.0) == "N/A"


def test_crypto_review_title_zh():
    a = _crypto_analyzer()
    assert "加密货币" in a._get_review_title("2026-06-08")
