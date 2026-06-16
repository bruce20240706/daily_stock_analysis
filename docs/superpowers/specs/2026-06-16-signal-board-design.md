# 容器 C · 信号看板（自选股近实时量价信号仪表盘）设计

> 容器演进：A 嵌入式 K 线抽屉（✅ 已上线，2026-06-15 L3）→ **C 信号看板（本期）** → B 个股工作台（终态）。
> 本期在 L3 引擎/端点之上做**聚合层**：把单股信号能力升级为「自选池一屏可操作看板」。

---

## 1. 背景与问题

L3 已交付单股能力：量价信号引擎（`src/services/volume_price_signals.py`）、`GET /api/v1/stocks/{code:path}/signals`（`SignalMarker`/`price_lines`/`consistency`/`status`）、命中率回填（`verified`）、前端 K 线抽屉双轨标注 + 钻取。

缺口：用户要在**多只自选股之间**快速看「现在哪只该买 / 该卖 / 观望」，单股抽屉一次只能看一只，没有聚合视图。容器 C 解决这个 triage 问题。

## 2. 目标与非目标

### 本期目标（C-MVP）
1. 新页「信号看板」：对**自选池（`STOCK_LIST`）**近实时计算每只标的的量价信号，聚合成一屏。
2. 每只标的呈现 **「动作标签 + 证据」**：动作（买入/观望/卖出候选）+ 一致性 + 关键量价信号 + 入/损/标价位 + 命中率/verified。
3. 布局 **分组表格**：顶层按动作分组（买入候选 / 观望 / 卖出候选，取数失败行另入「数据不可用」），组内为密集可排序表格。
4. 点任一行 → 复用 L3 `KLineDrawer` 看完整图与双轨标注。
5. 复用为主：抽出可复用 `build_signals_for_code` service，单股端点与看板端点**共用同一实现**，不造平行决策链。

### 非目标（明确不做，留后续）
- **全市场扫描 / 缓存快照**（候选 universe ~4500 A 股 + 新快照表 + 每日任务接入）——留 **C 第二步 / phase 2**。
- 自动下单 / 执行桥（opt-in 解耦，P2）。
- 超出「规则收敛方向 + 命中率」之外的新打分模型 / 新量能口径。
- B 个股工作台全屏盯盘 + 手绘（终态）。
- 看板内嵌完整 K 线（复用抽屉打开，看板只出摘要行）。

## 3. 已锁定决策

| 决策点 | 结论 |
| --- | --- |
| 扫描范围 | **自选池 `STOCK_LIST`**（含 A 股/港股通/美股/crypto，按自选实际内容），非全市场 |
| 新鲜度 | **近实时**：打开/刷新时并发逐股计算；短 TTL 缓存避免频繁重算；手动刷新强制重算 |
| 输出口径 | **动作标签 + 证据**：给明确买/卖/观望标签，旁列证据（一致性、关键量价信号、入/损/标、命中率/verified） |
| 布局 | **分组表格**（A 密度 + C 动作分组）：买入候选/观望/卖出候选三组，组内密集可排序表格 |
| 动作分组口径 | 按 **规则收敛方向**（确定性、实时）：bullish→买入候选 / bearish→卖出候选 / neutral→观望；取数/计算失败 → 第 4 桶「数据不可用」；`consistency` 列标规则↔LLM 是否一致，**冲突仍留在规则方向的组并标红**，系统不替用户做主 |
| 计算架构 | **方案 1**：新增看板端点 + 有界并发 + 复用 `build_signals_for_code` + 短 TTL 缓存；单 code 失败仅降级该行，不拖垮整盘 |
| 行交互 | 点击行 → 复用 L3 `KLineDrawer`（不在看板内嵌图） |
| 入口 | 新页 `/board`，侧栏 NavItem「信号看板」；可仿 `screening` 用配置门控，默认常显 |
| 复用 | 抽 `build_signals_for_code`；复用引擎/`BuySignal`/consistency/命中率 resolver/`_read_watchlist_codes`/`KLineDrawer`/`data_provider`，不造平行实现 |

## 4. 架构与数据流

```
[前端 SignalBoardPage (新页 /board, NavItem「信号看板」)]
        │  GET /api/v1/signals/board?days=120[&refresh=1]
        ▼
[api/v1/endpoints: signals_board (新端点)]
        │  ① _read_watchlist_codes(STOCK_LIST) 取自选池
        │  ② 有界并发(线程池, 上界 min(8, N)) 逐 code:
        │        build_signals_for_code(code, days) -> BoardSignals  ← 从 /signals handler 抽出、单股/board 共用
        │     · 每 code 独立 try：失败 → 该 entry status=degraded + action_group=unavailable（不抛、不拖垮整盘）
        │     · (code, trade_date, days) 短 TTL 内存缓存；refresh=1 跳过缓存
        │  ③ BoardSignals → BoardEntry 摘要（动作分组 + 关键信号 + 价位 + 命中率）
        ▼
[SignalsBoardResponse: entries[] + counts + degraded_codes + as_of]   （degraded 仍 200）
        ▼
[前端按 action_group 分组渲染密集表格；组内排序/筛选；行点击 → 复用 KLineDrawer]
```

