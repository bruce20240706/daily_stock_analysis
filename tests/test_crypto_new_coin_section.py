from src.market_analyzer import MarketAnalyzer


def test_crypto_addendum_present_for_crypto():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_addendum_prompt("zh")
    assert "新币" in block and "上新" in block


def test_crypto_addendum_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_addendum_prompt("zh") == ""


def test_crypto_extra_news_queries_nonempty():
    a = MarketAnalyzer(region="crypto")
    qs = a._get_crypto_new_coin_queries()
    assert any("IEO" in q or "上新" in q or "new" in q.lower() for q in qs)


def test_non_crypto_extra_news_queries_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_new_coin_queries() == []
