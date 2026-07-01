# 链路A 回测风险调整指标 + 回撤(Inc 1a)—— 设计 spec

> 日期:2026-07-01　类型:feat(追加式诊断指标,默认追加不改现有字段)
> 范围:`src/core/backtest_engine.py`(链路A 盘中/日线回测 summary 聚合)。无 config / schema 迁移 / API / Web 变化。
> 上位战略:`docs/strategy-actionable-signal-system.md` §5 Inc 1a;本 spec 只做 1a,**不含** 1b(链路B 滚动样本外)/1c(阈值 data-snooping)。

## 1. 背景与动机

链路A(操作建议 / PnL 回测)当前 summary 只有 `avg_simulated_return_pct` / `win_rate_pct` / `direction_accuracy_pct`(`backtest_engine.py:compute_summary` @346),**没有风险调整收益(Sharpe/Sortino)与回撤(maxDD)**。要把系统推向「明确买卖信号」,信号的**尾部风险画像**是必备——不知道回撤与下行波动,就无法谈仓位与止损。链路A 已按 `analysis_date` 产出 `simulated_return_pct` 序列(成本已折进),原料齐备,缺的只是派生指标。

## 2. 目标与非目标

**目标**:在链路A summary 上追加一组**不年化的事件序列风险指标**——`sharpe / sortino / max_drawdown_pct` 及辅助项,落进现有 `diagnostics` JSON,零 schema 迁移、追加式、现有字段字节级不变。

**非目标(明确不做)**:
- 不做 1b(链路B 滚动样本外稳定性)/ 1c(阈值 data-snooping 校正)—— 另立 spec。
- **不年化**(链路A 是事件样本、非等间隔时序,年化引入虚假精度)。
- **不做真实组合 maxDD**(每信号独立、窗口可重叠、无仓位管理;真实组合风险属 Inc 5 组合层)。本指标是「**信号收益序列的风险画像**」。
- 不改回测流程、不改成本模型、不改 `avg_simulated_return_pct` 及任何现有 summary 字段。
- 不加列、不迁移 schema、不改 `backtest_service` / `backtest_repo`。
- 不加 opt-in 开关(纯追加诊断、非行为变更;加 flag 属过度设计)。

## 3. 现状(改动须对齐)

- `BacktestEngine.compute_summary(*, results, scope, code, eval_window_days, engine_version) -> dict`(`backtest_engine.py:346`):
  - `results_list = list(results)`;`completed = [r for r in results_list if r.eval_status == "completed"]`(:359)。
  - `avg_simulated_return_pct = cls._average([r.simulated_return_pct for r in completed])`(:380,`_average` @732 跳过 None)。
  - `diagnostics = cls._compute_diagnostics(results_list)`(:432);返回 dict 含 `"diagnostics": diagnostics`(:457)。
- `simulated_return_pct` 单位**百分数**;`-100.0` 封顶**仅在 `leverage>1` 分支**(`:289` gate 内 `:307/:315`)——**默认 `crypto_backtest_leverage=1`,L=1 perp short(:275-282)/long(:274)不 floor,收益可 `< -100`**(见 §4.2 下钳)。cash(观望)经 cash-cost-gate 修复后 `simulated_return_pct=0.0`、无 entry。
- `BacktestResultLike` 协议(:28)含 `eval_status / position_recommendation / outcome / simulated_return_pct / ...`,**不含 `analysis_date`**。
- 落库:`backtest_service` 把 `summary["diagnostics"]` 序列化进 `BacktestSummary.diagnostics_json`;读回 `diagnostics=json.loads(...)`。**故只要把新指标塞进 `diagnostics` dict,即自动落库+回读,无需改 service/repo。**

## 4. 设计

### 4.1 纳入总体(population)

风险指标基于**实际建仓的已完成评估**:
```
series_rows = [r for r in completed
               if (r.position_recommendation or "") != "cash"
               and r.simulated_return_pct is not None]
```
- **排除 cash(观望)**:观望是「不交易」,不应向已建仓信号的风险画像注入 0 收益样本、人为压低波动/回撤。
- 这与 `avg_simulated_return_pct` 的总体(`completed` 全体,含 cash 的 0.0)**有意不同**;差异写进 `diagnostics.risk_metrics.note` 与文档。
- long 与 perp short 均含(二者都是真实持仓、有 round-trip 收益)。

### 4.2 指标定义(不年化,无风险利率=0)

