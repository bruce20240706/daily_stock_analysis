# M4-B 资金面呈现 + 龙虎榜激活 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已抓取但未呈现的资金面数据（主力资金流 + 龙虎榜）确定性补进报告 `CapitalFlow` section 与 notification，并以 presence-only 激活龙虎榜进 LLM prompt；不改动既有资金面决策行为。

**Architecture:** 纯后端、additive、A股-gated、presence-only。新增报告 schema `CapitalFlow` section + 确定性后处理填充 `fill_capital_flow_if_needed`（镜像 chip 填充，LLM 后、对决策只读）+ 龙虎榜 presence-only prompt 行 + notification 资金面块。**不写 `decision_stability`**（内部专用、零用户渲染），龙虎榜走用户可见面。

**Tech Stack:** Python / FastAPI / Pydantic（`report_schema.py`）/ pytest。无前端代码改动。

**基线分支：** `feat/m4b-capital-flow` ← `main@1c794c62`（独立，可独立合入 main）。Spec：`docs/superpowers/specs/2026-06-18-m4b-capital-flow-surface-design.md`。

## Global Constraints

- commit message：英文类型前缀 + 中文描述（如 `feat: 资金面 CapitalFlow section ...`）；**不加 `Co-Authored-By`**；不加工具/agent 前缀。
- **A股-gated + presence-only**：非 A股/港股/美股/crypto/ETF → capital_flow/dragon_tiger = `not_supported` → section 不出现、无 prompt 行、notification 不渲染。
- **零改动既有资金面决策**：`stabilize_decision_with_structure`、`_downgrade_buy_without_capital_flow`、`_set_decision_stability_unavailable`、`_downgrade_to_structural_hold`、`_capital_flow_bias_with_status`、主力资金流 prompt 量级表（`analyzer.py:3155` 的「### 主力资金流向」）**一律不改**。
- **不写 `decision_stability`**：不新增任何键（内部专用、零用户渲染）。
- **确定性填充**：资金数字来自 `fundamental_context`，不经 LLM 生成；龙虎榜填充在 LLM 后、对决策只读。
- 零新增配置/数据源/token（复用既有 akshare `fundamental_adapter`）。
- 追加式 schema：新字段带默认值（`Optional[...] = None`），旧 payload 无该键仍解析。
- `net_flow_status` 复用 `_capital_flow_bias_with_status(fundamental_context)[0]` 的 bias，人读映射（zh：净流入/净流出/中性/未知；en：Inflow/Outflow/Neutral/Unknown），不引入新阈值。
- 门禁：后端 `./scripts/ci_gate.sh`（flake8 critical E9/F63/F7/F82 + `pytest -m "not network"`）；纯后端、不触发 web-gate。venv：`/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`，从 `/root/dsa-m4b` 运行。
- ASCII 引号写 Python 代码；测试 import 置文件顶部（避免 E402，虽 ci_gate 仅查 E9/F63/F7/F82，但保持整洁）。

---

### Task 1: 报告 schema 新增 CapitalFlow section

**Files:**
- Modify: `src/schemas/report_schema.py`（`ChipStructure` 后 ~70 加 `CapitalFlow`；`DataPerspective` ~78 加字段）
- Test: `tests/test_report_schema.py`

**Interfaces:**
- Produces: `CapitalFlow` pydantic 模型（字段见下）；`DataPerspective.capital_flow: Optional[CapitalFlow] = None`。

- [ ] **Step 1: 写失败测试**（加到 `tests/test_report_schema.py`）

```python
from src.schemas.report_schema import CapitalFlow, DataPerspective


def test_capital_flow_model_fields():
    cf = CapitalFlow(
        main_net_inflow=1.2e8, inflow_5d=-3.4e7, inflow_10d=5.6e7,
        net_flow_status="净流入",
        dragon_tiger_on_list=True, dragon_tiger_recent_count=2, dragon_tiger_latest_date="2026-06-17",
    )
    assert cf.net_flow_status == "净流入"
    assert cf.dragon_tiger_on_list is True
    assert cf.dragon_tiger_recent_count == 2


def test_data_perspective_capital_flow_optional_backward_compat():
    # 旧 payload 无 capital_flow 键仍解析；默认 None
    dp = DataPerspective(**{"trend_status": None})
    assert dp.capital_flow is None
    dp2 = DataPerspective(capital_flow=CapitalFlow(net_flow_status="中性"))
    assert dp2.capital_flow.net_flow_status == "中性"
```

