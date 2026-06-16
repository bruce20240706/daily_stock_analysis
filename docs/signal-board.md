# 信号看板（容器 C）字段契约与语义

> 对应实现：后端 `src/services/signal_board_service.py`、`api/v1/endpoints/signals.py`、`api/v1/schemas/stocks.py`（`BoardEntry`/`BoardCounts`/`SignalsBoardResponse`）；前端 `apps/dsa-web/src/pages/SignalBoardPage.tsx` + `components/.../SignalBoard*`。
> 设计 spec：`docs/superpowers/specs/2026-06-16-signal-board-design.md`（v2）。
> 容器演进：A 嵌入式 K 线抽屉（已上线 L3）→ **C 信号看板（本文）** → B 个股工作台（终态）。

---

## 1. 定位与范围

「信号看板」是容器 C（MVP）：对**自选池**（`STOCK_LIST`）近实时计算每只标的的量价信号，聚合成一屏可操作的分组表格。

它回答的是「**现在自选池里该买 / 卖 / 观望哪只**」这个 triage 问题——是一份对固定标的池的可操作清单，**不是发现型选股器**，不做全市场扫描。

非目标（明确不做，留后续）：

- 全市场扫描 / 缓存快照（候选 universe + 新快照表 + 每日任务接入）。
- 自动下单 / 执行桥。
- 看板内嵌完整 K 线（看板只出摘要行，点行复用 L3 抽屉看图）。
- 超出「规则收敛方向 + 命中率」之外的新打分模型 / 新量能口径。

## 2. 与 AlphaSift「选股」的区别

二者定位不同、互补，不要混淆：

| | 信号看板（容器 C） | AlphaSift 选股 |
|---|---|---|
| 输入 | 固定自选池 `STOCK_LIST` | 全市场候选 universe |
| 方法 | 量价技术信号（规则引擎收敛方向 + LLM 结论一致性 + 历史命中率） | 基本面 / 条件筛选策略 + LLM 选股重排 |
| 产出 | 自选池逐股「买/卖/观望」可操作清单 | 「发现」出符合策略的新标的 |
| 角色 | 已有自选的盯盘 triage | 从未持有中找新候选 |

AlphaSift 接入说明见 `docs/alphasift-integration.md`。

## 3. 端点契约

`GET /api/v1/signals/board`

| Query | 默认 | 约束 | 说明 |
|---|---|---|---|
| `days` | `120` | `1..365` | 日历回看天数，与 `/history`、单股 `/signals` 同源窗口 |
| `refresh` | `false` | bool | `true` 跳过缓存强制重算 |

鉴权沿用现有 `/stocks` 端点的鉴权/中间件，不另开放。任意行降级仍返回 **200**；空自选返回 `entries=[]` + 200。

响应 `SignalsBoardResponse`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `as_of` | `int` | 看板数据基准时间（epoch ms） |
| `entries` | `BoardEntry[]` | 看板条目列表 |
| `counts` | `BoardCounts` | 四桶计数 `{buy, hold, sell, unavailable}` |
| `degraded_codes` | `string[]` | 降级（`status=degraded`）的股票代码列表 |

`BoardEntry`（字段以 `api/v1/schemas/stocks.py` 为准）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | `str` | 股票代码 |
| `name` | `str \| null` | 股票名称（取自 `StockService` 历史数据 `stock_name`） |
| `market` | `str \| null` | 市场，由 code 推断（`CN`/`HK`/`US`/`crypto`） |
| `action_group` | `'buy' \| 'hold' \| 'sell' \| 'unavailable'` | 动作分组（看板分栏依据，见 §4） |
| `rule_direction` | `'bullish' \| 'bearish' \| 'neutral' \| null` | 规则/量价收敛方向；degraded 行为 `null` |
| `llm_direction` | `'bullish' \| 'bearish' \| 'neutral' \| null` | LLM 方向，派生自 markers 中 `source=='llm'` 那点 |
| `consistency` | `'consistent' \| 'divergent' \| 'conflict' \| 'unknown' \| 'stale'` | 规则与 LLM 一致性 |
| `key_signals` | `string[]` | markers 中 `source=='rule'` 的 `signal_type` 去重 |
| `price_lines` | `{entry, stop, target}` | 买卖价位线，各子字段允许 `null` |
| `latest_close` | `float \| null` | 最新收盘价 |
| `hit_rate` | `float \| null` | 历史方向命中率，无样本为 `null` |
| `hit_sample` | `int \| null` | 命中率样本数 |
| `verified` | `bool` | `hit_sample` 达阈值则 `true` |
| `status` | `'ok' \| 'degraded'` | 单条状态 |
| `degraded_reason` | `str \| null` | `status=degraded` 时的原因说明 |

`BoardCounts`：`buy` / `hold` / `sell` / `unavailable` 四个整数，默认 0。

## 4. 动作分组口径

`action_group` 仅由**规则收敛方向**（`rule_direction`）决定，确定性、实时：

