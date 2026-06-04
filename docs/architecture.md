# 架构说明（QuantPick）

> 活文档：随实现演进更新。完整设计与决策记录见 `docs/superpowers/specs/2026-06-04-ai-stock-picker-design.md`。

## 分层与单向依赖

```
cli  ─▶  report
 │         ▲
 ├─▶ screening ─▶ factors ─▶ indicators
 │       │           ▲
 │       ├─▶ strategies          ai ─▶ (screening 输出)
 │       └─▶ data ─▶ (akshare/baostock/adata) + cache
 └─▶ backtest ─▶ data
            core（被所有层依赖：config / types / errors / calendar / logging）
```

依赖方向自上而下，**core 不依赖任何业务层**；`indicators`、`factors`(部分) 为**纯函数**，便于单测。

## 模块职责（一句话）

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `core` | 配置 / 类型 / 错误码 / 交易日历 / 日志 | `AppConfig`、`Result`、`Market`、`ScoredStock` |
| `data` | 多源行情 adapter + 缓存 + 降级 | `DataSource`、`DataManager`、`LocalCache` |
| `indicators` | 技术指标计算（pandas-ta） | `compute_indicators`、`detect_patterns` |
| `factors` | 指标/基本面 → 标准化因子 + 注册表 | `Factor`、`register`、`zscore/rank_pct/winsorize` |
| `screening` | 硬过滤 + 多因子打分 + 排序 | `apply_filters`、`weighted_score`、`score_and_rank`、`Screener` |
| `strategies` | YAML 具名策略 | `Strategy`、`load_strategies` |
| `ai` | LLM 定性分析（可插拔） | `Analyst`、`LLMAnalyst`、`MLRanker`(预留) |
| `backtest` | 前向收益验证 | `evaluate`、`ForwardReturnReport` |
| `report` | CLI 表格 + Markdown 报告 | `render_table`、`render_markdown`、`save_report` |
| `cli` | 命令入口 | `update/screen/backtest/report/run` |

## 数据契约

- 日线 OHLCV 统一 schema：`date, open, high, low, close, volume, amount`（见 `data.base.BAR_COLUMNS`），按 `date` 升序。
- 归一化代码：A 股 `600519.SH` / `000001.SZ`，港股 `00700.HK`。
- 缓存布局：`data_cache/bars/<market>/<code>.parquet` + `data_cache/meta.sqlite`。

## 约定

- 重依赖惰性导入；`import quantpick` 与 `quantpick --help` 仅需轻量依赖。
- 边界用 `Result`/错误码，单只失败不中断整批。
- 密钥仅来自环境变量 / `.env`。
- 存储用 parquet + SQLite，无 MySQL。

## 实现状态

- ✅ 已实现：归一化(`factors.normalize`)、打分(`screening.scorer`)、策略加载(`strategies.loader`)、过滤判定(`is_st_or_delisting`/`board_of`)、报告渲染(`report`)、CLI 骨架。
- ⏳ 待实现（见各文件 `TODO`）：数据层抓取/缓存、指标计算、因子取值、`Screener.run`、回测、LLM 分析。