- [ ] **Step 2: 运行确认失败**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_report_schema.py -q`（cwd `/root/dsa-m4b`）
Expected: FAIL（`ImportError: cannot import name 'CapitalFlow'`）

- [ ] **Step 3: 实现 schema**

`report_schema.py` 在 `class ChipStructure` 之后、`class DataPerspective` 之前加：

```python
class CapitalFlow(BaseModel):
    """资金面（A股；主力资金流 + 龙虎榜存在性）。"""

    main_net_inflow: Optional[Union[int, float, str]] = None
    inflow_5d: Optional[Union[int, float, str]] = None
    inflow_10d: Optional[Union[int, float, str]] = None
    net_flow_status: Optional[str] = None
    dragon_tiger_on_list: Optional[bool] = None
    dragon_tiger_recent_count: Optional[int] = None
    dragon_tiger_latest_date: Optional[str] = None
```

`class DataPerspective` 加一行（在 `chip_structure` 后）：

```python
    capital_flow: Optional[CapitalFlow] = None
```

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_report_schema.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/schemas/report_schema.py tests/test_report_schema.py
git commit -m "feat: 报告 schema 新增 CapitalFlow section(主力资金流+龙虎榜,追加式可选)"
```

---

### Task 2: 确定性填充 fill_capital_flow_if_needed + pipeline 接线

**Files:**
- Modify: `src/analyzer.py`（新增 `_build_capital_flow_from_context` + `fill_capital_flow_if_needed`，放在 `fill_chip_structure_if_needed`（~819-844）附近）
- Modify: `src/core/pipeline.py`（import + Step 7.6b 调用，~621 后）
- Test: `tests/test_capital_flow_surface.py`（新建）

**Interfaces:**
- Consumes: `_capital_flow_bias_with_status`（`analyzer.py:1180`，返回 `(bias, reason)`，bias∈inflow/outflow/neutral/unavailable）。
- Produces: `fill_capital_flow_if_needed(result, fundamental_context) -> None`（in-place 填 `result.dashboard['data_perspective']['capital_flow']`）；`_build_capital_flow_from_context(fundamental_context, language='zh') -> Optional[dict]`。

- [ ] **Step 1: 写失败测试** `tests/test_capital_flow_surface.py`

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_capital_flow_surface.py -q`
Expected: FAIL（import 缺失）

- [ ] **Step 3: 实现 `analyzer.py`**

在 `fill_chip_structure_if_needed`（~844）之后新增（模块级）：

```python
_NET_FLOW_STATUS_ZH = {"inflow": "净流入", "outflow": "净流出", "neutral": "中性", "unavailable": "未知"}
_NET_FLOW_STATUS_EN = {"inflow": "Inflow", "outflow": "Outflow", "neutral": "Neutral", "unavailable": "Unknown"}


