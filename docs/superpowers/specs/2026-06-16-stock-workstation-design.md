# 容器 B · 个股工作台（Stock Workstation）设计 spec

> 状态：设计已与用户确认（2026-06-16），待写实现计划。
> 容器演进：A 嵌入式 K 线抽屉（已上线）→ C 信号看板（已并入 `feat/kline-signals-spec`）→ **B 个股工作台（本期，终态）**。
> 前序资产：A=`apps/dsa-web/src/components/kline/*`；C=`/api/v1/signals/board` + `apps/dsa-web/src/pages/SignalBoardPage.tsx`。

## 1. 目标与定位

为单只标的提供一个**可导航、可常驻**的深度页 `/stock/:code`，把今天散落在抽屉/首页/侧栏的单股信息**汇聚到一处**，并提供页内**动作枢纽**（对这只股直接做事）。

今天的问题：单股信息是散的——K 线在临时抽屉(A)里、LLM 报告内联在首页、历史在侧栏，且**没有稳定的单股 URL**（当前仓库无任何参数路由）。B 给出一个稳定入口，从看板行 / 抽屉 / 首页报告都能跳进来。

定位口径（与用户确认）：**汇聚 + 动作枢纽**，而非纯只读页，也不在 v1 引入暂无后端的新分析面板。

## 2. 范围与非目标（YAGNI）

**v1 范围**
- 新增 `/stock/:code` 页面（首个参数路由）。
- 汇聚（全部复用现有组件/端点）：实时行情、K 线图+双轨信号+价位线+钻取、最新 LLM 报告、该股历史分析、相关新闻、命中率/一致性。
- 四个页内动作：加/减自选、发起/刷新 LLM 分析、为该股建告警、复制工作台链接。
- 从既有单股 surface 增加「在工作台打开」入口（additive）。

**非目标（v1 不做）**
- 命中率随时间的准确度趋势图、不绑定分析记录的「实时新闻流」、单股基本面面板（均为暂无后端 surface 的新工作，留待后续）。
- 不新增侧栏常驻导航项（B 是「针对某只股」的页，不是全局导航目标）。
- v1 不新增后端端点（见 §6 的 2 跳复用）；薄便捷端点仅作可选项标注。
- 不改动首页分析流程、抽屉(A)、看板(C)的既有行为（纯新增、共存）。

## 3. 锁定决策

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | 定位 | 汇聚 + 动作枢纽 |
| D2 | 与 A/首页边界 | 纯新增、共存；复用其组件，不改其页面 |
| D3 | 布局 | B：K 线常驻顶部 + 下方 tab（信号/报告/历史/新闻/告警） |
| D4 | 动作集 | 自选 toggle、刷新分析、建告警、复制链接（全部复用现有端点） |
| D5 | 后端 | v1 不新增端点；「最新报告」走 history 列表→最新 recordId→report 的 2 跳复用 |
| D6 | 取数 | 首屏 eager 取 quote + history+signals；报告/历史/新闻/告警按 tab 首次打开懒取 |
| D7 | 降级 | 各区块独立降级，单源失败不拖垮整页（沿用 C 的哲学） |

## 4. 路由与入口

- **路由**：`apps/dsa-web/src/App.tsx` 增 `const StockWorkstationPage = lazy(() => import('./pages/StockWorkstationPage'))` 与 `<Route path="/stock/:code" element={<StockWorkstationPage />} />`，挂在现有 Shell 包裹的路由组内。页面用 `useParams()` 取 `code`，`useNavigate()` 供返回/跳转。
- **入口（全部 additive，不改现有行为）**：
  - 看板行（`SignalBoardPage`/`SignalBoardGroup`）：保留行→快速抽屉(A)；**另加**每行一个「工作台 ↗」小图标/链接 → `navigate('/stock/' + code)`。
  - K 线抽屉(A `KLineDrawer`)头部：加「在工作台打开 ↗」→ 跳 B 并 `onClose()` 关抽屉。
  - 首页报告（`ReportOverview`）：加「在工作台打开 ↗」链接。
- **侧栏**：不加常驻项（见 §2 非目标）。

## 5. 页面组成（布局 B）

`apps/dsa-web/src/pages/StockWorkstationPage.tsx`，自上而下：

1. **行情头（sticky）**：返回链接、名称 + 代码 + 市场徽章、实时行情（复用 `GET /stocks/{code}/quote`）、四动作按钮（见 §7）。
2. **K 线区（全宽，常驻顶部）**：**直接复用 `KLineChartPanel`**（A 抽屉内同一组件，不经 `KLineDrawer`）——K 线 + 双轨买卖标注 + 价位线，数据来自 `GET /stocks/{code}/history` + `GET /stocks/{code}/signals`。
3. **下方 tab 面板**（默认开「信号」）：
   - **信号**：marker 列表 + 命中率/一致性（复用 `SignalDrilldownPanel`；数据即第 2 步 /signals 结果，无需再取）。
   - **报告**：该股最新 LLM 分析（复用 `ReportSummary` 及其子组件 `ReportOverview`/`ReportStrategy`/`ReportDetails`）。
   - **历史**：该股历史分析列表（复用 `HistoryList`/`HistoryItem`）；点条目查看该条旧报告（在「报告」区切换显示）。
   - **新闻**：最新分析记录的资讯（复用 `ReportNews`）。
   - **告警**：该股告警规则列表 + 快捷新建（复用 alerts 组件/端点，表单预填 `code`）。

新增小组件（B 私有）：`StockWorkstationHeader`（行情头 + 动作）、`WorkstationTabs`（tab 容器 + 懒挂载）。其余均为复用。

