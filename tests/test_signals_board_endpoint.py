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
                        lambda codes, *, days, refresh: {
                            "as_of": 1, "entries": [_entry(c) for c in codes],
                            "counts": {"buy": len(codes), "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, service=_Svc(["AAA", "BBB"]))
    assert resp.counts.buy == 2 and len(resp.entries) == 2


def test_board_endpoint_empty_watchlist(monkeypatch):
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh: {"as_of": 1, "entries": [],
                            "counts": {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, service=_Svc([]))
    assert resp.entries == []
