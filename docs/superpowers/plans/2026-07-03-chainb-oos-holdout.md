# 链路B 样本外 holdout 切分(Inc 1e)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个 (signal_type×market×interval×horizon) 格子新增样本外 holdout 切分报告(oos_json,per-market 切点+精确 purge/embargo),opt-in 且默认字节级不变,verified/胜率口径不动。

**Architecture:** `SignalOutcome` 增 `window_end_date` → `aggregate_signal_stats` 增 `oos_fraction` 尾参(f=0 早退;f>0 per-market 日期格点分位切点 + win/loss 三分 + 两段子统计)→ `signal_stats.oos_json` 幂等补列(泛化共用序列化 helper)→ resolver 第 11 键(抽共用防御解析 helper)→ 1d D5 管道同构透出(SignalMarker 不声明 + 顶层 map + BoardEntry)。

**Tech Stack:** Python dataclasses/SQLAlchemy/Pydantic/pytest;无新依赖。

**Spec:** `docs/superpowers/specs/2026-07-03-chainb-oos-holdout-design.md`(v3,对抗审查 9 确认+顶级复审 R1-R3 已折进)

## Global Constraints

- 默认(oos_fraction=0)**字节级不变**:aggregate early-return;oos_json 恒 NULL;既有 golden/精确断言全绿。
- 分类宇宙:三分仅对 `outcome ∈ {win, loss}`;expired 不进任何 OOS 计数;守恒恒等式 `train.sample + oos.sample + embargoed + undated == SignalStat.sample`。
- 谓词按序短路,undated(date is None)优先;train 要求 `window_end_date is not None and window_end_date <= cutoff_m`;OOS=`date > cutoff_m`;其余 embargo(含 end 缺失且 date≤cutoff 的保守归类)。
- cutoff **per-market**:`dates_m = sorted({o.date for o in baseline_m if o.date is not None})`;`len<2` → 退化 dict `{"cutoff_date": None, "fraction": f, "degenerate": True}`;否则 `dates_m[math.floor((1-f)*(len(dates_m)-1))]`(Python math.floor 语义,实现与测试同一)。
- oos_json 的 `fraction` 键落**钳后值**;钳域 [0.0, 0.5],loader+aggregate 函数内双钳。
- 子键 `excess` = 胜率点估计差(raw 值相减后 round4),区别于 SignalStat.excess 的 ci_low 保守口径——Field description/docs 必须点明。
- 禁平行实现:序列化用泛化的 `_serialize_cell_json`(两调用点共用),resolver 解析抽 `_parse_json_dict`(两解析点共用)。
- verified/win_rate/CI/Bonferroni/risk_metrics 等既有字段在 f>0 时也与 f=0 完全一致(切分只读 outcomes)。
- commit message=英文类型前缀+中文正文,**不加 Co-Authored-By**,单行 `git commit -m`。
- 所有 python 命令前置 `export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(带引号,主仓路径含空格);不得用 `| tail` 掩盖退出码。
- 执行 worktree:`/root/chainb-oos`(无空格持久路径),分支 `feature/chainb-oos-holdout`,base=main(f397c5c9)。

---

### Task 1: SignalOutcome.window_end_date 数据管道(_eval 两分支)

**Files:**
- Modify: `src/services/signal_backtest.py`(SignalOutcome `:47-66`;_eval 循环 `:166-185`)
- Test: `tests/test_signal_backtest.py`(末尾追加)

**Interfaces:**
- Produces: `SignalOutcome.window_end_date: Optional[str] = None`(末尾默认字段);_eval 产出的每个 outcome 均携带 `window_end_date == str(df.iloc[t+horizon]["date"])`。Task 2 消费。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_backtest.py` 末尾追加)

```python
def test_eval_window_end_date_exact_and_both_branches():
    """Inc 1e:window_end_date 精确=t+horizon bar 日期(审查 F3:弃 '>' 弱判别),双分支同补。"""
    import pandas as pd
    from src.services.signal_backtest import (
        evaluate_baseline_outcomes, evaluate_signal_outcomes,
    )
    n, horizon = 80, 10
    dates = [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-01-01", periods=n, freq="D")]
    idx_of = {d: i for i, d in enumerate(dates)}          # 逐 bar 唯一日期,可反查 t

    def _df(vol):
        return pd.DataFrame({
            "date": dates,
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [101.0 + i * 0.1 for i in range(n)],
            "low": [99.0 + i * 0.1 for i in range(n)],
            "close": [100.5 + i * 0.1 for i in range(n)],
            "volume": vol,
        })

    # 分支1:baseline
    df = _df([1_000_000] * n)
    outs = evaluate_baseline_outcomes(df, market="cn", horizon=horizon)
    assert outs, "应产出 baseline outcome"
    for o in outs:
        t = idx_of[o.date]
        assert o.window_end_date == str(df.iloc[t + horizon]["date"])   # 精确相等,杀 t+1/t+horizon-1 变体

    # 分支2:per-signal(放量突破夹具:60 根横盘 + 5 倍量突破,后接足量前瞻)
    vol = [1_000_000] * n
    close = [100.0] * 60 + [110.0 + i * 0.5 for i in range(n - 60)]
    vol[60] = 5_000_000
    df2 = pd.DataFrame({
        "date": dates,
        "open": [c - 0.5 for c in close],
        "high": [c + 1.0 for c in close],
        "low": [c - 1.0 for c in close],
        "close": close,
        "volume": vol,
    })
    sig_outs = evaluate_signal_outcomes(df2, market="cn", horizon=horizon)
    assert sig_outs, "夹具应触发至少一个 bullish 信号(若为空请调整夹具并在报告披露,勿降低断言)"
    for o in sig_outs:
        t = idx_of[o.date]
        assert o.window_end_date == str(df2.iloc[t + horizon]["date"])


def test_legacy_signaloutcome_construction_no_window_end():
    from src.services.signal_backtest import SignalOutcome
    o = SignalOutcome("x", "cn", "win")                    # 既有三参构造零破坏
    assert o.window_end_date is None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_backtest.py -x -q`
Expected: FAIL — `SignalOutcome.__init__` 无 `window_end_date` 属性 / AttributeError。

- [ ] **Step 3: 实现**

