# M3.1 细粒度信号字段(horizon/plan_quality/status) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给信号资产补三个确定性派生字段——`horizon`(该信号胜率被验证的前看窗口)、`plan_quality`(交易计划完整度+一致性)、`status`(生命周期),在实时 `/signals`、信号看板与 K线 tooltip 呈现;全 additive、transient-only、对既有产出只读。

**Architecture:** 三字段在编排层 `build_signals_for_code` compute-on-read:horizon 经 resolver 透出、status 用 bar 序列推导、plan_quality 用已填的 price_lines+consistency 推导;看板取驱动 hit_rate 的同一代表 marker(结构同源);Web 走既有 `api/stocks.ts` 显式 snake→camel 映射 + `credibility.ts` 纯格式化 + drilldown/board 渲染。

**Tech Stack:** Python 3 / pydantic v2 / pytest;React + TypeScript(apps/dsa-web)/ vitest。

**Spec:** `docs/superpowers/specs/2026-06-22-signal-finer-fields-design.md`(已评审通过)。

## Global Constraints

- **新字段冻结命名**(逐字一致,贯穿 schema/builder/mapper/test):
  - `SignalMarker`(后端 snake / 前端 camel):`horizon_bars`/`horizonBars`(int|None)、`status`/`status`(`active`|`aging`|`expired`|None)
  - `SignalsResponse`:`plan_quality`/`planQuality`(`high`|`medium`|`low`|None)
  - `BoardEntry`:`horizon_bars`/`horizonBars`、**`signal_status`/`signalStatus`**(生命周期;**不复用** `BoardEntry.status`——后者已是 `ok`|`degraded`)、`plan_quality`/`planQuality`
- **`horizon_label` 不是后端字段**(spec 精化):后端只出 `horizon_bars`(int);人读标签由前端 `credibility.ts` 的 `formatHorizon` 按 UI 语言格式化。
- **计算层 = 编排层 `build_signals_for_code`**(price_lines 填完后、df 在手);**不在** `build_signals_payload`(price_lines 恒 null、无 df)。
- **看板 horizon/signal_status 与该行 hit_rate 同源**:扩展 `_hit_fields_from_markers` 从同一代表 rule marker 一并返回(结构保证 D7)。
- **horizon/status 仅对 `source=="rule"` marker**;LLM marker 两者 None。
- **plan_quality 规则**:price_lines 全 null→None;`entry is None or stop is None` 或 `consistency=="conflict"`→`low`;`entry+stop+target` 齐全且 `consistency=="consistent"`→`high`;其余(含 `divergent`/`unknown`/`stale`)→`medium`。
- **status 规则**:`bars_since = last_idx - marker_idx`;窗口 `W = horizon_bars`(命中)否则 `config.signal_backtest_horizon_bars`(默认 10);`==0`→active,`0<·<W`→aging,`>=W`→expired;找不到对应 bar→None。
- **transient-only**:不进 DB/迁移/回填;历史不变;不动既有 hit_rate/CI/verified/price_lines/consistency/resonance/回测引擎。`.env.example` 不改、无新配置。
- 提交信息英文类型前缀 + 中文描述,**无 `Co-Authored-By`**、无工具前缀;未经用户确认不 push/tag。
- 验证:后端 `./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`);前端 `cd apps/dsa-web && npm ci && npm run lint && npm run build`(本 worktree `/root/dsa-m3-1` 无空格,可直接跑)。
- Python:`/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`(路径含空格,命令需精确加引号);pytest 从 `/root/dsa-m3-1` 跑。

---

## File Structure

- **Modify** `api/v1/schemas/stocks.py` — SignalMarker/SignalsResponse/BoardEntry 加字段(Task 1)。
- **Modify** `src/services/signal_hit_rate.py` — resolver 透出 horizon(Task 2)。
- **Modify** `src/services/signals_service.py` — `_marker_from_vpsignal`/`_llm_marker` 加 horizon_bars/status 默认 + horizon 映射(Task 2);新增纯helper `compute_plan_quality`/`compute_marker_statuses`(Task 3)。
- **Modify** `src/services/signal_board_service.py` — 编排层 compute-on-read 调用 + `_hit_fields_from_markers` 扩展 + `_entry_from_board_signals` 加 plan_quality(Task 4)。
- **Create** `tests/test_signal_finer_fields.py` — 后端 schema/horizon/helper/wiring 测试(Task 1-4)。
- **Modify** `apps/dsa-web/src/types/kline.ts`、`apps/dsa-web/src/api/stocks.ts`、`apps/dsa-web/src/utils/credibility.ts`、`apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`、`apps/dsa-web/src/components/board/SignalBoardGroup.tsx` + 各 `__tests__`(Task 5)。
- **Create/Modify** `docs/signal-finer-fields.md`、`docs/CHANGELOG.md`(Task 6)。

