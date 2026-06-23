# 盘中/分钟级回测 设计(MVP,crypto 首发,链路 A)

- 日期:2026-06-23
- 状态:设计已获批,待写实现计划(writing-plans)
- 范围:在既有"日线回测链路 A(操作建议/PnL + perp 杠杆)"上**追加一个 interval 维度**,使日线 AI 建议的止盈/止损/方向结论可在 **crypto 分钟级真实价格路径**上前向验证。对既有日线行为**只追加、不修改**。

## 0. 目标与非目标

### 目标
验证"日线 AI 操作建议(含 perp 杠杆情景)的止盈/止损/方向结论,在**分钟级真实价格路径**上是否依然成立"。复用既有 bar-agnostic 引擎与日线候选,不新建分析流水线,不破坏 M3/M3.1 日线可信度契约。

### 非目标(YAGNI / out-of-scope)
- A 股 / 美股分钟回测(数据深度受限:A股需 Tushare pro_bar 1/5/15min 暴露且深度不确定;美股免费仅 60–730d)→ 后续阶段,不在本 MVP。
- 港股分钟(无可纳入源)→ 永久 out-of-scope。
- 链路 B(信号可信度 / `signal_stats` / M3·M3.1)的分钟化 → 不碰其日线契约。
- 分钟侧信号生成、分钟级技术指标(本 MVP 前向评估只需 OHLC 判 barrier)。
- `StockIntraday` 持久化表(分钟 bar 按需取数+缓存,不落 DB)。
- tick 级真实成交顺序(无 tick 数据;沿用引擎 same-bar 保守 stop-first)。

## 1. 锁定决策(brainstorm 收敛)

| # | 决策 | 取值 |
| --- | --- | --- |
| Q1 | 市场范围 | crypto 首发(A股/美股后续阶段;港股 out) |
| Q2 | 回测链路 | 链路 A(操作建议/PnL + perp 杠杆),不碰 M3/M3.1 日线可信度 |
| Q3 | 样本来源 | 复用日线 `AnalysisHistory` 候选(`get_candidates`),min_age 仍按天;零新分析流水线 |
| Q4 | 前向窗口语义 | 同日历窗口(≈`eval_window_days` 天),路径用分钟 bar;interval ∈ {1m,5m,15m,1h},默认 5m |
| Q5 | 结果落库 | 同表 `BacktestResult` + 新增 `bar_interval`(默认 '1d')+ 可空 `first_hit_bar_index`;日线既有语义/唯一键/消费方零改动 |
| Q6 | 数据接口 | 新姊妹入口 `get_intraday_data(code, interval, start, end)`,模板方法复用,interval 透传原生直取+分页;分钟缓存独立 TTL;非 crypto fetcher 抛"暂不支持" |
| Q7 | 成本模型 | 理想化基线 + 成本可选(`fee_bps`/`slippage_bps` 默认 0/关);引擎行为不变 |
| Q8 | 触发方式 | 手动为主 + 预留 opt-in 调度开关(默认关) |
| Q9 | Web 呈现 | 复用 `BacktestPage` + interval 筛选器(默认 '1d')+ 分钟行额外列 `first_hit_bar_index`;日线视图逐字不变 |
| Q10 | 分钟指标 | `get_intraday_data` 仅返回干净 OHLC,跳过指标(MA20/ATR 重定标约束本 MVP 移除) |
| 方案 | Chain A 集成 | interval 参数贯穿 `BacktestService`(单路径复用),分钟分支与 minute-bar-count 派生落在 service 层 |

## 2. 关键取证结论(支撑设计的代码事实)

> 以下为设计依据,实现时以实际代码为准。

