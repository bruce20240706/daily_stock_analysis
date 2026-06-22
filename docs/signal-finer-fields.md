# 信号细粒度字段专题文档

> 适用版本：M3.1（feat/m3-1-signal-finer-fields）

## 概述

「信号细粒度字段」是 M3.1 在现有信号引擎（M3 可信度层）之上新增的三个派生字段：`horizon_bars`、`plan_quality`、`status`。这三个字段为实时 `/signals` 端点、K 线抽屉钻取面板与信号看板提供更精细的信号生命周期与交易计划质量信息。

**关键约束**：三个字段均为 **transient-only**，即实时计算、仅随 `/signals` 响应下发，**不写入数据库、不进历史**。对决策字段（`decision_type`、`decision_stability`、`action`）和既有 `hit_rate`/`verified`/`plan_quality`（旧）字段完全只读，不改写任何持久化字段。

---

## 三字段定义

### 1. `horizon_bars`（胜率验证窗口，整型 bar 数）

**挂载位置**：`SignalMarker.horizon_bars`（API: `markers[].horizonBars`）；看板 `BoardEntry.horizon_bars`（API: `horizonBars`）。

**语义**：该信号类型在历史回测时使用的评估窗口（bar 数）。与 `hit_rate`/`hit_sample`/`verified` 字段**同源**——三者均来自同一次回测统计（`signal_stats` 表），`horizon_bars` 就是那次回测的 `eval_window_days`。前端通过 `formatHorizon(horizonBars)` 格式化为可读标签（如「20 bar 窗口」），本地化在前端完成，后端只出整型。

**无 stats 时**：若该 `(rule_tag, market)` 无回测统计，`horizon_bars` 为 `null`。

### 2. `plan_quality`（交易计划完整度 + 一致性评分）

**挂载位置**：`SignalsResponse.plan_quality`（顶层）；看板 `BoardEntry.plan_quality`（API: `planQuality`）。

**语义**：综合衡量当前信号集合的「交易计划可用性」——是否具备入场价、止损价、目标价（来自 `price_lines`），以及方向信号是否内部一致（无对立买/卖信号）。取值范围：`"高"` / `"中"` / `"低"`（前端按界面语言本地化）。

**与可信度正交**：`plan_quality` 评估的是「价位是否完整 + 信号方向是否一致」，而可信度（`hit_rate`/`verified`）评估的是「历史胜率是否足够高」。两者可以独立高/低：可信度高但价位缺失 → `plan_quality` 低；价位完整但无足够样本 → 可信度未验证但 `plan_quality` 可为高。

**计算逻辑**（`src/services/signal_finer_fields.py: compute_plan_quality`）：
- `price_lines` 三位（entry/stop/target）均有值 → +1 分
- 无对立方向信号（BUY 与 SELL/SHORT 共存） → +1 分
- 2 分 → 「高」；1 分 → 「中」；0 分 → 「低」

### 3. `status`（信号生命周期：active / aging / expired）

**挂载位置**：`SignalMarker.status`（API: `markers[].status`）；看板 `BoardEntry.signal_status`（API: `signalStatus`，避让 `BoardEntry.status` 已占用字段）。

**语义**：基于信号触发时间与当前时间的相对距离（bar 数），反映信号的「新鲜度」：

设 `bars_since = 最新 bar 索引 − 触发 bar 索引`，窗口 `W = horizon_bars`（命中 `signal_stats` 时）否则配置默认窗口 `SIGNAL_BACKTEST_HORIZON_BARS`（默认 10）：

| 值 | 含义 | 判定 |
|---|---|---|
| `active` | 信号在最新 bar 触发 | `bars_since == 0`（实现按 `<= 0`） |
| `aging` | 触发后仍在验证窗口内 | `0 < bars_since < W` |
| `expired` | 已超出验证窗口 | `bars_since >= W` |

**时间相对性**：`status` 基于「触发 bar 到当前最新 bar 的距离」，仅在实时计算时有意义，**不适用于历史 bar 回放**（历史视角每根 bar 的 status 各不相同）。transient-only 保证了它不会被误当历史字段使用。