1. `SignalOutcome` 末尾追加字段(docstring 同步):

```python
    window_end_date: Optional[str] = None
```

docstring 追加一行:`window_end_date  前瞻窗末 bar(t+horizon)的日期字符串;用于 OOS holdout 切分的精确 purge(spec §3.2)。默认 None(末尾字段,legacy 构造零破坏)。`

2. `_eval` 循环体(`:166-185`):在 `date = str(df.iloc[t]["date"])` 后加一行,并在**两个** `SignalOutcome(...)` 构造点(baseline 分支与 per-signal 分支)都追加 kwarg:

```python
        window_end = str(df.iloc[t + horizon]["date"])     # 循环上界 n-horizon 保证索引有效
```

```python
                out.append(SignalOutcome(
                    signal_type=BASELINE_SIGNAL_TYPE, market=market,
                    outcome=r.outcome, return_pct=r.return_pct, date=date,
                    window_end_date=window_end))
```

```python
                    out.append(SignalOutcome(
                        signal_type=sig_type, market=market,
                        outcome=r.outcome, return_pct=r.return_pct, date=date,
                        window_end_date=window_end))
```

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_signal_backtest.py tests/test_signal_backtest_stats.py tests/test_signal_eval_vectorization.py -q`
Expected: 全绿(纯追加字段)。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest.py
git commit -m "feat: SignalOutcome 携带前瞻窗末日期 window_end_date(_eval 双分支同补,OOS 精确 purge 的数据前提,末尾默认字段 legacy 零破坏)"
```

---

### Task 2: aggregate 切分核心(per-market cutoff + 三分 + 两段子统计)

**Files:**
- Modify: `src/services/signal_backtest.py`(模块级新增 `_derive_market_cutoffs`/`_split_counts`/`_segment_stats`;`SignalStat` 追加字段;`aggregate_signal_stats` `:343-448`)
- Test: `tests/test_signal_backtest_stats.py`(末尾追加)

**Interfaces:**
- Consumes: Task 1 `SignalOutcome.window_end_date`。
- Produces: `aggregate_signal_stats(..., oos_fraction: float = 0.0)`;`SignalStat.oos: Optional[dict] = None`(键集见 spec §3.4:cutoff_date/fraction/embargoed/undated/train{win_rate,sample,baseline_win_rate,excess}/oos{同}或退化 dict);`_derive_market_cutoffs(baseline_outcomes, f) -> dict[str, Optional[str]]`。Task 4 落库消费。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_backtest_stats.py` 末尾追加)

```python
# =====================================================================
# 链路B OOS holdout 切分(spec §3,Inc 1e)
# =====================================================================


def _b(mkt, date, end=None, outcome="win"):
    return SignalOutcome("__baseline__", mkt, outcome, date=date, window_end_date=end)


def _sig(mkt, date, end=None, outcome="win", st="volume_breakout"):
    return SignalOutcome(st, mkt, outcome, date=date, window_end_date=end)


def test_cutoff_quantile_kills_floor_variants_and_no_date_parsing():
    """审查 F2:两组算例各杀 len 基/round 变体;非日期 token 证纯字典序无解析。"""
    from src.services.signal_backtest import _derive_market_cutoffs
    base = [_b("cn", d) for d in ["d1", "d2", "d3", "d4", "d5"]]
    assert _derive_market_cutoffs(base, 0.25)["cn"] == "d4"   # floor(0.75*4)=3;len 基变体 floor(3.75)-1=2 → d3 被杀
    assert _derive_market_cutoffs(base, 0.3)["cn"] == "d3"    # floor(2.8)=2;round 变体 round(2.8)=3 → d4 被杀


def test_dual_market_heterogeneous_span_uses_own_cutoff():
    """审查 STAT-1 回归锚:异构跨度双市场各用自己的分位;全局池化会把短跨度市场 train 清空。"""
    from src.services.signal_backtest import _derive_market_cutoffs
    base = [_b("us", f"a{i:03d}", f"a{min(i + 2, 99):03d}") for i in range(100)]
    base += [_b("cn", f"b{i:03d}", f"b{min(i + 2, 19):03d}",
                "win" if i % 2 else "loss") for i in range(20)]
    cutoffs = _derive_market_cutoffs(base, 0.2)
    assert cutoffs["us"] == "a079" and cutoffs["cn"] == "b015"   # 各自 floor(0.8*(len-1));全局池化会落 a095

    outs = [_sig("cn", f"b{i:03d}", f"b{i + 2:03d}") for i in range(12)]
    stats = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.2)
    rep = next(s for s in stats if s.market == "cn").oos
    assert rep["cutoff_date"] == "b015"
    assert rep["train"]["sample"] > 0            # 全局池化下 cn 全部日期 > a095 → train 必空,此断言即判别式


def test_split_conservation_embargo_and_expired_excluded():
    """审查 STAT-4:守恒恒等式承重;expired 不进任何计数;跨切点窗必 embargo;end 缺失保守归 embargo。"""
    base = []
    dates = [f"c{i:02d}" for i in range(10)]                     # c00..c09,f=0.3 → floor(0.7*9)=6 → cutoff=c06
    for i, d in enumerate(dates):
        end = dates[min(i + 2, 9)]
        base.append(_b("cn", d, end, "win" if i % 2 == 0 else "loss"))
    outs = [
        _sig("cn", "c01", "c03", "win"),      # train
        _sig("cn", "c02", "c04", "loss"),     # train
        _sig("cn", "c05", "c08", "win"),      # embargo:date≤c06<end(泄漏回归锚)
        _sig("cn", "c03", None, "loss"),      # embargo:end 缺失且 date≤cutoff 保守归类(#4)
        _sig("cn", "c07", "c09", "win"),      # OOS
        _sig("cn", "c08", "c09", "loss"),     # OOS
        _sig("cn", None, None, "win"),        # undated
        _sig("cn", "c01", "c03", "expired"),  # expired:不进任何 OOS 计数
    ]
    s = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)[0]
    rep = s.oos
    assert rep["cutoff_date"] == "c06" and rep["fraction"] == 0.3
    assert rep["train"]["sample"] == 2 and rep["oos"]["sample"] == 2
    assert rep["embargoed"] == 2 and rep["undated"] == 1
    # 守恒恒等式(win/loss 宇宙的精确划分):
    assert rep["train"]["sample"] + rep["oos"]["sample"] + rep["embargoed"] + rep["undated"] == s.sample == 7
    # 两段子统计手算 pin:
    # baseline train=c00..c04(end≤c06)=3W2L→0.6;baseline OOS=c07,c08,c09=1W2L→0.3333
    assert abs(rep["train"]["win_rate"] - 0.5) < 1e-9
    assert abs(rep["train"]["baseline_win_rate"] - 0.6) < 1e-9
    assert abs(rep["train"]["excess"] - (-0.1)) < 1e-9
    assert abs(rep["oos"]["win_rate"] - 0.5) < 1e-9
    assert abs(rep["oos"]["baseline_win_rate"] - 0.3333) < 1e-9
    assert abs(rep["oos"]["excess"] - 0.1667) < 1e-9            # round(0.5-1/3, 4):raw 相减后 round4


