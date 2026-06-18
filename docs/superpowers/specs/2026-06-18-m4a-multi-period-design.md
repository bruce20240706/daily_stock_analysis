# M4-A 周/月线多周期 + 多周期共振 设计

- 日期：2026-06-18
- 状态：设计已评审通过，待写实现计划（writing-plans）
- 基线分支：`feat/m4a-multi-period` ← `feat/m3-signal-credibility@3767d61a`（堆叠在 M3 之上）
- 关联：`docs/superpowers/specs/2026-06-17-m3-signal-credibility-design.md`（M3 信号可信度，本特性的前置依赖）

---

## 1. 背景与目标

M3 给日线 volume-price 信号建立了可信度框架（三重门回测、Wilson 区间、按 signal_type×market 胜率、verified 门）。M3 的非目标里把「周/月线多周期」明确推迟到 M4（见 M3 spec §2，`stock_service.py` 当前对非日线直接抛 `ValueError`）。

M4-A 的目标是两件事，且两件都建立在 M3 的日线可信度之上：

1. **解锁周/月线为一等周期**：图表可在 日/周/月 之间切换，周/月线由本地聚合（抓日线 → 重采样）得到，市场无关、确定性。
2. **多周期共振标记（轻量）**：给日线信号附一个共振维度——高周期（周线、月线）趋势方向与日线信号方向同向时标「共振确认」。共振复用日线信号已有的 M3 可信度，高周期只取**趋势方向**，不做自己的回测/胜率。

非目标在 §2 明确。本特性不触碰 signal_stats 回测管线。

## 2. 非目标（scope boundaries）

以下明确**不做**，留作后续增量：

- 周/月线的三重门回测、周/月线自己的胜率/可信度（signal_stats 管线完全不动）。
- 周/月 bar 上的逐根历史共振、周/月线信号标记（共振只在最新 bar 计算，信号 overlay 只在日视图渲染）。
- 资金面（换手率/主力资金流/龙虎榜/北向/融资融券/筹码）——属于 M4-B。
- 分钟级/实时流多周期、净值曲线、Strategy Tester UI、K 线回放。
- 交易所官方周/月 K 线对齐（本地按日历分组，见 §7 已知近似）。

## 3. 关键决策（已锁定）

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | 代码基线 | 堆叠在 M3 分支之上（M4-A 在 M3 入 main 前是 stacked PR） |
| D2 | 多周期深度 | 周/月线一等周期 + 多周期共振标记 |
| D3 | 数据获取 | 本地聚合：抓日线 → 重采样到周/月（市场无关、确定性、复用日线 fallback 与防未来函数） |
| D4 | 回测范围 | 图表 + 共振；**不做**周/月线回测；不动 signal_stats |
| D5 | 共振趋势定义 | 均线排列为主 + 收盘确认（参数自由）；共振徽标三档 `无 / 周线共振 / 周月双共振` |
| D6 | 共振开关 | 无开关，始终计算（零新增 `.env`/`config_registry`/settingsHelp） |

## 4. 架构与模块边界

### 4.1 新增（2 个纯逻辑单元，可独立测试）

**`data_provider/resample.py`** — 市场无关、确定性的纯函数：

- `resample_ohlc(df_daily, period) -> df_period`
  - 输入：标准化日线帧（`STANDARD_COLUMNS = ['date','open','high','low','close','volume','amount','pct_chg']`）。
  - `period ∈ {'weekly','monthly'}`。
  - 聚合：`open=first / high=max / low=min / close=last / volume=sum / amount=sum`。
  - `pct_chg` 重算：本期 close vs 上期 close；首根置 NaN。
  - 分组：pandas `resample('W')` / `resample('M')`，每根 bar 的 `date` 取该周期内**最后一个交易日**的日期（非日历边界）。
  - 复用 `base.py` 现有的 MA5/MA10/MA20、volume_ratio 计算口径（抽成共享 helper，避免两份均线公式漂移）。
  - partial 当期：当期未走完时输出「至今」partial bar（只含已过交易日）。