- **行隔离无需改唯一键**:`BacktestResult` 唯一键 = `(analysis_history_id, eval_window_days, engine_version)`(`src/storage.py` `uix_backtest_analysis_window_version`,约 storage.py:346)。杠杆情景已用 `engine_version="v1-x{N}"` 在同一唯一键内隔离行(`src/services/backtest_service.py:62-64`)。分钟回测**复用同一手法**(见 §5)。SQLite 改唯一约束需重建表,风险高 → 不动约束。
- **去重键**:`get_candidates` 以 `(eval_window_days, engine_version)` 子查询排除已评估候选(`src/repositories/backtest_repo.py:50-79`)→ 新 tag 自然与日线 `v1` 隔离。
- **引擎 bar-agnostic**:`BacktestEngine.evaluate_single`(`src/core/backtest_engine.py:177`)取 `forward_bars: Sequence[DailyBarLike]`,以 `config.eval_window_days` **既当窗口又当 bar 切片长度**(`window_bars = forward_bars[:eval_days]`)。same-bar stop+target 冲突已内置保守 stop-first(`first_hit="ambiguous"`,模拟按止损;backtest_engine.py:191-200)。
- **入场价来自日线**:`get_start_daily`(`src/repositories/stock_repo.py:141`)取 analysis_date 当日/前一日线收盘;`get_forward_bars`(stock_repo.py:151)取日线前向 bar。分钟路径仅替换 forward 来源,入场价仍用日线收盘(=AI 建议成立时点)。
- **数据获取模板**:`get_daily_data`(`data_provider/base.py:479`)模板方法 `_fetch_raw_data → _normalize_data → _clean_data → 算指标`;crypto 子类钩子 `_request_klines(symbol, days)` 硬编码 `"interval":"1d"`(`data_provider/binance_fetcher.py:24`,基类 `data_provider/crypto_base.py:31`)。`resample.py` `resample_ohlc` 仅支持日→周/月,**不能生成分钟**。
- **无 ALTER 迁移框架**:DB=SQLite(SQLAlchemy declarative);`Base.metadata.create_all()` 幂等**但不会给既存表加列**;仅有 `CURRENT_SCHEMA_VERSION` + `schema_migrations` 标记表(storage.py:60-78)。→ 加列需显式 guarded ALTER(见 §4)。
- **配置三件套**:`src/config.py`(dataclass 字段 + `parse_env_*` 加载,backtest 段约 891-894,`crypto_backtest_leverage` 约 959)、`src/core/config_registry.py`(`_FIELD_DEFINITIONS`,category="backtest")、`.env.example`(回测段约 686-706)。三者由 `tests/test_config_registry.py` 校验一致。opt-in 范例:`SIGNAL_BACKTEST_ENABLED`(默认 False);带 interval 的范例:`AGENT_EVENT_MONITOR_ENABLED` + `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`。
  - 既有小坑(本设计不修,仅记):`CRYPTO_BACKTEST_LEVERAGE` 在 config + .env.example 有,但**未登记进 config_registry**。
- **调度**:`src/scheduler.py` `add_background_task(task, interval_seconds, run_immediately, name)`(min 30s);日常分析 cron `.github/workflows/00-daily-analysis.yml`(UTC 10:00 = 北京 18:00)。
- **CLI/API 触发**:CLI `--backtest / --backtest-code / --backtest-days / --backtest-force`(`main.py:381-404`);API `POST /api/v1/backtest/run`(`api/v1/endpoints/backtest.py:45-75`,schema `api/v1/schemas/backtest.py`);查询 `GET /api/v1/backtest/results`、`/performance`、`/performance/{code}`;过滤经 `_build_result_conditions`(backtest_repo.py:449-472),默认 engine_version=配置基版。
- **Web**:`apps/dsa-web/src/pages/BacktestPage.tsx`(filters/state 269-294,fetch 297-364,列 623-726,就地中文 label map 43-85,`PHASE_FILTER_OPTIONS` 22-28/476-485 可作 interval 选择器范本);client `apps/dsa-web/src/api/backtest.ts`;无外部 i18n 字典,label 用就地 `Record<string,string>`。

## 3. 架构与数据流

