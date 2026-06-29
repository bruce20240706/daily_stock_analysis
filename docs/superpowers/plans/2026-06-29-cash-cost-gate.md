# 盘中回测 cash 成本误扣修复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让盘中回测成本后处理只对确有成交(`simulated_entry_price is not None`)的样本计征,修复 cash 仓在 `fee/slip>0` 时被误扣成负收益的 bug。

**Architecture:** 在既有成本后处理块的 `if _fee or _slip or _stamp:` 追加"确有成交"条件(1 行,门控留在调用方);cash(entry=None)跳过,long/perp short(entry=start_price)照常 round-trip。纯后端单点改动 + 参数化回归测试 + 文档。

**Tech Stack:** Python(pytest)。无前端改动。

## Global Constraints

- **门控语义**:成本仅对**确有成交**计征——`long` 与 perp `short` 照常扣 `fee/slip`(×2);**仅 cash**(无成交、`simulated_entry_price=None`)豁免。**禁止**写成 long-only(会错误豁免 perp short 的真实 round-trip)。
- **门控基准**:`evaluation.get("simulated_entry_price") is not None`(不可用 position 字符串比较替代——entry-based 对未来 position 取值更健壮,且与生产引擎 `cash→None` 语义一致)。
- **默认 0 字节级不变**:条件追加在 `_fee or _slip or _stamp` **之后**,默认全 0 时 Python 短路、entry 条件不被求值、整块跳过。
- **仅盘中路径**:改动只在 `if intraday:` 块内;日线 `else` 分支本就无成本,不动。
- **印花税门控不动**:`market == "cn" and position == "long"` 子门控保持原样。
- **浮点断言**一律 `pytest.approx(...)`。
- **commit message**:英文类型前缀 + 中文体,不加 `Co-Authored-By`,不加工具/agent 前缀。

**关联 spec:** `docs/superpowers/specs/2026-06-29-cash-cost-gate-design.md`(经对抗式审查 4 视角核验,0 Blocker)。

---

## 文件结构(改动面)

| 文件 | 职责 | 改动 |
|---|---|---|
| `src/services/backtest_service.py` | 盘中成本后处理 | `:313` 的 `if` 追加 entry 条件(+注释) |
| `tests/test_backtest_service_intraday.py` | 后处理集成测 | 加参数化 `test_cost_charged_only_when_filled`(F1–F4) |
| `docs/intraday-backtest.md` | 盘中回测专题 | §5 成本小节补"成本仅对确有成交计征(cash 不计)" |
| `docs/CHANGELOG.md` | 变更日志 | `[Unreleased]` 扁平 `[修复]` 条目 |

**单任务**:1 行 gate + 1 参数化测试 + 2 处文档构成一个内聚交付,审查者无法在不连带其它部分的情况下单独否决其一,故不拆分。

---

### Task 1: cash 成本门控修复 + F1–F4 回归 + 文档

**Files:**
- Modify: `src/services/backtest_service.py:313`
- Test: `tests/test_backtest_service_intraday.py`(在 `test_stamp_duty_gate` 之后、`# ---- Finding #1: validate_interval ----` 注释块之前插入)
- Docs: `docs/intraday-backtest.md:112`(§5 段尾)、`docs/CHANGELOG.md`(`## [Unreleased]` 段首)

**Interfaces:**
- Consumes: 既有 helper `_make_intraday_svc_with_engine_return(monkeypatch, tmp_path, engine_return_pct, fee_bps=0.0, slippage_bps=0.0, *, code="BTC/USDT:PERP", position="long", stamp_bps=0.0)`——其 mock 引擎按 `position` 设 `simulated_entry_price = None if position=="cash" else 100.0`(与生产 `backtest_engine` 一致),故 cash 用例忠实触发门控。
- 既有 `apply_round_trip_cost(return_pct, fee_bps, slippage_bps, sell_side_bps=0.0)` 不变。

- [ ] **Step 1: 写失败测试**(在 `tests/test_backtest_service_intraday.py` 的 `test_stamp_duty_gate` 函数结束后、`# -----...Finding #1` 注释块之前插入)

```python
@pytest.mark.parametrize(
    "label,code,position,engine_return,stamp_bps,fee_bps,slip_bps,expected",
    [
        # F1 cn + cash:无成交,fee/slip 不应计征 → 维持 0.0(改前 -0.20)
        ("F1_cn_cash",     "600519",        "cash",  0.0, 0.0, 5.0, 5.0, 0.0),
        # F2 crypto + cash:跨市场一致,无成交不计 → 0.0(改前 -0.20)
        ("F2_crypto_cash", "BTC/USDT:PERP", "cash",  0.0, 0.0, 5.0, 5.0, 0.0),
        # F3 perp short:真实 round-trip,照常扣 2*(5+5)/100=0.20 → 9.80(锁定非 long-only)
        ("F3_perp_short",  "BTC/USDT:PERP", "short", 10.0, 0.0, 5.0, 5.0, 9.80),
        # F4 long:照常扣 → 9.80(显式回归)
        ("F4_long",        "BTC/USDT:PERP", "long",  10.0, 0.0, 5.0, 5.0, 9.80),
    ],
)
def test_cost_charged_only_when_filled(monkeypatch, tmp_path, label, code, position, engine_return, stamp_bps, fee_bps, slip_bps, expected):
    """成本仅对确有成交计征:cash(无成交、entry=None)豁免 fee/slip;long/perp short 照常 round-trip。"""
    svc, saved_results = _make_intraday_svc_with_engine_return(
        monkeypatch, tmp_path, engine_return_pct=engine_return,
        fee_bps=fee_bps, slippage_bps=slip_bps,
        code=code, position=position, stamp_bps=stamp_bps,
    )
    out = svc.run_backtest(interval="5m", eval_window_days=1)
    assert out["completed"] == 1, f"{label}: expected completed=1, got {out}"
    assert len(saved_results) == 1, f"{label}: expected 1 saved, got {len(saved_results)}"
    r = saved_results[0]
    assert r.simulated_return_pct == pytest.approx(expected), (
        f"{label}: expected simulated_return_pct≈{expected}, got {r.simulated_return_pct}"
    )
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest "tests/test_backtest_service_intraday.py::test_cost_charged_only_when_filled" -v`
Expected: **F1_cn_cash 与 F2_crypto_cash FAIL**(改前 cash 被扣 `0.0 − 2×(5+5)/100 = −0.20` ≠ 0.0);**F3_perp_short 与 F4_long PASS**(short/long 本就计征 9.80,门控前后一致)。

