# -*- coding: utf-8 -*-
from api.v1.schemas.stocks import SignalMarker, SignalsResponse, BoardEntry


def _marker(**over):
    base = dict(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", source="rule", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
    )
    base.update(over)
    return base


def test_marker_finer_fields_default_none():
    m = SignalMarker(**_marker())
    assert m.horizon_bars is None
    assert m.status is None


def test_marker_finer_fields_set():
    m = SignalMarker(**_marker(horizon_bars=10, status="active"))
    assert m.horizon_bars == 10
    assert m.status == "active"


def test_signals_response_plan_quality_default_and_set():
    r = SignalsResponse(status="ok", consistency="consistent")
    assert r.plan_quality is None
    r2 = SignalsResponse(status="ok", consistency="consistent", plan_quality="high")
    assert r2.plan_quality == "high"


def test_board_entry_finer_fields():
    e = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None}, status="ok",
        horizon_bars=20, signal_status="aging", plan_quality="medium",
    )
    assert e.horizon_bars == 20
    assert e.signal_status == "aging"      # 区别于 e.status(ok/degraded)
    assert e.status == "ok"
    assert e.plan_quality == "medium"


def test_board_entry_finer_fields_default_none():
    e = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None}, status="ok",
    )
    assert e.horizon_bars is None and e.signal_status is None and e.plan_quality is None