| `rule_direction` | `action_group` | 看板分组 |
|---|---|---|
| `bullish` | `buy` | 买入候选 |
| `bearish` | `sell` | 卖出候选 |
| `neutral` | `hold` | 观望 |
| `null` | `unavailable` | 数据不可用 |

边界（务必区分两种「失败」）：

- **数据存在、分析器失败**：规则方向计算（`StockTrendAnalyzer`）抛错但有历史 bar 时，`rule_direction` 退化为 `neutral` → 落入**观望（hold）**，不是 unavailable。
- **无可用数据 / 单股异常**：取不到历史数据（`rows` 为空），或 `build_signals_for_code` 整体抛错被兜底捕获，`rule_direction=null` → 落入**数据不可用（unavailable）**，并计入 `degraded_codes`。

一致性冲突**不改变分组**：规则与 LLM 异向时，该行仍按规则方向入组，仅在 `consistency` 列标红呈现分歧，系统不替用户做主。`unavailable` 桶**不计入** buy/hold/sell，单列 `counts.unavailable`。

## 5. 并发 / 降级 / 缓存语义

- **并发**：`build_board` 用 `ThreadPoolExecutor`，上界 `min(SIGNALS_BOARD_MAX_WORKERS, len(codes))`；逐 code 调 `build_signals_for_code`。每个 worker 经 `StockService` / `DatabaseManager` 各取所需 DB 访问（只读看板，并发安全）。
- **降级**：单 code 计算失败仅降级该行——进 `unavailable` 桶（`status=degraded` + `degraded_reason`）并计入 `degraded_codes`，整盘继续、不被单源拖垮，整体仍 200。
- **缓存**：进程内 TTL 缓存，键为 `(code, days)`，TTL = `SIGNALS_BOARD_CACHE_TTL_S` 秒；命中即返回，`refresh=true` 绕过。缓存读写加锁（`threading.Lock`）。
- **请求语义**：单个 `GET /board` 为 barrier——等全部 code 算完一并返回，degraded 行与正常行同批返回；流式 / SSE 渐进渲染非本期。

## 6. 复用关系

- 单股 `/signals`（`api/v1/endpoints/stocks.py`）与看板 `/signals/board` 共用 `build_signals_for_code`（`src/services/signal_board_service.py`），不造平行决策链。
- `build_signals_for_code` 复用 `src/services/signals_service.py` 的纯函数（`build_signals_payload` / `compute_consistency` / `buy_signal_to_direction`）、L3 量价引擎（`volume_price_signals.py`）、价位反算器（`derive_price_levels`）、命中率 resolver（`signal_hit_rate.py`）。
- 自选池读取复用 `_read_watchlist_codes`；前端行点击复用 L3 `KLineDrawer`。

## 7. 配置项

| 环境变量 | 默认值 | 约束 | 语义 |
|---|---|---|---|
| `SIGNALS_BOARD_CACHE_TTL_S` | `300` | `>= 0` | 看板 `(code, days)` 缓存 TTL（秒）；0 关闭缓存 |
| `SIGNALS_BOARD_MAX_WORKERS` | `8` | `>= 1` | 并发线程池上界（实际取 `min(该值, 标的数)`） |

二者已登记在 `.env.example`，**不配置即可运行，配置后增强**。同时登记在 `src/core/config_registry.py` 的 `WEB_SETTINGS_HIDDEN_FROM_UI`——属运行时调优常量，**不在 Web 设置页暴露**。

## 8. 前端

- 页面路由 `/board`（`apps/dsa-web/src/App.tsx`），侧栏入口「信号看板」（`SidebarNav.tsx`）。
- 四动作分组的密集可排序表格（买入候选 / 观望 / 卖出候选 / 数据不可用），组内默认按命中率降序、无样本/未验证沉底。
- 列：标的 | 规则 | LLM | 一致性 | 关键量价信号 | 入/损/标 | 命中率·验证态。
- 中式配色：看多 / 买入 = 红，看空 / 卖出 = 绿；verified 标记沿用 L3 视觉。
- 点任一行 → 复用 K 线抽屉，看完整图与 L3 双轨标注 / 钻取。
- 状态：并发计算 loading 骨架、degraded 行（「信号不可用」+ 原因）、空自选引导。

## 9. 已知局限 / 风险

- **data_provider 并发压力**：有界并发 + TTL 缓存 + 单 code 降级兜底缓解；超大自选首屏仍可能偏慢。
- **多 worker 部署缓存不共享**：进程内缓存按 worker 各自维护，命中率有限、各 worker 各自重算（可接受）。
- **命中率按 code 近似**：沿用 L3（M2c）口径，同一 code 各规则信号共享该 code 的历史方向命中率。
- **无自动轮询刷新**：MVP 不含盘中自动刷新，靠手动「刷新」（`refresh=true`）强制重算。

## 10. 回滚方式

纯增量（新端点 + 新 service + 新页 + 新增 schema/配置），不改 L3 既有契约。回退看板端点（`api/v1/endpoints/signals.py` 注册）与前端新页即可；`build_signals_for_code` 抽取已以单股端点等价回归测试锁定，可保留（无害）或一并回退。
