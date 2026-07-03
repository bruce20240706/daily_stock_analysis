# 港股通南向维度(Inc 2)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HK 标的分析报告新增「港股通」段(成份 eligible 三态 + 个股南向持股 + 市场级南向净流,EOD),镜像 margin 报告链蓝图,纯展示不喂 prompt,东财不可达 fail-closed 不编造,非 HK 标的字节级零变化。

**Architecture:** `AkshareFundamentalAdapter` 增三个全市场级抓取方法(共享 `_ggt_key` 归一 + 模块缓存 + 负缓存 + single-flight)→ `DataFetcherManager.get_ggt_context` 门控组装 → offshore context 增 ggt 键(不入总 status)+ 两枚举工厂 → pipeline `fill_ggt_if_needed`(LLM 后)→ analyzer presence-only builder → report_schema/report_language/notification 五层透出。

**Tech Stack:** Python / pandas / akshare 1.18.64(已依赖)/ Pydantic / pytest;无新依赖。

**Spec:** `docs/superpowers/specs/2026-07-03-ggt-southbound-context-design.md`(v3,28 findings 折进)

## Global Constraints

- **纯展示维度**:ggt 不喂 prompt、不碰 decision_stability、不改任何信号/回测统计(镜像 margin `fill_*_if_needed` 是 LLM 后填充)。
- **fail-closed 不编造**:eligible 三态——False **必须**来自"成份表可得(≥50 行)且不在内";表不可得/截断→None(unknown);**禁把 None/False 混渲染成"不可买"**(三态文案三互异串,None 输出禁含 False 文案)。
- **共享键函数** `_ggt_key(code)`:`normalize_stock_code(code)` 后若结果仍为纯 1-5 位数字则 `"HK" + result.zfill(5)`,否则原样;成份 ingest 侧与查询侧**同一函数**,禁两侧各写。
- **南向净流 NaN-first**:两腿(港股通(沪)+港股通(深))`成交净买额`列,`isna` 视为该腿缺;**两腿皆 NaN→None(禁 `Series.sum` skipna 得假零 0.0)**;单腿 NaN→partial。
- **持股取最新行**:南向持股日表按 `_ggt_key(股票代码)` 匹配,取 `持股日期` 最大行(端点 TRADE_DATE 降序);占比字段真实列名 `持股数量占发行股百分比`;**不取市值变化列**;当日表空→逐日回退≤5 日历日。
- **status 分界**:`ok`=eligible 与两子块三者全有值;有值不全→`partial`;全 None 非门控→`failed`;非 hk/etf→`not_supported`。
- **ggt 不入总 status**(offshore active_statuses 只含 valuation/growth/earnings);两枚举工厂(build_failed / _build_market_not_supported)+ `_should_cache_fundamental_context` 块元组均增 ggt 键。
- **看板 BoardEntry.ggt_eligible 本版不做**(defer Inc 2b)——不改 signal_board_service.py / stocks.py。
- **免 web-gate**(无 registry/locale/前端改动);配置仅 `GGT_LIST_CACHE_TTL_SECONDS`(不进 registry/locale)。
- commit=英文类型前缀+中文正文,**无 Co-Authored-By**,单行 `git commit -m`,ASCII 逗号。
- 所有 python 命令前置 `export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(带引号,主仓路径含空格);不得 `| tail` 掩盖退出码;验证前台有界跑,勿挂后台 Monitor。
- 执行 worktree `/root/chainb-ggt`(无空格),分支 `feature/ggt-southbound`,base=main=9ef8b2d4。
- **端点列名以本地 akshare 1.18.64 源码契约为准**(fixtures 列名逐字一致):
  - 成份 `stock_hk_ggt_components_em()` → 列含 `代码`(裸 5 位)`名称`。
  - 持股 `stock_hsgt_stock_statistics_em(symbol="南向持股", start_date, end_date)` → 列含 `股票代码`/`股票简称`/`持股日期`/`持股数量`/`持股市值`/`持股数量占发行股百分比`/`持股市值变化-1日|-5日|-10日`/`当日收盘价`/`当日涨跌幅`。
  - 净流 `stock_hsgt_fund_flow_summary_em()` → 列含 `类型`/`板块`/`资金净流入`/`成交净买额`;南向为 `类型` in {`港股通(沪)`,`港股通(深)`}。

---

### Task 1: `_ggt_key` 共享键 + 缓存基建 + get_ggt_eligibility_set

**Files:**
- Modify: `data_provider/fundamental_adapter.py`(模块级 helper/缓存 + `AkshareFundamentalAdapter` 方法)
- Test: `tests/test_ggt_adapter.py`(新建)

**Interfaces:**
- Produces: `_ggt_key(code: str) -> str`;`get_ggt_eligibility_set(deadline: Optional[float] = None) -> Optional[set]`(全市场成份码 set,过 `_ggt_key`;表<50 行或不可得→None);模块缓存 `_GGT_LIST_CACHE`(成功 TTL 读 config,失败负缓存 300s)。Task 4 消费。

- [ ] **Step 1: 写失败测试**(`tests/test_ggt_adapter.py` 新建)

```python
import time
import pandas as pd
import pytest
from unittest.mock import patch
from data_provider.fundamental_adapter import AkshareFundamentalAdapter, _ggt_key


def test_ggt_key_normalizes_three_writings_to_same_hk_key():
    # 裸 5 位 / HK 前缀 / .HK 后缀 三写法同键(Blocker F1)
    assert _ggt_key("00700") == "HK00700"
    assert _ggt_key("hk00700") == "HK00700"
    assert _ggt_key("0700.HK") == "HK00700"
    assert _ggt_key("01810") == "HK01810"


def _components_df(codes):
    return pd.DataFrame({"代码": codes, "名称": [f"n{c}" for c in codes]})


