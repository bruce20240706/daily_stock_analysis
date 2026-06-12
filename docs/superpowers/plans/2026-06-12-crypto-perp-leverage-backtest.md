# perp 杠杆情景回测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec `docs/superpowers/specs/2026-06-11-crypto-perp-leverage-backtest-design.md` 落地 perp 杠杆情景回测——杠杆作为情景参数（非 AI 建议），强平按保守口径模拟，结果以 `engine_version` 标签 `v1-xN` 与 1x 数据隔离共存，零 schema 变更。

**Architecture:** 引擎 `evaluate_single` 增 `leverage` kwarg，仅 `is_perp and leverage > 1` 进杠杆分支（独立扫描首个触强平 bar 序号，与基线出场 bar 序号按日序对账；非强平行收益 ×L 并钳 ≥ −100%）；service 解析/钳制 leverage、注入 `v1-xN` 标签、两层 perp 过滤（repo SQL 粗滤 + 循环内 `is_perp_code` 兜底）；读路径（service 两方法 + 三个 GET 端点）增可选 `engine_version` 参数修复查询面 blocker；每日自动回测入口显式钉 `leverage=1`。

**Tech Stack:** Python（SQLAlchemy / FastAPI / pydantic / unittest+pytest）、React+vitest（仅一行标签 + 一条用例）、零 DB 迁移。

---

## 执行前提（开工前一次性完成，不计入任务提交）

- [ ] **分支手术**（沿用既往子项目模式：spec 提交随新分支走，前一分支恢复到交付 tip）：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git switch -c feat/crypto-perp-leverage-backtest          # 当前 tip 19436fc（含两个 spec 提交）
git branch -f feat/binance-fapi-derivatives-fallback 81d662f
git log --oneline -3                                       # 确认新分支 tip 仍为 19436fc
```

- 仓库路径含空格，**所有命令必须带引号**；Python 一律用 `.venv/bin/python`。
- **锁套件（全程零改动，新用例只进新文件）**：`tests/test_backtest_engine.py`、`tests/test_crypto_backtest.py`、`tests/test_backtest_summary.py`、`tests/test_perp_backtest_engine.py`。
- 不执行 `git push`/`git tag`；commit message 英文类型前缀+中文描述，不加 Co-Authored-By。

---

### Task 1: 引擎杠杆分支（强平判定 / 收益放大 / 钳制）

**Files:**
- Create: `tests/test_perp_leverage_backtest.py`
- Modify: `src/core/backtest_engine.py`（`evaluate_single` 签名 :177-189、perp 收益块之后 :279-281 附近、新增私有 helper）

背景（已核实的现状）：`evaluate_single` 全 kwargs，`funding_cost_pct: float = 0.0` 是最后一个参数（:188）；`_evaluate_targets` 返回 7 元组在 :245-259 解包，其中 `first_hit_days` 是命中 bar 的 1-based 序号（仅 SL/TP/ambiguous 出场时非 None）；perp 收益块 :261-281（long 减 funding；short 持有至窗口末 `window_end_short`；cash 0.0）。杠杆分支放在该块**之后**、return dict 之前——`_evaluate_targets` 与既有收益块零改动，保证 L=1 字节级一致。

- [ ] **Step 1: 写失败测试**（新文件，沿用 `tests/test_perp_backtest_engine.py` 的 Bar/_bars 形态）：

```python
# -*- coding: utf-8 -*-
"""perp 杠杆情景回测：引擎强平/放大分支、repo perp_only 粗滤、service 标签隔离、API 参数面。
L=1 与现货回归见锁套件（test_backtest_engine / test_crypto_backtest / test_backtest_summary / test_perp_backtest_engine，零改动）。"""
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from unittest.mock import patch

from src.config import Config
from src.core.backtest_engine import BacktestEngine, EvaluationConfig, OVERALL_SENTINEL_CODE
from src.repositories.backtest_repo import BacktestRepository
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, BacktestSummary, DatabaseManager, StockDaily

PERP_CODE = "BTC/USDT:PERP"


@dataclass
class Bar:
    date: date
    high: float
    low: float
    close: float


def _bars(start: date, closes, highs=None, lows=None):
    highs = highs or closes
    lows = lows or closes
    return [Bar(date=start + timedelta(days=i + 1), high=highs[i], low=lows[i], close=closes[i])
            for i in range(len(closes))]


def _eval(advice="买入", *, bars, leverage=None, stop_loss=None, take_profit=None,
          funding=0.0, is_perp=True, start_price=100.0):
    kwargs = dict(
        operation_advice=advice, analysis_date=date(2024, 1, 1), start_price=start_price,
        forward_bars=bars, stop_loss=stop_loss, take_profit=take_profit,
        config=EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0),
        is_perp=is_perp, funding_cost_pct=funding,
    )
    if leverage is not None:
        kwargs["leverage"] = leverage
    return BacktestEngine.evaluate_single(**kwargs)


