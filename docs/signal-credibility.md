# 信号可信度层（M3-A）

> 对应实现：`src/services/signal_backtest.py`（纯函数评估器）、`src/services/signal_backtest_service.py`（批作业）、`src/repositories/signal_stats_repo.py`（读写）、`src/storage.py` → `SignalStatRow`、`src/services/signal_hit_rate.py` → `resolve_marker_hit_fields`（改源）

---

## 1. 设计目标

把量价信号从"看图标注"升级为"有回测背书"的可信可操作信号（M3，零新数据源）：

- 对**自选池**所有股票做规则级三重门回测（目标/止损/到期）。
- 按「**信号类型 × 市场**」聚合胜率，附 Wilson 置信区间（CI）与"相对全体 bar 入场基准"的超额。
- `verified` 口径升级为"样本足 AND 置信下界 > 基准"。
- 命中率回填（`/signals` 端点）改读预计算的 `signal_stats` 表，而非 per-code BacktestResult 记录。
- 前端三处（钻取面板、工作台信号 tab、信号看板行）统一展示可信度字段。

---

## 2. 三重门回测

### 2.1 价位推导（`derive_price_levels`）

信号触发的入场价、止损价、目标价由 `derive_price_levels(window)` 从 OHLCV 窗口派生：

- 入场（`entry`）：MA20 / 近 20 日低 / ATR 派生
- 止损（`stop`）：同一函数输出
- 目标（`target`）：同一函数输出

两者均可空（`None`）；当 `stop` 或 `target` 为 `None` 时跳过该 bar 的评估，不产出 `SignalOutcome`。

### 2.2 三重门分类（`classify_triple_barrier`）

对信号触发 bar 之后的 `horizon` 根前瞻 bar 逐根判断（长仓视角）：

| 结果 | 触发条件 |
|------|---------|
| `win` | 前瞻窗口内 `high >= target` 先于止损触发 |
| `loss` | 前瞻窗口内 `low <= stop` 先于目标触发；或同 bar 同时触及两门（保守判 loss） |
| `expired` | 前瞻窗口到期，未触及任何门 |

`expired` 结果保留在原始列表中，但**不计入胜率分母**（`sample = win + loss`）。

### 2.3 因果约束

评估 bar `t` 时只使用 `df.iloc[:t+1]`（含当 bar，不含未来数据）。信号触发判定：marker 的 `timestamp == _last_ts(window)`，即"新触发于当前 bar"，与引擎出点口径一致。

---

## 3. 全体 bar 入场基准

`evaluate_baseline_outcomes(df, market=..., horizon=...)` 对每个有效入场 bar（`stop` 和 `target` 均可用时）产出一条 `__baseline__` 记录，用于模拟"随机时刻入场"的胜率。

`signal_type` 固定为 `BASELINE_SIGNAL_TYPE`（字面量 `'__baseline__'`），不写入 `signal_stats` 表（仅作统计参照）。

---

## 4. 统计聚合（`aggregate_signal_stats`）

`aggregate_signal_stats(outcomes, baseline_outcomes, horizon=..., interval='1d')` 按 `(signal_type × market)` 分桶聚合：

### 4.1 Wilson CI

```
wilson_ci(wins, n, z=1.96) → (ci_low, ci_high)
```

标准 Wilson score 95% 双尾置信区间，n == 0 返回 `(0.0, 0.0)`；结果 clamp 至 `[0, 1]`。

### 4.2 基准超额

```
excess = ci_low - baseline_win_rate
```

以置信下界（保守端）减去同市场全体 bar 基准胜率，正值代表信号相对随机入场有超额收益。

