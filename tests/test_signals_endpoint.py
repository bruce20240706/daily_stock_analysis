# -*- coding: utf-8 -*-
"""Contract tests for /signals schemas and endpoint (M2a)."""

from types import SimpleNamespace
from datetime import datetime

import pandas as pd
import pytest

import api.v1.endpoints.stocks as stocks_ep
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
        stocks_ep, "compute_volume_price_signals",
        lambda df, config=None: engine_result,
    )

    class _FakeAnalyzer:
        def __init__(self, *a, **k):
            pass

        def analyze(self, df, code):
            return SimpleNamespace(buy_signal=rule_signal)

    monkeypatch.setattr(stocks_ep, "StockTrendAnalyzer", _FakeAnalyzer)

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