def test_zero_sample_segment_yields_none_not_crash():
    base = [_b("cn", f"e{i}", f"e{min(i + 1, 4)}") for i in range(5)]   # e0..e4,f=0.5→floor(0.5*4)=2→cutoff=e2
    outs = [_sig("cn", "e3", "e4", "win")]                               # 仅 OOS 一件,train 空
    rep = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.5)[0].oos
    assert rep["train"]["sample"] == 0 and rep["train"]["win_rate"] is None
    assert rep["train"]["excess"] is None


def test_degenerate_market_coexists_with_normal_market():
    """§7.6:某市场 distinct 日期<2 → 退化 dict;同 run 其他市场正常。"""
    base = [_b("hk", "only-one-date", "only-one-date")]
    base += [_b("cn", f"g{i}", f"g{min(i + 1, 4)}") for i in range(5)]
    outs = [_sig("hk", "only-one-date", None), _sig("cn", "g3", "g4")]
    stats = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)
    hk = next(s for s in stats if s.market == "hk").oos
    cn = next(s for s in stats if s.market == "cn").oos
    assert hk == {"cutoff_date": None, "fraction": 0.3, "degenerate": True}
    assert cn["cutoff_date"] is not None and "train" in cn


def test_headline_stats_invariant_and_inputs_not_mutated():
    """§7.7:f>0 不改任何既有字段;输入列表未被变异(id/顺序/元素同一)。"""
    base = [_b("cn", f"h{i:02d}", f"h{min(i + 2, 9):02d}", "win" if i % 2 else "loss") for i in range(10)]
    outs = [_sig("cn", f"h{i:02d}", f"h{min(i + 2, 9):02d}", "win" if i < 4 else "loss") for i in range(8)]
    snap_outs, snap_base = list(outs), list(base)
    s0 = aggregate_signal_stats(outs, base, horizon=10)[0]
    s1 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)[0]
    assert all(a is b for a, b in zip(outs, snap_outs)) and len(outs) == len(snap_outs)
    assert all(a is b for a, b in zip(base, snap_base)) and len(base) == len(snap_base)
    for f in ("signal_type", "market", "win", "loss", "sample", "win_rate",
              "ci_low", "ci_high", "baseline_win_rate", "excess",
              "ci_low_corrected", "family_size", "risk_metrics"):
        assert getattr(s0, f) == getattr(s1, f), f
    assert s0.oos is None and s1.oos is not None


def test_function_side_clamp_and_early_exit():
    """审查 F7:函数侧二次钳;fraction 键落钳后值;负值走 f=0 早退。"""
    base = [_b("cn", f"k{i}", f"k{min(i + 1, 5)}") for i in range(6)]
    outs = [_sig("cn", "k1", "k2")]
    rep09 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.9)[0].oos
    rep05 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.5)[0].oos
    assert rep09 == rep05 and rep09["fraction"] == 0.5
    assert aggregate_signal_stats(outs, base, horizon=10, oos_fraction=-0.1)[0].oos is None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_backtest_stats.py -x -q`
Expected: FAIL — `_derive_market_cutoffs` 不存在 / `aggregate_signal_stats` 无 `oos_fraction` 关键字。

- [ ] **Step 3: 实现**(`src/services/signal_backtest.py`)

1. 模块级三个纯 helper(放 `aggregate_signal_stats` 前,`math` 已 import 则复用否则补):

```python
def _derive_market_cutoffs(baseline_outcomes, oos_fraction):
    """per-market holdout 切点(spec §3.1)。

    每市场取 baseline 事件 distinct 日期字符串字典序升序的
    floor((1-f)*(len-1)) 分位;len<2 → None(退化,调用方落 degenerate dict)。
    纯字典序比较,不做日期解析(同 maxDD 排序的同格式假设)。
    """
    by_market: dict = {}
    for o in baseline_outcomes:
        if o.date is not None:
            by_market.setdefault(o.market, set()).add(o.date)
    cutoffs: dict = {}
    for market, date_set in by_market.items():
        dates = sorted(date_set)
        if len(dates) < 2:
            cutoffs[market] = None
        else:
            cutoffs[market] = dates[math.floor((1 - oos_fraction) * (len(dates) - 1))]
    return cutoffs


def _split_counts(events, cutoff):
    """win/loss 事件三分(spec §3.2,谓词按序短路,undated 优先)。

    events: [(date, window_end_date, outcome)],outcome ∈ {win, loss}。
    返回 (train_win, train_loss, oos_win, oos_loss, embargoed, undated)。
    """
    tw = tl = ow = ol = emb = und = 0
    for date, end, outcome in events:
        if date is None:
            und += 1
        elif end is not None and end <= cutoff:
            if outcome == "win":
                tw += 1
            else:
                tl += 1
        elif date > cutoff:
            if outcome == "win":
                ow += 1
            else:
                ol += 1
        else:
            emb += 1                       # 跨切点窗 或 end 缺失且 date≤cutoff:保守剔除防泄漏
    return tw, tl, ow, ol, emb, und