### 4.3 SignalStat 字段契约

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_type` | `str` | 信号类型（与 `VPSignal.signal_type` 对应） |
| `market` | `str` | 市场标识（`cn` / `hk` / `us` / `crypto`） |
| `interval` | `str` | K 线周期，默认 `'1d'` |
| `horizon` | `int` | 前瞻 bar 数 |
| `win` | `int` | 胜次数 |
| `loss` | `int` | 败次数 |
| `sample` | `int` | 有效样本数 = win + loss |
| `win_rate` | `float \| None` | 胜率 = win / sample；sample == 0 时为 None |
| `ci_low` | `float \| None` | Wilson 95% CI 下界 |
| `ci_high` | `float \| None` | Wilson 95% CI 上界 |
| `baseline_win_rate` | `float \| None` | 同市场全体 bar 基准胜率 |
| `excess` | `float \| None` | 超额 = ci_low - baseline_win_rate |

---

## 5. `verified` 口径

`resolve_marker_hit_fields` 中的 `verified` 由以下条件同时满足：

```python
verified = (
    stat.sample >= min_sample
    AND stat.ci_low is not None
    AND stat.baseline_win_rate is not None
    AND stat.ci_low > stat.baseline_win_rate
)
```

其中 `min_sample` 优先读取 `SIGNAL_HIT_VERIFIED_MIN_SAMPLE` 配置（默认 `0`，即读 `backtest_eval_window_days` 作为兜底）。

**语义**：样本足（统计可信）且置信下界超过基准（有超额优势）。

---

## 6. `signal_stats` 表

```
表名：signal_stats（src/storage.py → SignalStatRow）
唯一约束：(signal_type, market, interval, horizon)
```

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK | 自增主键 |
| `signal_type` | VARCHAR(64) | 信号类型 |
| `market` | VARCHAR(16) | 市场（cn/hk/us/crypto） |
| `interval` | VARCHAR(8) | K 线周期（默认 1d） |
| `horizon` | INTEGER | 前瞻 bar 数 |
| `win` | INTEGER | 胜次数 |
| `loss` | INTEGER | 败次数 |
| `sample` | INTEGER | 有效样本数 |
| `win_rate` | FLOAT | 胜率 |
| `ci_low` | FLOAT | CI 下界 |
| `ci_high` | FLOAT | CI 上界 |
| `baseline_win_rate` | FLOAT | 基准胜率 |
| `excess` | FLOAT | 超额 |
| `computed_at` | DATETIME | 最后计算时间 |

读写接口：`src/repositories/signal_stats_repo.py` → `SignalStatsRepository`（`get` / `save_batch`）。

---

## 7. `--signal-backtest` 批作业

对自选池（或指定列表）运行三重门信号回测，聚合后写入 `signal_stats` 表。

### 7.1 触发方式

```bash
python main.py --signal-backtest
```

watchlist 来源与看板完全一致：读取 `SystemConfigService` 的 `STOCK_LIST` 配置项（等价逻辑在 `signal_backtest_service.py` 内独立实现，不依赖 FastAPI 端点模块，避免重型 Web 框架初始化）。

### 7.2 批作业参数

`SignalBacktestService.run(codes=None, horizon=None, interval='1d')` 可选参数：
- `codes`：指定股票代码列表；`None` 时读自选池。
- `horizon`：前瞻 bar 数；`None` 时取 `SIGNAL_BACKTEST_HORIZON_BARS` 配置（默认 10）。
- `interval`：bar 粒度；`'1d'`（默认）走日线，行为不变；分钟（`1m/5m/15m/1h`）在分钟 bar 上重算信号 + 三重门评估，详见 §11。

单股最小 bar 数（`_MIN_BARS = 50`）；不足则跳过，避免统计无意义。

### 7.3 返回结果

```python
{
    "processed": int,   # 成功回测的股票数
    "codes": int,       # 自选池总数（含跳过/失败）
    "stats_written": int,  # 写入 signal_stats 的行数
    "skipped": int,     # 跳过（数据不足/未知市场）
    "errors": int,      # 单股异常数（不拖垮整批）
}
```

---

## 8. SIGNAL_BACKTEST_* 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SIGNAL_BACKTEST_ENABLED` | `false` | 是否启用信号回测批作业（设为 `true` 后 `--signal-backtest` CLI 才触发写入） |
| `SIGNAL_BACKTEST_HORIZON_BARS` | `10` | 三重门前瞻 bar 数（日线数），决定评估周期 |
| `SIGNAL_HIT_VERIFIED_MIN_SAMPLE` | `0`（读 `backtest_eval_window_days` 作兜底） | verified 所需最小样本数 |

---

## 9. 命中率改源（A6）

M3-A6 之前，`/signals` 端点命中率来源为 per-code `BacktestResult` 记录（`backfill_signal_hit_rate`）。

M3-A6 改源后，`resolve_marker_hit_fields(signal_type, code, *, interval='1d')` 改读 `signal_stats` 表，按 `(signal_type, market(code), interval)` 聚合（`interval` 默认 `1d`，分钟读出见 §11）：