**复用为主、新增为辅**：新增 = 看板端点 + `build_signals_for_code` 抽取 + 有界并发/缓存封装 + 前端看板页/组件。复用 = 量价引擎、`build_signals_payload`、`BuySignal`/consistency、命中率 resolver、`_read_watchlist_codes`、`KLineDrawer`、`data_provider`。

## 5. 详细设计

### 5.1 后端 · 抽出 `build_signals_for_code`（N0，纯重构）
现状：`/signals` 端点（`api/v1/endpoints/stocks.py` ~591–700）把「取数 → 引擎 → BuySignal → consistency → 命中率回填 → price_lines」组装逻辑写在 handler 内。
重构：把这段抽成 `src/services/signals_service.py` 中的 `build_signals_for_code(code, *, days, ...) -> BoardSignals`（封装「取数（与 /history 同源）→ 引擎 → `BuySignal` → consistency → 命中率回填 → price_lines」的完整单股编排）。

**关键修订（评审 A）：返回比 `SignalsResponse` 更宽。** `SignalsResponse` 实测仅 `{status, markers, price_lines, consistency, degraded_reason}`（`api/v1/schemas/stocks.py`），**不含**看板分组所需的 `rule_direction`/`latest_close`/`name`——而 handler 里算出的 `rule_signal` 在 `build_signals_payload` 内用完即弃、不外露（`stocks.py:649-653`）。故 `build_signals_for_code` 返回内部结构 `BoardSignals`：
- `signals_payload`：即 SignalsResponse 的 5 字段（单股端点直接 `SignalsResponse(**signals_payload)`，逐字节不变）；
- `rule_direction`：`buy_signal_to_direction(rule_signal)`——**看板 `action_group` 的唯一数据源**；
- `latest_close`：`rows[-1].close`；
- `name` / `market`：`StockService.get_history_data` 的 `stock_name`（`stock_service.py:68`）/ 由 code 推断。

单股端点退化为薄壳：调 `build_signals_for_code` → 取 `signals_payload` → `SignalsResponse(**payload)`。**单股响应逐字节不变**，以等价回归测试锁定（`tests/test_signals_endpoint.py` 现有断言不变 + 新增 service 直测）。

### 5.2 后端 · 看板端点（N1）`GET /api/v1/signals/board`
- Query：`days`（默认 120，沿用 /history/signals 口径）；`refresh`（可选，跳过缓存）。
- 取自选池：`_read_watchlist_codes(system_config_service)`（`stocks.py` ~81–147）。
- 并发：有界线程池（上界 `min(8, len(codes))`），每 code 调 `build_signals_for_code`；**单 code try/except → 该 entry `status=degraded` + `degraded_reason` + `action_group='unavailable'`**，整盘继续。
- **线程安全（评审 C，N1 必须确认）**：看板为**只读** DB。① DB 经 `sessionmaker`（`storage.py:859`）须每线程取独立 session，并确认连接池容量 ≥ 并发上界（SQLite 走 WAL、只读并发无写锁竞争）；② TTL 缓存为可变共享态 → 读写**加锁**，且**多 worker 部署下进程内缓存不共享**（各 worker 各自重算，可接受）；③ `data_provider` 客户端线程安全或仅依赖其自身缓存。**优先复用 `AnalysisTaskQueue` 既有 worker pool** 而非自造线程池，规避线程安全自证。
- 缓存：进程内 TTL，键 `(code, trade_date, days)`；命中即返回，`refresh=1` 跳过；TTL 取短（默认值见 §10，避免盘中频繁重算又保持近实时）。
- 映射 `BoardSignals → BoardEntry`（摘要，见 5.3）。
- 空自选 → `entries=[]`（200）；任意行降级仍 200，`degraded_codes` 列出。

