# 链路B 信号风险画像 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给链路B 每个 (signal_type×market) 格子计算不年化风险画像(Sharpe/Sortino/maxDD/worst),含收益数据管道、与链路A 共享数学、落库与 API map 透出,外加 D7 现役 resolver-cache bug 前置修复。

**Architecture:** 三重门拆共核加收益维度(保守跳空感知+形态级失真守卫)→ SignalOutcome 携带 return_pct/date → aggregate per-cell 收益序列(expired 计入,excluded 披露)→ 共享纯函数 `risk_metrics_from_returns`(链路A 重构为薄调用方,golden 全等锁字节级)→ `risk_metrics_json` 落库(NULL=legacy)→ resolver→marker(内存)→ SignalsResponse 顶层 map(O(K))+BoardEntry 单 dict;SignalMarker 刻意不声明(防载荷膨胀)。

**Tech Stack:** Python stdlib(math/statistics/json)+ SQLAlchemy + Pydantic v2。零新依赖、零新配置。

**Spec:** `docs/superpowers/specs/2026-07-02-chainb-risk-metrics-design.md`(v3,两轮对抗审查收敛)。

## Global Constraints

- **工作区**:无空格 worktree `/root/chainb-risk`(`git worktree add /root/chainb-risk -b feature/chainb-risk-metrics main`);所有 python 命令前置 `export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(引号必须);`python -m pytest`;禁 `| tail`。
- **commit**:英文类型前缀+中文体,无 Co-Authored-By;每 task 一 commit。
- **classify_triple_barrier 签名/返回/语义零变**(薄 wrapper);既有测试(tests/test_signal_backtest.py、test_signal_eval_vectorization.py)不改一字全绿。
- **失真守卫是形态级**:`target <= entry` → return_pct=None,**win/loss/expired 三态一致**(放 outcome 分支之外)——放进 win 分支=Blocker 级选择偏差,review 必打回。
- **expired 计入收益序列**(D8):聚合过滤条件=`return_pct is not None`,不限 outcome。
- **全键 dict 恒落**(D4):格子存在即落 dict(sample=0 也落);DB NULL 仅 legacy 一义;ORM 门=`is not None` 非 truthy。
- **共享函数两参**:`risk_metrics_from_returns(returns, sort_keys=None)`;moment 统计按输入序;非有限值 zip 后**成对过滤**(单侧过滤=索引错位,review 必打回)。
- **链路A 输出字节级不变**:golden 快照 `==` 全等 + `json.dumps` 键序全等(note 末位);Inc 1a 既有测试(tests/test_backtest_risk_metrics.py 等)不改一字全绿。
- **SignalMarker Pydantic 不声明 `risk_metrics`**(刻意剥离防膨胀);`SignalsResponse.risk_metrics_by_signal_type` 顶层 map;`BoardEntry.risk_metrics` 单 dict。
- ci_gate 基线 **3903 passed**(Inc 1c 后 main;Inc 1b 前端分支未合不影响后端基线)。
- 不 push、不 tag;合并方式待用户确认。

---

### Task 1: D7 前置修复——resolver per-call cache 键 code → (signal_type, code)

**Files:**
- Modify: `src/services/signals_service.py`(effective_resolver 闭包 `:294-303` 区 + 上方陈旧注释 `:289-293`)
- Test: `tests/test_signals_service.py`(追加)

**Interfaces:**
- Consumes: 既有 `build(...)`(signals_service 纯函数)与 `_marker_from_vpsignal`。
- Produces: 修复后的 cache 语义——同股不同 signal_type 各查各的 stats;Task 6 的 risk_metrics 正确性依赖此。

- [ ] **Step 1: 写 RED 测试(先证现役 bug)**(`tests/test_signals_service.py` 末尾追加;import 区按该文件既有模式补 `from src.services import signals_service as _ss` 等——照抄文件头)

```python
# --- D7: per-call resolver cache 曾按 code-only 缓存,跨 signal_type 错挂 stats(现役 bug 修复) ---
import types as _t


def _mk_sig(sig_type):
    return _t.SimpleNamespace(
        timestamp=1000, price=10.0, anchor="low", direction="bullish",
        signal_type=sig_type, confidence="high", is_daily_approx=False,
        is_anomalous=False, reason="x", threshold=None, observed_value=None,
    )


def test_resolver_cache_keyed_by_signal_type_and_code():
    """同股两个不同 signal_type 的 marker 必须各拿各的 resolver fields。

    修复前:cache 键=code,第二个 signal_type 拿到第一个的 fields(hit_rate/verified 全错挂)。
    """
    calls = []

    def resolver(signal_type, code):
        calls.append(signal_type)
        return {"hit_rate": 0.9 if signal_type == "volume_breakout" else 0.1,
                "hit_sample": 30, "verified": signal_type == "volume_breakout",
                "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": 10,
                "ci_low_corrected": None, "family_size": None}

    engine_result = _t.SimpleNamespace(
        status="ok", degraded_reason=None,
        markers=[_mk_sig("volume_breakout"), _mk_sig("obv_top_divergence")],
    )
    payload = _ss.build(
        engine_result=engine_result, rule_signal=None, llm_record=None,
        latest_bar_date="2026-06-01", latest_close=10.0,
        trading_days_elapsed=0, code="600519", hit_fields_resolver=resolver,
    )
    m1, m2 = payload["markers"][0], payload["markers"][1]
    assert m1["hit_rate"] == 0.9 and m1["verified"] is True
    assert m2["hit_rate"] == 0.1 and m2["verified"] is False      # 修复前=0.9/True(错挂)
    # 缓存仍有效:每 signal_type 恰好一次 resolver 调用
    assert sorted(calls) == ["obv_top_divergence", "volume_breakout"]
