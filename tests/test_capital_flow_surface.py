import copy
import types
from src.analyzer import _build_capital_flow_from_context, fill_capital_flow_if_needed, _dragon_tiger_prompt_line


def _ctx(cf_status="ok", dt_status="ok", *, mni=1.2e8, i5=None, i10=None, on_list=True):
    """Build a minimal fundamental_context dict.

    i5/i10 default to the same direction as mni so fixtures are coherent unless
    a test deliberately passes conflicting values.
    """
    if i5 is None:
        i5 = 3.0e7 if mni >= 0 else -3.0e7
    if i10 is None:
        i10 = 5.0e7 if mni >= 0 else -5.0e7
    return {
        "capital_flow": {"status": cf_status, "data": {"stock_flow": {
            "main_net_inflow": mni, "inflow_5d": i5, "inflow_10d": i10}}},
        "dragon_tiger": {"status": dt_status, "data": {
            "is_on_list": on_list, "recent_count": 2, "latest_date": "2026-06-17"}},
    }


def test_build_inflow_status_and_dragon_tiger():
    # all-positive coherent set → bias=inflow → 净流入
    out = _build_capital_flow_from_context(_ctx(mni=1.2e8), language="zh")
    assert out["main_net_inflow"] == 1.2e8
    assert out["net_flow_status"] == "净流入"
    assert out["dragon_tiger_on_list"] is True
    assert out["dragon_tiger_recent_count"] == 2
    assert out["dragon_tiger_latest_date"] == "2026-06-17"


def test_build_outflow_and_english():
    # all-negative coherent set → bias=outflow → Outflow
    out = _build_capital_flow_from_context(_ctx(mni=-9.9e7), language="en")
    assert out["net_flow_status"] == "Outflow"


def test_build_conflict_signals_returns_neutral():
    # Conflicting signals: mni>0 but inflow_5d<0 → bias=neutral → 中性.
    # This is the key invariant: display and decision share the same source.
    out = _build_capital_flow_from_context(
        _ctx(mni=1.2e8, i5=-3.0e7, i10=5.0e7), language="zh"
    )
    assert out["net_flow_status"] == "中性"


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


def test_dragon_tiger_prompt_line_on_list():
    line = _dragon_tiger_prompt_line(_ctx(on_list=True))
    assert "龙虎榜" in line and "2" in line and "2026-06-17" in line


def test_dragon_tiger_prompt_line_absent_when_not_on_list():
    assert _dragon_tiger_prompt_line(_ctx(on_list=False)) == ""


def test_dragon_tiger_prompt_line_absent_when_not_supported():
    assert _dragon_tiger_prompt_line(_ctx(dt_status="not_supported", on_list=False)) == ""
    assert _dragon_tiger_prompt_line(None) == ""


# ---------------------------------------------------------------------------
# Task 5: 决策回归锁定 — fill 不改 decision_stability，不向 data_perspective 注入外溢键
# ---------------------------------------------------------------------------

def _result_with_dashboard(decision_type="buy"):
    return types.SimpleNamespace(
        dashboard={}, report_language="zh", decision_type=decision_type,
        confidence_level="高", operation_advice="买入",
    )


def test_capital_flow_fill_then_stabilize_no_decision_stability_keys_added():
    # fill 先于 stabilize（LLM 后）运行；fill 只写 data_perspective.capital_flow，不碰 decision_stability
    result = _result_with_dashboard()
    fill_capital_flow_if_needed(result, _ctx())
    ds_before = copy.deepcopy(result.dashboard.get("decision_stability"))
    assert ds_before is None  # fill 不建 decision_stability
    cf = result.dashboard["data_perspective"]["capital_flow"]
    assert set(cf) == {
        "main_net_inflow", "inflow_5d", "inflow_10d", "net_flow_status",
        "dragon_tiger_on_list", "dragon_tiger_recent_count", "dragon_tiger_latest_date",
    }  # 无外溢键
