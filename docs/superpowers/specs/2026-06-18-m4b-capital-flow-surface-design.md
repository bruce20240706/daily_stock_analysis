# M4-B 资金面呈现 + 龙虎榜激活 设计

- 日期：2026-06-18
- 状态：设计已评审通过，待写实现计划（writing-plans）
- 基线分支：`feat/m4b-capital-flow` ← `main@1c794c62`（**独立分支**，不依赖 M3/M4-A，可独立合入 main）
- 关联：M4 数据面拆分的第二片（M4-A 周/月线已停放在 feat/m4a-multi-period）；本片为 M4-B-1。

---

## 1. 背景与目标

资金面（capital-flow）数据在仓库中**已部分建成但未对用户呈现**：

- **主力资金流**（`fundamental_adapter.get_capital_flow`，akshare 个股+板块，A股）：已抓取、**已注入 LLM prompt**（`analyzer.py` 的「### 主力资金流向（操作建议过滤器）」表，含主力净流入/5日/10日/板块排名）、且驱动 post-LLM 降级（`_downgrade_buy_without_capital_flow`：资金面不可用时 BUY→HOLD）。但**不在报告 schema 里**——用户只看到 LLM 综合后的文字，看不到驱动决策的资金数字。
- **龙虎榜**（`get_dragon_tiger_context`，A股）：已抓取，但**完全休眠**——分析逻辑从不消费、不进 prompt、不进报告。
- 换手率/量比、筹码分布：已接且已在报告（VolumeAnalysis / ChipStructure block），**本片不动**。

M4-B-1 目标（最小、additive、A股-gated、presence-only）：

1. 把**主力资金流 + 龙虎榜**确定性地补进报告 schema（新增 `CapitalFlow` section），让用户在报告里看得到驱动决策的资金面数字。
2. **激活龙虎榜**：上榜时把标志/次数/日期确定性填进 `CapitalFlow` section（用户可见，notification 渲染谨慎提示行），并以 presence-only（标志级）进 LLM prompt 供其在叙述中引用。
3. **不改动**主力资金流既有的 prompt 量级注入与 post-LLM 降级逻辑；**不写 `decision_stability`**（经核查该结构仅 `analyzer.py` 内部使用、零用户渲染，往里加注记用户看不到）。

## 2. 非目标

- **北向/HSGT、融资融券**：留作 M4-B-2（需新数据源，可能需 Tushare Pro 积分；A股专属）。本片不做。
- **主力资金流量级 prompt 注入**：已存在，不重复、不改。
- **龙虎榜硬决策规则**（触发降级/覆盖）：明确不做，仅 presence-only prompt + CapitalFlow section/notification 呈现。
- **写入 `decision_stability`**：明确不做（内部专用、零用户渲染；用户向提示改走 CapitalFlow section + notification）。
- **新数据源 / 新 token / 新配置开关**：不引入（复用既有 akshare `fundamental_adapter`，tokenless）。
- **资金面报告的专用 Web React 组件**：不新增；与 chip/volume 一致，经 notification markdown + 报告 payload（web 当前以 raw JSON 呈现 DataPerspective 子块）呈现。DataPerspective 各子块的精细 Web 渲染是既有缺口，超出本片范围。
- **板块资金排名进报告 section**：保持只在 prompt（市场上下文），不进 per-stock 报告 section（YAGNI）。

## 3. 关键决策（已锁定）

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | 范围 | 激活+呈现存量（主力资金流+龙虎榜）；北向/融资融券留 M4-B-2 |
| D2 | 代码基线 | 基于 main 独立起（不依赖、不堆叠 M3/M4-A） |
| D3 | 激活深度 | 龙虎榜 presence-only 进 prompt + CapitalFlow section/notification 呈现（**用户可见面**；不写内部专用的 decision_stability）；主力资金流既有逻辑零改动 |
| D4 | 报告填充 | **确定性后处理填充**（镜像 `fill_chip_structure_if_needed`），非 LLM 生成（资金数字不可被幻觉） |
| D5 | 配置 | 零新增配置/数据源/token |

