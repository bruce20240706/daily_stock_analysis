from datetime import datetime
from types import SimpleNamespace

import pytest

from src.stock_analyzer import BuySignal
import src.services.signal_board_service as sbs


@pytest.fixture(autouse=True)
def _clear_board_cache():
    sbs._BOARD_CACHE.clear()
    yield
    sbs._BOARD_CACHE.clear()


def _bar(date, close):
    return {"date": date, "open": close, "high": close + 5, "low": close - 5,
            "close": close, "volume": 1000, "amount": 0}


def _fake_history(rows, name="贵州茅台"):
    return {"stock_code": "600519", "stock_name": name, "period": "daily", "data": rows}


def _patch(monkeypatch, *, rows, engine_result, rule_signal, llm_record):
    monkeypatch.setattr(sbs.StockService, "get_history_data",
                        lambda self, stock_code, period="daily", days=120: _fake_history(rows))
    monkeypatch.setattr(sbs, "compute_volume_price_signals",
                        lambda df, config=None: engine_result)

    class _A:
        def __init__(self, *a, **k): pass
        def analyze(self, df, code): return SimpleNamespace(buy_signal=rule_signal)
    monkeypatch.setattr(sbs, "StockTrendAnalyzer", _A)

    class _DB:
        def get_latest_analysis_by_code(self, code): return llm_record
    monkeypatch.setattr(sbs.DatabaseManager, "get_instance", classmethod(lambda cls: _DB()))