```

注意:`_ss.build` 的真实签名以文件内 `def build(` 为准(位置/关键字参数照既有测试调用形态抄——该文件已有 build 的测试可参照;若参数名不同,以实现文件为准调整调用,断言不变)。

- [ ] **Step 2: 跑测试确认 RED**

Run: `cd /root/chainb-risk && python -m pytest tests/test_signals_service.py -x -q`
Expected: 新测试 FAIL——`m2["hit_rate"] == 0.1` 断言失败(实际 0.9,错挂第一个 signal_type 的 fields)。既有测试全绿。

- [ ] **Step 3: 修复**(`src/services/signals_service.py`)

闭包改为(并同步订正上方 `:289-293` 陈旧注释——原注释援引 M2c"聚合源只取决于 code",M3-A6 后已失真):

```python
    # 终审#9 + D7 修正:按 (signal_type, code) 复用 resolver,避免同键重复 SELECT。
    # M3-A6 起 resolve_marker_hit_fields 按 (signal_type, market) 查 signal_stats,
    # signal_type 改变聚合源——曾按 code-only 缓存导致同股非首个 signal_type 的
    # marker 错挂第一个 signal_type 的 hit_rate/verified 等全部字段(现役 bug,已修)。
    # 缓存仅存活于本次调用,不用 module-level/lru_cache(防跨请求陈旧数据)。
    effective_resolver = hit_fields_resolver
    if hit_fields_resolver is not None and code:
        _per_call_cache: dict = {}

        def effective_resolver(signal_type: str, resolver_code: str) -> dict:
            cache_key = (signal_type, resolver_code)
            if cache_key in _per_call_cache:
                return _per_call_cache[cache_key]
            fields = hit_fields_resolver(signal_type, resolver_code)
            _per_call_cache[cache_key] = fields
            return fields
```

- [ ] **Step 4: 跑测试确认 GREEN + 邻域回归**

Run: `python -m pytest tests/test_signals_service.py tests/test_signal_finer_fields.py tests/test_signals_endpoint.py -q`
Expected: 全绿(新测试过;既有测试若有依赖"同股共享一份 fields"的断言而变红,则该断言锁的是 bug 行为——按新语义更新断言并在报告中说明,预期极少)。

- [ ] **Step 5: Commit**

```bash
git add src/services/signals_service.py tests/test_signals_service.py
git commit -m "fix: 信号 resolver per-call 缓存键改为 (signal_type, code)(修复同股跨 signal_type 错挂 hit_rate/verified 等字段的现役缺陷,M3-A6 后聚合源含 signal_type)"
```

---

### Task 2: 三重门收益纯函数(共核拆分 + 保守跳空感知 + 形态级失真守卫)

**Files:**
- Modify: `src/services/signal_backtest.py`(imports 加 `import math`;`classify_triple_barrier` `:53-84` 拆共核;`_bars_as_dicts` `:87-89` 加 open;新增 `TripleBarrierResult`/`classify_triple_barrier_with_return`)
- Test: `tests/test_signal_backtest.py`(追加)

**Interfaces:**
- Consumes: 无(纯函数层)。
- Produces: `_classify_core(forward_bars, *, stop, target) -> tuple[str, Optional[int]]`;`TripleBarrierResult(outcome: str, return_pct: Optional[float])`(frozen dataclass);`classify_triple_barrier_with_return(forward_bars, *, stop, target, entry) -> TripleBarrierResult`——Task 4 的 `_eval` 消费。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_backtest.py` 末尾追加)

```python
# =====================================================================
# 链路B 风险画像:三重门收益(保守跳空感知 + 形态级失真守卫,spec §4.1/§7.2)
# =====================================================================
from src.services.signal_backtest import (
    TripleBarrierResult,
    _classify_core,
    classify_triple_barrier_with_return,
)


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


# entry=100, stop=95, target=110(良构:target>entry)
def test_win_no_gap_return_at_target():
    fwd = [_bar(101, 111, 100, 108)]
    r = classify_triple_barrier_with_return(fwd, stop=95.0, target=110.0, entry=100.0)
    assert r.outcome == "win"
    assert abs(r.return_pct - 10.0) < 1e-9          # (110-100)/100*100


def test_distortion_guard_is_shape_level_not_outcome_level():
    """Blocker 回归锚:target<=entry 的事件 win/loss/expired 一律 None(单边剔除=选择偏差)。"""
    # 几何:entry=120 已越过 target=110(动量形态),stop=95
    win_fwd = [_bar(118, 125, 117, 124)]            # high>=110 → win
    loss_fwd = [_bar(118, 119, 90, 92)]             # low<=95 → loss
    exp_fwd = [_bar(109.5, 109.8, 109.0, 109.5)]    # 不触任何门 → expired
    for fwd, expect_outcome in ((win_fwd, "win"), (loss_fwd, "loss"), (exp_fwd, "expired")):
        r = classify_triple_barrier_with_return(fwd, stop=95.0, target=110.0, entry=120.0)
        assert r.outcome == expect_outcome
        assert r.return_pct is None, f"{expect_outcome} 应被形态级守卫置 None"


def test_loss_gap_uses_worse_open():
    fwd = [_bar(90, 96, 88, 89)]                    # 跳空低开 90 < stop 95
    r = classify_triple_barrier_with_return(fwd, stop=95.0, target=110.0, entry=100.0)
    assert r.outcome == "loss"
    assert abs(r.return_pct - (-10.0)) < 1e-9       # (90-100)/100*100,按更差的 open


def test_loss_no_gap_uses_stop():
    fwd = [_bar(101, 103, 94, 96)]                  # open 101 > stop 95,盘中破位
    r = classify_triple_barrier_with_return(fwd, stop=95.0, target=110.0, entry=100.0)
    assert r.outcome == "loss"
    assert abs(r.return_pct - (-5.0)) < 1e-9        # 按 stop 95


def test_double_touch_three_variants():
    """同 bar 双触(high>=target 且 low<=stop)恒判 loss;exit 按 min(open,stop) 三变体。"""
    # 变体1:open < stop(跳空低开双杀)→ exit=open=90 → -10%
    r1 = classify_triple_barrier_with_return([_bar(90, 111, 88, 100)], stop=95.0, target=110.0, entry=100.0)
    assert r1.outcome == "loss" and abs(r1.return_pct - (-10.0)) < 1e-9
    # 变体2:stop < open < target → exit=stop=95 → -5%
    r2 = classify_triple_barrier_with_return([_bar(100, 111, 90, 99)], stop=95.0, target=110.0, entry=100.0)
    assert r2.outcome == "loss" and abs(r2.return_pct - (-5.0)) < 1e-9
    # 变体3:open >= target(高开双杀,真实本可开盘止盈)→ 保守 loss,exit=min(open,stop)=stop=95 → -5%
    r3 = classify_triple_barrier_with_return([_bar(112, 113, 90, 91)], stop=95.0, target=110.0, entry=100.0)
    assert r3.outcome == "loss" and abs(r3.return_pct - (-5.0)) < 1e-9   # 锁符号:loss 不得配正收益


def test_expired_uses_window_end_close():
    fwd = [_bar(101, 105, 99, 103), _bar(103, 106, 101, 104)]
    r = classify_triple_barrier_with_return(fwd, stop=95.0, target=110.0, entry=100.0)
    assert r.outcome == "expired"
    assert abs(r.return_pct - 4.0) < 1e-9           # (104-100)/100*100(D8:expired 计收益)


def test_defensive_guards_return_none_not_poison():
    fwd_ok = [_bar(101, 111, 100, 108)]
    # entry 无效
    assert classify_triple_barrier_with_return(fwd_ok, stop=95.0, target=110.0, entry=0.0).return_pct is None
    assert classify_triple_barrier_with_return(fwd_ok, stop=95.0, target=110.0, entry=float("nan")).return_pct is None
    # loss 触障 bar open 无效(NaN / 0.0 哨兵)→ 回退 stop,非 None 非假 -100
    r_nan = classify_triple_barrier_with_return([_bar(float("nan"), 96, 88, 89)], stop=95.0, target=110.0, entry=100.0)
    assert r_nan.outcome == "loss" and abs(r_nan.return_pct - (-5.0)) < 1e-9
    r_zero = classify_triple_barrier_with_return([_bar(0.0, 96, 88, 89)], stop=95.0, target=110.0, entry=100.0)
    assert abs(r_zero.return_pct - (-5.0)) < 1e-9
    # expired 窗末 close 无效 → None(终门)
    r_badc = classify_triple_barrier_with_return([_bar(101, 105, 99, float("nan"))], stop=95.0, target=110.0, entry=100.0)
    assert r_badc.outcome == "expired" and r_badc.return_pct is None


def test_clamp_at_minus_100_defensive():
    # 非物理构造(负 stop 强制 exit<0 → 收益 <-100):防御性钳位,真实 long OHLCV 不可达
    r = classify_triple_barrier_with_return([_bar(-150.0, 96, -160.0, 90)], stop=-140.0, target=110.0, entry=100.0)
    assert r.outcome == "loss"
    assert r.return_pct == -100.0


def test_wrapper_equals_core_outcome():
    from src.services.signal_backtest import classify_triple_barrier
    cases = [
        ([_bar(101, 111, 100, 108)], 95.0, 110.0),
        ([_bar(90, 96, 88, 89)], 95.0, 110.0),
        ([_bar(101, 105, 99, 103)], 95.0, 110.0),
        ([_bar(90, 111, 88, 100)], 95.0, 110.0),
    ]
    for fwd, stop, target in cases:
        assert classify_triple_barrier(fwd, stop=stop, target=target) == _classify_core(fwd, stop=stop, target=target)[0]
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_backtest.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'TripleBarrierResult'`。