- **改变**：命中率不再按个股历史分析记录聚合，而是按信号类型跨自选池横截面统计。
- **不变**：`/signals` 端点 marker 的 6 个字段键名（`hit_rate / hit_sample / verified / ci_low / ci_high / baseline_excess`）兼容保留，缺桶时回落 all-None（与旧"无样本"路径行为一致）。
- **旧路径**：`backfill_signal_hit_rate` 仍保留，供 per-code 场景或历史兼容使用。

---

## 10. 前端三处展示

可信度字段通过 `apps/dsa-web/src/utils/credibility.ts` 统一格式化：

| 工具函数 | 输入 | 输出示例 |
|---------|------|---------|
| `formatCi({ ciLow, ciHigh })` | `{ ciLow: 0.55, ciHigh: 0.75 }` | `"[55%–75%]"` |
| `formatExcess(baselineExcess)` | `0.08` | `"超额 +8pp"` |
| `verifiedLabel(verified)` | `true` | `"已验证"` |

展示位置：
1. **K 线钻取面板**（`KLineChartPanel` 信号详情）：CI 区间 + 样本数 + 基准超额 + verified 标识
2. **工作台信号 tab**（`StockWorkstationPage` signals tab）：同上，per-signal 行展示
3. **信号看板行**（`SignalBoardPage`）：`BoardEntry` 级别的 `ciLow / ciHigh / baselineExcess / verified` 字段

---

## 11. 信号可信度分钟化（链路B）

把日线三重门可信度回测扩展到**分钟粒度**：在分钟 bar 上重算 VPS 信号、用分钟前向窗口做三重门评估，产出按 `(signal_type × market × interval × horizon)` 隔离的 `signal_stats` 桶。日线链路（`interval='1d'`）行为字节级不变——不传 `interval` 即现状。

### 11.1 语义：分钟信号 + 分钟评估

- **分钟信号**：`compute_volume_price_signals` / `evaluate_signal_outcomes` 在分钟 bar 上重新计算（同一套 VPS 规则，bar 粒度无关）。
- **分钟评估**：三重门前瞻 `horizon` 根 **分钟 bar**（非日线）。`horizon` 为相对 bar 数，分钟下口径随之缩短，例如 `5m × horizon=10 = 50 分钟` 评估窗。
- **触发对齐前提**：`_to_epoch_ms_shanghai` 已升级为分钟分辨率感知——纯日期 → 当日午夜（日线不变），带时分秒 → 保留时分秒，使分钟 bar 的 `marker.timestamp == _last_ts(window)` 触发判定成立（否则同日分钟 bar 会全部坍缩到午夜，破坏对齐）。

### 11.2 用法

```bash
# 日线（默认，行为不变）
python main.py --signal-backtest

# 分钟（在分钟 bar 上重算信号 + 三重门，落 interval=5m 桶）
python main.py --signal-backtest --signal-backtest-interval 5m
```

`--signal-backtest-interval` 取值 `{1d, 1m, 5m, 15m, 1h}`，词表与盘中回测统一（`src/core/intraday_backtest.validate_interval`）。

### 11.3 取数与市场/历史

分钟取数复用链路A 的 `DataFetcherManager.get_intraday_data`（market 路由按 code 在其内部判定），并把 `datetime` 列重命名为 `date`，复用下游 VPS/`_eval` 既有 `date` 列契约（保留分钟时间戳）：

| 市场 | 分钟来源 | 历史可得（近窗，源自身封顶） |
|------|---------|------------------------------|
| crypto | 交易所（Binance 等） | 较深；按 `days` 估算回看根数 |
| A股沪深 | Tushare（1m 需 token）/ akshare | 较深；窗口由源默认/上限决定 |
| 美股个股 | yfinance（免 key 单源） | 5m/15m≈60d、1h≈730d；**1m 不支持**（fail-closed） |

`_minute_fetch_days` 给 `get_intraday_data` 传"天数提示"（`1h → 730`，其余 `→ 365`）。**注意**：`days` 的实际生效程度因源而异——crypto 按 `days` 估算回看根数；A股（Tushare/akshare）与美股（yfinance）分钟历史窗口主要由各源自身默认/上限决定，`days` 偏大不会取错数据（各源自身封顶），故"取值给足"。如需为非 crypto 源真正加深历史，应改为下传 `start_date`（留待后续）。单股不足 `_MIN_BARS=50` 根则跳过。