---

### Task 1: 后端 schema 三字段

**Files:**
- Modify: `api/v1/schemas/stocks.py`(SignalMarker:111-132、SignalsResponse:143-155、BoardEntry:169-200)
- Test: `tests/test_signal_finer_fields.py`(新建)

**Interfaces:**
- Produces: `SignalMarker` +`horizon_bars:Optional[int]=None`、`status:Optional[Literal["active","aging","expired"]]=None`;`SignalsResponse` +`plan_quality:Optional[Literal["high","medium","low"]]=None`;`BoardEntry` +`horizon_bars`、`signal_status:Optional[Literal["active","aging","expired"]]=None`、`plan_quality`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_signal_finer_fields.py`:
```python
# -*- coding: utf-8 -*-
from api.v1.schemas.stocks import SignalMarker, SignalsResponse, BoardEntry


def _marker(**over):
    base = dict(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", source="rule", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
    )
    base.update(over)
    return base


def test_marker_finer_fields_default_none():
    m = SignalMarker(**_marker())
    assert m.horizon_bars is None
    assert m.status is None


def test_marker_finer_fields_set():
    m = SignalMarker(**_marker(horizon_bars=10, status="active"))
    assert m.horizon_bars == 10
    assert m.status == "active"


def test_signals_response_plan_quality_default_and_set():
    r = SignalsResponse(status="ok", consistency="consistent")
    assert r.plan_quality is None
    r2 = SignalsResponse(status="ok", consistency="consistent", plan_quality="high")
    assert r2.plan_quality == "high"


def test_board_entry_finer_fields():
    e = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None}, status="ok",
        horizon_bars=20, signal_status="aging", plan_quality="medium",
    )
    assert e.horizon_bars == 20
    assert e.signal_status == "aging"      # 区别于 e.status(ok/degraded)
    assert e.status == "ok"
    assert e.plan_quality == "medium"


def test_board_entry_finer_fields_default_none():
    e = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None}, status="ok",
    )
    assert e.horizon_bars is None and e.signal_status is None and e.plan_quality is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q`
Expected: FAIL(`SignalMarker`/`BoardEntry` 无 horizon_bars 等字段 → ValidationError 或 AttributeError)。

- [ ] **Step 3: 加 schema 字段**

In `api/v1/schemas/stocks.py`, `SignalMarker` 在 `as_of`(:132)之后加:
```python
    horizon_bars: Optional[int] = Field(None, description="该信号 hit_rate/CI 的验证前看窗口(bar 数);仅 rule、命中 signal_stats 时有值")
    status: Optional[Literal["active", "aging", "expired"]] = Field(None, description="信号生命周期;仅 rule。active=最新bar/aging=窗口内/expired=窗口已过")
```
`SignalsResponse` 在 `resonance`(:153-155)之后加:
```python
    plan_quality: Optional[Literal["high", "medium", "low"]] = Field(None, description="交易计划质量:price_lines 完整度 + consistency;无 price_lines→null")