### 5.3 BoardEntry / SignalsBoardResponse 契约（additive，镜像前端类型）
```
BoardEntry（来源：均由 build_signals_for_code 的 BoardSignals 投影/派生）:
  code: str                                      # 入参
  name: str | null                               # BoardSignals.name（StockService.stock_name）
  market: str | null                             # 由 code 推断
  action_group: 'buy'|'hold'|'sell'|'unavailable'  # rule_direction 映射；degraded → 'unavailable'
  rule_direction: 'bullish'|'bearish'|'neutral'| null  # BoardSignals.rule_direction（buy_signal_to_direction）；degraded → null
  llm_direction:  'bullish'|'bearish'|'neutral'| null  # 派生自 signals_payload.markers 中 source=='llm' 那点 direction
  consistency: 'consistent'|'divergent'|'conflict'|'unknown'|'stale'  # signals_payload.consistency
  key_signals: string[]                          # signals_payload.markers 中 source=='rule' 的 signal_type 去重
  price_lines: { entry: float|null, stop: float|null, target: float|null }  # signals_payload.price_lines
  latest_close: float | null                     # BoardSignals.latest_close
  hit_rate: float|null; hit_sample: int|null; verified: bool  # 取 rule markers 命中字段（M2c 按 code，同 code 各 rule marker 同值；无 rule marker → null/false）
  status: 'ok' | 'degraded'; degraded_reason: str | null

SignalsBoardResponse:
  as_of: int            # epoch ms
  entries: BoardEntry[]
  counts: { buy: int, hold: int, sell: int, unavailable: int }   # 四桶计数（unavailable = degraded 行）
  degraded_codes: string[]
```

### 5.4 动作分组与一致性口径
- `action_group` 仅由 **规则收敛方向**（`buy_signal_to_direction(rule_signal)`，确定性、实时）决定：bullish→buy / bearish→sell / neutral→hold。
- `consistency` 沿用 `compute_consistency`（规则方向 × LLM 最新结论 × 时效）。冲突（规则与 LLM 异向）**不改变分组**（仍按规则方向入组），仅在行内 `consistency` 列标红，让用户看见分歧、自行判断。
- **degraded 行（评审 B）**：取数/计算失败的 entry 无 `rule_direction`，落不进 buy/hold/sell，归入**第 4 桶「数据不可用」**（`action_group='unavailable'`），**不计入** buy/hold/sell counts（单列 `counts.unavailable` + `degraded_codes`）；前端单独成组、可折叠，避免污染候选判断。
- `hit_rate/hit_sample/verified`：沿用 M2c（按 code 聚合的历史方向命中率，含杠杆去重修复）；同一 code 各规则信号共享该值，行取该值。

### 5.5 前端（N2/N3/N4）
- 新页 `apps/dsa-web/src/pages/SignalBoardPage.tsx` + 路由 `/board`（`App.tsx`）+ 侧栏 NavItem「信号看板」（`SidebarNav.tsx` NAV_ITEMS）。
- client：`apps/dsa-web/src/api/stocks.ts` 追加 `getBoard(days?, refresh?)`（与 L3 `getSignals` 同域、同 snake→camel 风格；不复用 `history.ts`）。
- 组件：`SignalBoard`（分组容器，**四组**：买入候选 / 观望 / 卖出候选 / 数据不可用）→ `SignalBoardGroup`（可折叠组头：动作+数量；组内密集表格，列可排序）→ 行。
- 列：标的 | 规则 | LLM | 一致性 | 关键量价信号 | 入/损/标 | 命中率·验证态。中式红涨绿跌、verified 标记沿用 L3 视觉（复用 `SignalDrilldownPanel` 的展示 idioms）。
- 行点击 → 复用 `KLineDrawer`（A）打开该 code。
- 状态：loading 骨架（并发计算）、degraded 行（「信号不可用」+ 原因）、空自选引导（「自选为空，去添加」）；筛选（市场 / 仅已验证 / 一致性）、组内排序（命中率 / 一致性）。
- NavItem 默认常显；如需门控，仿 `screening` 的配置事件机制。

## 6. 测试策略
- **后端**：
  - `build_signals_for_code` 抽取后 `tests/test_signals_endpoint.py` 单股断言**不变**（等价回归）+ service 直测。
  - 看板端点：多 code 分组与 counts；**单 code 失败 → 该行 degraded、其余正常、整盘 200**（不被单源拖垮）；空自选；缓存命中（同参第二次不重算，可注入计数验证）；`refresh=1` 跳缓存。
- **前端**：`getBoard` snake→camel 映射；`SignalBoard` 分组/排序/筛选；行→抽屉打开；degraded/空态渲染。
- 门禁：后端 `ci_gate.sh`；前端 `vitest` + `eslint .` + `npm run build`（无空格副本/worktree 跑）。

## 7. 交付切分（各自可独立合入/回滚，沿用 Subagent-Driven）
- **N0**：抽出 `build_signals_for_code`（纯重构，单股行为不变 + 等价测试）。
- **N1**：看板端点 + BoardEntry 契约 + 有界并发 + 单行降级 + TTL 缓存 + 测试。
- **N2**：前端 `getBoard` client + 类型契约。
- **N3**：前端分组表格组件（组/排序/筛选）+ 状态 + 测试。
- **N4**：行→抽屉 + nav/路由 + 全量门禁 + `docs/CHANGELOG.md` + 专题文档。