> **市场支持范围**：分钟路径目前覆盖 crypto / A股沪深 / 美股个股（见上表）。港股（HK）及指数代码无分钟取数支持，走分钟回测时单股取数失败被计入 `errors`（单股失败不拖垮整批，符合稳定性护栏），不产出分钟桶。

### 11.4 读出（最小可查）

- **批落库**：`signal_stats` 行带 `interval=<interval>`，与日线桶按唯一键 `(signal_type, market, interval, horizon)` 天然隔离，**零 schema 迁移**（`interval` 列与唯一键此前已建好）。
- **看板 API**：`GET /api/v1/signals/board?interval=5m` 把可信度（`hit_rate`/`verified`/CI/超额）从对应分钟桶解析；非法 `interval` → `422`。看板 K 线与标记仍按日线计算，仅可信度字段切换到分钟桶，供"分钟级可信度"查看。
- **resolver**：`resolve_marker_hit_fields(signal_type, code, *, interval='1d')` 按 interval 取桶；缓存键已含 interval，避免 1d/5m 串桶。

### 11.5 限制

- VPS 阈值为**日线调参**，分钟下直接复用属 best-effort，未单独为分钟标定（crypto 旁路阈值除外）。
- 分钟批量取数受各源**限频**约束；自选池较大时分钟批作业耗时显著高于日线。
- 美股 `1m`、A股 `1m`（无 Tushare token 时）按各自数据源限制 **fail-closed**，不静默回退日线。
- 分钟桶需**先跑** `--signal-backtest-interval <粒度>` 才有数据；未跑时 `?interval=` 读出回落 all-None（与"无样本"路径一致）。

---

## 12. 已知局限

- **逐 bar 全量重跑成本**：当前回测按每根 bar 因果重跑信号规则，时间复杂度 O(n²)，受自选池股票数和历史 bar 数影响；默认拉取 365 日日线（`_FETCH_DAYS`），限自选池离线运行可接受，不适用于全市场在线实时触发。
- **仅覆盖自选池**：`signal_stats` 仅对 `STOCK_LIST` 自选池有数据，非自选池股票命中率回退 all-None。
- **换手率与 A 股资金面**：当前三重门仅用 OHLCV 推导价位，未接入换手率、主力资金、北向资金等 A 股流动性指标，留待 M4 补充。
- **盘中/分钟级**：已支持分钟粒度信号可信度回测（见 §11）；VPS 阈值仍为日线调参，分钟下为 best-effort。
- **`expired` 不计入胜率**：到期未触门的样本被排除在 `sample` 分母外，胜率是条件性胜率（非全样本命中率），需理解定义差异。
- **horizon 读取语义与旧桶残留**：`resolve_marker_hit_fields` 按当前 `SIGNAL_BACKTEST_HORIZON_BARS` 配置的 `horizon` 精确读取 `signal_stats` 桶；变更 horizon 配置后，旧 horizon 的桶行不会被自动删除而是被忽略（按精确 horizon 读取，不会误用），属无害累积，如需清理可重跑批作业或手动清桶。
- **小样本展示口径**：`sample < SIGNAL_HIT_VERIFIED_MIN_SAMPLE`（缺省回落 `BACKTEST_EVAL_WINDOW_DAYS`）时回填全 null（前端显示「样本不足」），不展示不可信的胜率/CI/超额（对齐本文 §5 与 spec §4.1）。
- **钻取面板命中率行**：当前为「X% · n」紧凑串（与工作台/看板共享 `formatHitRate` 口径统一），未带「命中率」前缀标签；属可读性取舍，不影响数据正确性。

---

## 13. 回滚方式

M3-A 为增量实现，无主流程强依赖：

- 回滚评估器：删除 `src/services/signal_backtest.py`、`src/services/signal_backtest_service.py`、`src/repositories/signal_stats_repo.py`、`src/storage.py` 中 `SignalStatRow` 定义及迁移。
- 回滚命中率改源：将 `src/services/signal_hit_rate.py` 的 `resolve_marker_hit_fields` 回退至 `backfill_signal_hit_rate` 调用（旧 per-code 实现保留，签名未变）。
- 前端回滚：移除 `apps/dsa-web/src/utils/credibility.ts` 引用、三处展示点的 CI/超额展示，以及 `SignalMarker` / `BoardEntry` 中的 `ciLow / ciHigh / baselineExcess` 字段。
- 批作业：从 `main.py` 移除 `--signal-backtest` 参数分支即可。