def _segment_stats(win, loss, base_win, base_loss):
    """单段子统计(spec §3.3)。excess=raw 胜率差后 round4(点估计口径,非 ci_low)。"""
    sample = win + loss
    win_rate = (win / sample) if sample else None
    b_sample = base_win + base_loss
    baseline = (base_win / b_sample) if b_sample else None
    excess = (win_rate - baseline) if win_rate is not None and baseline is not None else None
    return {
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "sample": sample,
        "baseline_win_rate": round(baseline, 4) if baseline is not None else None,
        "excess": round(excess, 4) if excess is not None else None,
    }
```

2. `SignalStat` 末尾追加(docstring 字段表同步;沿 risk_metrics 的不可哈希注释):

```python
    oos: Optional[dict] = None
```

3. `aggregate_signal_stats`:签名尾参 `oos_fraction: float = 0.0`(docstring 加一行:默认 0 仅供独测,生产路径必须显式传 `config.signal_backtest_oos_fraction`;函数内钳 [0.0, 0.5]);函数体开头钳制:

```python
    oos_fraction = min(max(float(oos_fraction), 0.0), 0.5)
```

Step 2 分桶循环处,`if oos_fraction > 0.0:` 守卫下顺路收集 win/loss 事件(f=0 零开销):

```python
    cell_wl: dict = {}
    baseline_wl: dict = {}
    if oos_fraction > 0.0:
        for o in outcomes:
            if o.outcome in ("win", "loss"):
                cell_wl.setdefault((o.signal_type, o.market), []).append(
                    (o.date, o.window_end_date, o.outcome))
        for o in baseline_outcomes:
            if o.outcome in ("win", "loss"):
                baseline_wl.setdefault(o.market, []).append(
                    (o.date, o.window_end_date, o.outcome))
        cutoffs = _derive_market_cutoffs(baseline_outcomes, oos_fraction)
        baseline_split_cache: dict = {}
```

Step 3c 每格(SignalStat 构造前,与 risk_metrics 计算并列):

```python
        oos_report = None
        if oos_fraction > 0.0:
            cutoff = cutoffs.get(market)
            if cutoff is None:
                oos_report = {"cutoff_date": None, "fraction": oos_fraction, "degenerate": True}
            else:
                tw, tl, ow, ol, emb, und = _split_counts(
                    cell_wl.get((sig_type, market), []), cutoff)
                if market not in baseline_split_cache:
                    baseline_split_cache[market] = _split_counts(
                        baseline_wl.get(market, []), cutoff)
                btw, btl, bow, bol, _, _ = baseline_split_cache[market]
                oos_report = {
                    "cutoff_date": cutoff,
                    "fraction": oos_fraction,
                    "embargoed": emb,
                    "undated": und,
                    "train": _segment_stats(tw, tl, btw, btl),
                    "oos": _segment_stats(ow, ol, bow, bol),
                }
```

`SignalStat(...)` 构造追加 `oos=oos_report,`。

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_signal_backtest_stats.py tests/test_signal_backtest.py tests/test_signal_eval_vectorization.py tests/test_signal_backtest_service.py -q`
Expected: 全绿(f=0 默认路径字节级不变,既有测试即基线)。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest_stats.py
git commit -m "feat: aggregate 新增 OOS holdout 切分核心(per-market 日期格点分位切点/win-loss 三分含 embargo 防泄漏/两段子统计点估计口径/守恒恒等式,f=0 早退字节级不变,函数侧钳 [0,0.5] 且 fraction 落钳后值)"
```

---

### Task 3: 配置四件套(SIGNAL_BACKTEST_OOS_FRACTION)

**Files:**
- Modify: `src/config.py`(字段 `:903` 邻域;loader 仿 `:1754-1760` FWER)
- Modify: `src/core/config_registry.py`(仿 `:3304-3327` FWER 条目,display_order=72)
- Modify: `apps/dsa-web/src/i18n/settingsHelp.ts`(仿 FWER 条目,help_key `settings.backtest.SIGNAL_BACKTEST_OOS_FRACTION`;**实现前先读该文件 FWER 条目取真实结构**,含 notes/impact 字段则同补)
- Modify: `.env.example`(FWER 注释块邻域)
- Test: `tests/test_signal_backtest_config.py`(仿 FWER 解析测试追加;文件不存在则找 FWER 解析测试真实所在文件追加)

**Interfaces:**
- Produces: `config.signal_backtest_oos_fraction: float`(默认 0.0,loader 钳 [0.0, 0.5])。Task 4 run() 接线消费。

- [ ] **Step 1: 写失败测试**(先 `grep -rn "SIGNAL_BACKTEST_FWER_ALPHA" tests/` 找 FWER 解析测试所在文件,同文件追加;下述以 tests/test_signal_backtest_config.py 为例,漂移时照真实文件适配并披露)

```python
def test_oos_fraction_default_zero(monkeypatch):
    monkeypatch.delenv("SIGNAL_BACKTEST_OOS_FRACTION", raising=False)
    cfg = _fresh_config()          # 用该文件既有的 Config 重建夹具(reset 单例的既有 helper/模式)
    assert cfg.signal_backtest_oos_fraction == 0.0


def test_oos_fraction_clamped(monkeypatch):
    monkeypatch.setenv("SIGNAL_BACKTEST_OOS_FRACTION", "0.9")
    assert _fresh_config().signal_backtest_oos_fraction == 0.5
    monkeypatch.setenv("SIGNAL_BACKTEST_OOS_FRACTION", "-0.1")
    assert _fresh_config().signal_backtest_oos_fraction == 0.0
    monkeypatch.setenv("SIGNAL_BACKTEST_OOS_FRACTION", "0.3")
    assert _fresh_config().signal_backtest_oos_fraction == 0.3
```

(`_fresh_config` 指该测试文件中既有的"重建 Config 实例"模式——FWER 测试怎么写就怎么仿,不新造夹具。)

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_backtest_config.py -x -q`(或真实文件)
Expected: FAIL — Config 无 `signal_backtest_oos_fraction` 属性。

- [ ] **Step 3: 实现**

1. `src/config.py` 字段(`signal_backtest_fwer_alpha` 后):

```python
    signal_backtest_oos_fraction: float = 0.0
```

loader(FWER loader 后,镜像其形态):