def _build_capital_flow_from_context(
    fundamental_context: Optional[Dict[str, Any]], language: str = "zh"
) -> Optional[Dict[str, Any]]:
    """从 fundamental_context 的 capital_flow + dragon_tiger 块确定性构建资金面 section dict。

    presence-only：两块 status 均非 ok/partial（含非 A股/ETF 的 not_supported）→ 返回 None（section 不出现）。
    net_flow_status 复用 _capital_flow_bias_with_status 的 bias，人读映射；不引入新阈值。
    """
    if not isinstance(fundamental_context, dict):
        return None
    cf = fundamental_context.get("capital_flow")
    dt = fundamental_context.get("dragon_tiger")
    cf = cf if isinstance(cf, dict) else {}
    dt = dt if isinstance(dt, dict) else {}
    cf_ok = str(cf.get("status") or "").strip().lower() in ("ok", "partial")
    dt_ok = str(dt.get("status") or "").strip().lower() in ("ok", "partial")
    if not cf_ok and not dt_ok:
        return None
    out: Dict[str, Any] = {
        "main_net_inflow": None, "inflow_5d": None, "inflow_10d": None,
        "net_flow_status": None,
        "dragon_tiger_on_list": None, "dragon_tiger_recent_count": None, "dragon_tiger_latest_date": None,
    }
    if cf_ok:
        data = cf.get("data") if isinstance(cf.get("data"), dict) else {}
        stock_flow = data.get("stock_flow") if isinstance(data.get("stock_flow"), dict) else {}
        out["main_net_inflow"] = stock_flow.get("main_net_inflow")
        out["inflow_5d"] = stock_flow.get("inflow_5d")
        out["inflow_10d"] = stock_flow.get("inflow_10d")
        bias = _capital_flow_bias_with_status(fundamental_context)[0]
        mapping = _NET_FLOW_STATUS_EN if language == "en" else _NET_FLOW_STATUS_ZH
        out["net_flow_status"] = mapping.get(bias, mapping["unavailable"])
    if dt_ok:
        dt_data = dt.get("data") if isinstance(dt.get("data"), dict) else {}
        out["dragon_tiger_on_list"] = bool(dt_data.get("is_on_list", False))
        out["dragon_tiger_recent_count"] = dt_data.get("recent_count")
        out["dragon_tiger_latest_date"] = dt_data.get("latest_date")
    return out


def fill_capital_flow_if_needed(
    result: "AnalysisResult", fundamental_context: Optional[Dict[str, Any]]
) -> None:
    """确定性把资金面（主力资金流 + 龙虎榜）填进 data_perspective.capital_flow（in-place）。

    presence-only + A股-gated；LLM 后、对决策只读；失败静默跳过、不阻断主流程。
    """
    if not result:
        return
    try:
        built = _build_capital_flow_from_context(
            fundamental_context, language=getattr(result, "report_language", "zh")
        )
        if built is None:
            return
        if not result.dashboard:
            result.dashboard = {}
        dp = result.dashboard.get("data_perspective") or {}
        result.dashboard["data_perspective"] = dp
        dp["capital_flow"] = built
        logger.info("[capital_flow] Filled capital-flow section from fundamental_context")
    except Exception as e:
        logger.warning("[capital_flow] Fill failed, skipping: %s", e)
```

- [ ] **Step 4: 接线 `pipeline.py`**

import 处（`pipeline.py:34` 的 `normalize_chip_structure_availability` 同 import 组）加 `fill_capital_flow_if_needed`。在 Step 7.6（`normalize_chip_structure_availability(result, chip_data)`，~621）之后插入：

```python
            # Step 7.6b: 资金面 section（主力资金流 + 龙虎榜呈现, presence-only, A股, 对决策只读）
            if result:
                fill_capital_flow_if_needed(result, fundamental_context)
```

（`fundamental_context` 在此作用域可得——同段 ~627 行 `stabilize_decision_with_structure(result, trend_result, fundamental_context)` 已使用。）

- [ ] **Step 5: 运行确认通过**

Run: `… -m pytest tests/test_capital_flow_surface.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/analyzer.py src/core/pipeline.py tests/test_capital_flow_surface.py
git commit -m "feat: 确定性填充资金面 section(fill_capital_flow_if_needed,net_flow_status 复用 bias)+pipeline 接线"
```

---

### Task 3: 龙虎榜 presence-only prompt 行

**Files:**
- Modify: `src/analyzer.py`（新增纯 helper `_dragon_tiger_prompt_line`；在 `_format_prompt`（~2923）的 `has_capital_flow` 块（~3165）之后调用）
- Test: `tests/test_capital_flow_surface.py`（追加）

**Interfaces:**
- Produces: `_dragon_tiger_prompt_line(fundamental_context) -> str`（未上榜/不可用 → 空串）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_capital_flow_surface.py`）

