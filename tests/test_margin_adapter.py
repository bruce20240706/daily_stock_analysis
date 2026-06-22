# -*- coding: utf-8 -*-
import pandas as pd
import data_provider.fundamental_adapter as fa
from data_provider.fundamental_adapter import AkshareFundamentalAdapter


def _sse_df(code="600519"):
    return pd.DataFrame([{
        "信用交易日期": "20260619",
        "标的证券代码": code,
        "标的证券简称": "贵州茅台",
        "融资余额": 1.23e8,
        "融资买入额": 4.5e7,
        "融券余量": 1000.0,
        "融券余量金额": 9.9e9,
    }])


def _szse_df(code="000001"):
    return pd.DataFrame([{
        "证券代码": code,
        "证券简称": "平安银行",
        "融资余额": 2.0e8,
        "融资买入额": 6.0e7,
        "融券余量": 2000.0,
    }])


def setup_function(_):
    fa._margin_detail_memo.clear()


def test_get_margin_detail_sse_routing_and_fields(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), cands[0][0], []))
    out = adapter.get_margin_detail("600519")
    assert out["exchange"] == "SSE"
    assert out["financing_balance"] == 1.23e8
    assert out["financing_buy"] == 4.5e7
    assert out["short_volume"] == 1000.0  # 融券余量, not 融券余量金额
    assert out["trade_date"] is not None
    assert out["status"] in ("ok", "partial")


def test_get_margin_detail_szse_routing(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_szse_df(), cands[0][0], []))
    out = adapter.get_margin_detail("000001")
    assert out["exchange"] == "SZSE"
    assert out["financing_balance"] == 2.0e8


def test_get_margin_detail_bse_etf_other_not_supported(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    # endpoint would return data, but routing must short-circuit before calling it
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), "x", []))
    for code in ("830799", "920819", "510300", "159915", "900001"):
        out = adapter.get_margin_detail(code)
        assert out["status"] == "not_supported", code
        assert out["exchange"] is None


def test_get_margin_detail_empty_fail_open(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (None, None, ["stock_margin_detail_sse:ValueError"]))
    out = adapter.get_margin_detail("600519")
    assert out["status"] == "not_supported"
    assert out["financing_balance"] is None


def test_margin_df_for_memo_hit_single_fetch(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    calls = {"n": 0}

    def _spy(cands):
        calls["n"] += 1
        return _sse_df(), cands[0][0], []

    monkeypatch.setattr(adapter, "_call_df_candidates", _spy)
    a = adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    b = adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    assert calls["n"] == 1
    assert a is b


def test_margin_memo_bounded(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), cands[0][0], []))
    for i in range(8):
        adapter._margin_df_for("SSE", "stock_margin_detail_sse", f"2026060{i}")
    assert len(fa._margin_detail_memo) <= 4


def test_margin_df_for_empty_not_cached(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (None, None, []))
    adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    assert len(fa._margin_detail_memo) == 0


def test_short_volume_excludes_amount_column_regardless_of_order(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    # 列序故意把「融券余量金额」放在「融券余量」之前，验证不取错值
    df = pd.DataFrame([{
        "标的证券代码": "600519",
        "融资余额": 1.0e8,
        "融资买入额": 4.0e7,
        "融券余量金额": 9.9e9,
        "融券余量": 1000.0,
    }])
    monkeypatch.setattr(adapter, "_call_df_candidates", lambda cands: (df, cands[0][0], []))
    out = adapter.get_margin_detail("600519")
    assert out["short_volume"] == 1000.0


def test_get_margin_detail_deadline_exhausted_skips_fetch(monkeypatch):
    import time as _t
    adapter = AkshareFundamentalAdapter()
    calls = {"n": 0}

    def _spy(cands):
        calls["n"] += 1
        return _sse_df(), cands[0][0], []

    monkeypatch.setattr(adapter, "_call_df_candidates", _spy)
    # deadline already in the past → loop breaks before any fetch
    out = adapter.get_margin_detail("600519", deadline=_t.monotonic() - 1.0)
    assert out["status"] == "not_supported"
    assert calls["n"] == 0