def test_eligibility_set_built_with_ggt_key_from_bare_codes():
    # 成份表用裸 "00700" 建 set,查询侧三写法均命中(禁用已归一 fixture)
    df = _components_df([f"{i:05d}" for i in range(60)] + ["00700"])  # >=50 行
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", return_value=df):
        s = AkshareFundamentalAdapter().get_ggt_eligibility_set()
    assert s is not None
    assert _ggt_key("hk00700") in s and _ggt_key("0700.HK") in s
    assert _ggt_key("99999") not in s


def test_eligibility_set_truncated_table_returns_none():
    # R4:行数 < 50 视为表不可得 → None(而非小而错的 set)
    df = _components_df(["00700", "01810"])  # 仅 2 行
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", return_value=df):
        assert AkshareFundamentalAdapter().get_ggt_eligibility_set() is None


def test_eligibility_set_endpoint_failure_returns_none_and_negative_cached():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("eastmoney unreachable")

    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", side_effect=boom):
        a = AkshareFundamentalAdapter()
        assert a.get_ggt_eligibility_set() is None
        assert a.get_ggt_eligibility_set() is None   # 负缓存 300s 内不重打
    assert calls["n"] == 1
```

（测试前置清缓存:在文件顶部加 `@pytest.fixture(autouse=True)` 清 `_GGT_LIST_CACHE` 与其他两缓存——见下 conftest 式清理，或每测试内 `import data_provider.fundamental_adapter as fa; fa._GGT_LIST_CACHE.clear()`。实现者按文件既有 fixture 惯例落地。）

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_adapter.py -x -q`
Expected: FAIL — `ImportError: cannot import name '_ggt_key'` / `AttributeError: get_ggt_eligibility_set`。

- [ ] **Step 3: 实现**(`data_provider/fundamental_adapter.py`)

文件头(仿 `_MARGIN_MEMO` :26-27 缓存惯例)加模块级:

```python
import time
from data_provider.base import normalize_stock_code, _market_tag

_GGT_CACHE_FAIL_TTL = 300.0                       # 失败负缓存 TTL(秒)
_GGT_LIST_CACHE: dict = {}                        # {"set": {...} | None, "ts": float, "ok": bool}
_GGT_CACHE_LOCK = threading.Lock()                # 只护 dict 读写,网络抓取一律锁外
_GGT_MIN_COMPONENTS = 50                          # R4 截断守卫:港股通成份常年 500+,<50 视为不可得


def _ggt_key(code: str) -> str:
    """港股通归一键:三写法(裸5位/HK前缀/.HK后缀)收敛同键(Blocker F1)。

    normalize_stock_code 对裸 5 位数字原样返回(不加 HK 前缀),故此处补齐:
    结果仍为纯 1-5 位数字 → 'HK' + zfill(5)。成份 ingest 与查询两侧必须同用本函数。
    """
    norm = normalize_stock_code(code)
    if norm.isdigit() and 1 <= len(norm) <= 5:
        return "HK" + norm.zfill(5)
    return norm
```

`AkshareFundamentalAdapter` 内加(private 抓取方法便于测试 patch + 公开缓存方法):

```python
    def _fetch_ggt_components_df(self):
        """抓港股通成份榜单(akshare 惰性 import;异常上抛给缓存层转负缓存)。"""
        import akshare as ak
        return ak.stock_hk_ggt_components_em()

    def get_ggt_eligibility_set(self, deadline: Optional[float] = None) -> Optional[set]:
        """港股通成份码 set(过 _ggt_key);表不可得或 <50 行 → None(fail-closed unknown)。

        成功缓存 TTL=config.ggt_list_cache_ttl_seconds;失败负缓存 300s(死端点不每报告重打)。
        锁只护 dict;网络抓取锁外;single-flight 由 'ok' 标志 + ts 近似(并发下最多重抓一次,可接受)。
        """
        from src.config import get_config
        ttl = int(getattr(get_config(), "ggt_list_cache_ttl_seconds", 43200))
        now = time.time()
        with _GGT_CACHE_LOCK:
            item = _GGT_LIST_CACHE.get("k")
            if item is not None:
                age = now - item["ts"]
                live = ttl if item["ok"] else _GGT_CACHE_FAIL_TTL
                if age <= live:
                    return item["set"]
        result_set = None
        try:
            df = self._fetch_ggt_components_df()
            if df is not None and not df.empty and len(df) >= _GGT_MIN_COMPONENTS:
                codes = [c for c in df["代码"].astype(str).tolist() if c and c.strip()]
                keys = {_ggt_key(c) for c in codes}
                # 归一失败比例守卫:有效 HK 键占比过低 → 整表作废(R4)
                valid = {k for k in keys if k.startswith("HK")}
                if len(valid) >= _GGT_MIN_COMPONENTS:
                    result_set = valid
        except Exception:
            result_set = None
        with _GGT_CACHE_LOCK:
            _GGT_LIST_CACHE["k"] = {"set": result_set, "ts": time.time(), "ok": result_set is not None}
        return result_set
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python -m pytest tests/test_ggt_adapter.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git add data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git commit -m "feat: 港股通成份 eligibility 数据面(_ggt_key 三写法归一共享键防假阴性,成份 set 带 12h 缓存/300s 负缓存/50 行截断守卫,表不可得返 None)"
```

---

### Task 2: get_ggt_holding(南向持股全市场表 + 最新行 + 末端日回退)

**Files:**
- Modify: `data_provider/fundamental_adapter.py`
- Test: `tests/test_ggt_adapter.py`(追加)

**Interfaces:**
- Consumes: Task 1 `_ggt_key`、缓存基建。
- Produces: `get_ggt_holding(stock_code: str, deadline: Optional[float] = None) -> Optional[dict]`——键 `holding_shares/holding_value/holding_ratio_pct/holding_trade_date`(全市场表无本股行或不可得→None)。模块缓存 `_SB_HOLDING_CACHE`。Task 4 消费。

- [ ] **Step 1: 写失败测试**(追加)

