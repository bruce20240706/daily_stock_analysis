# -*- coding: utf-8 -*-
import pandas as pd  # noqa: F401  (parity with sibling tests; not strictly needed)
from data_provider.base import DataFetcherManager


def _mgr():
    return DataFetcherManager()


def test_get_margin_context_gate_not_supported_for_non_a_share():
    mgr = _mgr()
    for code in ("00700", "AAPL", "510300", "830799", "920819"):
        block = mgr.get_margin_context(code)
        assert block["status"] == "not_supported", code


def test_get_margin_context_ok_with_mocked_adapter(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_margin_detail",
        lambda code, deadline=None: {
            "status": "ok", "financing_balance": 1.23e8, "financing_buy": 4.5e7,
            "short_volume": 1000.0, "trade_date": "20260619", "exchange": "SSE",
            "source_chain": ["margin:stock_margin_detail_sse"], "errors": [],
        },
    )
    block = mgr.get_margin_context("600519")
    assert block["status"] == "ok"
    assert block["data"]["financing_balance"] == 1.23e8
    assert block["data"]["trade_date"] == "20260619"
    assert block["data"]["exchange"] == "SSE"


def test_get_margin_context_fail_open_on_non_dict(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_margin_detail",
        lambda code, deadline=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # _run_with_retry 捕获异常 → payload 非 dict → failed 块（不抛）
    block = mgr.get_margin_context("600519")
    assert block["status"] == "failed"


def test_enumeration_not_supported_factory_includes_margin():
    mgr = _mgr()
    nf = mgr._build_market_not_supported("etf", "x")
    assert "margin" in nf
    assert "margin" in nf["coverage"]
    assert nf["coverage"]["margin"] == "not_supported"


def test_enumeration_failed_factory_includes_margin():
    mgr = _mgr()
    bf = mgr.build_failed_fundamental_context("600519", "x")
    assert "margin" in bf
    assert bf["coverage"]["margin"] == "failed"
