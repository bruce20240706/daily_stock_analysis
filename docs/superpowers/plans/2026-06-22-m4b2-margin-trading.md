# M4-B-2 融资融券呈现 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在报告里新增「融资融券」section——A股个股的融资余额/融资买入额/融券余量 + 交易日/交易所，确定性后处理填充、presence-only、对决策完全只读。

**Architecture:** 镜像已落地的 M4-B-1 资金面（capital_flow）链路：免 token akshare 沪深明细 → `AkshareFundamentalAdapter.get_margin_detail` → `DataFetcherManager.get_margin_context`（fail-open 块）→ `fundamental_context["margin"]` → `fill_margin_if_needed` 填 `dashboard.data_perspective["margin_trading"]` → 两条 notification 渲染路径。与 capital_flow 的唯一关键分歧：**margin 仅呈现，不喂 LLM prompt、不进 decision_stability、不产 bias**。

**Tech Stack:** Python 3 / pandas / akshare（沪深融资融券明细，免 key）/ pydantic v2 / Jinja2 / pytest。

**Spec:** `docs/superpowers/specs/2026-06-22-m4b2-margin-trading-surface-design.md`（已评审通过）。

## Global Constraints

- 提交信息英文类型前缀 + 中文描述，**不加 `Co-Authored-By`**，不加工具/agent 来源前缀。
- **未经用户明确确认，不执行 `git commit` / `git push` / `git tag`**；本计划的 commit 步骤在获授权后按上述风格执行。
- 字段词表全链冻结（D7）：`financing_balance` / `financing_buy` / `short_volume` / `trade_date` / `exchange`——model / builder 输出 / 两渲染路径 getter / 标签 / 测试断言**必须逐字一致**。
- 键名映射（D8）：`fundamental_context` 块键 = `margin`；dashboard/schema 字段 = `margin_trading`。**故意区分，禁止静默统一**。
- 零新增配置/数据源/token：复用 `fundamental_fetch_timeout_seconds` + A股/非 ETF/非北交所门控；**不改 `.env.example`**。
- 全程 additive、A股-gated、presence-only、fail-open：单一辅助源失败不阻断主分析流程。
- margin 对决策只读：**不**新增 prompt 行、**不**接 `_format_prompt`、**不**接 `stabilize_decision_with_structure`、**不**写 `decision_stability`、**不**返回 bias/tuple。
- adapter（`data_provider/fundamental_adapter.py`）**不得 import `data_provider/base.py`**（循环依赖）；BSE/ETF 排除靠 base 侧 `get_margin_context` 门控 + adapter 侧 allow-list 路由（`6→SSE`、`0/3→SZSE`、其余→not_supported）双层。
- 验证：`./scripts/ci_gate.sh`（flake8 critical E9/F63/F7/F82 + `pytest -m "not network"`）。所有新测试离线确定性（mock `_call_df_candidates` / mock adapter / 纯 dict fixture），不依赖网络。
- venv：`/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`；从 worktree 根 `/root/dsa-m4b` 运行 pytest。

---

## File Structure

- **Modify** `data_provider/fundamental_adapter.py` — 新增模块级 memo + `get_margin_detail` + `_margin_df_for`（Task 1）。
- **Create** `tests/test_margin_adapter.py` — adapter 层离线测试（Task 1）。
- **Modify** `src/schemas/report_schema.py` — 新增 `MarginTrading` + `DataPerspective.margin_trading`（Task 2）。
- **Modify** `data_provider/base.py` — 新增 `get_margin_context` + `margin` 接入全部块枚举站点 + 独立预算切片（Task 3）。
- **Create** `tests/test_margin_context.py` — 门控 + 枚举完整性 + `get_margin_context`（mock adapter）（Task 3）。
- **Modify** `src/analyzer.py` — `_build_margin_from_context` + `fill_margin_if_needed`（Task 4）。
- **Modify** `src/core/pipeline.py` — import `fill_margin_if_needed` + 两路径调用点（Task 4）。
- **Create** `tests/test_margin_surface.py` — builder/fill/fail-open/决策只读/渲染（Task 4 起，Task 5 续）。
- **Modify** `src/notification.py` + `templates/report_markdown.j2` + `src/report_language.py` — 两渲染路径 margin 块 + 双语标签（Task 5）。
- **Create** `docs/margin-trading.md`；**Modify** `docs/capital-flow.md` + `docs/CHANGELOG.md`（Task 6）。

---

### Task 1: Adapter `get_margin_detail`（沪深路由 + 有界回退 + 有界 memo）

**Files:**
- Modify: `data_provider/fundamental_adapter.py`（imports 顶部 + 模块级 memo 在 class 前 + class 内新增两方法）
- Test: `tests/test_margin_adapter.py`（新建）

**Interfaces:**
- Produces: `AkshareFundamentalAdapter.get_margin_detail(stock_code: str, deadline: Optional[float] = None) -> Dict[str, Any]`，返回 `{"status": "ok"|"partial"|"not_supported", "financing_balance": Optional[float], "financing_buy": Optional[float], "short_volume": Optional[float], "trade_date": Optional[str], "exchange": Optional[str], "source_chain": List[str], "errors": List[str]}`。
- Produces: `AkshareFundamentalAdapter._margin_df_for(exchange: str, fn_name: str, date_str: str) -> Optional[pd.DataFrame]`（带模块级有界 memo）。
- Produces: 模块级 `data_provider.fundamental_adapter._margin_detail_memo`（`OrderedDict[(str, str), pd.DataFrame]`，上限 4）。

- [ ] **Step 1: Write the failing tests**

Create `tests/test_margin_adapter.py`:

```python
# -*- coding: utf-8 -*-
import pandas as pd
import data_provider.fundamental_adapter as fa
from data_provider.fundamental_adapter import AkshareFundamentalAdapter


def _sse_df(code="600519"):
    return pd.DataFrame([{
        "信用交易日期": "20260619",
        "标的证券代码": code,
        "标的证券简称": "贵州茅台",
        "融资余额": 1.23e8,
        "融资买入额": 4.5e7,
        "融券余量": 1000.0,
        "融券余量金额": 9.9e9,
    }])


def _szse_df(code="000001"):
    return pd.DataFrame([{
        "证券代码": code,
        "证券简称": "平安银行",
        "融资余额": 2.0e8,
        "融资买入额": 6.0e7,
        "融券余量": 2000.0,
    }])


def setup_function(_):
    fa._margin_detail_memo.clear()


def test_get_margin_detail_sse_routing_and_fields(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), cands[0][0], []))
    out = adapter.get_margin_detail("600519")
    assert out["exchange"] == "SSE"
    assert out["financing_balance"] == 1.23e8
    assert out["financing_buy"] == 4.5e7
    assert out["short_volume"] == 1000.0  # 融券余量, not 融券余量金额
    assert out["trade_date"] is not None
    assert out["status"] in ("ok", "partial")


def test_get_margin_detail_szse_routing(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_szse_df(), cands[0][0], []))
    out = adapter.get_margin_detail("000001")
    assert out["exchange"] == "SZSE"
    assert out["financing_balance"] == 2.0e8


def test_get_margin_detail_bse_etf_other_not_supported(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    # endpoint would return data, but routing must short-circuit before calling it
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), "x", []))
    for code in ("830799", "920819", "510300", "159915", "900001"):
        out = adapter.get_margin_detail(code)
        assert out["status"] == "not_supported", code
        assert out["exchange"] is None


def test_get_margin_detail_empty_fail_open(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (None, None, ["stock_margin_detail_sse:ValueError"]))
    out = adapter.get_margin_detail("600519")
    assert out["status"] == "not_supported"
    assert out["financing_balance"] is None


def test_margin_df_for_memo_hit_single_fetch(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    calls = {"n": 0}

    def _spy(cands):
        calls["n"] += 1
        return _sse_df(), cands[0][0], []

    monkeypatch.setattr(adapter, "_call_df_candidates", _spy)
    a = adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    b = adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    assert calls["n"] == 1
    assert a is b


def test_margin_memo_bounded(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (_sse_df(), cands[0][0], []))
    for i in range(8):
        adapter._margin_df_for("SSE", "stock_margin_detail_sse", f"2026060{i}")
    assert len(fa._margin_detail_memo) <= 4


def test_margin_df_for_empty_not_cached(monkeypatch):
    adapter = AkshareFundamentalAdapter()
    monkeypatch.setattr(adapter, "_call_df_candidates",
                        lambda cands: (None, None, []))
    adapter._margin_df_for("SSE", "stock_margin_detail_sse", "20260619")
    assert len(fa._margin_detail_memo) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_adapter.py -q`
Expected: FAIL — `AttributeError: 'AkshareFundamentalAdapter' object has no attribute 'get_margin_detail'` / `module ... has no attribute '_margin_detail_memo'`.

- [ ] **Step 3: Add imports + module-level memo**

In `data_provider/fundamental_adapter.py`, change the import block (lines 9-16) from:

```python
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
```

to:

```python
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
```

Then immediately after `logger = logging.getLogger(__name__)` (line 18), add:

```python
# 融资融券（RZRQ）按日全市场明细的模块级有界 memo。
# key=(exchange, "YYYYMMDD")，value=非空全市场 DataFrame；仅缓存非空。
# 长驻 systemd 进程下用 LRU 上限封顶，避免全市场 DataFrame 无界堆积。
_MARGIN_MEMO_MAX = 4
_MARGIN_MEMO_LOCK = threading.Lock()
_margin_detail_memo: "OrderedDict[Tuple[str, str], pd.DataFrame]" = OrderedDict()
```

- [ ] **Step 4: Add `_margin_df_for` + `get_margin_detail` methods**

In `data_provider/fundamental_adapter.py`, after `get_dragon_tiger_flag` (ends at line 532), add inside class `AkshareFundamentalAdapter`:

```python
    def _margin_df_for(
        self, exchange: str, fn_name: str, date_str: str
    ) -> Optional[pd.DataFrame]:
        """按 (exchange, date) 取全市场融资融券明细，带模块级有界 memo。

        仅缓存非空 DataFrame（早盘空/预览快照不被钉死一整天）；锁只守护 dict
        读写/淘汰，不持锁抓网络（冷缓存极少量重复在途抓取可接受）。
        """
        key = (exchange, date_str)
        with _MARGIN_MEMO_LOCK:
            cached = _margin_detail_memo.get(key)
            if cached is not None:
                _margin_detail_memo.move_to_end(key)
                return cached
        df, _source, _errors = self._call_df_candidates([(fn_name, {"date": date_str})])
        if df is not None and not df.empty:
            with _MARGIN_MEMO_LOCK:
                _margin_detail_memo[key] = df
                _margin_detail_memo.move_to_end(key)
                while len(_margin_detail_memo) > _MARGIN_MEMO_MAX:
                    _margin_detail_memo.popitem(last=False)
        return df

    def get_margin_detail(
        self, stock_code: str, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        """返回 A 股个股最新交易日融资融券（RZRQ）快照（fail-open）。

        免 token akshare 沪深明细。presence-only：异常/无数据 → status='not_supported'，
        绝不抛给调用方。交易所路由用 allow-list（6→SSE、0/3→SZSE，其余→not_supported），
        天然排除北交所(4/8/9)/ETF(5/1)/B股(9/2)，无需 import base。
        最新交易日 = smart-start（最近工作日）+ 有界回退 <=3 个工作日候选取首个非空
        （非精确交易日历，节假日不建模）；命中日写入 trade_date，更旧日 → status='partial'。
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "financing_balance": None,
            "financing_buy": None,
            "short_volume": None,
            "trade_date": None,
            "exchange": None,
            "source_chain": [],
            "errors": [],
        }
        pure_code = _normalize_code(stock_code)
        if pure_code.startswith("6"):
            exchange, fn_name = "SSE", "stock_margin_detail_sse"
        elif pure_code.startswith(("0", "3")):
            exchange, fn_name = "SZSE", "stock_margin_detail_szse"
        else:
            return result

        candidates: List[str] = []
        cursor = datetime.now()
        while len(candidates) < 3:
            if cursor.weekday() < 5:  # 周一至周五
                candidates.append(cursor.strftime("%Y%m%d"))
            cursor = cursor - timedelta(days=1)

        for idx, date_str in enumerate(candidates):
            if deadline is not None and time.monotonic() >= deadline:
                break
            df = self._margin_df_for(exchange, fn_name, date_str)
            if df is None or df.empty:
                continue
            row = _extract_latest_row(df, stock_code)
            if row is None:
                continue
            result["financing_balance"] = _safe_float(_pick_by_keywords(row, ["融资余额"]))
            result["financing_buy"] = _safe_float(
                _pick_by_keywords(row, ["融资买入额", "融资买入"])
            )
            result["short_volume"] = _safe_float(_pick_by_keywords(row, ["融券余量"]))
            result["trade_date"] = date_str
            result["exchange"] = exchange
            result["source_chain"].append(f"margin:{fn_name}")
            result["status"] = "ok" if idx == 0 else "partial"
            break
        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_adapter.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
cd /root/dsa-m4b
git add data_provider/fundamental_adapter.py tests/test_margin_adapter.py
git commit -m "feat: 新增融资融券 adapter get_margin_detail(沪深路由+有界回退+有界memo)"
```