```
`BoardEntry` 在 `degraded_reason`(:200)之后加:
```python
    horizon_bars: Optional[int] = Field(None, description="代表信号的验证前看窗口(bar 数),与本行 hit_rate 同源")
    signal_status: Optional[Literal["active", "aging", "expired"]] = Field(None, description="代表信号的生命周期(区别于 status 的 ok/degraded)")
    plan_quality: Optional[Literal["high", "medium", "low"]] = Field(None, description="交易计划质量(该股响应级)")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q`
Expected: PASS(5 passed)。

- [ ] **Step 5: 提交**
```bash
cd /root/dsa-m3-1
git add api/v1/schemas/stocks.py tests/test_signal_finer_fields.py
git commit -m "feat: 信号契约新增 horizon_bars/status/plan_quality(SignalMarker/SignalsResponse/BoardEntry)"
```

---

### Task 2: horizon 透出(resolver + marker builder)

**Files:**
- Modify: `src/services/signal_hit_rate.py`(resolve_marker_hit_fields:73-111)
- Modify: `src/services/signals_service.py`(_marker_from_vpsignal:120-171、_llm_marker:174-209)
- Test: `tests/test_signal_finer_fields.py`(追加)

**Interfaces:**
- Consumes: SignalMarker schema(Task 1)。
- Produces: `resolve_marker_hit_fields` 返回 dict 追加 `"horizon"` 键(命中→该 signal_stats 行的 horizon int;未命中→None);rule marker dict 含 `"horizon_bars"`(映射自 resolver 的 horizon)与 `"status": None` 默认;LLM marker dict 含 `"horizon_bars": None`、`"status": None`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_signal_finer_fields.py`)
```python
import types as _types
from src.services import signals_service as _ss


def test_marker_from_vpsignal_maps_horizon_and_status_default():
    sig = _types.SimpleNamespace(
        timestamp=1000, price=10.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
        threshold=None, observed_value=None,
    )
    # resolver 返回含 horizon
    resolver = lambda st, code: {"hit_rate": 0.6, "hit_sample": 30, "verified": True,
                                 "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1, "horizon": 10}
    m = _ss._marker_from_vpsignal(sig, code="600519", hit_fields_resolver=resolver)
    assert m["horizon_bars"] == 10
    assert m["status"] is None  # status 由编排层后填,builder 默认 None


def test_marker_from_vpsignal_no_resolver_horizon_none():
    sig = _types.SimpleNamespace(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", confidence="low", is_daily_approx=False,
        is_anomalous=False, reason="r", threshold=None, observed_value=None,
    )
    m = _ss._marker_from_vpsignal(sig)
    assert m["horizon_bars"] is None and m["status"] is None
```
And a resolver-level test (mirrors existing signal_hit_rate tests; mock the repo):
```python
def test_resolver_returns_horizon_key(monkeypatch):
    from src.services import signal_hit_rate as shr
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "A")
    stat = _types.SimpleNamespace(win_rate=0.6, sample=99, ci_low=0.55,
                                  ci_high=0.7, baseline_win_rate=0.5, excess=0.05)
    monkeypatch.setattr(shr.SignalStatsRepository, "get", lambda self, st, mkt, horizon=None: stat)
    out = shr.resolve_marker_hit_fields("volume_breakout", "600519")
    assert out["horizon"] == int(shr.get_config().signal_backtest_horizon_bars)
    assert out["hit_rate"] == 0.6


def test_resolver_none_path_horizon_none(monkeypatch):
    from src.services import signal_hit_rate as shr
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "A")
    monkeypatch.setattr(shr.SignalStatsRepository, "get", lambda self, st, mkt, horizon=None: None)
    out = shr.resolve_marker_hit_fields("x", "600519")
    assert out["horizon"] is None and out["hit_rate"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q -k "horizon or status_default"`
Expected: FAIL(`horizon`/`horizon_bars` 键缺失)。

- [ ] **Step 3a: resolver 透出 horizon**

In `src/services/signal_hit_rate.py`:
将 `_none`(:82-83)改为含 horizon:
```python
    _none = {"hit_rate": None, "hit_sample": None, "verified": False,
             "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": None}
```
将成功返回(:104-111)追加 horizon:
```python
    return {
        "hit_rate": stat.win_rate,
        "hit_sample": stat.sample,
        "verified": verified,
        "ci_low": stat.ci_low,
        "ci_high": stat.ci_high,
        "baseline_excess": stat.excess,
        "horizon": horizon,
    }
```

- [ ] **Step 3b: marker builder 加 horizon_bars/status**

In `src/services/signals_service.py` `_marker_from_vpsignal`:marker 默认 dict(:148-156)在 `"as_of": None,` 之后(或同处)加两键:
```python
        "horizon_bars": None,
        "status": None,
```
resolver 回填块(:157-170)内追加:
```python
            marker["horizon_bars"] = fields.get("horizon")
```
(放在 `marker["baseline_excess"] = fields.get("baseline_excess")` 之后。)
`_llm_marker`(:188-209)的返回 dict 在 `"as_of": as_of,` 旁加 `"horizon_bars": None, "status": None,`(LLM 点恒 None)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**
```bash
cd /root/dsa-m3-1
git add src/services/signal_hit_rate.py src/services/signals_service.py tests/test_signal_finer_fields.py
git commit -m "feat: resolver 透出 horizon 并映射到 rule marker 的 horizon_bars"
```

---

### Task 3: 纯 helper compute_plan_quality + compute_marker_statuses

**Files:**
- Modify: `src/services/signals_service.py`(新增两纯函数,放在 `build_signals_payload` 附近)
- Test: `tests/test_signal_finer_fields.py`(追加)

**Interfaces:**
- Produces: `compute_plan_quality(price_lines: dict, consistency: str) -> Optional[str]`;`compute_marker_statuses(markers: list[dict], bar_dates: list[str], default_window: int) -> None`(in-place 给每条 `source=="rule"` marker 写 `status`)。