## 8. 风险与回滚
- 风险：并发对 `data_provider` 压力（有界并发 + TTL 缓存 + 单 code 超时缓解）；缓存陈旧（短 TTL + 手动刷新）；超大自选首屏耗时（有界并发 + TTL 缓存 + 单 code 超时 + degraded 行兜底）。
- **请求语义（评审 D）**：单个 `GET /board` 为 **barrier**——等全部 code 算完一并返回（转圈直到完成），degraded 行与正常行同批返回、不阻塞整盘；**流式/SSE 渐进渲染非本期**。
- 兼容：纯增量（新端点 + 新页 + 抽出的 service），不改 L3 既有契约；`build_signals_for_code` 抽取以等价测试保证单股端点不回归。
- 回滚：回退看板端点 + 新页即可；service 抽取可保留（无害）或一并回退。

## 9. 复用引用（核验于 2026-06-16，实现时以现状为准）
- `/signals` 单股 handler（待抽取）：`api/v1/endpoints/stocks.py` ~591–700
- 组装：`src/services/signals_service.py`（`build_signals_payload`/`compute_consistency`/`buy_signal_to_direction`）
- 引擎：`src/services/volume_price_signals.py`（`compute_volume_price_signals` + `VPSConfig.from_env`）
- 命中率：`src/services/signal_hit_rate.py`（`resolve_marker_hit_fields`）
- 自选池：`api/v1/endpoints/stocks.py` `_read_watchlist_codes` ~81–147（`STOCK_LIST` 配置）
- 并发参考：`src/services/task_queue.py`（worker pool）/ `data_provider`
- 前端抽屉：`apps/dsa-web/src/components/kline/KLineDrawer.tsx`、契约类型 `src/types/kline.ts`
- 前端路由/导航/页样式参考：`apps/dsa-web/src/App.tsx`、`src/components/layout/SidebarNav.tsx`、`src/pages/StockScreeningPage.tsx`

## 10. 开放细项（实现时定，不阻断设计）
- TTL 缓存具体时长与缓存键是否含「实时报价」维度（默认按 `(code, trade_date, days)`，日线 bar 内不变，盘中以短 TTL 兼顾）。
- `key_signals` 取多少个 / 是否按近因或强度排序（默认取规则 markers 去重后的 signal_type 全集，前端按需截断）。
- 并发上界与单 code 超时具体值（默认 `min(8, N)` / 复用 `data_provider` 既有超时）。
- **（评审 E）循环导入**：`build_signals_for_code` 进 `signals_service.py` 会引入 `StockService`/`StockTrendAnalyzer`/`DatabaseManager`/`derive_price_levels` 依赖；本仓已有先例 `apply_price_levels_to_guard` 对 `src.analyzer` 用延迟导入。N0 沿用延迟导入规避循环（`BuySignal`/`StockTrendAnalyzer` 同源 `src.stock_analyzer`、`signals_service` 已 import 之，低风险）。
- **（评审 F）crypto 缓存键**：crypto 7×24 无收盘日，`(code, trade_date, days)` 的 `trade_date` 对 crypto 取当前日期 + 仅靠短 TTL 兼顾新鲜度。
- **（评审 G）配置提读一次**：`SIGNALS_STALE_TRADING_DAYS`/`KLINE_PRICE_LEVEL_*`/`VPS_*` 每次看板请求读一次后传入各 code，避免每 code 重读 N 次。
- **（评审 H）组内排序定序**：`consistency` 5 态给定序（如 consistent>divergent>conflict>unknown>stale）；默认按命中率降序、未达样本/未验证沉底。
- **（评审 I）鉴权**：看板端点沿用现有 `/stocks` 端点的鉴权/中间件，不另开放。

## 11. 评审处置（review → v2）

本稿为 **v2**，已并入代码级评审的全部处置（均对照真实代码核验）：
- **A（必补，已改）**：`build_signals_for_code` 改为返回更宽的 `BoardSignals`（payload + `rule_direction` + `latest_close` + `name`），补齐 `action_group`/`rule_direction` 等 BoardEntry 字段的**数据来源**——此前 `SignalsResponse` 仅 5 字段、`action_group` 无来源（§5.1/§5.3）。
- **B（必补，已改）**：新增第 4 桶「数据不可用」承载 degraded 行（`action_group='unavailable'`、`counts.unavailable`），不混入 buy/hold/sell（§5.3/§5.4/§5.5）。
- **D（已改）**：修正 §8——单 `GET /board` 为 barrier，删除"部分先渲染"误述，流式非本期。
- **C（收紧，已改）**：§5.2 明确线程安全三项（每线程 session/连接池、缓存加锁/多 worker 不共享、provider 线程安全），优先复用 `AnalysisTaskQueue`。
- **E–I（已并入 §10）**：循环导入用延迟导入、crypto 缓存键、配置提读一次、排序定序、鉴权沿用。