- 不依赖任何 fetcher、网络、全局状态；输入 DataFrame、输出 DataFrame。

**`src/services/multi_period_resonance.py`** — 纯业务逻辑：

- `period_trend(df_period) -> Trend`，`Trend ∈ {'bullish','bearish','neutral'}`，取最后一根 bar：
  - `bullish`：`MA5 > MA10 > MA20` **且** `close >= MA20`（排列看多 + 收盘确认）。
  - `bearish`：`MA5 < MA10 < MA20` **且** `close <= MA20`。
  - 其余（均线纠缠、收盘未确认、MA 为 NaN/历史不足）：`neutral`。
  - 无阈值参数。
- `resonance_level(signal_direction, weekly_trend, monthly_trend) -> Level`，`Level ∈ {'none','weekly','weekly_monthly'}`：
  - `signal_direction ∈ {'buy','sell'}`（中性/观望/hold 信号 → 无共振概念 → `none`）。
  - `agree(trend, dir)`：`dir=='buy' and trend=='bullish'` 或 `dir=='sell' and trend=='bearish'`。
  - **周线为门控**：`not agree(weekly_trend, dir)` → `none`（周线不同向则无共振，即便月线同向；周线更贴近日线）。
  - 周线同向且 `agree(monthly_trend, dir)` → `weekly_monthly`。
  - 周线同向、月线不同向 → `weekly`。

### 4.2 改动现有（最小面）

- `src/services/stock_service.py:get_history_data`：解除 `period != "daily"` 的 `ValueError`；weekly/monthly 分支按周期算所需日线深度（§5）→ 走**现有** `DataFetcherManager.get_daily_data` 抓取 → 调 `resample_ohlc` → 返回与 daily **同结构**的 dict（仅 `period` 字段不同）。daily 路径完全不变。
- `src/services/signal_board_service.py` + 钻取来源：给每个信号 entry 附 `resonance` 字段（`none/weekly/weekly_monthly`）。复用对该 code 已抓的日线（加深一次抓取，§5）做 resample + `period_trend` + `resonance_level`。
- API 层：`GET /stocks/{code}/history` 已接受 `period`（pattern `^(daily|weekly|monthly)$`，当前下游拒绝），解锁后端到端可用；放宽 `days` 上限（§9）。entry schema 追加 `resonance` 字段（向后兼容追加）。
- 前端：`KLineChartPanel` 加周期切换；`SignalBoard`/`SignalDrilldownPanel` 加共振徽标（§8）。

### 4.3 关键复用点（不重造）

抓取链（`DataFetcherManager` + priority + fallback + CircuitBreaker）、MA/volume_ratio 计算口径、daily 返回结构、signal_stats 管线**完全不动**。

## 5. 数据流

### 5.1 图表请求

`GET /stocks/{code}/history?period=weekly&days=…`

1. stock_service 派生日线抓取深度：`fetch_days = min(display_days + WARMUP_DAYS[period], MAX_FETCH_DAYS)`。
   - `WARMUP_DAYS = {daily: 0, weekly: 200, monthly: 800}`（保证高周期 MA20 暖机；常量可在实现时微调）。
   - `MAX_FETCH_DAYS ≈ 3650`（封顶，防病态抓取）。
2. 走现有 `DataFetcherManager.get_daily_data` 抓日线（命中现有 fallback/CircuitBreaker）。
3. `resample_ohlc(df_daily, period)` → 高周期帧；重算 MA。
4. 裁掉前部 MA 暖机 NaN 段，返回展示窗口内（约 `display_days` 对应根数）的高周期 bar。
5. 返回结构与 daily 一致：`{stock_code, stock_name, period, data:[bar...]}`，bar 含 OHLCV/amount/pct_chg/MA5/MA10/MA20。

### 5.2 共振流