- [ ] **Step 1: 写失败测试**(追加)
```python
from src.services.signals_service import compute_plan_quality, compute_marker_statuses
from src.services.signals_service import date_str_to_epoch_ms


def test_plan_quality_rules():
    full = {"entry": 1.0, "stop": 0.9, "target": 1.2}
    assert compute_plan_quality(full, "consistent") == "high"
    assert compute_plan_quality(full, "divergent") == "medium"
    assert compute_plan_quality(full, "unknown") == "medium"
    assert compute_plan_quality(full, "conflict") == "low"
    assert compute_plan_quality({"entry": 1.0, "stop": 0.9, "target": None}, "consistent") == "medium"
    assert compute_plan_quality({"entry": 1.0, "stop": None, "target": 1.2}, "consistent") == "low"
    assert compute_plan_quality({"entry": None, "stop": None, "target": None}, "consistent") is None


def test_marker_statuses_active_aging_expired():
    dates = ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18"]  # idx 0..3, last=3
    def mk(date, horizon=None):
        return {"source": "rule", "timestamp": date_str_to_epoch_ms(date),
                "horizon_bars": horizon, "status": None}
    m_latest = mk("2026-06-18")           # bars_since=0 -> active
    m_one = mk("2026-06-17", horizon=5)   # bars_since=1 < 5 -> aging
    m_old = mk("2026-06-15", horizon=2)   # bars_since=3 >= 2 -> expired
    m_llm = {"source": "llm", "timestamp": date_str_to_epoch_ms("2026-06-18"), "status": None}
    compute_marker_statuses([m_latest, m_one, m_old, m_llm], dates, default_window=10)
    assert m_latest["status"] == "active"
    assert m_one["status"] == "aging"
    assert m_old["status"] == "expired"
    assert m_llm["status"] is None  # 不动 LLM


def test_marker_status_default_window_when_horizon_none():
    dates = ["2026-06-01", "2026-06-02", "2026-06-03"]  # last idx 2
    m = {"source": "rule", "timestamp": date_str_to_epoch_ms("2026-06-01"),
         "horizon_bars": None, "status": None}  # bars_since=2; W=default
    compute_marker_statuses([m], dates, default_window=2)  # 2>=2 -> expired
    assert m["status"] == "expired"


def test_marker_status_unmatched_timestamp_none():
    dates = ["2026-06-01", "2026-06-02"]
    m = {"source": "rule", "timestamp": 999999, "horizon_bars": None, "status": None}
    compute_marker_statuses([m], dates, default_window=10)
    assert m["status"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q -k "plan_quality or marker_status"`
Expected: FAIL(ImportError: compute_plan_quality)。

- [ ] **Step 3: 实现两纯函数**