```
get_candidates(日线 AnalysisHistory, min_age 按天)               ← 不变
  └─ run_backtest(..., interval='1d')                             ← 加 interval 参数
       ├─ start_price = get_start_daily(code, analysis_date)       ← 不变(入场=建议时日线收盘)
       ├─ interval=='1d':  forward = stock_repo.get_forward_bars(...)        ← 日线路径字节级一致
       │  interval!='1d':  forward = get_intraday_data(code, interval, ...)  ← 新:provider 直取+分页+缓存
       │                   window_bar_cnt = eval_window_days × bars_per_day(interval)
       ├─ evaluate_single(forward_bars, EvaluationConfig(eval_window_days=window_bar_cnt))  ← 同一引擎,不改;切片长度=window_bar_cnt
       ├─ [可选] 成本后处理:simulated_return_pct −= 2×fee_bps/1e4 + 2×slip_bps/1e4   ← service 层,默认 0 不变
       └─ BacktestResult(eval_window_days=日历天数, engine_version=tag, bar_interval, first_hit_bar_index)  ← 落库
                          ↑ 持久化存日历天数(如 10),非 window_bar_cnt;唯一键/过滤跨日线分钟语义一致
```

## 4. 各层设计

### 4.1 数据层 `data_provider`
- 新增姊妹入口 `get_intraday_data(code, interval, start, end)`,镜像 `get_daily_data` 模板流程,但**"算指标"步骤分钟跳过**(Q10),返回纯 OHLCV(+datetime)。
- crypto 子类钩子扩为 `_request_klines(symbol, days, interval='1d')`,interval 透传;Binance/OKX 原生 1m/5m/15m/1h;按 start/end **分页**(超单请求 limit 自动翻页拼接)。
- 非 crypto fetcher 不实现 `get_intraday_data`(或基类默认抛 `NotImplementedError`/清晰"暂不支持 interval");service 层据既有市场判定(`get_market_for_stock(code)=='crypto'`,复用现有 helper,不造新 `is_crypto`)预判跳过。
- 分钟缓存:key=(code, interval, window),独立 TTL=`CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S`,与日线缓存隔离。
- `bars_per_day(interval)` = 1440 / interval_minutes(crypto 7×24);**前向窗口起点** = analysis_date 日线 bar 收盘时刻之后第一根分钟 bar;窗口长度 = `eval_window_days × bars_per_day(interval)` 根分钟 bar。

### 4.2 存储与行隔离 `src/storage.py` / `src/repositories/backtest_repo.py`
- **行隔离走 engine_version 标签**(复用杠杆 v1-x{N} 手法,零唯一键迁移):
  - `tag = base` + (`-{interval}` 若 interval≠'1d') + (`-x{lev}` 若 lev>1)。统一顺序:`base → interval → leverage`。
  - 例:`v1`(日线 1x)/ `v1-x3`(日线 3x)/ `v1-5m`(5m 1x)/ `v1-5m-x3`(5m 3x)。
  - 既有 `get_candidates` 去重 + API engine_version 过滤天然隔离日线/分钟,无需改唯一约束。
- **新增两列**(additive、可空、默认安全):
  - `bar_interval TEXT DEFAULT '1d'`:显式判别/展示/过滤列(比解析 engine_version 串更直观)。
  - `first_hit_bar_index INTEGER NULL`:分钟首次命中 bar 序号;日线行仍用既有 `first_hit_trading_days`。
- **迁移(关键)**:`DatabaseManager` init 内 `create_all()` 之后,加幂等 `_ensure_backtest_intraday_columns()`——`PRAGMA table_info(backtest_results)` 检测缺列则 `ALTER TABLE backtest_results ADD COLUMN ...`;`CURRENT_SCHEMA_VERSION` 顺带 bump 并写 `schema_migrations`。
  - 日线既有行:`bar_interval='1d'`、`first_hit_bar_index=NULL` → 语义/唯一键/消费方零改动。