---

### Task 2: 报告 schema `MarginTrading`

**Files:**
- Modify: `src/schemas/report_schema.py`（在 `CapitalFlow` 后新增模型；`DataPerspective` 追加字段）
- Test: `tests/test_margin_surface.py`（新建，本任务先放 schema 测试）

**Interfaces:**
- Produces: `MarginTrading` pydantic 模型，字段 `financing_balance / financing_buy / short_volume`（`Optional[Union[int, float, str]]`）、`trade_date / exchange`（`Optional[str]`），全默认 None。
- Produces: `DataPerspective.margin_trading: Optional[MarginTrading] = None`。

- [ ] **Step 1: Write the failing test**

Create `tests/test_margin_surface.py`:

```python
# -*- coding: utf-8 -*-
import copy
import types

from src.schemas.report_schema import DataPerspective, MarginTrading


def test_margin_trading_model_fields():
    mt = MarginTrading(
        financing_balance=1.23e8, financing_buy=4.5e7,
        short_volume=1000, trade_date="20260619", exchange="SSE",
    )
    assert mt.financing_balance == 1.23e8
    assert mt.trade_date == "20260619"
    assert mt.exchange == "SSE"


def test_margin_trading_all_optional():
    mt = MarginTrading()
    assert mt.financing_balance is None
    assert mt.exchange is None


def test_data_perspective_backward_compatible_without_margin():
    # 旧 payload 无 margin_trading 键仍解析；默认 None。
    dp = DataPerspective.model_validate({"chip_structure": {"chip_health": "健康"}})
    assert dp.margin_trading is None


def test_data_perspective_accepts_margin_trading():
    dp = DataPerspective.model_validate(
        {"margin_trading": {"financing_balance": 1.0e8, "exchange": "SZSE"}}
    )
    assert dp.margin_trading.exchange == "SZSE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q`
Expected: FAIL — `ImportError: cannot import name 'MarginTrading' from 'src.schemas.report_schema'`.

- [ ] **Step 3: Add the model and field**

In `src/schemas/report_schema.py`, after `CapitalFlow` (ends line 81), before `class DataPerspective` (line 84), insert:

```python
class MarginTrading(BaseModel):
    """融资融券（A股；最新交易日快照，presence-only）。"""

    financing_balance: Optional[Union[int, float, str]] = None  # 融资余额（元）
    financing_buy: Optional[Union[int, float, str]] = None      # 融资买入额（元）
    short_volume: Optional[Union[int, float, str]] = None        # 融券余量（股）
    trade_date: Optional[str] = None                            # YYYYMMDD（实际命中日）
    exchange: Optional[str] = None                              # SSE / SZSE
```

Then in `class DataPerspective`, after line 91 `capital_flow: Optional[CapitalFlow] = None`, add:

```python
    margin_trading: Optional[MarginTrading] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /root/dsa-m4b
git add src/schemas/report_schema.py tests/test_margin_surface.py
git commit -m "feat: 报告 schema 新增 MarginTrading 模型(挂 DataPerspective.margin_trading)"
```

---

### Task 3: base.py `get_margin_context` + `margin` 接入全部块枚举站点

**Files:**
- Modify: `data_provider/base.py`（新增 `get_margin_context`；`margin` 接入 8 处枚举/初始化站点 + 普通/ETF 分支抓取）
- Test: `tests/test_margin_context.py`（新建）

**Interfaces:**
- Consumes: `AkshareFundamentalAdapter.get_margin_detail(stock_code, deadline)`（Task 1）。
- Produces: `DataFetcherManager.get_margin_context(stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]`，返回 `_build_fundamental_block` 形状：`{"status", "data": {"financing_balance", "financing_buy", "short_volume", "trade_date", "exchange"}, "source_chain", "errors"}`。
- Produces: `get_fundamental_context` / `_build_market_not_supported` / `build_failed_fundamental_context` / `_build_offshore_fundamental_context` 的结果中含 `margin` 块 + `coverage["margin"]`。

- [ ] **Step 1: Write the failing tests**

Create `tests/test_margin_context.py`:

```python
# -*- coding: utf-8 -*-
import pandas as pd  # noqa: F401  (parity with sibling tests; not strictly needed)
from data_provider.base import DataFetcherManager


def _mgr():
    return DataFetcherManager()


def test_get_margin_context_gate_not_supported_for_non_a_share():
    mgr = _mgr()
    for code in ("00700", "AAPL", "510300", "830799", "920819"):
        block = mgr.get_margin_context(code)
        assert block["status"] == "not_supported", code


def test_get_margin_context_ok_with_mocked_adapter(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_margin_detail",
        lambda code, deadline=None: {
            "status": "ok", "financing_balance": 1.23e8, "financing_buy": 4.5e7,
            "short_volume": 1000.0, "trade_date": "20260619", "exchange": "SSE",
            "source_chain": ["margin:stock_margin_detail_sse"], "errors": [],
        },
    )
    block = mgr.get_margin_context("600519")
    assert block["status"] == "ok"
    assert block["data"]["financing_balance"] == 1.23e8
    assert block["data"]["trade_date"] == "20260619"
    assert block["data"]["exchange"] == "SSE"


def test_get_margin_context_fail_open_on_non_dict(monkeypatch):
    mgr = _mgr()
    monkeypatch.setattr(
        mgr._fundamental_adapter, "get_margin_detail",
        lambda code, deadline=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # _run_with_retry 捕获异常 → payload 非 dict → failed 块（不抛）
    block = mgr.get_margin_context("600519")
    assert block["status"] == "failed"


def test_enumeration_not_supported_factory_includes_margin():
    mgr = _mgr()
    nf = mgr._build_market_not_supported("etf", "x")
    assert "margin" in nf
    assert "margin" in nf["coverage"]
    assert nf["coverage"]["margin"] == "not_supported"


def test_enumeration_failed_factory_includes_margin():
    mgr = _mgr()
    bf = mgr.build_failed_fundamental_context("600519", "x")
    assert "margin" in bf
    assert bf["coverage"]["margin"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_context.py -q`
Expected: FAIL — `AttributeError: 'DataFetcherManager' object has no attribute 'get_margin_context'` (and the enumeration tests fail on missing `margin` key).

- [ ] **Step 3a: Add `get_margin_context` method**

In `data_provider/base.py`, after `get_dragon_tiger_context` (ends line 3271), before `def get_board_context` (line 3273), insert:

```python
    def get_margin_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """融资融券块（fail-open）。仅呈现、对决策只读。"""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code) or is_bse_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )
        deadline = time.monotonic() + timeout
        payload, err, cost_ms = self._run_with_retry(
            lambda: self._fundamental_adapter.get_margin_detail(stock_code, deadline=deadline),
            timeout,
            "margin",
        )
        if not isinstance(payload, dict):
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": cost_ms}],
                [err or "margin failed"],
            )

        adapter_status = str(payload.get("status", "not_supported"))
        has_content = any(
            payload.get(k) is not None
            for k in ("financing_balance", "financing_buy", "short_volume")
        )
        if has_content:
            margin_status = adapter_status if adapter_status in ("ok", "partial") else "partial"
        elif adapter_status == "not_supported":
            margin_status = "not_supported"
        else:
            margin_status = "partial"

        return self._build_fundamental_block(
            margin_status,
            {
                "financing_balance": payload.get("financing_balance"),
                "financing_buy": payload.get("financing_buy"),
                "short_volume": payload.get("short_volume"),
                "trade_date": payload.get("trade_date"),
                "exchange": payload.get("exchange"),
            },
            self._normalize_source_chain(
                payload.get("source_chain", []),
                "margin",
                margin_status,
                cost_ms,
            ),
            list(payload.get("errors", [])) + ([err] if err else []),
        )
```

- [ ] **Step 3b: Add `margin` to `_should_cache_fundamental_context`**

In `data_provider/base.py`, in the tuple at lines 2580-2588, add `"margin",` after `"institution",`:

```python
        for block in (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "margin",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
```

- [ ] **Step 3c: Add `margin` not-supported block to `_build_market_not_supported`**

In `data_provider/base.py`, in the `blocks` dict, after the `"institution": self._build_fundamental_block(...)` entry (ends line 2619) and before `"capital_flow":` (line 2620), insert:

```python
            "margin": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
```

(`coverage` is auto-derived from `blocks` at lines 2642-2644, so no further edit needed here.)

- [ ] **Step 3d: Add `margin` to the offshore context builder (4 sub-edits)**

In `_build_offshore_fundamental_context`:

(i) `result_ctx` init (lines 2687-2700): after `"institution": {},` (line 2692), add `"margin": {},`.

(ii) not-supported loop (line 2783): change to include `"margin"`:

```python
        for block in ("institution", "margin", "capital_flow", "dragon_tiger", "boards"):
```

(iii) `block_statuses` dict (lines 2793-2801): after `"institution": "not_supported",` (line 2797), add `"margin": "not_supported",`.

(iv) error/source-chain loop (line 2803): change to include `"margin"`:

```python
        for block in ("valuation", "growth", "earnings", "institution", "margin", "capital_flow", "dragon_tiger", "boards"):
```

- [ ] **Step 3e: Add `margin` to `build_failed_fundamental_context`**

In `data_provider/base.py`, in `block_names` (lines 2828-2836), add `"margin",` after `"institution",`:

```python
        block_names = (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "margin",
            "capital_flow",
            "dragon_tiger",
            "boards",
        )
```

(`coverage` auto-derived at line 2849.)

- [ ] **Step 3f: Add `margin` to the CN `get_fundamental_context` (4 sub-edits)**

(i) `result_ctx` init (lines 2902-2914): after `"institution": {},` (line 2907), add `"margin": {},`.

(ii) ETF branch (lines 3073-3092): after the `result_ctx["boards"] = self._build_fundamental_block(... "etf not fully supported" ...)` block (ends line 3091) and before `result_ctx["status"] = "partial"` (line 3092), add:

```python
            result_ctx["margin"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
```

(iii) non-ETF branch: right after the `else:` (line 3093) and before `capital_flow_budget = ...` (line 3094), add the margin fetch with its own budget slice:

```python
            margin_budget = min(fetch_timeout, remaining_seconds)
            margin_start = time.time()
            result_ctx["margin"] = self.get_margin_context(
                stock_code,
                budget_seconds=margin_budget,
            )
            _consume_budget(int((time.time() - margin_start) * 1000))

```

(iv) `block_statuses` dict (lines 3115-3123): after `"institution": result_ctx["institution"].get("status", "not_supported"),` (line 3119), add (defensive `.get` per spec §4.2):

