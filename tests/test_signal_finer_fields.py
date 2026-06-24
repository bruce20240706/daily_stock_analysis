# -*- coding: utf-8 -*-
import types as _types

import src.services.signal_board_service as _sbs
from api.v1.schemas.stocks import SignalMarker, SignalsResponse, BoardEntry
from src.services import signals_service as _ss
from src.services.signals_service import (
    compute_marker_statuses,
    compute_plan_quality,
    date_str_to_epoch_ms,
)


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
    monkeypatch.setattr(shr.SignalStatsRepository, "get",
                        lambda self, st, mkt, *, interval="1d", horizon=None: stat)
    out = shr.resolve_marker_hit_fields("volume_breakout", "600519")
    assert out["horizon"] == int(shr.get_config().signal_backtest_horizon_bars)
    assert out["hit_rate"] == 0.6


def test_resolver_none_path_horizon_none(monkeypatch):
    from src.services import signal_hit_rate as shr
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "A")
    monkeypatch.setattr(shr.SignalStatsRepository, "get",
                        lambda self, st, mkt, *, interval="1d", horizon=None: None)
    out = shr.resolve_marker_hit_fields("x", "600519")
    assert out["horizon"] is None and out["hit_rate"] is None


def test_plan_quality_rules():
    full = {"entry": 1.0, "stop": 0.9, "target": 1.2}
    assert compute_plan_quality(full, "consistent") == "high"
    assert compute_plan_quality(full, "divergent") == "medium"
    assert compute_plan_quality(full, "unknown") == "medium"
    assert compute_plan_quality(full, "conflict") == "low"
    assert compute_plan_quality({"entry": 1.0, "stop": 0.9, "target": None}, "consistent") == "medium"
    assert compute_plan_quality({"entry": 1.0, "stop": None, "target": 1.2}, "consistent") == "low"
    assert compute_plan_quality({"entry": None, "stop": None, "target": None}, "consistent") is None


def test_marker_statuses_active_aging_expired():
    dates = ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18"]  # idx 0..3, last=3
    def mk(date, horizon=None):
        return {"source": "rule", "timestamp": date_str_to_epoch_ms(date),
                "horizon_bars": horizon, "status": None}
    m_latest = mk("2026-06-18")           # bars_since=0 -> active
    m_one = mk("2026-06-17", horizon=5)   # bars_since=1 < 5 -> aging
    m_old = mk("2026-06-15", horizon=2)   # bars_since=3 >= 2 -> expired
    m_llm = {"source": "llm", "timestamp": date_str_to_epoch_ms("2026-06-18"), "status": None}
    compute_marker_statuses([m_latest, m_one, m_old, m_llm], dates, default_window=10)
    assert m_latest["status"] == "active"
    assert m_one["status"] == "aging"
    assert m_old["status"] == "expired"
    assert m_llm["status"] is None  # 不动 LLM


def test_marker_status_default_window_when_horizon_none():
    dates = ["2026-06-01", "2026-06-02", "2026-06-03"]  # last idx 2
    m = {"source": "rule", "timestamp": date_str_to_epoch_ms("2026-06-01"),
         "horizon_bars": None, "status": None}  # bars_since=2; W=default
    compute_marker_statuses([m], dates, default_window=2)  # 2>=2 -> expired
    assert m["status"] == "expired"


def test_marker_status_unmatched_timestamp_none():
    dates = ["2026-06-01", "2026-06-02"]
    m = {"source": "rule", "timestamp": 999999, "horizon_bars": None, "status": None}
    compute_marker_statuses([m], dates, default_window=10)
    assert m["status"] is None


def test_marker_status_non_numeric_or_missing_timestamp_none():
    """timestamp 缺失/非数值（实流恒为 epoch ms int）→ 不崩溃，status 置 None。"""
    dates = ["2026-06-01", "2026-06-02"]
    for bad in (None, "not-a-ts", object()):
        m = {"source": "rule", "timestamp": bad, "horizon_bars": None, "status": "x"}
        compute_marker_statuses([m], dates, default_window=10)
        assert m["status"] is None
    m_missing = {"source": "rule", "horizon_bars": None, "status": "x"}  # 无 timestamp 键
    compute_marker_statuses([m_missing], dates, default_window=10)
    assert m_missing["status"] is None


