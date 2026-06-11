# Binance fapi 衍生品备援 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 衍生品抓取从 OKX 单源升级为 OKX 主 / Binance fapi 整源兜底（实时 5 路指标 + 回测资金费历史），`source` 如实标注并动态进分析 prompt。

**Architecture:** 新建纯抓取模块 `data_provider/binance_derivatives.py`（输出契约与 OKX 同形，OI 仅 USD 口径，裸包络解析）；`crypto_derivatives.py` 两个公开函数在"OKX 整组全空"时延迟 import 降级；`src/analyzer.py` 合约块标题动态读 `source`（对抗审查 blocker 修复）；config/.env/docs 同步。正常路径（OKX 可达）行为不变。

**Tech Stack:** Python 3（requests、ThreadPoolExecutor、pytest+monkeypatch，全离线——本环境 Binance 451 在线不可验）。

**对应 spec：** `docs/superpowers/specs/2026-06-11-binance-fapi-derivatives-fallback-design.md`（已按 27→13 对抗审查收敛）。

**分支：** 执行开始时由 controller 建 `feat/binance-fapi-derivatives-fallback`（携带 spec/plan 提交）并把 `feat/backtest-short-surface` 恢复到其交付 tip `beb13a7`——与前序子项目同法。

---

## 关键约定（所有任务通用）

- 仓库根 `/root/AI/WorkSpace/cursor/AI _Trading_System`（**路径含空格，shell 命令必须加引号**）；测试用 `.venv/bin/python -m pytest ...`（cwd=仓库根）；全量门禁 `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`。
- commit：英文类型前缀 + 中文描述，无 Co-Authored-By，无工具前缀；只本地提交，绝不 push。
- 锁套件（字节级不变）：`tests/test_backtest_engine.py`、`tests/test_crypto_backtest.py`、`tests/test_backtest_summary.py`。
- **monkeypatch 落点**：binance 模块经 `from ... import` 绑定 helpers，`cd._http_get_json` 与 `bd._http_get_json` 是两个独立名字——OKX 路 patch `cd.`，Binance 路 patch `bd.`，编排测试两个都 patch。

---

## Task 1: 新模块 `binance_derivatives.py`（TDD）

**Files:**
- Create: `data_provider/binance_derivatives.py`
- Test: `tests/test_binance_derivatives.py`（新建）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_binance_derivatives.py`：

```python
# -*- coding: utf-8 -*-
"""Binance fapi 衍生品备援模块单测（全离线，monkeypatch bd._http_get_json）。"""
import data_provider.binance_derivatives as bd


def _fake_fapi(url, params=None, headers=None):
    if "premiumIndex" in url:
        return {"symbol": "BTCUSDT", "markPrice": "62000.0", "lastFundingRate": "0.0001"}
    if "/fapi/v1/openInterest" in url:
        return {"symbol": "BTCUSDT", "openInterest": "10.5"}
    if "topLongShortAccountRatio" in url:
        return [{"longShortRatio": "0.70"}, {"longShortRatio": "0.80"}]   # 升序，最新在末
    if "globalLongShortAccountRatio" in url:
        return [{"longShortRatio": "1.10"}, {"longShortRatio": "1.20"}]
    return {}


def test_fetch_perp_metrics_parses_all(monkeypatch):
    monkeypatch.setattr(bd, "_http_get_json", _fake_fapi)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert abs(out["funding_rate"] - 0.0001) < 1e-12
    assert out["mark_price"] == 62000.0
    assert abs(out["open_interest_usd"] - 10.5 * 62000.0) < 1e-6   # 基础币数量×标记价
    assert "open_interest" not in out                              # 张数字段 OKX 专属，永不填
    assert out["long_short_ratio"] == 1.20                         # 升序取末元素（最新）
    assert out["long_short_ratio_top"] == 0.80
    assert out["source"] == "binance"


def test_non_linear_quote_zero_calls(monkeypatch):
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(bd, "_http_get_json", spy)
    assert bd.fetch_perp_metrics("BTC", "USD") == {}
    assert bd.fetch_perp_metrics("", "USDT") == {}
    assert bd.fetch_funding_rate_history("BTC", "USD", 0, 1) == []
    assert called["n"] == 0