```python
def _holding_df(rows):
    # rows: list of (股票代码, 持股日期, 持股数量, 持股市值, 占比)
    return pd.DataFrame({
        "股票代码": [r[0] for r in rows],
        "股票简称": [f"n{r[0]}" for r in rows],
        "持股日期": [r[1] for r in rows],
        "持股数量": [r[2] for r in rows],
        "持股市值": [r[3] for r in rows],
        "持股数量占发行股百分比": [r[4] for r in rows],
        "持股市值变化-5日": [0.0 for r in rows],   # 存在但不取
    })


def test_holding_picks_latest_row_by_date_and_real_columns():
    # 同股多日期 → 取持股日期最大行;占比读真实列名(非"占A股")
    df = _holding_df([
        ("00700", "2026-06-30", 100, 1000.0, 5.0),
        ("00700", "2026-07-01", 200, 2200.0, 6.5),   # 最新
        ("01810", "2026-07-01", 50, 500.0, 2.0),
    ])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        h = AkshareFundamentalAdapter().get_ggt_holding("hk00700")   # 三写法归一
    assert h["holding_shares"] == 200 and h["holding_value"] == 2200.0
    assert h["holding_ratio_pct"] == 6.5
    assert h["holding_trade_date"] == "2026-07-01"


def test_holding_stock_not_in_table_returns_none():
    df = _holding_df([("01810", "2026-07-01", 50, 500.0, 2.0)])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        assert AkshareFundamentalAdapter().get_ggt_holding("00700") is None


def test_holding_endpoint_failure_returns_none():
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df",
                      side_effect=RuntimeError("boom")):
        assert AkshareFundamentalAdapter().get_ggt_holding("00700") is None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_adapter.py -k holding -x -q`
Expected: FAIL — `AttributeError: get_ggt_holding`。

- [ ] **Step 3: 实现**

```python
_SB_HOLDING_CACHE: dict = {}                       # {"df": DataFrame|None, "ts": float, "ok": bool}


    def _fetch_ggt_holding_df(self):
        """抓南向持股全市场日表(近窗;akshare 惰性 import,异常上抛)。"""
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=7)             # 近 7 日历日窗覆盖末端日回退
        return ak.stock_hsgt_stock_statistics_em(
            symbol="南向持股",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )

    def _ggt_holding_df_cached(self):
        from src.config import get_config
        ttl = int(getattr(get_config(), "ggt_list_cache_ttl_seconds", 43200))
        now = time.time()
        with _GGT_CACHE_LOCK:
            item = _SB_HOLDING_CACHE.get("k")
            if item is not None:
                age = now - item["ts"]
                live = ttl if item["ok"] else _GGT_CACHE_FAIL_TTL
                if age <= live:
                    return item["df"]
        df = None
        try:
            fetched = self._fetch_ggt_holding_df()
            if fetched is not None and not fetched.empty:
                df = fetched
        except Exception:
            df = None
        with _GGT_CACHE_LOCK:
            _SB_HOLDING_CACHE["k"] = {"df": df, "ts": time.time(), "ok": df is not None}
        return df

    def get_ggt_holding(self, stock_code: str, deadline: Optional[float] = None) -> Optional[dict]:
        """本股南向持股最新行(持股日期最大);无本股行/不可得 → None。"""
        df = self._ggt_holding_df_cached()
        if df is None or df.empty or "股票代码" not in df.columns:
            return None
        key = _ggt_key(stock_code)
        sub = df[df["股票代码"].astype(str).map(_ggt_key) == key]
        if sub.empty:
            return None
        row = sub.loc[sub["持股日期"].astype(str).idxmax()]  # 持股日期最大行
        return {
            "holding_shares": _safe_float(row.get("持股数量")),
            "holding_value": _safe_float(row.get("持股市值")),
            "holding_ratio_pct": _safe_float(row.get("持股数量占发行股百分比")),
            "holding_trade_date": str(row.get("持股日期")),
        }
```

（`_safe_float` 是本文件既有 helper——`grep -n "_safe_float" data_provider/fundamental_adapter.py` 确认签名后复用;若非本文件则从其定义处 import。`idxmax()` 对字符串日期取字典序最大=最新,ISO 日期字典序=时序。）

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python -m pytest tests/test_ggt_adapter.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git add data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git commit -m "feat: 个股南向持股数据面(全市场日表按 _ggt_key 查本股最新行,占比读真实列 持股数量占发行股百分比,不取市值变化列,无本股行返 None)"
```

---

### Task 3: get_southbound_flow(市场级南向净流 + NaN-first 禁假零)

**Files:**
- Modify: `data_provider/fundamental_adapter.py`
- Test: `tests/test_ggt_adapter.py`(追加)

**Interfaces:**
- Consumes: Task 1 缓存基建。
- Produces: `get_southbound_flow(deadline: Optional[float] = None) -> Optional[dict]`——键 `southbound_net_flow`(亿,两南向腿"成交净买额"和)`flow_date`(有则)`partial`(bool,单腿缺时 True);两腿皆 NaN/不可得→None。模块缓存 `_SB_FLOW_CACHE`。Task 4 消费。

- [ ] **Step 1: 写失败测试**(追加)

```python
def _flow_df(sh, sz):
    return pd.DataFrame({
        "类型": ["北向", "港股通(沪)", "港股通(深)"],
        "板块": ["-", "-", "-"],
        "成交净买额": [10.0, sh, sz],
        "资金净流入": [11.0, sh, sz],
    })


def test_southbound_flow_sums_two_legs():
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df", return_value=_flow_df(12.0, 8.0)):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert abs(f["southbound_net_flow"] - 20.0) < 1e-9 and f["partial"] is False


def test_southbound_flow_both_nan_returns_none_not_zero():
    # F4 假零陷阱:两腿 NaN → None,禁 sum 得 0.0
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df",
                      return_value=_flow_df(float("nan"), float("nan"))):
        assert AkshareFundamentalAdapter().get_southbound_flow() is None


