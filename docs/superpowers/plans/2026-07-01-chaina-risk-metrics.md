# 链路A 回测风险调整指标 + 回撤(Inc 1a)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在链路A `BacktestEngine.compute_summary` 上追加一组不年化的事件序列风险指标(Sharpe/Sortino/maxDD/worst_single 等),折进现有 `diagnostics` JSON,零 schema/service/repo 改动、现有字段字节级不变。

**Architecture:** 新增纯函数 `BacktestEngine._compute_risk_metrics(completed) -> dict`,对「已完成、非 cash、有 simulated_return_pct」的收益序列(下钳 ≥ -100)算不年化 Sharpe/Sortino + 复利事件净值 maxDD;`compute_summary` 加一行把结果塞进 `diagnostics["risk_metrics"]`。落库经既有 `backtest_service:875` 整体 `json.dumps(diagnostics)` 自动完成,无需改 service/repo。

**Tech Stack:** Python `statistics`/`math`、pytest。验证 `./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`)。纯离线确定性。

## Global Constraints

> 值从 spec `docs/superpowers/specs/2026-07-01-chaina-risk-metrics-design.md` 逐字抄录;每个任务隐含本节。

- **纯追加诊断**:只加 `diagnostics["risk_metrics"]` 子键;`compute_summary` 其余逻辑/返回结构/现有 `diagnostics` 键(`eval_status`/`first_hit`)与 `avg_simulated_return_pct` 等现有 summary 字段**字节级不变**;**不改** `backtest_service`/`backtest_repo`/schema/API/Web/报告。
- **纳入总体**:`completed` 中 `(getattr(r,"position_recommendation",None) or "") != "cash"` 且 `getattr(r,"simulated_return_pct",None) is not None`(getattr 安全)。含 long 与 perp short(两市场无仓位哨兵均为字面量 `"cash"`)。
- **收益下钳(所有指标共用)**:`r_i = max(float(simulated_return_pct), -100.0)`(单笔不可亏超本金;补 engine L=1 perp 未 floor)。
- **指标(全部 `round(…, 4)`)**:`mean_return_pct=mean(r)`;`return_std_pct=样本std(ddof=1)`;`sharpe=mean/原始std`(**用未 round 的原始 std 求商,守卫也用原始 std>0**);`sortino=mean/downside_dev`,`downside_dev=sqrt(Σ_{r_i<0} r_i²/n)`;`max_drawdown_pct`=复利事件净值峰谷回撤(正 %,按 `analysis_date→code→原序` 排,getattr 容错);`equity_final_pct=(equity_final-1)*100`;`worst_single_return_pct=min(r)`;`sample=n`。
- **守卫**:`n==0 → {"sample":0, 其余 None, note}`;`sharpe`:`n>=2 且 原始 std>0` 否则 None;`sortino`:`n>=2 且 downside_dev>0` 否则 None;`return_std_pct`:`n>=2` 否则 None;`max_drawdown_pct/mean_return_pct/equity_final_pct/worst_single_return_pct`:`n>=1` 否则 None;**不返 inf/NaN**。
- **note 固定字符串**:`信号收益序列风险画像(排除 cash;收益下钳≥-100;每信号独立、窗口可重叠、无仓位管理;非真实组合 maxDD;单笔=-100 会使 maxDD 饱和 100%)`。
- **maxDD 饱和特性**:任一样本 = -100(爆仓)使 equity 归 0、此后恒 0 → maxDD 钉死 100%、equity_final 钉死 -100(忠实含义,非 bug)。
- **测试期望值一律用与实现相同的 Python `round()`(banker's)语义**;sqrt 派生量用 `pytest.approx(abs=1e-4)`。
- commit message:英文类型前缀 + 中文体,**不加** `Co-Authored-By`,不加工具/agent 前缀。
- 行号会漂移:按符号(函数/类名)定位。

---

### Task 1: `_compute_risk_metrics` 纯函数 + 单元测试

**Files:**
- Modify: `src/core/backtest_engine.py`（顶部加 `import math` / `import statistics`；在 `_average`(约 :732)或 `_compute_diagnostics`(约 :760)附近加模块级常量 `_RISK_NOTE` 与静态方法 `_compute_risk_metrics`）
- Create: `tests/test_backtest_risk_metrics.py`

**Interfaces:**
- Consumes: 形如 `BacktestResultLike` 的对象,读 `eval_status`(由调用方已筛 completed)、`position_recommendation`、`simulated_return_pct`,以及可选 `analysis_date`/`code`(getattr 容错)。
- Produces: `BacktestEngine._compute_risk_metrics(completed: list) -> dict`,键:`sample, mean_return_pct, return_std_pct, sharpe, sortino, max_drawdown_pct, equity_final_pct, worst_single_return_pct, note`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_backtest_risk_metrics.py`:

```python
# -*- coding: utf-8 -*-
"""BacktestEngine._compute_risk_metrics 单元测试(不年化风险指标 + 事件净值 maxDD)。"""
from datetime import date

import pytest

from src.core.backtest_engine import BacktestEngine


class _R:
    """轻量 duck-typed result stub(仅带风险指标需要的字段)。"""
    def __init__(self, ret, pos="long", d=None, code="X"):
        self.eval_status = "completed"
        self.position_recommendation = pos
        self.simulated_return_pct = ret
        self.analysis_date = d
        self.code = code


def _rm(rows):
    return BacktestEngine._compute_risk_metrics(rows)


def test_exact_values():
    d = [date(2024, 1, i) for i in (1, 2, 3, 4)]
    m = _rm([_R(2, d=d[0]), _R(-1, d=d[1]), _R(3, d=d[2]), _R(-2, d=d[3])])
    assert m["sample"] == 4
    assert m["mean_return_pct"] == 0.5
    assert m["worst_single_return_pct"] == -2.0
    assert m["return_std_pct"] == pytest.approx(2.3805, abs=1e-4)
    assert m["sharpe"] == pytest.approx(0.2100, abs=1e-4)
    assert m["sortino"] == pytest.approx(0.4472, abs=1e-4)
    assert m["max_drawdown_pct"] == pytest.approx(2.0, abs=1e-4)
    assert m["equity_final_pct"] == pytest.approx(1.9292, abs=1e-4)


def test_sharpe_guard_uses_raw_std_not_rounded():
    # 原始 std 微小非零(round_std=0.0000)时 sharpe 仍非 None(证守卫用原始 std)
    m = _rm([_R(1.00001), _R(1.0)])
    assert m["return_std_pct"] == 0.0
    assert m["sharpe"] is not None and m["sharpe"] != 0


def test_cash_excluded_perp_short_included():
    rows = [_R(2, pos="long"), _R(0.0, pos="cash"), _R(-3, pos="short"), _R(0.0, pos="cash")]
    m = _rm(rows)
    assert m["sample"] == 2                       # 仅 long + short,cash 不计
    assert m["mean_return_pct"] == pytest.approx(-0.5, abs=1e-9)  # (2 + -3)/2
    assert m["worst_single_return_pct"] == -3.0


def test_maxdd_ordering_determinism_and_reject_oracle():
    # 反例 oracle:选路径敏感的收益集,输入原序与日期序的 maxDD 明确不同,证实现确实按日期排序。
    # [+100,-60,-60](日期序)maxDD=84;[-60,+100,-60](输入乱序)maxDD=68。
    d1, d2, d3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
    date_order = [_R(100, d=d1), _R(-60, d=d2), _R(-60, d=d3)]
    input_order = [_R(-60, d=d2), _R(100, d=d1), _R(-60, d=d3)]   # 打乱输入,日期不变
    maxdd_by_input_naive = _naive_input_order_maxdd([-60, 100, -60])   # 68.0
    maxdd_by_date = _naive_input_order_maxdd([100, -60, -60])          # 84.0
    assert maxdd_by_input_naive != pytest.approx(maxdd_by_date, abs=1e-6)  # 乱序确会变(非 tautology)
    assert _rm(input_order)["max_drawdown_pct"] == pytest.approx(maxdd_by_date, abs=1e-4)
    assert _rm(date_order)["max_drawdown_pct"] == pytest.approx(maxdd_by_date, abs=1e-4)
    # overall 同日并列:同 date 不同 code,按 code 确定;打乱输入 original_index 不改结果
    a = [_R(10, d=d1, code="AAA"), _R(-20, d=d1, code="BBB")]
    b = [_R(-20, d=d1, code="BBB"), _R(10, d=d1, code="AAA")]
    assert _rm(a)["max_drawdown_pct"] == _rm(b)["max_drawdown_pct"]


def _naive_input_order_maxdd(returns):
    equity, peak, maxdd = 1.0, 1.0, 0.0
    for ri in returns:
        equity *= (1 + max(ri, -100.0) / 100.0)
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)
    return round(maxdd * 100, 4)