## 4. 架构与模块边界

全部 additive、A股-gated、presence-only。

### 4.1 报告 schema（`src/schemas/report_schema.py`）

新增模型，挂到 `DataPerspective`（与 `VolumeAnalysis`/`ChipStructure` 同级）：

```python
class CapitalFlow(BaseModel):
    """资金面（A股；主力资金流 + 龙虎榜存在性）。"""
    main_net_inflow: Optional[Union[int, float, str]] = None
    inflow_5d: Optional[Union[int, float, str]] = None
    inflow_10d: Optional[Union[int, float, str]] = None
    net_flow_status: Optional[str] = None           # 净流入/净流出/中性/未知
    dragon_tiger_on_list: Optional[bool] = None
    dragon_tiger_recent_count: Optional[int] = None
    dragon_tiger_latest_date: Optional[str] = None
```

`DataPerspective` 追加：`capital_flow: Optional[CapitalFlow] = None`（可选、默认 None，旧 payload 无该键仍解析）。

### 4.2 确定性后处理填充（`src/analyzer.py`，镜像 `fill_chip_structure_if_needed`）

新增 `fill_capital_flow_if_needed(result, fundamental_context)`：

- presence-only：`capital_flow.status` ∈ {ok, partial} 才填主力流字段；`dragon_tiger.status` ∈ {ok, partial} 才填龙虎榜字段；否则对应字段保持 None；两者皆无 → `data_perspective.capital_flow` 保持 None（section 不出现）。
- 主力流：从 `fundamental_context['capital_flow']['data']['stock_flow']` 取 `main_net_inflow/inflow_5d/inflow_10d`。
- `net_flow_status`：**复用** `_capital_flow_bias_with_status(fundamental_context)` 的 bias，人读映射 `inflow→净流入 / outflow→净流出 / neutral→中性 / unavailable→未知`（不引入新阈值，与降级所用同一判定，显示与决策一致）。
- 龙虎榜：从 `fundamental_context['dragon_tiger']['data']` 取 `is_on_list/recent_count/latest_date`。
- 在 pipeline LLM 分析后调用（紧挨 chip 填充处），与 `normalize_chip_structure_availability` 同段。

### 4.3 龙虎榜 → LLM prompt（presence-only）

在既有「主力资金流向」prompt 块附近，当 `dragon_tiger.is_on_list` 为真时追加一行存在性（**标志级、非量级**）：

> 龙虎榜：近 {recent_count} 日上榜，最新 {latest_date}（游资活跃，注意分歧/波动）。

独立于 `has_capital_flow`（龙虎榜上榜即渲染该行）。主力资金流量级表**不动**。

### 4.4 龙虎榜 → 用户可见面（CapitalFlow section + notification）

**不写 `decision_stability`**（review 核查：`grep -rn decision_stability` 全仓排除 analyzer.py/tests = 0 命中，仅 `analyzer.py` 内部使用、无任何用户向渲染——往里加注记用户看不到，且正常买入路径根本不建该块）。龙虎榜的用户向谨慎提示改走**已渲染**的面：

- **数据层**：`fill_capital_flow_if_needed`（§4.2）已把 `dragon_tiger_on_list/recent_count/latest_date` 确定性填进 `CapitalFlow` section。
- **呈现层**：notification 渲染器（§4.5）在 `dragon_tiger_on_list` 为真时，从这三字段确定性合成一行谨慎提示（如「龙虎榜：近 {recent_count} 日上榜，最新 {latest_date} — 游资活跃，注意分歧/波动」，双语按 `report_language`），用户可见。
- **LLM 叙述**：§4.3 的 prompt 行让 LLM 知晓龙虎榜，可自行写入其 `risk_alerts`/`sentiment` 叙述（`risk_alerts` 保持 LLM 所有，不确定性强插，避免与 LLM 自生内容打架）。
- 全程**不触碰** `stabilize_decision_with_structure` / `_downgrade_buy_without_capital_flow` / `_capital_flow_bias_with_status` 的决策行为；龙虎榜填充是 LLM 后、对决策只读。