> 用绝对路径的 venv 解释器(从仓库/worktree 根运行):`/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`(若在无空格 worktree,仍指向该 venv 的 site-packages,导入 worktree 的 src)。

- [ ] **Step 3: 改实现(C1 门控,`src/services/backtest_service.py:313`)**

把:

```python
                    if _fee or _slip or _stamp:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp
                        )
```

改为:

```python
                    # 成本仅对确有成交计征:cash 仓(无成交、entry=None)豁免;long/short 照常 round-trip
                    if (_fee or _slip or _stamp) and evaluation.get("simulated_entry_price") is not None:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp
                        )
```

- [ ] **Step 4: 运行验证通过(含既有成本/印花税测不回归)**

Run: `.venv/bin/python -m pytest "tests/test_backtest_service_intraday.py::test_cost_charged_only_when_filled" "tests/test_backtest_service_intraday.py::test_stamp_duty_gate" "tests/test_backtest_service_intraday.py::test_cost_zero_keeps_return_unchanged" "tests/test_backtest_service_intraday.py::test_cost_positive_deducts_round_trip" -v`
Expected: 全 PASS(F1–F4 全绿;`test_stamp_duty_gate` 6 例、两既有成本测均不回归)。

- [ ] **Step 5: 提交代码 + 测试**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_intraday.py
git commit -m "fix: 盘中回测成本仅对确有成交计征,修复 cash 仓被 fee/slip 误扣"
```

- [ ] **Step 6: 文档 — `docs/intraday-backtest.md` §5(`:112` 段尾)**

把(§5 中 A股印花税段落末句):

```
该 knob 也可在 Web 设置页 Backtest 分类直接调整。
```

改为:

```
该 knob 也可在 Web 设置页 Backtest 分类直接调整。

**成本仅对确有成交计征**：cash 仓（无买卖成交）不扣 fee/slippage/印花税；仅 long 与 perp short（确有成交）计 round-trip 成本。
```

- [ ] **Step 7: 文档 — `docs/CHANGELOG.md`(`## [Unreleased]` 段首,扁平,无 `### 标题`)**

在 `## [Unreleased]` 行之后、现有首条 `- [新功能] A股盘中回测支持卖出单边印花税…` 之前,插入一行:

```
- [修复] 盘中回测成本仅对确有成交计征：cash 仓（无成交）不再被 fee/slippage 误扣成负收益（此前 fee/slip>0 时 cash 样本 simulated_return_pct 被算成 0−成本，并经 avg_simulated_return_pct 聚合污染均值）；long/perp short 照常 round-trip；默认 0 字节级不变；历史聚合需重跑 run_backtest(force=True) 订正
```

- [ ] **Step 8: 核对 + 提交文档**

Run: `grep -n "成本仅对确有成交计征" docs/intraday-backtest.md docs/CHANGELOG.md`
Expected: 两文件各命中(intraday-backtest.md §5 + CHANGELOG [修复] 条目)。

```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md
git commit -m "docs: 盘中回测成本仅对确有成交计征(cash 不计) + CHANGELOG"
```

---

## 全量门禁(任务完成后)

- [ ] **后端 ci_gate**

Run: `./scripts/ci_gate.sh`(venv 在 PATH;或 `PATH="<venv>/bin:$PATH" ./scripts/ci_gate.sh`)
Expected: flake8 0 error;`pytest -m "not network"` 全绿,passed 数 = 基线 + 4(F1–F4)。
- **无前端改动 → 不需 web-gate。**

---

## Self-Review(plan vs. spec)

**1. Spec 覆盖:**
- §2 C1(entry 门控)→ Task 1 Step 3 ✅
- §4 F1–F4(cash 豁免 / short 仍计征 / long 回归,`pytest.approx`,stamp_bps=0 显式)→ Step 1 ✅
- §3 行为/兼容(默认字节级不变 / 历史聚合 force 重跑)→ Step 7 CHANGELOG 文案 ✅
- §5 文件清单 → 文件结构表全覆盖 ✅
- §7 YAGNI(不动日线/印花税/不加 entered 参)→ 计划无相关改动 ✅

**2. Placeholder 扫描:** 无 TBD/TODO;每改码步给出完整 old→new + 确切命令/预期。

**3. 类型/命名一致性:** 门控用 `evaluation.get("simulated_entry_price") is not None`(spec D2 一致);测试名 `test_cost_charged_only_when_filled`(spec §4 一致);参数元组序 `(label,code,position,engine_return,stamp_bps,fee_bps,slip_bps,expected)` 与既有 `test_stamp_duty_gate` 逐字一致;F1/F2 改前 −0.20 / 改后 0.0、F3/F4 = 9.80 经成本公式核对。
