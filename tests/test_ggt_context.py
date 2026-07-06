# -*- coding: utf-8 -*-
"""
Task 4: get_ggt_context 管理层组装 + offshore context / 枚举工厂 ggt 接线测试。
"""
import time
from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import DataFetcherManager
from data_provider.fundamental_adapter import _ggt_key


def _mgr():
    return DataFetcherManager()


# ---------------------------------------------------------------------------
# get_ggt_context
# ---------------------------------------------------------------------------

def test_get_ggt_context_gate_not_supported_for_non_hk():
    mgr = _mgr()
    for code in ("600519", "AAPL", "000001", "510300"):
        block = mgr.get_ggt_context(code)
        assert block["status"] == "not_supported", code
        assert block["data"] == {"eligible": None, "holding": None, "southbound_flow": None}


def test_get_ggt_context_ok_all_three_present(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_eligibility_set",
        lambda deadline=None: {_ggt_key("00700")},
    )
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_holding",
        lambda code, deadline=None: {
            "holding_shares": 200, "holding_value": 2200.0,
            "holding_ratio_pct": 6.5, "holding_trade_date": "2026-07-01",
        },
    )
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_southbound_flow",
        lambda deadline=None: {"southbound_net_flow": 20.0, "flow_date": "2026-07-01", "partial": False},
    )
    block = mgr.get_ggt_context("hk00700")
    assert block["status"] == "ok"
    assert block["data"]["eligible"] is True
    assert block["data"]["holding"]["holding_shares"] == 200
    assert block["data"]["southbound_flow"]["southbound_net_flow"] == 20.0


def test_get_ggt_context_failed_when_all_none(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_eligibility_set", lambda deadline=None: None)
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_holding", lambda code, deadline=None: None)
    monkeypatch.setattr(mgr._fundamental_adapter, "get_southbound_flow", lambda deadline=None: None)
    block = mgr.get_ggt_context("hk00700")
    assert block["status"] == "failed"
    assert block["data"] == {"eligible": None, "holding": None, "southbound_flow": None}


def test_get_ggt_context_partial_when_only_some_legs(monkeypatch):
    # eligibility 面在集合中 → True(有值);holding/flow 均不可得 → None。
    # 三面中仅一面有值 → status 应为 "partial"(非 "ok" 也非 "failed")。
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_eligibility_set",
        lambda deadline=None: {_ggt_key("00700")},
    )
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_holding", lambda code, deadline=None: None)
    monkeypatch.setattr(mgr._fundamental_adapter, "get_southbound_flow", lambda deadline=None: None)
    block = mgr.get_ggt_context("hk00700")
    assert block["status"] == "partial"
    assert block["data"]["eligible"] is True
    assert block["data"]["holding"] is None
    assert block["data"]["southbound_flow"] is None


def test_get_ggt_context_one_leg_raises_does_not_drop_others(monkeypatch):
    # R2: 某 leg 抛异常必须被自身的 try/except 吞掉,不得中断/拖垮其余 leg 的抓取。
    mgr = _mgr()

    def boom(deadline=None):
        raise RuntimeError("eastmoney unreachable")

    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_eligibility_set", boom)
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_holding",
        lambda code, deadline=None: {
            "holding_shares": 1, "holding_value": 1.0,
            "holding_ratio_pct": 1.0, "holding_trade_date": "2026-07-01",
        },
    )
    monkeypatch.setattr(mgr._fundamental_adapter, "get_southbound_flow", lambda deadline=None: None)
    block = mgr.get_ggt_context("hk00700")
    assert block["status"] == "partial"
    assert block["data"]["eligible"] is None
    assert block["data"]["holding"]["holding_shares"] == 1
    assert block["data"]["southbound_flow"] is None


def test_get_ggt_context_hung_leg_is_bounded_not_blocking(monkeypatch):
    # G8: 单腿"挂起"(网络库无超时导致的真实卡死场景)不得拖垮整个调用——每腿经
    # _run_with_retry 跑在有界超时线程下,受 per_leg_cap(=fundamental_fetch_timeout_seconds)
    # 上限,挂起腿最多消耗 per_leg_cap 而非拖满整个总预算(budget_seconds),腾出预算给
    # 其余两腿仍能正常完成——生产默认 stage(8s)/fetch(3s)本就总预算>>单腿上限,这里把
    # per_leg_cap 压到 0.1s(远小于 sleep(3))令测试保持快速且确定性,同时把总预算设为
    # 1.0s(> per_leg_cap)以复现"挂起腿只吃掉自己的上限,不吃光总预算"这一关键行为。
    cfg = SimpleNamespace(fundamental_fetch_timeout_seconds=0.1, fundamental_retry_max=1)

    def slow_eligibility():
        time.sleep(3)
        return {_ggt_key("00700")}

    mgr = _mgr()
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_eligibility_set", slow_eligibility)
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_holding",
        lambda code: {
            "holding_shares": 100, "holding_value": 1000.0,
            "holding_ratio_pct": 3.0, "holding_trade_date": "2026-07-01",
        },
    )
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_southbound_flow",
        lambda: {"southbound_net_flow": 5.0, "flow_date": "2026-07-01", "partial": False},
    )
    with patch("src.config.get_config", return_value=cfg):
        t0 = time.monotonic()
        block = mgr.get_ggt_context("hk00700", budget_seconds=1.0)
        elapsed = time.monotonic() - t0
    # 挂起腿 sleep(3) 若未被有界超时机制 abandon,调用会阻塞~3s;
    # 断言 < 2.0s 证明调用方在挂起腿完成前已被释放(未原样等满 3s)。
    assert elapsed < 2.0, f"get_ggt_context blocked for {elapsed:.2f}s waiting on hung leg"
    assert block["data"]["eligible"] is None
    assert block["data"]["holding"]["holding_shares"] == 100
    assert block["data"]["southbound_flow"]["southbound_net_flow"] == 5.0
    assert block["status"] == "partial"