def test_oi_usd_requires_both_oi_and_mark(monkeypatch):
    def no_premium(url, params=None, headers=None):
        if "/fapi/v1/openInterest" in url:
            return {"openInterest": "10.5"}
        raise RuntimeError("premiumIndex down")
    monkeypatch.setattr(bd, "_http_get_json", no_premium)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert "open_interest_usd" not in out   # 缺 markPrice 不换算
    assert "funding_rate" not in out


def test_single_route_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "topLongShortAccountRatio" in url:
            raise RuntimeError("top ls down")
        return _fake_fapi(url, params, headers)
    monkeypatch.setattr(bd, "_http_get_json", partial)
    out = bd.fetch_perp_metrics("BTC", "USDT")
    assert "long_short_ratio_top" not in out
    assert out["long_short_ratio"] == 1.20
    assert out["source"] == "binance"


def test_all_fail_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bd, "_http_get_json", boom)
    assert bd.fetch_perp_metrics("BTC", "USDT") == {}


def test_ratio_structural_anomalies(monkeypatch):
    for payload in [[], [123], [{"foo": 1}], {"not": "a list"}]:
        monkeypatch.setattr(bd, "_http_get_json", lambda url, params=None, headers=None, p=payload: p if "LongShortAccountRatio" in url else {})
        out = bd.fetch_perp_metrics("BTC", "USDT")
        assert "long_short_ratio" not in out
        assert "long_short_ratio_top" not in out


def test_funding_history_half_open_window(monkeypatch):
    def fake(url, params=None, headers=None):
        assert "/fapi/v1/fundingRate" in url
        assert params["startTime"] == "1000" and params["endTime"] == "4000"
        return [
            {"fundingTime": "500", "fundingRate": "0.005"},    # < start，排除
            {"fundingTime": "1000", "fundingRate": "0.0001"},  # == start，包含
            {"fundingTime": "2000", "fundingRate": "0.0002"},
            {"fundingTime": "4000", "fundingRate": "0.0004"},  # == end，半开排除
        ]
    monkeypatch.setattr(bd, "_http_get_json", fake)
    assert bd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000) == [0.0001, 0.0002]


def test_funding_history_failures_and_anomalies(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bd, "_http_get_json", boom)
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []
    monkeypatch.setattr(bd, "_http_get_json", lambda *a, **k: {"not": "a list"})
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []
    monkeypatch.setattr(bd, "_http_get_json", lambda *a, **k: [{"fundingTime": "abc", "fundingRate": "0.1"}, 42])
    assert bd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []


def test_fapi_base_env_override(monkeypatch):
    seen = {}
    def spy(url, params=None, headers=None):
        seen["url"] = url
        raise RuntimeError("stop")
    monkeypatch.setenv("BINANCE_FAPI_BASE_URL", "https://mirror.example.com/")
    monkeypatch.setattr(bd, "_http_get_json", spy)
    bd.fetch_funding_rate_history("BTC", "USDT", 0, 1)
    assert seen["url"].startswith("https://mirror.example.com/fapi/v1/fundingRate")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_binance_derivatives.py -q`
Expected: 收集即报 `ModuleNotFoundError: No module named 'data_provider.binance_derivatives'`

- [ ] **Step 3: 实现模块** — 新建 `data_provider/binance_derivatives.py`：

```python
# -*- coding: utf-8 -*-
"""Binance fapi 衍生品备援（纯抓取，免 API Key）。

OKX 整源不可得时的兜底源（编排在 crypto_derivatives 的公开函数内）。分层纪律同
crypto_derivatives：不 import src.*、不碰 DB。注意响应**无包络**（premiumIndex/
openInterest 为裸 dict，futures/data 与 fundingRate 为裸数组），与 OKX 的
{"code":..,"data":[...]} 不同；futures/data 数组为升序（最新在末）。
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from data_provider.crypto_derivatives import _LINEAR_QUOTES, _http_get_json, _to_float

logger = logging.getLogger(__name__)

_PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
_OPEN_INTEREST_PATH = "/fapi/v1/openInterest"
_LS_GLOBAL_PATH = "/futures/data/globalLongShortAccountRatio"
_LS_TOP_PATH = "/futures/data/topLongShortAccountRatio"
_FUNDING_HISTORY_PATH = "/fapi/v1/fundingRate"
_FUNDING_HISTORY_LIMIT = 1000  # 单页上限 ≥ 窗口最大需求(3N≤360)；若返回行数==limit（截断迹象）仍用已得（fail-soft 低估）


def _fapi_base() -> str:
    """Binance fapi 基址；BINANCE_FAPI_BASE_URL 可覆盖（受限地区换镜像），默认官方域名。"""
    return (os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")


def _get_dict(path: str, params: dict) -> dict:
    """GET 裸 dict 端点（premiumIndex/openInterest）；失败/非 dict → {}（fail-soft）。"""
    try:
        data = _http_get_json(_fapi_base() + path, params)
    except Exception as e:
        logger.warning("[合约指标:binance] %s 抓取失败: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _get_latest_ratio(path: str, symbol: str) -> Optional[float]:
    """GET futures/data 多空比端点（裸数组升序，最新在末）；取末行 longShortRatio；失败/空/结构异常 → None。"""
    try:
        data = _http_get_json(_fapi_base() + path, {"symbol": symbol, "period": "5m", "limit": "1"})
    except Exception as e:
        logger.warning("[合约指标:binance] %s 抓取失败: %s", path, e)
        return None
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return _to_float(data[-1].get("longShortRatio"))
    return None


def fetch_perp_metrics(base: str, quote: str) -> dict:
    """BASE/QUOTE → Binance fapi BASEQUOTE 永续；4 路并发；presence-only；失败/不支持 → {}。
    OI 仅 USD 口径（openInterest×markPrice，基础币数量换算），永不填张数 open_interest（OKX 专属语义）。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return {}
    symbol = f"{base}{quote}"
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_pi = ex.submit(_get_dict, _PREMIUM_INDEX_PATH, {"symbol": symbol})
        f_oi = ex.submit(_get_dict, _OPEN_INTEREST_PATH, {"symbol": symbol})
        f_ls = ex.submit(_get_latest_ratio, _LS_GLOBAL_PATH, symbol)
        f_lst = ex.submit(_get_latest_ratio, _LS_TOP_PATH, symbol)
        pi, oi_raw = f_pi.result(), f_oi.result()
        ls, lst = f_ls.result(), f_lst.result()
    out: dict = {}
    fr = _to_float(pi.get("lastFundingRate"))
    if fr is not None:
        out["funding_rate"] = fr
    mp = _to_float(pi.get("markPrice"))
    if mp is not None:
        out["mark_price"] = mp
    oi = _to_float(oi_raw.get("openInterest"))
    if oi is not None and mp is not None:
        out["open_interest_usd"] = oi * mp
    if ls is not None:
        out["long_short_ratio"] = ls
    if lst is not None:
        out["long_short_ratio_top"] = lst
    if out:
        out["source"] = "binance"
    return out