def test_marker_status_zero_horizon_falls_back_to_default_window():
    """horizon_bars=0 不是有效窗口 → 回退默认窗口（而非恒判 expired）。"""
    dates = ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18"]  # last idx 3
    m = {"source": "rule", "timestamp": date_str_to_epoch_ms("2026-06-17"),
         "horizon_bars": 0, "status": None}  # bars_since=1; W=default(5) → aging
    compute_marker_statuses([m], dates, default_window=5)
    assert m["status"] == "aging"


# ---------------------------------------------------------------------------
# Task 4 tests: _hit_fields_from_markers 扩展 + _augment_payload_finer_fields
# ---------------------------------------------------------------------------


def test_hit_fields_from_markers_includes_horizon_and_signal_status():
    """_hit_fields_from_markers 返回首条 rule marker 的 horizon_bars + signal_status。"""
    markers = [
        {
            "source": "rule", "signal_type": "a",
            "hit_rate": 0.6, "hit_sample": 30,
            "verified": True, "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1,
            "horizon_bars": 10, "status": "aging",
        },
        {
            "source": "rule", "signal_type": "b",
            "hit_rate": 0.9, "horizon_bars": 20, "status": "active",
        },  # 第二条不应被取
    ]
    out = _sbs._hit_fields_from_markers(markers)
    assert out["hit_rate"] == 0.6           # 第一条 rule marker
    assert out["horizon_bars"] == 10        # 与 hit_rate 同源（同一条）
    assert out["signal_status"] == "aging"  # 同一条的 status


def test_hit_fields_from_markers_empty_has_horizon_keys():
    """空列表 → 兜底 dict 含 horizon_bars=None, signal_status=None。"""
    out = _sbs._hit_fields_from_markers([])
    assert out["horizon_bars"] is None
    assert out["signal_status"] is None


def test_hit_fields_from_markers_no_rule_markers_returns_none_fields():
    """只有 llm marker → 回退 dict 含 horizon_bars/signal_status None。"""
    markers = [{"source": "llm", "signal_type": "llm_advice", "direction": "bullish"}]
    out = _sbs._hit_fields_from_markers(markers)
    assert out["hit_rate"] is None
    assert out["horizon_bars"] is None
    assert out["signal_status"] is None


def test_hit_fields_from_markers_first_rule_wins():
    """首条 rule marker 决定所有字段，第二条不应影响。"""
    markers = [
        {"source": "rule", "signal_type": "first", "hit_rate": 0.55,
         "horizon_bars": 7, "status": "expired"},
        {"source": "rule", "signal_type": "second", "hit_rate": 0.99,
         "horizon_bars": 3, "status": "active"},
    ]
    out = _sbs._hit_fields_from_markers(markers)
    assert out["hit_rate"] == 0.55
    assert out["horizon_bars"] == 7
    assert out["signal_status"] == "expired"


def test_augment_payload_sets_plan_quality_high():
    """全线 price_lines + consistent → plan_quality == 'high'。"""
    payload = {
        "price_lines": {"entry": 10.0, "stop": 9.0, "target": 12.0},
        "consistency": "consistent",
        "markers": [],
    }
    _sbs._augment_payload_finer_fields(payload, [])
    assert payload["plan_quality"] == "high"


def test_augment_payload_sets_plan_quality_none_when_all_price_lines_null():
    """所有 price_lines 为 None → plan_quality == None。"""
    payload = {
        "price_lines": {"entry": None, "stop": None, "target": None},
        "consistency": "consistent",
        "markers": [],
    }
    _sbs._augment_payload_finer_fields(payload, [])
    assert payload["plan_quality"] is None


