# 容器 C · 信号看板 实现计划（N0–N4）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 复选框跟踪。
> **Spec（v2，已提交 c467fd37）**：`docs/superpowers/specs/2026-06-16-signal-board-design.md`。

**Goal：** 在 L3 单股信号能力之上加一个「自选池近实时信号看板」——新页 `/board` 按动作（买入/观望/卖出/数据不可用）分组的密集可排序表格，点行复用 L3 K 线抽屉。

**Architecture：** 把现有 `/signals` handler 的单股编排抽成可复用 `build_signals_for_code`（返回比 `SignalsResponse` 更宽的 `BoardSignals`）；新 board 端点读 `STOCK_LIST` 并发逐股调它、按动作分组、单 code 失败仅降级该行、短 TTL 缓存；前端新页消费。复用为主、不造平行链。

**Tech Stack：** 后端 FastAPI + pandas + `concurrent.futures.ThreadPoolExecutor`；前端 React + TS + vitest/RTL；验证沿用无空格 worktree/副本。

---

## 关键设计决策（实现前必读）

1. **`build_signals_for_code` 落点（refine spec §5.1）**：`src/services/signals_service.py` 自述为**纯函数模块**（不持网络/DB 句柄）。为不破坏该契约，单股编排（含取数+DB+引擎）放进**新模块 `src/services/signal_board_service.py`**，复用 `signals_service` 的纯函数（`build_signals_payload`/`buy_signal_to_direction`）。单股端点与 board 端点都调它。
2. **board 端点落点**：`stocks.router` 挂在 `/stocks` 前缀下，无法产出 `/api/v1/signals/board`。新建 `api/v1/endpoints/signals.py`（`router`，前缀 `/signals`），在 `api/v1/router.py` 注册。
3. **`BoardSignals`（build_signals_for_code 返回）**：
   - `signals_payload: dict`（即 SignalsResponse 5 字段，含已填的 price_lines）
   - `rule_direction: str | None`（有数据→`buy_signal_to_direction(rule_signal)`；**无可用数据→None**）
   - `latest_close: float | None`、`name: str | None`、`market: str | None`
4. **action_group 映射**：`rule_direction` None→`unavailable`，`bullish`→`buy`，`bearish`→`sell`，`neutral`→`hold`。build_board 的 per-code try/except 异常分支也产 `unavailable`。
5. **线程安全**：board 只读 DB；`DatabaseManager.get_session()` 每次返回**新 session**（`storage.py:1051`），ThreadPoolExecutor 各 worker 各自取 session → 安全。SQLite busy-timeout 已设。缓存 dict 用 `threading.Lock`。
6. **新增 env**（不配置可运行）：`SIGNALS_BOARD_CACHE_TTL_S`（默认 300）、`SIGNALS_BOARD_MAX_WORKERS`（默认 8）。同步 `.env.example` + `src/core/config_registry.py` 的 `WEB_SETTINGS_HIDDEN_FROM_UI`（沿用 L3 KLINE_PRICE_LEVEL_* 做法）。

---

## File Structure

```
src/services/
  signal_board_service.py        # NEW: BoardSignals + build_signals_for_code（单股编排，I/O）+ build_board（并发聚合+缓存）
api/v1/
  schemas/stocks.py              # MODIFY: 追加 BoardEntry / SignalsBoardResponse（additive）
  endpoints/stocks.py            # MODIFY: 单股 /signals handler 退化为薄壳调 build_signals_for_code
  endpoints/signals.py           # NEW: GET /signals/board 端点（读 watchlist → build_board）
  router.py                      # MODIFY: 注册 signals.router(prefix=/signals)
src/core/config_registry.py      # MODIFY: 新 env 入 WEB_SETTINGS_HIDDEN_FROM_UI
tests/
  test_signal_board_service.py   # NEW: build_signals_for_code 直测 + build_board 分组/降级/缓存
  test_signals_board_endpoint.py # NEW: /signals/board 端点（空自选/分组/单行降级）
  test_signals_endpoint.py       # 不变（N0 等价回归）

apps/dsa-web/src/
  types/kline.ts                 # MODIFY: 追加 ActionGroup / BoardEntry / SignalsBoardResponse
  api/stocks.ts                  # MODIFY: 追加 getBoard + Raw 类型 + mapBoardEntry
  api/__tests__/stocks.board.test.ts          # NEW
  components/board/SignalBoard.tsx             # NEW: 分组容器（4 组）
  components/board/SignalBoardGroup.tsx        # NEW: 组头 + 密集表格 + 组内排序
  components/board/__tests__/SignalBoard.test.tsx  # NEW
  pages/SignalBoardPage.tsx      # NEW: 取数/loading/error/empty/refresh + 行→KLineDrawer
  pages/__tests__/SignalBoardPage.test.tsx     # NEW
  App.tsx                        # MODIFY: lazy 路由 /board
  components/layout/SidebarNav.tsx             # MODIFY: NavItem「信号看板」
docs/CHANGELOG.md / docs/signal-board.md       # MODIFY/NEW（N4）
```

---

# N0 · 抽出 build_signals_for_code（纯重构，单股行为不变）

### Task N0.1：新建 signal_board_service.py + build_signals_for_code + 直测

**Files**
- Create: `src/services/signal_board_service.py`
- Create: `tests/test_signal_board_service.py`

- [ ] **Step 1: 写失败测试**（mock 取数/引擎/分析器/DB，断言 BoardSignals 形状）

