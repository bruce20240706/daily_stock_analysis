# crypto 回测兼容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以离线端到端集成测试锁定「crypto symbol 走现有回测流程产出正确 `BacktestResult`」，并在文档中正式登记 crypto 回测支持。零生产代码改动（除非测试暴露真 bug）。

**Architecture:** 纯验证 + 文档。新增 `tests/test_crypto_backtest.py`（镜像 `tests/test_backtest_service.py` 的 temp-SQLite + seed 模式），覆盖三条真实路径：seeded-completed、autofill-routes-crypto（mock `get_daily_data`）、insufficient-when-no-data。再补 `docs/crypto-guide.md` 回测节与 `CHANGELOG`。

**Tech Stack:** Python 3.10、`unittest`、`unittest.mock.patch`、`pandas`、SQLAlchemy（temp SQLite）。

**重要——这些是 characterization / lock 测试，不是红→绿 TDD：** 它们验证**既有**行为，故首次运行就应 **PASS**。若某条 FAIL，说明查证遗漏、暴露了 crypto 处理的真 bug → 按 spec §7 在本特性内独立 commit 修复并说明根因；不得为了让测试变绿而改测试断言去迁就错误行为。

**环境约定：** 无 `python`，用 `.venv/bin/python`；pytest 需 `PYTHONPATH="$PWD"`。

**Spec：** `docs/superpowers/specs/2026-06-09-crypto-backtest-design.md`
**分支：** `feat/crypto-backtest`（已建，叠在 `feat/crypto-market-indicators`）。

---

### Task 1: 测试骨架 + seeded-completed（核心锁定）

**Files:**
- Create: `tests/test_crypto_backtest.py`

- [ ] **Step 1: 写测试（含 setUp/tearDown/helper + 第 1 个 case）**

```python
# -*- coding: utf-8 -*-
"""crypto 回测兼容 characterization / lock 测试（离线，无网络、无 LLM）。

证明并锁定：crypto symbol（如 BTC/USDT）走与股票相同的现有回测流程
（AnalysisHistory → 前向日线 → evaluate_single）产出正确 BacktestResult。
这些测试验证既有行为，应首次即 PASS；若 FAIL 说明暴露真 bug，按 spec §7 修复。
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from src.config import Config
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager, StockDaily

CRYPTO_CODE = "BTC/USDT"


class CryptoBacktestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_crypto_backtest.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_analysis(self, *, analysis_date: date = date(2024, 1, 1)) -> None:
        """Seed a crypto AnalysisHistory old enough to be a backtest candidate."""
        with self.db.get_session() as session:
            session.add(
                AnalysisHistory(
                    query_id="cq1",
                    code=CRYPTO_CODE,
                    name="BTC/USDT",
                    report_type="simple",
                    sentiment_score=70,
                    operation_advice="买入",
                    trend_prediction="看多",
                    analysis_summary="crypto backtest lock test",
                    stop_loss=57000.0,
                    take_profit=66000.0,
                    created_at=datetime(2024, 1, 1, 0, 0, 0),
                    context_snapshot=json.dumps(
                        {
                            "enhanced_context": {"date": analysis_date.isoformat()},
                            "market_phase_summary": {
                                "phase": "intraday",
                                "market": "crypto",
                                "trigger_source": "api",
                            },
                        }
                    ),
                )
            )
            session.commit()

    def _seed_daily(self) -> None:
        """Seed start bar + 3 forward bars; day2 high (67000) hits take_profit (66000)."""
        with self.db.get_session() as session:
            session.add(
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 1),
                           open=60000.0, high=60500.0, low=59500.0, close=60000.0)
            )
            session.add_all([
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 2), open=60000.0, high=67000.0, low=60000.0, close=65000.0),
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 3), open=65000.0, high=66000.0, low=64000.0, close=65500.0),
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 4), open=65500.0, high=66500.0, low=64500.0, close=66000.0),
            ])
            session.commit()

    def _result(self) -> BacktestResult:
        with self.db.get_session() as session:
            return session.query(BacktestResult).filter(BacktestResult.code == CRYPTO_CODE).one()

    def test_crypto_backtest_seeded_data_completed(self) -> None:
        self._seed_analysis()
        self._seed_daily()
        service = BacktestService(self.db)
        stats = service.run_backtest(code=CRYPTO_CODE, force=False, eval_window_days=3, min_age_days=0, limit=10)

        self.assertEqual(stats["completed"], 1)
        r = self._result()
        self.assertEqual(r.eval_status, "completed")
        self.assertEqual(r.code, CRYPTO_CODE)
        self.assertEqual(r.analysis_date, date(2024, 1, 1))
        # end_close = last forward bar close = 66000; return = (66000-60000)/60000*100
        self.assertAlmostEqual(r.start_price, 60000.0)
        self.assertAlmostEqual(r.end_close, 66000.0)
        self.assertAlmostEqual(r.stock_return_pct, 10.0)
        # day2 high=67000 >= take_profit=66000
        self.assertTrue(r.hit_take_profit)
        self.assertFalse(r.hit_stop_loss)
        self.assertEqual(r.first_hit, "take_profit")
        self.assertEqual(r.first_hit_date, date(2024, 1, 2))
        self.assertEqual(r.first_hit_trading_days, 1)
        self.assertEqual(r.outcome, "win")
        self.assertTrue(r.direction_correct)
```