```python
        self.signal_backtest_oos_fraction = parse_env_float(
            os.getenv('SIGNAL_BACKTEST_OOS_FRACTION'),
            0.0,
            field_name='SIGNAL_BACKTEST_OOS_FRACTION',
            minimum=0.0,
            maximum=0.5,
        )
```

2. `src/core/config_registry.py`(FWER 条目后,**display_order=72**;docs 链接照抄 FWER 条目的):

```python
    "SIGNAL_BACKTEST_OOS_FRACTION": {
        "title": "Signal Backtest OOS Fraction",
        "description": "Out-of-sample holdout fraction of the per-market trading-date grid for chain-B signal stats (Inc 1e). 0 disables (default, behavior unchanged). Descriptive disclosure only - the verified badge and win-rate stay full-sample.",
        "category": "backtest",
        "data_type": "number",
        "ui_control": "number",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "0.0",
        "options": [],
        "validation": {"min": 0.0, "max": 0.5},
        "display_order": 72,
        "help_key": "settings.backtest.SIGNAL_BACKTEST_OOS_FRACTION",
        "examples": [
            "SIGNAL_BACKTEST_OOS_FRACTION=0.0",
            "SIGNAL_BACKTEST_OOS_FRACTION=0.3",
        ],
        "docs": [FWER 条目同款 docs 数组,照抄],
        "warning_codes": [],
    },
```

3. `apps/dsa-web/src/i18n/settingsHelp.ts`:仿 FWER 条目结构补 `settings.backtest.SIGNAL_BACKTEST_OOS_FRACTION` 双语条目,文案(spec §4.6/CFG-2):
   - zh 描述:`链路B 信号统计的样本外 holdout 切分比例(按市场交易日期格点分位,0=关闭)。启用后仅新增披露(oos 字段),verified 徽章与胜率口径不变;切的是时间轴而非样本配额。`
   - zh notes:`修改后需重新运行 --signal-backtest(值>0)才会落 oos_json。`
   - zh impact:`影响 signal_stats.oos_json 与 API oos_by_signal_type/BoardEntry.oos 披露;不影响 verified 徽章与胜率口径。`
   - en 对应直译,禁"out-of-sample validated/passed"判定式措辞。

4. `.env.example`(FWER 注释块后):

```
# 链路B 样本外 holdout 切分比例(0=关闭;描述性披露,不影响 verified 徽章)
# SIGNAL_BACKTEST_OOS_FRACTION=0.3
```

- [ ] **Step 4: 跑测试确认 GREEN + registry/locale 联动测试**

Run: `python -m pytest tests/test_signal_backtest_config.py tests/ -q -k "registry or locale or settings_help or config_metadata or web_metadata"`
Expected: 全绿(registry↔locale 双测覆盖新键;若 -k 无命中,改跑 `grep -rln "help_key" tests/ | head` 找到的元数据测试文件)。

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/core/config_registry.py apps/dsa-web/src/i18n/settingsHelp.ts .env.example tests/
git commit -m "feat: 新增 SIGNAL_BACKTEST_OOS_FRACTION 配置(默认 0 关闭,钳制域 [0,0.5],registry order=72+locale 双语含重跑生效提示+env.example 注释行,文案标明纯披露不影响 verified)"
```

---

### Task 4: 落库 + 服务接线 + resolver 第 11 键(共用 helper 泛化)

**Files:**
- Modify: `src/storage.py`(`SignalStatRow` `:436` 邻域;`_ensure_signal_stats_columns`)
- Modify: `src/services/signal_backtest_service.py`(`_serialize_risk_metrics` `:101-114` 泛化;ORM 构造 `:209`;run() aggregate 调用 `:186-190` 传参)
- Modify: `src/services/signal_hit_rate.py`(`_parse_json_dict` 抽取;`_none`;返回 dict;docstring)
- Test: `tests/test_signal_stats_migration.py`、`tests/test_signal_hit_rate.py`、`tests/test_signal_backtest_service.py`、`tests/test_signal_finer_fields.py`(桩+断言)

**Interfaces:**
- Consumes: Task 2 `SignalStat.oos`;Task 3 `config.signal_backtest_oos_fraction`。
- Produces: `SignalStatRow.oos_json`(Text nullable);`_serialize_cell_json(payload, field_label, signal_type, market) -> Optional[str]`;`_parse_json_dict(raw) -> Optional[dict]`;resolver dict 第 11 键 `"oos": Optional[dict]`——Task 5 消费。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_stats_migration.py` 追加(仿既有 risk_metrics 两测,reset_instance 全套):

```python
def test_oos_json_column_present_and_migrated(tmp_path):
    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh4.db'}")
        assert "oos_json" in _columns(db)
    finally:
        DatabaseManager.reset_instance()


def test_oos_json_roundtrip_and_null_legacy(tmp_path):
    import json
    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'rt4.db'}")
        rep = {"cutoff_date": "2026-06-01", "fraction": 0.3, "embargoed": 1, "undated": 0,
               "train": {"win_rate": 0.6, "sample": 5, "baseline_win_rate": 0.5, "excess": 0.1},
               "oos": {"win_rate": None, "sample": 0, "baseline_win_rate": None, "excess": None}}
        row = SignalStatRow(signal_type="volume_breakout", market="cn", interval="1d",
                            horizon=10, win=2, loss=1, sample=3,
                            oos_json=json.dumps(rep, ensure_ascii=False))
        legacy = SignalStatRow(signal_type="old_sig", market="cn", interval="1d",
                               horizon=10, win=1, loss=1, sample=2)
        with db.get_session() as s:
            s.add_all([row, legacy])
            s.commit()
            fetched = s.query(SignalStatRow).filter_by(signal_type="volume_breakout").one()
            assert json.loads(fetched.oos_json)["train"]["excess"] == 0.1
            assert s.query(SignalStatRow).filter_by(signal_type="old_sig").one().oos_json is None
    finally:
        DatabaseManager.reset_instance()
```

`tests/test_signal_hit_rate.py`:全部桩(`_make_stat`/`_stat`/SimpleNamespace 各处,1d 补 `risk_metrics_json=None` 的同一批位置)补 `oos_json=None`;两处精确 dict 断言 10→11 键(补 `"oos": None`)。末尾追加:

```python
def test_resolver_returns_oos_dict_and_defensive_paths():
    rep = {"cutoff_date": "x", "fraction": 0.3, "embargoed": 0, "undated": 0,
           "train": {"win_rate": 0.6, "sample": 5, "baseline_win_rate": 0.5, "excess": 0.1},
           "oos": {"win_rate": 0.4, "sample": 3, "baseline_win_rate": 0.5, "excess": -0.1}}
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(oos_json=_json.dumps(rep))
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["oos"]["oos"]["excess"] == -0.1
    for bad in (None, "not json{", "[1,2]", "42"):
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = _stat(oos_json=bad)
            assert resolve_marker_hit_fields("volume_breakout", "600519")["oos"] is None
```

`tests/test_signal_backtest_service.py` 追加(审查 F1-Blocker 接线测试;仿 `:104` spy 先例与 `:27` run 夹具):

```python
def test_run_passes_oos_fraction_and_persists_oos_json(monkeypatch):
    """F1:防静默死配置——config 值必须传到 aggregate 且落库 oos_json 非 NULL。"""
    import json
    import src.services.signal_backtest_service as svc_mod
    captured = {}
    real_aggregate = svc_mod.aggregate_signal_stats

    def spy(*args, **kwargs):
        captured["oos_fraction"] = kwargs.get("oos_fraction")
        return real_aggregate(*args, **kwargs)

    monkeypatch.setattr(svc_mod, "aggregate_signal_stats", spy)
    monkeypatch.setattr(svc_mod.get_config(), "signal_backtest_oos_fraction", 0.3, raising=False)
    # 复用本文件 test_run_aggregates_watchlist_and_writes_stats 的夹具流程跑 run()
    # (照抄其 stub/patch 与调用形态;此处省略的夹具代码在实现时从 :27 测试复制适配)
    ...
    assert captured["oos_fraction"] == 0.3
    # 落库断言:repo.save_batch 收到的行 oos_json 非 NULL 且含全键
    row = <捕获的 orm_rows>[0]
    rep = json.loads(row.oos_json)
    assert {"cutoff_date", "fraction", "embargoed", "undated", "train", "oos"} <= set(rep) \
        or rep.get("degenerate") is True


def test_run_default_config_leaves_oos_json_null(monkeypatch):
    """#1 服务层对偶:默认 config(0.0)跑 run() → 落库 oos_json 全 NULL。"""
    # 同上夹具,不改 config,断言捕获行 oos_json is None
    ...
```

(两测的夹具部分照抄该文件 `:27`/`:104` 既有测试的 stub 形态——plan 不复制其全部 60 行夹具;实现者以真实文件为准适配,断言语义不得弱化。)

`tests/test_signal_finer_fields.py`:`:86` 区 SimpleNamespace 桩补 `oos_json=None`(与 1d 补 `risk_metrics_json=None` 同位置)。

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_hit_rate.py tests/test_signal_backtest_service.py -x -q`
Expected: FAIL — SignalStatRow 无 `oos_json` / resolver dict 无 `oos` 键(精确 dict 断言先红)。

- [ ] **Step 3: 实现**

1. `storage.py`:`risk_metrics_json` 列后追加:

```python
    oos_json = Column(Text)            # OOS holdout 切分报告 JSON;NULL=legacy 行/未启用
```

`_ensure_signal_stats_columns` 追加第四段(docstring 三列→四列):

```python
                if "oos_json" not in existing:
                    conn.execute(text(
                        "ALTER TABLE signal_stats ADD COLUMN oos_json TEXT"
                    ))
```

2. `signal_backtest_service.py`:`_serialize_risk_metrics` 泛化为(顶级复审 R1,禁平行实现;先 `grep -rn "_serialize_risk_metrics" tests/` 确认无测试 import——已核仅源码一处命中,若实现时发现测试引用则一并更名):

```python
def _serialize_cell_json(payload: Optional[dict], field_label: str,
                         signal_type: str = "?", market: str = "?") -> Optional[str]:
    """单格 JSON 字段序列化(risk_metrics/oos 共用);None 直接透传(legacy/未启用)。

    allow_nan=False fail-closed;失败单格降级 NULL 不拖垮整批,warning 带字段名与格子身份。
    """
    if payload is None:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        logger.warning("%s 序列化失败，(%s, %s) 该格降级为 legacy(NULL): %s",
                       field_label, signal_type, market, exc)
        return None
```

删除旧 `_serialize_risk_metrics`;ORM 构造两行:

```python
                risk_metrics_json=_serialize_cell_json(
                    s.risk_metrics, "risk_metrics", s.signal_type, s.market),
                oos_json=_serialize_cell_json(s.oos, "oos", s.signal_type, s.market),