class LeverageEngineTestCase(unittest.TestCase):
    def test_l1_explicit_equals_default_per_field(self):
        # L=1 不变量（最高优先级）：显式 leverage=1 与不传 leverage 输出逐字段相等（long/short/cash 三态）
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        cases = [
            ("买入", dict(stop_loss=95.0, take_profit=110.0, funding=0.5)),
            ("卖出", dict(funding=0.3)),
            ("观望", dict(funding=0.9)),
        ]
        for advice, extra in cases:
            with self.subTest(advice=advice):
                self.assertEqual(_eval(advice, bars=bars, leverage=1, **extra),
                                 _eval(advice, bars=bars, **extra))

    def test_l1_short_does_not_liquidate_at_double_entry(self):
        # L=1 门控判别：1x 做空理论爆仓点 +100%（high≥2×entry）不模拟强平（与 E 一致）；
        # 若门控误写 leverage >= 1，本用例即挂（liq_short=200，high 210 会被误判强平）
        bars = _bars(date(2024, 1, 1), [95, 90, 85], highs=[210, 95, 90], lows=[94, 88, 84])
        res = _eval("卖出", bars=bars, leverage=1, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], (100 - 85) / 100 * 100 + 0.3)

    def test_long_liquidation_overrides_later_tp(self):
        # L=4 → liq=75；bar1 low 74 触强平，bar2 才触 TP=110 → 强平（已出场，后续 TP 不改结果）
        bars = _bars(date(2024, 1, 1), [80, 85, 90], highs=[95, 110, 112], lows=[74, 80, 85])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        self.assertAlmostEqual(res["simulated_entry_price"], 100.0)
        # 预测质量字段维持既有逻辑（不感知强平）：TP 在 bar2 命中照旧记录
        self.assertTrue(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "take_profit")
        self.assertEqual(res["first_hit_trading_days"], 2)

    def test_long_exit_before_liq_touch_is_not_liquidated(self):
        # 判别用例（整窗 min/max 误实现的唯一可挂点）：bar1 触 TP 出场，bar2 才跌穿强平线 75
        # → 止盈放大 4×10% − 4×0.5 = 38.0，非 liquidated
        bars = _bars(date(2024, 1, 1), [105, 80, 78], highs=[111, 95, 90], lows=[90, 70, 72])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "take_profit")
        self.assertAlmostEqual(res["simulated_exit_price"], 110.0)
        self.assertAlmostEqual(res["simulated_return_pct"], 38.0)

    def test_same_bar_liq_and_tp_prefers_liquidation(self):
        # 同 bar 双触（bar1 high 111≥TP 且 low 74≤liq 75）→ 保守强平优先
        bars = _bars(date(2024, 1, 1), [100, 100, 100], highs=[111, 100, 100], lows=[74, 99, 99])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_same_bar_liq_and_sl_prefers_liquidation(self):
        # SL=80 > liq=75：触强平的 bar 必同触 SL（杠杆强平最常见形态）→ 强平优先，非 stop_loss
        bars = _bars(date(2024, 1, 1), [85, 90, 95], highs=[100, 95, 96], lows=[74, 85, 90])
        res = _eval("买入", bars=bars, leverage=4, stop_loss=80.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        # 预测字段照旧：SL 命中记录不变
        self.assertTrue(res["hit_stop_loss"])
        self.assertEqual(res["first_hit"], "stop_loss")

    def test_short_liquidation_on_high_touch(self):
        # L=4 → liq_short=125；bar1 high 126 → 强平
        bars = _bars(date(2024, 1, 1), [120, 110, 105], highs=[126, 115, 108], lows=[110, 105, 100])
        res = _eval("卖出", bars=bars, leverage=4, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 125.0)

    def test_short_window_end_amplification(self):
        # 未触强平（liq_short=133.33，high≤99）：L×(entry−end)/entry×100 + L×funding = 3×10 + 3×0.6 = 31.8
        bars = _bars(date(2024, 1, 1), [95, 92, 90], highs=[99, 96, 93], lows=[94, 91, 89])
        res = _eval("卖出", bars=bars, leverage=3, funding=0.6)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], 31.8)

    def test_clamp_at_minus_100_without_liq_touch(self):
        # 近似声明④判别：价格未触强平线（liq_short=133.33，high≤132）但负资金费拖穿
        # 3×(−30) + 3×(−5) = −105 → 钳制输出恰 −100.0
        bars = _bars(date(2024, 1, 1), [120, 125, 130], highs=[125, 128, 132], lows=[115, 120, 126])
        res = _eval("卖出", bars=bars, leverage=3, funding=-5.0)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_non_perp_ignores_leverage(self):
        # is_perp=False + leverage=5 → 与不传 leverage 完全一致（bar1 low 74 若误进杠杆分支会被判强平）
        bars = _bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[74, 105, 106])
        self.assertEqual(
            _eval("买入", bars=bars, leverage=5, take_profit=110.0, is_perp=False),
            _eval("买入", bars=bars, take_profit=110.0, is_perp=False),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py -v
```

预期：除 `test_l1_*` 外全部 FAIL/ERROR，错误为 `TypeError: evaluate_single() got an unexpected keyword argument 'leverage'`（带 leverage kwarg 的用例）。若 `test_l1_explicit_equals_default_per_field` 因同样 TypeError 失败也属预期。

- [ ] **Step 3: 实现引擎分支**

3a. `evaluate_single` 签名（:188 `funding_cost_pct: float = 0.0,` 之后）加一行：

```python
        leverage: int = 1,
```

3b. docstring Notes 末尾补一行：

```python
        - Leverage (perp only, leverage > 1): liquidation is touch-based per bar
          (conservative), liq price excludes MMR (slightly optimistic), funding
          stops accruing after liquidation, non-liquidated PnL is clamped >= -100%.
```

3c. 在 cash 分支（`else:  # cash` 块，:279-281）之后、`return {` 之前插入杠杆分支：

```python
        # 杠杆情景分支（仅 is_perp 且 L>1；L=1 与非 perp 完全不进入，保证既有路径字节级一致）
        leverage_int = int(leverage or 1)
        if is_perp and leverage_int > 1 and position in ("long", "short"):
            if position == "long":
                liq_price = start_price * (1.0 - 1.0 / leverage_int)
                liq_touch_idx = cls._first_liq_touch_idx(window_bars, liq_price=liq_price, side="long")
                # 基线出场 bar 序号：SL/TP/ambiguous 出场取命中 bar，否则窗口末
                if simulated_exit_reason in ("stop_loss", "take_profit", "ambiguous_stop_loss"):
                    baseline_exit_idx = first_hit_days
                else:
                    baseline_exit_idx = len(window_bars)
            else:
                liq_price = start_price * (1.0 + 1.0 / leverage_int)
                liq_touch_idx = cls._first_liq_touch_idx(window_bars, liq_price=liq_price, side="short")
                baseline_exit_idx = len(window_bars)

            if liq_touch_idx is not None and liq_touch_idx <= baseline_exit_idx:
                # 触线即强平（保守）；同 bar 与 TP/SL 双触时强平优先；强平后资金费不再计入
                simulated_exit_price = liq_price
                simulated_exit_reason = "liquidated"
                simulated_return_pct = -100.0
            elif simulated_return_pct is not None:
                if position == "long":
                    price_return_pct = (simulated_exit_price - start_price) / start_price * 100
                    simulated_return_pct = leverage_int * price_return_pct - leverage_int * funding
                else:
                    price_return_pct = (start_price - simulated_exit_price) / start_price * 100
                    simulated_return_pct = leverage_int * price_return_pct + leverage_int * funding
                simulated_return_pct = max(simulated_return_pct, -100.0)  # 保证金不可亏穿
```

3d. 类内新增私有 helper（放在 `_evaluate_targets` 之后）：

```python
    @classmethod
    def _first_liq_touch_idx(
        cls,
        window_bars: List[DailyBarLike],
        *,
        liq_price: float,
        side: str,
    ) -> Optional[int]:
        """首个触及强平价的 bar 序号（1-based，按日序）；未触及返回 None。"""
        for idx, bar in enumerate(window_bars, start=1):
            if side == "long":
                if bar.low is not None and bar.low <= liq_price:
                    return idx
            elif bar.high is not None and bar.high >= liq_price:
                return idx
        return None
```

实现要点（对账语义，禁止整窗 min/max）：long 的强平扫描独立于 `_evaluate_targets`，对账规则是 `liq_touch_idx <= baseline_exit_idx`（同 bar 含等号 = 强平优先）；TP/SL 在更早 bar 出场则不强平。short 基线即窗口末（与 E 的 `window_end_short` 覆盖同型）。long 非强平放大从 `simulated_exit_price` 重算价格收益（不能复用已扣 funding 的基线值）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py tests/test_perp_backtest_engine.py tests/test_backtest_engine.py -v
```

预期：全部 PASS（含锁套件两文件）。

- [ ] **Step 5: 提交**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add tests/test_perp_leverage_backtest.py src/core/backtest_engine.py
git commit -m "feat: perp 回测引擎杠杆情景分支（按日序对账的保守强平/收益放大/钳制，L=1 字节级不变）"
```

---

### Task 2: repo 候选 SQL 粗滤（perp_only 防饥饿）

**Files:**
- Modify: `tests/test_perp_leverage_backtest.py`（追加）
- Modify: `src/repositories/backtest_repo.py:29-66`（`get_candidates`）

背景：force=False 时 `get_candidates` 按 `(eval_window_days, engine_version)` 既有结果行 `not_in` 去重后 `order_by(created_at desc).limit(limit)`（:55-64）。非 perp 候选在 `v1-xN` 命名空间永无结果行可去重 → 每轮重扫占满 limit 配额 → perp 饥饿。`func` 已在 :14 导入。

- [ ] **Step 1: 写失败测试**（追加到测试文件；公共临时 DB 基类同时为 Task 3/4 复用）：

```python
class _TempDbTestCase(unittest.TestCase):
    """临时 DB + 配置隔离（形态同 tests/test_perp_backtest_service.py）。"""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "leverage_bt.db")
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _add_analysis(self, *, query_id, code, created_at, advice="买入"):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id=query_id, code=code, name=code, report_type="simple",
                sentiment_score=40, operation_advice=advice, trend_prediction="测试",
                analysis_summary="leverage test", created_at=created_at,
                context_snapshot=json.dumps({"enhanced_context": {"date": created_at.strftime("%Y-%m-%d")}}),
            ))
            session.commit()


class GetCandidatesPerpOnlyTestCase(_TempDbTestCase):
    def test_sql_filter_prevents_starvation(self):
        # 饥饿判别：5 条更新的股票分析 + 1 条更老的 perp，limit=3 < 股票行数。
        # 若只靠循环内跳过（无 SQL 粗滤），配额会被 3 条最新股票占满、永远选不到 perp。
        for i in range(5):
            self._add_analysis(query_id=f"s{i}", code="600519", created_at=datetime(2024, 1, 10 + i))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")

        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=3, eval_window_days=3,
            engine_version="v1-x3", force=False, perp_only=True,
        )
        self.assertEqual([r.code for r in rows], [PERP_CODE])

    def test_default_perp_only_false_keeps_existing_behavior(self):
        self._add_analysis(query_id="s0", code="600519", created_at=datetime(2024, 1, 2))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")
        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=10, eval_window_days=3,
            engine_version="v1", force=False,
        )
        self.assertEqual({r.code for r in rows}, {"600519", PERP_CODE})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py::GetCandidatesPerpOnlyTestCase -v
```

预期：`test_sql_filter_prevents_starvation` ERROR（`TypeError: ... unexpected keyword argument 'perp_only'`）；`test_default_perp_only_false_keeps_existing_behavior` PASS（现状回归）。

- [ ] **Step 3: 实现**：`get_candidates` 签名 `force: bool,` 之后加 `perp_only: bool = False,`；条件块（:44-45 `if code:` 之后）追加：

```python
            if perp_only:
                # 杠杆情景粗滤：非 perp 候选在 v1-xN 命名空间永无结果行可去重，会每轮重扫占满 limit 配额
                conditions.append(func.upper(AnalysisHistory.code).like('%:PERP'))
```

（与 `is_perp_code` 的大写 `:PERP` 后缀约定一致；quote 白名单等语义真值由 Task 3 的循环内兜底负责。）

- [ ] **Step 4: 跑测试确认通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add tests/test_perp_leverage_backtest.py src/repositories/backtest_repo.py
git commit -m "feat: 回测候选支持 perp_only SQL 粗滤（杠杆情景防非 perp 候选占满配额）"
```

---

### Task 3: service 杠杆 run + config + .env.example + 每日入口钉 1x

**Files:**
- Modify: `tests/test_perp_leverage_backtest.py`（追加）
- Modify: `src/services/backtest_service.py:33-65`（`run_backtest` 签名与 run 前置解析）、`:77-79`（循环兜底）、`:143-153`（`evaluate_single` 调用）、`:216-229`（收尾日志）
- Modify: `src/config.py:942` 附近（dataclass 字段）、`:1783` 附近（from_env）
- Modify: `.env.example:314` 附近（`# CRYPTO_DERIVATIVES_ENABLED=true` 块之后）
- Modify: `main.py:650-655`（每日自动回测调用）

- [ ] **Step 1: 写失败测试**（追加）：

```python
def _seed_perp(db, *, code=PERP_CODE):
    """perp 分析 + 起始 bar + 3 根下跌前向 bar（同 E 的 service 测试种子：做空 1x 收益 5% + funding）。"""
    with db.get_session() as session:
        session.add(AnalysisHistory(
            query_id=f"q-{code}", code=code, name=code, report_type="simple",
            sentiment_score=40, operation_advice="卖出", trend_prediction="看空",
            analysis_summary="leverage test", created_at=datetime(2024, 1, 1),
            context_snapshot=json.dumps({"enhanced_context": {"date": "2024-01-01"}}),
        ))
        session.add(StockDaily(code=code, date=date(2024, 1, 1), open=100000.0, high=100500.0, low=99500.0, close=100000.0))
        session.add_all([
            StockDaily(code=code, date=date(2024, 1, 2), open=100000.0, high=100000.0, low=97000.0, close=98000.0),
            StockDaily(code=code, date=date(2024, 1, 3), open=98000.0, high=98000.0, low=95000.0, close=96000.0),
            StockDaily(code=code, date=date(2024, 1, 4), open=96000.0, high=96000.0, low=94000.0, close=95000.0),
        ])
        session.commit()


FUNDING_PATCH = patch("data_provider.crypto_derivatives.fetch_funding_rate_history", return_value=[0.0003])  # 0.03%


class LeverageServiceTestCase(_TempDbTestCase):
    def _rows(self):
        with self.db.get_session() as session:
            return session.query(BacktestResult).order_by(BacktestResult.id).all()

    def test_leverage_run_writes_tagged_rows_coexisting_with_v1(self):
        _seed_perp(self.db)
        with FUNDING_PATCH:
            svc = BacktestService(self.db)
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)              # 1x → v1
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=3)  # 3x → v1-x3
        rows = {r.engine_version: r for r in self._rows()}
        self.assertEqual(set(rows), {"v1", "v1-x3"})                                  # 标签隔离共存，互不覆盖
        self.assertAlmostEqual(rows["v1"].simulated_return_pct, 5.03, places=4)       # E 语义不变
        # liq_short=133333 未触（max high 100000）→ 放大：3×5 + 3×0.03 = 15.09
        self.assertAlmostEqual(rows["v1-x3"].simulated_return_pct, 15.09, places=4)
        self.assertEqual(rows["v1-x3"].simulated_exit_reason, "window_end_short")

    def test_non_perp_candidate_excluded_in_leverage_run(self):
        _seed_perp(self.db)
        self._add_analysis(query_id="s1", code="600519", created_at=datetime(2024, 1, 1))
        with FUNDING_PATCH:
            stats = BacktestService(self.db).run_backtest(eval_window_days=3, min_age_days=0, limit=10, leverage=3)
        self.assertEqual([r.code for r in self._rows()], [PERP_CODE])   # 股票候选不写入
        self.assertEqual(stats["processed"], 1)                          # 统计照实反映 perp-only run

    def test_loop_guard_catches_sql_filter_leak(self):
        # 两层过滤判别：ABC/BUSD:PERP 过得了 SQL 粗滤（后缀 :PERP）但 is_perp_code 为假（quote 非 USDT/USDC）
        # → 循环内兜底跳过，不写入、不计入统计
        self._add_analysis(query_id="b1", code="ABC/BUSD:PERP", created_at=datetime(2024, 1, 1), advice="卖出")
        with FUNDING_PATCH:
            stats = BacktestService(self.db).run_backtest(eval_window_days=3, min_age_days=0, limit=10, leverage=3)
        self.assertEqual(self._rows(), [])
        self.assertEqual(stats["processed"], 0)

    def test_leverage_none_falls_back_to_config(self):
        os.environ["CRYPTO_BACKTEST_LEVERAGE"] = "3"
        Config._instance = None
        _seed_perp(self.db)
        with FUNDING_PATCH:
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        self.assertEqual(self._rows()[0].engine_version, "v1-x3")

    def test_leverage_clamped_to_bounds(self):
        _seed_perp(self.db)
        with FUNDING_PATCH:
            svc = BacktestService(self.db)
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=0)    # → 1：不打标签
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=126)  # → 125
        self.assertEqual({r.engine_version for r in self._rows()}, {"v1", "v1-x125"})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py::LeverageServiceTestCase -v
```

预期：全部 ERROR（`TypeError: run_backtest() got an unexpected keyword argument 'leverage'`；config 回退用例则因无标签断言失败）。

- [ ] **Step 3: 实现**

3a. `src/services/backtest_service.py` —— `run_backtest` 签名 `limit: int = 200,` 之后加 `leverage: Optional[int] = None,`；`min_age_days` 解析（:47）之后、`engine_version` 读取（:49）处改为：

```python
        if leverage is None:
            leverage = getattr(config, "crypto_backtest_leverage", 1)
        try:
            leverage = int(leverage)
        except (TypeError, ValueError):
            leverage = 1
        leverage = max(1, min(125, leverage))  # service 兜底钳制（API Field 已拒越界，config 已钳下限）
        perp_only = leverage > 1

        engine_version = str(getattr(config, "backtest_engine_version", "v1"))
        if perp_only:
            # 情景标签：去重、落库、汇总全程使用标签版本，与 1x 行按唯一键隔离共存
            engine_version = f"{engine_version}-x{leverage}"
            logger.info(f"杠杆情景回测: L={leverage}（仅 perp 候选, engine_version={engine_version}）")
```

（原 :49 `engine_version = getattr(...)` 一行被上述块取代；后续 `str(engine_version)` 用法不变。）

3b. `get_candidates` 调用（:58-65）追加 `perp_only=perp_only,`。

3c. 循环兜底——`results_to_save`/`is_perp_code` import（:73-75）之后加 `skipped_non_perp = 0`，循环首两行（:77-78）改为：

```python
        for analysis in candidates:
            if perp_only and not is_perp_code(analysis.code):
                # SQL 粗滤漏网兜底（如 quote 非 USDT/USDC 的构造码）：杠杆情景只评估 perp
                skipped_non_perp += 1
                continue
            processed += 1
```

3d. `evaluate_single` 调用（:143-153）追加 `leverage=leverage,`（恒传；引擎按 `>1` 门控，L=1 行为不变）。

3e. return 前（:223 之前）加：

```python
        if skipped_non_perp:
            logger.info(f"杠杆情景回测跳过非 perp 候选 {skipped_non_perp} 条")
```

3f. `src/config.py` —— dataclass 字段（:942 `binance_fapi_base_url` 之后）：

```python
    # perp 杠杆情景回测默认杠杆（1=与现状一致；仅按需运行生效，每日自动回测固定 1x；上限 125 在 service 钳制）
    crypto_backtest_leverage: int = 1
```

from_env（:1783 `binance_fapi_base_url=...` 之后）：

```python
            crypto_backtest_leverage=parse_env_int(os.getenv('CRYPTO_BACKTEST_LEVERAGE'), 1, field_name='CRYPTO_BACKTEST_LEVERAGE', minimum=1),
```

3g. `.env.example` —— `# CRYPTO_DERIVATIVES_ENABLED=true`（:314）之后追加：

```
#
# perp 杠杆情景回测默认杠杆（情景参数非 AI 建议；默认 1=与现状一致；仅 perp 标的生效，合法域 1-125）
# 仅影响按需运行（CLI --backtest / API /run 未显式传 leverage 时）；每日自动回测固定 1x，不受本项影响
# 杠杆行按 engine_version 标签（如 v1-x3）与 1x 数据隔离共存，读 API 经 engine_version 参数查询
# 注意：BACKTEST_ENGINE_VERSION 不应设为带 -xN 后缀的标签值，否则会叠出 v1-x3-x3 之类复合标签
# CRYPTO_BACKTEST_LEVERAGE=1
```

3h. `main.py` 每日自动回测（:650-655）追加显式钉死：

```python
                stats = service.run_backtest(
                    force=False,
                    eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
                    min_age_days=getattr(config, 'backtest_min_age_days', 14),
                    limit=200,
                    leverage=1,  # 每日自动回测固定 1x：v1 生产流水线对 CRYPTO_BACKTEST_LEVERAGE 免疫
                )
```

CLI `--backtest`（:891）**不改**——未显式传 leverage 时落 config 默认，即"按需运行的默认杠杆"语义（不新增 CLI flag，YAGNI）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py tests/test_perp_backtest_service.py tests/test_backtest_service.py -v && .venv/bin/python -m py_compile main.py src/config.py src/services/backtest_service.py
```

预期：全部 PASS；py_compile 无输出。

- [ ] **Step 5: 提交**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add tests/test_perp_leverage_backtest.py src/services/backtest_service.py src/config.py .env.example main.py
git commit -m "feat: 回测 service 杠杆情景 run（v1-xN 标签隔离/两层 perp 过滤/钳制，每日自动回测钉 1x）"
```

---

### Task 4: API 参数面（写 leverage + 读 engine_version）与 service 读路径

**Files:**
- Modify: `tests/test_perp_leverage_backtest.py`（追加）
- Modify: `api/v1/schemas/backtest.py:13-18`（`BacktestRunRequest`）
- Modify: `api/v1/endpoints/backtest.py`（`/run` :61-67 穿参；三个 GET :88-96/:143-148/:193-199 增 Query 参数并穿参）
- Modify: `src/services/backtest_service.py`（`get_recent_evaluations` :251-263、`get_summary` :310-321）

背景（blocker 依据，已亲核）：读路径 service 两方法均 `engine_version = str(getattr(config, "backtest_engine_version", "v1"))` 钉死；端点无该参数 → `v1-xN` 行经 API 完全不可见。repo 侧 `_build_result_conditions` 已支持 `engine_version` 精确过滤（穿参即可）。

- [ ] **Step 1: 写失败测试**（追加；TestClient 形态同 `tests/test_crypto_api_routes.py`，DB 经 `DatabaseManager.get_instance()` 单例按请求解析，`_TempDbTestCase` 的重置即生效）：

```python
class LeverageApiTestCase(_TempDbTestCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from api.app import app
        self.client = TestClient(app)

    def test_run_request_leverage_bounds_rejected(self):
        for bad in (0, 126):
            resp = self.client.post("/api/v1/backtest/run", json={"leverage": bad})
            self.assertEqual(resp.status_code, 422, f"leverage={bad} 应被 Field 校验拒绝")

    def test_run_passes_leverage_to_service(self):
        import api.v1.endpoints.backtest as backtest_ep
        captured = {}

        class _Spy:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, **kwargs):
                captured.update(kwargs)
                return {"processed": 0, "saved": 0, "completed": 0, "insufficient": 0, "errors": 0}

        with patch.object(backtest_ep, "BacktestService", _Spy):
            resp = self.client.post("/api/v1/backtest/run", json={"leverage": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["leverage"], 3)

    def _seed_tagged_results(self):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id="pq1", code=PERP_CODE, name="BTC perp", report_type="simple",
                operation_advice="卖出", created_at=datetime(2024, 1, 1),
            ))
            session.commit()
            ah_id = session.query(AnalysisHistory).filter_by(query_id="pq1").one().id
            for version, ret in (("v1", 5.0), ("v1-x3", 15.0)):
                session.add(BacktestResult(
                    analysis_history_id=ah_id, code=PERP_CODE, analysis_date=date(2024, 1, 1),
                    eval_window_days=3, engine_version=version, eval_status="completed",
                    evaluated_at=datetime(2024, 1, 10), position_recommendation="short",
                    simulated_return_pct=ret,
                ))
            session.commit()

    def test_results_engine_version_filters_and_default_unchanged(self):
        self._seed_tagged_results()
        tagged = self.client.get("/api/v1/backtest/results", params={"engine_version": "v1-x3"}).json()
        self.assertEqual(tagged["total"], 1)
        self.assertEqual(tagged["items"][0]["engine_version"], "v1-x3")
        # 默认行为不变判别：不带参数仍只看 config 基础版本 v1
        default = self.client.get("/api/v1/backtest/results").json()
        self.assertEqual(default["total"], 1)
        self.assertEqual(default["items"][0]["engine_version"], "v1")

    def test_performance_engine_version_param(self):
        with self.db.get_session() as session:
            for version in ("v1", "v1-x3"):
                session.add(BacktestSummary(
                    scope="overall", code=OVERALL_SENTINEL_CODE, eval_window_days=3,
                    engine_version=version, total_evaluations=1, completed_count=1,
                ))
            session.commit()
        tagged = self.client.get("/api/v1/backtest/performance",
                                 params={"engine_version": "v1-x3", "eval_window_days": 3})
        self.assertEqual(tagged.status_code, 200)
        self.assertEqual(tagged.json()["engine_version"], "v1-x3")
        default = self.client.get("/api/v1/backtest/performance", params={"eval_window_days": 3})
        self.assertEqual(default.json()["engine_version"], "v1")

    def test_openapi_declares_engine_version_on_all_read_endpoints(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/v1/backtest/results",
                     "/api/v1/backtest/performance",
                     "/api/v1/backtest/performance/{code}"):
            names = {p["name"] for p in paths[path]["get"]["parameters"]}
            self.assertIn("engine_version", names, f"{path} 缺 engine_version 查询参数")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py::LeverageApiTestCase -v
```

预期：`test_run_request_leverage_bounds_rejected` FAIL（未知字段被忽略，返回 200/500 而非 422）、`test_run_passes_leverage_to_service` FAIL（captured 无 leverage 键）、读参数三用例 FAIL（无 engine_version 参数 → 过滤不生效/openapi 无声明）。

- [ ] **Step 3: 实现**

3a. `api/v1/schemas/backtest.py` —— `BacktestRunRequest` 末尾（`limit` 字段后）加：

```python
    leverage: Optional[int] = Field(None, ge=1, le=125, description="perp 杠杆情景（默认取配置 CRYPTO_BACKTEST_LEVERAGE；1=与现状一致；仅 perp 标的生效）")
```

3b. `api/v1/endpoints/backtest.py` —— `/run` 的 service 调用（:61-67）追加 `leverage=request.leverage,`。

3c. 三个 GET 端点（`get_backtest_results` :88-96、`get_overall_performance` :143-148、`get_stock_performance` :193-199）的参数列表各加一行（放在 `analysis_phase` 之后）：

```python
    engine_version: Optional[str] = Query(None, min_length=1, max_length=16, description="引擎版本过滤（默认取配置基础版本，如 v1；杠杆情景行需显式传标签，如 v1-x3）"),
```

（`min_length=1` 拒绝空串——空串会让 repo 的 `if engine_version:` 判假、退化为跨版本无过滤；`max_length=16` 对齐列宽。）各自的 service 调用追加 `engine_version=engine_version,`。

3d. `src/services/backtest_service.py` —— `get_recent_evaluations` 签名 `analysis_phase` 之后加 `engine_version: Optional[str] = None,`；body 头部（:262-263）改为：

```python
        if engine_version is None:
            engine_version = str(getattr(get_config(), "backtest_engine_version", "v1"))
        else:
            engine_version = str(engine_version)
```

`get_summary` 同型修改（签名加参；:320-321 改为同样四行）。两方法后续所有 `engine_version=engine_version` 透传（含 `_get_recent_evaluations_by_phase`、`_infer_eval_window_for_query`、动态汇总 `_build_dynamic_summary`）自动使用解析后的值，无需再改。`get_global_summary`/`get_stock_summary` 等既有调用方不传新参 → None → config 现行为，零影响。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_perp_leverage_backtest.py tests/test_analysis_api_contract.py -v
```

预期：全部 PASS（contract 测试确认既有 openapi 契约未破坏）。

- [ ] **Step 5: 提交**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add tests/test_perp_leverage_backtest.py api/v1/schemas/backtest.py api/v1/endpoints/backtest.py src/services/backtest_service.py
git commit -m "feat: 回测 API 增 leverage 写参数与 engine_version 读过滤（杠杆情景行可经 API 查询）"
```

---

### Task 5: Web 强平出场标签

**Files:**
- Modify: `apps/dsa-web/src/pages/BacktestPage.tsx:76-83`（`EXIT_REASON_LABELS`）
- Modify: `apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx`（追加一条用例，复用 :51 `perpRowBase` 与 :68 `mockSingleRow`）

- [ ] **Step 1: 写失败测试**（加在 `'renders long position with take-profit exit'` 用例之后）：

```tsx
  it('renders liquidated exit reason for leverage scenario rows', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'up', actualMovement: 'down', actualReturnPct: -30, positionRecommendation: 'long', simulatedReturnPct: -100, simulatedExitReason: 'liquidated' });
    render(<BacktestPage />);

    expect(await screen.findByText('做多')).toBeInTheDocument();
    expect(screen.getByText('强平')).toBeInTheDocument();           // 新标签；未映射时会显示原文 'liquidated'
    expect(screen.getByText(/-100(\.0)?%/)).toBeInTheDocument();    // 模拟收益 −100
  });
```

- [ ] **Step 2: 跑测试确认失败**（路径含空格，npm 须在 /tmp 无空格副本执行）：

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-check/
cd /tmp/dsa-web-check && npm ci && npx vitest run src/pages/__tests__/BacktestPage.test.tsx
```

预期：新用例 FAIL（找不到文本 '强平'，因 labelFromMap 回退显示原文 'liquidated'）；其余用例 PASS。

- [ ] **Step 3: 实现**：`EXIT_REASON_LABELS`（BacktestPage.tsx:76-83）`window_end_short` 行后加：

```tsx
  liquidated: '强平',
```

- [ ] **Step 4: 跑测试确认通过**（先把改动同步回 /tmp 副本）：

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-check/
cd /tmp/dsa-web-check && npx vitest run src/pages/__tests__/BacktestPage.test.tsx && npm run lint && npm run build
```

预期：vitest 全 PASS、eslint 0 error、build 成功（web-gate 等价验证）。

- [ ] **Step 5: 提交**（在原仓库路径提交）：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/pages/BacktestPage.tsx apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx
git commit -m "feat: Web 回测出场原因新增强平标签（杠杆情景行渲染）"
```

---

### Task 6: 文档（crypto-guide 新子节 + 四处矛盾修订 + CHANGELOG）

**Files:**
- Modify: `docs/crypto-guide.md`（:77 标题、:79 括注、:84 出场原因、永续回测小节末尾新增子节、:255 已知限制）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平一行）

- [ ] **Step 1: 修订四处矛盾表述**（行号为改动前基线）：

:77 标题改为：

```markdown
### 永续回测（资金费 + 做空 + 杠杆情景）
```

:79 引言改为：

```markdown
perp 标的（`BASE/QUOTE:PERP`）回测在现货流程之上叠加永续机制（默认 1x；可选杠杆情景含强平模拟，见下）：
```

:84「Web 透出」条目中 `出场原因（做空恒为窗口期满；空仓显示"无交易"）` 改为：

```markdown
出场原因（做空默认窗口期满，杠杆情景下可为强平；空仓显示"无交易"）
```

:255 已知限制条目改为：

```markdown
- **杠杆精化**（杠杆情景回测已支持 1-125x 含保守强平；仍不做：维持保证金率（MMR）、分档杠杆、全仓模式、杠杆下做空 TP/SL、Web 杠杆选择器）。
```

- [ ] **Step 2: 永续回测小节末尾（:84 条目之后、`## 7. API 用法` 之前）新增子节**：

```markdown
#### 杠杆情景回测（v1-xN）

杠杆是**情景参数**（what-if 旋钮），不是 AI 建议：回答"若该批 perp 建议按 N 倍杠杆执行会怎样（含强平风险）"。

- **启用方式**：`CRYPTO_BACKTEST_LEVERAGE`（默认 1，合法域 1-125，仅影响按需运行——CLI `--backtest` / API `/run` 未显式传参时）或 API `POST /api/v1/backtest/run` 的 `leverage` 参数；**每日自动回测固定 1x**，不受配置影响。仅 perp 标的生效（L>1 的 run 只评估 perp 候选）。
- **强平口径（线性逐仓简化）**：`liq_long = entry×(1−1/L)`、`liq_short = entry×(1+1/L)`；窗口内逐日 bar 触线（long：`low ≤ liq`；short：`high ≥ liq`）即判强平——`simulated_return_pct = -100%`、出场原因 `liquidated`、出场价 = 强平价。
- **四条近似声明**：① 不含维持保证金率（MMR），强平价比真实略远、结果**略乐观**；② 日线无法判定 bar 内先后，触线即强平为**保守**裁定（同 bar 与 TP/SL 双触时强平优先）；③ 强平后资金费不再计入；④ 非强平行收益 = `L×方向收益 + L×带符号资金费`，钳制 ≥ −100%（保证金不可亏穿）。
- **数据隔离**：杠杆行落库 `engine_version` 标签 `v1-xN`（如 `v1-x3`），与 1x 行按唯一键共存互不覆盖；读 API（`GET /results`、`GET /performance`、`GET /performance/{code}`）默认仍只看配置基础版本，显式传 `engine_version=v1-x3` 查询情景行。`BACKTEST_ENGINE_VERSION` 不应设为带 `-xN` 后缀的标签值（否则叠出 `v1-x3-x3` 复合标签）。
- 预测质量字段（方向正确率、TP/SL 命中）不感知强平、与 1x 同口径："方向判对但被强平"并存即杠杆风险的呈现方式。
```

- [ ] **Step 3: `docs/CHANGELOG.md` 的 `[Unreleased]` 追加扁平一行**（不加 `###` 标题）：

```markdown
- [新功能] perp 回测新增杠杆情景（CRYPTO_BACKTEST_LEVERAGE/API leverage，1-125x，含保守强平模拟与收益放大；结果按 engine_version 标签 v1-xN 与 1x 隔离共存，读 API 增 engine_version 查询参数；默认 1x 行为不变，每日自动回测固定 1x，仅 perp 生效）
```

- [ ] **Step 4: 核对**：确认文中命令/配置名/端点路径与实现一致（`CRYPTO_BACKTEST_LEVERAGE`、`/api/v1/backtest/...`、`liquidated`）；本文档任务无代码测试（Docs only）。

- [ ] **Step 5: 提交**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: crypto-guide 杠杆情景回测子节与既有 1x 表述修订、CHANGELOG 条目"
```

---

### Task 7: 终验（锁套件零改动 / 全量 gate / 人工核查清单）

- [ ] **Step 1: 锁套件零改动证据 + 全绿**：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git diff 19436fc..HEAD --stat -- tests/test_backtest_engine.py tests/test_crypto_backtest.py tests/test_backtest_summary.py tests/test_perp_backtest_engine.py
# 预期：无输出（四个锁文件零改动）
.venv/bin/python -m pytest tests/test_backtest_engine.py tests/test_crypto_backtest.py tests/test_backtest_summary.py tests/test_perp_backtest_engine.py tests/test_perp_backtest_service.py tests/test_backtest_service.py tests/test_perp_leverage_backtest.py -v
# 预期：全部 PASS
```

- [ ] **Step 2: 全量后端 gate**：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh
```

预期：flake8 + pytest（not network）全绿。

- [ ] **Step 3: web-gate 等价验证**（若 Task 5 的 Step 4 已跑过且其后无 web 改动，可引用其结果）：

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-check/
cd /tmp/dsa-web-check && npm run lint && npm run build
```

- [ ] **Step 4: 人工核查清单**（spec §5「入口钉死」与边界项）：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
grep -n "leverage=1" main.py                      # 预期：每日自动回测调用处 1 行命中（CLI --backtest 处无）
grep -n "CRYPTO_BACKTEST_LEVERAGE" .env.example src/config.py   # 预期：.env.example 注释块 + config 两处
grep -rn "liquidated" src/storage.py              # 预期：simulated_exit_reason 注释可顺手补 'liquidated' 枚举（可选，不强制）
git log --oneline 19436fc..HEAD                   # 预期：6 个实现提交，无夹带改动
```

- [ ] **Step 5: 交付说明**（按 AGENTS.md 默认结构汇总：改了什么 / 为什么 / 验证情况 / 未验证项〔在线 API 实跑、Docker、桌面端〕 / 风险点〔默认零扰动、杠杆行隔离 v1-xN 可删〕 / 回滚方式〔按提交 revert，孤儿 v1-xN 行无害〕），随后进入 finishing-a-development-branch 流程（预期沿用：保持本地 + 推 fork 备份，需用户确认）。

---

## Self-Review 记录（写计划时已核）

- **Spec 覆盖**：§0/§1（两层过滤含 BUSD 漏网判别、钳制三层）→ Task 2/3/4；§2（四条声明、对账语义、禁整窗 min/max 判别用例、L=1 门控判别、字段分离）→ Task 1；§3（标签注入/入口语义/日志/查询面 blocker）→ Task 3/4；§4（config/.env/API 写读/Web）→ Task 3/4/5；§5 测试清单逐项映射（main.py 钉死为 Task 7 人工核查项）；§6 文档四处矛盾 + 新子节 + CHANGELOG → Task 6；§7 回滚 → Task 7 交付说明。
- **类型/签名一致性**：`leverage: int = 1`（引擎）vs `Optional[int] = None`（service/API）分层正确；`perp_only` 默认 False 向后兼容；`first_hit_days` 在三种命中出场下必非 None（:650-651 在 break 前赋值）。
- **数值自检**：液价 75/125/133.33、放大 38.0/31.8/15.09、清算钳 −105→−100、126→125 时 liq_short=100800 > 种子最高价 100000 不触线。
- **无占位符**：所有步骤含完整代码/命令/预期输出。