- [ ] **Step 2: 运行（characterization：应 PASS）**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_backtest.py::CryptoBacktestTestCase::test_crypto_backtest_seeded_data_completed -v`
Expected: **PASS**（验证既有行为）。若 FAIL：先用 `-vv` 看实际 `BacktestResult` 字段，判断是测试 seed/断言写错还是真 bug。若是真 bug，按 spec §7 处理（独立修复 commit）；若是断言数值算错，修正断言使其忠实反映**正确**行为。

- [ ] **Step 3: （无生产实现）** 本任务不写生产代码。仅当 Step 2 暴露真 bug 时才改生产代码（spec §7）。

- [ ] **Step 4: 复核** 确认测试确实驱动了真实链路（`run_backtest` → 仓储 → `evaluate_single`），未 mock 掉被测层。

- [ ] **Step 5: Commit**

```bash
git add tests/test_crypto_backtest.py
git commit -m "test: crypto 回测端到端锁定（seeded 数据 → completed，行情/止盈命中正确）"
```

---

### Task 2: autofill 路由 crypto 日线（mock get_daily_data）

**Files:**
- Modify: `tests/test_crypto_backtest.py`

- [ ] **Step 1: 追加测试**

```python
    def test_crypto_backtest_autofill_routes_crypto_klines(self) -> None:
        """无 StockDaily 时，_try_fill_daily_data 经 get_daily_data 路由 crypto code 并持久化。"""
        self._seed_analysis()  # 仅 AnalysisHistory，无 StockDaily
        df = pd.DataFrame([
            {"date": "2024-01-01", "open": 60000.0, "high": 60500.0, "low": 59500.0, "close": 60000.0, "volume": 100.0, "amount": 6_000_000.0, "pct_chg": 0.0},
            {"date": "2024-01-02", "open": 60000.0, "high": 67000.0, "low": 60000.0, "close": 65000.0, "volume": 120.0, "amount": 7_000_000.0, "pct_chg": 8.33},
            {"date": "2024-01-03", "open": 65000.0, "high": 66000.0, "low": 64000.0, "close": 65500.0, "volume": 110.0, "amount": 7_000_000.0, "pct_chg": 0.77},
            {"date": "2024-01-04", "open": 65500.0, "high": 66500.0, "low": 64500.0, "close": 66000.0, "volume": 115.0, "amount": 7_000_000.0, "pct_chg": 0.76},
        ])
        service = BacktestService(self.db)
        with patch("data_provider.base.DataFetcherManager.get_daily_data", return_value=(df, "binance")) as mocked:
            stats = service.run_backtest(code=CRYPTO_CODE, force=False, eval_window_days=3, min_age_days=0, limit=10)

        # get_daily_data 被以 crypto code 调用
        self.assertTrue(mocked.called)
        called_code = mocked.call_args.kwargs.get("stock_code") or (mocked.call_args.args[0] if mocked.call_args.args else None)
        self.assertEqual(called_code, CRYPTO_CODE)
        # 补数已持久化到 StockDaily
        with self.db.get_session() as session:
            saved = session.query(StockDaily).filter(StockDaily.code == CRYPTO_CODE).count()
        self.assertGreaterEqual(saved, 4)
        # 结果完成
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(self._result().eval_status, "completed")
```

- [ ] **Step 2: 运行（应 PASS）**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_backtest.py::CryptoBacktestTestCase::test_crypto_backtest_autofill_routes_crypto_klines -v`
Expected: **PASS**。
排障提示：`_try_fill_daily_data` 内 `from data_provider.base import DataFetcherManager` 后 `DataFetcherManager().get_daily_data(...)`——patch 目标 `data_provider.base.DataFetcherManager.get_daily_data`（patch 类方法，任何实例调用都返回 mock）。若 `call_args.kwargs` 取不到（实参以位置传），断言行已 fallback 到 `args[0]`；生产代码当前以关键字 `stock_code=code` 传，故 kwargs 分支命中。

- [ ] **Step 3: （无生产实现）** 同 Task 1。

- [ ] **Step 4: 复核** 确认 mock 只替换了网络边界 `get_daily_data`，`_try_fill_daily_data`/`save_daily_data`/`run_backtest` 均真实运行。

- [ ] **Step 5: Commit**

```bash
git add tests/test_crypto_backtest.py
git commit -m "test: crypto 回测补数路径锁定（get_daily_data 以 crypto code 路由并持久化）"
```

---

### Task 3: insufficient-when-no-data（优雅降级锁定）

**Files:**
- Modify: `tests/test_crypto_backtest.py`

- [ ] **Step 1: 追加测试**