**收益下钳(所有指标共用,修复 L=1 perp 未 floor 缺陷)**:单笔仓位经济上不可亏超本金 100%,故对每个样本先 floor:`r_i = max(raw_simulated_return_pct, -100.0)`。**根因**:`evaluate_single` 的 `-100` 封顶只在 `leverage>1` 分支(`backtest_engine.py:289`);**默认 `crypto_backtest_leverage=1`,L=1 perp short(:275-282)/perp long(:274)不 floor**,做空标的窗口内翻倍即产出 `simulated_return_pct<-100`(如价格 3× → -200)。若不 floor,复利 `(1+r/100)` 变负、equity 转负、maxDD>100% 且符号翻转,口径失真。风险指标层**防御式 floor、不传播、不改 engine、不影响 `avg_simulated_return_pct`**(后者仍用 raw)。

设 `r = [r_i]`(百分数,**已按上式 floor 到 ≥ -100**)为 `series_rows` 的收益;`n = len(r)`。

- `mean_return_pct = mean(r)`
- `return_std_pct = 样本标准差(ddof=1)`。实现用 `statistics.stdev(r)`(**默认即样本 ddof=1;勿传 `ddof=` 关键字——`statistics.stdev` 不接受**),或等价手算。
- `sharpe = mean(r) / 样本std(r)`(**不年化**;用**未 round 的** mean/std 求商,最后再 round,勿用 round 后的 `return_std_pct` 当除数)
- **Sortino**:目标下行半标准差(MAR=0,分母用总样本 n):
  `downside_dev = sqrt( Σ_{r_i<0} r_i^2 / n )`;`sortino = mean(r) / downside_dev`(同样用未 round 的中间量求商再 round)
- **max_drawdown_pct**:按 `analysis_date` 升序把 `r` 复利成事件净值曲线,取峰谷最大回撤(正幅度 %):
  ```
  order = sort series_rows by (getattr(r,"analysis_date",None) is None,
                               getattr(r,"analysis_date",None),
                               getattr(r,"code","") or "",     # 同日确定性次键(overall 跨标的)
                               original_index)                 # 最终兜底
  equity = 1.0; peak = 1.0; maxdd = 0.0
  for ri in ordered_returns:                                   # ri 已 floor 到 ≥ -100
      equity *= (1 + ri/100)                                   # 因子 ∈ [0, ∞):(1+(-100)/100)=0
      peak = max(peak, equity)
      maxdd = max(maxdd, (peak - equity) / peak)               # peak ≥ 1.0 恒成立 → 无除零
  max_drawdown_pct = maxdd * 100                               # ∈ [0, 100]
  ```
  - `analysis_date` 经 `getattr` 容错:全有则按日期稳定排序;缺失则回退给定顺序(`getattr(r,"code","")` 再 `original_index` 兜底,确定性)——与代码库 `getattr(row,"bar_interval","1d")` 惯例一致。
  - **overall scope 跨标的**:同一 `analysis_date` 常有多只标的多行;复利次序取「日期→code→原序」的**规约稳定序**(非真实时间序,maxDD 峰谷路径对此不做组合意义解释,见 §9),但保证**同输入 → 同输出**(可复现)。
  - `original_index` = 对 `series_rows` 过滤后 `enumerate` 的稳定下标(0..n-1),仅作最终 tie 兜底。
  - **maxDD 饱和特性(务必写进 note)**:复利模型下**任一样本 = -100(如 perp 爆仓)会把 equity 归 0**,此后 `equity *= 0` 恒为 0 → `max_drawdown_pct` 钉死 100%、`equity_final_pct` 钉死 -100。这是「逐信号全额复投」的忠实含义(单笔总亏即爆),非 bug;但含爆仓的信号集 maxDD 会饱和于 100%。故额外产出 `worst_single_return_pct` 单独暴露单笔尾部,避免与复利回撤混淆。
- 辅助:`equity_final_pct = (equity_final - 1) * 100`;`worst_single_return_pct = min(r)`;`sample = n`。

### 4.3 守卫(follow 现有 `sample==0 → None` 惯例)