## 6. 数据流与取数策略

- **首屏 eager**：`code` 来自 `useParams` → 并行取 `quote`、`history`+`signals`（图）、`useWatchlist` 状态。
- **懒取**：报告/历史/新闻/告警在各自 tab **首次打开**时再取，避免每次开页就拉重的 LLM 报告。
- **最新报告（2 跳复用，D5）**：`GET /history?stock_code={code}`（列表）→ 取最新 recordId → `GET /history/{recordId}`（报告）；新闻 → `GET /history/{recordId}/news`。**不新增后端**。
  - 可选项（默认不做）：若 2 跳体感差，加薄端点 `GET /stocks/{code}/analysis/latest` 直接返回最新报告；属新增后端，留待度量后再定。
- **刷新分析**：`POST /analysis/analyze`（异步，返回 task_id）→ 轮询 `GET /analysis/status/{task_id}` → 完成后刷新「报告」tab（沿用首页分析的异步+轮询模式）。

## 7. 动作（全部复用现有端点）

| 动作 | 复用 | 行为 |
| --- | --- | --- |
| 加/减自选 | `useWatchlist` hook + `POST /stocks/watchlist/add\|remove` | 星标 toggle，乐观更新 |
| 刷新分析 | `POST /analysis/analyze` + 轮询 `GET /analysis/status/{task_id}` | 触发新分析，进行中态在按钮/报告区显示，完成刷新报告 |
| 建告警 | alerts/rules CRUD（`POST /alerts/rules`） | 打开预填本股 code 的告警表单 |
| 复制链接 | 浏览器 clipboard | 复制 `/stock/:code` 绝对 URL |

## 8. 复用映射

| 能力 | 复用组件 | 复用端点 |
| --- | --- | --- |
| K 线图 + 信号 | `KLineChartPanel`、`SignalDrilldownPanel` | `/stocks/{code}/history`、`/stocks/{code}/signals` |
| 实时行情 | （行情头内联渲染） | `/stocks/{code}/quote` |
| LLM 报告 | `ReportSummary`/`ReportOverview`/`ReportStrategy`/`ReportDetails` | `/history?stock_code=`、`/history/{recordId}` |
| 历史分析 | `HistoryList`/`HistoryItem` | `/history?stock_code={code}` |
| 新闻 | `ReportNews` | `/history/{recordId}/news` |
| 自选 | `useWatchlist` | `/stocks/watchlist/add\|remove` |
| 告警 | alerts 表单/列表组件 | `/alerts/rules` |

## 9. 错误 / 空态 / 降级（D7）

- **各区块独立降级**：图出错不影响报告；报告出错不影响图（沿用 C「单源失败不拖垮整页」）。
- **非法/未知 code**：整页显示「未找到该标的 / 无数据」态（不抛白屏）。
- **空态**：无分析记录 → 报告 tab 提示「尚无分析，点『刷新分析』生成」；无告警 → 「尚无告警，新建一条」；无新闻 → 「暂无相关资讯」。

## 10. 测试策略

- **前端页测试**（RTL + jsdom，`MemoryRouter` 包 `/stock/:code` 路由以驱动 `useParams`；mock api、`KLineChartPanel`、报告组件）：
  - 行情头 + quote 渲染；tab 切换（含懒取触发）；
  - 四动作接线：自选 toggle 调 hook；刷新分析触发 analyze + 轮询并在完成后刷新报告；建告警打开预填表单；复制链接写 clipboard；
  - 各区块独立空/错态；非法 code 的整页降级态。
- **复用组件**：已有各自测试，不重复；B 仅加页级 + tab 切换 + 动作接线 + 降级测试。
- **后端**：v1 无新增端点 → 无新增后端测试（若最终采纳可选薄端点，另补端点测试）。
- **门禁**：前端 `npm run lint`(eslint .) + 完整 `vitest` + `npm run build`；后端无改动则免（仅当采纳薄端点时跑 `ci_gate.sh`）。无空格 worktree 验证（沿用 A/C）。

## 11. 风险与缓解

- **报告组件耦合**：`ReportSummary` 等可能与 HomePage 状态耦合而非纯 props 驱动。缓解：实现期先核实；若耦合，做**最小解耦**（抽出 props 接口），不夹带无关重构。
- **2 跳取最新报告的延迟/语义**：列表→最新→报告共 2 次请求。缓解：懒取（仅开「报告」tab 时）；度量后再决定是否加薄端点（D5 可选项）。
- **刷新分析的成本/时长**：触发 LLM 分析消耗 token 且异步耗时。缓解：明确「进行中」态、禁用重复触发、复用既有轮询与超时文案。
- **首个参数路由**：确认 Shell 布局与 `RouteOutletBoundary` 对 `:code` 参数路由无额外要求（实现期核实）。

## 12. 实现期需核实项

1. `KLineChartPanel` 可脱离 `KLineDrawer` 独立按 props 复用（stockCode/market 等）。
2. `ReportSummary` 及子组件可按 recordId/report props 独立渲染（脱离 HomePage 状态）。
3. alerts 表单组件可预填并锁定单一 code。
4. `/analysis/analyze` + status 轮询的前端封装是否已有可复用客户端方法。
5. Shell 路由组对参数路由 `/stock/:code` 无特殊处理需求。

---

## 执行方式
沿用 A/C：**Subagent-Driven**（每任务 fresh 实现 subagent + 两段 review），无空格 worktree 验证。spec 获批 → 调 writing-plans 出实现计划（N 阶段，TDD）。