```python
# tests/test_signal_board_service.py
from datetime import datetime
from types import SimpleNamespace

from src.stock_analyzer import BuySignal
import src.services.signal_board_service as sbs


def _bar(date, close):
    return {"date": date, "open": close, "high": close + 5, "low": close - 5,
            "close": close, "volume": 1000, "amount": 0}


def _fake_history(rows, name="贵州茅台"):
    return {"stock_code": "600519", "stock_name": name, "period": "daily", "data": rows}


def _patch(monkeypatch, *, rows, engine_result, rule_signal, llm_record):
    monkeypatch.setattr(sbs.StockService, "get_history_data",
                        lambda self, stock_code, period="daily", days=120: _fake_history(rows))
    monkeypatch.setattr(sbs, "compute_volume_price_signals",
                        lambda df, config=None: engine_result)

    class _A:
        def __init__(self, *a, **k): pass
        def analyze(self, df, code): return SimpleNamespace(buy_signal=rule_signal)
    monkeypatch.setattr(sbs, "StockTrendAnalyzer", _A)

    class _DB:
        def get_latest_analysis_by_code(self, code): return llm_record
    monkeypatch.setattr(sbs.DatabaseManager, "get_instance", classmethod(lambda cls: _DB()))


def test_build_signals_for_code_returns_board_signals(monkeypatch):
    engine = SimpleNamespace(markers=[], status="ok", degraded_reason=None)
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))
    _patch(monkeypatch, rows=[_bar("2026-06-11", 1790.0), _bar("2026-06-12", 1800.0)],
           engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    bs = sbs.build_signals_for_code("600519", days=120)

    assert set(bs.signals_payload) == {"status", "markers", "price_lines", "consistency", "degraded_reason"}
    assert bs.signals_payload["status"] == "ok"
    assert bs.rule_direction == "bullish"          # BuySignal.BUY → bullish
    assert bs.latest_close == 1800.0
    assert bs.name == "贵州茅台"


def test_build_signals_for_code_no_data_is_unavailable(monkeypatch):
    _patch(monkeypatch, rows=[], engine_result=SimpleNamespace(markers=[], status="degraded", degraded_reason="x"),
           rule_signal=None, llm_record=None)

    bs = sbs.build_signals_for_code("600519", days=120)

    assert bs.signals_payload["status"] == "degraded"
    assert bs.rule_direction is None               # 无数据 → None（看板映射为 unavailable）
    assert bs.latest_close is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /tmp/<no-space-copy> && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_signal_board_service.py -q`
Expected: FAIL（`signal_board_service` 不存在 / `build_signals_for_code` 未定义）。

- [ ] **Step 3: 写最小实现**（把 `stocks.py:622-698` 的单股编排原样搬入，参数化、返回 BoardSignals；纯函数依赖延迟导入以防循环）

```python
# src/services/signal_board_service.py
"""容器 C 信号看板编排（含 I/O）。复用 signals_service 纯函数 + L3 引擎/反算器。

注意：signals_service.py 为纯函数模块；本模块承载取数+DB+引擎的单股编排（build_signals_for_code）
与并发聚合（build_board），供单股 /signals 端点与看板 /signals/board 端点共用，避免平行实现。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from src.config import parse_env_float, parse_env_int
from src.services.signals_service import build_signals_payload, buy_signal_to_direction, STALE_TRADING_DAYS_DEFAULT
from src.services.signal_hit_rate import resolve_marker_hit_fields
from src.services.stock_service import StockService
from src.services.volume_price_signals import (
    _DEFAULT_ATR_MULT, _DEFAULT_RR_TARGET, VPSConfig,
    compute_volume_price_signals, derive_price_levels,
)
from src.stock_analyzer import StockTrendAnalyzer
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class BoardSignals:
    signals_payload: dict            # SignalsResponse 5 字段（含已填 price_lines）
    rule_direction: Optional[str]    # bullish/bearish/neutral；无数据 → None
    latest_close: Optional[float]
    name: Optional[str]
    market: Optional[str]


def _infer_market(code: str) -> Optional[str]:
    c = (code or "").upper()
    if "/" in c:
        return "crypto"
    if c.startswith("HK") or c.endswith(".HK"):
        return "HK"
    if c[:6].isdigit():
        return "CN"
    return "US"


def build_signals_for_code(code: str, *, days: int = 120) -> BoardSignals:
    """单股编排：取数（与 /history 同源）→ 引擎 → BuySignal → consistency → 命中率回填 → price_lines。

    与原 /signals handler 行为一致；额外外露 rule_direction/latest_close/name/market 供看板分组。
    """
    # 延迟导入，避免 endpoint 层辅助函数的潜在循环（沿用 apply_price_levels_to_guard 先例）
    from api.v1.endpoints.stocks import build_price_lines, _elapsed_trading_days

    service = StockService()
    history = service.get_history_data(stock_code=code, period="daily", days=days)
    rows = history.get("data", []) or []
    name = history.get("stock_name")
    market = _infer_market(code)

    if not rows:
        payload = {
            "status": "degraded", "markers": [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "unknown", "degraded_reason": "无可用历史数据",
        }
        return BoardSignals(signals_payload=payload, rule_direction=None,
                            latest_close=None, name=name, market=market)

    df = pd.DataFrame(rows)
    latest_bar_date = str(rows[-1].get("date"))
    _lc = rows[-1].get("close")
    latest_close = float(_lc) if _lc is not None else None

    engine_result = compute_volume_price_signals(df, config=VPSConfig.from_env())

    rule_signal = None
    try:
        trend_result = StockTrendAnalyzer().analyze(df, code)
        rule_signal = getattr(trend_result, "buy_signal", None)
    except Exception as exc:
        logger.warning("规则代表方向计算失败 code=%s err=%s", code, exc)

    llm_record = None
    try:
        llm_record = DatabaseManager.get_instance().get_latest_analysis_by_code(code)
    except Exception as exc:
        logger.warning("LLM 最新结论读取失败 code=%s err=%s", code, exc)

    trading_days_elapsed = _elapsed_trading_days(llm_record, rows)
    stale_threshold = parse_env_int(
        os.getenv("SIGNALS_STALE_TRADING_DAYS"), STALE_TRADING_DAYS_DEFAULT,
        field_name="SIGNALS_STALE_TRADING_DAYS", minimum=1,
    )

    payload = build_signals_payload(
        engine_result=engine_result, rule_signal=rule_signal,
        latest_bar_date=latest_bar_date, latest_close=latest_close,
        llm_record=llm_record, trading_days_elapsed=trading_days_elapsed,
        stale_threshold=stale_threshold, code=code,
        hit_fields_resolver=resolve_marker_hit_fields,
    )

    atr_mult = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_ATR_MULT"), _DEFAULT_ATR_MULT,
                               field_name="KLINE_PRICE_LEVEL_ATR_MULT", minimum=0.1)
    rr_target = parse_env_float(os.getenv("KLINE_PRICE_LEVEL_RR_TARGET"), _DEFAULT_RR_TARGET,
                                field_name="KLINE_PRICE_LEVEL_RR_TARGET", minimum=0.1)
    price_levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)
    payload["price_lines"] = build_price_lines(price_levels).model_dump()

    return BoardSignals(
        signals_payload=payload,
        rule_direction=buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral",
        latest_close=latest_close, name=name, market=market,
    )
```

> 注：`rule_signal is None`（数据在但分析器失败）→ `rule_direction="neutral"`（观望）；只有**无数据**才置 None→unavailable。

- [ ] **Step 4: 跑测试验证通过**

Run: `… -m pytest tests/test_signal_board_service.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_board_service.py tests/test_signal_board_service.py
git commit -m "refactor: 抽出 build_signals_for_code 单股编排为可复用 service(N0,容器C)"
```

