# -*- coding: utf-8 -*-
"""端到端集成测试：crypto 复盘 payload 含 new_listings（presence-only 无行情时 omit price）。

离线——mock 抓取器 + 行情富化，无网络请求，无 LLM，走模板报告。
验证整条链路：MarketAnalyzer.run_daily_review_with_snapshot()
  → _get_crypto_new_listings()
  → CryptoNewListingService.discover()
  → fetch_okx_instruments / fetch_coinbase_products
  → _enrich()（data_manager.get_realtime_quote → None → price omitted）
  → structured_payload["new_listings"]
"""
import time

import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.market_analyzer import MarketAnalyzer, MarketIndex


def test_crypto_review_includes_new_listings_offline(monkeypatch):
    a = MarketAnalyzer(region="crypto")  # analyzer=None → 模板报告；search_service=None → 无新闻
    monkeypatch.setattr(a, "_get_main_indices",
                        lambda: [MarketIndex(code="BTC/USDT", name="BTC/USDT", current=64000.0, change_pct=1.0)])
    now = int(time.time() * 1000)
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("NEW", "USDT", "NEW-USDT", "okx", now - 86_400_000, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products", lambda w, n: [])
    # 富化：manager 对 NEW/USDT 返回 None（新币无行情）→ presence-only omit 价格
    monkeypatch.setattr(a.data_manager, "get_realtime_quote", lambda code, log_final_failure=True: None)

    payload = a.run_daily_review_with_snapshot().structured_payload
    assert payload["region"] == "crypto"
    assert "new_listings" in payload
    assert payload["new_listings"][0]["base"] == "NEW"
    assert "price" not in payload["new_listings"][0]    # 无行情 → omit