```python
    def test_crypto_backtest_insufficient_when_no_data(self) -> None:
        """无 StockDaily 且补数返回空 → 优雅降级为 insufficient_data（非崩溃）。"""
        self._seed_analysis()  # 仅 AnalysisHistory
        service = BacktestService(self.db)
        with patch("data_provider.base.DataFetcherManager.get_daily_data", return_value=(pd.DataFrame(), "")):
            service.run_backtest(code=CRYPTO_CODE, force=False, eval_window_days=3, min_age_days=0, limit=10)
        self.assertEqual(self._result().eval_status, "insufficient_data")
```

- [ ] **Step 2: 运行（应 PASS）**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_backtest.py::CryptoBacktestTestCase::test_crypto_backtest_insufficient_when_no_data -v`
Expected: **PASS**（空 df → `_try_fill` 提前 return → start_daily 仍 None → `insufficient_data` 行被保存）。

- [ ] **Step 3: （无生产实现）** 同上。

- [ ] **Step 4: 运行整文件确认 3 条全过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_backtest.py -v`
Expected: **3 passed**。

- [ ] **Step 5: Commit**

```bash
git add tests/test_crypto_backtest.py
git commit -m "test: crypto 回测无数据优雅降级锁定（insufficient_data）"
```

---

### Task 4: 文档（crypto-guide 回测节 + CHANGELOG）

**Files:**
- Modify: `docs/crypto-guide.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: crypto-guide 新增「回测」节** —— 在 `docs/crypto-guide.md` 的「## 加密市场宏观指标」节之后、`## 9. 已知限制（后续阶段）` 之前插入：

```markdown
## 回测

crypto symbol 走与股票**相同**的回测流程，无需独立配置或日历：

- 回测对象是历史 `AnalysisHistory` 分析记录；crypto 分析记录（code 含 `/`，如 `BTC/USDT`）自动纳入回测候选（无市场过滤）。
- 历史日线来自既有 crypto K 线抓取器（Binance/OKX/Coinbase，`interval=1d`），缺数据时由回测自动补取并存入 `StockDaily`。
- 回测引擎对 `AnalysisHistory` 的 `operation_advice` / `stop_loss` / `take_profit`，在分析日之后的前向日线上评估方向正确性、止盈/止损命中与收益，与股票完全一致。
- crypto 为 7×24，每个日历日皆交易日，故 `first_hit_trading_days` 即「命中所需日历日数」（对 crypto 等于交易日数）。
- 失败降级与股票一致：取不到起始/前向数据时记 `insufficient_data`，不影响其余回测。
```

- [ ] **Step 2: CHANGELOG** —— 在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段（扁平、独立一行）新增：

```markdown
- [文档] 文档化并以端到端测试锁定 crypto 回测支持（沿用现有回测引擎与 crypto 日线数据源，无新增运行时/配置）
```

- [ ] **Step 3: 核对** 确认措辞与实仓一致（回测走 `AnalysisHistory`、crypto 日线源、`insufficient_data` 降级、`first_hit_trading_days` 语义）。Docs only, tests not run。

- [ ] **Step 4: Commit**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: crypto 回测节与 CHANGELOG（沿用现有引擎/数据源）"
```

---

### Task 5: 收尾 —— 全量离线回归 + ci_gate

**Files:** 无（仅验证）

- [ ] **Step 1: 全量离线回归**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`
Expected: 全绿（在 2869 基线上 +3 本特性用例）。

- [ ] **Step 2: ci_gate**

Run: `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh`
Expected: `all checks passed`。

- [ ] **Step 3: （无 commit）** 验证任务。若回归暴露问题，回对应 Task 修正后重跑。

---

## Self-Review

**1. Spec coverage（spec → task）：**
- §4 测试 1 seeded-completed → Task 1 ✓
- §4 测试 2 autofill-routes-crypto → Task 2 ✓
- §4 测试 3 insufficient-when-no-data → Task 3 ✓
- §6 文档（crypto-guide 回测节 + CHANGELOG）→ Task 4 ✓
- §5 错误处理（insufficient_data 降级）→ Task 3 锁定 ✓
- §7 应急（测试暴露 bug 则修）→ 每个测试任务 Step 2 排障指引 + characterization 框定 ✓
- §8 分支/回滚 → header + 各 commit ✓
- §9 范围边界（零引擎/Web/API/config 改动）→ 全程仅 tests + docs，无生产代码任务 ✓

**2. Placeholder scan：** 无 TBD/TODO；每条测试均给出完整可运行代码与确切命令。Task 1/2/3 的「无生产实现」是有意的（characterization 测试锁定既有行为），非占位。

**3. Type/signature consistency：**
- `CRYPTO_CODE="BTC/USDT"`、`_seed_analysis`/`_seed_daily`/`_result` helper 在三个测试中引用一致。
- `BacktestService(self.db).run_backtest(code=, force=, eval_window_days=, min_age_days=, limit=)` 签名与 `test_backtest_service.py` 既有用法一致。
- 断言字段（`eval_status`/`stock_return_pct`/`hit_take_profit`/`first_hit`/`first_hit_trading_days`/`first_hit_date`/`outcome`/`direction_correct`）与 `BacktestResult` 模型及既有 `test_result_fields_correct` 用法一致。
- patch 目标 `data_provider.base.DataFetcherManager.get_daily_data` 与 `_try_fill_daily_data` 的实际导入/调用一致。