### Task N0.2：单股 /signals 端点退化为薄壳 + 等价回归

**Files**
- Modify: `api/v1/endpoints/stocks.py`（`get_stock_signals` 函数体 ~622-700）

- [ ] **Step 1: 改实现**（保留装饰器/签名不变；函数体改为调 service）

```python
def get_stock_signals(
    stock_code: str,
    days: int = Query(120, ge=1, le=365, description="日历回看天数（与 /history 同源）"),
) -> SignalsResponse:
    try:
        from src.services.signal_board_service import build_signals_for_code
        bs = build_signals_for_code(stock_code, days=days)
        return SignalsResponse(**bs.signals_payload)
    except Exception as exc:  # 保留原 500 行为
        logger.error("获取信号失败 code=%s err=%s", stock_code, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="获取信号失败") from exc
```

> 对照原 handler 的 try/except 收尾（`stocks.py:700` 之后）保持同样的 500 语义；若原实现 except 文案不同，照原样保留以维持等价。

- [ ] **Step 2: 跑等价回归**

Run: `… -m pytest tests/test_signals_endpoint.py -q`
Expected: **现有断言全 PASS**（行为逐字节不变）。如有 fail，说明抽取改了语义，按 systematic-debugging 定位，不得改测试迁就。

- [ ] **Step 3: py_compile + commit**

```bash
"…/.venv/bin/python" -m py_compile api/v1/endpoints/stocks.py src/services/signal_board_service.py
git add api/v1/endpoints/stocks.py
git commit -m "refactor: /signals 端点改调 build_signals_for_code,单股行为等价(N0,容器C)"
```

---

# N1 · 看板端点 + 并发 + 降级 + 缓存

### Task N1.1：BoardEntry / SignalsBoardResponse schema（additive）

**Files**
- Modify: `api/v1/schemas/stocks.py`（追加，不动既有）

- [ ] **Step 1: 追加 Pydantic 模型**

```python
# api/v1/schemas/stocks.py（追加；ActionGroup/Consistency 等沿用字面量）
from typing import List, Literal, Optional
from pydantic import BaseModel

class BoardEntry(BaseModel):
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    action_group: Literal["buy", "hold", "sell", "unavailable"]
    rule_direction: Optional[Literal["bullish", "bearish", "neutral"]] = None
    llm_direction: Optional[Literal["bullish", "bearish", "neutral"]] = None
    consistency: Literal["consistent", "divergent", "conflict", "unknown", "stale"]
    key_signals: List[str] = []
    price_lines: PriceLines
    latest_close: Optional[float] = None
    hit_rate: Optional[float] = None
    hit_sample: Optional[int] = None
    verified: bool = False
    status: Literal["ok", "degraded"]
    degraded_reason: Optional[str] = None

class BoardCounts(BaseModel):
    buy: int = 0
    hold: int = 0
    sell: int = 0
    unavailable: int = 0

class SignalsBoardResponse(BaseModel):
    as_of: int
    entries: List[BoardEntry]
    counts: BoardCounts
    degraded_codes: List[str] = []
```

- [ ] **Step 2: py_compile + commit**（schema 由 N1.3 端点测试覆盖）

```bash
"…/.venv/bin/python" -m py_compile api/v1/schemas/stocks.py
git add api/v1/schemas/stocks.py
git commit -m "feat: 新增 BoardEntry/SignalsBoardResponse 契约(N1,容器C)"
```

### Task N1.2：build_board（并发 + 降级 + 映射 + 缓存）

**Files**
- Modify: `src/services/signal_board_service.py`（追加 `build_board` + 缓存 + 映射）
- Modify: `tests/test_signal_board_service.py`（追加 build_board 测试）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_board_service.py（追加）
import src.services.signal_board_service as sbs

