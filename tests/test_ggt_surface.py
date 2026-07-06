# -*- coding: utf-8 -*-
"""
Task 5: GgtContext schema + _build_ggt_from_context/fill_ggt_if_needed(analyzer)
+ pipeline.py 两处接线(Blocker CONTRACT-2)测试。
"""
import json
from types import SimpleNamespace

from src.analyzer import _build_ggt_from_context, fill_ggt_if_needed
from src.schemas.report_schema import GgtContext


def _ctx(status, eligible=True):
    return {"ggt": {"status": status, "data": {
        "eligible": eligible,
        "holding": {"holding_shares": 200, "holding_value": 2200.0,
                    "holding_ratio_pct": 6.5, "holding_trade_date": "2026-07-01"},
        "southbound_flow": {"southbound_net_flow": 20.0, "flow_date": None, "partial": False}}}}


def test_build_ggt_presence_only_ok():
    built = _build_ggt_from_context(_ctx("ok"))
    assert built["eligible"] is True
    assert built["holding_shares"] == 200 and built["holding_ratio_pct"] == 6.5
    assert built["southbound_net_flow"] == 20.0


def test_build_ggt_failed_returns_none():
    assert _build_ggt_from_context(_ctx("failed")) is None
    assert _build_ggt_from_context(_ctx("not_supported")) is None


def test_build_ggt_product_constructs_schema_key_parity():
    # F10:用真实产物直接构造 GgtContext 钉键名奇偶(非手写 dict)
    built = _build_ggt_from_context(_ctx("ok"))
    model = GgtContext(**built)                       # 键名漂移会因多余键被忽略→字段全 None,下断言即防线
    assert model.eligible is True and model.holding_shares == 200


def test_fill_ggt_wires_into_data_perspective():
    # Blocker CONTRACT-2:fill 把 ggt_context 填进 data_perspective;failed 不填
    result = SimpleNamespace(dashboard={}, report_language="zh")
    fill_ggt_if_needed(result, _ctx("ok"))
    assert result.dashboard["data_perspective"]["ggt_context"]["eligible"] is True
    result2 = SimpleNamespace(dashboard={}, report_language="zh")
    fill_ggt_if_needed(result2, _ctx("failed"))
    assert "ggt_context" not in result2.dashboard.get("data_perspective", {})


# --- MUST-ADD: NaN→None coercion hazard pin ---


def test_build_ggt_coerces_nan_to_none():
    ctx = {"ggt": {"status": "ok", "data": {
        "eligible": None,
        "holding": {"holding_shares": float("nan"), "holding_value": float("nan"),
                    "holding_ratio_pct": float("nan"), "holding_trade_date": None},
        "southbound_flow": {"southbound_net_flow": float("nan"), "flow_date": None}}}}
    built = _build_ggt_from_context(ctx)
    assert built["holding_shares"] is None
    assert built["holding_value"] is None
    assert built["holding_ratio_pct"] is None
    assert built["southbound_net_flow"] is None
    # 真正的隐患钉:allow_nan=False 下若有 NaN 漏网会抛 ValueError
    assert json.dumps(built, allow_nan=False)
    assert GgtContext(**built).holding_shares is None


def test_build_ggt_partial_status_also_builds():
    # 与 margin 一致:ok 与 partial 均构建(非 None)
    out = _build_ggt_from_context(_ctx("partial"))
    assert out is not None
    assert out["eligible"] is True


def test_fill_ggt_both_pipeline_call_sites_present():
    # 静态守卫,钉住 Blocker CONTRACT-2:两处调用点均须存在,防未来半接线回归。
    with open("src/core/pipeline.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert content.count("fill_ggt_if_needed(result, fundamental_context)") == 2
    import_block = content[:content.index("from src.notification import")]
    assert "fill_ggt_if_needed" in import_block


# --- Task 6: report_language 双语 + notification 三态渲染 ---


def test_ggt_render_three_state_eligible_distinct_text():
    from src.notification import _render_ggt_section

    ok_true = {"eligible": True, "holding_shares": 200, "holding_ratio_pct": 6.5,
               "southbound_net_flow": 20.0}
    ok_false = {"eligible": False}
    ok_none = {"eligible": None}
    t_true = _render_ggt_section(ok_true, "zh")
    t_false = _render_ggt_section(ok_false, "zh")
    t_none = _render_ggt_section(ok_none, "zh")
    assert "港股通标的" in t_true
    assert "非港股通标的" in t_false
    assert "成份状态未知" in t_none
    # None 输出禁含 False 文案(F10/§6):None 不得读成"非港股通标的"
    assert "非港股通标的" not in t_none


def test_ggt_render_none_section_empty():
    from src.notification import _render_ggt_section

    assert _render_ggt_section(None, "zh").strip() == ""


def test_ggt_render_en_three_state():
    from src.notification import _render_ggt_section

    t_true = _render_ggt_section({"eligible": True}, "en")
    t_false = _render_ggt_section({"eligible": False}, "en")
    t_none = _render_ggt_section({"eligible": None}, "en")
    assert "HKSC Eligible" in t_true
    assert "Not HKSC Eligible" in t_false
    assert "HKSC Status Unknown" in t_none
    assert "Not HKSC Eligible" not in t_none


def test_ggt_render_numeric_none_fields_skipped():
    from src.notification import _render_ggt_section

    text = _render_ggt_section(
        {"eligible": True, "holding_ratio_pct": None, "southbound_net_flow": None}, "zh"
    )
    assert "港股通标的" in text
    assert "南向持股占比" not in text
    assert "南向净流" not in text
    assert "None" not in text
