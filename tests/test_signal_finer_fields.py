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


import types as _types
from src.services import signals_service as _ss


def test_marker_from_vpsignal_maps_horizon_and_status_default():
    sig = _types.SimpleNamespace(
        timestamp=1000, price=10.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
        threshold=None, observed_value=None,
    )
    # resolver 返回含 horizon
    resolver = lambda st, code: {"hit_rate": 0.6, "hit_sample": 30, "verified": True,
                                 "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1, "horizon": 10}
    m = _ss._marker_from_vpsignal(sig, code="600519", hit_fields_resolver=resolver)
    assert m["horizon_bars"] == 10
    assert m["status"] is None  # status 由编排层后填,builder 默认 None


def test_marker_from_vpsignal_no_resolver_horizon_none():
    sig = _types.SimpleNamespace(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", confidence="low", is_daily_approx=False,
        is_anomalous=False, reason="r", threshold=None, observed_value=None,
    )
    m = _ss._marker_from_vpsignal(sig)
    assert m["horizon_bars"] is None and m["status"] is None


def test_resolver_returns_horizon_key(monkeypatch):
    from src.services import signal_hit_rate as shr
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "A")
    stat = _types.SimpleNamespace(win_rate=0.6, sample=99, ci_low=0.55,
                                  ci_high=0.7, baseline_win_rate=0.5, excess=0.05)
    monkeypatch.setattr(shr.SignalStatsRepository, "get", lambda self, st, mkt, horizon=None: stat)
    out = shr.resolve_marker_hit_fields("volume_breakout", "600519")
    assert out["horizon"] == int(shr.get_config().signal_backtest_horizon_bars)
    assert out["hit_rate"] == 0.6


def test_resolver_none_path_horizon_none(monkeypatch):
    from src.services import signal_hit_rate as shr
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "A")
    monkeypatch.setattr(shr.SignalStatsRepository, "get", lambda self, st, mkt, horizon=None: None)
    out = shr.resolve_marker_hit_fields("x", "600519")
    assert out["horizon"] is None and out["hit_rate"] is None
