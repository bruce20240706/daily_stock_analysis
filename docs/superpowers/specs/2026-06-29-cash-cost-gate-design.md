# 盘中回测 cash 成本误扣修复(成本仅对确有成交计征)设计

- 主题：`cash-cost-gate`
- 日期：2026-06-29
- 状态：设计定稿(经对抗式审查 4 视角读真实代码核验:核心修复 0 Blocker;§0.2/§0.3 影响面措辞已精确化,见 §8),待落实现计划
- 范围：链路A 盘中回测成本后处理；修 cash 仓被 fee/slip 误扣;**仅盘中路径**
- 关联：承接 [A股印花税](2026-06-29-ashare-stamp-duty-design.md)(§0.4 当时标为"另议",此处独立立项);「盘中/分钟级回测」epic 成本真实性收尾。

---

## 0. 背景与现状

### 0.1 当前成本后处理(已核验,`src/services/backtest_service.py:304-316`)

```python
# ★ Task 6: 分钟路径可选成本后处理（日线路径不变）
if intraday:
    from src.core.intraday_backtest import apply_round_trip_cost
    _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
    _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
    # A股印花税:卖出单边,仅 cn 且 long 仓出场计征(cash/crypto/us 不征)
    _stamp = 0.0
    if market == "cn" and evaluation.get("position_recommendation") == "long":
        _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
    if _fee or _slip or _stamp:
        evaluation["simulated_return_pct"] = apply_round_trip_cost(
            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp
        )
```

### 0.2 方向/成交模型(已核验,`src/core/backtest_engine.py`)

- 现货 `infer_position_recommendation` → 仅 `long`/`cash`，**永不返回 short**(`:275` elif 行内注释)。
- perp `infer_perp_position` → 可返回 `short`(`:161`)。
- 入场价:`long`→`start_price`(`:268`)、`short`→`start_price`(`:276`)、`cash`→`None`(`:284`)。
- 故"**确有成交**" ⟺ `simulated_entry_price is not None` ⟺ `position_recommendation != "cash"`——**仅对 `eval_status=='completed'` 样本成立**:`insufficient_data`/`error` 样本的 evaluation dict 不含 `simulated_entry_price` key(`.get()`→None),门控视其"无成交"一并跳过,等效现有行为、对落库无副作用(额外防御收益,见 §0.3)。
- cash 仓:`simulated_entry_price=None`、`simulated_return_pct=0.0`(`:283-285`)。
- 边缘:`long` 仓若窗口末 `end_close=None`,`simulated_return_pct=None` 但 `simulated_entry_price` 仍非 None(`:269-270`);该情形门控放行,`apply_round_trip_cost(None,...)` 经 None 守卫短路返回 None,与改前一致(见 §2)。

### 0.3 缺口(bug)

后处理在 `eval_status` 路由前对到达成本块的 evaluation 一律调 `apply_round_trip_cost`(无 position 门控)。cash 仓('completed'、无成交、`simulated_return_pct=0.0`、`simulated_entry_price=None`)在 `fee/slip>0`(opt-in，默认 0)时被扣成 `0.0 − 2×(fee+slip)/100`(spurious 负收益)。

**精确影响面**(已核验聚合口径,`backtest_engine.py`):
- **per-row 落库**:`backtest_results.simulated_return_pct` 落为负值,经 API/Web 逐行展示错误负收益。
- **聚合 `avg_simulated_return_pct`**(`:380` 对全量 completed 无 position 过滤,含 cash)被 cash 负值下拉,进而经 `_recompute_summaries` 污染 `BacktestSummary.avg_simulated_return_pct`(API `/backtest/summary` 与看板)。
- **不受影响**:`win_rate_pct`/`direction_accuracy_pct`(基于 outcome/direction_correct,与 `simulated_return_pct` 无关)、stop/take_profit/ambiguous_rate(`position=='long'` 门控已排除 cash)。

印花税分量已正确门控(cn+long，本就排除 cash)，**唯 fee/slip 对 cash 无门控**。**仅盘中路径**有此问题:日线 `else` 分支不施加任何成本(已核验 `apply_round_trip_cost` 仅在 `if intraday:` 下出现)。

> 注:除三处 pre-engine `continue`(无 start_daily / fetch 抛错 / forward_bars 为空)外,**引擎返回的 `insufficient_data`**(forward_bars 非空但 `< eval_days`,`backtest_engine.py:217-225`)也到达成本块,但其 evaluation 不含 `simulated_return_pct` key→`apply_round_trip_cost(None,...)` 短路返回 None,改前改后均落库 None(无害);修复后 entry 门控亦正确跳过。故"所有完成样本"应理解为"所有到达成本块的样本",cash 是其中唯一被误扣的真实样本。