1. board service 对每个 code 抓**一份足够深的日线**（够月线 MA20 暖机，深度同 §5.1 monthly 口径），日线近段用于信号、整段 `resample` 用于周/月趋势。**一次抓取两用，不重复抓**（实现需断言抓取次数）。
2. `period_trend(weekly)`、`period_trend(monthly)` → `resonance_level(entry.signal_direction, weekly_trend, monthly_trend)` → 附到 entry。
3. **单股共振计算失败不拖垮看板**：该股 `resonance` 降级为 `none`，其余股正常（符合稳定性护栏：单股/单源失败不拖垮主流程）。

## 6. 共振判定规则（汇总，见 §4.1 精确定义）

- 高周期趋势 = 均线排列（多头/空头）+ 收盘 vs MA20 确认 → `{bullish/bearish/neutral}`，无阈值参数。
- 共振 = 日线信号方向 × 高周期同向；周线门控，月线加强 → `{none/weekly/weekly_monthly}`。
- 共振只在最新 bar 计算（不做历史 as-of），不存在跨期未来泄漏。

## 7. 防未来函数与边界处理

- **partial 当期 bar**：周/月当期未走完时是「至今」partial bar，参与 MA 与趋势判定——这是「当前趋势」的正确语义，图表按 forming bar 显示。M4-A 共振只在最新 bar 计算，无回测，故无跨期泄漏。
- **日历分组近似**：`resample('W'/'M')` 按日历分组，跨市场（A 股/港股/美股/crypto）统一、确定性。与交易所官方周/月 K 线（按交易日历）可能有边界差异；对趋势方向判定足够。**列为已知语义近似（§13），写入用户文档**。
- **历史不足**：日线不够算高周期 MA20 → 暖机段 MA 为 NaN 裁掉，图表渲染可用 bar；趋势无法判定 → `neutral`，共振降级 `none`。不抛错。
- **抓取失败**：高周期复用现有 fallback/CircuitBreaker；全链失败时该接口按现有错误语义返回（与 daily 一致），不新增静默降级路径。

## 8. 前端范围（YAGNI）

- `KLineChartPanel`：加 **日/周/月** 分段切换控件。切换时按周期重取 history（带 `period`）、重渲蜡烛 + MA。沿用 M3 已做的懒载 + 请求 id 取消守卫，避免切换竞态覆盖/卸载后写入。
  - **信号 overlay（signalGlyph）只在「日」视图渲染**：信号是日线概念，定位在周/月 bar 上无意义且易误读。切到周/月隐藏信号标记。
- 共振徽标：`SignalBoard` 行 + `SignalDrilldownPanel` 加 3 档徽标（`无 / 周线共振 / 周月双共振`），带 tooltip 说明含义。**视觉/配色与 M3 可信度徽标区分**，避免混淆两个维度。
- i18n：徽标文案 + tooltip 走 locale；共振始终计算、无开关。

## 9. 配置与兼容性

- **零新增配置**：共振无开关；不新增 `.env`/`config_registry`/settingsHelp 项（不配置也可运行，避免配置膨胀）。
- **API `days` 上限放宽**：当前 `GET /history` 的 `days` 为 `ge=1, le=365`。月线下 365 天仅约 12 根，体验弱。放宽上限（如 `le=1825`，**纯加宽、向后兼容**，旧调用 ≤365 仍合法），前端按周期送不同 days（日 120 / 周 ~365 / 月 ~1825）。内部 MA 暖机抓取独立于该上限。
- **Schema 追加**：entry/BoardEntry 追加 `resonance` 字段（追加式，旧客户端忽略即可，不破坏现有契约）；history 返回结构不变（period 字段已存在）。
- **市场范围**：本地聚合市场无关 → 所有引擎支持市场（A 股/港股/美股/crypto），无需按市场门控。

## 10. 测试矩阵

后端单测（pytest，离线确定性，固定 fixture 不打网）：