**无 horizon 时**：若 `horizon_bars` 为 `null`（未命中 `signal_stats`），`status` 仍按默认窗口 `W` 正常计算；**仅当** marker 时间戳匹配不到任何 bar（或非 rule marker）时 `status` 才为 `null`。

---

## 计算层与编排位置

三个字段全部在编排层（`src/core/pipeline.py`）的 `_augment_payload_finer_fields` 中计算，位于 `price_lines` 填充完成（Step 7.x）之后，保证 `plan_quality` 可读到完整 `price_lines`。

```
pipeline step 7.x  →  price_lines 填充完成
pipeline step 7.y  →  _augment_payload_finer_fields(payload, signal_stats)
                       ├── compute_marker_statuses(markers, stats)   → horizon_bars + status
                       └── compute_plan_quality(markers, price_lines) → plan_quality
```

纯 helper 实现在 `src/services/signal_finer_fields.py`（无 I/O，可单独测试）。

---

## Transient-Only 约束

- **不写 DB**：三个字段不在任何 repository write 路径中出现。
- **不进历史**：`/signals` 历史 bar 视图不下发这三个字段（`status` 历史无意义；`plan_quality`/`horizon_bars` 的 transient 值仅对最新 bar 集合有意义）。
- **不改既有字段**：`hit_rate`、`verified`、`hit_sample`、`decision_type`、`action` 等字段均不被写入或覆盖。
- **降级安全**：任何字段计算失败均返回 `null`，不阻断主流程。

---

## 看板同源说明

信号看板（`GET /api/v1/signals/board`）的 `horizon_bars`、`signal_status`、`plan_quality` 字段来自**与 `/signals` 端点相同的底层计算路径**，通过 `_hit_fields_from_markers` 扩展从 markers 中取 representative marker（首条 active 规则 marker）的值聚合到 `BoardEntry`。

**偏差说明**：看板代表 marker = 首条匹配规则的 marker，在多信号共存时往往是较老的 aging/expired 信号（因 markers 按时间顺序排列，最早触发的排在前）。这是有意设计：看板行代表「最早/最显著触发的信号」，而非最新信号；用户如需查看全部 marker 状态，应进入钻取面板。

---

## Resolver 与 Schema 层

| 字段 | 后端 schema | 前端 type | Resolver |
|---|---|---|---|
| `horizon_bars` | `SignalMarker.horizon_bars: int \| None` | `horizonBars?: number` | `resolve_signals` 透传 `signal_stats` 中的 `eval_window_days` |
| `plan_quality` | `SignalsResponse.plan_quality: str \| None` | `planQuality?: "高"\|"中"\|"低"` | 编排层填充后随 payload 序列化 |
| `status` | `SignalMarker.status: str \| None` | `status?: "active"\|"aging"\|"expired"` | 编排层填充 |
| `signal_status`（看板） | `BoardEntry.signal_status: str \| None` | `signalStatus?: string` | `_hit_fields_from_markers` 扩展 |

---

## Web 呈现

- **K 线抽屉钻取面板**：`SignalDrilldown` 组件展示 `horizonBars`（`formatHorizon` 格式化）、`status`（badge 着色）、`planQuality`（`formatPlanQuality` 本地化）。
- **信号看板**：`BoardEntry` 新增 `horizon_bars`/`signal_status`/`plan_quality` 列，`credibility.ts` 提供共享格式化函数。
- **可信度与 plan_quality 共存**：可信度列（`hit_rate`/`verified`）与 `plan_quality` 列独立呈现，不合并，体现两个正交维度。

---

## v1 已知局限

- **status 仅实时**：`status` 基于「最新 bar 到触发 bar」的相对距离，对历史报告无意义，历史视图不展示。
- **plan_quality 依赖 price_lines**：若 `price_lines` 因数据不足全为 `null`，`plan_quality` 恒为「低」，与信号质量本身无关。
- **看板代表 marker 偏 aging/expired**：首条规则 marker 往往是最早触发的信号，在多信号共存时可能已 aging 或 expired；看板行的 `signal_status` 不代表所有信号的综合状态。
- **transient-only 不进历史**：三个字段无法在历史报告中查看，不参与回测或趋势分析。
- **horizon_bars 无 stats 时为 null**：新规则或冷启动阶段尚无回测统计时，`horizon_bars` 和 `status` 均回退为 `null`。