### 0.4 现有测试现状(已核验)

- 无任何测试断言"cash 被 fee 扣减"的既有(错误)行为——唯一 cash 测试 `tests/test_backtest_service_intraday.py::test_stamp_duty_gate` 的 `G2_cn_cash` 用 `fee/slip=0`，不触发本 bug。
- 故**无 golden 需"反 bug 化"**；只需新增针对 `cash + fee>0` 的回归测试(改前会得负值,改后 0.0)。

---

## 1. 设计决策

- **D1 门控语义**:成本仅对**确有成交**计征——`long` 与 perp `short` 均为真实 round-trip，照常扣 `fee/slip`(×2)；**仅 cash**(无成交)豁免。否决"long-only"语义(会错误豁免 perp short 的真实回归成本)。
- **D2 门控基准**:用 `evaluation.get("simulated_entry_price") is not None`(直接信号="已投入资金"，对未来新增 position 取值健壮)。等价的 `position_recommendation != "cash"` 为备选,精度略低。
- **D3 实现位置**:在既有 `if _fee or _slip or _stamp:` **追加** entry 条件,门控留在调用方。否决给纯成本函数 `apply_round_trip_cost` 加 `entered` 参(污染其单一职责)。
- **D4 范围**:仅盘中路径(日线本就无成本);印花税门控不动(已正确);跨市场(crypto/cn/us/perp)统一受益于本修复。

---

## 2. 改动 C1:后处理加"确有成交"门控(`src/services/backtest_service.py:313`)

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

**正确性**:
- cash → `simulated_entry_price is None` → 跳过 → `simulated_return_pct` 维持 0.0(修复)。
- long → entry 非 None → 照常扣 `2×(fee+slip)/100`（+印花税若 cn）。
- perp short → entry 非 None → 照常扣 `2×(fee+slip)/100`(印花税 0,A股不可能 short)。
- 边缘(long `end_close=None` / 引擎 `insufficient_data`)→ `simulated_return_pct` 为 None 或 key 缺席:long-None 时 entry 非 None **放行**,`apply_round_trip_cost(None,...)` 经 None 守卫返回 None(与改前一致);insufficient 时 entry key 缺席 → 门控**跳过**(同样落库 None)。两者均无回归。
- 默认 0(fee/slip/stamp 全 0)→ `_fee or _slip or _stamp` 为假 → Python 短路,entry 条件不被求值 → 整块跳过 → 字节级不变。

**为何不会回归 long/short**:对完成样本，long/short 恒有 `simulated_entry_price`(`backtest_engine.py:268/276`)，entry 条件恒真，行为与改前一致。

---

## 3. 行为变更与兼容性

- 类型 **[修复]**。仅当 `fee/slip>0`(opt-in，默认 0)时，盘中 **cash 样本**的 `simulated_return_pct` 由"负的 −cost"变回 **0.0**(正确)。`long`/perp `short` 不变。
- **默认 0 → 字节级不变**(entry 条件仅在已进入成本块后追加,默认全 0 时整块仍跳过)。crypto/cn/us/日线在默认下数值不变。
- 无 DB schema 迁移、无 API 字段/枚举变化、无配置新增。对客户端无破坏。
- **历史聚合订正**:`fee/slip>0` 的既有运行已把 cash 负值算入 `BacktestSummary.avg_simulated_return_pct`;修复后**新跑**即正确,**历史聚合**需重跑 `run_backtest(force=True)` 触发 `_recompute_summaries` 订正(本特性不自动迁移历史数据,与既有"重跑订正"惯例一致)。
- 文档:`docs/intraday-backtest.md` 成本小节补"成本仅对确有成交计征(cash 不计)";`docs/CHANGELOG.md` `[修复]` 扁平条目。

---

## 4. 测试设计

新增参数化 `test_cost_charged_only_when_filled`（`tests/test_backtest_service_intraday.py`，复用既有 helper `_make_intraday_svc_with_engine_return`，已支持关键字 `code`/`position`/`stamp_bps`；`stamp_bps` 全 0 显式列出以暴露 cn+cash 的"默认 0 + cn+long 门控"双重保护;浮点断言用 `pytest.approx`）：

| 用例 | code | position | engine_return | stamp_bps | fee | slip | 期望 simulated_return_pct |
|---|---|---|---|---|---|---|---|
| **F1 cash 豁免(cn)** | 600519 | cash | 0.0 | 0 | 5 | 5 | `approx(0.0)`(改前 −0.20) |
| **F2 cash 豁免(crypto)** | BTC/USDT:PERP | cash | 0.0 | 0 | 5 | 5 | `approx(0.0)`(改前 −0.20) |
| **F3 perp short 仍计征** | BTC/USDT:PERP | short | 10.0 | 0 | 5 | 5 | `approx(9.80)`(10 − 2×(5+5)/100) |
| **F4 long 仍计征** | BTC/USDT:PERP | long | 10.0 | 0 | 5 | 5 | `approx(9.80)` |

