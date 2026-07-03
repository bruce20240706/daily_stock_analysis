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
| `ci_low_corrected` | `float \| None` | family-wise（Bonferroni-CI）校正后 Wilson 下界；`family_size <= 1` 时等于 `ci_low`；`sample == 0` 时为 `None`（详见 §5.1） |
| `family_size` | `int` | 本次聚合中可检验格子数 N（同批 `sample >= min_sample` 的 `(signal_type × market)` 格子数） |

### 4.4 风险画像（`risk_metrics`，链路B 增量）

`aggregate_signal_stats` 在按 `(signal_type × market)` 聚合胜率的同时，顺路收集每个格子的信号触发事件收益序列，算出一份不年化风险画像，写入 `SignalStat.risk_metrics`（dict，最终落库为 `signal_stats.risk_metrics_json`）。数学公式与链路A（Inc 1a 回测风险画像）通过共享纯函数 `risk_metrics_from_returns`（`src/core/backtest_engine.py`）完全一致：Sharpe = 原始样本 `mean/std`、Sortino = `mean / sqrt(Σ_{r<0}r²/n)`、`max_drawdown_pct` 按复利事件净值峰谷、均 `round4`，除零/未定义返回 `None`，绝不返回 `inf`/`NaN`。

**收益口径（保守跳空感知）**：单笔收益由 `classify_triple_barrier_with_return` 算出（毛收益，不计交易成本）：
- `entry` = 信号触发 bar 的收盘价（`close`），隐含"收盘可完全成交"的简化假设，不建模真实滑点/流动性冲击。
- `win`：`exit = target`（不因跳空高开多计盈利）。
- `loss`：`exit = min(触障 bar open, stop)`（跳空低开按更差的 open 计，比单纯用 stop 更保守）。
- `expired`：`exit = 前瞻窗口末 bar 的 close`（窗末平仓）。
- 三种结果的 exit 侧选取原则一致：宁可低估收益、不高估收益（win 不吃跳空红利、loss 吃跳空亏损、expired 按窗末价了结），因此风险画像整体偏保守估计，不代表实盘可实现的最优执行价。

**失真形态整层剔除**：当 `target <= entry`（触发 bar 收盘已越过回踩锚定的 target，属动量/突破形态下的价位推导失真）时，不论最终 `win`/`loss`/`expired`，该笔 `return_pct` 一律记为 `None`，从收益序列**整层剔除**（不是只剔除某一类结果——单边剔除会导致"赢的不计、输的照计"的选择性偏差）。剔除数量以 `risk_metrics.excluded` 计数披露，供判断该格收益序列的有效覆盖度。

**expired 计入收益序列**：`expired`（到期未触门）虽不计入胜率分母（`SignalStat.sample = win + loss`），但会按窗末平仓价计入风险画像的收益序列——即风险画像只按"结果是否有效（未被失真剔除）"筛选样本，不按"结果类型（win/loss/expired）"筛选，这一原则与链路A（回测风险画像仅按"已完成且非 cash"筛选、不按盈亏方向筛选）口径一致。

**sample 三口径关系**：同一个格子里有三个不同的"样本数"，避免混用：

| 字段 | 分母含义 | 计入范围 |
|------|---------|---------|
| `SignalStat.sample`（= `win + loss`） | 胜率/CI 分母 | 仅 `win`、`loss`，不含 `expired` |
| `risk_metrics["sample"]` | 风险画像收益序列有效样本数 | `win` + `loss` + `expired`，且 `return_pct` 非 `None`（已剔除失真形态） |
| `risk_metrics["excluded"]` | 因失真形态剔除的笔数 | 上述三类结果中 `target <= entry` 被整层剔除的部分 |

三者不保证相等：`risk_metrics["sample"] + risk_metrics["excluded"]` 一般 **大于** `SignalStat.sample`（因为多了 `expired`），也可能因失真剔除而与 `win + loss + expired` 总数不同。

