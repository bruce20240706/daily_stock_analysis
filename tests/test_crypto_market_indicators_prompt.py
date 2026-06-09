from src.market_analyzer import MarketAnalyzer, MarketOverview

IND = {
    "btc_dominance": 56.07, "eth_dominance": 8.98,
    "total_market_cap_usd": 2241017397766.0, "market_cap_change_24h_pct": -0.64,
    "fear_greed": {"value": 10, "classification": "Extreme Fear", "timestamp": 1},
}


def test_indicators_prompt_block_zh_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_indicators_prompt_block(IND, "zh")
    assert "加密市场宏观指标" in block
    assert "BTC 主导率：56.07%" in block
    assert "恐贪指数：10（Extreme Fear）" in block


def test_indicators_prompt_block_empty_when_no_data():
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_indicators_prompt_block({}, "zh") == ""


def test_indicators_prompt_block_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_indicators_prompt_block(IND, "zh") == ""


def test_review_prompt_includes_indicators_block():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    prompt = a._build_review_prompt(ov, [], IND)
    assert "加密市场宏观指标" in prompt


def test_indicators_prompt_block_en_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_indicators_prompt_block(IND, "en")
    assert "## Crypto Macro Indicators" in block
    assert "BTC dominance: 56.07%" in block
    assert "Fear & Greed: 10 (Extreme Fear)" in block
    assert "-0.64% 24h" in block
    assert "do not invent data" in block