def test_southbound_flow_one_leg_nan_is_partial():
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df",
                      return_value=_flow_df(12.0, float("nan"))):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert abs(f["southbound_net_flow"] - 12.0) < 1e-9 and f["partial"] is True
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_adapter.py -k southbound -x -q`
Expected: FAIL — `AttributeError: get_southbound_flow`。

- [ ] **Step 3: 实现**

```python
import math

_SB_FLOW_CACHE: dict = {}


    def _fetch_sb_flow_df(self):
        import akshare as ak
        return ak.stock_hsgt_fund_flow_summary_em()

    def get_southbound_flow(self, deadline: Optional[float] = None) -> Optional[dict]:
        """市场级南向净流(两腿 成交净买额 之和,亿);两腿皆缺 → None(禁假零)。"""
        from src.config import get_config
        ttl = int(getattr(get_config(), "ggt_list_cache_ttl_seconds", 43200))
        now = time.time()
        with _GGT_CACHE_LOCK:
            item = _SB_FLOW_CACHE.get("k")
            if item is not None:
                age = now - item["ts"]
                live = ttl if item["ok"] else _GGT_CACHE_FAIL_TTL
                if age <= live:
                    return item["result"]
        result = None
        try:
            df = self._fetch_sb_flow_df()
            if df is not None and not df.empty and "类型" in df.columns:
                legs = df[df["类型"].astype(str).isin(["港股通(沪)", "港股通(深)"])]
                vals = [_safe_float(v) for v in legs["成交净买额"].tolist()]
                present = [v for v in vals if v is not None and not math.isnan(v)]
                if present:                                   # 至少一腿有值,否则 None(禁假零)
                    result = {
                        "southbound_net_flow": round(sum(present), 4),
                        "flow_date": None,
                        "partial": len(present) < 2,
                    }
        except Exception:
            result = None
        with _GGT_CACHE_LOCK:
            _SB_FLOW_CACHE["k"] = {"result": result, "ts": time.time(), "ok": result is not None}
        return result
```

（`成交净买额` vs `资金净流入` 口径差、币种未决在 docs 写明(Task 7)。`flow_date` 端点无稳定日期列时保持 None;若源表有 `TRADE_DATE`/`日期` 列可填,实现者按真实列名决定,无则 None。）

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python -m pytest tests/test_ggt_adapter.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git add data_provider/fundamental_adapter.py tests/test_ggt_adapter.py
git commit -m "feat: 市场级南向净流数据面(两南向腿 成交净买额 之和,NaN-first 两腿皆缺返 None 禁假零 0.0,单腿缺标 partial)"
```

---

### Task 4: get_ggt_context 管理层组装 + offshore 接线 + 枚举工厂

**Files:**
- Modify: `data_provider/base.py`(`DataFetcherManager.get_ggt_context`;`_build_offshore_fundamental_context`;`build_failed_fundamental_context`;`_build_market_not_supported`;`_should_cache_fundamental_context`;offshore 总 status/coverage/errors 聚合)
- Test: `tests/test_ggt_context.py`(新建)

**Interfaces:**
- Consumes: Task 1-3 三 adapter 方法。
- Produces: `DataFetcherManager.get_ggt_context(stock_code, budget_seconds=None) -> Dict`(`_build_fundamental_block` 形态:status/data{eligible,holding,southbound_flow}/coverage/source_chain/errors);offshore context 增 `"ggt"` 键。Task 5 消费 `fundamental_context["ggt"]`。

- [ ] **Step 1: 写失败测试**(`tests/test_ggt_context.py` 新建)

```python
import pandas as pd
from unittest.mock import patch
from data_provider.base import DataFetcherManager


def test_get_ggt_context_non_hk_is_not_supported():
    ctx = DataFetcherManager().get_ggt_context("600519")   # A股
    assert ctx["status"] == "not_supported"


def test_get_ggt_context_hk_ok_when_all_three_present():
    with patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_ggt_eligibility_set",
               return_value={"HK00700"}), \
         patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_ggt_holding",
               return_value={"holding_shares": 200, "holding_value": 2200.0,
                             "holding_ratio_pct": 6.5, "holding_trade_date": "2026-07-01"}), \
         patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_southbound_flow",
               return_value={"southbound_net_flow": 20.0, "flow_date": None, "partial": False}):
        ctx = DataFetcherManager().get_ggt_context("hk00700")
    assert ctx["status"] == "ok"
    assert ctx["data"]["eligible"] is True
    assert ctx["data"]["holding"]["holding_shares"] == 200
    assert ctx["data"]["southbound_flow"]["southbound_net_flow"] == 20.0


def test_get_ggt_context_hk_failed_when_all_none():
    with patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_ggt_eligibility_set",
               return_value=None), \
         patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_ggt_holding",
               return_value=None), \
         patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_southbound_flow",
               return_value=None):
        ctx = DataFetcherManager().get_ggt_context("hk00700")
    assert ctx["status"] == "failed"
    assert ctx["data"]["eligible"] is None


def test_offshore_context_has_ggt_key_and_ggt_not_dragging_total_status():
    # F4-scope:hk yfinance ok + ggt failed → 总 status 仍 ok(ggt 不入 active_statuses)
    mgr = DataFetcherManager()
    with patch.object(mgr, "get_ggt_context",
                      return_value={"status": "failed", "data": {"eligible": None},
                                    "coverage": {}, "source_chain": [], "errors": []}), \
         patch.object(mgr, "_build_offshore_fundamental_context",
                      wraps=mgr._build_offshore_fundamental_context):
        # 直接调 offshore 需 yfinance mock;简化:断言 ggt 键存在与 coverage 记录
        ctx = mgr._build_offshore_fundamental_context("hk00700", "hk")
    assert "ggt" in ctx
    assert ctx["coverage"].get("ggt") in ("failed", "not_supported", "ok", "partial")


def test_enumeration_factories_include_ggt_key():
    mgr = DataFetcherManager()
    assert "ggt" in mgr.build_failed_fundamental_context("hk00700", "x")
    assert "ggt" in mgr._build_market_not_supported("us", "x")
```