def _bs(direction, *, status="ok", markers=None, hit=None):
    return sbs.BoardSignals(
        signals_payload={
            "status": status, "markers": markers or [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "consistent", "degraded_reason": None,
        },
        rule_direction=direction, latest_close=100.0, name="N", market="CN",
    )

def test_build_board_groups_and_counts(monkeypatch):
    mapping = {"AAA": _bs("bullish"), "BBB": _bs("neutral"), "CCC": _bs("bearish")}
    monkeypatch.setattr(sbs, "build_signals_for_code", lambda code, *, days=120: mapping[code])
    out = sbs.build_board(["AAA", "BBB", "CCC"], days=120, refresh=True)
    groups = {e["code"]: e["action_group"] for e in out["entries"]}
    assert groups == {"AAA": "buy", "BBB": "hold", "CCC": "sell"}
    assert out["counts"] == {"buy": 1, "hold": 1, "sell": 1, "unavailable": 0}
    assert out["degraded_codes"] == []

def test_build_board_single_failure_degrades_only_that_row(monkeypatch):
    def fake(code, *, days=120):
        if code == "BAD":
            raise RuntimeError("boom")
        return _bs("bullish")
    monkeypatch.setattr(sbs, "build_signals_for_code", fake)
    out = sbs.build_board(["AAA", "BAD"], days=120, refresh=True)
    bad = next(e for e in out["entries"] if e["code"] == "BAD")
    assert bad["action_group"] == "unavailable" and bad["status"] == "degraded"
    assert out["counts"]["buy"] == 1 and out["counts"]["unavailable"] == 1
    assert "BAD" in out["degraded_codes"]
    # 其余正常返回（整盘不被单源拖垮）
    assert any(e["code"] == "AAA" and e["action_group"] == "buy" for e in out["entries"])

def test_build_board_empty(monkeypatch):
    out = sbs.build_board([], days=120, refresh=True)
    assert out["entries"] == [] and out["counts"]["buy"] == 0

def test_build_board_cache_hit(monkeypatch):
    calls = {"n": 0}
    def fake(code, *, days=120):
        calls["n"] += 1
        return _bs("bullish")
    monkeypatch.setattr(sbs, "build_signals_for_code", fake)
    sbs._BOARD_CACHE.clear()
    sbs.build_board(["AAA"], days=120, refresh=True)   # 计算 1 次并写缓存
    sbs.build_board(["AAA"], days=120, refresh=False)  # 命中缓存，不再算
    assert calls["n"] == 1
```

- [ ] **Step 2: 跑测试验证失败**

Run: `… -m pytest tests/test_signal_board_service.py -q`
Expected: FAIL（`build_board` / `_BOARD_CACHE` 未定义）。

- [ ] **Step 3: 写最小实现**（追加到 signal_board_service.py）

```python
# src/services/signal_board_service.py（追加）
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_ACTION_BY_DIRECTION = {"bullish": "buy", "bearish": "sell", "neutral": "hold"}
_BOARD_CACHE: dict[tuple, tuple[float, dict]] = {}   # key -> (ts, BoardEntry-dict)
_BOARD_CACHE_LOCK = threading.Lock()


def _llm_direction_from_markers(markers: list) -> Optional[str]:
    for m in markers:
        if m.get("source") == "llm":
            return m.get("direction")
    return None


def _key_signals_from_markers(markers: list) -> list:
    seen, out = set(), []
    for m in markers:
        if m.get("source") == "rule":
            st = m.get("signal_type")
            if st and st not in seen:
                seen.add(st); out.append(st)
    return out


def _hit_fields_from_markers(markers: list) -> dict:
    for m in markers:
        if m.get("source") == "rule":
            return {"hit_rate": m.get("hit_rate"), "hit_sample": m.get("hit_sample"),
                    "verified": bool(m.get("verified", False))}
    return {"hit_rate": None, "hit_sample": None, "verified": False}


def _entry_from_board_signals(code: str, bs: "BoardSignals") -> dict:
    payload = bs.signals_payload
    markers = payload.get("markers", []) or []
    action = "unavailable" if bs.rule_direction is None else _ACTION_BY_DIRECTION[bs.rule_direction]
    hit = _hit_fields_from_markers(markers)
    return {
        "code": code, "name": bs.name, "market": bs.market,
        "action_group": action, "rule_direction": bs.rule_direction,
        "llm_direction": _llm_direction_from_markers(markers),
        "consistency": payload.get("consistency", "unknown"),
        "key_signals": _key_signals_from_markers(markers),
        "price_lines": payload.get("price_lines", {"entry": None, "stop": None, "target": None}),
        "latest_close": bs.latest_close, **hit,
        "status": payload.get("status", "ok"), "degraded_reason": payload.get("degraded_reason"),
    }


def _degraded_entry(code: str, reason: str) -> dict:
    return {
        "code": code, "name": None, "market": _infer_market(code),
        "action_group": "unavailable", "rule_direction": None, "llm_direction": None,
        "consistency": "unknown", "key_signals": [],
        "price_lines": {"entry": None, "stop": None, "target": None},
        "latest_close": None, "hit_rate": None, "hit_sample": None, "verified": False,
        "status": "degraded", "degraded_reason": reason,
    }


def _compute_entry(code: str, *, days: int, refresh: bool, now_s: float, ttl_s: int) -> dict:
    cache_key = (code, days)
    if not refresh:
        with _BOARD_CACHE_LOCK:
            hit = _BOARD_CACHE.get(cache_key)
            if hit and (now_s - hit[0]) < ttl_s:
                return hit[1]
    try:
        entry = _entry_from_board_signals(code, build_signals_for_code(code, days=days))
    except Exception as exc:
        logger.warning("看板单股计算失败 code=%s err=%s", code, exc)
        entry = _degraded_entry(code, "信号计算失败")
    with _BOARD_CACHE_LOCK:
        _BOARD_CACHE[cache_key] = (now_s, entry)
    return entry


def build_board(codes: list, *, days: int = 120, refresh: bool = False) -> dict:
    ttl_s = parse_env_int(os.getenv("SIGNALS_BOARD_CACHE_TTL_S"), 300,
                          field_name="SIGNALS_BOARD_CACHE_TTL_S", minimum=0)
    max_workers = parse_env_int(os.getenv("SIGNALS_BOARD_MAX_WORKERS"), 8,
                                field_name="SIGNALS_BOARD_MAX_WORKERS", minimum=1)
    now_s = time.time()
    entries: list = []
    if codes:
        workers = min(max_workers, len(codes))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="signal_board_") as ex:
            entries = list(ex.map(
                lambda c: _compute_entry(c, days=days, refresh=refresh, now_s=now_s, ttl_s=ttl_s),
                codes,
            ))
    counts = {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0}
    degraded_codes = []
    for e in entries:
        counts[e["action_group"]] = counts.get(e["action_group"], 0) + 1
        if e["status"] == "degraded":
            degraded_codes.append(e["code"])
    return {"as_of": int(now_s * 1000), "entries": entries, "counts": counts,
            "degraded_codes": degraded_codes}
```

> `ex.map` 保持入参顺序；每线程内 `build_signals_for_code` 各自 `get_session()`（新 session）→ 只读并发安全。

- [ ] **Step 4: 跑测试验证通过**

Run: `… -m pytest tests/test_signal_board_service.py -q`
Expected: PASS（6 passed：N0.1 的 2 + 本步 4）。

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_board_service.py tests/test_signal_board_service.py
git commit -m "feat: build_board 并发聚合+单行降级(unavailable桶)+TTL缓存(N1,容器C)"
```

### Task N1.3：/signals/board 端点 + 新 router 注册

**Files**
- Create: `api/v1/endpoints/signals.py`
- Modify: `api/v1/router.py`（注册）
- Create: `tests/test_signals_board_endpoint.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signals_board_endpoint.py
import api.v1.endpoints.signals as board_ep

class _Svc:
    def __init__(self, codes): self._codes = codes
    def get_config(self, include_schema=False):
        return {"items": [{"key": "STOCK_LIST", "value": ",".join(self._codes)}]}

def test_board_endpoint_groups(monkeypatch):
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh: {
                            "as_of": 1, "entries": [{"code": c} for c in codes],
                            "counts": {"buy": len(codes), "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, service=_Svc(["AAA", "BBB"]))
    assert resp.counts.buy == 2 and len(resp.entries) == 2

def test_board_endpoint_empty_watchlist(monkeypatch):
    monkeypatch.setattr(board_ep, "build_board",
                        lambda codes, *, days, refresh: {"as_of": 1, "entries": [],
                            "counts": {"buy": 0, "hold": 0, "sell": 0, "unavailable": 0},
                            "degraded_codes": []})
    resp = board_ep.get_signals_board(days=120, refresh=False, service=_Svc([]))
    assert resp.entries == []
```

- [ ] **Step 2: 跑验证失败**

Run: `… -m pytest tests/test_signals_board_endpoint.py -q`
Expected: FAIL（模块/函数不存在）。

- [ ] **Step 3: 写最小实现**

