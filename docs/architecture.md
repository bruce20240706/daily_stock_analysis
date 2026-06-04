# 架构说明（QuantPick）

> 活文档：随实现演进更新。完整设计与决策记录见 `docs/superpowers/specs/2026-06-04-ai-stock-picker-design.md`。

## 分层与单向依赖

```
cli ─▶ report ◀─ signals ◀─ portfolio(持仓)
 │        ▲          ▲
 ├─▶ screening ─▶ factors ─▶ indicators ◀── signals(择时也用指标)
 │      │            ▲
 │      ├─▶ strategies
 │      └─▶ data ─▶ (akshare/baostock/adata) + cache
 ├─▶ backtest ─▶ data            ai ─▶ (选股/择时输出)
 └─▶ advise ─▶ signals + portfolio
           core（被所有层依赖：config / types / errors / calendar / logging）
```

选股（横截面：选谁）与择时（`signals`，时间序列：何时进出）互补。依赖方向自上而下，**core 不依赖任何业务层**；`indicators`、`factors`、`signals.levels` 等为**纯函数**，便于单测。

## 模块职责（一句话）

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `core` | 配置 / 类型 / 错误码 / 交易日历 / 日志 | `AppConfig`、`Result`、`Market`、`ScoredStock` |
| `data` | 多源行情 adapter + 缓存 + 降级 | `DataSource`、`DataManager`、`LocalCache` |
| `indicators` | 技术指标计算（pandas-ta） | `compute_indicators`、`detect_patterns` |
| `factors` | 指标/基本面 → 标准化因子 + 注册表 | `Factor`、`register`、`zscore/rank_pct/winsorize` |
| `screening` | 硬过滤 + 多因子打分 + 排序 | `apply_filters`、`weighted_score`、`score_and_rank`、`Screener` |
| `strategies` | YAML 具名策略 | `Strategy`、`load_strategies` |
| `signals` | 择时：趋势过滤 + 量价触发 → 买卖信号 | `classify_regime`、`evaluate_buy/sell`、`build_levels`、`SignalEngine` |
| `portfolio` | 自选/持仓清单 | `Holding`、`load_holdings` |
| `ai` | LLM 定性分析（可插拔） | `Analyst`、`LLMAnalyst`、`MLRanker`(预留) |
| `backtest` | 前向收益验证（含信号有效性） | `evaluate`、`ForwardReturnReport` |
| `report` | CLI 表格 + Markdown 报告（含操作建议） | `render_table`、`render_markdown`、`save_report` |
| `cli` | 命令入口 | `update / screen / advise / backtest / report / run` |

## 信号层数据流（择时）

```
对候选股(买) / 持仓(卖)：
  指标增强日线 → trend 趋势过滤(Regime) → 闸门
     ├─ UP            → 买入触发(量价/动量) → 强度 + levels(ATR+摆动点) → BUY
     ├─ DOWN/超买/破位 → 卖出触发              → 强度 + levels            → SELL
     └─ RANGE         → 抑制                                            → HOLD
```

## 数据契约

- 日线 OHLCV 统一 schema：`date, open, high, low, close, volume, amount`（见 `data.base.BAR_COLUMNS`），按 `date` 升序。
- 归一化代码：A 股 `600519.SH` / `000001.SZ`，港股 `00700.HK`。
- 缓存布局：`data_cache/bars/<market>/<code>.parquet` + `data_cache/meta.sqlite`。
- 价位：`止损 = max(入场 − 2×ATR, 近期摆动低点)`，`止盈 = 入场 + 2R`。

## 约定

- 重依赖惰性导入；`import quantpick` 与 `quantpick --help` 仅需轻量依赖。
- 边界用 `Result`/错误码，单只失败不中断整批。
- 密钥仅来自环境变量 / `.env`；`config/holdings.yaml` 持仓已 gitignore。
- 存储用 parquet + SQLite，无 MySQL。

## 实现状态

- ✅ 已实现：归一化(`factors.normalize`)、打分(`screening.scorer`)、策略加载(`strategies.loader`)、过滤判定(`is_st_or_delisting`/`board_of`)、**价位计算(`signals.levels`)**、**持仓加载(`portfolio.holdings`)**、报告渲染(`report`)、CLI 骨架。
- ⏳ 待实现（见各文件 `TODO`）：数据抓取/缓存、指标计算、因子取值、`Screener.run`、**趋势过滤/触发/`SignalEngine`**、回测、LLM 分析。