def test_guards():
    assert _rm([])["sample"] == 0
    assert _rm([])["sharpe"] is None and _rm([])["max_drawdown_pct"] is None
    one = _rm([_R(3, d=date(2024, 1, 1))])
    assert one["return_std_pct"] is None and one["sharpe"] is None and one["sortino"] is None
    assert one["mean_return_pct"] == 3.0 and one["max_drawdown_pct"] == 0.0
    flat = _rm([_R(2), _R(2), _R(2)])
    assert flat["sharpe"] is None                 # std==0
    assert flat["sortino"] is None                # 无负收益 downside_dev==0
    allpos = _rm([_R(1), _R(2), _R(3)])
    assert allpos["sortino"] is None


def test_perp_short_floor_and_maxdd_saturation():
    # L=1 perp short 原始 -200 → 下钳 -100;单笔 -100 使 maxDD 饱和 100%
    single = _rm([_R(-200, pos="short", d=date(2024, 1, 1))])
    assert single["worst_single_return_pct"] == -100.0
    assert single["max_drawdown_pct"] == pytest.approx(100.0, abs=1e-4)
    assert single["equity_final_pct"] == pytest.approx(-100.0, abs=1e-4)
    mid = _rm([_R(5, d=date(2024, 1, 1)), _R(-100, d=date(2024, 1, 2)), _R(5, d=date(2024, 1, 3))])
    assert mid["max_drawdown_pct"] == pytest.approx(100.0, abs=1e-4)
    assert mid["equity_final_pct"] == pytest.approx(-100.0, abs=1e-4)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_backtest_risk_metrics.py -v`
Expected: FAIL —— `AttributeError: type object 'BacktestEngine' has no attribute '_compute_risk_metrics'`。

- [ ] **Step 3: 加 import + 常量 + 方法**

在 `src/core/backtest_engine.py` 顶部 import 区(`import re` 附近)补:
```python
import math
import statistics
```
在 `_compute_diagnostics`(约 :760)之前加模块内常量与静态方法(置于 `BacktestEngine` 类体内、与其它 `@staticmethod` 并列):
```python
    _RISK_NOTE = (
        "信号收益序列风险画像(排除 cash;收益下钳≥-100;每信号独立、窗口可重叠、"
        "无仓位管理;非真实组合 maxDD;单笔=-100 会使 maxDD 饱和 100%)"
    )

    @classmethod
    def _compute_risk_metrics(cls, completed: List[BacktestResultLike]) -> Dict[str, Any]:
        """信号收益序列的风险画像:不年化 Sharpe/Sortino + 事件净值 maxDD。

        总体=已完成、非 cash、有 simulated_return_pct 的评估;收益下钳 ≥ -100
        (单笔不可亏超本金;补 engine L=1 perp 未 floor)。全部 round 4;除零/未定义 → None。
        详见 docs/superpowers/specs/2026-07-01-chaina-risk-metrics-design.md。
        """
        rows = [
            r for r in completed
            if (getattr(r, "position_recommendation", None) or "") != "cash"
            and getattr(r, "simulated_return_pct", None) is not None
        ]
        n = len(rows)
        if n == 0:
            return {
                "sample": 0, "mean_return_pct": None, "return_std_pct": None,
                "sharpe": None, "sortino": None, "max_drawdown_pct": None,
                "equity_final_pct": None, "worst_single_return_pct": None,
                "note": cls._RISK_NOTE,
            }

        returns = [max(float(r.simulated_return_pct), -100.0) for r in rows]
        mean_r = sum(returns) / n
        std_r = statistics.stdev(returns) if n >= 2 else None        # 样本 ddof=1
        sharpe = round(mean_r / std_r, 4) if (std_r is not None and std_r > 0) else None

        downside_sq = sum(x * x for x in returns if x < 0)
        downside_dev = math.sqrt(downside_sq / n)
        sortino = round(mean_r / downside_dev, 4) if (n >= 2 and downside_dev > 0) else None

        # maxDD:按 analysis_date→code→原序 复利事件净值(getattr 容错)
        ordered = sorted(
            enumerate(rows),
            key=lambda t: (
                getattr(t[1], "analysis_date", None) is None,
                getattr(t[1], "analysis_date", None),
                getattr(t[1], "code", "") or "",
                t[0],
            ),
        )
        equity, peak, maxdd = 1.0, 1.0, 0.0
        for _idx, r in ordered:
            ri = max(float(r.simulated_return_pct), -100.0)
            equity *= (1 + ri / 100.0)
            peak = max(peak, equity)
            maxdd = max(maxdd, (peak - equity) / peak)

        return {
            "sample": n,
            "mean_return_pct": round(mean_r, 4),
            "return_std_pct": round(std_r, 4) if std_r is not None else None,
            "sharpe": sharpe,
            "sortino": sortino,
            "max_drawdown_pct": round(maxdd * 100, 4),
            "equity_final_pct": round((equity - 1.0) * 100, 4),
            "worst_single_return_pct": round(min(returns), 4),
            "note": cls._RISK_NOTE,
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_backtest_risk_metrics.py -v`
Expected: PASS(全 7 个测试函数绿)。若某 sqrt 派生量差在第 4 位,核对公式后以 `pytest.approx(abs=1e-4)` 容差(已用),勿改公式迁就。

- [ ] **Step 5: flake8**

Run: `.venv/bin/python -m flake8 src/core/backtest_engine.py tests/test_backtest_risk_metrics.py`
Expected: 无输出。

- [ ] **Step 6: Commit**

```bash
git add src/core/backtest_engine.py tests/test_backtest_risk_metrics.py
git commit -m "feat: 链路A 回测新增不年化风险指标(Sharpe/Sortino/maxDD/worst_single),收益下钳≥-100"
```

---

### Task 2: 接线到 `compute_summary` + 集成/零回归

**Files:**
- Modify: `src/core/backtest_engine.py`（`compute_summary` 内 `diagnostics = cls._compute_diagnostics(results_list)` 之后加一行）
- Test: `tests/test_backtest_summary.py`（复用既有 `FakeRow`,追加集成测试)

**Interfaces:**
- Consumes: Task 1 的 `BacktestEngine._compute_risk_metrics(completed)`;既有 `compute_summary(*, results, scope, code, eval_window_days, engine_version) -> dict` 与其 `completed` 局部变量、`diagnostics` dict。
- Produces: `compute_summary` 返回的 `diagnostics` dict 多一个 `"risk_metrics"` 键。

- [ ] **Step 1: 写失败测试**

在 `tests/test_backtest_summary.py` 末尾(`if __name__` 之前)追加(文件已 import `unittest`、`FakeRow`、`BacktestEngine`):

```python
class RiskMetricsWiringTestCase(unittest.TestCase):
    def test_compute_summary_emits_risk_metrics_and_preserves_existing(self) -> None:
        rows = [
            FakeRow(simulated_return_pct=2.0, position_recommendation="long"),
            FakeRow(simulated_return_pct=-1.0, position_recommendation="long"),
            FakeRow(simulated_return_pct=0.0, position_recommendation="cash"),  # 应被风险指标排除
        ]
        summary = BacktestEngine.compute_summary(
            results=rows, scope="overall", code="__OVERALL__",
            eval_window_days=3, engine_version="v1",
        )
        rm = summary["diagnostics"]["risk_metrics"]
        self.assertEqual(rm["sample"], 2)                      # cash 不计
        self.assertEqual(rm["mean_return_pct"], 0.5)           # (2 + -1)/2
        self.assertIn("sharpe", rm)
        self.assertIn("max_drawdown_pct", rm)
        # 既有字段不回归:cash 仍计入 avg / 既有 diagnostics 键仍在
        self.assertIsNotNone(summary["avg_simulated_return_pct"])
        self.assertIn("eval_status", summary["diagnostics"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_backtest_summary.py::RiskMetricsWiringTestCase -v`
Expected: FAIL —— `KeyError: 'risk_metrics'`(compute_summary 尚未接线)。

- [ ] **Step 3: 接线一行**

在 `src/core/backtest_engine.py` 的 `compute_summary` 内,找到 `diagnostics = cls._compute_diagnostics(results_list)`(约 :432),其后紧接加一行:
```python
        diagnostics["risk_metrics"] = cls._compute_risk_metrics(completed)
```
（`completed` 是同函数内已有的局部变量,约 :359 定义;`_compute_diagnostics` 与 `compute_summary` 其余不动。）

- [ ] **Step 4: 跑测试确认通过 + 既有 summary 测试零回归**

Run: `.venv/bin/python -m pytest tests/test_backtest_summary.py -v`
Expected: PASS（新集成测试绿;既有 `test_trigger_rates_use_applicable_denominators` 不回归)。

- [ ] **Step 5: 全仓 grep 既有 diagnostics/compute_summary 精确断言零回归**

Run: `grep -rn "diagnostics\"\]\|\[.diagnostics.\]\|compute_summary" tests/ | grep -v test_backtest_risk_metrics`
Expected: 逐一核对命中处是否有断言 `diagnostics` **精确 dict 相等**或 keys 完全集合(会因多一 `risk_metrics` 键而红)。若有,补 `risk_metrics` 键(plan-mandated 更新,非缺陷);若均为「子键存在性/单键值」断言则不受影响。把核对结论写进报告。

- [ ] **Step 6: flake8 + Commit**

Run: `.venv/bin/python -m flake8 src/core/backtest_engine.py tests/test_backtest_summary.py`
Expected: 无输出。

```bash
git add src/core/backtest_engine.py tests/test_backtest_summary.py
git commit -m "feat: compute_summary 接线 risk_metrics 进 diagnostics（现有字段与落库不变）"
```

---

## 收尾:整批门禁

- [ ] **Step A: 跑 ci_gate**

Run: `./scripts/ci_gate.sh`
Expected: `backend-gate: all checks passed`;pytest 全绿(基线 3865 passed,本计划净增约 +8:Task1 7 函数 + Task2 1)。

- [ ] **Step B: 交付说明**

按 spec §7:改了什么 / 为什么(补风险调整+回撤画像)/ 验证情况(ci_gate + 精确值/守卫/下钳/排序测)/ 未验证项(无,离线全覆盖;crypto 真网端到端可选)/ 风险点(低,纯追加诊断、现有字段字节级不变)/ 回滚(单分支 revert,diagnostics 少一子键)。

---

## Self-Review(plan vs spec)

**1. Spec 覆盖:**
- §4.1 总体(排除 cash、含 perp short、getattr)→ Task1 `_compute_risk_metrics` 过滤 + Task1 `test_cash_excluded_perp_short_included` ✓
- §4.2 下钳/公式(mean/std/sharpe raw/sortino/maxDD 排序/worst_single/equity_final)→ Task1 实现 + `test_exact_values`/`test_perp_short_floor_and_maxdd_saturation`/`test_maxdd_ordering_determinism_and_reject_oracle` ✓
- §4.3 守卫(n=0/1、raw std、downside_dev、round4)→ Task1 `test_guards`/`test_sharpe_guard_uses_raw_std_not_rounded` ✓
- §4.4 接线(diagnostics["risk_metrics"])→ Task2 ✓
- §5 兼容(零 service/repo/schema、现有字段不变)→ Task2 集成断言 + Step5 grep ✓
- §6 测试(1 精确/2 排序反例 oracle/3 cash/4 守卫/5 下钳饱和/6 接线/7 零回归)→ Task1 Step1 六函数 + Task2 集成 + Step5 grep ✓
- §9 诚实边界(note)→ `_RISK_NOTE` 常量 ✓

**2. Placeholder 扫描:** 无 TBD/TODO;每 code step 含完整代码与确切命令/期望;期望值 sqrt 派生用 approx、clean 值精确。✓

**3. 类型一致性:** `_compute_risk_metrics(completed)->dict` 在 Task1 定义、Task2 调用一致;键名(sample/mean_return_pct/return_std_pct/sharpe/sortino/max_drawdown_pct/equity_final_pct/worst_single_return_pct/note)Task1 产出与 Task2/测试消费一致;`_naive_input_order_maxdd` 仅测试内 helper。✓
