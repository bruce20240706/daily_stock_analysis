from src.core.market_strategy import get_market_strategy_blueprint


def test_crypto_blueprint_routed():
    bp = get_market_strategy_blueprint("crypto")
    assert bp.region == "crypto"


def test_crypto_blueprint_not_cn_content():
    bp = get_market_strategy_blueprint("crypto")
    joined = bp.title + " " + " ".join(bp.principles)
    assert "涨停" not in joined
    assert "国企指数" not in joined