- [ ] **Step 3: 实现**(`src/services/signal_backtest.py`)

imports 追加 `import math`。`classify_triple_barrier`(`:53-84`)整体替换为 spec §4.1 的四件套(逐字转录 spec 代码块:`TripleBarrierResult` dataclass、`_classify_core`、薄 wrapper `classify_triple_barrier`、`classify_triple_barrier_with_return`——spec 中 docstring 一并转录;**守卫顺序:entry 有效性 → `if target <= entry` 形态级守卫 → outcome 分支 → 终门**)。`_bars_as_dicts` 改 `df[["open", "high", "low", "close"]].to_dict("records")`(docstring 同步)。

- [ ] **Step 4: 跑测试确认 GREEN + 既有零回归**

Run: `python -m pytest tests/test_signal_backtest.py tests/test_signal_eval_vectorization.py tests/test_signal_backtest_stats.py -q`
Expected: 全绿(既有 classify/向量化/聚合测试不改一字)。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest.py
git commit -m "feat: 三重门拆共核并新增保守跳空感知收益(win按target/loss按min(open,stop)带哨兵守卫/expired按窗末close;target<=entry 失真形态三态一律置 None 防单边截断偏差;既有 classify_triple_barrier 签名语义零变)"
```

---

### Task 3: 共享风险数学 risk_metrics_from_returns + 链路A 重构(golden 全等)

**Files:**
- Modify: `src/core/backtest_engine.py`(模块级新增 `risk_metrics_from_returns`;`_compute_risk_metrics` `:768-825` 重构为薄调用方)
- Test: `tests/test_backtest_risk_metrics.py`(追加 golden + 直调单测)

**Interfaces:**
- Consumes: 无。
- Produces: `risk_metrics_from_returns(returns: List[float], sort_keys: Optional[list] = None) -> Dict[str, Any]`(模块级,Task 4 import:`from src.core.backtest_engine import risk_metrics_from_returns`);键集 `sample/mean_return_pct/return_std_pct/sharpe/sortino/max_drawdown_pct/equity_final_pct/worst_single_return_pct`(无 note)。

- [ ] **Step 1: 写 golden 快照测试(先对旧实现取样)**

先写脚本跑一次**当前(重构前)**实现,取得字面量输出(实现者执行,把输出写死进测试):

```bash
python - <<'EOF'
import json, random
from types import SimpleNamespace
from src.core.backtest_engine import BacktestEngine
rows = [SimpleNamespace(position_recommendation="long", simulated_return_pct=r,
                        analysis_date=d, code=c)
        for r, d, c in [(5.2, "2026-01-02", "A"), (-3.1, "2026-01-02", "B"),
                        (12.0, "2026-01-03", "A"), (-8.4, "2026-01-05", "C"),
                        (0.0, "2026-01-04", "B"), (2.5, None, "D")]]
rng = random.Random(42)
rows += [SimpleNamespace(position_recommendation="long",
                         simulated_return_pct=round(rng.uniform(-30, 30), 4),
                         analysis_date=f"2026-02-{i+1:02d}", code=f"R{i%3}")
         for i in range(20)]
out = BacktestEngine._compute_risk_metrics(rows)
print(json.dumps(out, ensure_ascii=False))
EOF
```

然后把打印出的 JSON 逐字写进测试(`tests/test_backtest_risk_metrics.py` 末尾追加):

```python
# --- 链路B 风险画像:共享数学抽取的 golden 全等锁(spec §4.4/§7.4,永久回归) ---
import json as _json
import random as _random
from types import SimpleNamespace as _NS

from src.core.backtest_engine import risk_metrics_from_returns


def _golden_rows():
    rows = [_NS(position_recommendation="long", simulated_return_pct=r, analysis_date=d, code=c)
            for r, d, c in [(5.2, "2026-01-02", "A"), (-3.1, "2026-01-02", "B"),
                            (12.0, "2026-01-03", "A"), (-8.4, "2026-01-05", "C"),
                            (0.0, "2026-01-04", "B"), (2.5, None, "D")]]
    rng = _random.Random(42)   # seed 固定,离线确定性
    rows += [_NS(position_recommendation="long",
                 simulated_return_pct=round(rng.uniform(-30, 30), 4),
                 analysis_date=f"2026-02-{i+1:02d}", code=f"R{i%3}")
             for i in range(20)]
    return rows


# 由重构前实现生成(Step 1 脚本输出逐字粘贴)——重构后必须逐位/逐键序全等
_GOLDEN_JSON = '<粘贴 Step 1 脚本的单行 JSON 输出>'


def test_compute_risk_metrics_golden_byte_equivalence():
    out = BacktestEngine._compute_risk_metrics(_golden_rows())
    assert _json.dumps(out, ensure_ascii=False) == _GOLDEN_JSON


def test_risk_metrics_from_returns_direct():
    # 空/单元素
    empty = risk_metrics_from_returns([])
    assert empty["sample"] == 0 and empty["sharpe"] is None
    one = risk_metrics_from_returns([5.0])
    assert one["sample"] == 1 and one["sharpe"] is None and one["worst_single_return_pct"] == 5.0
    # 已知序列 pin(手算):[10, -10] → mean=0, std=stdev=14.1421..., sharpe=0.0
    two = risk_metrics_from_returns([10.0, -10.0])
    assert two["mean_return_pct"] == 0.0
    assert abs(two["return_std_pct"] - 14.1421) < 1e-4
    assert two["sharpe"] == 0.0
    # maxDD:equity 1.1*0.9=0.99,peak 1.1 → dd=(1.1-0.99)/1.1=10%
    assert abs(two["max_drawdown_pct"] - 10.0) < 1e-4


