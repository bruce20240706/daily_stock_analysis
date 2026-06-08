from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_crypto_payload_omits_market_light_and_breadth():
    a = MarketAnalyzer(region="crypto")
    overview = MarketOverview(date="2026-06-08")
    payload = a.build_market_review_payload(overview, news=[], report="# 加密货币大盘复盘\n\n## 一、概览\n内容")
    assert payload["region"] == "crypto"
    assert "market_light" not in payload   # crypto 不出市场灯
    assert "breadth" not in payload        # has_market_stats=False


def test_cn_payload_still_has_market_light():
    # 回归：cn 仍应携带 market_light（保证我们没破坏既有行为）
    a = MarketAnalyzer(region="cn")
    overview = MarketOverview(date="2026-06-08")
    payload = a.build_market_review_payload(overview, news=[], report="# A股大盘复盘\n\n## 一、概览\n内容")
    assert payload["region"] == "cn"
    assert "market_light" in payload
