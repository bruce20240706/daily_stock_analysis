# 个股工作台（容器 B）

## 定位

「个股工作台」是单股深度页 + 动作枢纽，路由为 `/stock/:code`。

容器演进脉络：A（K 线抽屉，临时浮层） → C（信号看板，列表聚焦） → **B（工作台，终态可导航深度页）**。工作台将散落在抽屉、看板、首页报告中的单股 surface 汇聚为可直接导航、可收藏为浏览器书签的独立页面，并提供行情头 + 4 个页内动作（加/出自选、刷新分析、建告警、复制链接），不改动 A/C/首页任何现有行为。

---

## 路由与入口

**路由**：`/stock/:code`（项目首个带 path 参数的路由，react-router v6 已自动 URL-decode path 参数，含 `/` 的 crypto 代码如 `BTC/USDT` 须先 `encodeURIComponent` 再写入 href）。

**三个入口**：

| 位置 | 元素 | 行为 |
|------|------|------|
| 信号看板行（`SignalBoardGroup`） | 「工作台 ↗」按钮 | `navigate('/stock/' + encodeURIComponent(code))` |
| K 线抽屉（`KLineDrawer`） | 「在工作台打开 ↗」链接 | `<Link to={...}>` |
| 首页报告概览（`ReportOverview`） | 「在工作台打开 ↗」链接 | `<Link to={...}>` |

以上三处均为纯新增链接，不改原有交互。

---

## 页面布局

```
┌──────────────────────────────────────────┐
│ 行情头（sticky-like；股名 + 实时价格 + 涨跌幅）      │
│ [☆ 自选]  [刷新分析]  [建告警]  [复制链接]         │
├──────────────────────────────────────────┤
│ K 线区（KLineChartPanel，Suspense 懒加载，         │
│         ChartErrorBoundary 独立降级）              │
├──────────────────────────────────────────┤
│ [信号] [报告] [历史] [告警]  ← 默认「信号」tab     │
├──────────────────────────────────────────┤
│ Tab 内容区（按 tab 懒取）                          │
└──────────────────────────────────────────┘
```

**行情头**（`StockWorkstationHeader`）：mount 时立即调用 `stocksApi.getQuote(code)` 取实时行情（名称 + 价格 + 涨跌幅），失败静默不阻塞页面。4 个动作按钮均在此区域。

**K 线区**：复用 `KLineChartPanel`，`Suspense` 懒加载 klinecharts vendor chunk；外层 `ChartErrorBoundary` 确保图区出错只降级图，不影响下方 tab。

**4 个 tab**（`信号 / 报告 / 历史 / 告警`）：默认展示「信号」，其余 tab 首次点击时懒取数据。新闻并入「报告」tab（`ReportSummary` 内含 `ReportNews`），不单设新闻 tab。

---

## 各 Tab 数据来源与懒取策略

### 信号 tab（`StockSignalsPanel`）

- 数据来源：`stocksApi.getSignals(code)` → `GET /api/v1/stocks/{code}/signals`
- 展示：量价信号列表（信号类型、方向、来源 LLM/规则、命中率）+ 一致性标签 + 入/损/标价位
- 降级：fetch 失败仅显示"信号加载失败"，不影响其他区域

### 报告 tab（`ReportSummary` via `historyApi`）

- **懒取 + 2-hop**：首次切换到报告 tab 时触发
  1. `historyApi.getList({ stockCode: code, limit: 1 })` 取最新 recordId
  2. `historyApi.getDetail(recordId)` 取完整报告
- 通过 `reportLoadedForRef`（ref 哨兵）确保每个 code 最多触发一次自动取报告，防止空记录死循环
- 空态文案："尚无分析，点上方「刷新分析」生成。"
- 点历史列表中某条目 → `onSelectHistory(recordId)` → 直接取指定 recordId，无需二次查 list
- 无新增后端端点，复用现有 `/api/v1/history` 接口

### 历史 tab（`StockHistoryPanel`）

- 数据来源：`historyApi.getList({ stockCode: code, limit: 20 })`
- 展示：操作建议 + 日期 + 情绪分列表
- 点击某条目：切换到报告 tab 并加载该 recordId 的完整报告

### 告警 tab（`StockAlertsPanel`）

- 数据来源：`alertsApi.listRules({ target: code, targetScope: 'single_symbol' })`
- 上方展示「建告警」表单，复用 `AlertRuleForm`，通过可选 prop `lockedTarget={code}` 预填并锁定标的字段（默认行为不变，lockedTarget 为 additive prop）
- 下方展示本股已有告警规则只读列表
- 底部「在告警页管理 →」链接指向 `/alerts` 完整告警管理页

