# 设计：回测做空感知 Web 透出（仓位/模拟收益/出场原因，纯渲染层）

- 日期：2026-06-11
- 状态：设计已批准（方案 A：加一列「仓位/模拟」），待写实施计划
- 子项目：perp 系列收尾——子项目 E（perp 回测）的"最后一公里"用户可见闭环
- 关联前序：E（资金费+做空 1x 回测）已落地于 `feat/crypto-perp-backtest`；多空比指标已落地于 `feat/crypto-perp-long-short-ratio`

## 0. 背景与动机

子项目 E 让 perp 回测产出做空记录：`position_recommendation="short"`、`simulated_exit_reason="window_end_short"`、`simulated_return_pct`（资金费已折入：多头 `−funding`、空头 `+funding`）。这些字段**已经**随既有回测 API（`api/v1/schemas/backtest.py:BacktestResultItem`，自由字符串/数值字段）流到前端，TS 类型 `apps/dsa-web/src/types/backtest.ts` 也已齐备（`positionRecommendation`/`simulatedReturnPct`/`simulatedExitReason`）。

但 Web 回测页（`apps/dsa-web/src/pages/BacktestPage.tsx`）的结果表**不渲染**这三个字段——E 的成果对用户完全不可见。并存在一个真实的展示反差：做空赢单的「窗口收益」列（标的价格变动 `actualReturnPct`）显示红色负数，而「结果」列显示绿色"盈利"，缺少能解释该反差的模拟收益展示。

（澄清：`DIRECTION_EXPECTED_LABELS` 里的 `long`/`cash` 是方向列的遗留兼容映射，并非仓位渲染。）

## 1. 范围与零改动面

**只改**：`apps/dsa-web/src/pages/BacktestPage.tsx` + `apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx` + 文档（§5）。

**零改动**：后端（引擎/service/API）、API schema、TS 类型（字段已存在）、DB schema、桌面端（复用同一 web 构建自动获得，已于多空比子项目核实 `--serve-only` 加载同一构建）。

**明确不做（YAGNI）**：
- 不加 `short_count` 汇总：需动 `BacktestPerformance` DB 列（E 期已定零 schema）；且 `PerformanceCard` 今天本就不渲染 `longCount`/`cashCount`，无 UI 不一致。
- 不动 `PerformanceCard`：「平均模拟收益」（`avgSimulatedReturnPct`）已按 E 的 position-agnostic 语义包含短仓。
- 不加筛选器（按仓位过滤）、不加 entry/exit 价格列。

## 2. 新列「仓位/模拟」

位置：插在「窗口收益」与「方向匹配」之间（表 8→9 列；`min-w-[900px]` 加宽约 100px（双行单元格实际占宽以实施期渲染为准微调，1000–1020px 量级），已有"小屏幕可横向滚动"提示）。

单元格结构（上下两行，与「AI 预测」列的双行样式同构）：

- **第一行**：仓位 badge + 模拟收益
  - `POSITION_LABELS: Record<string, string> = { long: '做多', short: '做空', cash: '空仓' }`
  - badge 变体：`long` → `success`、`short` → `danger`、`cash` → `default`；未知值经 `labelFromMap` 原样回显（`default` 变体），与现有容错一致。
  - 模拟收益：`pct(row.simulatedReturnPct)`，按符号着色（复用现有三元式：`>0` `text-success` / `<0` `text-danger` / `=0` `text-secondary-text`，null → `text-muted-text` 显示 `--`）。cash 行引擎产出 `0.0` → 显示 `0.0%` 中性色（真实语义：空仓零收益，非缺数据）。
- **第二行（小字）**：出场原因
  - `EXIT_REASON_LABELS: Record<string, string> = { take_profit: '止盈', stop_loss: '止损', window_end: '窗口期满', window_end_short: '窗口期满(空)', cash: '无交易' }`（新增常量，与 `POSITION_LABELS` 一样当前文件中不存在）
  - 引擎事实（对抗审查修正后，对照 `_evaluate_targets` 非 long 分支 7 元组逐槽位核实）：long → `take_profit`/`stop_loss`/`window_end`；short → 恒为 `window_end_short`（`evaluate_single` 覆写）；**cash → `'cash'`**（元组第 7 槽位；`'not_applicable'` 是第 3 槽位 `first_hit` 的值，不是出场原因——本 spec 初稿曾误归因，已更正）。未知值经 `labelFromMap` 原样回显。
  - cash 标签选 `'无交易'` 而非 `'空仓'`：badge 已显示「空仓」，小字重复无信息量；「无交易」表达真实语义（未开仓、无出场）。