In `src/services/signals_service.py`(在 `build_signals_payload` 定义之前或之后,模块级):
```python
def compute_plan_quality(price_lines: dict, consistency: str) -> Optional[str]:
    """交易计划质量(确定性,与可信度正交)。price_lines 全 null→None。"""
    entry = price_lines.get("entry") if isinstance(price_lines, dict) else None
    stop = price_lines.get("stop") if isinstance(price_lines, dict) else None
    target = price_lines.get("target") if isinstance(price_lines, dict) else None
    if entry is None and stop is None and target is None:
        return None
    if entry is None or stop is None or consistency == "conflict":
        return "low"
    if target is not None and consistency == "consistent":
        return "high"
    return "medium"


def compute_marker_statuses(markers: list, bar_dates: list, default_window: int) -> None:
    """in-place 给每条 source==rule marker 写 status(active/aging/expired);找不到 bar→None。"""
    ts_to_idx = {}
    for i, d in enumerate(bar_dates):
        try:
            ts_to_idx[date_str_to_epoch_ms(str(d))] = i
        except Exception:
            continue
    last_idx = len(bar_dates) - 1
    for m in markers:
        if m.get("source") != "rule":
            continue
        idx = ts_to_idx.get(int(m.get("timestamp", -1)))
        if idx is None:
            m["status"] = None
            continue
        bars_since = last_idx - idx
        w = m.get("horizon_bars") or default_window
        if bars_since <= 0:
            m["status"] = "active"
        elif bars_since < w:
            m["status"] = "aging"
        else:
            m["status"] = "expired"
```
(`date_str_to_epoch_ms` 已在本模块导入,见 `_llm_marker` 用法。`Optional`/`list` 类型已可用。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**
```bash
cd /root/dsa-m3-1
git add src/services/signals_service.py tests/test_signal_finer_fields.py
git commit -m "feat: 新增 compute_plan_quality 与 compute_marker_statuses 纯派生 helper"
```

---

### Task 4: 编排层接线 + 看板同源聚合

**Files:**
- Modify: `src/services/signal_board_service.py`(build_signals_for_code:111-145、_hit_fields_from_markers:171-183、_entry_from_board_signals:186-201)
- Test: `tests/test_signal_finer_fields.py`(追加)

**Interfaces:**
- Consumes: `compute_plan_quality`/`compute_marker_statuses`(Task 3)、marker.horizon_bars(Task 2)。
- Produces: `build_signals_for_code` 的 `payload` 含 `plan_quality` 且 markers 的 rule 项含 `status`;`_hit_fields_from_markers` 返回追加 `horizon_bars`+`signal_status`(取自首条 rule marker);`_entry_from_board_signals` 含 `plan_quality`。

- [ ] **Step 1: 写失败测试**(追加;mock StockService/引擎太重 → 直接测 `_hit_fields_from_markers` + 编排后处理逻辑用小函数化的断言)

为可单测,测 `_hit_fields_from_markers` 扩展 + 一个"编排后处理"等价断言:
```python
from src.services import signal_board_service as _sbs


def test_hit_fields_from_markers_includes_horizon_and_signal_status():
    markers = [
        {"source": "rule", "signal_type": "a", "hit_rate": 0.6, "hit_sample": 30,
         "verified": True, "ci_low": 0.5, "ci_high": 0.7, "baseline_excess": 0.1,
         "horizon_bars": 10, "status": "aging"},
        {"source": "rule", "signal_type": "b", "hit_rate": 0.9, "horizon_bars": 20,
         "status": "active"},  # 第二条不应被取
    ]
    out = _sbs._hit_fields_from_markers(markers)
    assert out["hit_rate"] == 0.6           # 第一条 rule marker
    assert out["horizon_bars"] == 10        # 与 hit_rate 同源(同一条)
    assert out["signal_status"] == "aging"  # 同一条的 status


def test_hit_fields_from_markers_empty_has_horizon_keys():
    out = _sbs._hit_fields_from_markers([])
    assert out["horizon_bars"] is None and out["signal_status"] is None
```

D7 同源回归 + plan_quality/status 编排(用 monkeypatch 把重依赖替成轻 stub):
```python
def test_build_signals_for_code_fills_plan_quality_and_status(monkeypatch):
    rows = [{"date": "2026-06-16", "close": 10.0}, {"date": "2026-06-17", "close": 10.5},
            {"date": "2026-06-18", "close": 11.0}]
    # 引擎返回两条 rule marker(不同 bar/type),resolver 给 horizon
    eng_markers = [
        {"timestamp": _sbs_ts("2026-06-16"), "source": "rule", "signal_type": "a",
         "horizon_bars": None, "status": None},
        {"timestamp": _sbs_ts("2026-06-18"), "source": "rule", "signal_type": "b",
         "horizon_bars": 5, "status": None},
    ]
    _stub_build_signals_for_code(monkeypatch, rows, eng_markers,
                                 price_lines={"entry": 1.0, "stop": 0.9, "target": 1.2},
                                 consistency="consistent")
    bs = _sbs.build_signals_for_code("600519", days=120)
    payload = bs.signals_payload
    assert payload["plan_quality"] == "high"          # 全线 + consistent
    statuses = {m["signal_type"]: m["status"] for m in payload["markers"] if m["source"] == "rule"}
    assert statuses["a"] == "expired"  # 2026-06-16 bars_since=2 >= W(default 10? no -> a.horizon None -> default)
    assert statuses["b"] == "active"   # 2026-06-18 bars_since=0
```
> 实现提示:`_sbs_ts` = `date_str_to_epoch_ms`;`_stub_build_signals_for_code` 用 monkeypatch 替换 `StockService.get_history_data`(返回 `{"data": rows, "stock_name": "x"}`)、`compute_volume_price_signals`(返回带 `.markers=eng_markers, .status="ok", .degraded_reason=None`)、`StockTrendAnalyzer`、`DatabaseManager.get_instance`、`derive_price_levels`+`build_price_lines`(返回给定 price_lines)、`resonance` 深抓。实现者按既有 test_signal_board_service.py 的 stub 模式补全(见该文件现有 monkeypatch 写法)。a 的 horizon 经 resolver 为 None → status 用 default_window;断言按 default=10 时 bars_since=2<10 → 应为 **aging**,请按实际 default 校正断言值。

> 注:若 stub 编排过重,可改为更聚焦的"后处理"测试:直接构造 payload(markers+price_lines+consistency)→ 调用编排层抽出的后处理(见 Step 3 是否抽 `_augment_payload_finer_fields`)。**推荐 Step 3 抽一个可单测的后处理函数**,把这条测试落到该函数上(更稳、不依赖重 stub)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py -q -k "hit_fields or for_code"`
Expected: FAIL。

- [ ] **Step 3: 实现编排接线 + 看板扩展**

(a) 在 `signal_board_service.py` 顶部 import 处加:`from src.config import get_config`(已 `from src.config import parse_env_float, parse_env_int`,改为一并导入 `get_config`),并 `from src.services.signals_service import build_signals_payload, buy_signal_to_direction, STALE_TRADING_DAYS_DEFAULT, compute_plan_quality, compute_marker_statuses`(扩 :21 import)。

(b) **抽可单测后处理函数**(放在 `build_signals_for_code` 之前):
```python
def _augment_payload_finer_fields(payload: dict, rows: list) -> None:
    """编排层 compute-on-read:填 plan_quality(响应级)+ 各 rule marker 的 status(in-place)。"""
    payload["plan_quality"] = compute_plan_quality(
        payload.get("price_lines") or {}, payload.get("consistency", "unknown")
    )
    bar_dates = [str(r.get("date")) for r in rows]
    default_window = int(get_config().signal_backtest_horizon_bars)
    compute_marker_statuses(payload.get("markers") or [], bar_dates, default_window)
```
在 `build_signals_for_code` 的 price_lines 填充(`:124`)之后、resonance 之前,调用:
```python
    _augment_payload_finer_fields(payload, rows)
```

(c) `_hit_fields_from_markers`(:171-183)扩展:命中分支 dict 追加两键、兜底 dict 追加两键:
```python
def _hit_fields_from_markers(markers: list) -> dict:
    for m in markers:
        if m.get("source") == "rule":
            return {
                "hit_rate": m.get("hit_rate"),
                "hit_sample": m.get("hit_sample"),
                "verified": bool(m.get("verified", False)),
                "ci_low": m.get("ci_low"),
                "ci_high": m.get("ci_high"),
                "baseline_excess": m.get("baseline_excess"),
                "horizon_bars": m.get("horizon_bars"),
                "signal_status": m.get("status"),
            }
    return {"hit_rate": None, "hit_sample": None, "verified": False,
            "ci_low": None, "ci_high": None, "baseline_excess": None,
            "horizon_bars": None, "signal_status": None}
```

(d) `_entry_from_board_signals`(:190-201)返回 dict 追加(在 `**_hit_fields_from_markers(markers)` 已带 horizon_bars/signal_status,只需再加 plan_quality):
```python
        "plan_quality": payload.get("plan_quality"),
```
(`_degraded_entry` 无需改:BoardEntry 三新字段默认 None。)

把上面的 D7/编排测试改写为针对 `_augment_payload_finer_fields` 的聚焦单测(构造 payload+rows 直接断言),避免重 stub。

- [ ] **Step 4: 跑测试 + 既有 signals/board 回归**

Run: `cd /root/dsa-m3-1 && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_service.py -q -m "not network"`
Expected: PASS(新测试全绿 + 既有信号/看板测试无回归)。

- [ ] **Step 5: 提交**
```bash
cd /root/dsa-m3-1
git add src/services/signal_board_service.py tests/test_signal_finer_fields.py
git commit -m "feat: 编排层填 plan_quality/status + 看板同源(代表 marker)聚合 horizon/signal_status"
```

---

### Task 5: Web 呈现(类型 + 映射 + 格式化 + drilldown + 看板列)

**Files:**
- Modify: `apps/dsa-web/src/types/kline.ts`、`apps/dsa-web/src/api/stocks.ts`、`apps/dsa-web/src/utils/credibility.ts`、`apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`、`apps/dsa-web/src/components/board/SignalBoardGroup.tsx`
- Test: `apps/dsa-web/src/api/__tests__/stocks.signals.test.ts`、`stocks.board.test.ts`、`src/utils/__tests__/credibility.test.ts`、`src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`、`src/components/board/__tests__/SignalBoard.test.tsx`

**Interfaces:**
- Consumes: 后端 snake 字段 `horizon_bars`/`status`/`plan_quality`/`signal_status`(Tasks 1-4)。
- Produces: TS `SignalMarker.horizonBars/status`、`SignalsResponse.planQuality`、`BoardEntry.horizonBars/signalStatus/planQuality`;`formatHorizon`/`markerStatusLabel` 格式化函数;drilldown + 看板列渲染。

- [ ] **Step 1: 写失败测试**

(a) `src/utils/__tests__/credibility.test.ts` 追加:
```ts
import { formatHorizon, markerStatusLabel } from '../credibility';
it('formats horizon bars', () => {
  expect(formatHorizon(10)).toBe('窗口 10 根');
  expect(formatHorizon(null)).toBeNull();
});
it('labels marker status', () => {
  expect(markerStatusLabel('active')).toBe('最新');
  expect(markerStatusLabel('aging')).toBe('窗口内');
  expect(markerStatusLabel('expired')).toBe('已过窗');
  expect(markerStatusLabel(null)).toBeNull();
});
```
(b) `src/api/__tests__/stocks.signals.test.ts` 追加映射断言(mock data 加 snake 字段):
```ts
it('maps horizon_bars/status/plan_quality snake→camel', async () => {
  get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent',
    degraded_reason: null, resonance: 'none', plan_quality: 'high',
    price_lines: { entry: null, stop: null, target: null },
    markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish',
      signal_type: 'a', source: 'rule', confidence: 'high', is_daily_approx: false,
      is_anomalous: false, reason: 'r', threshold: null, observed_value: null,
      hit_rate: null, hit_sample: null, verified: false, ci_low: null, ci_high: null,
      baseline_excess: null, as_of: null, horizon_bars: 10, status: 'active' }] } });
  const res = await stocksApi.getSignals('600519');
  expect(res.planQuality).toBe('high');
  expect(res.markers[0].horizonBars).toBe(10);
  expect(res.markers[0].status).toBe('active');
});
```
(c) `src/api/__tests__/stocks.board.test.ts` 追加 board 映射(snake `horizon_bars/signal_status/plan_quality` → camel)。
(d) `src/components/kline/__tests__/SignalDrilldownPanel.test.tsx` 追加:rule marker 带 `horizonBars:5, status:'active'` → `getByTestId('drilldown-horizon')`/`'drilldown-marker-status'` 有文本;null → `queryByTestId(...)` 不存在。
(e) `src/components/board/__tests__/SignalBoard.test.tsx` 追加:`mk({ planQuality:'high', signalStatus:'aging', horizonBars:10 })` → `getByTestId('board-plan-quality')` 等存在;null → 不存在。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/dsa-m3-1/apps/dsa-web && npx vitest run src/utils/__tests__/credibility.test.ts src/api/__tests__/stocks.signals.test.ts src/components/kline/__tests__/SignalDrilldownPanel.test.tsx src/components/board/__tests__/SignalBoard.test.tsx`
Expected: FAIL(导出/字段/testid 不存在)。