```python
from src.analyzer import _dragon_tiger_prompt_line


def test_dragon_tiger_prompt_line_on_list():
    line = _dragon_tiger_prompt_line(_ctx(on_list=True))
    assert "龙虎榜" in line and "2" in line and "2026-06-17" in line


def test_dragon_tiger_prompt_line_absent_when_not_on_list():
    assert _dragon_tiger_prompt_line(_ctx(on_list=False)) == ""


def test_dragon_tiger_prompt_line_absent_when_not_supported():
    assert _dragon_tiger_prompt_line(_ctx(dt_status="not_supported", on_list=False)) == ""
    assert _dragon_tiger_prompt_line(None) == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_capital_flow_surface.py -q -k dragon_tiger_prompt`
Expected: FAIL（import 缺失）

- [ ] **Step 3: 实现 helper + 接线**

`analyzer.py` 模块级新增（紧邻 `_build_capital_flow_from_context`）：

```python
def _dragon_tiger_prompt_line(fundamental_context: Optional[Dict[str, Any]]) -> str:
    """龙虎榜 presence-only prompt 行（标志级，非量级）；未上榜/不可用 → 空串。"""
    if not isinstance(fundamental_context, dict):
        return ""
    dt = fundamental_context.get("dragon_tiger")
    data = dt.get("data") if isinstance(dt, dict) and isinstance(dt.get("data"), dict) else {}
    if not data.get("is_on_list"):
        return ""
    return (
        "\n### 龙虎榜（游资信号，存在性）\n"
        f"近 {data.get('recent_count', 'N/A')} 日上榜龙虎榜，最新 {data.get('latest_date', 'N/A')}；"
        "游资活跃，注意分歧/波动。\n"
    )
```

在 `_format_prompt` 的 `if has_capital_flow:` 块结束之后（`analyzer.py:3165` 之后，`# 添加筹码分布数据` 之前）加一行：

```python
        prompt += _dragon_tiger_prompt_line(fundamental_context)
```

（`fundamental_context` 在 `_format_prompt` 内由 `context.get("fundamental_context")` 取得，~3070；与 `capital_flow_block` 同源。）

- [ ] **Step 4: 运行确认通过 + 主力资金流块回归**

Run: `… -m pytest tests/test_capital_flow_surface.py -q && … -m pytest tests/ -q -k "prompt or format_prompt or capital_flow" -m "not network"`
Expected: PASS（确认主力资金流向 prompt 块未受影响；若 `-k` 选择器无命中则忽略该子句、不杜撰测试）

- [ ] **Step 5: 提交**

```bash
git add src/analyzer.py tests/test_capital_flow_surface.py
git commit -m "feat: 龙虎榜 presence-only 进 LLM prompt(标志级,独立于 has_capital_flow)"
```

---

### Task 4: notification 资金面 markdown 块

**Files:**
- Modify: `src/notification.py`（DataPerspective 渲染段，筹码块（~1249）之后加资金面块）
- Test: `tests/test_notification.py`

**Interfaces:**
- Consumes: `data_perspective.capital_flow`（Task 1/2 产出的 dict）。

- [ ] **Step 1: 写失败测试**（加到 `tests/test_notification.py`，沿用其报告渲染测试模式）

```python
def test_notification_renders_capital_flow_block(...):
    # 构造含 dashboard.data_perspective.capital_flow 的报告（on_list=True），渲染 markdown
    # 断言输出含 "资金面"/"Capital Flow"、净流入数值、net_flow_status，且含龙虎榜提示行（含 recent_count/latest_date）

def test_notification_capital_flow_absent_not_rendered(...):
    # data_perspective 无 capital_flow → 输出不含资金面块

def test_notification_capital_flow_not_on_list_no_dragon_line(...):
    # capital_flow 存在但 dragon_tiger_on_list=False → 含资金面块、不含龙虎榜提示行
```

