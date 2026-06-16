# -*- coding: utf-8 -*-
"""Contract tests for /signals schemas and endpoint (M2a)."""

from types import SimpleNamespace
from datetime import datetime

import pandas as pd
import pytest

import api.v1.endpoints.stocks as stocks_ep
import src.services.signal_board_service as sbs
from api.v1.schemas.stocks import PriceLines, SignalMarker, SignalsResponse
from src.stock_analyzer import BuySignal
from src.services.signals_service import date_str_to_epoch_ms


def test_signal_marker_defaults_for_m2a_unfilled_fields():
    marker = SignalMarker(
        timestamp=1717200000000,
        price=1800.0,
        anchor="close",
        direction="bullish",
        signal_type="volume_breakout",
        source="rule",
        confidence="high",
        is_daily_approx=False,
        is_anomalous=False,
        reason="放量突破 20 日新高",
    )
    # M2c 回填前默认空
    assert marker.threshold is None
    assert marker.observed_value is None
    assert marker.hit_rate is None
    assert marker.hit_sample is None
    assert marker.verified is False
    # as_of 仅 source=llm 时有值
    assert marker.as_of is None


def test_price_lines_all_null_is_valid_for_m2a():
    lines = PriceLines()
    assert lines.entry is None
    assert lines.stop is None
    assert lines.target is None


def test_signals_response_degraded_shape():
    resp = SignalsResponse(
        status="degraded",
        markers=[],
        price_lines=PriceLines(),
        consistency="unknown",
        degraded_reason="数据不足，窗口未满",
    )
    assert resp.status == "degraded"
    assert resp.markers == []
    assert resp.consistency == "unknown"
    assert resp.degraded_reason == "数据不足，窗口未满"
    # 序列化保留 price_lines 子字段（不整体省略）
    dumped = resp.model_dump()
    assert dumped["price_lines"] == {"entry": None, "stop": None, "target": None}


# ─── 端点集成用例（Task 5）───────────────────────────────────────────────────


def _fake_history_result(rows):
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "period": "daily",
        "data": rows,
    }


def _bar(date, close):
    return {
        "date": date, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 1000.0,
        "amount": 1000.0 * close, "change_percent": 0.5,
    }


def _vpsignal(timestamp):
    # M1 VPSignal 的时间锚是 timestamp:int（epoch ms, Asia/Shanghai），无 .date。
    return SimpleNamespace(
        timestamp=timestamp, price=1800.0, anchor="close", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False,
        reason="放量突破", threshold=2.0, observed_value=2.5,
    )


def _patch_common(monkeypatch, *, engine_result, rule_signal, llm_record):
    rows = [_bar("2026-06-11", 1790.0), _bar("2026-06-12", 1800.0)]
    monkeypatch.setattr(
        stocks_ep.StockService, "get_history_data",
        lambda self, stock_code, period="daily", days=120: _fake_history_result(rows),
    )
    monkeypatch.setattr(
        sbs, "compute_volume_price_signals",
        lambda df, config=None: engine_result,
    )

    class _FakeAnalyzer:
        def __init__(self, *a, **k):
            pass

        def analyze(self, df, code):
            return SimpleNamespace(buy_signal=rule_signal)

    monkeypatch.setattr(sbs, "StockTrendAnalyzer", _FakeAnalyzer)

    class _FakeDB:
        def get_latest_analysis_by_code(self, code):
            return llm_record

    monkeypatch.setattr(stocks_ep.DatabaseManager, "get_instance", classmethod(lambda cls: _FakeDB()))


def test_signals_endpoint_ok_shape(monkeypatch):
    engine = SimpleNamespace(
        markers=[
            _vpsignal(date_str_to_epoch_ms("2026-06-11")),
            _vpsignal(date_str_to_epoch_ms("2026-06-12")),
        ],
        status="ok", degraded_reason=None,
    )
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert resp.status == "ok"
    assert resp.consistency == "consistent"
    assert resp.price_lines.entry is None
    assert any(m.source == "llm" for m in resp.markers)
    assert any(m.source == "rule" for m in resp.markers)


def test_signals_endpoint_degraded_returns_200_payload(monkeypatch):
    engine = SimpleNamespace(markers=[], status="degraded", degraded_reason="窗口不足")
    _patch_common(monkeypatch, engine_result=engine, rule_signal=None, llm_record=None)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert resp.status == "degraded"
    assert resp.markers == []
    assert resp.consistency == "unknown"
    assert resp.degraded_reason == "窗口不足"


def test_signals_endpoint_empty_history_is_degraded(monkeypatch):
    monkeypatch.setattr(
        stocks_ep.StockService, "get_history_data",
        lambda self, stock_code, period="daily", days=120: _fake_history_result([]),
    )
    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)
    assert resp.status == "degraded"
    assert resp.consistency == "unknown"
    assert resp.markers == []