```python
            "margin": result_ctx.get("margin", {}).get("status", "not_supported"),
```

(v) error/source-chain loop tuple (lines 3125-3133): add `"margin",` after `"institution",`:

```python
        for block in (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "margin",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
```

(The subscript `result_ctx[block]` at line 3134 is safe because `margin` is always initialized at edit (i) and set in both ETF/non-ETF branches.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_context.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the existing fundamental/base suite for regression**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/ -q -k "fundamental or capital_flow or base" -m "not network"`
Expected: PASS (no regressions; coverage dicts now carry `margin` everywhere).

- [ ] **Step 6: Commit**

```bash
cd /root/dsa-m4b
git add data_provider/base.py tests/test_margin_context.py
git commit -m "feat: base 新增 get_margin_context 并把 margin 接入全部块枚举站点(独立预算切片+完整性回归)"
```

---

### Task 4: analyzer `_build_margin_from_context` + `fill_margin_if_needed` + pipeline 接线

**Files:**
- Modify: `src/analyzer.py`（在 `fill_capital_flow_if_needed` 后新增两函数）
- Modify: `src/core/pipeline.py`（import + 两路径调用点）
- Test: `tests/test_margin_surface.py`（追加 builder/fill/fail-open/决策只读用例）

**Interfaces:**
- Consumes: `fundamental_context["margin"]["data"]`（Task 3 产出，键 `financing_balance/financing_buy/short_volume/trade_date/exchange`）。
- Produces: `_build_margin_from_context(fundamental_context, language="zh") -> Optional[Dict[str, Any]]`，返回纯 dict（键同上）或 None。
- Produces: `fill_margin_if_needed(result, fundamental_context) -> None`，in-place 写 `result.dashboard["data_perspective"]["margin_trading"]`。

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_margin_surface.py`:

```python
# --- Task 4: builder / fill / fail-open / decision read-only ---
from src.analyzer import _build_margin_from_context, fill_margin_if_needed  # noqa: E402


def _mctx(status="ok"):
    return {
        "margin": {"status": status, "data": {
            "financing_balance": 1.23e8, "financing_buy": 4.5e7,
            "short_volume": 1000.0, "trade_date": "20260619", "exchange": "SSE",
        }}
    }


def test_build_margin_ok():
    out = _build_margin_from_context(_mctx("ok"))
    assert set(out) >= {"financing_balance", "financing_buy", "short_volume", "trade_date", "exchange"}
    assert out["financing_balance"] == 1.23e8
    assert out["exchange"] == "SSE"
    assert out["trade_date"] == "20260619"


def test_build_margin_partial_still_builds():
    out = _build_margin_from_context(_mctx("partial"))
    assert out is not None
    assert out["exchange"] == "SSE"


def test_build_margin_not_supported_returns_none():
    assert _build_margin_from_context(_mctx("not_supported")) is None
    assert _build_margin_from_context(_mctx("failed")) is None
    assert _build_margin_from_context({}) is None
    assert _build_margin_from_context(None) is None


def test_fill_sets_margin_trading():
    result = types.SimpleNamespace(dashboard={}, report_language="zh")
    fill_margin_if_needed(result, _mctx("ok"))
    mt = result.dashboard["data_perspective"]["margin_trading"]
    assert mt["financing_balance"] == 1.23e8
    # 冻结词表：无外溢/决策键
    assert set(mt) == {"financing_balance", "financing_buy", "short_volume", "trade_date", "exchange"}


def test_fill_not_supported_leaves_margin_absent():
    result = types.SimpleNamespace(dashboard={"data_perspective": {}}, report_language="zh")
    fill_margin_if_needed(result, _mctx("not_supported"))
    assert "margin_trading" not in result.dashboard["data_perspective"]


def test_fill_fail_open_on_exception(monkeypatch):
    import src.analyzer as az
    monkeypatch.setattr(az, "_build_margin_from_context",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    result = types.SimpleNamespace(dashboard={"data_perspective": {}}, report_language="zh")
    fill_margin_if_needed(result, _mctx("ok"))  # must not raise
    assert "margin_trading" not in result.dashboard["data_perspective"]


def test_fill_margin_does_not_touch_decision_stability():
    result = types.SimpleNamespace(
        dashboard={}, report_language="zh", decision_type="buy",
        confidence_level="高", operation_advice="买入",
    )
    fill_margin_if_needed(result, _mctx("ok"))
    assert result.dashboard.get("decision_stability") is None
    assert result.decision_type == "buy"  # 决策不变
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q`
Expected: FAIL — `ImportError: cannot import name '_build_margin_from_context' from 'src.analyzer'`.

- [ ] **Step 3a: Add builder + fill to `src/analyzer.py`**

In `src/analyzer.py`, after `fill_capital_flow_if_needed` (ends line 915) and before `_dragon_tiger_prompt_line` (line 918), insert:

```python
def _build_margin_from_context(
    fundamental_context: Optional[Dict[str, Any]], language: str = "zh"
) -> Optional[Dict[str, Any]]:
    """从 fundamental_context 的 margin 块确定性构建融资融券 section dict。

    presence-only：margin 块 status 非 ok/partial（含非 A股/ETF/北交所的 not_supported）→ None。
    仅呈现：返回纯 dict（冻结词表），绝不含 bias/决策字段；调用方对决策只读。
    键名映射（D8）：读 fundamental_context["margin"]，写 dashboard.data_perspective["margin_trading"]。
    language 形参为与 capital_flow 签名对齐保留；margin 无语言相关字段。
    """
    if not isinstance(fundamental_context, dict):
        return None
    mg = fundamental_context.get("margin")
    mg = mg if isinstance(mg, dict) else {}
    if str(mg.get("status") or "").strip().lower() not in ("ok", "partial"):
        return None
    data = mg.get("data") if isinstance(mg.get("data"), dict) else {}
    return {
        "financing_balance": data.get("financing_balance"),
        "financing_buy": data.get("financing_buy"),
        "short_volume": data.get("short_volume"),
        "trade_date": data.get("trade_date"),
        "exchange": data.get("exchange"),
    }


def fill_margin_if_needed(
    result: "AnalysisResult", fundamental_context: Optional[Dict[str, Any]]
) -> None:
    """确定性把融资融券填进 data_perspective.margin_trading（in-place）。

    presence-only + A股-gated；LLM 后、对决策只读（不喂 prompt、不碰 decision_stability）；
    失败静默跳过、不阻断主流程。
    """
    if not result:
        return
    try:
        built = _build_margin_from_context(
            fundamental_context, language=getattr(result, "report_language", "zh")
        )
        if built is None:
            return
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        result.dashboard = dashboard
        dp = dashboard.get("data_perspective") or {}
        dashboard["data_perspective"] = dp
        dp["margin_trading"] = built
        logger.info("[margin] Filled margin-trading section from fundamental_context")
    except Exception as e:
        logger.warning("[margin] Fill failed, skipping: %s", e)
```

- [ ] **Step 3b: Wire `fill_margin_if_needed` into pipeline import**

In `src/core/pipeline.py`, the import block (lines 30-38) — add `fill_margin_if_needed,` after `fill_capital_flow_if_needed,`:

```python
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_capital_flow_if_needed,
    fill_margin_if_needed,
    fill_price_position_if_needed,
    normalize_chip_structure_availability,
    populate_decision_action_fields,
    stabilize_decision_with_structure,
)
```

- [ ] **Step 3c: Wire call site — traditional path**

In `src/core/pipeline.py`, after the Step 7.6b block (lines 624-626) and before `# Step 7.7` (line 628), insert:

```python
            # Step 7.6c: 融资融券 section（presence-only, A股, 仅呈现、对决策只读, NO prompt/decision_stability）
            if result:
                fill_margin_if_needed(result, fundamental_context)

```

- [ ] **Step 3d: Wire call site — agent path**

In `src/core/pipeline.py`, after the capital_flow fill block (lines 1142-1145) and before `# price_position fallback` (line 1147), insert:

```python
            # 融资融券 section（与传统路径 Step 7.6c 同序：chip → capital_flow → margin → price_position → stabilize）
            if result:
                fill_margin_if_needed(result, fundamental_context)

```

Also update the parity comment on line 1143 from `chip → capital_flow → price_position → stabilize` to `chip → capital_flow → margin → price_position → stabilize`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q`
Expected: PASS (15 passed — 4 schema + 11 builder/fill).

- [ ] **Step 5: Verify pipeline import compiles**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m py_compile src/core/pipeline.py src/analyzer.py`
Expected: no output (success).

- [ ] **Step 6: Commit**

```bash
cd /root/dsa-m4b
git add src/analyzer.py src/core/pipeline.py tests/test_margin_surface.py
git commit -m "feat: 新增 fill_margin_if_needed 并接入两条 pipeline 路径(presence-only,对决策只读)"
```

---

### Task 5: 两条 notification 渲染路径 + 双语标签

**Files:**
- Modify: `src/notification.py`（Python 传统路径，capital_flow 块后）
- Modify: `templates/report_markdown.j2`（Jinja2 路径，capital_flow 块后）
- Modify: `src/report_language.py`（zh + en 各 4 个标签）
- Test: `tests/test_margin_surface.py`（追加两渲染路径 + zh/en 用例）

**Interfaces:**
- Consumes: `dashboard.data_perspective["margin_trading"]`（Task 4 产出）+ `labels.margin_label/financing_balance_label/financing_buy_label/short_volume_label`（本任务产出，仅供 j2）。

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_margin_surface.py`:

```python
# --- Task 5: both render paths (Python legacy + Jinja2) + zh/en ---
from unittest import mock  # noqa: E402
from src.analyzer import AnalysisResult  # noqa: E402
from src.services.report_renderer import render as _render  # noqa: E402


def _result_with_margin(report_language="zh"):
    return AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=72,
        trend_prediction="看多", operation_advice="持有", analysis_summary="稳健",
        report_language=report_language,
        dashboard={"data_perspective": {"margin_trading": {
            "financing_balance": "1.23亿", "financing_buy": "4500万",
            "short_volume": "1000", "trade_date": "20260619", "exchange": "SSE",
        }}},
    )