def test_pairwise_nonfinite_filter_keeps_alignment():
    """NEW-3 回归锚:非有限值成对剔除后 returns 与 sort_keys 仍对齐(maxDD 序正确)。"""
    returns = [10.0, float("nan"), -10.0]
    keys = [(False, "2026-01-03", 0), (False, "2026-01-01", 1), (False, "2026-01-02", 2)]
    out = risk_metrics_from_returns(returns, sort_keys=keys)
    assert out["sample"] == 2                       # NaN 成对剔除
    # 排序后遍历序=[-10(01-02), +10(01-03)]:equity 0.9→0.99,peak 峰值 1.0 → maxDD=10%
    assert abs(out["max_drawdown_pct"] - 10.0) < 1e-4


def test_sort_keys_affect_only_maxdd_not_moments():
    returns = [20.0, -15.0, 5.0]
    a = risk_metrics_from_returns(returns, sort_keys=[(False, "c", 0), (False, "a", 1), (False, "b", 2)])
    b = risk_metrics_from_returns(returns, sort_keys=None)
    assert a["mean_return_pct"] == b["mean_return_pct"]
    assert a["sharpe"] == b["sharpe"]
    assert a["max_drawdown_pct"] != b["max_drawdown_pct"]   # 序不同 maxDD 必不同(3 元非对称构造)
```

- [ ] **Step 2: 跑 golden 确认基线绿、直调测试 RED**

Run: `python -m pytest tests/test_backtest_risk_metrics.py -x -q`
Expected: golden 测试 PASS(旧实现自证);`risk_metrics_from_returns` 相关 FAIL(ImportError)。

- [ ] **Step 3: 实现**(`src/core/backtest_engine.py`)

模块级(class 外,邻 import 区之后或 class 之前)新增:

```python
def risk_metrics_from_returns(returns, sort_keys=None):
    """不年化风险指标共享纯函数(链路A/链路B 共用;数学与原 _compute_risk_metrics 逐行等价)。

    Args:
        returns:   已下钳(>=-100)收益列表(百分比数值);moment 统计(mean/std/sharpe/sortino)
                   按**输入序**求和——与链路A 重构前逐行等价。
        sort_keys: 与 returns 等长的排序键列表;仅 maxDD/equity 按 sorted(zip(sort_keys, ...))
                   的序遍历;None=输入序。非有限值(inf/NaN)按 (return, key) **成对剔除**
                   (单侧过滤会致索引错位、maxDD 排序静默错配)。

    Returns:
        dict:sample/mean_return_pct/return_std_pct/sharpe/sortino/max_drawdown_pct/
        equity_final_pct/worst_single_return_pct(不含 note——调用方按语境追加);
        round4;除零/未定义返 None,绝不 inf/NaN。
    """
    if sort_keys is None:
        pairs = [(i, r) for i, r in enumerate(returns)]
    else:
        pairs = list(zip(sort_keys, returns))
    pairs = [(k, r) for k, r in pairs if isinstance(r, (int, float)) and math.isfinite(r)]
    clean = [r for _, r in pairs]                     # 输入序(过滤后)
    n = len(clean)
    if n == 0:
        return {
            "sample": 0, "mean_return_pct": None, "return_std_pct": None,
            "sharpe": None, "sortino": None, "max_drawdown_pct": None,
            "equity_final_pct": None, "worst_single_return_pct": None,
        }

    mean_r = sum(clean) / n
    std_r = statistics.stdev(clean) if n >= 2 else None        # 样本 ddof=1
    sharpe = round(mean_r / std_r, 4) if (std_r is not None and std_r > 0) else None

    downside_sq = sum(x * x for x in clean if x < 0)
    downside_dev = math.sqrt(downside_sq / n)
    sortino = round(mean_r / downside_dev, 4) if (n >= 2 and downside_dev > 0) else None

    equity, peak, maxdd = 1.0, 1.0, 0.0
    for _key, ri in sorted(pairs, key=lambda t: t[0]):
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
        "worst_single_return_pct": round(min(clean), 4),
    }
```

`_compute_risk_metrics`(`:768-825`)重构为(过滤/下钳/排序键留此,数学全委托;**note 末位追加保 diagnostics_json 键序字节**):

```python
    @classmethod
    def _compute_risk_metrics(cls, completed: List[BacktestResultLike]) -> Dict[str, Any]:
        """信号收益序列的风险画像:不年化 Sharpe/Sortino + 事件净值 maxDD。

        总体=已完成、非 cash、有 simulated_return_pct 的评估;收益下钳 ≥ -100。
        数学委托 risk_metrics_from_returns(输出与历史逐位一致,golden 测试锁全等)。
        详见 docs/superpowers/specs/2026-07-01-chaina-risk-metrics-design.md。
        """
        rows = [
            r for r in completed
            if (getattr(r, "position_recommendation", None) or "") != "cash"
            and getattr(r, "simulated_return_pct", None) is not None
        ]
        returns = [max(float(r.simulated_return_pct), -100.0) for r in rows]
        sort_keys = [
            (
                getattr(r, "analysis_date", None) is None,
                getattr(r, "analysis_date", None) or "",
                getattr(r, "code", "") or "",
                i,
            )
            for i, r in enumerate(rows)
        ]
        metrics = risk_metrics_from_returns(returns, sort_keys=sort_keys)
        metrics["note"] = cls._RISK_NOTE
        return metrics
```

**排序键等价性注意**:原实现 key 第二元素是 `analysis_date`(可为 None,靠 leading-bool 保护——None 时 Python 比较 None 与 str 会炸?原实现 `(is None, analysis_date, ...)`:两行都 None 时第二元素 None==None 继续比 code,不炸;一 None 一 str 时 leading-bool 已分胜负,第二元素不比较——**Python 元组比较短路**,等价)。新实现用 `analysis_date or ""` 消 None——**语义差**:两行都 None 时原比较 None==None(相等)→ 新 ""==""(相等)——等价;但若某行 `analysis_date=""` 与 None 行:原 (False,"") vs (True,None) → False<True 前者在前;新 (False,"") vs (True,"") → 同序。**等价成立**,golden 覆盖含 None 行样本(`_golden_rows` 第 6 行 date=None)证之。

- [ ] **Step 4: 跑测试确认全绿(golden 全等 = 字节级担保)**

Run: `python -m pytest tests/test_backtest_risk_metrics.py tests/test_backtest_summary.py -q`
Expected: 全绿——golden `json.dumps` 全等含键序与 note 末位;Inc 1a 既有测试不改一字。

- [ ] **Step 5: Commit**

```bash
git add src/core/backtest_engine.py tests/test_backtest_risk_metrics.py
git commit -m "refactor: 抽共享纯函数 risk_metrics_from_returns(两参:moment 按输入序/maxDD 按 sort_keys 序,非有限值成对剔除防索引错位);链路A _compute_risk_metrics 改薄调用方,golden 快照锁输出逐位与键序全等"
```

---

### Task 4: SignalOutcome 收益管道 + 聚合 per-cell 风险指标

**Files:**
- Modify: `src/services/signal_backtest.py`(`SignalOutcome` `:39-50`;`_eval` `:124-139`;`SignalStat` 追加字段;`aggregate_signal_stats` `:292-382`;模块 note 常量;import `risk_metrics_from_returns`)
- Test: `tests/test_signal_backtest_stats.py`(追加)、`tests/test_signal_backtest.py`(_eval 贯通追加)

**Interfaces:**
- Consumes: Task 2 `classify_triple_barrier_with_return`;Task 3 `risk_metrics_from_returns`。
- Produces: `SignalOutcome.return_pct/date`(末尾默认字段);`SignalStat.risk_metrics: Optional[dict] = None`(末尾);per-cell dict 键集=共享函数 8 键 + `excluded/interval/horizon`(末位三键)。Task 5 落库消费。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_backtest_stats.py` 末尾追加:

```python
# =====================================================================
# 链路B 风险画像:per-cell risk_metrics 聚合(spec §4.3/§7.5/§7.6)
# =====================================================================


def _o(sig, mkt, outcome, ret=None, date=None):
    return SignalOutcome(sig, mkt, outcome, return_pct=ret, date=date)


def test_cell_risk_metrics_includes_expired_and_counts_excluded():
    outs = [
        _o("volume_breakout", "cn", "win", 10.0, "2026-01-02"),
        _o("volume_breakout", "cn", "loss", -5.0, "2026-01-03"),
        _o("volume_breakout", "cn", "expired", 2.0, "2026-01-04"),   # D8:expired 计入
        _o("volume_breakout", "cn", "win", None, "2026-01-05"),      # 失真 win(excluded)
        _o("volume_breakout", "cn", "loss", None, "2026-01-06"),     # 失真 loss(excluded)
    ]
    base = [SignalOutcome("__baseline__", "cn", "win")] * 5 + [SignalOutcome("__baseline__", "cn", "loss")] * 5
    stats = aggregate_signal_stats(outs, base, horizon=10, interval="5m", min_sample=3)
    s = stats[0]
    rm = s.risk_metrics
    assert rm is not None
    assert rm["sample"] == 3                     # 10,-5,2(expired 计入;两 None 剔除)
    assert rm["excluded"] == 2                   # 失真 win + 失真 loss 都计
    assert rm["interval"] == "5m" and rm["horizon"] == 10
    assert "note" not in rm                      # D5:note 不入 dict
    # 胜率分母不变式:sample(win+loss)=4,与 risk sample=3 不定序共存
    assert s.sample == 4


def test_all_excluded_cell_still_gets_full_dict_not_none():
    """NEW-4 回归锚:100% 剔除时落全键 dict(sample=0/excluded=N),None 仅 legacy 一义。"""
    outs = [_o("x", "cn", "win", None, "2026-01-02"), _o("x", "cn", "loss", None, "2026-01-03")]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    stats = aggregate_signal_stats(outs, base, horizon=10)
    rm = stats[0].risk_metrics
    assert rm is not None
    assert rm["sample"] == 0 and rm["excluded"] == 2
    assert rm["sharpe"] is None and rm["max_drawdown_pct"] is None


def test_cell_maxdd_uses_date_order_not_input_order():
    """maxDD 排序判别式(3 元非对称):date 序 ≠ 输入序时 maxDD 不同;mean 不随序变。"""
    # date 序:-50, +100, -50 → equity 0.5→1.0→0.5,maxDD=50%
    # 输入序:+100, -50, -50 → equity 2.0→1.0→0.5,maxDD=75%
    outs = [
        _o("x", "cn", "win", 100.0, "2026-01-02"),
        _o("x", "cn", "loss", -50.0, "2026-01-01"),
        _o("x", "cn", "loss", -50.0, "2026-01-03"),
    ]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    rm = aggregate_signal_stats(outs, base, horizon=10)[0].risk_metrics
    assert abs(rm["max_drawdown_pct"] - 50.0) < 1e-4      # date 序生效(输入序会是 75%)
    assert abs(rm["mean_return_pct"] - 0.0) < 1e-4        # moment 不随序变


def test_dates_all_none_falls_back_to_input_order_no_typeerror():
    outs = [_o("x", "cn", "win", 100.0), _o("x", "cn", "loss", -50.0), _o("x", "cn", "loss", -50.0)]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    rm = aggregate_signal_stats(outs, base, horizon=10)[0].risk_metrics
    assert abs(rm["max_drawdown_pct"] - 75.0) < 1e-4      # 输入序


def test_legacy_signaloutcome_construction_still_works():
    o = SignalOutcome("x", "cn", "win")                    # 既有三参构造零破坏
    assert o.return_pct is None and o.date is None
```

`tests/test_signal_backtest.py` 末尾追加 _eval 贯通:

```python
def test_eval_produces_outcomes_with_return_and_date():
    import pandas as pd
    from src.services.signal_backtest import evaluate_baseline_outcomes
    n = 80
    df = pd.DataFrame({
        "date": [f"2026-03-{(i % 28) + 1:02d}" for i in range(n)],
        "open": [100.0 + i * 0.1 for i in range(n)],
        "high": [101.0 + i * 0.1 for i in range(n)],
        "low": [99.0 + i * 0.1 for i in range(n)],
        "close": [100.5 + i * 0.1 for i in range(n)],
        "volume": [1_000_000] * n,
    })
    outs = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    assert outs, "应产出 baseline outcome"
    assert all(o.date is not None for o in outs)
    assert any(o.return_pct is not None for o in outs)
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_backtest_stats.py tests/test_signal_backtest.py -x -q`
Expected: FAIL — `SignalOutcome.__init__` 无 `return_pct` 关键字 / `SignalStat` 无 `risk_metrics`。

- [ ] **Step 3: 实现**(`src/services/signal_backtest.py`)

1. imports 追加 `from src.core.backtest_engine import risk_metrics_from_returns`。
2. `SignalOutcome` 追加两字段(spec §4.2 代码,docstring 同步)。
3. 模块常量(邻 `BASELINE_SIGNAL_TYPE`):

```python
SIGNAL_RISK_NOTE = (
    "信号流三重门事件序列(毛收益,保守跳空感知:win按target/loss按min(open,stop)/"
    "expired按窗末close;entry=触发bar close;target<=entry 失真形态整层剔除计excluded;"
    "非组合回撤;不年化;窗口可重叠自相关;描述性统计无CI未经多重检验校正)"
)
```

(注:此常量**不进 per-cell dict**(D5),供 Task 6 的 Field description 与 Task 7 文档引用——单一真源放数据模块。)
4. `SignalStat` 末尾追加 `risk_metrics: Optional[dict] = None`(docstring 字段表同步+失哈希契约注释)。
5. `_eval` 循环体改造(`:124-139`):

