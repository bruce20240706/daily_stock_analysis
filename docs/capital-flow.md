# 资金面专题文档

> 适用版本：M4-B（v1）

## 概述

「资金面」是报告的新增 section，以确定性方式（数字直接来自数据源、不经 LLM）呈现 A 股主力资金流与龙虎榜上榜信息。该 section 对 LLM 决策只读，仅用于报告呈现与 notification 渲染；分析管线的决策链（`decision_type`、`decision_stability`、主力资金流降级规则）全部保持零改动。

---

## 数据来源与可用性

- **数据适配器**：`data_provider/base.py`，`get_capital_flow_context` / `get_dragon_tiger_context`，底层由 `fundamental_adapter`（akshare）提供。
- **市场限制**：**仅支持 A 股**（`_market_tag == "cn"` 且非 ETF）。港股、美股、crypto、ETF 均返回 `not_supported`，对应 section 在报告和 notification 中缺席。
- **免 token**：不依赖任何付费 API key。
- **fail-open**：任何阶段失败均返回结构化状态（`ok` / `partial` / `not_supported` / `failed`）+ `errors` 链，不抛异常，不中断分析主流程。

---

## CapitalFlow section 字段与 net_flow_status 口径

`CapitalFlow` 定义于 `src/schemas/report_schema.py`，挂载在 `DataPerspective.capital_flow`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `main_net_inflow` | `int/float/str/None` | 当日主力净流入金额（元） |
| `inflow_5d` | `int/float/str/None` | 5 日累计净流入 |
| `inflow_10d` | `int/float/str/None` | 10 日累计净流入 |
| `net_flow_status` | `str/None` | 净流入状态（见下方口径说明） |
| `dragon_tiger_on_list` | `bool/None` | 最近是否上龙虎榜 |
| `dragon_tiger_recent_count` | `int/None` | 近期上榜次数 |
| `dragon_tiger_latest_date` | `str/None` | 最近一次上榜日期 |

### net_flow_status 口径

`net_flow_status` **完全复用** `_capital_flow_bias_with_status(fundamental_context)[0]` 的 `bias` 值，经人读映射后写入：

| bias 值 | 中文显示 | 英文显示 |
|---|---|---|
| `inflow` | 净流入 | Inflow |
| `outflow` | 净流出 | Outflow |
| `neutral` | 中性 | Neutral |
| `unavailable` | 未知 | Unknown |

关键设计约束：显示值与现有决策 bias 同源，矛盾信号会在 `_capital_flow_bias_with_status` 内收敛为 `neutral`，`net_flow_status` 跟随显示「中性」，不引入任何新阈值或独立判定逻辑。

确定性填充由 `fill_capital_flow_if_needed`（`src/analyzer.py`）完成，在 pipeline `Step 7.6b`（`src/core/pipeline.py`）LLM 分析结束后调用，数字直接来自 `fundamental_context`，**不经 LLM 生成**。

---

## 龙虎榜激活（prompt + 呈现）

龙虎榜以 **presence-only** 方式激活，仅使用存在性信息（上榜次数 + 日期），**不解析席位明细，不做硬决策规则**。

### LLM prompt 注入

当股票上榜（`dragon_tiger_on_list == True`）时，`_dragon_tiger_prompt_line`（`src/analyzer.py`）向分析 prompt 追加一行标志级提示（次数 + 日期），供 LLM 在分析中引用参考。未上榜时不追加任何内容。

### 报告与 notification 呈现

- 上榜时，`CapitalFlow` section 中的龙虎榜字段（`dragon_tiger_on_list/recent_count/latest_date`）会填充并在 notification 资金面 markdown 块中渲染谨慎提示。
- 未上榜或数据不可用时，对应字段为 `None`，notification 不渲染龙虎榜行。

---

## 不动的既有行为

以下内容在 M4-B 中**零改动**，与资金面 section 无交叉：

- **主力资金流 prompt 量级表**：`src/analyzer.py` 中 LLM prompt 内的主力资金流量级描述与行文逻辑。
- **post-LLM 降级**：资金面数据不可用时将 `BUY` 降为 `HOLD` 的 `stabilize_decision_with_structure` 逻辑。
- **`decision_stability`**：内部决策稳定性字段，`fill_capital_flow_if_needed` 不写入，文档不公开此字段。
- **换手率 / 量比 / 筹码 section**：既有 `chip_structure` 等 section 全部保持原样。

---

## presence-only 与 A 股 gate

| 场景 | capital_flow status | dragon_tiger status | CapitalFlow section | notification 资金面块 |
|---|---|---|---|---|
| A 股，数据正常 | `ok` | `ok` / `partial` | 填充 | 渲染 |
| A 股，数据抓取失败 | `failed` / `partial` | `failed` | 不填充 | 不渲染 |
| 港股 / 美股 / crypto | `not_supported` | `not_supported` | 缺席 | 缺席 |
| ETF | `not_supported` | `not_supported` | 缺席 | 缺席 |

非 A 股或 ETF 时，`get_capital_flow_context` / `get_dragon_tiger_context` 直接返回 `not_supported`，`fill_capital_flow_if_needed` 不写入 `capital_flow` 字段，notification 资金面块不渲染，`_dragon_tiger_prompt_line` 不追加 prompt 行。

---

## v1 已知局限

- **仅 A 股**：港股、美股、crypto、ETF 均不支持，`capital_flow` section 缺席。
- **龙虎榜仅存在性**：仅记录上榜次数与日期，不解析席位明细（买方/卖方席位名称、净额），不做基于席位的硬决策规则。
- **北向资金 / 融资融券未纳入**：留待 M4-B-2 迭代补充。
- **无专用 Web 组件**：与 `chip_structure` / `volume` 等 section 一致，资金面信息经 notification markdown 和报告 payload（`data_perspective.capital_flow`）呈现，前端不感知新字段时安全忽略。