def test_augment_payload_sets_plan_quality_low_on_conflict():
    """entry+stop 有值但 conflict → plan_quality == 'low'。"""
    payload = {
        "price_lines": {"entry": 10.0, "stop": 9.0, "target": None},
        "consistency": "conflict",
        "markers": [],
    }
    _sbs._augment_payload_finer_fields(payload, [])
    assert payload["plan_quality"] == "low"


def test_augment_payload_writes_marker_statuses(monkeypatch):
    """compute_marker_statuses 被调用：markers 的 status 字段被 in-place 填写。"""
    import types as _t
    monkeypatch.setattr(_sbs, "get_config",
                        lambda: _t.SimpleNamespace(signal_backtest_horizon_bars=10))
    ts_0 = date_str_to_epoch_ms("2026-06-16")  # bars_since = 2 (last is index 2)
    ts_2 = date_str_to_epoch_ms("2026-06-18")  # bars_since = 0 → active

    payload = {
        "price_lines": {"entry": 10.0, "stop": 9.0, "target": 12.0},
        "consistency": "consistent",
        "markers": [
            {"source": "rule", "signal_type": "a", "timestamp": ts_0,
             "horizon_bars": None, "status": None},
            {"source": "rule", "signal_type": "b", "timestamp": ts_2,
             "horizon_bars": 5, "status": None},
        ],
    }
    rows = [
        {"date": "2026-06-16"},
        {"date": "2026-06-17"},
        {"date": "2026-06-18"},
    ]
    _sbs._augment_payload_finer_fields(payload, rows)

    statuses = {m["signal_type"]: m["status"] for m in payload["markers"] if m["source"] == "rule"}
    # marker b: bars_since=0 → active
    assert statuses["b"] == "active"
    # marker a: horizon_bars=None → default_window applies; bars_since=2
    # default_window from get_config().signal_backtest_horizon_bars = 10
    # bars_since(2) < 10 → aging
    assert statuses["a"] == "aging"


def test_d7_entry_from_board_signals_horizon_and_status_come_from_first_rule_marker():
    """_entry_from_board_signals 中 horizon_bars/signal_status 必须与 hit_rate 同源（首条 rule marker）。"""
    payload = {
        "status": "ok",
        "markers": [
            {
                "source": "rule", "signal_type": "a",
                "hit_rate": 0.65, "hit_sample": 20, "verified": True,
                "ci_low": 0.5, "ci_high": 0.8, "baseline_excess": 0.1,
                "horizon_bars": 15, "status": "aging",
            },
            {
                "source": "rule", "signal_type": "b",
                "hit_rate": 0.90, "horizon_bars": 5, "status": "active",
            },
        ],
        "price_lines": {"entry": 10.0, "stop": 9.0, "target": 12.0},
        "consistency": "consistent",
        "degraded_reason": None,
        "resonance": "none",
        "plan_quality": "high",
    }
    bs = _sbs.BoardSignals(
        signals_payload=payload,
        rule_direction="bullish",
        latest_close=100.0,
        name="TestStock",
        market="CN",
    )
    entry = _sbs._entry_from_board_signals("600519", bs)
    # hit_rate 来自首条 rule marker
    assert entry["hit_rate"] == 0.65
    # horizon_bars/signal_status 必须与 hit_rate 同源（首条）
    assert entry["horizon_bars"] == 15
    assert entry["signal_status"] == "aging"
    # plan_quality 穿透
    assert entry["plan_quality"] == "high"


def test_d7_entry_plan_quality_none_when_missing_from_payload():
    """payload 中无 plan_quality → entry 的 plan_quality 为 None。"""
    payload = {
        "status": "ok",
        "markers": [],
        "price_lines": {"entry": None, "stop": None, "target": None},
        "consistency": "unknown",
        "degraded_reason": None,
        "resonance": "none",
        # plan_quality 未设置
    }
    bs = _sbs.BoardSignals(
        signals_payload=payload,
        rule_direction="neutral",
        latest_close=50.0,
        name="X",
        market="CN",
    )
    entry = _sbs._entry_from_board_signals("X", bs)
    assert entry["plan_quality"] is None
    assert entry["horizon_bars"] is None
    assert entry["signal_status"] is None