> 用本文件既有的报告-渲染测试 helper 构造 dashboard 并取 markdown 文本；断言 `assert "资金面" in text`（或 en 标签）、`assert "龙虎榜" in text`（on_list 用例）/ `assert "龙虎榜" not in text`（not-on-list 用例）。

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_notification.py -q -k capital_flow`
Expected: FAIL（无资金面渲染）

- [ ] **Step 3: 实现渲染**

`notification.py` 在筹码结构渲染块（~1243-1249 的 `else:` 之后、`# ========== 作战计划 ==========`（~1251）之前）加：

```python
                    # 资金面（主力资金流 + 龙虎榜，A股；presence-only）
                    cf_data = data_persp.get('capital_flow', {})
                    if cf_data:
                        cf_label = 'Capital Flow' if report_language == 'en' else '资金面'
                        mni_label = 'Main net inflow' if report_language == 'en' else '主力净流入'
                        _mni = cf_data.get('main_net_inflow')
                        _i5 = cf_data.get('inflow_5d')
                        _i10 = cf_data.get('inflow_10d')
                        report_lines.extend([
                            f"**{cf_label}**: {mni_label} "
                            f"{'N/A' if _mni is None else _mni} ({cf_data.get('net_flow_status') or 'N/A'}) | "
                            f"5d {'N/A' if _i5 is None else _i5} | 10d {'N/A' if _i10 is None else _i10}",
                            "",
                        ])
                        if cf_data.get('dragon_tiger_on_list'):
                            dt_prefix = (
                                'Dragon-Tiger list: on list' if report_language == 'en'
                                else '龙虎榜：上榜'
                            )
                            report_lines.extend([
                                f"🐯 {dt_prefix} "
                                f"{cf_data.get('dragon_tiger_recent_count', 'N/A')}"
                                f"{'' if report_language == 'en' else ' 次'}"
                                f", {cf_data.get('dragon_tiger_latest_date', 'N/A')}",
                                "",
                            ])
```

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_notification.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/notification.py tests/test_notification.py
git commit -m "feat: notification 资金面 markdown 块(主力资金流+net_flow_status+龙虎榜提示行)"
```

---

### Task 5: 决策回归锁定（I2）

**Files:**
- Test: `tests/test_capital_flow_surface.py`（追加回归断言）；运行既有 `tests/test_decision_stability.py`

**Interfaces:**
- Consumes: `stabilize_decision_with_structure`、`fill_capital_flow_if_needed`（已实现）。

目的：M4-B 不改 `stabilize_decision_with_structure`，但要**锁死**「加入资金面填充/龙虎榜呈现后，既有 flow_bias 决策行为字节不变、`decision_stability` 不新增键」。

- [ ] **Step 1: 写回归测试**（追加到 `tests/test_capital_flow_surface.py`）

```python
import copy
from src.analyzer import stabilize_decision_with_structure


def _result_with_dashboard(decision_type="buy"):
    return types.SimpleNamespace(
        dashboard={}, report_language="zh", decision_type=decision_type,
        confidence_level="高", operation_advice="买入",
    )


def test_capital_flow_fill_then_stabilize_no_decision_stability_keys_added():
    # fill 在前(LLM 后)，stabilize 在后；fill 只写 data_perspective.capital_flow，不碰 decision_stability
    result = _result_with_dashboard()
    fill_capital_flow_if_needed(result, _ctx())
    ds_before = copy.deepcopy(result.dashboard.get("decision_stability"))
    assert ds_before is None  # fill 不建 decision_stability
    cf = result.dashboard["data_perspective"]["capital_flow"]
    assert set(cf) == {
        "main_net_inflow", "inflow_5d", "inflow_10d", "net_flow_status",
        "dragon_tiger_on_list", "dragon_tiger_recent_count", "dragon_tiger_latest_date",
    }  # 无外溢键
