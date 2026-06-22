# -*- coding: utf-8 -*-
import copy
import types

from src.schemas.report_schema import DataPerspective, MarginTrading


def test_margin_trading_model_fields():
    mt = MarginTrading(
        financing_balance=1.23e8, financing_buy=4.5e7,
        short_volume=1000, trade_date="20260619", exchange="SSE",
    )
    assert mt.financing_balance == 1.23e8
    assert mt.trade_date == "20260619"
    assert mt.exchange == "SSE"


def test_margin_trading_all_optional():
    mt = MarginTrading()
    assert mt.financing_balance is None
    assert mt.exchange is None


def test_data_perspective_backward_compatible_without_margin():
    # 旧 payload 无 margin_trading 键仍解析；默认 None。
    dp = DataPerspective.model_validate({"chip_structure": {"chip_health": "健康"}})
    assert dp.margin_trading is None


def test_data_perspective_accepts_margin_trading():
    dp = DataPerspective.model_validate(
        {"margin_trading": {"financing_balance": 1.0e8, "exchange": "SZSE"}}
    )
    assert dp.margin_trading.exchange == "SZSE"


# --- Task 4: builder / fill / fail-open / decision read-only ---
from src.analyzer import _build_margin_from_context, fill_margin_if_needed  # noqa: E402


def _mctx(status="ok"):
    return {
        "margin": {"status": status, "data": {
            "financing_balance": 1.23e8, "financing_buy": 4.5e7,
            "short_volume": 1000.0, "trade_date": "20260619", "exchange": "SSE",
        }}
    }


def test_build_margin_ok():
    out = _build_margin_from_context(_mctx("ok"))
    assert set(out) >= {"financing_balance", "financing_buy", "short_volume", "trade_date", "exchange"}
    assert out["financing_balance"] == 1.23e8
    assert out["exchange"] == "SSE"
    assert out["trade_date"] == "20260619"


def test_build_margin_partial_still_builds():
    out = _build_margin_from_context(_mctx("partial"))
    assert out is not None
    assert out["exchange"] == "SSE"


def test_build_margin_not_supported_returns_none():
    assert _build_margin_from_context(_mctx("not_supported")) is None
    assert _build_margin_from_context(_mctx("failed")) is None
    assert _build_margin_from_context({}) is None
    assert _build_margin_from_context(None) is None


def test_fill_sets_margin_trading():
    result = types.SimpleNamespace(dashboard={}, report_language="zh")
    fill_margin_if_needed(result, _mctx("ok"))
    mt = result.dashboard["data_perspective"]["margin_trading"]
    assert mt["financing_balance"] == 1.23e8
    # 冻结词表：无外溢/决策键
    assert set(mt) == {"financing_balance", "financing_buy", "short_volume", "trade_date", "exchange"}


def test_fill_not_supported_leaves_margin_absent():
    result = types.SimpleNamespace(dashboard={"data_perspective": {}}, report_language="zh")
    fill_margin_if_needed(result, _mctx("not_supported"))
    assert "margin_trading" not in result.dashboard["data_perspective"]


def test_fill_fail_open_on_exception(monkeypatch):
    import src.analyzer as az
    monkeypatch.setattr(az, "_build_margin_from_context",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    result = types.SimpleNamespace(dashboard={"data_perspective": {}}, report_language="zh")
    fill_margin_if_needed(result, _mctx("ok"))  # must not raise
    assert "margin_trading" not in result.dashboard["data_perspective"]


def test_fill_margin_does_not_touch_decision_stability():
    result = types.SimpleNamespace(
        dashboard={}, report_language="zh", decision_type="buy",
        confidence_level="高", operation_advice="买入",
    )
    fill_margin_if_needed(result, _mctx("ok"))
    assert result.dashboard.get("decision_stability") is None
    assert result.decision_type == "buy"  # 决策不变