- [ ] **Step 3a: TS 类型**(`src/types/kline.ts`)

`SignalMarker`(:48 `asOf` 后)加:`horizonBars: number | null;` `status: 'active' | 'aging' | 'expired' | null;`
`SignalsResponse`(:63 `resonance` 后)加:`planQuality: 'high' | 'medium' | 'low' | null;`
`BoardEntry`(:89 `resonance` 后)加:`horizonBars: number | null;` `signalStatus: 'active' | 'aging' | 'expired' | null;` `planQuality: 'high' | 'medium' | 'low' | null;`

- [ ] **Step 3b: 显式映射**(`src/api/stocks.ts`)

`RawSignalMarker`(:22-42)加 `horizon_bars?: number | null; status?: 'active'|'aging'|'expired'|null;`;`mapSignalMarker`(:53-73)加 `horizonBars: raw.horizon_bars ?? null, status: raw.status ?? null,`。
`getSignals` 返回(:179-190)加 `planQuality: (data.plan_quality ?? null),`。
`RawBoardEntry`(:75-85)加 `horizon_bars?: number | null; signal_status?: 'active'|'aging'|'expired'|null; plan_quality?: 'high'|'medium'|'low'|null;`;`mapBoardEntry`(:91-101)加 `horizonBars: raw.horizon_bars ?? null, signalStatus: raw.signal_status ?? null, planQuality: raw.plan_quality ?? null,`。