- `data_provider/resample.py`：OHLCV 聚合正确性（open=首/high=高/low=低/close=尾/volume,amount=和）、pct_chg 重算、周/月边界切分、partial 当期 bar、MA 与 base.py 口径一致、历史不足暖机 NaN 裁剪。
- `src/services/multi_period_resonance.py`：`period_trend` 多头/空头/纠缠/收盘未确认/NaN 各 fixture；`resonance_level` 买×周看多→`weekly`、买×周月皆看多→`weekly_monthly`、买×周中性/反向→`none`（周线门控）、卖向对称、hold/中性方向→`none`。
- `src/services/stock_service.py`：`get_history_data(period='weekly'/'monthly')` 返回 resampled 同结构；daily 路径回归不变；抓取深度派生（warmup）；历史不足优雅降级（不抛错）。
- API `tests/`：`GET /history?period=weekly` 由 **422 → 200**，schema 不变 + period 回显；`days` 放宽上限边界值。
- board/drilldown service：entry 带 `resonance`；**单股共振失败不破坏看板**（mock 一股抛错 → 该股 `none`、其余正常）；一次抓取两用、不重复抓（spy 断言抓取次数）。

前端单测（vitest）：

- `KLineChartPanel`：切到周/月 → 按 period 重取（断言请求参数）；信号 glyph **仅日视图**渲染；切换竞态用现有请求 id 守卫不串。
- 共振徽标：3 档各自渲染 + tooltip 文案；与可信度徽标并存不冲突。

## 11. 验证门禁（交付前亲自跑）

- 后端：`./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`）。
- 前端：`cd apps/dsa-web && npm ci && npm run lint && npm run build` + vitest（vitest 全绿 ≠ web-gate，必须真跑 `npm run lint`=eslint .；在无空格 worktree `/root/dsa-m4a` 下执行）。
- 文档：CHANGELOG `[Unreleased]` 扁平格式追加 `新功能/改进` 条目；新增/更新专题文档（多周期 + 共振语义 + 日历分组近似 + 月线 bar 数 v1 限制）。

## 12. 风险与回滚

- 风险：
  - 月线展示根数偏少（即便放宽 days），体验弱——列为 v1 限制。
  - 日历分组与交易所官方周/月 bar 有边界差异——文档说明。
  - board 加深日线抓取 → 单股抓取量上升；用一次抓取两用 + 失败降级控制开销与稳定性。
  - 堆叠在 M3 之上：M3 若在 review 中改动需 rebase；M4-A 入 main 须等 M3 先入。
- 回滚：M4-A 改动集中在新增模块 + `stock_service` period 分支 + 前端切换/徽标，回滚 = 还原 period 分支抛错 + 撤前端组件 + 撤 schema 追加字段；signal_stats/daily 主链未动，回滚面小。

## 13. 已知局限（v1）

- 月线在 `days` 上限内展示根数有限（约数十根），更深历史留后续增量。
- 周/月 bar 按日历分组，非交易所官方 K 线，存在边界近似。
- 共振只在最新 bar 计算，不提供历史逐根共振。
- 周/月线无自己的回测/胜率，共振仅复用日线 M3 可信度 + 高周期趋势方向。

---

## 实现切片（供 writing-plans 参考）

1. `data_provider/resample.py` + 共享 MA helper 抽取 + 单测。
2. `src/services/multi_period_resonance.py` + 单测。
3. `stock_service.get_history_data` 解锁 weekly/monthly + 抓取深度 + 单测。
4. API `days` 上限放宽 + history 端到端测试（422→200）。
5. board/drilldown service 附 `resonance` + schema 追加 + 单测（失败降级、抓取复用）。
6. 前端 `KLineChartPanel` 周期切换（信号仅日视图）+ vitest。
7. 前端共振徽标（board + drilldown）+ i18n + vitest。
8. 文档（CHANGELOG + 专题）+ 全量门禁。