**重叠窗自相关与跨标的混流**：同一信号类型在同一市场下，不同股票、不同触发时点的收益样本被合并进同一格子统计。前瞻窗口（`horizon` 根 bar）在时间上可能相互重叠（同一股票连续触发、或不同股票同期触发），样本之间并非独立同分布；风险画像也不区分标的，多只股票的收益混流进同一 Sharpe/Sortino/maxDD 计算，不代表可执行的单一资金曲线（`max_drawdown_pct` 是"信号事件序列净值"的峰谷回撤，不是真实组合回撤）。解读时需按此局限打折扣。

**描述性统计、无 CI、不得跨格子挑选**：风险画像是纯描述性统计（点估计），不附带置信区间，也未经 §5.1 的多重检验（family-wise）校正——`ci_low_corrected` / `verified` 才是经过校正的可信度判据。**不应**依据风险画像（如"Sharpe 更高"）在多个 `(signal_type × market)` 格子间挑选信号，这等价于对未校正统计量做隐式多重比较，容易把运气误判为优势；跨格子挑选仍应以 `verified`（§5.1 校正后）为准。

**跨 interval 不可比**：`risk_metrics.interval` / `risk_metrics.horizon` 为自描述字段，标注该格风险画像来自哪个 K 线周期与前瞻窗口。不同 `interval`（如 `1d` vs `5m`）下 `horizon` 根 bar 对应的实际时间跨度不同（例：`5m × horizon=10` ≈ 50 分钟，`1d × horizon=10` ≈ 10 个交易日），Sharpe/Sortino/maxDD 的时间尺度不可直接跨 interval 比较。

**生效前提**：风险画像随 `aggregate_signal_stats` 一并计算，仅在**手动执行** `python main.py --signal-backtest`（或 `--signal-backtest-interval <粒度>`）时生效写入；当前无调度任务自动触发该批作业（`main.py` 仅在显式传入 `--signal-backtest` 参数时才运行，未接入 `--schedule`/GitHub Actions 定时流程）。

**legacy NULL 语义**：`signal_stats.risk_metrics_json` 为幂等补列（`_ensure_signal_stats_columns`，与 `ci_low_corrected`/`family_size` 同款迁移守卫），升级前写入的老行该列为 `NULL`；`resolve_marker_hit_fields` 读到 `NULL`（或非法 JSON、非 dict 内容）一律返回 `risk_metrics: None`，不报错、不假造数据。需重跑批作业才能为老行补上风险画像。

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

> 自 Inc 1c 起，判据中的置信下界改为 family-wise 校正后的 `ci_low_corrected`（上式中的 `stat.ci_low` 实际读取 `ci_low_corrected`，老行 NULL 回退 raw `ci_low`），完整语义见 §5.1。

---

## 5.1 Inc 1c：family-wise（Bonferroni-CI）多重检验校正

单次 `--signal-backtest` 批作业会同时对多个 `(signal_type × market)` 格子做显著性判定；格子数越多，仅凭运气出现"raw `ci_low` > 基准"的组合概率越高。Inc 1c 引入 family-wise Bonferroni-CI 校正收紧判据，抑制这类偶然命中被误标为 `verified`：

- **校正判定语义**：`verified` 的置信下界判据由 raw `ci_low` 改为**校正后下界** `ci_low_corrected`（即 `resolve_marker_hit_fields` 判据变为 `stat.sample >= min_sample AND stat.ci_low_corrected > stat.baseline_win_rate`）。`ci_low_corrected` 由 `wilson_ci(win, sample, z_corr)` 算出，其中 `z_corr = bonferroni_z(family_size, fwer_alpha)`；当 `family_size <= 1`（family 内仅此一个可检验格子）时 `z_corr` 取字面量 `1.96`，`ci_low_corrected == ci_low`，判定与升级前完全一致，不引入变化。
- **family 口径**：family 为单次 `--signal-backtest`（或 `--signal-backtest-interval`）批作业聚合出的、`sample >= min_sample` 的 `(signal_type × market)` 格子集合；`family_size` 即该集合的大小。**跨 run、跨 interval、跨 horizon 的格子互不合并校正**——每次批作业独立成 family，不同 interval/horizon 桶各自的 `family_size` 互不影响。
- **写时快照与老行回退**：`ci_low_corrected` / `family_size` 在批作业写入 `signal_stats` 时按当次 family 计算并落库为快照；此后单独调整 `SIGNAL_HIT_VERIFIED_MIN_SAMPLE` 或 `SIGNAL_BACKTEST_FWER_ALPHA` 不会自动重算已落库的行，需重跑批作业才能刷新。升级前写入的老行 `ci_low_corrected` / `family_size` 为 `NULL`（legacy），读路径 `resolve_marker_hit_fields` 检测到 `NULL` 时回退用 raw `ci_low` 判定 `verified`（即升级前行为），不因缺列而误判或报错。
- **alpha 配置（双尾口径）**：`SIGNAL_BACKTEST_FWER_ALPHA`（默认 `0.05`，域 `[0.0001, 0.05]`，函数内钳制、仅可更严不可更松）控制的是**双尾**族错误率；`verified` 是单尾判据（只看下界一侧是否超基准），因此等价单尾族错误率约为 `alpha / 2`。
- **诚实边界**：`baseline_win_rate`（见 §3）为**每市场共享**、按**已知常量**参与比较的全体 bar 入场基准，本身不参与 family-wise 校正、不随 family 收紧。也就是说，本次校正只收紧了信号胜率一侧的置信下界，**不包含 baseline 自身的估计误差**，两侧比较仍是"校正后的信号置信区间 vs 未加误差带的基准点估计"，理解本节局限时需注意这一边界。

