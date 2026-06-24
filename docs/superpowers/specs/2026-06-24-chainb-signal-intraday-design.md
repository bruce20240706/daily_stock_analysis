# 链路B 信号可信度分钟化 设计 Spec

> 日期：2026-06-24
> 阶段：盘中/分钟级回测 epic follow-up —— 把信号可信度回测(链路B / M3)从日线扩到分钟。
> 前置：链路A 盘中回测已落地 crypto(66cf4166)+ A股(41f6e450)+ 美股(c019034a),均在 main。

## 1. 目标与范围

把**信号三重门可信度回测(链路B)**从日线扩展到分钟粒度(`5m/15m/1h`),按方案 A:**在分钟 bar 上重算 VPS 信号 + 分钟前向三重门评估**,产出按 `(signal_type × market × interval × horizon)` 隔离的 `signal_stats`,并提供最小 interval-aware 读出。

- **In scope**:`SignalBacktestService` 分钟取数与评估;`_to_epoch_ms_shanghai` 分钟分辨率改造;`signal_stats` 按 interval 落库(零迁移);读路径 interval-aware(默认 1d 不变);`--signal-backtest-interval` CLI。
- **Out of scope**:盘中信号 marker 的看板 UI 呈现(本期不建,仅最小可查读出);VPS 分钟专属调参(沿用 `VPSConfig.for_market`,best-effort);链路A 与日线链路B 行为变更;`end_date 右边界截断`/`intraday_data capability 显式声明`/`A股印花税`(独立 follow-up,各自 spec)。
- **硬约束**:**链路A 与日线链路B 行为字节级不变**;`interval` 默认 `1d` 即等于现状;**零新增配置项**(仅新增 CLI flag);`interval` 词表统一复用 `src/core/intraday_backtest.validate_interval`(`{1d,1m,5m,15m,1h}`)。

## 2. 决策记录(brainstorm 定稿)

- **D1 语义**:**方案 A —— 分钟信号 + 分钟评估**。在分钟 bar 上 `compute_volume_price_signals` 重算信号,三重门前向 horizon 走分钟 bar。与 `signal_stats` 预留的 `interval` 列设计一致;样本更多、CI 更紧。
- **D2 市场/历史**:**复用链路A 路由,全市场 × 各自最大窗**。自选池逐码 `market_of`→`get_intraday_data(interval)`,每市场取其源可得最大近窗;无分钟数据的码无样本(优雅跳过)。
- **D3 消费边界**:**回测 + 落库 + 最小可查读出**(不建盘中信号看板 UI)。
- **D4 读出形式**:**现有读路径 interval-aware + 可选 `?interval=` 透传**(默认 1d 不变,零新端点)。

## 3. 架构

链路B 现状(全部已存在,见 grounding):
- 入口 `main.py --signal-backtest` → `SignalBacktestService().run()`。
- `run()`:读自选池(STOCK_LIST)→ 逐码 `StockService.get_history_data(period="daily", days=365)` → `evaluate_signal_outcomes`/`evaluate_baseline_outcomes`(`src/services/signal_backtest.py` 的 `_eval` 逐 bar 因果走查 + `compute_volume_price_signals` 重算 + `classify_triple_barrier` 三重门)→ `aggregate_signal_stats`(Wilson CI + 基准超额)→ `SignalStatsRepository.save_batch` 落 `signal_stats`。
- 读出:`signal_hit_rate.resolve_marker_hit_fields(signal_type, code)` → `SignalStatsRepository.get(...)`(interval 默认 '1d')→ 信号看板 marker 的 hit 字段。

分钟化只在四处接入 interval:取数分叉、触发对齐修复、interval 透传、读出参数化。**链路B 是 bar-interval 无关设计**(其 docstring 即写明),核心评估逻辑不改。

## 4. 详细设计

### 4.1 取数(`SignalBacktestService.run`,按 interval 分叉)

`run()` 增 `interval: str = "1d"`(并 `validate_interval(interval)`):

```python
def run(self, *, codes=None, horizon=None, interval="1d"):
    validate_interval(interval)
    ...
    for code in codes:
        market = get_market_for_stock(code)
        if market is None: skipped += 1; continue
        df = self._load_bars(code, interval)        # 见下
        if df is None or len(df) < _MIN_BARS: skipped += 1; continue
        cfg_m = VPSConfig.for_market(market)
        all_sig.extend(evaluate_signal_outcomes(df, market=market, horizon=hz, config=cfg_m))
        all_base.extend(evaluate_baseline_outcomes(df, market=market, horizon=hz, config=cfg_m))
        processed += 1
    stats = aggregate_signal_stats(all_sig, all_base, horizon=hz, interval=interval)   # interval 透传
    # 落库同现状,SignalStatRow(interval=s.interval ...)
```

