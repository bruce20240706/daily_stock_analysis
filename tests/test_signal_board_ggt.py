# -*- coding: utf-8 -*-
"""Inc 2b:看板港股通可买性注解后置 pass。"""
from unittest.mock import patch

from data_provider.fundamental_adapter import _ggt_key
import src.services.signal_board_service as sbs


def _entry(code, market, status="ok"):
    return {"code": code, "market": market, "action_group": "hold", "status": "ok"
            if status == "ok" else "degraded"}


def _patch_set(mp, value):
    class _M:
        def get_ggt_eligibility_set(self_inner):
            return value
    mp.setattr(sbs, "_get_ggt_manager", lambda: _M())


def test_annotate_hk_ok_true_false(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    out = sbs._annotate_ggt([_entry("hk00700", "HK"), _entry("hk09999", "HK")])
    assert out[0]["ggt_eligible"] is True
    assert out[1]["ggt_eligible"] is False


def test_annotate_non_hk_untouched(monkeypatch):
    spy = {"calls": 0}

    class _M:
        def get_ggt_eligibility_set(self_inner):
            spy["calls"] += 1
            return {_ggt_key("00700")}
    monkeypatch.setattr(sbs, "_get_ggt_manager", lambda: _M())
    out = sbs._annotate_ggt([_entry("600519", "CN"), _entry("AAPL", "US")])
    assert "ggt_eligible" not in out[0] and "ggt_eligible" not in out[1]
    assert spy["calls"] == 0                      # 无 HK-ok 行 → 门控短路,不抓


def test_annotate_degraded_hk_kept_none(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    out = sbs._annotate_ggt([_entry("hk00700", "HK", status="degraded")])
    assert out[0].get("ggt_eligible") is None     # degraded 不注解(status 门控)


def test_annotate_fetch_none_all_hk_none(monkeypatch):
    _patch_set(monkeypatch, None)
    out = sbs._annotate_ggt([_entry("hk00700", "HK")])
    assert out[0]["ggt_eligible"] is None         # fail-closed


def test_annotate_does_not_mutate_cached_dict(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    cached = _entry("hk00700", "HK")
    out = sbs._annotate_ggt([cached])
    assert out[0] is not cached                    # 浅拷贝,非原地改
    assert "ggt_eligible" not in cached            # 缓存对象未被 mutate


def test_board_entry_schema_accepts_ggt_eligible():
    from api.v1.schemas.stocks import BoardEntry
    base = dict(code="hk00700", action_group="hold", consistency="unknown",
                price_lines={"entry": None, "stop": None, "target": None}, status="ok")
    assert BoardEntry(**base).ggt_eligible is None                    # legacy 无字段 → None 默认
    assert BoardEntry(**base, ggt_eligible=True).ggt_eligible is True
    assert BoardEntry(**base, ggt_eligible=False).ggt_eligible is False