---

## 5.2 行为变更与兼容说明（向量化重构后）

信号引擎 `evaluate_signal_outcomes` 在 Task 4–11 中完成 O(n²)→O(n log k) 向量化重构，并同步修正了因果语义。以下几点在重跑 `--signal-backtest` 后会产生可见变化：

### 因果修正语义（回测路径）

`evaluate_signal_outcomes`（回测路径）现在使用 `_detect_upthrust_spring_causal_rows` 变体：对每根 bar `i`，只引用确认索引满足 `center + swing_k ≤ i` 的已确认 pivot，严格消除回测前视偏差。图表渲染路径（`compute_volume_price_signals` 全 df 调用）继续使用原 `_detect_upthrust_spring`，不受影响，图表 marker 几何不变。

### 命中率 7 字段重跑后会更新

重跑 `--signal-backtest` 后，`signal_stats` 表中以下 7 个字段会因因果修正而更新：

| 字段 | 说明 |
|------|------|
| `hit_rate` | 胜率（win/sample），因部分非因果信号样本消失而变化 |
| `hit_sample` (`sample`) | 有效样本数（win+loss），排除到期未触门的 expired |
| `verified` | 是否通过"样本足 AND ci_low > baseline_win_rate"验证 |
| `ci_low` | Wilson 95% CI 下界 |
| `ci_high` | Wilson 95% CI 上界 |
| `baseline_excess` (`excess`) | 超额 = ci_low - baseline_win_rate |
| `horizon` | 前瞻 bar 数（精确匹配桶键，配置变更后旧桶被忽略，无害累积） |

**操作**：重跑 `python main.py --signal-backtest`（或加 `--signal-backtest-interval <粒度>`）后数据更新，旧 horizon 桶被精确 horizon 查询忽略，不会误用。

### verified 注解跨 min_sample 阈值出现/消失

`SIGNAL_HIT_VERIFIED_MIN_SAMPLE` 配置（或 `backtest_eval_window_days` 兜底）决定 `verified` 所需最小样本数。自选池变化、重新回测或调整 `min_sample` 配置后，`verified` 标注可能跨阈值出现或消失：

- 样本增加（如自选池扩大）：`sample` 升过阈值，`verified` 可能从 null 变为 true/false。
- 样本减少（如因果修正排除非因果样本）：`sample` 可能降至阈值以下，`verified` 回落 null，前端展示"样本不足"。
- 调高 `SIGNAL_HIT_VERIFIED_MIN_SAMPLE`：已展示的 `verified=true` 标注可能消失，属预期行为。

### 今日已收盘 bar 现正常产信号

因果路径修正后，今日已完成收盘的 bar（`i == len(df)-1`）可以正常产出信号并进入评估，不再因前视检查而被意外过滤。图表 viz 路径行为不变。