`_load_bars(code, interval)`:
- `interval == "1d"`:现路径 `StockService.get_history_data(code, period="daily", days=_FETCH_DAYS)` → DataFrame(含 `date` 列),**逐字不变**。
- 分钟:`df, _src = DataFetcherManager().get_intraday_data(code, interval, days=_minute_fetch_days(market, interval))`;返回列 `datetime/open/high/low/close/volume/...` → **`df = df.rename(columns={"datetime": "date"})`**(保留完整分钟时间戳到 `date` 列,供 `_eval`/VPS 复用)。取数失败/空 → 该码计入 errors/skipped(不拖垮整批,沿用现有 try)。
- `_minute_fetch_days(market, interval)`:返回各源可得近窗上界(crypto 大、美股 5m/15m≈60、1h≈730、A股 Tushare 深)。不传 start_date → `get_intraday_data` 的"近 N 天"语义,各源返回其可得范围;实现时以 `get_intraday_data` 无 start_date 路径实际行为为准,取值偏大即可(源自身封顶)。

### 4.2 信号触发对齐修复(`_to_epoch_ms_shanghai`,核心)

现状(`src/services/volume_price_signals.py`):
```python
if isinstance(date_value, str):
    dt = datetime.strptime(date_value[:10], "%Y-%m-%d")   # 截断到日期
...
if dt.tzinfo is None:
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_SHANGHAI)  # 置零午夜
```
→ 同日所有分钟 bar 坍缩到当日午夜 → `marker.timestamp == _last_ts(window)` 会误配同日任意 bar,分钟触发判定失效。

**改为分钟分辨率感知(向后兼容日线)**:
- 字符串:含时间(`len > 10` 或含 `':'`)→ 解析完整 `"%Y-%m-%d %H:%M:%S"`(失败回退 `%Y-%m-%d`);纯日期 → 仍按日期。
- Timestamp/datetime:保留时分秒。
- 仅**纯日期来源**(无时间分量)才 `replace(hour=0,...)` 置零午夜;有时间分量则保留,补 `_SHANGHAI` 时区。

日线行为**逐字不变**(日期串本就 10 字符无时间 → 仍午夜)。分钟得真实时分 → 逐 bar 触发正确。绝对时区对"相等 join 键"无影响(`marker.timestamp` 与 `_last_ts` 两侧同函数同列值)。**加测试锁定日线不变 + 分钟保留时分**。

### 4.3 `_eval` / evaluate_* / aggregate(interval 贯穿)

`_eval`/`evaluate_signal_outcomes`/`evaluate_baseline_outcomes` 本就 bar-interval 无关(逐 bar 走查、`derive_price_levels`、前瞻 `horizon` 分类、用 `df["date"]`)。配合 §4.1 的 `date` 列(分钟时间戳)+ §4.2 修复,即在分钟 df 上正确运行,**无需改其评估逻辑**。`min_history=40`(40 分钟 bar 预热,合理)。`horizon` 取 `SIGNAL_BACKTEST_HORIZON_BARS`(bars 相对;5m×10=50min,文档化墙钟含义)。`aggregate_signal_stats(interval=...)` 已支持(line 308)→ `run()` 透传。

### 4.4 CLI(零新增配置)

`main.py` 增:
```python
parser.add_argument('--signal-backtest-interval', type=str, default='1d',
                    choices=["1d","1m","5m","15m","1h"],
                    help="信号回测 bar 粒度(默认 1d=日线;分钟在分钟 bar 上重算信号+评估)")
```
入口透传:`SignalBacktestService().run(interval=getattr(args,'signal_backtest_interval','1d'))`。一次跑一个 interval;多 interval 多次跑(各自落 `(interval,horizon)` 行)。`horizon` 沿用 `SIGNAL_BACKTEST_HORIZON_BARS`。**不新增配置项**。

### 4.5 落库(零迁移)

`signal_stats` 已有 `interval` 列 + 唯一键 `uix_signal_stats_type_market_interval_horizon`;`run()` 按 interval 落库,`save_batch(replace_existing=True)` 按该键作用域 → 分钟行与日线行天然隔离,重复跑同 interval 覆盖同 interval 行。**无需 schema 迁移**。