---

## 刷新分析（异步 + 轮询）

触发：点「刷新分析」按钮 → `onRefreshAnalysis` → `runAnalysis()`。

```
analysisApi.analyzeAsync({ stockCode, reportType: 'detailed', forceRefresh: true })
  → 返回 taskId
  → pollUntilDone(taskId)：首次立即、之后每 2s 取状态
  → completed → setReport(result.report)，切换到报告 tab
  → failed    → setReportError(error)，切换到报告 tab
  → 30次轮询耗尽(~60s) → "分析超时，请稍后重试"
```

- 进行中态（`refreshing=true`）：按钮显示"分析中…"并禁用，防止重复触发
- `DuplicateTaskError`（HTTP 409）：复用 `e.existingTaskId` 直接轮询已有任务
- 卸载守卫：`isMountedRef` 确保组件卸载后不再调用 setState

---

## 与 A（抽屉）/ C（看板）/ 首页的边界

- **纯新增、共存**：工作台不修改 `KLineDrawer`、`SignalBoardPage`、`HomePage` 的任何现有交互逻辑。
- 三个入口处仅追加链接，不改变原有点击行为。
- `AlertRuleForm` 的 `lockedTarget` 为可选 prop（`lockedTarget?: string`），不传时完全保持原有行为。
- `SignalBoardGroup` 新增 `useNavigate()` 调用（用于「工作台 ↗」按钮），因此在测试中需提供 Router 上下文（`MemoryRouter` 包裹）。

---

## 前端文件清单

| 文件 | 说明 |
|------|------|
| `src/pages/StockWorkstationPage.tsx` | 页面主体：路由读参、tab 状态、报告懒取、刷新分析逻辑 |
| `src/components/workstation/StockWorkstationHeader.tsx` | 行情头 + 4 动作按钮（getQuote、toggleWatchlist、刷新分析、建告警、复制链接） |
| `src/components/workstation/StockSignalsPanel.tsx` | 信号 tab（getSignals） |
| `src/components/workstation/StockHistoryPanel.tsx` | 历史 tab（getList，点选条目） |
| `src/components/workstation/StockAlertsPanel.tsx` | 告警 tab（listRules + createRule + lockedTarget 表单） |
| `src/components/workstation/ChartErrorBoundary.tsx` | K 线区独立错误边界 |
| `src/api/stocks.ts` | 新增 `stocksApi.getQuote(code)` + `StockQuote` 类型 |
| `src/components/alerts/AlertRuleForm.tsx` | 新增可选 `lockedTarget` prop |
| `src/components/board/SignalBoardGroup.tsx` | 新增「工作台 ↗」按钮（useNavigate） |
| `src/components/kline/KLineDrawer.tsx` | 新增「在工作台打开 ↗」链接 |
| `src/components/report/ReportOverview.tsx` | 新增「在工作台打开 ↗」链接 |
| `src/App.tsx` | 新增路由 `/stock/:code` |

---

## 已知局限

- **无独立新闻 tab**：新闻含于「报告」tab（`ReportSummary` 内置 `ReportNews`），不单设。
- **quote 为薄客户端**：`stocksApi.getQuote` 仅取当前时刻行情快照，不做实时推送或轮询刷新。
- **命中率口径**：沿用 `/signals` 端点既有命中率计算口径，不新增统计逻辑。
- **轮询超时约 60s**：`pollUntilDone` 最多 30 次 × 2s = ~60s 后提示超时，不可配置。
- **无端到端验证**：quote/刷新分析/告警创建需真实后端下 `npm run dev` 人工目检；自动门禁覆盖前端单元测试 + 构建。

---

## 回滚

纯前端增量。回退方式：

1. 移除 `/stock/:code` 路由（`src/App.tsx`）
2. 删除 `src/pages/StockWorkstationPage.tsx` 及 `src/components/workstation/` 目录
3. 撤销 `AlertRuleForm` 的 `lockedTarget` prop（可保留，为 additive）
4. 撤销 `SignalBoardGroup`、`KLineDrawer`、`ReportOverview` 中的入口链接
5. 可选：撤销 `stocksApi.getQuote` + `StockQuote`（可保留，为 additive）

无后端变更，无数据库迁移，回滚不需要数据操作。
