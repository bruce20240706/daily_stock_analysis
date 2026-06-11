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

位置：插在「窗口收益」与「方向匹配」之间（表 8→9 列；`min-w-[900px]` → `min-w-[980px]`，已有"小屏幕可横向滚动"提示）。

单元格结构（上下两行，与「AI 预测」列的双行样式同构）：

- **第一行**：仓位 badge + 模拟收益
  - `POSITION_LABELS: Record<string, string> = { long: '做多', short: '做空', cash: '空仓' }`
  - badge 变体：`long` → `success`、`short` → `danger`、`cash` → `default`；未知值经 `labelFromMap` 原样回显（`default` 变体），与现有容错一致。
  - 模拟收益：`pct(row.simulatedReturnPct)`，按符号着色（复用现有三元式：`>0` `text-success` / `<0` `text-danger` / `=0` `text-secondary-text`，null → `text-muted-text` 显示 `--`）。cash 行引擎产出 `0.0` → 显示 `0.0%` 中性色（真实语义：空仓零收益，非缺数据）。
- **第二行（小字）**：出场原因
  - `EXIT_REASON_LABELS: Record<string, string> = { take_profit: '止盈', stop_loss: '止损', window_end: '窗口期满', window_end_short: '窗口期满(空)', not_applicable: '不适用' }`
  - 引擎事实（已核实 `src/core/backtest_engine.py`）：long → `take_profit`/`stop_loss`/`window_end`；short → 恒为 `window_end_short`（覆写）；cash → `not_applicable`（来自 `_evaluate_targets` 非 long 分支）。未知值原样回显。

**缺省语义**：`positionRecommendation` 缺省（旧记录/`insufficient_data`/error 行）→ 整列渲染 `--`（不渲染 badge 与小字）。

## 3. 测试（扩展 `BacktestPage.test.tsx`，沿用其既有 mock/render 约定）

1. **short 赢单行**：`positionRecommendation:'short'`, `simulatedReturnPct:5.4`, `simulatedExitReason:'window_end_short'`，且 `actualReturnPct:-5.2`（价格下跌）→ 断言「做空」badge、`+`收益绿色（或至少 `5.4%` 文本 + success 类）、「窗口期满(空)」同列渲染——锁"价格红/结果绿"反差被模拟收益解释的核心场景。
2. **long 止盈行**：`'long'`/`10.0`/`'take_profit'` → 「做多」「止盈」「10.0%」。
3. **cash 行**：`'cash'`/`0.0`/`'not_applicable'` → 「空仓」「0.0%」「不适用」。
4. **缺省行**：无 `positionRecommendation` → 该列 `--`，无 badge。
5. 既有用例零改动（回归证据）。

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
- 引擎出场原因全集与各仓位取值（§2 引擎事实）已对照 `src/core/backtest_engine.py` 逐分支核实。
- 桌面端复用 web 构建（`apps/dsa-desktop/main.js` 加载 `--serve-only` 后端静态挂载的同一 dist）。
- `BacktestPage.test.tsx` 存在于 `apps/dsa-web/src/pages/__tests__/`，测试可循其约定扩展。