@mock.patch("src.notification.get_config")
def test_notification_legacy_renders_margin_zh(mock_cfg):
    from tests.test_notification import _make_config
    from src.notification import NotificationService
    mock_cfg.return_value = _make_config(report_renderer_enabled=False)
    out = NotificationService().generate_dashboard_report(
        [_result_with_margin("zh")], report_date="2026-06-19")
    assert "融资融券" in out
    assert "融资余额" in out
    assert "1.23亿" in out
    assert "20260619" in out
    assert "沪" in out  # exchange SSE → 沪


@mock.patch("src.notification.get_config")
def test_notification_legacy_renders_margin_en(mock_cfg):
    from tests.test_notification import _make_config
    from src.notification import NotificationService
    mock_cfg.return_value = _make_config(report_renderer_enabled=False, report_language="en")
    out = NotificationService().generate_dashboard_report(
        [_result_with_margin("en")], report_date="2026-06-19")
    assert "Margin Trading" in out
    assert "Financing Balance" in out
    assert "SSE" in out


@mock.patch("src.notification.get_config")
def test_notification_legacy_margin_absent_not_rendered(mock_cfg):
    from tests.test_notification import _make_config
    from src.notification import NotificationService
    mock_cfg.return_value = _make_config(report_renderer_enabled=False)
    r = AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=72, trend_prediction="看多",
        operation_advice="持有", analysis_summary="x",
        dashboard={"data_perspective": {"chip_structure": {"chip_health": "健康"}}})
    out = NotificationService().generate_dashboard_report([r], report_date="2026-06-19")
    assert "融资融券" not in out


def test_jinja2_renders_margin_labels_zh():
    out = _render("markdown", [_result_with_margin("zh")], summary_only=False)
    assert "融资融券" in out
    assert "融资余额" in out
    assert "融券余量" in out