```

> 决策路径（`_downgrade_buy_without_capital_flow`/`_set_decision_stability_unavailable`/`_downgrade_to_structural_hold` 三分支）的行为由既有 `tests/test_decision_stability.py` 覆盖；本任务额外锁定「资金面填充不向 `decision_stability` 注入键、不改 `data_perspective` 既有子块」。若 `test_decision_stability.py` 未覆盖某结构性降级分支，在其中补一条对应断言（near_resistance 非 inflow / outflow 非 breakout / mid_range neutral → 预期 decision_type/decision_stability）。

- [ ] **Step 2: 运行新测试 + 既有决策测试回归**

Run: `… -m pytest tests/test_capital_flow_surface.py tests/test_decision_stability.py -q`
Expected: PASS（既有决策行为不变）

- [ ] **Step 3: 提交**

```bash
git add tests/test_capital_flow_surface.py tests/test_decision_stability.py
git commit -m "test: 锁定资金面填充不改 decision_stability 与既有 flow_bias 决策行为(I2 回归)"
```

---

### Task 6: 文档（CHANGELOG + 资金面专题）

**Files:**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加）
- Create: `docs/capital-flow.md`
- Test: 无（docs only）

- [ ] **Step 1: CHANGELOG 扁平追加**（每条独立一行 `- [类型] 描述`，**禁止新增 `###` 标题**）

```markdown
- [新功能] 报告新增「资金面」section：A股主力资金流（主力净流入/5日/10日 + 净流入状态）与龙虎榜（上榜/次数/最新）确定性呈现，notification 渲染
- [改进] 激活龙虎榜：上榜时以 presence-only 进 LLM 分析 prompt 供引用，并在资金面 section 呈现（不写内部 decision_stability、不改既有主力资金流降级逻辑）
```

- [ ] **Step 2: 写专题文档** `docs/capital-flow.md`

覆盖：资金面来源（akshare `fundamental_adapter`，A股，tokenless，fail-open）；`CapitalFlow` section 字段与 `net_flow_status` 口径（复用 `_capital_flow_bias_with_status`）；龙虎榜激活（presence-only prompt + section 呈现，不做硬决策）；**明确不写 `decision_stability`**（内部专用）、主力资金流既有 prompt 量级表 + post-LLM 降级零改动；presence-only + A股-gated 边界；v1 已知局限（仅 A股；龙虎榜仅存在性不解析席位；北向/融资融券留 M4-B-2；无专用 Web 组件）。

- [ ] **Step 3: 核对命令/文件名/字段名与实仓一致**（docs-only，无需跑测试）

- [ ] **Step 4: 提交**

```bash
git add docs/CHANGELOG.md docs/capital-flow.md
git commit -m "docs: 资金面 CHANGELOG 与专题文档(presence-only/A股 gate/v1 局限)"
```

---

## 全量门禁（全部任务完成后，交付前亲自跑）

- 后端：`./scripts/ci_gate.sh`（flake8 critical + `pytest -m "not network"`），从 `/root/dsa-m4b` 用 venv 跑。
- 纯后端、无前端代码改动 → 不触发 web-gate；报告 payload 新增可选字段属 API/Schema 联动，交付说明写明前端忽略未知字段、无破坏。
- 交付说明：改了什么 / 为什么 / 验证 / 未验证 / 风险 / 回滚。

## Self-Review（计划自审结论）

- **Spec 覆盖**：CapitalFlow schema(T1)、确定性填充+pipeline 接线(T2)、龙虎榜 prompt 行(T3)、notification 呈现(T4)、决策回归锁定 I2(T5)、文档+局限(T6) —— 逐条对应；不写 decision_stability、主力资金流零改动在 Global Constraints + T2/T3/T5 体现。
- **类型一致**：`CapitalFlow` 字段全程一致（schema/fill/notification/test）；`net_flow_status` zh/en 映射键与 bias 取值（inflow/outflow/neutral/unavailable）一致；`fill_capital_flow_if_needed`/`_build_capital_flow_from_context`/`_dragon_tiger_prompt_line` 签名跨任务一致。
- **占位符**：核心代码均给出；仅 T4 notification 测试与 T5 既有决策分支断言依赖既有测试 helper/覆盖（已注明用现有渲染 helper 构造、并在缺口处补断言，非 TBD）。
- **稳定性**：fill 在 LLM 后、对决策只读；presence-only + A股-gate + fail-open；不新增配置/源/token；additive schema 向后兼容。
