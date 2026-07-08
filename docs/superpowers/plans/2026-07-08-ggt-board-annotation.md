# 信号看板港股通(GGT)可买性注解 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在信号看板每行(`BoardEntry`)呈现港股通可买性(eligibility)三态徽章 —— presence-only、fail-closed、不影响决策/信号/排序/计数。

**Architecture:** GGT 是 stock-level 属性(镜像 `market`/`resonance` 线),非 marker-derived。看板在 `build_board` 内并行算完 entries 后做一次**后置注解 pass**:一次性抓全市场 eligibility set(有界、进程级 12h 缓存、fail-closed None),对每个 `market=="HK" and status=="ok"` 的行浅拷贝并打三态。三态判定用与 `get_ggt_context` 同源的纯函数 `_ggt_eligible_state` 防漂移。

**Tech Stack:** Python(FastAPI/Pydantic v2、pytest)、TypeScript/React(vitest)、akshare 东财端点。

## Global Constraints

- 提交信息英文类型前缀 + 中文正文,单条 `git commit -m`,**不加 `Co-Authored-By`**、无工具/agent 前缀。
- 稳定性优先:默认 always-on 但 fail-closed;非 HK 看板行为字节级不变;`get_ggt_context` 重构 byte-identical。
- **不新增配置开关**:复用 `GGT_FETCH_TIMEOUT_SECONDS`(默认 20s)+ `GGT_LIST_CACHE_TTL_SECONDS`(12h)。
- 门控用 board entry 的**大写 `market=="HK"`**(`_infer_market` 产出,已 `.upper()`),不复用小写 `"hk"` 判定。
- **不特殊化 HK ETF**:镜像报告(`_is_etf_code` 对 HK 恒 False,报告不返 not_supported);HK ETF 不在成份表 → False。
- 注解**浅拷贝**命中行(`{**e, ...}`),不原地 mutate `_BOARD_CACHE` 缓存 dict。
- 前端字段 `?? null` legacy 容错;徽章中文硬编码(镜像看板 verified/resonance)。
- **环境**:主仓路径含空格会破坏前端 `npm ci`。执行须在**无空格持久 worktree**(如 `/root/ggt-board`)跑前后端全门禁;venv 在 `.venv/bin/python`,裸 python 命令须 `PATH=.venv/bin:$PATH` 前置,勿 `| tail` 掩盖退出码。
- 双门禁:后端 `./scripts/ci_gate.sh`;动了前端必跑 web-gate `cd apps/dsa-web && npm ci && npm run lint && npm run build`。

**Spec:** `docs/superpowers/specs/2026-07-08-ggt-board-annotation-design.md`

---

### Task 1: 适配器纯函数 `_ggt_eligible_state`

**Files:**
- Modify: `data_provider/fundamental_adapter.py`(在 `_ggt_key` 后,约 line 55)
- Test: `tests/test_ggt_adapter.py`

**Interfaces:**
- Consumes: 既有 `_ggt_key(code) -> str`(fundamental_adapter.py:41)。
- Produces: `_ggt_eligible_state(code: str, elig_set) -> Optional[bool]` —— `elig_set` 非 `set` → `None`;否则 `_ggt_key(code) in elig_set`(True/False)。供 Task 2(base.py `get_ggt_context`)与 Task 3(board service)共用。

- [ ] **Step 1: Write the failing test**

在 `tests/test_ggt_adapter.py` 末尾追加:

```python
from data_provider.fundamental_adapter import _ggt_eligible_state, _ggt_key


def test_ggt_eligible_state_true_when_key_in_set():
    s = {_ggt_key("00700")}
    assert _ggt_eligible_state("hk00700", s) is True
    assert _ggt_eligible_state("00700", s) is True          # 裸5位归一同键
    assert _ggt_eligible_state("00700.HK", s) is True        # .HK 后缀归一同键


def test_ggt_eligible_state_false_when_set_present_but_absent():
    assert _ggt_eligible_state("hk09999", {_ggt_key("00700")}) is False


def test_ggt_eligible_state_none_when_not_a_set():
    assert _ggt_eligible_state("hk00700", None) is None
    assert _ggt_eligible_state("hk00700", []) is None        # 非 set(空 list)→ None,非 False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_ggt_adapter.py::test_ggt_eligible_state_none_when_not_a_set -v`