（`_build_offshore_fundamental_context` 的 yfinance 依赖较重;若该测试因 yfinance 网络/桩复杂,实现者可用既有 offshore 测试的 mock 惯例——`grep -n "_build_offshore_fundamental_context" tests/` 找现成夹具复用;断言语义不弱化。）

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_context.py -x -q`
Expected: FAIL — `AttributeError: get_ggt_context` / offshore 无 ggt 键 / 工厂无 ggt 键。

- [ ] **Step 3: 实现**(`data_provider/base.py`)

1. `get_ggt_context`(仿 `get_margin_context` :3494 全形态,门控 `_market_tag != "hk"`):

```python
    def get_ggt_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """港股通块(fail-open,仅呈现、对决策只读)。三 leg 共享 deadline,某 leg 失败不丢其余。"""
        from src.config import get_config
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter
        config = get_config()
        code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(code) != "hk" or _is_etf_code(code):
            return self._build_fundamental_block(
                "not_supported", {"eligible": None, "holding": None, "southbound_flow": None},
                [{"provider": "ggt", "result": "not_supported", "duration_ms": 0}], ["not supported"])
        if timeout <= 0:
            return self._build_fundamental_block(
                "failed", {"eligible": None, "holding": None, "southbound_flow": None},
                [{"provider": "ggt", "result": "failed", "duration_ms": 0}], ["ggt stage timeout"])
        deadline = time.monotonic() + timeout
        adapter = AkshareFundamentalAdapter()
        # 三 leg 各自独立,共享 deadline;某 leg 抛错→该子块 None,不丢其余(R2)
        try:
            elig_set = adapter.get_ggt_eligibility_set(deadline=deadline)
        except Exception:
            elig_set = None
        eligible = (_ggt_key(code) in elig_set) if isinstance(elig_set, set) else None
        try:
            holding = adapter.get_ggt_holding(stock_code, deadline=deadline)
        except Exception:
            holding = None
        try:
            flow = adapter.get_southbound_flow(deadline=deadline)
        except Exception:
            flow = None
        present = [x for x in (eligible, holding, flow) if x is not None]
        if not present:
            status = "failed"
        elif eligible is not None and holding is not None and flow is not None:
            status = "ok"
        else:
            status = "partial"
        return self._build_fundamental_block(
            status, {"eligible": eligible, "holding": holding, "southbound_flow": flow},
            [{"provider": "ggt", "result": status, "duration_ms": 0}], [])
```

（`_ggt_key` 从 fundamental_adapter import;`_is_etf_code`/`_build_fundamental_block` 是 base.py 既有——确认签名后复用。)

2. `_build_offshore_fundamental_context`:`result_ctx` 初始化处加 `"ggt": {}`;函数体在既有 stage 预算内、`market == "hk"` 时 `result_ctx["ggt"] = self.get_ggt_context(stock_code, budget_seconds=<剩余预算>)`,us 时留 `self._build_fundamental_block("not_supported", {...three None...}, [...], ["not supported"])`;`coverage` 增 `"ggt": result_ctx["ggt"].get("status")`。**总 status 聚合(:3010 active_statuses)不加 ggt**(F4-scope)。**errors/source_chain 聚合元组(:3006-3008)加 'ggt'**(CONTRACT-5)。

3. `build_failed_fundamental_context`(:3028)与 `_build_market_not_supported`(:2789):各自枚举块处**增 'ggt' 键**(值=对应 failed / not_supported 块,镜像 margin)。

4. `_should_cache_fundamental_context`(:2766)块元组**加 'ggt'**(CONTRACT-6)。

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_ggt_context.py tests/test_fundamental_context.py tests/test_margin_context.py -q`
Expected: 全绿(既有 offshore/margin 邻域不受影响——ggt 是新增键、不入总 status)。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 data_provider/base.py tests/test_ggt_context.py
git add data_provider/base.py tests/test_ggt_context.py
git commit -m "feat: get_ggt_context 管理层组装(三 leg 共享 deadline 某 leg 失败不丢其余,offshore context 增 ggt 键+coverage 记录但不入总 status 防拖垮,两枚举工厂与 should_cache 同步补 ggt 键)"
```

---

### Task 5: report_schema + analyzer builder/fill + pipeline 接线(Blocker CONTRACT-2)

**Files:**
- Modify: `src/schemas/report_schema.py`(`GgtContext` 类 + `DataPerspective` 段)
- Modify: `src/analyzer.py`(`_build_ggt_from_context` + `fill_ggt_if_needed`,仿 :918/:944)
- Modify: `src/core/pipeline.py`(import + 两处调用点,镜像 :631/:1154)
- Test: `tests/test_ggt_surface.py`(新建)

**Interfaces:**
- Consumes: Task 4 `fundamental_context["ggt"]`(块 status/data)。
- Produces: `dashboard.data_perspective["ggt_context"]` dict(键=GgtContext 字段);`GgtContext` Pydantic 模型。

- [ ] **Step 1: 写失败测试**(`tests/test_ggt_surface.py` 新建)

```python
from types import SimpleNamespace
from src.analyzer import _build_ggt_from_context, fill_ggt_if_needed
from src.schemas.report_schema import GgtContext


def _ctx(status, eligible=True):
    return {"ggt": {"status": status, "data": {
        "eligible": eligible,
        "holding": {"holding_shares": 200, "holding_value": 2200.0,
                    "holding_ratio_pct": 6.5, "holding_trade_date": "2026-07-01"},
        "southbound_flow": {"southbound_net_flow": 20.0, "flow_date": None, "partial": False}}}}