def fetch_funding_rate_history(base: str, quote: str, start_ms: int, end_ms: int) -> list:
    """Binance fapi 资金费历史，半开窗口 [start_ms, end_ms)。服务端 startTime/endTime 过滤 +
    客户端半开兜底（endTime 包含/排除语义无论哪种，最终结果一致）；单页 limit=1000 不分页；失败 → []。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return []
    symbol = f"{base}{quote}"
    params = {
        "symbol": symbol,
        "startTime": str(int(start_ms)),
        "endTime": str(int(end_ms)),
        "limit": str(_FUNDING_HISTORY_LIMIT),
    }
    try:
        data = _http_get_json(_fapi_base() + _FUNDING_HISTORY_PATH, params)
    except Exception as e:
        logger.warning("[资金费历史:binance] %s 抓取失败: %s", symbol, e)
        return []
    if not isinstance(data, list):
        return []
    rates: list = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = _to_float(item.get("fundingTime"))
        fr = _to_float(item.get("fundingRate"))
        if ts is None or fr is None:
            continue
        if start_ms <= ts < end_ms:  # 半开 [start, end)：与 OKX 路完全一致
            rates.append(fr)
    return rates
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_binance_derivatives.py -q`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add data_provider/binance_derivatives.py tests/test_binance_derivatives.py
git commit -m "feat: 新增 Binance fapi 衍生品抓取模块（裸包络解析/OI 仅 USD 口径/升序取末/半开窗口）"
```

---

## Task 2: 整源降级编排（crypto_derivatives.py）+ 2 个既有用例最小补丁

**Files:**
- Modify: `data_provider/crypto_derivatives.py`（两函数尾部 + 2 处复用注释）
- Test: `tests/test_crypto_derivatives_fetch.py`（新增 4 用例 + 1 既有用例补丁 + 顶部 import）
- Test: `tests/test_crypto_funding_history.py`（新增 2 用例 + 1 既有用例补丁 + 顶部 import）

- [ ] **Step 1: 写失败测试**

1a. `tests/test_crypto_derivatives_fetch.py` 顶部 `import data_provider.crypto_derivatives as cd` 之后加一行：

```python
import data_provider.binance_derivatives as bd
```

末尾追加（`_fake_binance` 为模块级助手）：

```python
def _fake_binance(url, params=None, headers=None):
    if "premiumIndex" in url:
        return {"markPrice": "62000.0", "lastFundingRate": "0.0002"}
    if "/fapi/v1/openInterest" in url:
        return {"openInterest": "10.0"}
    if "LongShortAccountRatio" in url:
        return [{"longShortRatio": "1.50"}]
    return {}


def test_fallback_to_binance_when_okx_all_empty(monkeypatch):
    def okx_boom(*a, **k):
        raise RuntimeError("okx down")
    monkeypatch.setattr(cd, "_http_get_json", okx_boom)
    monkeypatch.setattr(bd, "_http_get_json", _fake_binance)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert out["source"] == "binance"
    assert out["funding_rate"] == 0.0002
    assert abs(out["open_interest_usd"] - 620000.0) < 1e-6
    assert "open_interest" not in out


def test_no_binance_call_when_okx_partial_success(monkeypatch):
    bd_calls = {"n": 0}
    def bd_spy(*a, **k):
        bd_calls["n"] += 1
        return {}
    def okx_partial(url, params=None, headers=None):
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        raise RuntimeError("others down")
    monkeypatch.setattr(cd, "_http_get_json", okx_partial)
    monkeypatch.setattr(bd, "_http_get_json", bd_spy)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert out["source"] == "okx"          # OKX 部分成功即用 OKX
    assert out["funding_rate"] == 0.0001
    assert bd_calls["n"] == 0              # 整源降级：部分成功不触 Binance


def test_4xx_shaped_failure_falls_back_to_binance(monkeypatch):
    import requests
    cd_calls = {"n": 0}
    def okx_451(*a, **k):
        cd_calls["n"] += 1
        resp = requests.Response()
        resp.status_code = 451
        raise requests.HTTPError(response=resp)
    monkeypatch.setattr(cd, "_http_get_json", okx_451)
    monkeypatch.setattr(bd, "_http_get_json", _fake_binance)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert out["source"] == "binance"
    assert cd_calls["n"] == 5              # OKX 5 路各一次（每路单调用，无放大）


def test_non_linear_quote_no_calls_to_either_source(monkeypatch):
    calls = {"n": 0}
    def spy(*a, **k):
        calls["n"] += 1
        return {}
    monkeypatch.setattr(cd, "_http_get_json", spy)
    monkeypatch.setattr(bd, "_http_get_json", spy)
    assert cd.fetch_perp_metrics("BTC", "USD") == {}
    assert calls["n"] == 0                 # guard 在降级分支前，两源都零调用
```

1b. **既有用例最小补丁（spec §5 指定，恰 2 处）**——`tests/test_crypto_derivatives_fetch.py` 的 `test_fetch_perp_metrics_all_fail_returns_empty` 在 `monkeypatch.setattr(cd, "_http_get_json", boom)` 之后加一行（语义升级为"双源全挂"，断言不变）：

```python
    monkeypatch.setattr(bd, "_http_get_json", boom)  # 双源全挂：防降级分支击穿到真实网络
```

1c. `tests/test_crypto_funding_history.py` 顶部 `import data_provider.crypto_derivatives as cd` 之后加：

```python
import data_provider.binance_derivatives as bd
```

`test_exception_fails_soft_to_empty` 在 `monkeypatch.setattr(cd, "_http_get_json", boom)` 之后加一行：

```python
    monkeypatch.setattr(bd, "_http_get_json", boom)  # 双源全挂：防降级分支击穿到真实网络
```

末尾追加 2 个降级用例：

```python
def test_history_falls_back_to_binance_when_okx_empty(monkeypatch):
    def okx_boom(*a, **k):
        raise RuntimeError("okx down")
    def fake_binance(url, params=None, headers=None):
        assert "/fapi/v1/fundingRate" in url
        return [
            {"fundingTime": "1000", "fundingRate": "0.0001"},
            {"fundingTime": "2000", "fundingRate": "0.0002"},
            {"fundingTime": "4000", "fundingRate": "0.0004"},  # == end_ms，半开排除
        ]
    monkeypatch.setattr(cd, "_http_get_json", okx_boom)
    monkeypatch.setattr(bd, "_http_get_json", fake_binance)
    assert cd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000) == [0.0001, 0.0002]


def test_history_no_binance_call_when_okx_non_empty(monkeypatch):
    bd_calls = {"n": 0}
    def bd_spy(*a, **k):
        bd_calls["n"] += 1
        return []
    fake, _calls = _page_fake([(2000, 0.02)])
    monkeypatch.setattr(cd, "_http_get_json", fake)
    monkeypatch.setattr(bd, "_http_get_json", bd_spy)
    assert cd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000) == [0.02]
    assert bd_calls["n"] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py::test_fallback_to_binance_when_okx_all_empty tests/test_crypto_funding_history.py::test_history_falls_back_to_binance_when_okx_empty -q`
Expected: FAIL（降级分支尚不存在，OKX 全挂返回 `{}`/`[]`）

- [ ] **Step 3: 实现降级分支（`data_provider/crypto_derivatives.py`）**

3a. `fetch_perp_metrics` 尾部，把现有：

```python
    if out:
        out["source"] = "okx"
    return out
```

替换为（**插入点精确化：降级分支在 source 赋值之前、与其同缩进层级**——spec §3 对抗审查修正）：

```python
    if not out:  # OKX 整组全空（含全部失败）→ 整源降级 Binance（fapi）
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环（bd 顶层 import 本模块 helpers）
        return bd.fetch_perp_metrics(base, quote)            # 空时返回 {}，presence-only 契约保持
    out["source"] = "okx"
    return out
```

3b. `fetch_funding_rate_history` 尾部，把循环后的 `    return rates` 替换为：

```python
    if not rates:  # OKX 窗口内无数据/抓取失败 → 整源降级 Binance 同窗口
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环
        return bd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
    return rates
```

3c. 复用边界注释（spec §2 约定）：`_LINEAR_QUOTES` 行尾注释扩为 `# OKX 线性永续计价（供 binance_derivatives 复用）`；`_http_get_json` docstring 末尾追加 `供 binance_derivatives 复用。`（`_to_float` 同理可不加——三处太碎，按 `_LINEAR_QUOTES` + `_http_get_json` 两处即可）。

- [ ] **Step 4: 运行确认通过（两文件全量，含既有用例）**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py tests/test_crypto_funding_history.py tests/test_binance_derivatives.py tests/test_crypto_perp_snapshot.py -q`
Expected: 全绿（fetch 文件 8+4=12、history 文件 5+2=7、binance 9、snapshot 9 不受影响）

- [ ] **Step 5: 提交**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_derivatives_fetch.py tests/test_crypto_funding_history.py
git commit -m "feat: 衍生品抓取接入 Binance 整源兜底（OKX 全空才降级，延迟 import；既有双源全挂用例补 bd 补丁）"
```

---

## Task 3: analyzer 合约块标题动态来源（对抗审查 blocker 修复）

**Files:**
- Modify: `src/analyzer.py`（合约指标块，~line 3024-3031）
- Test: `tests/test_crypto_derivatives_prompt.py`

- [ ] **Step 1: 写失败测试** — `tests/test_crypto_derivatives_prompt.py` 末尾追加：

```python
def test_prompt_contract_source_dynamic_binance():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "source": "binance"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "来源 BINANCE" in prompt
    assert "来源 OKX" not in prompt


def test_prompt_contract_source_defaults_to_okx():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001}   # 无 source（防御：理论上抓取层总会带）
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "来源 OKX" in prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py::test_prompt_contract_source_dynamic_binance -q`
Expected: FAIL（标题硬编码"来源 OKX"）

- [ ] **Step 3: 实现** — `src/analyzer.py` 合约块的：

```python
            if rows:
                rows_text = "\n".join(rows)
                prompt += f"""