- [ ] **Step 3c: 格式化函数**(`src/utils/credibility.ts`,在 :37 后)
```ts
export function formatHorizon(horizonBars: number | null): string | null {
  return horizonBars == null ? null : `窗口 ${horizonBars} 根`;
}
const _MARKER_STATUS_LABEL: Record<string, string> = {
  active: '最新', aging: '窗口内', expired: '已过窗',
};
export function markerStatusLabel(status: 'active' | 'aging' | 'expired' | null): string | null {
  return status == null ? null : (_MARKER_STATUS_LABEL[status] ?? null);
}
```

- [ ] **Step 3d: drilldown 渲染**(`src/components/kline/SignalDrilldownPanel.tsx`,MarkerCard 信用行 `</div>`(:82)之后)
```tsx
        {marker.horizonBars != null && (
          <span data-testid="drilldown-horizon">{formatHorizon(marker.horizonBars)}</span>
        )}
        {marker.status != null && (
          <span data-testid="drilldown-marker-status">{markerStatusLabel(marker.status)}</span>
        )}
```
(顶部 import 处补 `formatHorizon, markerStatusLabel`。)

- [ ] **Step 3e: 看板列**(`src/components/board/SignalBoardGroup.tsx`)

`<thead>`(:29 `<th>命中率</th>` 后、`<th>工作台</th>` 前)加:`<th>计划/窗口/状态</th>`。
`<tbody>` 信用 cell `</td>`(:66)后、工作台 `<td>`(:68)前加:
```tsx
                <td>
                  {entry.planQuality != null && (
                    <span data-testid="board-plan-quality">{entry.planQuality}</span>
                  )}
                  {entry.horizonBars != null && (
                    <span data-testid="board-horizon">{formatHorizon(entry.horizonBars)}</span>
                  )}
                  {entry.signalStatus != null && (
                    <span data-testid="board-signal-status">{markerStatusLabel(entry.signalStatus)}</span>
                  )}
                </td>
```
(顶部 import `formatHorizon, markerStatusLabel`。)

- [ ] **Step 4: 跑测试 + lint + build**