### 4.3 引擎/服务层 `src/services/backtest_service.py`(引擎 `backtest_engine.py` 不改)
- `run_backtest` 增 `interval: str='1d'`。interval≠'1d' 分支:
  - 校验市场(`get_market_for_stock(code)=='crypto'`,复用现有 helper);非 crypto 跳过 + 计数(类比现有 `skipped_non_perp`),不报错中断。
  - forward 改取 `get_intraday_data`;`EvaluationConfig.eval_window_days` 传入**派生的 `window_bar_cnt`**(=`eval_window_days_days × bars_per_day(interval)`;复用引擎对该字段"既窗口又切片长度"的现有语义,仅用于切片)。
  - `engine_version` 用 §4.2 的 tag。
- **`eval_window_days` 持久化语义(消歧)**:`BacktestResult.eval_window_days` 始终存**日历天数**(run_backtest 入参,如 10),**不存** `window_bar_cnt`。引擎结果 dict 返回的 `eval_window_days`(=window_bar_cnt)在 service 落库时被覆盖为日历天数。日线行二者相等,无变化;分钟行据此与日线共用同一"按天"过滤口径(API/Web 传 eval_window_days=10 + interval=5m 即可命中分钟行),唯一键 `(analysis_id, 10, "v1-5m")` 仍与日线 `(analysis_id, 10, "v1")` 经 engine_version 隔离。
- `evaluate_single` 返回的 `first_hit_days`(=首次命中 bar 序号):interval≠'1d' 时映射进 `first_hit_bar_index` 列(日线仍进 `first_hit_trading_days`);`first_hit_date` 存命中 bar 日期(时分留待迭代)。
- **成本可选**(默认 0/关):`fee_bps`/`slippage_bps`>0 时,在 service 层对引擎返回的 `simulated_return_pct` 做一进一出后处理 `−= (2×fee_bps + 2×slip_bps)/1e4 ×100`;默认 0 时**不进入后处理分支**,引擎与既有路径字节级不变。

### 4.4 API 层 `api/v1/{endpoints,schemas}/backtest.py`
- `BacktestRunRequest` 加 `interval: Optional[str]`(允许集 {1d,1m,5m,15m,1h};None→'1d');run 透传 service。
- results / performance / performance/{code} 端点加 `interval` query(默认 '1d'),经 `_build_result_conditions` 落 `BacktestResult.bar_interval == interval` 过滤。**默认 '1d' → 既有日线响应逐字不变**。
- `BacktestResultItem` 响应 schema 追加 `bar_interval`、`first_hit_bar_index`(追加字段,旧客户端忽略即兼容)。

### 4.5 Web 层 `apps/dsa-web`
- `BacktestPage.tsx`:镜像 `PHASE_FILTER_OPTIONS` 加 `INTERVAL_OPTIONS`(1d 默认 / 1m / 5m / 15m / 1h),新增 `intervalFilter` state + 下拉,thread 进 `getResults / getOverallPerformance / getStockPerformance`;label 用就地 `Record<string,string>` 中文(日线 / 1分 / 5分 / 15分 / 1时)。
- 表格:分钟行多一列 `first_hit_bar_index`(中文"首次命中(bar)")。**默认 '1d' → 视图与现状一致**。
- `api/backtest.ts` + types:`getResults/getOverallPerformance/getStockPerformance` params 加 `interval?: string`;`BacktestResultItem` 类型加 `barInterval`、`firstHitBarIndex`。

### 4.6 配置(三件套同步 + opt-in 调度)
新增项(均需 `src/config.py` 字段+`parse_env_*` 加载、`src/core/config_registry.py` 登记 category="backtest"、`.env.example` 注释项;受 `tests/test_config_registry.py` 强校验):

