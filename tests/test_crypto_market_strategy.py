from src.core.market_strategy import get_market_strategy_blueprint


def test_crypto_blueprint_routed():
    bp = get_market_strategy_blueprint("crypto")
    assert bp.region == "crypto"


def test_crypto_blueprint_not_cn_content():
    bp = get_market_strategy_blueprint("crypto")
    all_text = " ".join([
        bp.title, bp.positioning,
        *bp.principles,
        *[f"{d.name} {d.objective} {' '.join(d.checkpoints)}" for d in bp.dimensions],
        *bp.action_framework,
    ])
    assert "涨跌停结构" not in all_text
    assert "国企指数" not in all_text
    assert "上证" not in all_text