- **链路B 右端截尾修复（2026-06-26）**：`_eval` 评估上界由 `n-1` 收紧为 `n-horizon`，仅统计有完整
  horizon 前瞻的信号，消除「慢解析者记 expired 被排除、快解析者计入」的右端截尾偏差。影响**日线 +
  分钟** signal_stats：`win/loss/sample/win_rate/ci_low/ci_high/baseline_win_rate/excess` 小幅变化，
  最近 `horizon-1` 根欠龄信号不再计入。图表 marker 与几何不变；重跑 `--signal-backtest` 落库后命中率
  注解数值随之刷新，并可能跨 `min_sample` 阈值出现/消失。
- **非 crypto 分钟历史深度（2026-06-26）**：链路B 分钟回测对非 crypto 显式下传 `start_date` 加深历史
  （美股/港股夹 yfinance band 5m/15m=58d、1h=725d；A股 tushare 经 start_date 加深，5m/1m 夹保守上限）。
  crypto 不变。美股分钟可信度统计由「源默认浅窗」变为可用。
- **yfinance band off-by-one 修复（2026-06-30）**：Yahoo Finance 要求请求严格在 last-N-天内；请求恰好
  上限天数被硬拒（真网核验：5m 59 天 OK / 60 天 FAIL，1h 729 天 OK / 730 天 FAIL）。us 与 hk（分钟均
  走 yfinance 路径）band 由 5m/15m=60、1h=730 收敛为 5m/15m=58、1h=725，留 1–4 天余量；cn 不受影响。

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
| `ci_low_corrected` | FLOAT | family-wise 校正后 CI 下界（Inc 1c）；`NULL`=legacy 行（升级前写入，未重跑） |
| `family_size` | INTEGER | 写时 family 可检验格子数 N（Inc 1c）；`NULL`=legacy 行 |
| `risk_metrics_json` | TEXT | 该格不年化风险画像 JSON（见 §4.4）；`NULL`=legacy 行（升级前写入，未重跑） |
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
| `SIGNAL_BACKTEST_FWER_ALPHA` | `0.05` | family-wise（Bonferroni-CI）多重检验校正的双尾族错误率（Inc 1c）；域 `[0.0001, 0.05]`，函数内钳制、仅可更严不可更松；详见 §5.1 |

---

## 9. 命中率改源（A6）

M3-A6 之前，`/signals` 端点命中率来源为 per-code `BacktestResult` 记录（`backfill_signal_hit_rate`）。

M3-A6 改源后，`resolve_marker_hit_fields(signal_type, code, *, interval='1d')` 改读 `signal_stats` 表，按 `(signal_type, market(code), interval)` 聚合（`interval` 默认 `1d`，分钟读出见 §11）：

- **改变**：命中率不再按个股历史分析记录聚合，而是按信号类型跨自选池横截面统计。
- **不变**：`/signals` 端点 marker 的 6 个字段键名（`hit_rate / hit_sample / verified / ci_low / ci_high / baseline_excess`）兼容保留，缺桶时回落 all-None（与旧"无样本"路径行为一致）。
- **新增（Inc 1c）**：追加 `ci_low_corrected` / `family_size` 两个可选透明字段（详见 §5.1），随原 6 字段一并透出；老行/缺桶同样回落 `None`，不破坏既有 6 字段契约。
- **旧路径**：`backfill_signal_hit_rate` 仍保留，供 per-code 场景或历史兼容使用。

---

## 10. 前端三处展示

可信度字段通过 `apps/dsa-web/src/utils/credibility.ts` 统一格式化：

| 工具函数 | 输入 | 输出示例 |
|---------|------|---------|
| `formatCi({ ciLow, ciHigh })` | `{ ciLow: 0.55, ciHigh: 0.75 }` | `"[55%–75%]"` |
| `formatExcess(baselineExcess)` | `0.08` | `"超额 +8pp"` |
| `verifiedLabel(verified)` | `true` | `"已验证"` |
| `unverifiedExcessNote({ verified, baselineExcess, ciLowCorrected, familySize })`（Inc 1c） | `verified=false` 但 raw 超额为正 | `"20 组同检校正后下界 48%,未超基准"`（消解"有超额却未验证"的矛盾展示；legacy 行 `ciLowCorrected=null` 时不注解） |

