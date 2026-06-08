# -*- coding: utf-8 -*-
"""
端到端集成测试：crypto 大盘复盘
- 离线，mock 篮子，无 LLM 走模板
- 验证 payload 合约：region preserved, no market_light, no breadth, basket present, no cn fallback
"""
from src.market_analyzer import MarketAnalyzer, MarketIndex


def test_crypto_review_end_to_end_offline(monkeypatch):
    a = MarketAnalyzer(region="crypto")  # analyzer=None → 模板报告；search_service=None → 无新闻

    basket = [
        MarketIndex(code="BTC/USDT", name="BTC/USDT", current=64210.0, change_pct=2.1, high=65000.0, low=63000.0),
        MarketIndex(code="ETH/USDT", name="ETH/USDT", current=3180.0, change_pct=1.4, high=3250.0, low=3100.0),
    ]
    monkeypatch.setattr(a, "_get_main_indices", lambda: basket)

    result = a.run_daily_review_with_snapshot()
    payload = result.structured_payload

    assert payload["region"] == "crypto"
    assert result.market_light_snapshot is None
    assert "market_light" not in payload
    assert "breadth" not in payload
    assert any(idx["code"] == "BTC/USDT" for idx in payload["indices"])
    # 报告不含 A 股专属字样（防回退）
    assert "A股大盘复盘" not in payload["markdown_report"]