```python
# api/v1/endpoints/signals.py
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.stocks import SignalsBoardResponse
from api.v1.schemas.common import ErrorResponse
from src.services.system_config_service import SystemConfigService
from src.services.signal_board_service import build_board
# 复用 stocks.py 的自选池读取，避免平行实现
from api.v1.endpoints.stocks import _read_watchlist_codes

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/board",
    response_model=SignalsBoardResponse,
    responses={200: {"description": "自选池信号看板（含 degraded 行）"},
               500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="自选池量价信号看板",
    description="对 STOCK_LIST 自选池近实时计算每只标的的量价信号，按动作分组返回；单 code 失败仅降级该行。",
)
def get_signals_board(
    days: int = Query(120, ge=1, le=365, description="日历回看天数（与 /history 同源）"),
    refresh: bool = Query(False, description="跳过缓存强制重算"),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SignalsBoardResponse:
    try:
        codes = _read_watchlist_codes(service)
        return SignalsBoardResponse(**build_board(codes, days=days, refresh=refresh))
    except Exception as exc:
        logger.error("信号看板失败 err=%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="信号看板失败") from exc
```

在 `api/v1/router.py` 注册（仿现有 include_router）：

```python
from api.v1.endpoints import alerts, analysis, auth, history, stocks, backtest, system_config, agent, usage, portfolio, alphasift, health, signals
...
router.include_router(signals.router, prefix="/signals", tags=["Signals"])
```

- [ ] **Step 4: 跑验证通过**

Run: `… -m pytest tests/test_signals_board_endpoint.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
"…/.venv/bin/python" -m py_compile api/v1/endpoints/signals.py api/v1/router.py
git add api/v1/endpoints/signals.py api/v1/router.py tests/test_signals_board_endpoint.py
git commit -m "feat: 新增 GET /api/v1/signals/board 端点(读自选池→build_board)(N1,容器C)"
```

### Task N1.4：新 env 入 .env.example + config_registry

**Files**
- Modify: `.env.example`、`src/core/config_registry.py`

- [ ] **Step 1: .env.example 追加**

```
# 信号看板（容器 C）；不配置走默认
SIGNALS_BOARD_CACHE_TTL_S=300
SIGNALS_BOARD_MAX_WORKERS=8
```

- [ ] **Step 2: config_registry 的 WEB_SETTINGS_HIDDEN_FROM_UI 追加这两个 key**（沿用 L3 KLINE_PRICE_LEVEL_* 做法，满足 active-env-key gate）。先 `grep -n "KLINE_PRICE_LEVEL_ATR_MULT" src/core/config_registry.py` 找到该集合，照样加入 `SIGNALS_BOARD_CACHE_TTL_S` / `SIGNALS_BOARD_MAX_WORKERS`。

- [ ] **Step 3: ci_gate 校验 + commit**

Run: `cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && PATH=".venv/bin:$PATH" ./scripts/ci_gate.sh all 2>&1 | tail -15`（无空格副本/worktree 跑）
Expected: backend-gate all checks passed。

```bash
git add .env.example src/core/config_registry.py
git commit -m "chore: 信号看板新增 SIGNALS_BOARD_* 配置并入注册表(N1,容器C)"
```

---

# N2 · 前端 client + 类型

### Task N2.1：kline.ts 追加 board 契约类型

**Files**
- Modify: `apps/dsa-web/src/types/kline.ts`（追加，不动 L3 类型）

- [ ] **Step 1: 追加类型**

```typescript
// apps/dsa-web/src/types/kline.ts（追加）
export type ActionGroup = 'buy' | 'hold' | 'sell' | 'unavailable';

export interface BoardEntry {
  code: string;
  name: string | null;
  market: string | null;
  actionGroup: ActionGroup;
  ruleDirection: SignalDirection | null;
  llmDirection: SignalDirection | null;
  consistency: Consistency;
  keySignals: string[];
  priceLines: PriceLines;
  latestClose: number | null;
  hitRate: number | null;
  hitSample: number | null;
  verified: boolean;
  status: 'ok' | 'degraded';
  degradedReason: string | null;
}

export interface BoardCounts { buy: number; hold: number; sell: number; unavailable: number; }

export interface SignalsBoardResponse {
  asOf: number;
  entries: BoardEntry[];
  counts: BoardCounts;
  degradedCodes: string[];
}
```

- [ ] **Step 2: commit**

```bash
git add apps/dsa-web/src/types/kline.ts
git commit -m "feat: 前端追加信号看板契约类型(N2,容器C)"
```

### Task N2.2：stocks.ts 追加 getBoard + 映射 + api 测试

**Files**
- Modify: `apps/dsa-web/src/api/stocks.ts`
- Create: `apps/dsa-web/src/api/__tests__/stocks.board.test.ts`

- [ ] **Step 1: 写失败测试**（仿 stocks.signals.test.ts）

```typescript
// apps/dsa-web/src/api/__tests__/stocks.board.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../index', () => ({ default: { get } }));

describe('stocksApi.getBoard', () => {
  beforeEach(() => { get.mockReset(); });

  it('maps snake_case board entries to camelCase and passes days/refresh', async () => {
    get.mockResolvedValueOnce({ data: {
      as_of: 111, counts: { buy: 1, hold: 0, sell: 0, unavailable: 1 }, degraded_codes: ['BAD'],
      entries: [
        { code: '600519', name: '贵州茅台', market: 'CN', action_group: 'buy',
          rule_direction: 'bullish', llm_direction: 'bullish', consistency: 'consistent',
          key_signals: ['volume_breakout'], price_lines: { entry: 1700, stop: 1620, target: 1850 },
          latest_close: 1660, hit_rate: 0.62, hit_sample: 18, verified: true,
          status: 'ok', degraded_reason: null },
        { code: 'BAD', name: null, market: 'CN', action_group: 'unavailable',
          rule_direction: null, llm_direction: null, consistency: 'unknown', key_signals: [],
          price_lines: { entry: null, stop: null, target: null }, latest_close: null,
          hit_rate: null, hit_sample: null, verified: false, status: 'degraded', degraded_reason: '信号计算失败' },
      ],
    }});

    const res = await stocksApi.getBoard(120, true);

    expect(get).toHaveBeenCalledWith('/api/v1/signals/board', { params: { days: 120, refresh: true } });
    expect(res.asOf).toBe(111);
    expect(res.counts.unavailable).toBe(1);
    expect(res.degradedCodes).toEqual(['BAD']);
    expect(res.entries[0].actionGroup).toBe('buy');
    expect(res.entries[0].keySignals).toEqual(['volume_breakout']);
    expect(res.entries[0].hitRate).toBe(0.62);
    expect(res.entries[0].priceLines).toEqual({ entry: 1700, stop: 1620, target: 1850 });
    expect(res.entries[1].actionGroup).toBe('unavailable');
    expect(res.entries[1].ruleDirection).toBeNull();
  });

  it('omits refresh when false-y and days when undefined', async () => {
    get.mockResolvedValueOnce({ data: { as_of: 1, counts: { buy:0,hold:0,sell:0,unavailable:0 }, degraded_codes: [], entries: [] }});
    await stocksApi.getBoard();
    expect(get).toHaveBeenCalledWith('/api/v1/signals/board', { params: {} });
  });
});
```