Run: `cd /root/dsa-m3-1/apps/dsa-web && npx vitest run src/utils/__tests__/credibility.test.ts src/api/__tests__/stocks.signals.test.ts src/api/__tests__/stocks.board.test.ts src/components/kline/__tests__/SignalDrilldownPanel.test.tsx src/components/board/__tests__/SignalBoard.test.tsx && npm run lint && npm run build`
Expected: 测试 PASS;`npm run lint`(eslint .)无错;`npm run build` 成功。

- [ ] **Step 5: 提交**
```bash
cd /root/dsa-m3-1
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/utils/credibility.ts apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx apps/dsa-web/src/components/board/SignalBoardGroup.tsx apps/dsa-web/src/utils/__tests__/credibility.test.ts apps/dsa-web/src/api/__tests__/stocks.signals.test.ts apps/dsa-web/src/api/__tests__/stocks.board.test.ts apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx
git commit -m "feat: Web 信号细粒度字段(horizon/plan_quality/status)映射、格式化、drilldown 与看板列渲染"
```

---

### Task 6: 文档 + 全量门禁

**Files:**
- Create: `docs/signal-finer-fields.md`;Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 文档**

Create `docs/signal-finer-fields.md`(简版专题:三字段定义/派生规则/计算层/transient-only/看板同源/局限,镜像 docs/capital-flow.md 结构),关键写明:horizon 与 hit_rate 同源、plan_quality 与可信度正交、status 时间相对仅实时、看板代表 marker = 首条 rule(偏 aging/expired)。

`docs/CHANGELOG.md` `[Unreleased]` 扁平追加一行(无 ### 标题):
```markdown
- [新功能] 信号资产新增细粒度字段:horizon(胜率验证窗口,与 hit_rate 同源)/plan_quality(交易计划完整度+一致性,与可信度正交)/status(active/aging/expired 生命周期),实时 /signals + 信号看板 + K线 drilldown 呈现(transient-only,不进 DB/历史,对决策与既有字段只读)
```

- [ ] **Step 2: 全量门禁**

Run: `cd /root/dsa-m3-1 && ./scripts/ci_gate.sh`(若 bare `python` 不可用,等价跑 `"/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m flake8 --select=E9,F63,F7,F82 api/ src/ && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest -m "not network" -q`)
Expected: flake8 critical 干净 + pytest 全绿。
Run(前端):`cd /root/dsa-m3-1/apps/dsa-web && npm ci && npm run lint && npm run build` → 全绿。

- [ ] **Step 3: 提交**
```bash
cd /root/dsa-m3-1
git add docs/signal-finer-fields.md docs/CHANGELOG.md
git commit -m "docs: 信号细粒度字段专题 + CHANGELOG"
```

---

## Self-Review

**1. Spec coverage:**

| Spec | Task |
| --- | --- |
| §4.1 契约三字段 | Task 1(+ board `signal_status` 命名修正;horizon_label 移前端) |
| §4.2 horizon 派生(resolver 透出) | Task 2 |
| §4.2 plan_quality / status 规则 | Task 3(纯 helper)+ Task 4(编排接线) |
| §4.3 计算层=编排层 | Task 4(`_augment_payload_finer_fields`,price_lines 填后) |
| §4.3.4 / D7 看板同源 | Task 4(`_hit_fields_from_markers` 扩展)+ §Step1 同源回归 |
| §4.4 Web 呈现 | Task 5 |
| §5 字段契约 / §6 边界(LLM None/degraded/无 stats/未匹配 bar) | Tasks 2-4 + 测试 |
| §8 测试矩阵 | Tasks 1-5 测试 |
| §9 门禁 / 文档 | Task 6 |

**2. Placeholder scan:** Task 4 Step 1 含"实现提示/推荐改聚焦测试"——已在 Step 3 落实为可单测的 `_augment_payload_finer_fields`,实现者据此把测试落到该函数(非占位,是给重 stub 的更稳替代,代码已给全)。其余步骤均含完整代码。

**3. Type consistency:** 后端 snake `horizon_bars/status/plan_quality/signal_status` ↔ 前端 camel `horizonBars/status/planQuality/signalStatus` 全链一致;marker 用 `status`(无冲突),board 用 `signal_status`(避让既有 `BoardEntry.status`);resolver 返回键 `horizon` → marker `horizon_bars`,贯穿一致。

---

## 偏离 spec 的两处精化(写码时坐实,需交付说明)

1. **`horizon_label` 移到前端**:spec §4.1 原列为后端 SignalMarker 字段;坐实发现 marker builder 无 `report_language`、且既有 marker `reason` 本就 Chinese-native 不本地化。改为后端只出 `horizon_bars`(int)、前端 `credibility.ts:formatHorizon` 按 UI 语言格式化——更干净的 i18n,且不改既有 marker 语言惯例。
2. **看板生命周期字段 = `signal_status`**:`BoardEntry.status` 已占用(ok/degraded),故看板用 `signal_status`;SignalMarker 仍用 `status`(无冲突,与 spec 一致)。
