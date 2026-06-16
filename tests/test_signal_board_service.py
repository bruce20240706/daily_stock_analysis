from datetime import datetime
from types import SimpleNamespace

from src.stock_analyzer import BuySignal
import src.services.signal_board_service as sbs


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