def test_build_signals_for_code_returns_board_signals(monkeypatch):
    engine = SimpleNamespace(markers=[], status="ok", degraded_reason=None)
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))
    _patch(monkeypatch, rows=[_bar("2026-06-11", 1790.0), _bar("2026-06-12", 1800.0)],
           engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    bs = sbs.build_signals_for_code("600519", days=120)

    assert set(bs.signals_payload) == {"status", "markers", "price_lines", "consistency", "degraded_reason"}
    assert bs.signals_payload["status"] == "ok"
    assert bs.rule_direction == "bullish"
    assert bs.latest_close == 1800.0
    assert bs.name == "贵州茅台"


def test_build_signals_for_code_no_data_is_unavailable(monkeypatch):
    _patch(monkeypatch, rows=[], engine_result=SimpleNamespace(markers=[], status="degraded", degraded_reason="x"),
           rule_signal=None, llm_record=None)

    bs = sbs.build_signals_for_code("600519", days=120)

    assert bs.signals_payload["status"] == "degraded"
    assert bs.rule_direction is None
    assert bs.latest_close is None


def _bs(direction, *, status="ok", markers=None):
    return sbs.BoardSignals(
        signals_payload={
            "status": status, "markers": markers or [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "consistent", "degraded_reason": None,
        },
        rule_direction=direction, latest_close=100.0, name="N", market="CN",
    )


def test_build_board_groups_and_counts(monkeypatch):
    mapping = {"AAA": _bs("bullish"), "BBB": _bs("neutral"), "CCC": _bs("bearish")}
    monkeypatch.setattr(sbs, "build_signals_for_code", lambda code, *, days=120: mapping[code])
    out = sbs.build_board(["AAA", "BBB", "CCC"], days=120, refresh=True)
    groups = {e["code"]: e["action_group"] for e in out["entries"]}
    assert groups == {"AAA": "buy", "BBB": "hold", "CCC": "sell"}
    assert out["counts"] == {"buy": 1, "hold": 1, "sell": 1, "unavailable": 0}
    assert out["degraded_codes"] == []
    assert isinstance(out["as_of"], int)


def test_build_board_single_failure_degrades_only_that_row(monkeypatch):
    def fake(code, *, days=120):
        if code == "BAD":
            raise RuntimeError("boom")
        return _bs("bullish")
    monkeypatch.setattr(sbs, "build_signals_for_code", fake)
    out = sbs.build_board(["AAA", "BAD"], days=120, refresh=True)
    bad = next(e for e in out["entries"] if e["code"] == "BAD")
    assert bad["action_group"] == "unavailable" and bad["status"] == "degraded"
    assert out["counts"]["buy"] == 1 and out["counts"]["unavailable"] == 1
    assert "BAD" in out["degraded_codes"]
    assert any(e["code"] == "AAA" and e["action_group"] == "buy" for e in out["entries"])


def test_build_board_unavailable_when_rule_direction_none(monkeypatch):
    monkeypatch.setattr(sbs, "build_signals_for_code",
                        lambda code, *, days=120: _bs(None, status="degraded"))
    out = sbs.build_board(["X"], days=120, refresh=True)
    assert out["entries"][0]["action_group"] == "unavailable"
    assert out["counts"]["unavailable"] == 1


def test_build_board_maps_markers_to_entry_fields(monkeypatch):
    markers = [
        {"source": "rule", "signal_type": "volume_breakout", "direction": "bullish",
         "hit_rate": 0.62, "hit_sample": 18, "verified": True},
        {"source": "rule", "signal_type": "shrink_pullback", "direction": "bullish",
         "hit_rate": 0.62, "hit_sample": 18, "verified": True},
        {"source": "llm", "signal_type": "llm_advice", "direction": "bearish"},
    ]
    monkeypatch.setattr(sbs, "build_signals_for_code",
                        lambda code, *, days=120: _bs("bullish", markers=markers))
    e = sbs.build_board(["AAA"], days=120, refresh=True)["entries"][0]
    assert e["key_signals"] == ["volume_breakout", "shrink_pullback"]   # rule 去重保序
    assert e["llm_direction"] == "bearish"
    assert e["hit_rate"] == 0.62 and e["hit_sample"] == 18 and e["verified"] is True
    assert e["consistency"] == "consistent"


def test_build_board_empty(monkeypatch):
    out = sbs.build_board([], days=120, refresh=True)
    assert out["entries"] == [] and out["counts"]["buy"] == 0


def test_build_board_cache_hit(monkeypatch):
    calls = {"n": 0}
    def fake(code, *, days=120):
        calls["n"] += 1
        return _bs("bullish")
    monkeypatch.setattr(sbs, "build_signals_for_code", fake)
    sbs._BOARD_CACHE.clear()
    sbs.build_board(["AAA"], days=120, refresh=True)    # 计算并写缓存
    sbs.build_board(["AAA"], days=120, refresh=False)   # 命中缓存，不再算
    assert calls["n"] == 1


def test_build_board_refresh_bypasses_populated_cache(monkeypatch):
    calls = {"n": 0}
    def fake_a(code, *, days=120):
        calls["n"] += 1
        return _bs("bullish")            # ok-status, action buy
    monkeypatch.setattr(sbs, "build_signals_for_code", fake_a)
    sbs.build_board(["AAA"], days=120, refresh=False)   # compute + cache
    assert calls["n"] == 1
    def fake_b(code, *, days=120):
        calls["n"] += 1
        return _bs("bearish")            # ok-status, action sell
    monkeypatch.setattr(sbs, "build_signals_for_code", fake_b)
    out = sbs.build_board(["AAA"], days=120, refresh=True)   # must recompute, NOT serve cached buy
    assert calls["n"] == 2
    assert out["entries"][0]["action_group"] == "sell"


def test_build_board_ttl_zero_disables_cache_writes(monkeypatch):
    monkeypatch.setenv("SIGNALS_BOARD_CACHE_TTL_S", "0")
    monkeypatch.setattr(sbs, "build_signals_for_code", lambda code, *, days=120: _bs("bullish"))
    sbs.build_board(["AAA"], days=120, refresh=False)
    assert sbs._BOARD_CACHE == {}        # caching off → nothing stored


def test_build_board_does_not_cache_degraded(monkeypatch):
    calls = {"n": 0}
    def fake(code, *, days=120):
        calls["n"] += 1
        raise RuntimeError("transient")
    monkeypatch.setattr(sbs, "build_signals_for_code", fake)
    sbs.build_board(["BAD"], days=120, refresh=False)        # degraded -> must NOT cache
    out = sbs.build_board(["BAD"], days=120, refresh=False)  # cache miss -> retried
    assert calls["n"] == 2
    assert out["entries"][0]["action_group"] == "unavailable"
    assert out["entries"][0]["status"] == "degraded"