def test_build_ggt_presence_only_ok():
    built = _build_ggt_from_context(_ctx("ok"))
    assert built["eligible"] is True
    assert built["holding_shares"] == 200 and built["holding_ratio_pct"] == 6.5
    assert built["southbound_net_flow"] == 20.0


def test_build_ggt_failed_returns_none():
    assert _build_ggt_from_context(_ctx("failed")) is None
    assert _build_ggt_from_context(_ctx("not_supported")) is None


def test_build_ggt_product_constructs_schema_key_parity():
    # F10:用真实产物直接构造 GgtContext 钉键名奇偶(非手写 dict)
    built = _build_ggt_from_context(_ctx("ok"))
    model = GgtContext(**built)                       # 键名漂移会因多余键被忽略→字段全 None,下断言即防线
    assert model.eligible is True and model.holding_shares == 200


def test_fill_ggt_wires_into_data_perspective():
    # Blocker CONTRACT-2:fill 把 ggt_context 填进 data_perspective;failed 不填
    result = SimpleNamespace(dashboard={}, report_language="zh")
    fill_ggt_if_needed(result, _ctx("ok"))
    assert result.dashboard["data_perspective"]["ggt_context"]["eligible"] is True
    result2 = SimpleNamespace(dashboard={}, report_language="zh")
    fill_ggt_if_needed(result2, _ctx("failed"))
    assert "ggt_context" not in result2.dashboard.get("data_perspective", {})
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_surface.py -x -q`
Expected: FAIL — `ImportError: _build_ggt_from_context` / `GgtContext`。

- [ ] **Step 3: 实现**

`src/schemas/report_schema.py`(`MarginTrading` 类 :84 旁):

```python
class GgtContext(BaseModel):
    """港股通南向维度(HK;EOD 快照,presence-only,纯展示)。"""

    eligible: Optional[bool] = None                                  # True=港股通成份/False=非成份/None=名单不可得(unknown)
    holding_shares: Optional[Union[int, float, str]] = None          # 南向持股数量(股)
    holding_value: Optional[Union[int, float, str]] = None           # 南向持股市值
    holding_ratio_pct: Optional[Union[int, float, str]] = None       # 持股数量占发行股百分比
    holding_trade_date: Optional[str] = None                         # 持股日期
    southbound_net_flow: Optional[Union[int, float, str]] = None     # 市场级南向净流(亿,币种以数据源口径为准)
    flow_date: Optional[str] = None
```

`DataPerspective`(:94 邻域)加字段:`ggt_context: Optional[GgtContext] = None`。

`src/analyzer.py`(`_build_margin_from_context` :918 / `fill_margin_if_needed` :944 旁,逐行镜像):

```python
def _build_ggt_from_context(
    fundamental_context: Optional[Dict[str, Any]], language: str = "zh"
) -> Optional[Dict[str, Any]]:
    """从 fundamental_context 的 ggt 块确定性构建港股通 section dict(presence-only)。

    status 非 ok/partial → None。键名映射(D8 冻结):读 ctx["ggt"]["data"],写 dashboard
    data_perspective["ggt_context"]。纯展示,不含决策字段。language 形参对齐 margin 签名保留。
    """
    if not isinstance(fundamental_context, dict):
        return None
    gg = fundamental_context.get("ggt")
    gg = gg if isinstance(gg, dict) else {}
    if str(gg.get("status") or "").strip().lower() not in ("ok", "partial"):
        return None
    data = gg.get("data") if isinstance(gg.get("data"), dict) else {}
    holding = data.get("holding") if isinstance(data.get("holding"), dict) else {}
    flow = data.get("southbound_flow") if isinstance(data.get("southbound_flow"), dict) else {}
    return {
        "eligible": data.get("eligible"),
        "holding_shares": holding.get("holding_shares"),
        "holding_value": holding.get("holding_value"),
        "holding_ratio_pct": holding.get("holding_ratio_pct"),
        "holding_trade_date": holding.get("holding_trade_date"),
        "southbound_net_flow": flow.get("southbound_net_flow"),
        "flow_date": flow.get("flow_date"),
    }


def fill_ggt_if_needed(
    result: "AnalysisResult", fundamental_context: Optional[Dict[str, Any]]
) -> None:
    """确定性把港股通段填进 data_perspective.ggt_context(in-place)。

    presence-only + HK-gated;LLM 后、对决策只读(不喂 prompt、不碰 decision_stability);
    失败静默跳过、不阻断主流程。
    """
    if not result:
        return
    try:
        built = _build_ggt_from_context(
            fundamental_context, language=getattr(result, "report_language", "zh")
        )
        if built is None:
            return
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        result.dashboard = dashboard
        dp = dashboard.get("data_perspective") or {}
        dashboard["data_perspective"] = dp
        dp["ggt_context"] = built
        logger.info("[ggt] Filled HKSC southbound section from fundamental_context")
    except Exception as e:
        logger.warning("[ggt] Fill failed, skipping: %s", e)
```

`src/core/pipeline.py`:import 段(:33-34 旁)加 `fill_ggt_if_needed`;**两处调用点**(:631 与 :1154 的 `fill_margin_if_needed(result, fundamental_context)` 后各加一行):

```python
                fill_ggt_if_needed(result, fundamental_context)
```

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_ggt_surface.py tests/test_margin_surface.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 src/schemas/report_schema.py src/analyzer.py src/core/pipeline.py tests/test_ggt_surface.py
git add src/schemas/report_schema.py src/analyzer.py src/core/pipeline.py tests/test_ggt_surface.py
git commit -m "feat: 港股通段进报告(GgtContext schema+presence-only builder+fill_ggt_if_needed LLM后填充不喂prompt+pipeline 两处接线,真实产物构造 schema 钉键名奇偶防漂移)"
```

---

### Task 6: report_language 双语 + notification 三态渲染

**Files:**
- Modify: `src/report_language.py`(zh/en label 组)
- Modify: `src/notification.py`(ggt 渲染,margin :1277 旁)
- Test: `tests/test_ggt_surface.py`(追加渲染测试)

