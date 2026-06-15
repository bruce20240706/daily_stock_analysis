# -*- coding: utf-8 -*-
"""Contract tests for /signals schemas and endpoint (M2a)."""

from api.v1.schemas.stocks import PriceLines, SignalMarker, SignalsResponse


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
