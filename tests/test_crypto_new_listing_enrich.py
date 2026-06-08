# -*- coding: utf-8 -*-
"""Task 8: _enrich 行情富化单元测试（presence-only）。"""
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


class _Q:
    def __init__(self, price, change_pct=None, volume=None):
        self.price = price
        self.change_pct = change_pct
        self.volume = volume


class _FakeMgr:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get_realtime_quote(self, code, log_final_failure=True):
        self.calls.append(code)
        return self.mapping.get(code)


def _cfg():
    return Config._load_from_env()


def test_enrich_presence_only():
    items = [
        {"base": "AAA", "exchanges": ["okx"], "pairs": ["AAA-USDT"], "listed_at": 123, "quote": "USDT", "_sort": 123},
        {"base": "BBB", "exchanges": ["binance"], "pairs": ["BBBUSDT"], "listed_at": None, "quote": "USDT", "_sort": 9},
    ]
    mgr = _FakeMgr({"AAA/USDT": _Q(1.5, 2.0, 1000.0)})   # BBB 无行情
    svc = CryptoNewListingService(data_manager=mgr, repo=None, config=_cfg())
    out = svc._enrich(items)
    a = next(r for r in out if r["base"] == "AAA")
    assert a["price"] == 1.5 and a["change_pct"] == 2.0 and a["quote_pair"] == "AAA/USDT"
    assert a["listed_at"] == 123 and "_sort" not in a and "quote" not in a
    b = next(r for r in out if r["base"] == "BBB")
    assert "price" not in b and "listed_at" not in b      # 无行情→omit 价格；listed_at None→omit


def test_enrich_no_data_manager_omits_quotes():
    items = [{"base": "AAA", "exchanges": ["okx"], "pairs": ["AAA-USDT"], "listed_at": 123, "quote": "USDT", "_sort": 123}]
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg())
    out = svc._enrich(items)
    assert out[0]["base"] == "AAA" and "price" not in out[0] and "_sort" not in out[0]