def test_jinja2_renders_margin_labels_en():
    out = _render("markdown", [_result_with_margin("en")], summary_only=False)
    assert "Margin Trading" in out
    assert "Financing Balance" in out
    assert "Short Volume" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q -k "notification or jinja2"`
Expected: FAIL — assertions miss `融资融券` / `Margin Trading` (block not rendered yet; j2 may also raise `UndefinedError` on `labels.margin_label`).

- [ ] **Step 3a: Add Python legacy render block**

In `src/notification.py`, after the capital_flow block (ends line 1275, the `])` closing the dragon-tiger `report_lines.extend`) and before `# ========== 作战计划 ==========` (line 1277), insert (match the 20-space indentation of the cf block):

```python
                    # 融资融券（A股；presence-only；仅呈现、不喂 LLM、不改决策）
                    margin_data = data_persp.get('margin_trading', {})
                    if margin_data:
                        margin_label = 'Margin Trading' if report_language == 'en' else '融资融券'
                        fb_label = 'Financing Balance' if report_language == 'en' else '融资余额'
                        fbuy_label = 'Financing Buy' if report_language == 'en' else '融资买入'
                        sv_label = 'Short Volume' if report_language == 'en' else '融券余量'
                        _fb = margin_data.get('financing_balance')
                        _fbuy = margin_data.get('financing_buy')
                        _sv = margin_data.get('short_volume')
                        _exch = margin_data.get('exchange') or ''
                        if report_language != 'en':
                            _exch = {'SSE': '沪', 'SZSE': '深'}.get(_exch, _exch)
                        _td = margin_data.get('trade_date') or 'N/A'
                        report_lines.extend([
                            f"**{margin_label}**: {fb_label} "
                            f"{'N/A' if _fb is None else _fb} | "
                            f"{fbuy_label} {'N/A' if _fbuy is None else _fbuy} | "
                            f"{sv_label} {'N/A' if _sv is None else _sv}"
                            f" ({_exch} {_td})",
                            "",
                        ])
```

- [ ] **Step 3b: Add Jinja2 render block**

In `templates/report_markdown.j2`, after the capital_flow block's closing `{% endif %}` (line 116) and before the data-perspective section's closing `{% endif %}` (line 117), insert:

```jinja
{% set margin_data = data_persp.get('margin_trading', {}) %}
{% if margin_data %}
**{{ labels.margin_label }}**: {{ labels.financing_balance_label }} {{ 'N/A' if margin_data.get('financing_balance') is none else margin_data.get('financing_balance') }} | {{ labels.financing_buy_label }} {{ 'N/A' if margin_data.get('financing_buy') is none else margin_data.get('financing_buy') }} | {{ labels.short_volume_label }} {{ 'N/A' if margin_data.get('short_volume') is none else margin_data.get('short_volume') }} ({{ margin_data.get('exchange') or 'N/A' }} {{ margin_data.get('trade_date') or 'N/A' }})
{% endif %}
```

- [ ] **Step 3c: Add zh labels**

In `src/report_language.py`, after `"dragon_tiger_count_suffix": " 次",` (line 260, the zh dict), add:

```python
        "margin_label": "融资融券",
        "financing_balance_label": "融资余额",
        "financing_buy_label": "融资买入",
        "short_volume_label": "融券余量",
```

- [ ] **Step 3d: Add en labels**

In `src/report_language.py`, after `"dragon_tiger_count_suffix": "",` (line 372, the en dict), add:

```python
        "margin_label": "Margin Trading",
        "financing_balance_label": "Financing Balance",
        "financing_buy_label": "Financing Buy",
        "short_volume_label": "Short Volume",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest tests/test_margin_surface.py -q`
Expected: PASS (all margin-surface tests, both render paths zh/en green).

- [ ] **Step 5: Commit**

```bash
cd /root/dsa-m4b
git add src/notification.py templates/report_markdown.j2 src/report_language.py tests/test_margin_surface.py
git commit -m "feat: 两条通知渲染路径新增融资融券块 + 双语标签(显示交易日/交易所)"
```

---

### Task 6: 文档 + 全量后端门禁

**Files:**
- Create: `docs/margin-trading.md`
- Modify: `docs/capital-flow.md`（订正第 103 行延期说明）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加一行）

**Interfaces:** none（文档 + 门禁）。

- [ ] **Step 1: Create `docs/margin-trading.md`**

Create `docs/margin-trading.md` (mirror `docs/capital-flow.md` structure; **显式写 D4 分歧**):

```markdown
# 融资融券（Margin Trading）呈现

M4-B-2 在报告中新增「融资融券」section，确定性呈现 A 股个股最新交易日的融资融券快照。

## 数据来源

- 免 token akshare 沪深明细：`stock_margin_detail_sse(date=YYYYMMDD)`（沪，代码列「标的证券代码」）、`stock_margin_detail_szse(date=YYYYMMDD)`（深，代码列「证券代码」）。两接口按日返回全市场明细。
- 交易所路由（allow-list）：`6xx → SSE`、`0xx/3xx → SZSE`；北交所(4/8/9)、ETF(5/1)、B 股(9/2) → `not_supported`（不落端点）。

## 字段

| 字段 | 含义 | 单位 |
| --- | --- | --- |
| `financing_balance` | 融资余额 | 元 |
| `financing_buy` | 融资买入额 | 元 |
| `short_volume` | 融券余量 | 股 |
| `trade_date` | 实际命中交易日 | YYYYMMDD |
| `exchange` | 交易所 | SSE / SZSE |

全部 Optional / presence-only / None-tolerant。

## 门控与语义

- **仅 A 股**（沪深主板/科创/创业）；港股/美股/crypto/ETF/北交所无该 section。
- **presence-only**：margin 块 status ∈ {ok, partial} 才出现；否则 `data_perspective.margin_trading` 为 None、两条 notification 渲染路径不渲染该块。
- **最新交易日**：smart-start（最近工作日）+ 有界回退 ≤3 个工作日候选取首个非空（非精确交易日历，节假日不建模）；命中更旧日 → status=`partial`，`trade_date` 始终随余额一起显示以暴露陈旧度。
- **fail-open**：抓取异常/空数据不抛、不阻断主分析流程。
- **有界 memo**：按 (exchange, date) 缓存非空全市场明细，LRU 上限 4，仅缓存非空。

## 关键分歧（与资金面 capital_flow 不同，reviewer 必查）

融资融券**仅呈现**：不喂 LLM prompt、不进 `decision_stability`、不产生任何 bias、对决策完全只读。
（capital_flow 则相反：喂 prompt 且驱动 post-LLM 降级。）

## 不动的既有行为

资金面（capital_flow）/龙虎榜、主力资金流 prompt 与降级、decision_stability、换手率/量比/筹码 既有 section 全部零改动。

## v1 已知局限

- 仅最新交易日快照，无趋势/多日序列/占比衍生。
- 有界回退非精确日历；长假/晚发布可能命中数日前快照（以 `trade_date` + `partial` 暴露）。
- 北向 / HSGT 个股数据 2024 起被交易所停更，无源可纳入（永久局限）。
- 无专用 Web 组件（同 capital_flow/chip/volume，经 notification markdown + 报告 payload 呈现）。
- 配置：复用 `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS` + A 股门控，零新增 key。
```