**缺省语义**：`positionRecommendation` 为 falsy（`undefined`/`null`/空串——旧记录/`insufficient_data`/error 行）→ 以 `!row.positionRecommendation` 守卫，整列渲染 `--`（不渲染 badge 与小字）。`positionRecommendation` 存在但 `simulatedReturnPct` 为 null（如 long 行极端窗口缺收盘）→ badge 照渲、收益位 `--`（`pct()` 既有行为）。

## 3. 测试（扩展 `BacktestPage.test.tsx`，沿用其既有 mock/render 约定）

**「做多」文本碰撞陷阱（fixture 取值必须钉死）**：既有用例断言 `screen.getByText('做多')`，命中的是**方向列**对 `directionExpected:'long'` 的遗留映射（`DIRECTION_EXPECTED_LABELS`）。既有用例保持绿**依赖一个脆弱不变量**：基础 fixture 没有 `positionRecommendation`（新列对它渲染 `--`）——基础 fixture 不要动。新用例的 fixture 若同时含 `directionExpected:'long'` 与仓位 long badge，`getByText('做多')` 会因多匹配抛错；规避方式是用**现代引擎方向值**（`infer_direction_expected` 实际产出 `up/down/not_down/flat`，不产出 long/cash）：

1. **short 赢单行**：`positionRecommendation:'short'`, `simulatedReturnPct:5.4`, `simulatedExitReason:'window_end_short'`, `directionExpected:'down'`, `actualReturnPct:-5.2`（价格下跌）→ 断言「做空」badge、`5.4%` 文本 + success 着色、「窗口期满(空)」同列渲染——锁"价格红/结果绿"反差被模拟收益解释的核心场景。
2. **long 止盈行**：`'long'`/`10.0`/`'take_profit'`/`directionExpected:'up'` → 「做多」（此 fixture 中唯一）「止盈」「10.0%」。
3. **cash 行**：`'cash'`/`0.0`/`'cash'`/`directionExpected:'flat'` → 「空仓」badge、「0.0%」、小字「无交易」。
4. **缺省行**：无 `positionRecommendation` → 该列 `--`，无 badge。
5. **收益缺值行**：`positionRecommendation:'long'` 且 `simulatedReturnPct` 缺省 → badge 照渲、收益位 `--`。
6. **未知出场原因**：`simulatedExitReason:'some_future_reason'` → 原样回显（锁 `labelFromMap` 容错路径）。
7. 既有用例零改动（回归证据；其前提见上方不变量说明）。

## 4. 验证

vitest（rsync 至 /tmp 无空格副本跑 `npx vitest run src/pages/__tests__/BacktestPage.test.tsx`）→ web-gate（`npm run lint && npm run build`，vitest 绿≠gate 绿，必须真跑）。后端零改动，不跑 ci_gate（最终核对 `git diff` 不含 .py 即可）。

## 5. 文档

- `docs/crypto-guide.md` 「永续回测（资金费 + 做空，1x）」小节补一句：Web 回测页已透出仓位（做多/做空/空仓）、资金费折算后的模拟收益与出场原因。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平追加一行：`- [新功能] Web 回测页新增「仓位/模拟」列：透出做多/做空/空仓、资金费折算后的模拟收益与出场原因（perp 做空回测结果首次用户可见）`。

## 6. 风险与回滚

- **风险**：极低——纯增列渲染，presence-only，不触碰任何取数/状态逻辑；旧数据（无仓位字段）降级为 `--`。
- **回滚**：单 commit revert，字节级恢复。

## 7. 已核实事实（设计依据）

- `BacktestResultItem`（API + TS）已含全部所需字段，无需任何契约改动。
- 引擎出场原因全集与各仓位取值（§2 引擎事实）：初稿曾把 `_evaluate_targets` 非 long 分支 7 元组的第 3 槽位 `first_hit='not_applicable'` 误归因为出场原因；经对抗审查逐槽位复核更正为 **cash 行 `simulated_exit_reason='cash'`**（第 7 槽位），§2/§3 已按更正后事实编写。
- 「窗口收益」列的 `actualReturnPct` 即 `stock_return_pct` 的直接映射（`src/services/backtest_service.py:667`），窗口模式恒填充——§0 的"价格红/结果绿"反差断言依此成立；1 日验证模式下新列同样成立（引擎同路径产出模拟字段），无需条件渲染。
- 桌面端复用 web 构建（`apps/dsa-desktop/main.js` 加载 `--serve-only` 后端静态挂载的同一 dist）。
- `BacktestPage.test.tsx` 存在于 `apps/dsa-web/src/pages/__tests__/`，测试可循其约定扩展；其基础 fixture 的 `'做多'` 断言来自方向列遗留映射（详见 §3 碰撞陷阱）。