| ENV | 默认 | 说明 |
| --- | --- | --- |
| `CRYPTO_INTRADAY_BACKTEST_INTERVAL` | `5m` | 默认 bar 粒度(允许集 1m/5m/15m/1h) |
| `CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S` | `900` | 分钟取数缓存 TTL(秒) |
| `CRYPTO_INTRADAY_BACKTEST_FEE_BPS` | `0` | 单边手续费 bps,默认关 |
| `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` | `0` | 单边滑点 bps,默认关 |
| `INTRADAY_BACKTEST_ENABLED` | `false` | opt-in 调度开关,默认关(手动为主) |
| `INTRADAY_BACKTEST_SCHEDULE_MINUTES` | `60` | 调度周期(分);命名用 schedule 避免与 bar interval 混淆 |

- 调度:仅 `INTRADAY_BACKTEST_ENABLED=true` 时,经 `scheduler.add_background_task` 挂载(周期=`INTRADAY_BACKTEST_SCHEDULE_MINUTES×60`,min 30s);默认不挂。
- CLI:顺带加 `--backtest-interval`(主路径,手动触发分钟回测)。

## 5. 错误处理与降级
- 单标的分钟取数失败/深度不足 → 该样本记 `insufficient_data`/`error`(复用既有状态),不拖垮批次(类比日线 `_try_fill_daily_data` 兜底)。
- 非 crypto 传 interval≠'1d' → 跳过计数 + 清晰日志,不中断。
- 成本、调度均默认关 → "不配置即可跑出理想化可比基线",配置后增强能力。

## 6. 测试策略(离线确定性优先)
- 引擎复用既有 bar-agnostic 测试(分钟 bar = 同一 `DailyBarLike` Protocol,无需新引擎测试)。
- 新增单测:
  - `get_intraday_data`:分页拼接 / 缓存命中与 TTL / 跳指标(mock HTTP,确定性)。
  - engine_version tag 组合(`v1-5m` / `v1-5m-x3`)与 `get_candidates` 去重隔离日线/分钟。
  - minute-bar-count 派生(`eval_window_days × bars_per_day(interval)`,各 interval)。
  - 成本后处理(0=字节不变;>0=按 2×(fee+slip) 扣减)。
  - 迁移 `_ensure_backtest_intraday_columns` 幂等(重复调用不报错;旧库补列;新库 create_all 即有)。
  - API interval 过滤默认 '1d' 回归(分钟行不污染日线响应)。
  - config 三件套一致性(`tests/test_config_registry.py` 自动覆盖新键)。
- Web:`npm run lint && npm run build` + interval 选择器/默认 '1d' 渲染单测。
- 网络类(真打 Binance/OKX 分钟端点)归 `-m network`,非阻断观测项。

## 7. 风险与回滚
- **风险**:
  - 分钟取数体量(10d×5m≈2880 bar/样本 × limit)→ 分页 + 独立缓存 + limit 控制;必要时文档提示降低 limit / 缩窗。
  - crypto 日界对齐(分钟窗口起点 = 日线收盘时刻之后)需在实现中明确并测试。
  - `engine_version` tag 串顺序必须统一(base→interval→leverage),否则读写不一致。
- **回滚**:特性纯追加(新列可空、新入口、新配置默认关、interval 默认 '1d')→ 不启用即等于现状;两列可保留为惰性列;Web/API 默认路径不变。

## 8. 交付验证矩阵(实现后)
- 后端:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`(含新增用例);`python -m py_compile` 改动文件。
- Web:`cd apps/dsa-web && npm ci && npm run lint && npm run build`。
- 文档:同步 `docs/CHANGELOG.md`(`[Unreleased]` 扁平格式 `- [新功能] ...`)、`.env.example`、必要时新增专题 `docs/intraday-backtest.md`。
- 用户可见:CLI `--backtest-interval`、API `interval` 参数、Web interval 选择器 → 均属用户可见,需文档与 CHANGELOG 覆盖。

---

附:本 spec 为 writing-plans 的输入。实现阶段以实际代码为准,若发现取证事实与代码漂移,优先信任代码并顺手订正本 spec。
