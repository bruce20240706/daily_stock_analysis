import types
import pytest
from src.services import signal_board_service as sbs


@pytest.fixture(autouse=True)
def _clear_cache():
    sbs._BOARD_CACHE.clear()
    yield
    sbs._BOARD_CACHE.clear()


def _engine_ok():
    return types.SimpleNamespace(markers=[], status="ok", degraded_reason=None)


def _install_common(monkeypatch, rule_direction):
    """把引擎/规则/LLM/价位/payload 都 mock 成确定值，仅留共振路径真实。"""
    monkeypatch.setattr(sbs, "compute_volume_price_signals", lambda df, config=None: _engine_ok())
    monkeypatch.setattr(sbs.StockTrendAnalyzer, "analyze",
                        lambda self, df, code: types.SimpleNamespace(buy_signal=object()))
    monkeypatch.setattr(sbs, "buy_signal_to_direction", lambda s: rule_direction)
    monkeypatch.setattr(sbs.DatabaseManager, "get_instance",
                        lambda: types.SimpleNamespace(get_latest_analysis_by_code=lambda code: None))
    monkeypatch.setattr(sbs, "build_signals_payload",
                        lambda **kw: {"status": "ok", "markers": [], "consistency": "unknown",
                                      "price_lines": {"entry": None, "stop": None, "target": None}})
    monkeypatch.setattr(sbs, "derive_price_levels", lambda df, **kw: None)
    # build_price_lines is imported lazily inside build_signals_for_code
    import api.v1.endpoints.stocks as stocks_ep
    monkeypatch.setattr(stocks_ep, "build_price_lines",
                        lambda lv: types.SimpleNamespace(
                            model_dump=lambda: {"entry": None, "stop": None, "target": None}
                        ))
    monkeypatch.setattr(stocks_ep, "_elapsed_trading_days", lambda rec, rows: 0)


def _spy_history(monkeypatch, *, deep_direction_payload):
    """spy get_history_data: 记录 (period, days)，返回少量日线行。"""
    calls = []
    base_rows = [
        {"date": "2024-01-0%d" % d, "open": 1, "high": 1, "low": 1, "close": 1,
         "volume": 1, "amount": 1, "change_percent": None}
        for d in range(1, 6)
    ]

    def fake(self, stock_code, period="daily", days=30):
        calls.append((period, days))
        return {"stock_code": stock_code, "stock_name": "X", "period": period, "data": base_rows}

    monkeypatch.setattr(sbs.StockService, "get_history_data", fake)
    return calls


def test_directional_entry_triggers_gated_deep_fetch_and_resonance(monkeypatch):
    _install_common(monkeypatch, rule_direction="bullish")
    calls = _spy_history(monkeypatch, deep_direction_payload="bullish")
    monkeypatch.setattr(sbs, "resonance_from_daily", lambda df, d: "weekly_monthly")
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "weekly_monthly"
    # 两次抓取：信号 days=120 + 共振深抓 days=RESONANCE_DAILY_DAYS
    assert ("daily", 120) in calls
    assert ("daily", sbs.RESONANCE_DAILY_DAYS) in calls


def test_hold_entry_skips_resonance_fetch(monkeypatch):
    _install_common(monkeypatch, rule_direction="neutral")
    calls = _spy_history(monkeypatch, deep_direction_payload="x")
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "none"
    assert [c for c in calls if c[1] == sbs.RESONANCE_DAILY_DAYS] == []  # 未触发深抓


def test_resonance_failure_degrades_to_none(monkeypatch):
    _install_common(monkeypatch, rule_direction="bullish")
    _spy_history(monkeypatch, deep_direction_payload="bullish")

    def boom(df, d):
        raise RuntimeError("resample failed")

    monkeypatch.setattr(sbs, "resonance_from_daily", boom)
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "none"         # 降级
    assert entry["action_group"] == "buy"       # 其余字段正常