- **F1/F2 是修复点**：必须**先写、先红**(改前 cash 被扣 −0.20),再加 C1 转绿。
- **F3 锁定语义**：perp short 是真实 round-trip，门控不得豁免它(防 D1 退化为 long-only)。
- F4 与既有 `test_cost_positive_deducts_round_trip` 语义重叠,作显式回归保留(确认 long 不受门控影响)。
- 既有 `test_cost_zero_keeps_return_unchanged`/`test_cost_positive_deducts_round_trip`/`test_stamp_duty_gate` 全部应不回归。

> 注:helper 的 mock 引擎按 `position` 设 `simulated_entry_price = None if position=="cash" else 100.0`(已就位),与生产 `backtest_engine` 的 cash→None 语义一致,故 F1/F2 的 entry=None 忠实触发门控。

### 4.1 门禁

- 后端:`./scripts/ci_gate.sh`(flake8 + `pytest -m "not network"`)全绿,记录 passed 增量(预期 +4:F1-F4)。
- 无前端改动(纯后端 + 文档)→ 不需 web-gate。

---

## 5. 文件清单

- `src/services/backtest_service.py`:后处理 `if` 加 `and evaluation.get("simulated_entry_price") is not None`(+ 注释,§2)。
- `tests/test_backtest_service_intraday.py`:加 `test_cost_charged_only_when_filled`(F1-F4,§4)。
- `docs/intraday-backtest.md`:成本小节补"成本仅对确有成交计征(cash 不计)"。
- `docs/CHANGELOG.md`:`[Unreleased]` 扁平 `[修复]` 条目。

---

## 6. 风险与回滚

- **极低**:默认 0 字节级不变(entry 条件不改默认跳过路径);long/short 行为不变(entry 恒非 None);仅 cash+成本启用时数值由错变对。
- **回滚**:纯单行逻辑 + 测试 + 文档;`git revert` 即恢复(恢复为 cash 被误扣的旧行为)。

---

## 7. 不做(YAGNI / 范围外)

- 日线路径成本(本就无成本,刻意设计)。
- 印花税门控(已正确 cn+long)。
- 给 `apply_round_trip_cost` 加 `entered` 参(D3 否决,污染纯函数职责)。
- cash 样本在其它口径的呈现调整(范围外)。注:`win_rate_pct`/`direction_accuracy_pct` 基于 outcome/direction_correct,与 `simulated_return_pct` 无关,本就不受此 bug 影响、无需另议;`avg_simulated_return_pct` 经本修复透传订正(§0.3/§3),无需单独处理。
- 过户费/佣金等其它成本分量(各自独立)。

---

## 8. 对抗式审查可追溯(2026-06-29)

4 视角并行读真实代码核验(engine 语义 / service 流 / test-golden 影响 / bug 影响面)。**核心修复 0 Blocker**;改动逻辑(cash 跳过、long/short 不变、默认短路字节级不变、仅盘中、F3 perp short 离线安全)全部 confirmed。处置:

- **engine 语义**:§0.2 全部 confirmed(现货永不 short、perp 可 short、cash→entry None+return 0.0、completed long/short 恒有 entry、非完成 dict 无此 key)。
- **service 流(refuted 1 主张)**:"所有 insufficient 均被 continue 旁路"被反例驳回——引擎返回的 `insufficient_data`(forward_bars 非空但 <eval_days)直通成本块,但 `return_pct` 缺席→None 短路无害,门控亦正确跳过。→ §0.2/§0.3 已补限定。
- **test-golden**:§0.4 影响面为零(无任何 cash+fee>0 断言数值的现有测试/golden);helper 已支持 `code`/`position`/`stamp_bps`;F1-F4 数值经代码核验正确;F3 离线全 mock 安全(perp funding 仅日线路径取数)。
- **影响面精确化(Important)**:`avg_simulated_return_pct`(`:380` 全量 completed 无门控)被污染,`win_rate_pct`/`direction_accuracy_pct`/stop-tp **不受影响**。→ §0.3 精确化、§7 订正措辞、§3 补历史聚合订正(force 重跑)。
- **Minor/Nit 已收**:long `end_close=None` 边缘(§0.2/§2)、§4 表加 `stamp_bps` 列、测试改名 `test_cost_charged_only_when_filled`、`:275` 行内注释表述。