### 合约市场指标（永续，来源 OKX）
```

改为：

```python
            if rows:
                rows_text = "\n".join(rows)
                src_label = (contracts.get("source") or "okx").upper()
                prompt += f"""
### 合约市场指标（永续，来源 {src_label}）
```

（块内其余行不动；f-string 本就存在，仅标题行换占位符。）

- [ ] **Step 4: 运行确认通过（含既有 6 个 prompt 用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py -q`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/analyzer.py tests/test_crypto_derivatives_prompt.py
git commit -m "fix: 合约指标 prompt 标题来源动态读 source（备援降级后不再误标 OKX）"
```

---

## Task 4: 配置注册（config.py + registry 描述 + .env.example）

**Files:**
- Modify: `src/config.py`（两处，对称 `binance_base_url`）
- Modify: `src/core/config_registry.py`（仅 `CRYPTO_DERIVATIVES_ENABLED` 描述字符串）
- Modify: `.env.example`（291 行 `BINANCE_BASE_URL` 之后）

- [ ] **Step 1: config.py dataclass 字段** — 在 `binance_base_url: str = "https://api.binance.com"`（~line 940）之后追加：

```python
    # Binance fapi（衍生品备援）Base URL：仅 OKX 整组不可得时使用；受限地区可换镜像
    binance_fapi_base_url: str = "https://fapi.binance.com"
```

- [ ] **Step 2: config.py getenv 装配** — 在 `binance_base_url=os.getenv('BINANCE_BASE_URL', 'https://api.binance.com'),`（~line 1780）之后追加：

```python
            binance_fapi_base_url=os.getenv('BINANCE_FAPI_BASE_URL', 'https://fapi.binance.com'),
```

- [ ] **Step 3: registry 描述防漂移更正** — `src/core/config_registry.py` 的 `CRYPTO_DERIVATIVES_ENABLED` 条目（~line 3887），把：

```
"description": "Enable injecting matching perpetual funding rate, mark price, and open interest into the crypto spot analysis prompt (sourced from OKX public APIs).",
```

改为：

```
"description": "Enable injecting matching perpetual funding rate, mark price, and open interest into the crypto spot analysis prompt (OKX primary source; falls back to Binance fapi whole-source when OKX yields nothing).",
```

- [ ] **Step 4: .env.example** — 在 `# BINANCE_BASE_URL=...`（line 291）之后追加一行：

```bash
# BINANCE_FAPI_BASE_URL=https://fapi.binance.com    # 仅衍生品备援路径使用（OKX 整组不可得时降级）；与现货 BINANCE_BASE_URL 互不影响；受限地区可换镜像
```

- [ ] **Step 5: 验证 + 提交**

```bash
.venv/bin/python -m py_compile src/config.py src/core/config_registry.py
.venv/bin/python -c "import os; os.environ['BINANCE_FAPI_BASE_URL']='https://x.example'; import importlib, src.config as c; importlib.reload(c); print(c.Config.from_env().binance_fapi_base_url if hasattr(c.Config, 'from_env') else 'check-manually')"
```

> 第二条为冒烟性质：若 `Config` 的装配入口不是 `from_env`（以实际为准，~line 1780 所在方法名），改用该入口验证字段装配生效；无法简单实例化时，以 `grep -n binance_fapi_base_url src/config.py` 两处命中 + ci_gate（Task 5）兜底即可，并在报告说明。

```bash
git add src/config.py src/core/config_registry.py .env.example
git commit -m "chore: 注册 BINANCE_FAPI_BASE_URL（对称 binance_base_url），registry 描述补 Binance 兜底语义"
```

---

## Task 5: 文档同步 + 后端全量门禁

**Files:**
- Modify: `docs/crypto-guide.md`（两处）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平一行）

- [ ] **Step 1: crypto-guide「加密永续合约指标」小节** — 在该小节（~line 225 起）的字段表之后、「五路并发」bullet 附近，追加一条 bullet：

```markdown
- 数据源：OKX 主源；OKX 整组不可得时自动整源降级 **Binance fapi**（`source` 字段标实际来源，分析 prompt 标题同步显示实际来源；Binance 路 OI 仅 USD 口径、无张数）。基址可经 `BINANCE_FAPI_BASE_URL` 换镜像（仅备援路径使用，与现货 `BINANCE_BASE_URL` 互不影响）。
```

- [ ] **Step 2: crypto-guide「永续回测」资金费条目** — 「**资金费成本**」bullet（~line 82）末尾追加一句：

```markdown
OKX 拉不到时自动改用 Binance fapi 同窗口（同 `[start, end)` 半开语义）。
```

- [ ] **Step 3: CHANGELOG** — `## [Unreleased]` 段末尾扁平追加（无 `###` 标题）：

```markdown
- [新功能] crypto 衍生品抓取新增 Binance fapi 整源兜底（OKX 全空才降级，source 标实际来源并动态进 prompt；回测资金费历史同享；新增 BINANCE_FAPI_BASE_URL 可换镜像，默认官方域名）
```

- [ ] **Step 4: 后端全量门禁**

Run: `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`
Expected: 全绿（基线 2970 + 本期新增 ~17 用例；输出尾部贴报告）

- [ ] **Step 5: 提交**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 衍生品 Binance 兜底补 crypto-guide 数据源/回测条目与 CHANGELOG"
```

---

## 最终验证（全部任务完成后）

- [ ] 锁套件字节级不变：`git diff --stat <branch-base>..HEAD -- tests/test_backtest_engine.py tests/test_crypto_backtest.py tests/test_backtest_summary.py` → 空
- [ ] Web 零改动：`git diff --stat <branch-base>..HEAD -- apps/` → 空（无需 web-gate）
- [ ] 离线纪律抽查：`grep -rn "fapi.binance.com" tests/` 仅出现在断言/注释中，无真实请求路径
- [ ] 交付说明：改了什么 / 为什么 / 验证情况 / 未验证项（**Binance 在线行为全部未验证**——本环境 451；spec §8 假设清单留待 OKX 受限环境实测）/ 风险点 / 回滚方式（按提交 revert）

---

## Self-Review（plan 对照 spec）

- **§2 新模块**（基址/包络/4 路/OI 口径/历史半开+limit 截断）→ Task 1（实现与测试逐项覆盖）。✓
- **§3 编排**（插入点精确化/延迟 import/触发边界四条）→ Task 2 Step 3 + 4 个新用例（全空降级/部分成功不降/4xx/双 guard 零调用）+ 历史 2 用例。✓
- **§1 analyzer blocker** → Task 3。✓
- **§4 配置**（config.py 两处对称/registry 描述/env.example）→ Task 4。✓
- **§5 测试**（monkeypatch 落点/2 既有用例补丁/其余零改动）→ Task 1/2 测试 + 既有补丁步骤 1b/1c。✓
- **§6 文档** → Task 5。✓
- **§7/§8**（权重风险/未验证假设）→ 最终验证交付说明。✓
- **类型一致性**：`bd.fetch_perp_metrics(base, quote)`/`bd.fetch_funding_rate_history(base, quote, start_ms, end_ms)` 在 Task 1 定义与 Task 2 调用一致；`_fake_binance` 的 url 判定（`/fapi/v1/openInterest` 精确路径）与模块常量一致。✓
- **占位扫描**：Task 4 Step 5 冒烟命令含"以实际为准"的条件指引——已给出兜底验证路径（grep 两处 + ci_gate），非占位。✓