- [ ] **Step 2: Fix `docs/capital-flow.md:103`**

In `docs/capital-flow.md`, replace line 103:

```markdown
- **北向资金 / 融资融券未纳入**：留待 M4-B-2 迭代补充。
```

with:

```markdown
- **融资融券已纳入**（M4-B-2，见 [margin-trading.md](margin-trading.md)）；**北向资金**因交易所 2024 年停更个股级数据，无源可纳入（永久局限）。
```

- [ ] **Step 3: Append CHANGELOG flat entry**

In `docs/CHANGELOG.md`, in the `[Unreleased]` section, append one flat line after the existing entries / marker comment (no `###` heading):

```markdown
- [新功能] 报告新增「融资融券」呈现：A股融资余额/融资买入/融券余量 + 交易日/交易所确定性呈现（akshare 沪深明细，免 key，presence-only，仅呈现不喂 LLM、不改决策），notification 两条渲染路径与双语标签覆盖
```

- [ ] **Step 4: Verify doc references and run full backend gate**

Run: `cd /root/dsa-m4b && ./scripts/ci_gate.sh`
Expected: PASS — flake8 critical (E9/F63/F7/F82) clean + `pytest -m "not network"` all green (incl. `test_margin_adapter.py`, `test_margin_context.py`, `test_margin_surface.py`).

If `ci_gate.sh` is unavailable in environment, run the equivalent:
Run: `cd /root/dsa-m4b && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m flake8 --select=E9,F63,F7,F82 data_provider/fundamental_adapter.py data_provider/base.py src/analyzer.py src/core/pipeline.py src/notification.py src/report_language.py src/schemas/report_schema.py && "/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python" -m pytest -m "not network" -q`

- [ ] **Step 5: Commit**

```bash
cd /root/dsa-m4b
git add docs/margin-trading.md docs/capital-flow.md docs/CHANGELOG.md
git commit -m "docs: 新增融资融券专题文档 + 订正资金面延期说明 + CHANGELOG"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
| --- | --- |
| §4.1 抓取层 `get_margin_detail`（路由/回退/deadline/memo/字段） | Task 1 |
| §4.2 `get_margin_context` + 枚举接入 + 独立预算切片 + `.get` 防御 | Task 3 |
| §4.3 `_build_margin_from_context` + `fill_margin_if_needed`（presence-only/纯 dict/写 margin_trading） | Task 4 |
| §4.4 pipeline import + 两路径调用点 | Task 4 |
| §4.5 两渲染路径 + 标签锁步 | Task 5 |
| §4.6 `MarginTrading` + `DataPerspective.margin_trading` | Task 2 |
| §6 字段契约 / D7 词表 / D8 键名映射 | Tasks 1-5（冻结词表逐字一致） |
| §7 陈旧度(trade_date/partial)/空df/预算/memo/BSE | Tasks 1, 3 |
| §9 测试矩阵（两路径/子集断言/无外溢/决策只读/not_supported/fail-open空与异常/枚举完整性/memo/双语） | Tasks 1,3,4,5 |
| §10 门禁 / §6 文档 / .env 不改 | Task 6 |
| D4 不喂 LLM/不进 decision | Task 4（`test_fill_margin_does_not_touch_decision_stability`）+ §1 约束（无 prompt 行） |

**2. Placeholder scan:** 无 TBD/TODO/"similar to"/"add error handling"——每个代码步骤含完整代码与精确插入锚点。

**3. Type consistency:** `get_margin_detail` 返回键 ↔ `get_margin_context` 读取键 ↔ `_build_fundamental_block` 的 `data` 键 ↔ `_build_margin_from_context` 读 `["margin"]["data"]` 键 ↔ `fill` 写 `margin_trading` dict 键 ↔ 两渲染路径 getter ↔ `MarginTrading` 模型字段 ↔ 测试断言——全部 `{financing_balance, financing_buy, short_volume, trade_date, exchange}`，逐字一致。块键 `margin`（fundamental_context）与 dashboard 键 `margin_trading` 故意区分（D8），builder 是唯一映射点。

**已知近似（非阻断，spec §7 已记）:** `_pick_by_keywords(row, ["融券余量"])` 依赖 akshare 列序中「融券余量」先于「融券余量金额」；live 探测确认沪深明细列序如此。fixture `_sse_df` 同时含两列以回归此点。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-22-m4b2-margin-trading.md`. 两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派新 subagent，Task 间两段式 review，迭代快。

**2. Inline Execution** — 本会话内按 executing-plans 批量执行 + checkpoint。

注：本计划含 commit 步骤，但按仓库硬规则**需用户明确授权后**才执行 `git commit`。
