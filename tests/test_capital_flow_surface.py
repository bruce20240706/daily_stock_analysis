import types
from src.analyzer import _build_capital_flow_from_context, fill_capital_flow_if_needed


def _ctx(cf_status="ok", dt_status="ok", *, mni=1.2e8, on_list=True):
    return {
        "capital_flow": {"status": cf_status, "data": {"stock_flow": {
            "main_net_inflow": mni, "inflow_5d": -3.0e7, "inflow_10d": 5.0e7}}},
        "dragon_tiger": {"status": dt_status, "data": {
            "is_on_list": on_list, "recent_count": 2, "latest_date": "2026-06-17"}},
    }


def test_build_inflow_status_and_dragon_tiger():
    out = _build_capital_flow_from_context(_ctx(mni=1.2e8), language="zh")
    assert out["main_net_inflow"] == 1.2e8
    assert out["net_flow_status"] == "净流入"
    assert out["dragon_tiger_on_list"] is True
    assert out["dragon_tiger_recent_count"] == 2
    assert out["dragon_tiger_latest_date"] == "2026-06-17"


def test_build_outflow_and_english():
    out = _build_capital_flow_from_context(_ctx(mni=-9.9e7), language="en")
    assert out["net_flow_status"] == "Outflow"


def test_build_not_supported_returns_none():
    out = _build_capital_flow_from_context(_ctx(cf_status="not_supported", dt_status="not_supported"))
    assert out is None


def test_build_dragon_tiger_not_on_list():
    out = _build_capital_flow_from_context(_ctx(on_list=False))
    assert out["dragon_tiger_on_list"] is False


def test_fill_sets_data_perspective_capital_flow():
    result = types.SimpleNamespace(dashboard={}, report_language="zh")
    fill_capital_flow_if_needed(result, _ctx())
    assert result.dashboard["data_perspective"]["capital_flow"]["net_flow_status"] == "净流入"


def test_fill_not_supported_leaves_capital_flow_absent():
    result = types.SimpleNamespace(dashboard={"data_perspective": {}}, report_language="zh")
    fill_capital_flow_if_needed(result, _ctx(cf_status="not_supported", dt_status="not_supported"))
    assert "capital_flow" not in result.dashboard["data_perspective"]