def test_signals_endpoint_timestamp_anchored_to_bar(monkeypatch):
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-11"))],
        status="ok", degraded_reason=None,
    )
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=None)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)
    rule_markers = [m for m in resp.markers if m.source == "rule"]
    assert rule_markers[0].timestamp == date_str_to_epoch_ms("2026-06-11")


def test_signals_endpoint_llm_marker_price_anchored_to_latest_close(monkeypatch):
    """LLM marker 的 price 必须等于最新 bar 的收盘价（而非 0.0 占位）。"""
    # _patch_common 中 rows[-1] 的 close 是 1800.0（_bar("2026-06-12", 1800.0)）
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-11"))],
        status="ok", degraded_reason=None,
    )
    # operation_advice="买入" → direction="bullish"，LLM 点会被生成
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    llm_markers = [m for m in resp.markers if m.source == "llm"]
    assert len(llm_markers) == 1, "应有且仅有 1 个 LLM marker"
    assert llm_markers[0].price == 1800.0, (
        f"LLM marker.price 应锚到最新 bar 收盘 1800.0，实际为 {llm_markers[0].price}"
    )


def test_signals_endpoint_stale_when_llm_too_old(monkeypatch):
    """当 LLM 结论超过 stale 阈值个交易日时，consistency 必须返回 'stale'。"""
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-11"))],
        status="ok", degraded_reason=None,
    )
    # created_at=2026-06-10：bars 中 2026-06-11 和 2026-06-12 均 > cutoff
    # → trading_days_elapsed=2；阈值设为 1 → 2 > 1 → stale
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 10))
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    # 将 stale 阈值降到 1，让 elapsed=2 触发 stale
    monkeypatch.setenv("SIGNALS_STALE_TRADING_DAYS", "1")

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert resp.consistency == "stale", (
        f"期望 consistency='stale'，实际为 '{resp.consistency}'"
    )


# ─── 向后兼容回归 + 路由注册校验（Task 6）────────────────────────────────────


def test_history_schema_backward_compatible():
    # 旧 schema 字段不变（追加 SignalMarker 不影响 KLineData/StockHistoryResponse）
    from api.v1.schemas.stocks import KLineData, StockHistoryResponse

    kline = KLineData(date="2026-06-12", open=1.0, high=2.0, low=0.5, close=1.5)
    assert kline.volume is None
    resp = StockHistoryResponse(stock_code="600519", period="daily")
    assert resp.data == []


def test_signals_route_registered_on_stocks_router():
    from api.v1.endpoints.stocks import router

    paths = {route.path for route in router.routes}
    assert "/{stock_code:path}/signals" in paths
    assert "/{stock_code:path}/history" in paths  # 旧路由仍在


# ─── M2c-5：端点 resolver 接线回归（verified 端到端可达）────────────────────────

def test_signals_endpoint_backfills_verified_via_resolver(monkeypatch):
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-12"))],
        status="ok", degraded_reason=None,
    )
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=None)
    # 端点内部用的 resolver 被替换为确定性桩，证明组装层确实调用了它
    monkeypatch.setattr(
        sbs, "resolve_marker_hit_fields",
        lambda signal_type, code: {"hit_rate": 0.7, "hit_sample": 30, "verified": True},
    )

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    rule_markers = [m for m in resp.markers if m.source == "rule"]
    assert rule_markers[0].verified is True
    assert rule_markers[0].hit_rate == 0.7
    assert rule_markers[0].hit_sample == 30


# ─── 终审 #2：端点把 VPSConfig.from_env() 真正传给引擎（非 None）───────────────

def test_signals_endpoint_passes_vps_config_from_env(monkeypatch):
    """/signals 端点必须以 config=VPSConfig.from_env() 调用引擎，
    而非 config=None（否则 13 个 VPS_* 环境变量全部失效）。"""
    from src.services.volume_price_signals import VPSConfig

    recorded = {}

    def _fake_engine(df, config=None):
        recorded["config"] = config
        return SimpleNamespace(markers=[], status="ok", degraded_reason=None)

    rows = [_bar("2026-06-11", 1790.0), _bar("2026-06-12", 1800.0)]
    monkeypatch.setattr(
        stocks_ep.StockService, "get_history_data",
        lambda self, stock_code, period="daily", days=120: _fake_history_result(rows),
    )
    monkeypatch.setattr(sbs, "compute_volume_price_signals", _fake_engine)

    class _FakeAnalyzer:
        def __init__(self, *a, **k):
            pass

        def analyze(self, df, code):
            return SimpleNamespace(buy_signal=None)

    monkeypatch.setattr(sbs, "StockTrendAnalyzer", _FakeAnalyzer)

    class _FakeDB:
        def get_latest_analysis_by_code(self, code):
            return None

    monkeypatch.setattr(
        stocks_ep.DatabaseManager, "get_instance", classmethod(lambda cls: _FakeDB())
    )

    stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert "config" in recorded, "引擎必须被调用"
    assert isinstance(recorded["config"], VPSConfig), (
        "端点应传 VPSConfig.from_env() 实例，而非 None（否则 VPS_* 配置失效）"
    )