Expected: FAIL — `ImportError: cannot import name '_ggt_eligible_state'`.

- [ ] **Step 3: Write minimal implementation**

在 `data_provider/fundamental_adapter.py` 的 `_ggt_key` 函数之后(约 line 55,`_DIVIDEND_KEYWORD_MAP` 之前)插入:

```python
def _ggt_eligible_state(code: str, elig_set) -> Optional[bool]:
    """港股通可买性三态(fail-closed):elig_set 非 set → None(unknown);否则本股归一键是否在成份集。

    与 base.py `get_ggt_context` 的 eligibility 推断同源,防读写两处判定漂移。
    """
    if not isinstance(elig_set, set):
        return None
    return _ggt_key(code) in elig_set
```

(`Optional` 已在本文件 typing 导入;若无则在文件头 `from typing import ... Optional` 补。)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_ggt_adapter.py -v`
Expected: PASS(既有 17 测试 + 新 3 测试全绿)。

- [ ] **Step 5: Commit**

```bash
git add data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git commit -m "feat: 港股通可买性三态纯函数 _ggt_eligible_state(isinstance(set) 守卫→True/False,否则 None;供 get_ggt_context 与看板注解共用防漂移)"
```

---

### Task 2: manager `get_ggt_eligibility_set()` 有界 + `get_ggt_context` 重构 byte-identical

**Files:**
- Modify: `data_provider/base.py`(`get_ggt_context` 内 import 行 3611、eligibility 行 3646;新方法加在 `get_ggt_context` 之后约 line 3664)
- Test: `tests/test_ggt_context.py`

**Interfaces:**
- Consumes: Task 1 的 `_ggt_eligible_state`;既有 `self._run_with_retry(task, timeout_seconds, task_name) -> (result, err, ms)`(base.py:2627,超时不抛、返 `(None, err, ms)`);`self._fundamental_adapter`(base.py:746);`config.ggt_fetch_timeout_seconds`。
- Produces: `DataFetcherManager.get_ggt_eligibility_set() -> Optional[set]` —— 有界(`GGT_FETCH_TIMEOUT_SECONDS`)抓全市场成份集,超时/异常/非 set → `None`。供 Task 3 mock+调用。

- [ ] **Step 1: Write the failing test**

在 `tests/test_ggt_context.py` 末尾追加:

```python
def test_get_ggt_eligibility_set_delegates_and_passes_through(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_ggt_eligibility_set",
        lambda: {_ggt_key("00700")},
    )
    got = mgr.get_ggt_eligibility_set()
    assert isinstance(got, set) and _ggt_key("00700") in got   # 透传 set,非返 tuple