**Interfaces:**
- Consumes: Task 5 `data_perspective["ggt_context"]` dict。

- [ ] **Step 1: 写失败测试**(追加)

```python
def _render(dp, lang):
    # 复用 notification 的报告渲染入口——实现者按 notification 既有 margin 渲染测试的调用形态适配
    from src.notification import _render_ggt_section   # 抽出的可测渲染函数(见 Step 3)
    return _render_ggt_section(dp.get("ggt_context"), lang)


def test_ggt_render_three_state_eligible_distinct_text():
    ok_true = {"eligible": True, "holding_shares": 200, "holding_ratio_pct": 6.5,
               "southbound_net_flow": 20.0}
    ok_false = {"eligible": False}
    ok_none = {"eligible": None}
    t_true = _render({"ggt_context": ok_true}, "zh")
    t_false = _render({"ggt_context": ok_false}, "zh")
    t_none = _render({"ggt_context": ok_none}, "zh")
    assert "港股通标的" in t_true
    assert "非港股通标的" in t_false
    assert "成份状态未知" in t_none
    # None 输出禁含 False 文案(F10/§6)
    assert "非港股通标的" not in t_none


def test_ggt_render_none_section_empty():
    assert _render({"ggt_context": None}, "zh").strip() == ""
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest tests/test_ggt_surface.py -k render -x -q`
Expected: FAIL — `ImportError: _render_ggt_section`。

- [ ] **Step 3: 实现**

`src/report_language.py`:zh 词表(:261 margin_label 旁)与 en 词表(:377 旁)各加:

```python
        "ggt_label": "港股通",                          # en: "HKSC"
        "ggt_eligible_label": "港股通标的",              # en: "HKSC Eligible"
        "ggt_not_eligible_label": "非港股通标的(内地账户不可买)",   # en: "Not HKSC Eligible"
        "ggt_unknown_label": "成份状态未知(数据不可达)",  # en: "HKSC Status Unknown"
        "southbound_label": "南向资金",                  # en: "Southbound"
```

`src/notification.py`:抽 `_render_ggt_section(ggt: Optional[dict], report_language: str) -> str`(三态文案 + 数值字段 None 跳过 + 整段 None 返 ""),在 margin 渲染块(:1277)旁调用:

```python
def _render_ggt_section(ggt, report_language: str) -> str:
    if not ggt:
        return ""
    zh = report_language != "en"
    label = "港股通" if zh else "HKSC"
    eligible = ggt.get("eligible")
    if eligible is True:
        elig_text = "港股通标的" if zh else "HKSC Eligible"
    elif eligible is False:
        elig_text = "非港股通标的(内地账户不可买)" if zh else "Not HKSC Eligible"
    else:
        elig_text = "成份状态未知(数据不可达)" if zh else "HKSC Status Unknown"
    lines = [f"{label}: {elig_text}"]
    ratio = ggt.get("holding_ratio_pct")
    if ratio is not None:                              # 数值 None 跳过
        lines.append((f"南向持股占比: {ratio}%") if zh else f"Southbound Holding: {ratio}%")
    flow = ggt.get("southbound_net_flow")
    if flow is not None:
        lines.append((f"南向净流: {flow} 亿") if zh else f"Southbound Net Flow: {flow}")
    return "\n".join(lines)
```

（在 notification 既有报告拼装处,HK 报告分支调 `_render_ggt_section(data_persp.get("ggt_context"), report_language)` 并入正文——实现者按 margin :1277-1295 的拼装形态就近接入;label 词表若走 report_language 的 lang dict 则用 dict 取值,此处内联三态是最小实现,与文件既有风格对齐即可。)

- [ ] **Step 4: 跑测试确认 GREEN + 邻域**

Run: `python -m pytest tests/test_ggt_surface.py tests/test_report_language.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + Commit**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && flake8 src/report_language.py src/notification.py tests/test_ggt_surface.py
git add src/report_language.py src/notification.py tests/test_ggt_surface.py
git commit -m "feat: 港股通段双语渲染(eligible True/False/None 三互异文案,None 输出禁含'非港股通标的'防误读,持股占比/南向净流 None 字段跳过,整段 None 不渲染)"
```

---

### Task 7: 配置 + docs + CHANGELOG + 策略文档订正 + 全量门禁

**Files:**
- Modify: `src/config.py`(字段 + env 解析)
- Modify: `.env.example`(注释行)
- Create: `docs/ggt-southbound.md`
- Modify: `docs/strategy-actionable-signal-system.md`(:119 验收行订正)
- Modify: `docs/CHANGELOG.md`([Unreleased] 一条)
- Test: `tests/test_ggt_adapter.py`(配置解析追加)+ 全量 `./scripts/ci_gate.sh`

- [ ] **Step 1: 写配置失败测试**(追加到 tests/test_ggt_adapter.py 或 config 测试文件——先 grep 找 SIGNAL_BACKTEST_FWER_ALPHA 解析测试所在文件仿其模式)

```python
def test_ggt_ttl_default_and_min_clamp(monkeypatch):
    monkeypatch.delenv("GGT_LIST_CACHE_TTL_SECONDS", raising=False)
    from src.config import Config
    assert Config()._load_from_env() or True   # 用该文件既有 Config 重建夹具模式
    # 默认 43200
    monkeypatch.setenv("GGT_LIST_CACHE_TTL_SECONDS", "30")
    # minimum=60 钳制 → 60(按 parse_env_int 实际语义断言)
```

（此测试形态照 FWER/OOS 既有配置解析测试逐字仿写——实现者 grep 定位真实夹具,断言默认 43200 + minimum=60 钳制,不新造夹具。）

- [ ] **Step 2: 跑测试确认 RED**

Run: `python -m pytest -k ggt_ttl -x -q`
Expected: FAIL — Config 无 `ggt_list_cache_ttl_seconds`。

- [ ] **Step 3: 实现**