- [ ] **Step 2: 跑验证失败**

Run: `cd /tmp/<copy>/apps/dsa-web && npx vitest run src/api/__tests__/stocks.board.test.ts`
Expected: FAIL（`getBoard` 未定义）。

- [ ] **Step 3: 写最小实现**（stocks.ts：导入类型 + raw 类型 + mapper + 方法）

```typescript
// 顶部 import 追加
import type { BoardEntry, SignalsBoardResponse } from '../types/kline';

// stocksApi 之前追加 raw 类型 + 映射
type RawBoardEntry = {
  code: string; name: string | null; market: string | null;
  action_group: BoardEntry['actionGroup'];
  rule_direction: BoardEntry['ruleDirection']; llm_direction: BoardEntry['llmDirection'];
  consistency: BoardEntry['consistency']; key_signals: string[];
  price_lines: { entry: number | null; stop: number | null; target: number | null };
  latest_close: number | null; hit_rate: number | null; hit_sample: number | null;
  verified: boolean; status: BoardEntry['status']; degraded_reason: string | null;
};
type RawBoardResponse = {
  as_of: number; entries: RawBoardEntry[] | null;
  counts: { buy: number; hold: number; sell: number; unavailable: number };
  degraded_codes: string[] | null;
};
const mapBoardEntry = (r: RawBoardEntry): BoardEntry => ({
  code: r.code, name: r.name ?? null, market: r.market ?? null,
  actionGroup: r.action_group, ruleDirection: r.rule_direction ?? null,
  llmDirection: r.llm_direction ?? null, consistency: r.consistency,
  keySignals: r.key_signals ?? [],
  priceLines: { entry: r.price_lines?.entry ?? null, stop: r.price_lines?.stop ?? null, target: r.price_lines?.target ?? null },
  latestClose: r.latest_close ?? null, hitRate: r.hit_rate ?? null, hitSample: r.hit_sample ?? null,
  verified: r.verified, status: r.status, degradedReason: r.degraded_reason ?? null,
});

// stocksApi 对象内追加方法
  async getBoard(days?: number, refresh?: boolean): Promise<SignalsBoardResponse> {
    const params: { days?: number; refresh?: boolean } = {};
    if (days !== undefined) params.days = days;
    if (refresh) params.refresh = true;
    const response = await apiClient.get('/api/v1/signals/board', { params });
    const data = response.data as RawBoardResponse;
    return {
      asOf: data.as_of,
      entries: (data.entries ?? []).map(mapBoardEntry),
      counts: data.counts,
      degradedCodes: data.degraded_codes ?? [],
    };
  },
```

- [ ] **Step 4: 跑验证通过**

Run: `npx vitest run src/api/__tests__/stocks.board.test.ts`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/stocks.board.test.ts
git commit -m "feat: 前端 getBoard client + snake→camel 映射(N2,容器C)"
```

---

# N3 · 前端分组表格组件

### Task N3.1：SignalBoard + SignalBoardGroup（4 组/组内排序）

**Files**
- Create: `apps/dsa-web/src/components/board/SignalBoard.tsx`
- Create: `apps/dsa-web/src/components/board/SignalBoardGroup.tsx`
- Create: `apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { BoardEntry } from '../../../types/kline';
import { SignalBoard } from '../SignalBoard';

const mk = (over: Partial<BoardEntry>): BoardEntry => ({
  code: 'X', name: 'X名', market: 'CN', actionGroup: 'buy', ruleDirection: 'bullish',
  llmDirection: 'bullish', consistency: 'consistent', keySignals: ['volume_breakout'],
  priceLines: { entry: 1700, stop: 1620, target: 1850 }, latestClose: 1660,
  hitRate: 0.62, hitSample: 18, verified: true, status: 'ok', degradedReason: null, ...over,
});