# ---------------------------------------------------------------------------
# enumeration factories
# ---------------------------------------------------------------------------

def test_enumeration_not_supported_factory_includes_ggt():
    mgr = _mgr()
    nf = mgr._build_market_not_supported("etf", "x")
    assert "ggt" in nf
    assert nf["coverage"]["ggt"] == "not_supported"
    assert nf["ggt"]["data"] == {"eligible": None, "holding": None, "southbound_flow": None}


def test_enumeration_failed_factory_includes_ggt():
    mgr = _mgr()
    bf = mgr.build_failed_fundamental_context("600519", "x")
    assert "ggt" in bf
    assert bf["coverage"]["ggt"] == "failed"
    # ggt payload 是 3-key 形状(非其余 block 的通用 "{}"),供下游(Task 5)稳定消费。
    assert bf["ggt"]["data"] == {"eligible": None, "holding": None, "southbound_flow": None}


# ---------------------------------------------------------------------------
# _should_cache_fundamental_context (CONTRACT-6)
# ---------------------------------------------------------------------------

def test_should_cache_context_considers_ggt_payload():
    # 其余 8 个 block 均无实质 payload,仅 ggt 有 → 若 ggt 未被纳入扫描 tuple 会误判 False。
    mgr = _mgr()
    ctx = {
        "status": "partial",
        "valuation": {"data": {}}, "growth": {"data": {}}, "earnings": {"data": {}},
        "institution": {"data": {}}, "margin": {"data": {}}, "capital_flow": {"data": {}},
        "dragon_tiger": {"data": {}}, "boards": {"data": {}},
        "ggt": {"data": {"eligible": True, "holding": None, "southbound_flow": None}},
    }
    assert mgr._should_cache_fundamental_context(ctx) is True


# ---------------------------------------------------------------------------
# _build_offshore_fundamental_context wiring (F4-scope critical invariant)
# ---------------------------------------------------------------------------

def _ggt_failed_block():
    return {
        "status": "failed",
        "coverage": {"status": "failed"},
        "source_chain": [],
        "errors": ["ggt failed"],
        "data": {"eligible": None, "holding": None, "southbound_flow": None},
    }


def test_offshore_context_has_ggt_key_and_ggt_not_dragging_total_status():
    # budget_seconds=0.0 让 valuation/bundle 两阶段因超时短路(无网络),
    # active_statuses 全部 "not_supported" → total status 应为 "not_supported"。
    # ggt 被 mock 为 "failed",若 ggt 被误纳入 active_statuses,total 会被拖成 "failed"。
    mgr = DataFetcherManager(fetchers=[])
    with patch.object(mgr, "get_ggt_context", return_value=_ggt_failed_block()):
        ctx = mgr._build_offshore_fundamental_context("hk00700", market="hk", budget_seconds=0.0)
    assert "ggt" in ctx
    assert ctx["coverage"]["ggt"] == "failed"
    assert ctx["status"] == "not_supported"
    assert "ggt failed" in ctx["errors"]


def test_offshore_context_ggt_failed_does_not_downgrade_ok_total_status():
    # 更强的不变式版本:让 valuation/growth/earnings 三面均真实走"ok"(mock 掉网络层,
    # 非 mock get_ggt_context 之外的真实聚合/状态推断逻辑),证明即便整体本应是 "ok",
    # ggt 的 "failed" 也不会把 total 拖成 "partial"/"failed"。
    mgr = DataFetcherManager(fetchers=[])
    quote = SimpleNamespace(pe_ratio=10.0, pb_ratio=1.2, total_mv=1e9, circ_mv=1e9)
    bundle = {
        "status": "ok",
        "growth": {"revenue_yoy": 5.0},
        "earnings": {"financial_report": {"revenue": 1.0}},
        "belong_boards": [],
        "source_chain": [],
        "errors": [],
    }
    with patch.object(mgr, "get_realtime_quote", return_value=quote), \
            patch.object(mgr._yfinance_fundamental_adapter, "get_fundamental_bundle", return_value=bundle), \
            patch.object(mgr, "get_ggt_context", return_value=_ggt_failed_block()):
        ctx = mgr._build_offshore_fundamental_context("hk00700", market="hk", budget_seconds=5.0)
    assert ctx["coverage"]["ggt"] == "failed"
    assert ctx["status"] == "ok"


def test_offshore_context_us_market_ggt_not_supported():
    # US 市场无南向概念,ggt 应直接 not_supported,不应调用 get_ggt_context。
    mgr = DataFetcherManager(fetchers=[])
    with patch.object(mgr, "get_ggt_context") as spy:
        ctx = mgr._build_offshore_fundamental_context("AAPL", market="us", budget_seconds=0.0)
    spy.assert_not_called()
    assert ctx["coverage"]["ggt"] == "not_supported"
    assert ctx["ggt"]["data"] == {"eligible": None, "holding": None, "southbound_flow": None}