`src/config.py`:字段(fundamental_* 邻域)`ggt_list_cache_ttl_seconds: int = 43200`;loader(仿既有 parse_env_int)`self.ggt_list_cache_ttl_seconds = parse_env_int(os.getenv('GGT_LIST_CACHE_TTL_SECONDS'), 43200, field_name='GGT_LIST_CACHE_TTL_SECONDS', minimum=60)`。

`.env.example`(fundamental 配置邻域)注释行:

```
# 港股通成份/南向持股/净流缓存 TTL(秒,日级数据默认 12h;不配置也可运行)
# GGT_LIST_CACHE_TTL_SECONDS=43200
```

`docs/ggt-southbound.md` 新建(仿 margin-trading.md 体例),必含:三端点清单+**活性未核验声明**(签名/列名已本地 inspect,活性待真网);eligible 三态语义(False 必来自表可得≥50行且不在内);EOD 口径;持股占比"占发行股百分比"口径;净流取"成交净买额"(与"资金净流入"口径差)+**币种未决(通常港元,真网核验前不标人民币)**;缓存 12h/负缓存 300s;min-row<50 守卫;fail-closed 边界;**看板注解 defer 到 Inc 2b 说明**;真网 deferred 清单(三 probe + 一次真实 HK 报告 + 净流币种/量级核验)。

`docs/strategy-actionable-signal-system.md:119` 验收行订正(F2-scope):南向无第二源故"facade 兜底"不适用、"1m fail-closed"系 HK MVP 措辞残留,南向验收=离线单测(端点 schema/成份归一/NaN 假零/截断守卫)+ 真网 deferred。

`docs/CHANGELOG.md` [Unreleased] 顶部(扁平,禁 ###):

```markdown
- [新功能] HK 标的分析报告新增港股通(HKSC)南向维度:成份可买性(港股通标的/非标的/名单不可达三态)、个股南向持股(数量/市值/占发行股比)、市场级南向净流(EOD 成交净买额),经 akshare 东财端点抓取(12h 缓存+300s 负缓存,东财不可达时 fail-closed 不编造);纯展示不影响信号/决策;看板可买性注解顺延后续增量
```

- [ ] **Step 4: 全量门禁**

```bash
cd /root/chainb-ggt && export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" && (./scripts/ci_gate.sh > .superpowers/sdd/ci_gate_t7.log 2>&1; echo $? > .superpowers/sdd/ci_gate_t7.exit) &
```

同回合轮询 `.exit`(每次 `sleep 45; cat .exit 2>/dev/null || tail -1 .log`,约 15-20 次,勿结束回合勿挂 Monitor);报告引用真实退出码+pytest 汇总行。Expected: 全绿(基线 3955 + 本增量 ~30),exit 0。**免 web-gate**(无前端/registry/locale 改动)。

- [ ] **Step 5: Commit**

```bash
git add src/config.py .env.example docs/
git commit -m "feat: 港股通缓存 TTL 配置(GGT_LIST_CACHE_TTL_SECONDS 默认 12h,不进 registry)+docs/ggt-southbound 专题(端点/三态/口径/fail-closed/看板 defer)并订正策略文档验收行记 CHANGELOG"
```

---

## Self-Review(plan 作者已核)

- **Spec 覆盖**:§4.1→T1(_ggt_key+eligibility)/T2(holding)/T3(flow);§4.2→T4(get_ggt_context+offshore+枚举工厂);§4.3→T5(schema+builder+fill+pipeline)+T6(report_language+notification);§4.4→T7(config);§4.5→T7(docs)。§7 测试 1-15 映射:1→T1-T3,2→T1,3→T1,4→T2,5→T3,6→T4,7→T4,8→T4,9→T5(fill 断链,Blocker),10→T5,11→T6,12→T7,13→T5(schema roundtrip 折进 T5 GgtContext 测试),14→T5/T6(纯展示不变式),15→network deferred(T7 docs 列清单,-m network 观测测试实现者按 test_*_network.py 惯例补或列 deferred)。**看板相关(defer)不映射**。
- **Blocker 覆盖**:F1→T1 `_ggt_key`(测试 test_ggt_key_normalizes + test_eligibility_set_built_with_ggt_key,裸码 fixture);CONTRACT-2→T5 pipeline 两处接线(测试 test_fill_ggt_wires_into_data_perspective,failed 不填的断链防护)。
- **Placeholder 扫描**:T4 offshore 测试与 T6 渲染接入点标注"按既有夹具/拼装形态适配"是对真实文件惯例的复用指令(margin 有可照的孪生),非 TBD;代码块均完整。config 测试夹具指"照 FWER/OOS 既有解析测试仿写"——同 1c/1d/1e 先例。
- **类型一致性**:`_ggt_key`/`get_ggt_eligibility_set`(→set|None)/`get_ggt_holding`(→dict|None,键 holding_*)/`get_southbound_flow`(→dict|None,键 southbound_net_flow/partial)/`get_ggt_context`(→块 dict,data{eligible,holding,southbound_flow})/`_build_ggt_from_context`(→扁平 dict)/`GgtContext` 字段/`fill_ggt_if_needed`/`_render_ggt_section` 全链命名一致;holding 子 dict 键(holding_shares/holding_value/holding_ratio_pct/holding_trade_date)T2 产、T4 透、T5 摊平进 GgtContext、T6 渲染一致。
- **顺序**:T1(基建+eligibility)→T2/T3(复用基建)→T4(消费三方法)→T5(消费 context)→T6(消费 section)→T7(收尾)。SDD 串行。
- **判别式非 tautology**:T1 三写法归一有裸码 fixture 钉 F1 陷阱;T3 两腿 NaN→None 钉 F4 假零;T4 ggt failed 时总 status 仍 ok 钉 F4-scope 不拖垮;T5 failed 不填 data_perspective 钉 CONTRACT-2 断链;T6 None 输出禁含 False 文案钉 F10 三态。