- `n == 0`:`risk_metrics = {"sample": 0}`,其余键取 `None`(显式给出,便于诊断/查询)。
- `sharpe`:`n >= 2 且 未 round 的原始 std > 0` 否则 `None`(**守卫用原始 std,勿用 round 后的 `return_std_pct`——微小非零 std 会 round 成 0.0000、误触 None,而 §4.2 是用原始 std 相除**)。
- `sortino`:`n >= 2 且 downside_dev > 0`(downside_dev 为未 round 原始中间量)否则 `None`(**全为非负收益 → downside_dev=0 → None**,文档注明)。
- `return_std_pct`:`n >= 2` 否则 `None`;`max_drawdown_pct / mean_return_pct / equity_final_pct / worst_single_return_pct`:`n >= 1` 否则 `None`。
- 数值精度:**全部 `round(…, 4)`**(对齐既有 `_average` 的 round 4:`backtest_engine.py:736`),避免与顶层收益均值精度不一致。比率(sharpe/sortino)与百分数(maxDD/mean/std/equity_final)统一 4 位。
- 不返 `inf/NaN`——任何除零/未定义一律 `None`。

### 4.4 落点与接线

- 新增静态方法 `BacktestEngine._compute_risk_metrics(completed) -> dict`(职责单一、可独立测),内部按 §4.1 过滤、§4.2 计算、§4.3 守卫。
- 在 `compute_summary` 内 `diagnostics = cls._compute_diagnostics(results_list)` 之后追加一行:
  ```python
  diagnostics["risk_metrics"] = cls._compute_risk_metrics(completed)
  ```
  `_compute_diagnostics` **不改**;`compute_summary` 其余逻辑与返回结构不变(只是 `diagnostics` dict 多一个键)。
- `diagnostics.risk_metrics` 结构:
  ```json
  {"sample": 12, "mean_return_pct": 1.83, "return_std_pct": 4.10,
   "sharpe": 0.4463, "sortino": 0.7215, "max_drawdown_pct": 12.54,
   "equity_final_pct": 21.7, "worst_single_return_pct": -8.4,
   "note": "信号收益序列风险画像(排除 cash;收益下钳≥-100;每信号独立、窗口可重叠、无仓位管理;非真实组合 maxDD;单笔=-100 会使 maxDD 饱和 100%)"}
  ```

## 5. 影响面 / 兼容性

- 唯一改动文件:`src/core/backtest_engine.py`(+`_compute_risk_metrics`,`compute_summary` +1 行)。
- `backtest_service` / `backtest_repo` / schema / API / Web / 报告模板 **不改**;`diagnostics_json` 自动多一子键,旧消费者忽略未知键、不受影响。
- 现有 summary 字段(含 `avg_simulated_return_pct`、既有 `diagnostics` 键)字节级不变。
- 若报告未来要展示,追加一小节即可(本 spec 不改报告)。

## 6. 测试(确定性精确值 + 守卫 + 零回归)