### 4.6 读出 interval-aware(D4)

- `resolve_marker_hit_fields(signal_type, code, *, interval="1d")` 增可选 `interval`,透传 `SignalStatsRepository().get(signal_type, market, interval=interval, horizon=horizon)`(仓库 `get` 已支持 interval)。默认 `'1d'` → **现状不变**。
- 信号看板 `build_signals_for_code`/`build_board`(`signal_board_service.py`)与 signals API(`api/v1/endpoints/signals.py`)加可选 `interval` 参数(查询 `?interval=`),默认 `'1d'` 透传至 `resolve_marker_hit_fields`。分钟可信度即可按 interval 查到。**不新增端点、不建盘中信号 marker UI**。

### 4.7 零配置 / 兼容

不新增配置项;`--signal-backtest-interval` 默认 `1d`、读路径 `interval` 默认 `1d` → 不传即等于现状。链路A、日线链路B、信号看板默认呈现**字节级不变**。

## 5. 市场/语义/限制

- **市场**:全市场经 `market_of`/`get_market_for_stock` + `get_intraday_data`;无分钟数据的码无样本(graceful)。
- **历史深度**:分钟回测覆盖各源可得近窗(crypto 深、美股 5m/15m≈60d/1h≈730d、A股 Tushare 深);窗口短的市场样本更少 → CI 更宽,如常处理。
- **horizon 语义**:bars 相对(5m×10=50min);文档写明墙钟含义,用户可调 `SIGNAL_BACKTEST_HORIZON_BARS`。
- **VPS 调参**:分钟沿用 `VPSConfig.for_market(market)`(日线调参),分钟专属调参 best-effort 留后续。
- **批量可靠性**:分钟取数走 yfinance/akshare 等可能限频(链路A 已有 @retry/降级);单码失败计入 errors,不拖垮整批。

## 6. 测试

- `_to_epoch_ms_shanghai`:纯日期 → 午夜(日线不变);带时间字符串/Timestamp → 保留时分秒(分钟分辨率)。
- `_eval` 分钟触发:合成同日多根分钟 bar,断言信号按**各自 bar** 触发(非全日坍缩);三重门分类在分钟前向窗正确。
- `SignalBacktestService.run(interval="5m")`:mock `get_intraday_data` 返回分钟 df + mock repo,断言走分钟取数、`aggregate_signal_stats` 收到 `interval="5m"`、落库行 `interval=="5m"`;`run(interval="1d")` 默认仍走 `StockService.get_history_data`(日线路径不变)。
- `resolve_marker_hit_fields(interval="5m")`:读 `signal_stats` 的 5m 桶;默认 `interval="1d"` 行为不变。
- signals 看板/API `?interval=` 透传(默认 1d 不变)。
- 全量 `pytest -m "not network"` 零回归(链路A/日线链路B/看板)。
- 可选 `-m network`:真拉一支 crypto 跑 `run(interval="5m")` 产出非空 stats(连接异常带重试后 skip)。

## 7. 验证矩阵

- 后端:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`(venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`)。
- 影响面:`src/services/signal_backtest_service.py`、`src/services/signal_backtest.py`(仅取数/透传,评估逻辑不改)、`src/services/volume_price_signals.py`(`_to_epoch_ms_shanghai` 分钟感知)、`src/services/signal_hit_rate.py`、`src/services/signal_board_service.py`、`api/v1/endpoints/signals.py`、`main.py`、`docs/`、`tests/`。无 schema 迁移;无 Web/Desktop 改动(API 仅加可选 query 参数,向后兼容)。

## 8. 自审清单(实现期对齐)

- `_to_epoch_ms_shanghai` 是 VPS 引擎共享 util:改后必须有测试确认**日线 marker 时间戳逐字不变**(日期串仍午夜),仅分钟新增时分;并跑全量确认 VPS/看板零回归。
- `get_intraday_data` 无 start_date 的"近 N 天"语义按各 fetcher 实际为准(crypto days 驱动;yfinance start=None 取最大近窗;A股 Tushare/akshare 近窗)——`_minute_fetch_days` 取值偏大、由源封顶即可。
- 分钟 df `datetime→date` rename 后,`compute_volume_price_signals`/`_normalize` 对 `date` 列与 OHLCV 的列契约满足(实现时核对 `_normalize`)。
- signals API 加 `interval` query 须保持默认 1d、旧调用零感知;若 OpenAPI schema 有契约测试,同步。
- horizon bars 相对的墙钟含义在 docs 写明(5m×10=50min)。