```python
    for t in range(min_history, n - horizon):
        lv = levels[t]
        if lv.stop is None or lv.target is None:
            continue
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue
        entry = float(df.iloc[t]["close"])
        date = str(df.iloc[t]["date"])
        r = classify_triple_barrier_with_return(fwd, stop=lv.stop, target=lv.target, entry=entry)
        if all_bars:
            out.append(SignalOutcome(
                signal_type=BASELINE_SIGNAL_TYPE, market=market,
                outcome=r.outcome, return_pct=r.return_pct, date=date))
        else:
            for sig_type in sig_by_bar.get(t, ()):
                out.append(SignalOutcome(
                    signal_type=sig_type, market=market,
                    outcome=r.outcome, return_pct=r.return_pct, date=date))
```

(注意:同 bar 多信号共享同一 `r`——classify 只算一次,与原逐信号重复调用相比是顺带的 O(信号数) 优化,行为等价因入参相同。)
6. `aggregate_signal_stats`:Step 2 分桶顺路收集全量事件(含 expired,D8):

```python
    # Step 2: 按 (signal_type, market) 分桶统计 + 顺路收集收益序列(D8:expired 计入)
    buckets: dict = defaultdict(lambda: {"win": 0, "loss": 0})
    cell_events: dict = defaultdict(list)      # (sig_type, market) -> [(date, idx, return_pct)]
    for idx, o in enumerate(outcomes):
        if o.outcome in ("win", "loss"):
            buckets[(o.signal_type, o.market)][o.outcome] += 1
        if o.outcome in ("win", "loss", "expired"):
            cell_events[(o.signal_type, o.market)].append((o.date, idx, o.return_pct))
```

(**注意**:纯 expired 格子会进 cell_events 但不进 buckets → 不产 SignalStat 行——保持"格子=有 win/loss"现语义,cell_events 只为已有格子供数;Step 3c 用 `cell_events.get((sig_type, market), [])`。)
Step 3c 每格追加(SignalStat 构造前):

```python
        events = cell_events.get((sig_type, market), [])
        returns_in = [ret for _, _, ret in events if ret is not None]
        sort_keys_in = [(d is None, d or "", i) for d, i, ret in events if ret is not None]
        excluded = sum(1 for _, _, ret in events if ret is None)
        rm = risk_metrics_from_returns(returns_in, sort_keys=sort_keys_in)
        rm["excluded"] = excluded
        rm["interval"] = interval
        rm["horizon"] = horizon
```

`SignalStat(...)` 构造追加 `risk_metrics=rm,`。(全键 dict 恒落——`risk_metrics_from_returns([])` 返 sample=0 全键 dict,恰合 NEW-4。)

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_signal_backtest_stats.py tests/test_signal_backtest.py tests/test_signal_eval_vectorization.py tests/test_signal_backtest_service.py -q`
Expected: 全绿(既有 aggregate/服务测试不受影响——纯追加字段)。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest_stats.py tests/test_signal_backtest.py
git commit -m "feat: SignalOutcome 携带收益与日期,aggregate 每格计算 risk_metrics(expired 计入收益序列/excluded 披露失真与无效剔除/含 interval-horizon 自描述/全键 dict 恒落,maxDD 按 date→输入序)"
```

---

### Task 5: 落库(risk_metrics_json)+ 读路径 + 桩改

**Files:**
- Modify: `src/storage.py`(`SignalStatRow` 追加列;`_ensure_signal_stats_columns` 追加第三列+docstring)
- Modify: `src/services/signal_backtest_service.py`(ORM 构造 `:189` 区追加;import json 若无)
- Modify: `src/services/signal_hit_rate.py`(`_none` `:102-104`、返回 dict `:128-138`、docstring)
- Test: `tests/test_signal_stats_migration.py`、`tests/test_signal_hit_rate.py`、`tests/test_signal_finer_fields.py`(桩+断言)

**Interfaces:**
- Consumes: Task 4 `SignalStat.risk_metrics`。
- Produces: `SignalStatRow.risk_metrics_json`(Text nullable);resolver dict 第 10 key `"risk_metrics": Optional[dict]`——Task 6 消费。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_stats_migration.py` 追加(仿既有三测模式,reset_instance 全套):

```python
def test_risk_metrics_json_column_present_and_migrated(tmp_path):
    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh3.db'}")
        assert "risk_metrics_json" in _columns(db)
    finally:
        DatabaseManager.reset_instance()


def test_risk_metrics_json_roundtrip_and_null_legacy(tmp_path):
    import json
    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'rt3.db'}")
        rm = {"sample": 3, "sharpe": 1.2, "excluded": 1, "interval": "1d", "horizon": 10}
        row = SignalStatRow(signal_type="volume_breakout", market="cn", interval="1d",
                            horizon=10, win=2, loss=1, sample=3, win_rate=0.667,
                            ci_low=0.2, ci_high=0.9, baseline_win_rate=0.5, excess=-0.3,
                            risk_metrics_json=json.dumps(rm, ensure_ascii=False))
        legacy = SignalStatRow(signal_type="old_sig", market="cn", interval="1d",
                               horizon=10, win=1, loss=1, sample=2)
        with db.get_session() as s:
            s.add_all([row, legacy]); s.commit()
            fetched = s.query(SignalStatRow).filter_by(signal_type="volume_breakout").one()
            assert json.loads(fetched.risk_metrics_json)["sharpe"] == 1.2
            assert s.query(SignalStatRow).filter_by(signal_type="old_sig").one().risk_metrics_json is None
    finally:
        DatabaseManager.reset_instance()
```

`tests/test_signal_hit_rate.py`:`_make_stat`/`_stat` 补 `risk_metrics_json = None`(_make_stat 加 `m.risk_metrics_json = None`;_stat 加 `d.setdefault("risk_metrics_json", None)`);`:512` 区与 finer_fields SimpleNamespace 补同字段;两处精确 dict 断言补 `"risk_metrics": None`(9→10 keys)。末尾追加:

```python
# --- 链路B 风险画像:resolver 透出 risk_metrics(spec §4.5) ---
import json as _json


def test_resolver_returns_risk_metrics_dict():
    rm = {"sample": 3, "sharpe": 1.2, "excluded": 0, "interval": "1d", "horizon": 10}
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(risk_metrics_json=_json.dumps(rm))
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["risk_metrics"]["sharpe"] == 1.2


def test_resolver_risk_metrics_defensive_paths():
    for bad in (None, "not json{", "[1,2]", "42"):
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = _stat(risk_metrics_json=bad)
            assert resolve_marker_hit_fields("volume_breakout", "600519")["risk_metrics"] is None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_hit_rate.py -x -q`
Expected: FAIL — SignalStatRow 无 `risk_metrics_json` / resolver dict 无 `risk_metrics` key(精确 dict 断言先红)。

- [ ] **Step 3: 实现**

`storage.py`:`family_size` 列后追加 `risk_metrics_json = Column(Text)  # 该格风险画像 JSON;NULL=legacy 行(未重跑)`;`_ensure_signal_stats_columns` 追加:

```python
                if "risk_metrics_json" not in existing:
                    conn.execute(text(
                        "ALTER TABLE signal_stats ADD COLUMN risk_metrics_json TEXT"
                    ))
```

(docstring 由"ci_low_corrected/family_size"扩为"…/risk_metrics_json"。)

`signal_backtest_service.py`:文件头 import json(若无);ORM 构造 `family_size=s.family_size,` 后追加:

```python
                risk_metrics_json=(
                    json.dumps(s.risk_metrics, ensure_ascii=False, allow_nan=False)
                    if s.risk_metrics is not None else None
                ),
```

(`allow_nan=False` fail-closed;若担心单格异常拖垮整批,允许实现为 per-row helper 内 try/except ValueError → None 并 logger.warning——报告中写明取舍。)

`signal_hit_rate.py`:`_none` 追加 `"risk_metrics": None`;返回 dict 追加:

```python
    _rm_raw = getattr(stat, "risk_metrics_json", None)
    risk_metrics = None
    if _rm_raw:
        try:
            _parsed = json.loads(_rm_raw)
            if isinstance(_parsed, dict):
                risk_metrics = _parsed
        except (TypeError, ValueError):
            risk_metrics = None
```

(文件头 import json 若无;返回 dict 加 `"risk_metrics": risk_metrics,`;docstring keys 列表 9→10。)

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_hit_rate.py tests/test_signal_finer_fields.py tests/test_signal_backtest_service.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/storage.py src/services/signal_backtest_service.py src/services/signal_hit_rate.py tests/
git commit -m "feat: signal_stats 追加 risk_metrics_json 列(幂等补列,NULL=legacy,allow_nan=False fail-closed),resolver 透出 risk_metrics dict(json 防御:坏 JSON/非 dict 一律 None)"
```

---

### Task 6: surfacing——marker 内存 dict + SignalsResponse 顶层 map + BoardEntry(SignalMarker 刻意不声明)

**Files:**
- Modify: `src/services/signals_service.py`(`_marker_from_vpsignal` 基础 dict+回填块;`_llm_marker`;`build` return dict 前收敛 map)
- Modify: `src/services/signal_board_service.py`(`_hit_fields_from_markers` 两处 return + `_degraded_entry`)
- Modify: `api/v1/schemas/stocks.py`(`SignalsResponse` 加 map 字段;`BoardEntry` 加 `risk_metrics`;**SignalMarker 不加**;补 `Dict`/`Any` import)
- Test: `tests/test_signal_finer_fields.py`、`tests/test_signal_board_service.py`、`tests/test_signals_board_endpoint.py`(追加)

**Interfaces:**
- Consumes: Task 5 resolver dict 的 `risk_metrics` key;Task 1 修复后的 cache(risk_metrics 不错挂)。
- Produces: `SignalsResponse.risk_metrics_by_signal_type: Dict[str, Dict[str, Any]]`;`BoardEntry.risk_metrics: Optional[Dict[str, Any]]`;marker 内存 dict 键 `risk_metrics`(API 层剥离)。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_finer_fields.py` 追加:

```python
def test_marker_carries_risk_metrics_in_memory_dict():
    sig = _types.SimpleNamespace(
        timestamp=1000, price=10.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
        threshold=None, observed_value=None,
    )
    rm = {"sample": 3, "sharpe": 1.2, "excluded": 0, "interval": "1d", "horizon": 10}
    resolver = lambda st, code: {"hit_rate": 0.6, "hit_sample": 30, "verified": True,
                                 "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1,
                                 "horizon": 10, "ci_low_corrected": None, "family_size": None,
                                 "risk_metrics": rm}
    m = _ss._marker_from_vpsignal(sig, code="600519", hit_fields_resolver=resolver)
    assert m["risk_metrics"] == rm


def test_build_collects_risk_metrics_by_signal_type():
    rm_a = {"sample": 3, "sharpe": 1.2}
    rm_b = {"sample": 5, "sharpe": -0.4}

    def resolver(st, code):
        return {"hit_rate": 0.6, "hit_sample": 30, "verified": False,
                "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": 10,
                "ci_low_corrected": None, "family_size": None,
                "risk_metrics": rm_a if st == "volume_breakout" else rm_b}

    engine_result = _types.SimpleNamespace(
        status="ok", degraded_reason=None,
        markers=[_mk("volume_breakout"), _mk("volume_breakout"), _mk("obv_top_divergence")],
    )
    payload = _ss.build(
        engine_result=engine_result, rule_signal=None, llm_record=None,
        latest_bar_date="2026-06-01", latest_close=10.0,
        trading_days_elapsed=0, code="600519", hit_fields_resolver=resolver,
    )
    assert payload["risk_metrics_by_signal_type"] == {
        "volume_breakout": rm_a, "obv_top_divergence": rm_b,
    }   # O(K):两型三 marker 收敛两键
```

(`_mk` 为该测试文件内已有/新建的 VPSignal SimpleNamespace 工厂,同 Task 1 的 `_mk_sig` 形态;`_ss.build` 调用形态照文件内既有 build 测试。)

`tests/test_signal_board_service.py` 追加:

```python
def test_hit_fields_carry_risk_metrics_and_degraded_none():
    rm = {"sample": 3, "sharpe": 1.2}
    marker = {"source": "rule", "hit_rate": 0.6, "hit_sample": 30, "verified": False,
              "ci_low": None, "ci_high": None, "baseline_excess": None,
              "ci_low_corrected": None, "family_size": None,
              "horizon_bars": 10, "status": "active", "risk_metrics": rm}
    assert _hit_fields_from_markers([marker])["risk_metrics"] == rm
    assert _hit_fields_from_markers([])["risk_metrics"] is None
    assert _degraded_entry("600519", "x")["risk_metrics"] is None
```

`tests/test_signals_board_endpoint.py` 追加:

```python
# --- 链路B 风险画像:D5 载荷形态(BoardEntry 带 dict / SignalMarker 刻意剥离 / Response 顶层 map) ---
from api.v1.schemas.stocks import SignalsResponse


def test_board_entry_preserves_risk_metrics_dict():
    entry = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None},
        status="ok", risk_metrics={"sample": 3, "sharpe": 1.2},
    )
    assert entry.model_dump()["risk_metrics"]["sharpe"] == 1.2


def test_signal_marker_deliberately_strips_risk_metrics():
    """D5 反向断言:SignalMarker 不声明 risk_metrics——内存 marker 带、序列化剥离(防逐 bar 膨胀)。"""
    m = SignalMarker(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", source="rule", confidence="low",
        is_daily_approx=False, is_anomalous=False, reason="r",
        risk_metrics={"sample": 3},          # Pydantic extra=ignore:静默丢弃
    )
    assert "risk_metrics" not in m.model_dump()


def test_signals_response_top_level_map():
    resp = SignalsResponse(
        status="ok", markers=[], consistency="consistent", degraded_reason=None,
        risk_metrics_by_signal_type={"volume_breakout": {"sample": 3, "sharpe": 1.2}},
    )
    assert resp.model_dump()["risk_metrics_by_signal_type"]["volume_breakout"]["sample"] == 3
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py -x -q`
Expected: FAIL — marker 无 `risk_metrics` key / SignalsResponse 无 map 字段。