describe('SignalBoard', () => {
  it('renders four action groups with counts and rows, hides empty groups optional', () => {
    const entries = [
      mk({ code: '600519', name: '贵州茅台', actionGroup: 'buy' }),
      mk({ code: 'BAD', name: null, actionGroup: 'unavailable', status: 'degraded', ruleDirection: null }),
    ];
    render(<SignalBoard entries={entries} onRowClick={vi.fn()} />);
    expect(screen.getByText('买入候选')).toBeInTheDocument();
    expect(screen.getByText('数据不可用')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    // verified 徽章
    expect(screen.getByText('已验证')).toBeInTheDocument();
  });

  it('calls onRowClick with code+name when a row is clicked', () => {
    const onRowClick = vi.fn();
    render(<SignalBoard entries={[mk({ code: '600519', name: '贵州茅台' })]} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByText('贵州茅台'));
    expect(onRowClick).toHaveBeenCalledWith('600519', '贵州茅台');
  });

  it('sorts a group by hit rate descending within group', () => {
    const entries = [
      mk({ code: 'LOW', name: 'L', hitRate: 0.3 }),
      mk({ code: 'HIGH', name: 'H', hitRate: 0.9 }),
    ];
    render(<SignalBoard entries={entries} onRowClick={vi.fn()} />);
    const buyGroup = screen.getByTestId('group-buy');
    const rows = within(buyGroup).getAllByTestId('board-row');
    expect(rows[0]).toHaveTextContent('H');   // 命中率高在前（默认排序）
  });
});
```

- [ ] **Step 2: 跑验证失败**

Run: `npx vitest run src/components/board/__tests__/SignalBoard.test.tsx`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 写最小实现**（SignalBoardGroup 渲染组头+密集表格+默认按 hitRate 降序；SignalBoard 分四组）

```tsx
// apps/dsa-web/src/components/board/SignalBoardGroup.tsx
import type React from 'react';
import type { BoardEntry } from '../../types/kline';
import { cn } from '../../utils/cn';

const dirLabel: Record<string, string> = { bullish: '看多', bearish: '看空', neutral: '中性' };
const consistencyLabel: Record<BoardEntry['consistency'], string> = {
  consistent: '一致', divergent: '分歧', conflict: '冲突', unknown: '未知', stale: '过期',
};
const fmt = (v: number | null) => (v === null || Number.isNaN(v) ? '—' : String(v));
const fmtHit = (e: BoardEntry) =>
  e.hitRate === null || e.hitSample === null || e.hitSample <= 0
    ? '暂无样本' : `${Math.round(e.hitRate * 100)}% · ${e.hitSample}`;

interface GroupProps { groupKey: string; title: string; entries: BoardEntry[]; onRowClick: (c: string, n?: string) => void; }

export const SignalBoardGroup: React.FC<GroupProps> = ({ groupKey, title, entries, onRowClick }) => {
  if (entries.length === 0) return null;
  // 默认按命中率降序，无样本沉底
  const sorted = [...entries].sort((a, b) => (b.hitRate ?? -1) - (a.hitRate ?? -1));
  return (
    <section data-testid={`group-${groupKey}`} className="mb-5">
      <div className="label-uppercase mb-2">{title}（{entries.length}）</div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-secondary-text">
            <th className="text-left">标的</th><th>规则</th><th>LLM</th><th>一致性</th>
            <th className="text-left">关键量价信号</th><th>入/损/标</th><th>命中率</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e) => (
            <tr key={e.code} data-testid="board-row"
                onClick={() => onRowClick(e.code, e.name ?? undefined)}
                className="cursor-pointer border-t border-border/60 hover:bg-hover">
              <td className="py-1.5 text-left text-foreground">{e.name ?? e.code}</td>
              <td className={cn(e.ruleDirection === 'bullish' && 'text-danger', e.ruleDirection === 'bearish' && 'text-success')}>
                {e.ruleDirection ? dirLabel[e.ruleDirection] : '—'}</td>
              <td>{e.llmDirection ? dirLabel[e.llmDirection] : '—'}</td>
              <td className={cn(e.consistency === 'conflict' && 'text-danger')}>{consistencyLabel[e.consistency]}</td>
              <td className="text-left text-secondary-text">{e.keySignals.join('·') || '—'}</td>
              <td className="text-secondary-text">{fmt(e.priceLines.entry)}/{fmt(e.priceLines.stop)}/{fmt(e.priceLines.target)}</td>
              <td>
                <span className="text-secondary-text">{fmtHit(e)}</span>{' '}
                <span className={cn(e.verified ? 'text-success' : 'text-secondary-text')}>{e.verified ? '已验证' : '未验证'}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
};
```

```tsx
// apps/dsa-web/src/components/board/SignalBoard.tsx
import type React from 'react';
import type { ActionGroup, BoardEntry } from '../../types/kline';
import { SignalBoardGroup } from './SignalBoardGroup';

const GROUPS: { key: ActionGroup; title: string }[] = [
  { key: 'buy', title: '买入候选' }, { key: 'hold', title: '观望' },
  { key: 'sell', title: '卖出候选' }, { key: 'unavailable', title: '数据不可用' },
];

interface SignalBoardProps { entries: BoardEntry[]; onRowClick: (code: string, name?: string) => void; }

export const SignalBoard: React.FC<SignalBoardProps> = ({ entries, onRowClick }) => (
  <div>
    {GROUPS.map((g) => (
      <SignalBoardGroup key={g.key} groupKey={g.key} title={g.title}
        entries={entries.filter((e) => e.actionGroup === g.key)} onRowClick={onRowClick} />
    ))}
  </div>
);
```

- [ ] **Step 4: 跑验证通过 + eslint**

Run: `npx vitest run src/components/board && npx eslint src/components/board`
Expected: 3 passed；eslint exit 0。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/board/
git commit -m "feat: 信号看板分组表格组件(4组/组内命中率排序/点击行)(N3,容器C)"
```

### Task N3.2：SignalBoardPage（取数/状态/refresh/行→抽屉）

**Files**
- Create: `apps/dsa-web/src/pages/SignalBoardPage.tsx`
- Create: `apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx`

- [ ] **Step 1: 写失败测试**（mock stocksApi.getBoard + KLineDrawer，验证渲染/空态/行→抽屉）

```tsx
// apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const getBoard = vi.fn();
vi.mock('../../api/stocks', () => ({ stocksApi: { getBoard } }));
const drawerProps: any[] = [];
vi.mock('../../components/kline', () => ({
  KLineDrawer: (p: any) => { drawerProps.push(p); return p.isOpen ? <div data-testid="kline-drawer">{p.stockCode}</div> : null; },
}));

import SignalBoardPage from '../SignalBoardPage';

const entry = (over: any = {}) => ({
  code: '600519', name: '贵州茅台', market: 'CN', actionGroup: 'buy', ruleDirection: 'bullish',
  llmDirection: 'bullish', consistency: 'consistent', keySignals: ['volume_breakout'],
  priceLines: { entry: 1700, stop: 1620, target: 1850 }, latestClose: 1660,
  hitRate: 0.62, hitSample: 18, verified: true, status: 'ok', degradedReason: null, ...over });

describe('SignalBoardPage', () => {
  afterEach(() => { getBoard.mockReset(); drawerProps.length = 0; });

  it('renders board rows after fetch', async () => {
    getBoard.mockResolvedValueOnce({ asOf: 1, entries: [entry()], counts: { buy:1,hold:0,sell:0,unavailable:0 }, degradedCodes: [] });
    render(<SignalBoardPage />);
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
  });

  it('shows empty-watchlist guidance', async () => {
    getBoard.mockResolvedValueOnce({ asOf: 1, entries: [], counts: { buy:0,hold:0,sell:0,unavailable:0 }, degradedCodes: [] });
    render(<SignalBoardPage />);
    expect(await screen.findByTestId('board-empty')).toBeInTheDocument();
  });

  it('opens KLineDrawer on row click', async () => {
    getBoard.mockResolvedValueOnce({ asOf: 1, entries: [entry()], counts: { buy:1,hold:0,sell:0,unavailable:0 }, degradedCodes: [] });
    render(<SignalBoardPage />);
    fireEvent.click(await screen.findByText('贵州茅台'));
    expect(await screen.findByTestId('kline-drawer')).toHaveTextContent('600519');
  });
});
```

- [ ] **Step 2: 跑验证失败** → FAIL（页面不存在）。

- [ ] **Step 3: 写最小实现**

```tsx
// apps/dsa-web/src/pages/SignalBoardPage.tsx
import { useCallback, useEffect, useState } from 'react';
import { stocksApi } from '../api/stocks';
import type { SignalsBoardResponse } from '../types/kline';
import { SignalBoard } from '../components/board/SignalBoard';
import { KLineDrawer } from '../components/kline';
import { AppPage, Button, InlineAlert } from '../components/common';

const SignalBoardPage: React.FC = () => {
  const [data, setData] = useState<SignalsBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [klineTarget, setKlineTarget] = useState<{ stockCode: string; stockName?: string } | null>(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true); setError(null);
    try { setData(await stocksApi.getBoard(undefined, refresh)); }
    catch { setError('信号看板加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(false); }, [load]);

  const onRowClick = useCallback((stockCode: string, stockName?: string) => setKlineTarget({ stockCode, stockName }), []);

  return (
    <AppPage title="信号看板">
      <div className="mb-3 flex items-center justify-end">
        <Button variant="ghost" size="sm" onClick={() => load(true)} disabled={loading}>刷新</Button>
      </div>
      {error && <InlineAlert>{error}</InlineAlert>}
      {loading && !data && <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />}
      {data && data.entries.length === 0 && (
        <div data-testid="board-empty" className="text-sm text-secondary-text">自选为空，去「首页」添加自选股后再来看信号看板。</div>
      )}
      {data && data.entries.length > 0 && <SignalBoard entries={data.entries} onRowClick={onRowClick} />}
      {klineTarget && (
        <KLineDrawer stockCode={klineTarget.stockCode} stockName={klineTarget.stockName}
          isOpen onClose={() => setKlineTarget(null)} />
      )}
    </AppPage>
  );
};

export default SignalBoardPage;
```

> 注：`AppPage`/`Button`/`InlineAlert` 来自 `../components/common`（StockScreeningPage 同源）；`KLineDrawer` 从 `../components/kline` barrel 导出（实现时确认 barrel 路径，否则用 `../components/kline/KLineDrawer`）。
> 测试注意：若 `AppPage`/其子组件用到 router 上下文（`useLocation` 等），page 测试需用 `<MemoryRouter>` 包裹 `render(<SignalBoardPage/>)`（仿仓库现有 page 测试；运行报 "useLocation outside Router" 即补）。

- [ ] **Step 4: 跑验证通过 + eslint** → 3 passed；eslint 0。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/SignalBoardPage.tsx apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx
git commit -m "feat: 信号看板页(取数/空态/刷新/行→K线抽屉)(N3,容器C)"
```

---

# N4 · 路由/导航 + 门禁 + 文档

### Task N4.1：注册路由 + 侧栏 NavItem

**Files**
- Modify: `apps/dsa-web/src/App.tsx`、`apps/dsa-web/src/components/layout/SidebarNav.tsx`

- [ ] **Step 1: App.tsx 懒加载 + 路由**

```tsx
const SignalBoardPage = lazy(() => import('./pages/SignalBoardPage'));
// 在 Shell 包裹的 <Route> 组内追加：
<Route path="/board" element={<SignalBoardPage />} />
```

- [ ] **Step 2: SidebarNav NavItem**（默认常显；图标用 lucide-react，import 行追加 `LayoutDashboard`）

```tsx
// import 行追加 LayoutDashboard
// NAV_ITEMS 内（建议放在 screening 之后）追加：
{ key: 'board', label: '信号看板', to: '/board', icon: LayoutDashboard },
```

- [ ] **Step 3: 验证（无空格副本）**

Run: `cd /tmp/<copy>/apps/dsa-web && npx vitest run src/components/board src/pages src/api/__tests__/stocks.board.test.ts && npx eslint src/components/board src/pages/SignalBoardPage.tsx src/App.tsx src/components/layout/SidebarNav.tsx`
Expected: 全 passed；eslint 0。

- [ ] **Step 4: Commit**

```bash
git add apps/dsa-web/src/App.tsx apps/dsa-web/src/components/layout/SidebarNav.tsx
git commit -m "feat: 注册 /board 路由与侧栏「信号看板」入口(N4,容器C)"
```

### Task N4.2：全量门禁 + CHANGELOG + 专题文档

**Files**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加一行）
- Create: `docs/signal-board.md`（专题）

- [ ] **Step 1: 后端 ci_gate + 前端全量门禁（无空格副本/worktree）**

Run（后端）：`PATH=".venv/bin:$PATH" ./scripts/ci_gate.sh all 2>&1 | tail -15` → all checks passed。
Run（前端）：`cd /tmp/<copy>/apps/dsa-web && npx vitest run && npx eslint . && npm run build` → 全绿、build 产出 dist。

- [ ] **Step 2: CHANGELOG `[Unreleased]` 追加一行（扁平、无类目标题）**

```
- [新功能] 新增「信号看板」(容器C)：对自选池(STOCK_LIST)近实时计算量价信号，按动作(买入/观望/卖出/数据不可用)分组的密集可排序表格，点行复用 K 线抽屉；新增 GET /api/v1/signals/board 端点(并发复用 build_signals_for_code、单股失败仅降级该行、TTL 缓存)与 SIGNALS_BOARD_* 配置
```

- [ ] **Step 3: 专题文档 `docs/signal-board.md`**：范围(自选近实时)、动作分组口径(规则收敛方向，失败→数据不可用)、端点契约、缓存/并发/降级语义、与单股 /signals 的复用关系、与 AlphaSift「选股」的区别(量价信号 vs 基本面/LLM)。

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md docs/signal-board.md
git commit -m "docs: 信号看板(容器C) CHANGELOG 与专题文档(N4)"
```

---

## 验证矩阵（交付说明）
- **改了什么**：抽出 `build_signals_for_code`/`BoardSignals`(新 service)；`build_board` 并发聚合+降级+TTL 缓存；`GET /api/v1/signals/board`(新 signals router)；前端 `getBoard`+类型+分组表格组件+看板页+路由/导航；`SIGNALS_BOARD_*` 配置。
- **为什么**：兑现容器 C——自选池一屏「动作标签+证据」可操作看板，复用 L3 引擎/端点/抽屉，不造平行链。
- **验证情况**：后端 `tests/test_signal_board_service.py`/`test_signals_board_endpoint.py` + 单股 `test_signals_endpoint.py` 等价回归 + `ci_gate.sh`；前端 `stocks.board.test.ts`/`SignalBoard.test.tsx`/`SignalBoardPage.test.tsx` + `eslint .` + `npm run build`。
- **未验证项**：真实多市场自选池的端到端取数耗时与并发压力需 `npm run dev` 接真实后端人工目检（沿用 L3 verify 方式）。
- **风险点**：并发对 data_provider 压力(有界并发+TTL 缓存)；多 worker 部署缓存不共享(可接受)；命中率按 code 近似(沿用 M2c)。
- **回滚方式**：纯增量——回退 signals router/看板页/分组组件/getBoard 即可；`build_signals_for_code` 抽取(N0)可保留(单股端点已等价)或一并回退。

---

## 执行方式
沿用 L3：**Subagent-Driven**（每任务 fresh 实现 subagent + spec/质量两段 review），无空格 worktree 验证（后端用真实 `.venv` 绝对路径、cwd=worktree；前端 `npm ci` 于无空格副本）。