```

run() aggregate 调用(`:186-190`)追加 `oos_fraction=cfg.signal_backtest_oos_fraction,`(该处 cfg 变量名以真实代码为准,与 fwer_alpha 传参同源)。

3. `signal_hit_rate.py`:模块级抽 helper(顶级复审 R1):

```python
def _parse_json_dict(raw) -> Optional[dict]:
    """防御解析 JSON 列:非真值/坏 JSON/非 dict 一律 None(NULL=legacy 单义)。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
```

resolver 中既有 risk_metrics 解析块(`:134-142`)改为 `risk_metrics = _parse_json_dict(getattr(stat, "risk_metrics_json", None))`(既有行为不变,1d 防御四路测试即回归锚);新增 `oos = _parse_json_dict(getattr(stat, "oos_json", None))`;`_none` 补 `"oos": None`;返回 dict 补 `"oos": oos,`;docstring 键列表 10→11。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_hit_rate.py tests/test_signal_backtest_service.py tests/test_signal_finer_fields.py -q`
Expected: 全绿(含 1d 防御四路回归——证 _parse_json_dict 抽取行为不变)。

- [ ] **Step 5: Commit**

```bash
git add src/storage.py src/services/signal_backtest_service.py src/services/signal_hit_rate.py tests/
git commit -m "feat: signal_stats 追加 oos_json 列并接线 run()(幂等补列 NULL=legacy,序列化/防御解析泛化为共用 helper 消平行实现,resolver 第 11 键,服务级接线测试防静默死配置)"
```

---

### Task 5: surfacing(marker/board/Pydantic,1d D5 同构)

**Files:**
- Modify: `src/services/signals_service.py`(`_marker_from_vpsignal` 基础 dict+回填;`_llm_marker`;`build_signals_payload` 收敛 map)
- Modify: `src/services/signal_board_service.py`(`_hit_fields_from_markers` 两 return + `_degraded_entry`)
- Modify: `api/v1/schemas/stocks.py`(`SignalsResponse.oos_by_signal_type`;`BoardEntry.oos`;**SignalMarker 不加**)
- Test: `tests/test_signal_finer_fields.py`、`tests/test_signal_board_service.py`、`tests/test_signals_board_endpoint.py`(追加)

**Interfaces:**
- Consumes: Task 4 resolver dict 的 `oos` 键。
- Produces: `SignalsResponse.oos_by_signal_type: Dict[str, Dict[str, Any]]`;`BoardEntry.oos: Optional[Dict[str, Any]]`;marker 内存 dict 键 `oos`(API 层经 SignalMarker 剥离)。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_finer_fields.py` 追加(复用该文件既有 `_mk` 工厂与 1d 测试形态):

```python
def test_marker_carries_oos_in_memory_dict():
    sig = _mk("volume_breakout")
    rep = {"cutoff_date": "x", "fraction": 0.3, "embargoed": 0, "undated": 0,
           "train": {"win_rate": 0.6, "sample": 5, "baseline_win_rate": 0.5, "excess": 0.1},
           "oos": {"win_rate": 0.4, "sample": 3, "baseline_win_rate": 0.5, "excess": -0.1}}
    resolver = lambda st, code: {"hit_rate": 0.6, "hit_sample": 30, "verified": True,
                                 "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1,
                                 "horizon": 10, "ci_low_corrected": None, "family_size": None,
                                 "risk_metrics": None, "oos": rep}
    m = _ss._marker_from_vpsignal(sig, code="600519", hit_fields_resolver=resolver)
    assert m["oos"] == rep


def test_build_collects_oos_by_signal_type():
    rep_a = {"cutoff_date": "x", "fraction": 0.3}
    def resolver(st, code):
        return {"hit_rate": 0.6, "hit_sample": 30, "verified": False,
                "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": 10,
                "ci_low_corrected": None, "family_size": None, "risk_metrics": None,
                "oos": rep_a if st == "volume_breakout" else None}
    engine_result = _types.SimpleNamespace(
        status="ok", degraded_reason=None,
        markers=[_mk("volume_breakout"), _mk("obv_top_divergence")],
    )
    payload = _ss.build_signals_payload(
        engine_result=engine_result, rule_signal=None, llm_record=None,
        latest_bar_date="2026-06-01", latest_close=10.0,
        trading_days_elapsed=0, code="600519", hit_fields_resolver=resolver,
    )
    assert payload["oos_by_signal_type"] == {"volume_breakout": rep_a}   # None 值被 isinstance 守卫跳过
```

`tests/test_signal_board_service.py`:追加三站点测试,并把 `:51` 顶层键集断言补 `"oos_by_signal_type"`(8→9 键,审查 C1——**不改断言形态,只扩键集**):

```python
def test_hit_fields_carry_oos_and_degraded_none():
    rep = {"cutoff_date": "x", "fraction": 0.3}
    marker = {"source": "rule", "hit_rate": 0.6, "hit_sample": 30, "verified": False,
              "ci_low": None, "ci_high": None, "baseline_excess": None,
              "ci_low_corrected": None, "family_size": None, "risk_metrics": None,
              "horizon_bars": 10, "status": "active", "oos": rep}
    assert _hit_fields_from_markers([marker])["oos"] == rep
    assert _hit_fields_from_markers([])["oos"] is None
    assert _degraded_entry("600519", "x")["oos"] is None
```

`tests/test_signals_board_endpoint.py` 追加:

```python
def test_board_entry_preserves_oos_dict():
    entry = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None},
        status="ok", oos={"cutoff_date": "x", "fraction": 0.3},
    )
    assert entry.model_dump()["oos"]["fraction"] == 0.3


def test_signal_marker_deliberately_strips_oos():
    """D7 反向断言:SignalMarker 不声明 oos——内存 marker 带、序列化剥离(防逐 bar 膨胀)。"""
    m = SignalMarker(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", source="rule", confidence="low",
        is_daily_approx=False, is_anomalous=False, reason="r",
        oos={"fraction": 0.3},
    )
    assert "oos" not in m.model_dump()


def test_signals_response_top_level_oos_map():
    resp = SignalsResponse(
        status="ok", markers=[], consistency="consistent", degraded_reason=None,
        oos_by_signal_type={"volume_breakout": {"cutoff_date": "x", "fraction": 0.3}},
    )
    assert resp.model_dump()["oos_by_signal_type"]["volume_breakout"]["fraction"] == 0.3
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py -x -q`
Expected: FAIL — marker 无 `oos` 键 / SignalsResponse 无 map 字段 / `:51` 键集断言红。

- [ ] **Step 3: 实现**(逐点镜像 1d risk_metrics 同名改动,`oos` 替换字段名)

1. `signals_service.py`:`_marker_from_vpsignal` 基础 dict 加 `'oos': None`;回填块加 `marker['oos'] = fields.get('oos')`;`_llm_marker` 加 `'oos': None`;`build_signals_payload` 的 `risk_by_type` 收敛循环旁并列:

```python
    oos_by_type: dict = {}
    for m in markers:
        rep = m.get("oos")
        st = m.get("signal_type")
        if isinstance(rep, dict) and st and st not in oos_by_type:
            oos_by_type[st] = rep
```

return dict 追加 `"oos_by_signal_type": oos_by_type,`。

2. `signal_board_service.py`:`_hit_fields_from_markers` 两处 return 加 `"oos": m.get("oos")` / `"oos": None`;`_degraded_entry` 加 `"oos": None`。

3. `api/v1/schemas/stocks.py`:`SignalsResponse` 追加:

```python
    oos_by_signal_type: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="各信号型所在 (signal_type×market) 格子的样本外 holdout 切分报告(per-market 交易日期格点分位切点,train/oos 两段胜率/样本/基准/超额,embargo 防泄漏剔除计数;子键 excess=胜率点估计差,非 SignalStat.excess 的 ci_low 保守口径)。描述性统计:无置信区间、未经多重检验校正,不作为 verified 判据(verified 全样本口径不变)。空 dict=无格子、legacy 或未启用(SIGNAL_BACKTEST_OOS_FRACTION=0);degenerate=true 表示该市场日期格点不足无法切分。",
    )
```

`BoardEntry` 追加:

```python
    oos: Optional[Dict[str, Any]] = Field(None, description="代表信号格子的样本外 holdout 切分报告(口径同 SignalsResponse.oos_by_signal_type);null=legacy 或未启用。描述性统计,不影响 verified。")
```

**SignalMarker 不加任何字段**(D7 刻意)。

- [ ] **Step 4: 跑测试确认 GREEN + 邻域六套件**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py tests/test_signals_service.py tests/test_signals_endpoint.py tests/test_signal_board_resonance.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/services/signals_service.py src/services/signal_board_service.py api/v1/schemas/stocks.py tests/
git commit -m "feat: OOS 切分报告 API 透出——SignalsResponse 顶层 oos_by_signal_type map+BoardEntry.oos(SignalMarker 刻意不声明防逐 bar 膨胀),Field description 标注描述性统计与 excess 点估计口径差异"
```

---

### Task 6: 文档 + CHANGELOG + 零回归 grep + 全量门禁

**Files:**
- Modify: `docs/signal-credibility.md`(追加"样本外 holdout 切分"节)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 一条扁平)
- Test: 全量 `./scripts/ci_gate.sh` + web-gate

- [ ] **Step 1: 零回归 grep**

```bash
grep -rn "SignalOutcome(\|aggregate_signal_stats\|resolve_marker_hit_fields\|_serialize_risk_metrics\|oos_json" tests/ src/ --include="*.py" -l
```

逐文件核:(a)精确 dict/键集断言(resolver 11 键/payload 9 键)已补;(b)stat 桩缺 `oos_json` 的静默 None 是否影响断言;(c)`_serialize_risk_metrics` 更名后无残留引用。结论进报告(方法与结论一致,勿标注未执行的验证手段——1e 前车之鉴)。

- [ ] **Step 2: 文档**

`docs/signal-credibility.md` 追加"样本外 holdout 切分"节(按现有文档结构落笔),要点必含:切点口径(per-market 交易日期格点分位,非日历/非事件配额——fraction 切的是时间轴)、embargo 语义(跨切点窗两边不算,防泄漏)、undated 与 baseline 侧 undated 静默剔除差异、退化情形(格点<2)、分类宇宙(win/loss;expired 不进 OOS 计数,与 risk_metrics 含 expired 的口径差异)、守恒恒等式、excess 点估计口径 vs SignalStat.excess、与 verified 的关系(不影响)、生效前提(配置>0 且重跑 --signal-backtest)、legacy NULL 语义、切点随批跑滑动(写时快照)、跨市场/跨 interval 不可比。

`docs/CHANGELOG.md` `[Unreleased]` 顶部追加一行(扁平,禁 `###`):

```markdown
- [新功能] 链路B 信号统计新增样本外 holdout 切分披露(opt-in SIGNAL_BACKTEST_OOS_FRACTION,默认 0 关闭且行为不变;per-market 交易日期格点分位切点,train/OOS 两段胜率/基准/超额并排披露,跨切点窗口 embargo 防泄漏剔除;落库 signal_stats.oos_json,API 经 /signals 顶层 map 与看板行透出;描述性统计不影响 verified 徽章)
```

- [ ] **Step 3: 全量门禁 + web-gate**

```bash
cd /root/chainb-oos && export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && (./scripts/ci_gate.sh > .superpowers/sdd/ci_gate_t6.log 2>&1; echo $? > .superpowers/sdd/ci_gate_t6.exit) &
```

后台+退出码文件模式(历史耗时 ~650s 超 Bash 单次上限);轮询 `.exit` 出现后读真实退出码与 pytest 汇总行。Expected: 全绿(基线 3933 + 本增量 ~25-30),exit 0。

web-gate(settingsHelp.ts 已触前端面):

```bash
cd /root/chainb-oos/apps/dsa-web && npm ci && npm run lint && npm run build
```

(worktree 为无空格路径,npm ci 可用;Expected: lint 0 error,build 成功。)

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: signal-credibility 补样本外 holdout 切分节(切点口径/embargo/守恒恒等式/诚实边界/生效前提)并记 CHANGELOG"
```

---

## Self-Review(plan 作者已核)

- **Spec 覆盖**:§4.1(SignalOutcome/_eval)→T1;§3+§4.1(aggregate)→T2;§4.6→T3;§4.2/§4.3/§4.4→T4;§4.5→T5;§4.7/§8→T6。§7 测试 1-14 映射:1→T2(既有邻域)+T4(服务层 NULL 对偶),2→T2,3→T2,4→T2(embargo end 缺失用例),5→T2,6→T2,7→T2,8→T4,9→T4,10→T5(含 C1 键集),11→T3(loader)+T2(函数侧钳),12→T1,13→T4,14→T2。无缺口。
- **Placeholder 扫描**:T4 服务级接线测试的夹具部分明示"照抄 :27/:104 既有测试适配"——这是对既有 60 行夹具的复用指令而非 TBD;断言语义完整给出。其余任务代码完整。
- **类型一致性**:`window_end_date`/`oos_fraction`/`SignalStat.oos`/`oos_json`/resolver 键 `oos`/`oos_by_signal_type`/`BoardEntry.oos` 全链命名一致;`_serialize_cell_json`/`_parse_json_dict` 在 T4 定义、仅 T4 使用;`_derive_market_cutoffs` T2 定义并测试。
- **顺序**:T1→T2(window_end_date);T3 独立(仅被 T4 接线消费);T2+T3→T4→T5;T6 收尾。SDD 串行执行防同文件冲突。
- **判别式非 tautology 复核**:T2 cutoff 两组算例已手算(0.75×4=3.0 精确/0.7×4 浮点 floor 安全);守恒恒等式 7 事件手算成立(2+2+2+1=7);双市场用例 b015/a079 手算成立(floor(0.8×19)=15/floor(0.8×99)=79)。