### 4.5 呈现（`src/notification.py`）

markdown 渲染器（DataPerspective 段，现渲染 trend/price/volume/chip）追加「资金面」块：present 时渲染主力净流入/5日/10日 + net_flow_status；`dragon_tiger_on_list` 为真时additionally 合成一行龙虎榜谨慎提示（§4.4，由 on_list/count/date 确定性组成）；`capital_flow` 缺失/None → 不渲染该块。报告 schema 即 API 载荷，web/推送自动携带 section。

### 4.6 关键复用 / 不动

复用 `fundamental_adapter`（capital_flow/dragon_tiger 抓取，fail-open）、`_capital_flow_bias_with_status`（net_flow_status 同源）、chip 填充模式。**不动**：主力资金流 prompt 量级表、post-LLM 降级、capital_flow_bias、换手率/量比/筹码 既有 section。

## 5. 数据流

pipeline 取 `fundamental_context`（capital_flow + dragon_tiger，A股 gate）→ LLM prompt（既有主力资金流量级表 + 新增龙虎榜存在行）→ LLM 分析 → **后处理**：`fill_capital_flow_if_needed` 确定性填 `DataPerspective.capital_flow`（含 dragon_tiger 三字段）→ 报告序列化 → notification markdown 渲染资金面块（present 时含龙虎榜提示行）/ API 载荷携带 section。`decision_stability` 不参与本特性。

## 6. 字段契约

- `CapitalFlow`：见 §4.1。`net_flow_status` 取值 `净流入/净流出/中性/未知`（英文报告：`Inflow/Outflow/Neutral/Unknown`，按 report_language）。
- 龙虎榜谨慎提示：**不是独立 schema 字段**，而是 notification 渲染层由 `dragon_tiger_on_list/recent_count/latest_date` 确定性合成的一行（§4.4/§4.5）。
- `decision_stability`：**不新增任何键**（保持内部专用、行为不变）。
- 非 A股/港股/美股/crypto/ETF：capital_flow/dragon_tiger = `not_supported` → `DataPerspective.capital_flow` = None、无龙虎榜 prompt 行、notification 不渲染资金面块（与现状一致）。

## 7. 错误处理与边界

- presence-only + fail-open：`fundamental_adapter` 已 fail-open（status + errors 链，不抛异常）。capital_flow/dragon_tiger 任一 `failed` → 对应字段 None、不阻断分析主流程（稳定性护栏：单一辅助数据失败不拖垮分析）。
- 部分数据：stock_flow 某字段 None → 该字段 None；`net_flow_status` 据 bias 判定（混合/缺失 → 中性或未知）。
- 龙虎榜 not-on-list / status 非 ok → 无 prompt 行、notification 不渲染龙虎榜提示行、section 龙虎榜字段 None。
- 不新增 fail-fast、不静默掩盖契约（status 如实反映为 section 是否出现 + 字段是否 None）。

## 8. 兼容性

- 报告 schema：`DataPerspective.capital_flow` 追加可选（默认 None）→ 旧报告消费方（notification/web/历史回放）忽略缺失字段，不破坏 payload 契约。
- **主力资金流 prompt 注入 + `stabilize_decision_with_structure` 全部 flow_bias 决策路径（`_downgrade_buy_without_capital_flow`、`_set_decision_stability_unavailable`、`_downgrade_to_structural_hold` 的 near_resistance/outflow/mid_range 三分支）+ `decision_stability` 结构零改动** → 现有决策行为不变（回归锁定，见 §9）。
- 前端无代码改动；报告 payload 新增可选字段，前端忽略未知字段，无破坏。
- 零新配置/数据源/token。

## 9. 测试矩阵（pytest，离线确定性，mock fundamental_context）

