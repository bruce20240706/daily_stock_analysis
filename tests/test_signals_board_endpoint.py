# -*- coding: utf-8 -*-
"""Contract tests for GET /api/v1/signals/board endpoint (容器 C / N1.3)."""

import api.v1.endpoints.signals as board_ep


class _Svc:
    def __init__(self, codes):
        self._codes = codes

    def get_config(self, include_schema=False):
        return {"items": [{"key": "STOCK_LIST", "value": ",".join(self._codes)}]}


def _entry(code, action="buy"):
    return {"code": code, "name": None, "market": "CN", "action_group": action,
            "rule_direction": "bullish", "llm_direction": None, "consistency": "consistent",
            "key_signals": [], "price_lines": {"entry": None, "stop": None, "target": None},
            "latest_close": None, "hit_rate": None, "hit_sample": None, "verified": False,
            "status": "ok", "degraded_reason": None}


def test_board_endpoint_groups(monkeypatch):
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh, interval="1d": {
                            "as_of": 1, "entries": [_entry(c) for c in codes],
                            "counts": {"buy": len(codes), "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, interval="1d", service=_Svc(["AAA", "BBB"]))
    assert resp.counts.buy == 2 and len(resp.entries) == 2


def test_board_endpoint_empty_watchlist(monkeypatch):
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh, interval="1d": {"as_of": 1, "entries": [],
                            "counts": {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, interval="1d", service=_Svc([]))
    assert resp.entries == []


# T4: /board resonance field end-to-end via BoardEntry(**entry) splat path
def _entry_with_resonance(code, resonance):
    """Entry dict WITH explicit resonance value."""
    base = _entry(code)
    base["resonance"] = resonance
    return base


def test_board_entry_resonance_weekly_monthly(monkeypatch):
    """BoardEntry splat with resonance='weekly_monthly' should be accepted and round-trip."""
    entry_dict = _entry_with_resonance("AAA", "weekly_monthly")
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh, interval="1d": {
                            "as_of": 1,
                            "entries": [entry_dict],
                            "counts": {"buy": 1, "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": [],
                        })
    resp = board_ep.get_signals_board(days=120, refresh=False, interval="1d", service=_Svc(["AAA"]))
    assert len(resp.entries) == 1
    assert resp.entries[0].resonance == "weekly_monthly"


def test_board_entry_resonance_defaults_to_none(monkeypatch):
    """BoardEntry splat WITHOUT resonance key should default to 'none'."""
    entry_dict = _entry("BBB")  # no resonance key
    assert "resonance" not in entry_dict
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh, interval="1d": {
                            "as_of": 1,
                            "entries": [entry_dict],
                            "counts": {"buy": 1, "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": [],
                        })
    resp = board_ep.get_signals_board(days=120, refresh=False, interval="1d", service=_Svc(["BBB"]))
    assert len(resp.entries) == 1
    assert resp.entries[0].resonance == "none"


# === 链路B 分钟化:board 端点 interval 透传与校验(B-T3)===
def test_board_endpoint_passes_interval_to_build_board(monkeypatch):
    seen = {}
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh, interval="1d": (
                            seen.update(interval=interval),
                            {"as_of": 1, "entries": [], "degraded_codes": [],
                             "counts": {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0}})[1])
    board_ep.get_signals_board(days=120, refresh=False, interval="5m", service=_Svc(["AAA"]))
    assert seen["interval"] == "5m"


def test_board_endpoint_rejects_bad_interval(monkeypatch):
    import pytest
    from fastapi import HTTPException
    # build_board 不应被调用:校验在前
    monkeypatch.setattr(board_ep, "build_board",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    with pytest.raises(HTTPException) as ei:
        board_ep.get_signals_board(days=120, refresh=False, interval="2h", service=_Svc(["AAA"]))
    assert ei.value.status_code == 422