置于链路A 引擎既有测试文件(`tests/test_backtest_engine*.py` 就近追加;无则新增 `tests/test_backtest_risk_metrics.py`)。用轻量 duck-typed result stub(带 `eval_status/position_recommendation/simulated_return_pct/analysis_date/code`)。**写死的期望值一律用与实现相同的 Python `round()`(round-half-even/banker's)语义生成,勿用外部计算器的 round-half-up;构造序列尽量避开 `.xx5` 半值输入**(如 `round(2.125,4)` 与手算可能不一致)。

1. **精确值(手算对照)**:构造已知 `r=[+2,-1,+3,-2]`(全 long、日期递增),断言 `mean/std(样本 ddof=1)/sharpe/sortino/max_drawdown_pct/equity_final_pct/worst_single_return_pct` 全部等于手算值(写死期望数,round 4;sharpe/sortino 用**未 round** 的 mean/std/downside_dev 求商再 round;`worst_single_return_pct=min(r)=-2`)。另补一例:原始 std 微小非零(如 `r=[1.00001,1.0]`)round_std=0.0000 但 `sharpe` **非 None**(证守卫用原始 std)。
2. **maxDD 复利、排序与确定性(非 tautology)**:构造使净值先升后降的序列,断言 maxDD 等于峰谷手算值;**先断言「按输入原序复利」的 maxDD ≠「按 analysis_date 序」的 maxDD(反例 oracle,证乱序会变)**,再打乱输入顺序但给正确 `analysis_date`,断言实现输出 == 日期序值(证被纠正为日期序);`analysis_date` 缺失时回退给定顺序(getattr 容错);**overall 同日并列**:同一 `analysis_date`、不同 `code` 多行,断言输出按「日期→code→原序」确定(打乱输入的 `original_index` 不改结果)。
3. **cash 排除**:completed 混入若干 cash(return 0.0),断言风险指标总体 `sample` 只数非 cash、且指标值与「去掉 cash」一致(证 §4.1 排除)。perp short(position≠"cash")计入。
4. **守卫**:`n=0 → sample:0 + 全 None`;`n=1 → sharpe/sortino/std None、maxDD/mean 有值`;`std=0`(全相同收益)`→ sharpe None`;全非负收益 `→ sortino None`;`r=-100`(下钳后)使 maxDD=100。
5. **L=1 perp short 下钳 + maxDD 饱和(锁 Blocker 修复)**:构造 perp short 原始 `simulated_return_pct=-200`(price 3× 反向,position≠"cash"),断言:(a) 进入 `series_rows`;(b) 经 §4.2 下钳到 `-100` 后 `max_drawdown_pct == 100`(**非 >100、非负净值、符号不翻转**)、`equity_final_pct == -100`、`worst_single_return_pct == -100`;(c) `mean_return_pct/sharpe/sortino` 均基于下钳值。再补:序列中部含一个 -100(如 `[+5, -100, +5]`)断言 maxDD 饱和 100%、equity_final -100(证爆仓后 equity 恒 0)。**§6.1/6.4 只测 L>1 液化 r=-100,覆盖不到此 <-100 反例。**
6. **接线**:调 `compute_summary` 断言返回 `diagnostics["risk_metrics"]` 存在且键齐;既有 `compute_summary` 断言(avg_simulated_return_pct 等)不回归(只多一键)。
7. **既有回测 summary 用例零回归**:若有 golden/snapshot 断言 `compute_summary` 返回或 diagnostics 精确内容,同步补 `risk_metrics` 键(plan-mandated 更新,非缺陷;实现者须 grep `tests/` 找 `compute_summary`/`diagnostics` 断言逐一核对)。

> 变异抵抗:测试 1(精确值)+3(cash 排除)+2(排序/确定性反例 oracle)+5(下钳反例)pin 死公式、总体、顺序语义、下钳边界——任一实现偏差即红。

## 7. 验证矩阵

- 后端 Python 改动:`src/core/`。执行 `./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`)。
- 最低:`python -m py_compile src/core/backtest_engine.py <测试文件>`。
- 无 config/API/Schema/Web → 免 web-gate;纯离线确定性,无网络依赖。
- crypto 真网端到端(可选):真实 run 后 summary.diagnostics.risk_metrics 随之出现,验证端到端落库+回读。
- 交付说明写明:纯追加诊断、现有字段字节级不变;口径=不年化事件序列 + 信号流 maxDD(非组合)。

## 8. 风险与回滚

- **风险:低**。纯派生、追加式、局部单文件、守卫齐全(除零/未定义一律 None、收益下钳 ≥ -100)。主要实现风险是公式/总体口径/下钳写错——由 §6.1/6.2/6.3/6.5 精确测覆盖。
- **口径争议点(留给评审确认)**:风险指标总体**排除 cash**、与 `avg_simulated_return_pct`(含 cash)**有意不同**;若评审倾向一致口径可改为含 cash,但会以 0 收益样本稀释波动/回撤(不推荐)。
- **收益下钳(对抗审查逮的 Blocker,已修)**:L=1 perp(默认)未在 engine floor,收益可 `<-100` → 若不 floor,maxDD 复利失真(>100%/负净值/符号翻转)。风险指标层统一 `max(r,-100)` 防御式处理;**不改 engine、不影响 `avg_simulated_return_pct`**。engine L=1 perp 未 floor 本身是既有 quirk(是否修 engine 属另议,超出本 spec)。
- **回滚**:单分支 `git revert`;`diagnostics` 少一子键,无迁移、无残留。

## 9. 诚实边界(写入 diagnostics.note 与文档)

本指标是**信号收益序列的风险画像**——每信号独立、前向窗口可重叠、无仓位/组合管理。`max_drawdown_pct` 反映「按时间顺序逐信号复利」的假想净值回撤,**不等于**真实组合回撤(真实组合含仓位/相关性/资金约束,属 Inc 5)。收益已下钳到 `≥ -100%`(单笔不可亏超本金)。overall scope 下同一 `analysis_date` 跨标的多行的复利次序取「日期→code→原序」的**规约稳定序**(可复现,但非真实时间序,同日内峰谷路径不做组合意义解释)。年化亦刻意不做(事件样本非等间隔时序)。