- `fill_capital_flow_if_needed`：A股 ok → 各字段 + `net_flow_status`（正→净流入/负→净流出/混合或缺→中性或未知）；not_supported → section None；partial（部分字段 None）→ 优雅；dragon_tiger on-list → 三字段填、not-on-list → 龙虎榜字段 None。
- 龙虎榜 prompt 行：on-list → prompt 含存在行；not-on-list/not_supported → 不含；**主力资金流既有块行为回归不变**。
- **决策回归锁定（I2）**：`stabilize_decision_with_structure` 的全部 flow_bias 决策路径行为字节不变——断言 `_downgrade_buy_without_capital_flow`（buy×unavailable→hold）、`_set_decision_stability_unavailable`（非 buy×unavailable）、`_downgrade_to_structural_hold` 三分支（near_resistance 且非 inflow、outflow 非 breakout、mid_range 且 neutral）在加入龙虎榜呈现后 `decision_type`/`decision_stability` 不变；`decision_stability` 不新增键。
- notification markdown：capital_flow present → 资金面块渲染（主力流 + net_flow_status）；`dragon_tiger_on_list` → 含龙虎榜提示行；not-on-list → 无该行；capital_flow 缺失 → 不渲染整块。
- schema：`CapitalFlow` 校验；`DataPerspective` 向后兼容（旧 payload 无该键仍解析）。

## 10. 验证门禁

- 后端：`./scripts/ci_gate.sh`（flake8 critical + `pytest -m "not network"`）。
- 纯后端改动（analyzer/pipeline/schema/notification）→ 不触发 web-gate；报告 payload 新增可选字段属 API/Schema 联动，交付说明写明前端忽略未知字段、无破坏。
- 文档：CHANGELOG `[Unreleased]` 扁平追加；更新/新增资金面专题文档。

## 11. 风险与回滚

- 风险：龙虎榜呈现是 LLM 后、对决策只读，不改 `stabilize_decision_with_structure`，故不扰动 flow_bias 决策（§9 回归锁定全部 bias 路径作为护栏）。`net_flow_status` 复用 `_capital_flow_bias_with_status`（会二次计算一次 bias——pure/cheap，可接受；或在 plan 把已算 bias 透传以 DRY），若 bias 语义变化会联动（同源，实为显示与决策一致的优点）。
- 回滚：改动集中在新增 `CapitalFlow` 模型 + `fill_capital_flow_if_needed` + 龙虎榜 prompt 行 + notification 资金面块（含龙虎榜提示行）；回滚=撤这些 additive 改动；主力资金流/降级/`decision_stability`/换手率/筹码 主链未动，回滚面小。独立分支可整支弃用。

## 12. 已知局限（v1）

- 仅 A股（capital_flow/dragon_tiger 数据源 A股专属）；港股/美股/crypto 无资金面 section。
- 龙虎榜仅 presence-only（上榜标志/次数/日期），不解析席位明细、不做硬决策。
- 北向/融资融券未纳入（M4-B-2）。
- 资金面报告无专用 Web 组件（同 chip/volume，经 notification markdown + 报告 payload 呈现）。

---

## 实现切片（供 writing-plans 参考）

1. `report_schema.py`：`CapitalFlow` 模型 + `DataPerspective.capital_flow` + schema 测试。
2. `analyzer.py` + `src/core/pipeline.py`：`fill_capital_flow_if_needed`（确定性填充，net_flow_status 复用 bias）+ **钉死 post-LLM 调用点**（紧挨 `normalize_chip_structure_availability`，确认 `fundamental_context` 在该点可得、且在 LLM 填完 dashboard 之后）+ 单测（含 not_supported→None、partial、龙虎榜字段）。
3. `analyzer.py`：龙虎榜 presence-only prompt 行（独立于 has_capital_flow）+ 单测（主力流块回归不变）。
4. `notification.py`：资金面 markdown 块（主力流 + net_flow_status + 龙虎榜提示行）+ 单测（present/缺失/on-list/not-on-list）。
5. **决策回归锁定（I2）**：覆盖 `stabilize_decision_with_structure` 全部 flow_bias 路径（含 `_downgrade_to_structural_hold` 三分支）的回归用例，断言加入龙虎榜呈现后决策与 `decision_stability` 不变。
6. 文档（CHANGELOG + 资金面专题）+ 全量后端门禁。
