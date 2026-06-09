# crypto 回测兼容 设计（验证 + 锁定 + 文档）

> 阶段二「回测兼容」项。经子系统映射查证：crypto 回测已通过现有基础设施**基本走通**——无需新增日历或历史数据源。本特性的目标是**用端到端测试锁定该行为、补齐文档**，而非新建能力。

**Goal:** 以离线端到端集成测试证明并锁定「crypto symbol 走现有回测流程产出正确 `BacktestResult`」，并在文档中正式登记 crypto 回测支持。零生产代码改动（除非测试暴露真 bug）。

**范围决策（已确认）：** 验证 + 锁定 + 文档（最小、稳定性优先）；**不**做语义重标、crypto 专属诊断、引擎/Web/API/config 改动、做空/杠杆/合约。

---

## 1. 背景：为何这是「验证」而非「构建」

子系统映射（5 路并行只读探查）+ 直接读码查证，确认 crypto 回测的每一层已就绪：

- **候选纳入**：`BacktestRepository.get_candidates`（`backtest_repo.py:42-66`）按 age + 可选精确 code + 排除 `market_review` 过滤 `AnalysisHistory`，**无市场/区域过滤**——crypto 分析记录（code 含 `/`）天然是回测候选。
- **历史数据源已存在**：`_try_fill_daily_data`（`backtest_service.py:517-534`）调 `DataFetcherManager.get_daily_data(code)`，对 crypto code 经 `is_crypto_code` 路由到 Binance/OKX/Coinbase 的日线 K 线抓取器（`/api/v3/klines interval=1d` 等，已实现），返回标准 OHLCV DataFrame，`save_daily_data` 存入 **code 格式无关**的 `StockDaily` 表。
- **7×24「日历」非问题**：回测不从日历生成日期，而是取 `StockDaily` 中 `analysis_date` 之后的**前 N 行**（`get_forward_bars`，`stock_repo.py:152-161`）。各市场抓取器只填充本市场交易日；crypto 抓取器返回每个日历日，故「前 N 行 = 后 N 个日历日」**天生正确**。
- **引擎符号无关**：`BacktestEngine.evaluate_single` 基于 `DailyBarLike(date, high, low, close)` 与简单价差收益，无 A 股特化硬假设，crypto 现货价直接适用。

唯一「不完美」处（**本特性不修，仅文档说明**）：`first_hit_trading_days` 对 crypto 实为日历日计数（crypto 每日皆交易日，值正确，标签源自股票语境）；`_try_fill_daily_data` 对 crypto 取 `eval_window_days*2` 日历日属轻微 overfetch（仍能取够 bar）。

## 2. 架构与组件

无新增生产模块。

- **新增** `tests/test_crypto_backtest.py`：镜像 `tests/test_backtest_service.py` 的隔离模式——temp SQLite（`DATABASE_PATH` env + `Config._instance=None` + `DatabaseManager.reset_instance()`），seed `AnalysisHistory` + `StockDaily`，跑 `BacktestService.run_backtest`，断言 `BacktestResult`。
- **修改** `docs/crypto-guide.md`：新增「回测」节。
- **修改** `docs/CHANGELOG.md`：`[Unreleased]` 扁平一条。

## 3. 已查证的数据流（本特性锁定它）

```
crypto AnalysisHistory(code="BTC/USDT", advice, stop_loss, take_profit)
  → get_candidates（无市场过滤，纳入）
  → get_start_daily / get_forward_bars（StockDaily，code 无关）
  → evaluate_single（DailyBarLike，符号无关）→ BacktestResult(completed)
缺数据时：_try_fill_daily_data → get_daily_data（is_crypto_code 路由 → Binance/OKX/Coinbase 日线）→ save_daily_data
```

## 4. 测试（核心交付，全离线、无网络、无 LLM）

隔离与 seed 完全沿用 `test_backtest_service.py` 模式（`AnalysisHistory` 需含 `context_snapshot` 内 `enhanced_context.date` 供 `_resolve_analysis_date` 解析）。

1. **`test_crypto_backtest_seeded_data_completed`**：seed `AnalysisHistory(code="BTC/USDT", operation_advice="买入", stop_loss, take_profit, report_type="simple")` + `StockDaily(code="BTC/USDT")` 起始行 + **`eval_window_days`(=3) 个 forward bars**（其一 `high >= take_profit`，避免 insufficient_data）。`run_backtest(code="BTC/USDT", eval_window_days=3, min_age_days=0)` → 断言 `eval_status=="completed"`、`stock_return_pct`/`outcome`/`hit_take_profit==True`/`first_hit=="take_profit"`/`first_hit_date` 正确，且 `code=="BTC/USDT"`。证明候选纳入 + 仓储 + 引擎对 crypto code 等同处理。
2. **`test_crypto_backtest_autofill_routes_crypto_klines`**：仅 seed `AnalysisHistory(code="BTC/USDT")`（无 `StockDaily`）；`unittest.mock.patch` `DataFetcherManager.get_daily_data` 返回一个 crypto 日线形 DataFrame（标准列 `date/open/high/low/close/volume/amount`，含起始日 + **≥ `eval_window_days` 个 forward 日**）与 source `"binance"`。`run_backtest` → 断言 `StockDaily` 中出现 `code=="BTC/USDT"` 行（补数已持久化）且结果 `completed`。证明补数路径把 crypto code 正确路由（不触网）。
3. **`test_crypto_backtest_insufficient_when_no_data`**：仅 seed `AnalysisHistory(code="BTC/USDT")`；patch `get_daily_data` 返回 `(空 DataFrame, "")` → 断言 `eval_status=="insufficient_data"`。锁定优雅降级。

## 5. 错误处理

复用现有降级：无起始/前向数据 → `insufficient_data`（非异常、非崩溃）。测试 3 锁定。crypto 抓取器全失败时 `get_daily_data` 抛 `DataFetchError`，被 `_try_fill_daily_data` 的 `try/except` 吞为 warning，最终仍记 `insufficient_data`——与股票同语义，本特性不改。

## 6. 文档

- `docs/crypto-guide.md` 新增「回测」节：crypto symbol 走与股票相同的回测流程（`AnalysisHistory` → 前向日线 → `evaluate_single`）；历史数据来自 crypto 日线 K 线抓取器（Binance/OKX/Coinbase）；7×24 下 `first_hit_trading_days` 即日历日（对 crypto = 交易日）；无需独立日历或配置；crypto 分析记录自动纳入回测候选。
- `docs/CHANGELOG.md` `[Unreleased]`：`- [文档] 文档化并以端到端测试锁定 crypto 回测支持（沿用现有回测引擎与 crypto 日线数据源，无新增运行时/配置）`。

## 7. 应急（contingent）

若任一测试暴露 crypto 处理的真 bug（分析判断不会），在本特性内以独立、最小 commit 修复并在交付说明中写明根因与影响；不扩大到 option 2 的硬化范围。

## 8. 分支与回滚

- 分支：`feat/crypto-backtest`，叠在 `feat/crypto-market-indicators` 之上；栈：`main ← crypto-market-review ← crypto-new-listings ← crypto-market-indicators ← crypto-backtest`。
- 回滚：纯测试 + 文档，`git revert` 或丢弃分支即恢复，无运行时影响。

## 9. 范围边界（YAGNI）

不做：`BacktestEngine` 任何改动、`first_hit_trading_days` 语义重标、crypto 专属诊断/错误码、`_try_fill` 窗口精确化、Web/API/schema/config 改动、做空/杠杆/永续回测。纯现货 long-only 按现状锁定。