def test_get_ggt_eligibility_set_none_on_adapter_none(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_eligibility_set", lambda: None)
    assert mgr.get_ggt_eligibility_set() is None


def test_get_ggt_eligibility_set_bounded_on_hung_adapter(monkeypatch):
    # 挂起适配器(网络库无超时场景)→ 方法在 leg_cap 内返 None,不阻塞。
    cfg = SimpleNamespace(fundamental_retry_max=1, ggt_fetch_timeout_seconds=0.3)

    def slow():
        time.sleep(3)
        return {_ggt_key("00700")}

    mgr = _mgr()
    monkeypatch.setattr(mgr._fundamental_adapter, "get_ggt_eligibility_set", slow)
    with patch("src.config.get_config", return_value=cfg):
        t0 = time.monotonic()
        got = mgr.get_ggt_eligibility_set()
        elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"get_ggt_eligibility_set blocked {elapsed:.2f}s on hung adapter"
    assert got is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_ggt_context.py::test_get_ggt_eligibility_set_none_on_adapter_none -v`
Expected: FAIL — `AttributeError: 'DataFetcherManager' object has no attribute 'get_ggt_eligibility_set'`.

- [ ] **Step 3a: 加 manager 方法**

在 `data_provider/base.py` 的 `get_ggt_context` 方法**之后**(约 line 3664,`get_board_context` 之前)插入:

```python
    def get_ggt_eligibility_set(self):
        """港股通全市场成份集(有界、fail-closed)。供看板注解一次性获取后逐行成员判定。

        适配器 get_ggt_eligibility_set 本身不经 G8 有界超时(那是 get_ggt_context._fetch_leg
        提供的);此处包 _run_with_retry 给看板路径同款护栏。超时/异常/非 set → None。
        """
        from src.config import get_config
        cap = max(0.0, float(get_config().ggt_fetch_timeout_seconds))
        if cap <= 0:
            return None
        try:
            payload, _err, _ms = self._run_with_retry(
                lambda: self._fundamental_adapter.get_ggt_eligibility_set(),
                cap, "ggt_eligibility",
            )
            return payload if isinstance(payload, set) else None
        except Exception:
            return None
```

- [ ] **Step 3b: 重构 get_ggt_context eligibility 行(byte-identical)**

`data_provider/base.py:3611` 的 import 行改为(加 `_ggt_eligible_state`):

```python
        from data_provider.fundamental_adapter import _ggt_key, _ggt_eligible_state
```

`data_provider/base.py:3646` 的 eligibility 行:

```python
        eligible = (_ggt_key(code) in elig_set) if isinstance(elig_set, set) else None
```

改为:

```python
        eligible = _ggt_eligible_state(code, elig_set)
```

(语义完全等价:`_ggt_eligible_state` 内部就是同一 `isinstance(set)` 守卫 + `_ggt_key(code) in elig_set`。`_ggt_key` 仍被 import 供该函数其余处使用——不要删 `_ggt_key` import。)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_ggt_context.py -v`
Expected: PASS —— 既有 12 测试(ok/failed/partial/one_leg_raises/hung 等,锁 get_ggt_context byte-identical)+ 新 3 测试全绿。

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py tests/test_ggt_context.py
git commit -m "feat: DataFetcherManager.get_ggt_eligibility_set 有界获取港股通成份集(包 _run_with_retry G8 护栏+三元组解包,超时/非set→None)+get_ggt_context eligibility 改调 _ggt_eligible_state byte-identical"
```

---

### Task 3: Pydantic `BoardEntry.ggt_eligible` + 看板后置注解 pass

**Files:**
- Modify: `api/v1/schemas/stocks.py`(`BoardEntry`,line 213 后)
- Modify: `src/services/signal_board_service.py`(顶部 import;新增 `_get_ggt_manager`/`_annotate_ggt`;`build_board` 接线;`_degraded_entry`)
- Test: `tests/test_signal_board_ggt.py`(新建)

**Interfaces:**
- Consumes: Task 1 `_ggt_eligible_state`;Task 2 `DataFetcherManager.get_ggt_eligibility_set`;既有 `_infer_market`(产大写 `"HK"`)、`_BOARD_CACHE`、`build_board`。
- Produces: 看板 entry dict 含 `ggt_eligible: Optional[bool]`;`BoardEntry` schema 新字段。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_signal_board_ggt.py`:

```python
# -*- coding: utf-8 -*-
"""Inc 2b:看板港股通可买性注解后置 pass。"""
from unittest.mock import patch

from data_provider.fundamental_adapter import _ggt_key
import src.services.signal_board_service as sbs


def _entry(code, market, status="ok"):
    return {"code": code, "market": market, "action_group": "hold", "status": "ok"
            if status == "ok" else "degraded"}


def _patch_set(mp, value):
    class _M:
        def get_ggt_eligibility_set(self_inner):
            return value
    mp.setattr(sbs, "_get_ggt_manager", lambda: _M())


def test_annotate_hk_ok_true_false(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    out = sbs._annotate_ggt([_entry("hk00700", "HK"), _entry("hk09999", "HK")])
    assert out[0]["ggt_eligible"] is True
    assert out[1]["ggt_eligible"] is False


def test_annotate_non_hk_untouched(monkeypatch):
    spy = {"calls": 0}

    class _M:
        def get_ggt_eligibility_set(self_inner):
            spy["calls"] += 1
            return {_ggt_key("00700")}
    monkeypatch.setattr(sbs, "_get_ggt_manager", lambda: _M())
    out = sbs._annotate_ggt([_entry("600519", "CN"), _entry("AAPL", "US")])
    assert "ggt_eligible" not in out[0] and "ggt_eligible" not in out[1]
    assert spy["calls"] == 0                      # 无 HK-ok 行 → 门控短路,不抓


def test_annotate_degraded_hk_kept_none(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    out = sbs._annotate_ggt([_entry("hk00700", "HK", status="degraded")])
    assert out[0].get("ggt_eligible") is None     # degraded 不注解(status 门控)


def test_annotate_fetch_none_all_hk_none(monkeypatch):
    _patch_set(monkeypatch, None)
    out = sbs._annotate_ggt([_entry("hk00700", "HK")])
    assert out[0]["ggt_eligible"] is None         # fail-closed


def test_annotate_does_not_mutate_cached_dict(monkeypatch):
    _patch_set(monkeypatch, {_ggt_key("00700")})
    cached = _entry("hk00700", "HK")
    out = sbs._annotate_ggt([cached])
    assert out[0] is not cached                    # 浅拷贝,非原地改
    assert "ggt_eligible" not in cached            # 缓存对象未被 mutate


def test_board_entry_schema_accepts_ggt_eligible():
    from api.v1.schemas.stocks import BoardEntry
    base = dict(code="hk00700", action_group="hold", consistency="unknown",
                price_lines={"entry": None, "stop": None, "target": None}, status="ok")
    assert BoardEntry(**base).ggt_eligible is None                    # legacy 无字段 → None 默认
    assert BoardEntry(**base, ggt_eligible=True).ggt_eligible is True
    assert BoardEntry(**base, ggt_eligible=False).ggt_eligible is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_signal_board_ggt.py -v`
Expected: FAIL — `AttributeError: module 'src.services.signal_board_service' has no attribute '_annotate_ggt'`.

- [ ] **Step 3a: schema 字段**

`api/v1/schemas/stocks.py`,`BoardEntry` 的 `family_size` 行(213)之后插入:

```python
    ggt_eligible: Optional[bool] = Field(None, description="港股通成份可买性三态:True=港股通标的/False=非成份/None=名单不可达或非HK(Inc 2b,presence-only 看板注解)")
```

- [ ] **Step 3b: board service import + 单例 + 注解 pass**

`src/services/signal_board_service.py` 顶部 import 区(约 line 32,`from src.storage import DatabaseManager` 后)加:

```python
from data_provider.fundamental_adapter import _ggt_eligible_state
```

在 `logger = logging.getLogger(__name__)`(line 34)之后加单例:

```python
_GGT_MANAGER = None
_GGT_MANAGER_LOCK = threading.Lock()


def _get_ggt_manager():
    """看板 GGT 注解用进程级单例 manager(镜像 history_loader._get_fetcher_manager 双检锁)。

    避免每 /board 请求重建整个 DataFetcherManager;eligibility set 在 fundamental_adapter
    模块级 12h 缓存,单例只承载 get_ggt_eligibility_set 的有界调用。
    """
    global _GGT_MANAGER
    if _GGT_MANAGER is None:
        with _GGT_MANAGER_LOCK:
            if _GGT_MANAGER is None:
                from data_provider import DataFetcherManager
                _GGT_MANAGER = DataFetcherManager()
    return _GGT_MANAGER


def _annotate_ggt(entries: list) -> list:
    """看板后置 pass:给 HK-ok 行打港股通可买性三态(presence-only,fail-closed)。

    门控 market=="HK"(大写)且 status=="ok"(排除 degraded/非HK;不特殊化 HK ETF,镜像报告);
    一次性抓全市场 eligibility set(有界、进程级 12h 缓存),逐行成员判定;
    浅拷贝命中行,不原地 mutate _BOARD_CACHE 缓存对象。
    """
    if not any(e["market"] == "HK" and e["status"] == "ok" for e in entries):
        return entries
    elig_set = _get_ggt_manager().get_ggt_eligibility_set()
    return [
        {**e, "ggt_eligible": _ggt_eligible_state(e["code"], elig_set)}
        if (e["market"] == "HK" and e["status"] == "ok") else e
        for e in entries
    ]
```

- [ ] **Step 3c: build_board 接线 + degraded None**

`src/services/signal_board_service.py` 的 `build_board`,在 `if codes:` 块**之后、`counts = {...}` 之前**(即 build_board **函数体缩进层**,与 `counts` 同级、非 `if codes:` 块内)插入一行:

```python
    entries = _annotate_ggt(entries)
```

(注:`if codes:` 为假时 `entries == []`,`_annotate_ggt([])` 的 `any(...)` 短路返回 `[]`,安全。)

`_degraded_entry`(约 line 244,`"oos": None,` 前)加(冗余但符合该 builder 全字段枚举惯例;post-pass 不碰 degraded 行,靠 Pydantic 默认亦 None):

```python
        "ggt_eligible": None,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH=.venv/bin:$PATH python -m pytest tests/test_signal_board_ggt.py -v`
Expected: PASS(5 测试全绿)。

- [ ] **Step 5: Commit**

```bash
git add api/v1/schemas/stocks.py src/services/signal_board_service.py tests/test_signal_board_ggt.py
git commit -m "feat: 信号看板港股通可买性注解后置 pass(BoardEntry.ggt_eligible 三态;_annotate_ggt 门控 HK+status==ok 一次抓全市场成份集+浅拷贝不改缓存;进程级单例 manager 减 churn;degraded/非HK→None fail-closed)"
```

---

### Task 4: 前端类型 + mapper + 测试工厂(tsc-green 数据线)

**Files:**
- Modify: `apps/dsa-web/src/types/kline.ts`(`BoardEntry`,line 93 后)
- Modify: `apps/dsa-web/src/api/stocks.ts`(`RawBoardEntry` line 93 后;`mapBoardEntry` line 114 后)
- Modify: `apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`(`mk` 工厂 line 24)
- Modify: `apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx`(`entry` 工厂 line 34)
- Test: `apps/dsa-web/src/api/__tests__/`(mapper 若有现成测试文件则加;否则并入 Task 5 的 vitest)

**Interfaces:**
- Consumes: 后端 `BoardEntry.ggt_eligible`(Task 3)。
- Produces: TS `BoardEntry.ggtEligible: boolean | null`;`mapBoardEntry` 映射 `ggt_eligible → ggtEligible`。

- [ ] **Step 1: TS 类型加字段**

`apps/dsa-web/src/types/kline.ts`,`BoardEntry` 的 `familySize` 行(93)之后插入:

```typescript
  ggtEligible: boolean | null;    // 港股通可买性三态(Inc 2b);null=名单不可达/非HK/legacy
```

- [ ] **Step 2: mapper 加字段**

`apps/dsa-web/src/api/stocks.ts`,`RawBoardEntry` 的 `family_size?` 行(93)之后插入:

```typescript
  ggt_eligible?: boolean | null;
```

`mapBoardEntry` 的 `familySize: r.family_size ?? null,` 行(114)之后插入:

```typescript
  ggtEligible: r.ggt_eligible ?? null,
```

- [ ] **Step 3: 补两个测试工厂(否则 required 字段缺失致 tsc RED)**

`apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`,`mk` 工厂 `horizonBars: null, signalStatus: null, planQuality: null,` 行(24)之后、`...over,` 之前插入:

```typescript
  ggtEligible: null,
```

`apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx`,`entry` 工厂同一处(line 34,`planQuality: null,` 之后、`...over` 之前)插入:

```typescript
  ggtEligible: null,
```

- [ ] **Step 4: 编译校验**

Run: `cd apps/dsa-web && npm run build`
Expected: `tsc` 无 TS2322/TS2741(required 字段已补),build 绿。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts \
  apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx \
  apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx
git commit -m "feat: 看板 BoardEntry.ggtEligible 类型+mapper(?? null legacy 容错)+补两测试工厂 ggtEligible:null 保 tsc"
```

---

### Task 5: 前端徽章 helper `ggtLabel` + `SignalBoardGroup` 渲染

**Files:**
- Modify: `apps/dsa-web/src/utils/credibility.ts`(`verifiedLabel` line 37 后)
- Modify: `apps/dsa-web/src/components/board/SignalBoardGroup.tsx`(import line 5;徽章 line 76 后)
- Test: `apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`(加 GGT 徽章断言)

**Interfaces:**
- Consumes: Task 4 的 `BoardEntry.ggtEligible`。
- Produces: `ggtLabel(ggtEligible: boolean | null) -> string | null`(True→'港股通'、False→'非港股通'、null→null)。

- [ ] **Step 1: Write the failing test**

`apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx` 的 `describe('SignalBoard', ...)` 内追加:

```typescript
  it('renders GGT eligibility badge tri-state', () => {
    const entries = [
      mk({ code: 'hk00700', name: '腾讯', market: 'HK', ggtEligible: true }),
      mk({ code: 'hk09999', name: '非通', market: 'HK', ggtEligible: false, hitRate: 0.5, hitSample: 9 }),
      mk({ code: '600519', name: '茅台', market: 'CN', ggtEligible: null, hitRate: 0.4, hitSample: 8 }),
    ];
    render(<MemoryRouter><SignalBoard entries={entries} onRowClick={vi.fn()} /></MemoryRouter>);
    const badges = screen.queryAllByTestId('board-ggt');
    expect(badges).toHaveLength(2);                 // 仅 True/False 渲染,None 不渲染
    expect(screen.getByText('港股通')).toBeInTheDocument();
    expect(screen.getByText('非港股通')).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/dsa-web && npx vitest run src/components/board/__tests__/SignalBoard.test.tsx -t "GGT eligibility"`
Expected: FAIL —— 无 `board-ggt` testid(徽章未渲染)。

- [ ] **Step 3a: 加 `ggtLabel` helper**

`apps/dsa-web/src/utils/credibility.ts`,`verifiedLabel` 函数(37)之后插入:

```typescript
/** 港股通可买性徽章文案:True→'港股通' / False→'非港股通' / null→null(不渲染)。 */
export function ggtLabel(ggtEligible: boolean | null): string | null {
  if (ggtEligible === null) return null;
  return ggtEligible ? '港股通' : '非港股通';
}
```

- [ ] **Step 3b: 渲染徽章**

`apps/dsa-web/src/components/board/SignalBoardGroup.tsx` 的 import 行(5)加 `ggtLabel`:

```typescript
import { formatCi, formatExcess, formatHitRate, formatHorizon, ggtLabel, markerStatusLabel, planQualityLabel, unverifiedExcessNote, verifiedLabel } from '../../utils/credibility';
```

命中率 `<td>` 内 resonance 徽章块(70-76)**之后**、`</td>`(77)之前插入:

```tsx
                {ggtLabel(e.ggtEligible) && (
                  <span
                    data-testid="board-ggt"
                    className={cn('ml-1 rounded px-1', e.ggtEligible ? 'bg-success/15 text-success' : 'bg-border/40 text-secondary-text')}
                  >{ggtLabel(e.ggtEligible)}</span>
                )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/dsa-web && npx vitest run src/components/board/__tests__/SignalBoard.test.tsx`
Expected: PASS(含新 GGT 徽章断言 + 既有断言)。

- [ ] **Step 5: web-gate + Commit**

```bash
cd apps/dsa-web && npm run lint && npm run build && cd ../..
git add apps/dsa-web/src/utils/credibility.ts apps/dsa-web/src/components/board/SignalBoardGroup.tsx apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx
git commit -m "feat: 看板港股通三态徽章(ggtLabel 中文硬编码 True→港股通/False→非港股通/None→不渲染;SignalBoardGroup 命中率格 resonance 旁 success/muted 徽章)"
```

---

### Task 6: docs + CHANGELOG

**Files:**
- Modify: `docs/ggt-southbound.md`(「看板注解 defer 到 Inc 2b」整段 + `## v1 已知局限` bullet)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 扁平一行)

**Interfaces:** 无代码接口。

- [ ] **Step 1: 订正 ggt-southbound.md**

将 `docs/ggt-southbound.md` 的「## 看板注解 defer 到 Inc 2b」整段(含「看板侧的 eligible 读取会结构性地永远命中 None」跨进程论据)改写为已落地说明,例如:

```markdown
## 看板可买性注解(Inc 2b,已落地)

信号看板每行呈现港股通可买性 eligibility 三态徽章(True→「港股通」/ False→「非港股通」/ None→无徽章)。**实现要点**:看板 `GET /board` 在 uvicorn 请求内按需计算(无 systemd/scheduler 预计算),故在**同一进程内自抓**一次全市场 eligibility set(`get_ggt_eligibility_set`,有界、进程级 12h 缓存),对每个 `market=="HK" and status=="ok"` 的行做成员判定 —— **不需要跨进程共享缓存**(早期 defer 基于「看板与报告异进程、模块缓存不可见」的前提,经核实看板与报告同为 uvicorn 进程内计算,该前提不成立)。只呈现 eligibility;个股持股/市场级净流仍仅在报告 section。HK ETF 镜像报告(不在成份表 → False)。
```

将 `## v1 已知局限` 段的 bullet「看板可买性注解 deferred 至 Inc 2b(跨进程缓存基础设施缺口)」删除(已落地)。核对全文无其他「看板 defer」残留。

- [ ] **Step 2: CHANGELOG 扁平一行**

`docs/CHANGELOG.md` `[Unreleased]` 顶部(GGT 相关条目附近)加一行:

```markdown
- [新功能] 信号看板每行呈现港股通(HKSC)可买性三态徽章(True→港股通/False→非港股通/None→无徽章,presence-only 纯展示不影响排序/决策);看板 uvicorn 请求内一次性自抓全市场成份集(有界 GGT_FETCH_TIMEOUT_SECONDS+进程级 12h 缓存+fail-closed),不需跨进程共享缓存;HK ETF 镜像报告口径
```

- [ ] **Step 3: 校验命令/文件名**

Run: `grep -n "defer 到 Inc 2b\|跨进程缓存基础设施" docs/ggt-southbound.md`
Expected: 无残留(或仅历史语境明确标注已落地)。

- [ ] **Step 4: Commit**

```bash
git add docs/ggt-southbound.md docs/CHANGELOG.md
git commit -m "docs: 港股通看板注解落地——ggt-southbound 订正跨进程 defer 论据段+删 v1 局限 bullet,CHANGELOG 扁平记看板可买性三态徽章"
```

---

## 最终验证(全部 task 后)

- [ ] 后端全门禁:`PATH=.venv/bin:$PATH ./scripts/ci_gate.sh` → 全绿(基线 4003 → +约 11 新测试)。
- [ ] 前端 web-gate:`cd apps/dsa-web && npm ci && npm run lint && npm run build` → lint 0 错、build 绿。
- [ ] 真网可选(关沙箱前台 + `export PYTHONPATH=<repo>`):`GET /board` 含 hk00700 → `ggt_eligible=true`;非成份 HK → false;东财不可达 → 全 None 且看板正常返回。

## Self-Review 覆盖核对(spec → task)

- §2 helper `_ggt_eligible_state` → Task 1 ✅
- §5 manager `get_ggt_eligibility_set` 有界 + tuple 解包 + 单例 → Task 2(方法)+ Task 3(单例)✅
- §4 `get_ggt_context` 重构 byte-identical → Task 2 ✅
- §3/§4 后置 pass + 门控(HK+status==ok)+ HK ETF 镜像 + 浅拷贝 → Task 3 ✅
- §6 Pydantic 字段 → Task 3 ✅;前端类型/mapper/两工厂 → Task 4 ✅;渲染/helper → Task 5 ✅
- §7 测试(三态/门控短路/fetch-once/fail-closed/不改缓存/有界/工厂/vitest)→ Task 1-5 ✅
- §10 docs 三处订正 + CHANGELOG → Task 6 ✅