- [ ] **Step 3: 实现**

1. `signals_service._marker_from_vpsignal`:基础 dict 加 `'risk_metrics': None`;回填块加 `marker['risk_metrics'] = fields.get('risk_metrics')`;`_llm_marker` 加 `'risk_metrics': None`。
2. `signals_service.build`:markers 构造后、return dict 前收敛:

```python
    risk_by_type: dict = {}
    for m in markers:
        rm = m.get("risk_metrics")
        st = m.get("signal_type")
        if isinstance(rm, dict) and st and st not in risk_by_type:
            risk_by_type[st] = rm
```

return dict 追加 `"risk_metrics_by_signal_type": risk_by_type,`。
3. `signal_board_service._hit_fields_from_markers` 两处 return 加 `"risk_metrics": m.get("risk_metrics")` / `"risk_metrics": None`;`_degraded_entry` 加 `"risk_metrics": None`。
4. `api/v1/schemas/stocks.py`:typing import 补 `Dict, Any`;`SignalsResponse` 追加:

```python
    risk_metrics_by_signal_type: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="各信号型所在 (signal_type×market) 格子的风险画像(不年化 Sharpe/Sortino/maxDD/worst,毛收益保守跳空感知口径,expired 计入,excluded=失真/无效剔除数,含 interval/horizon 自描述)。描述性统计:无置信区间、未经多重检验校正,不得作为跨格子挑选依据(verified 才是校正后判据)。空 dict=无格子或 legacy 未重跑。",
    )
```

`BoardEntry` 追加:

```python
    risk_metrics: Optional[Dict[str, Any]] = Field(None, description="代表信号格子的风险画像(口径同 SignalsResponse.risk_metrics_by_signal_type);null=legacy 未重跑。描述性统计,不得作为跨格子挑选依据。")
```

**SignalMarker 不加任何字段**(D5 刻意)。

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py tests/test_signals_service.py tests/test_signals_endpoint.py tests/test_signal_board_resonance.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add src/services/signals_service.py src/services/signal_board_service.py api/v1/schemas/stocks.py tests/
git commit -m "feat: 风险画像 API 透出——SignalsResponse 顶层按 signal_type 收敛 map(O(K) 防逐 bar marker 载荷膨胀,SignalMarker 刻意不声明),BoardEntry 单 dict,Field description 标注描述性统计不得作跨格子挑选依据"
```

---

### Task 7: 文档 + CHANGELOG(两条)+ 零回归 grep + 全量门禁

**Files:**
- Modify: `docs/signal-credibility.md`(追加风险画像节)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 两条扁平)
- Test: 全量 `./scripts/ci_gate.sh`

- [ ] **Step 1: 零回归 grep**

```bash
grep -rn "SignalOutcome(\|classify_triple_barrier\|aggregate_signal_stats\|resolve_marker_hit_fields\|_compute_risk_metrics" tests/ src/ --include="*.py" -l
```

逐文件核:(a)精确 dict/键集断言(resolver 10 keys/marker 键集)已补;(b)stat 桩缺 `risk_metrics_json` 的静默 None 是否影响断言。结论进报告。

- [ ] **Step 2: 文档**

`docs/signal-credibility.md` 追加"风险画像"节(按现有文档结构落笔),要点必含:收益口径(保守跳空感知/entry=触发 close/收盘成交假设/exit 侧宁低估)、失真形态整层剔除+excluded、expired 计入(与链路A 同口径)、sample 三口径关系、重叠窗自相关与跨标的混流、无 CI 未校正不得作跨格子挑选(vs verified)、跨 interval 不可比、**生效前提=手动跑 `--signal-backtest`(无调度自动触发)**、legacy NULL 语义。

`docs/CHANGELOG.md` `[Unreleased]` 顶部追加两行(扁平,禁 `###`):

```markdown
- [新功能] 链路B 信号回测新增 per-(信号类型×市场) 风险画像:不年化 Sharpe/Sortino/事件净值最大回撤/最差单笔(毛收益保守跳空感知口径,expired 窗末平仓计入,失真形态整层剔除并以 excluded 计数披露);落库 signal_stats.risk_metrics_json(历史行为 null,重跑 --signal-backtest 生效),API 经 /signals 顶层 map 与看板行透出;描述性统计无置信区间,不得作为跨格子挑选依据
- [修复] 信号 resolver 单次调用缓存键由 code 改为 (signal_type, code):修复同股多信号类型时非首个类型的 hit_rate/verified/置信区间等字段错挂第一个类型数值的缺陷
```

- [ ] **Step 3: 全量门禁**

```bash
cd /root/chainb-risk && export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && ./scripts/ci_gate.sh
```

Expected: 全绿(基线 3903 + 本增量 ~30+ 新测试),真实退出码 0,不得 `| tail`。约 10 分钟,timeout 设 900000ms。无前端改动免 web-gate(Pydantic 变化已由后端测试覆盖)。

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: signal-credibility 补链路B 风险画像节(口径/诚实边界/手动触发前提)并记 CHANGELOG 两条(新功能+resolver 缓存修复)"
```

---

## Self-Review 结论(plan 作者已核)

- **Spec 覆盖**:D7→T1;§4.1/D1/D2→T2;§4.4/D3→T3;§4.2/§4.3/D8→T4;§4.5/D4→T5;§4.6/D5→T6;§6/§8→T7。§7 测试 1-11 映射:1/2→T2,3→T4(_eval),4→T3,5/6→T4,7/8→T5,9→T6,10→T1,11→T7。无缺口。
- **Placeholder**:golden 字面量由 Step 1 脚本实跑生成(snapshot 测试标准流程,已给完整脚本与粘贴指令);其余代码完整。
- **类型一致性**:`risk_metrics_from_returns(returns, sort_keys=None)`/`TripleBarrierResult`/`SignalOutcome(return_pct, date)`/`SignalStat.risk_metrics`/resolver key `risk_metrics`/`risk_metrics_by_signal_type` 全链命名一致;excluded/interval/horizon 三键在 T4 产、T5 落、T6 透、T7 文档一致。
- **顺序**:T1 独立先行;T2→T4;T3→T4;T4→T5→T6;T7 收尾。T1/T2/T3 之间无依赖(但 SDD 串行执行,防同文件冲突:T1(signals_service)与 T2/T3 不同文件,仍按序)。
- **排序键等价性论证**(T3 Step 3)已内嵌,golden 样本含 None-date 行覆盖之。