展示位置：
1. **K 线钻取面板**（`KLineChartPanel` 信号详情）：CI 区间 + 样本数 + 基准超额 + verified 标识
2. **工作台信号 tab**（`StockWorkstationPage` signals tab）：同上，per-signal 行展示
3. **信号看板行**（`SignalBoardPage`）：`BoardEntry` 级别的 `ciLow / ciHigh / baselineExcess / verified` 字段，以及 Inc 1c 新增的 `ciLowCorrected / familySize`（老行/legacy 载荷缺字段时为 `null`）

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
| 美股个股 | yfinance（免 key 单源） | 5m/15m≈58d、1h≈725d（yfinance 上限 60/730 被硬拒，余量）；**1m 不支持**（fail-closed） |
| 港股个股 | akshare（`stock_hk_hist_min_em`，主）/ yfinance（兜底） | 5m/15m≈58d、1h≈725d（yfinance 上限 60/730 被硬拒，余量）；**1m** fail-closed |

`_minute_fetch_days` 给 `get_intraday_data` 传"天数提示"（`1h → 730`，其余 `→ 365`）。**注意**：`days` 的实际生效程度因源而异——crypto 按 `days` 估算回看根数；A股（Tushare/akshare）与美股（yfinance）分钟历史窗口主要由各源自身默认/上限决定，`days` 偏大不会取错数据（各源自身封顶），故"取值给足"。如需为非 crypto 源真正加深历史，应改为下传 `start_date`（留待后续）。单股不足 `_MIN_BARS=50` 根则跳过。

> **市场支持范围**：分钟路径目前覆盖 crypto / A股沪深 / 美股个股 / 港股个股（best-effort，见上表）。港股（HK）个股已支持分钟（akshare 东财主 + yfinance 兜底，best-effort）；指数代码无分钟取数支持，单股取数失败被计入 `errors`（单股失败不拖垮整批，符合稳定性护栏），不产出分钟桶。

### 11.4 读出（最小可查）

- **批落库**：`signal_stats` 行带 `interval=<interval>`，与日线桶按唯一键 `(signal_type, market, interval, horizon)` 天然隔离，**零 schema 迁移**（`interval` 列与唯一键此前已建好）。
- **看板 API**：`GET /api/v1/signals/board?interval=5m` 把可信度（`hit_rate`/`verified`/CI/超额）从对应分钟桶解析；非法 `interval` → `422`。看板 K 线与标记仍按日线计算，仅可信度字段切换到分钟桶，供"分钟级可信度"查看。
- **resolver**：`resolve_marker_hit_fields(signal_type, code, *, interval='1d')` 按 interval 取桶；缓存键已含 interval，避免 1d/5m 串桶。

### 11.5 限制

- 已提供 interval 维度**窗口**覆盖通道（`VPS_<窗口>_<interval>`，默认不配=复用日线值，仅 4 个 window 字段，见 `docs/volume-price-signals.md` §7.3）；具体分钟定值仍待真实数据标定（crypto 旁路阈值除外）。
- 分钟批量取数受各源**限频**约束；自选池较大时分钟批作业耗时显著高于日线。
- 美股 `1m`、A股 `1m`（无 Tushare token 时）按各自数据源限制 **fail-closed**，不静默回退日线。
- 分钟桶需**先跑** `--signal-backtest-interval <粒度>` 才有数据；未跑时 `?interval=` 读出回落 all-None（与"无样本"路径一致）。

---

## 12. 已知局限

- **逐 bar 全量重跑成本**：当前回测按每根 bar 因果重跑信号规则，时间复杂度 O(n²)，受自选池股票数和历史 bar 数影响；默认拉取 365 日日线（`_FETCH_DAYS`），限自选池离线运行可接受，不适用于全市场在线实时触发。
- **仅覆盖自选池**：`signal_stats` 仅对 `STOCK_LIST` 自选池有数据，非自选池股票命中率回退 all-None。
- **换手率与 A 股资金面**：当前三重门仅用 OHLCV 推导价位，未接入换手率、主力资金、北向资金等 A 股流动性指标，留待 M4 补充。
- **盘中/分钟级**：已支持分钟粒度信号可信度回测（见 §11）；已提供 interval 维度窗口覆盖通道（默认不配=复用日线值，仅 4 个 window 字段），具体分钟定值仍待真实数据标定。
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
