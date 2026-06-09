# crypto 永续合约指标 Implementation Plan（Phase 1：注入分析）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分析 crypto 现货标的时，并发拉取对应 OKX 永续的资金费率/标记价/未平仓量（免费无 key、presence-only），注入分析 prompt 的「合约市场指标」块。默认开、失败优雅降级、零 schema/API/Web/符号改动。

**Architecture:** 镜像已落地 crypto 范式：`data_provider/crypto_derivatives.py`（纯抓取，OKX 三接口并发）→ `src/services/crypto_derivatives_service.py`（`collect` + `attach_crypto_contracts` 门控注入）→ `src/core/pipeline.py` 在 analyze 前调 `attach_crypto_contracts(enhanced_context, config)` → `GeminiAnalyzer._format_prompt` 渲染 presence-only 块。

**Tech Stack:** Python 3.10（`requests`、`concurrent.futures.ThreadPoolExecutor`、`pytest`、`monkeypatch`）、React 仅 settingsHelp 文案。

**环境约定：** 无 `python`，用 `.venv/bin/python`；pytest 需 `PYTHONPATH="$PWD"`；前端路径含空格，settingsHelp 改动交 CI（本特性无 Web 渲染改动）。

**Spec：** `docs/superpowers/specs/2026-06-09-crypto-derivatives-design.md`
**分支：** `feat/crypto-derivatives`（已建，叠在 `feat/crypto-backtest`）。

---

### Task 1: data_provider — `fetch_perp_metrics`（OKX 三接口并发，纯抓取）

**Files:**
- Create: `data_provider/crypto_derivatives.py`
- Test: `tests/test_crypto_derivatives_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_derivatives_fetch.py
import data_provider.crypto_derivatives as cd


def _fake_okx(url, params=None, headers=None):
    if "funding-rate" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0000059888", "nextFundingTime": "1781049600000"}]}
    if "mark-price" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "markPx": "62669.5"}]}
    if "open-interest" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "2861888.58", "oiCcy": "28618.88", "oiUsd": "1793545573.08"}]}
    return {}


def test_fetch_perp_metrics_parses_all(monkeypatch):
    monkeypatch.setattr(cd, "_http_get_json", _fake_okx)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert abs(out["funding_rate"] - 0.0000059888) < 1e-12
    assert out["next_funding_time"] == 1781049600000
    assert out["mark_price"] == 62669.5
    assert out["open_interest"] == 2861888.58
    assert out["open_interest_usd"] == 1793545573.08
    assert out["source"] == "okx"


def test_fetch_perp_metrics_non_linear_quote_returns_empty(monkeypatch):
    # 非线性永续计价（如 USD/BTC/ETH）直接 {}，不发请求
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(cd, "_http_get_json", spy)
    assert cd.fetch_perp_metrics("BTC", "USD") == {}
    assert called["n"] == 0


def test_fetch_perp_metrics_single_source_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "funding-rate" in url:
            raise RuntimeError("okx funding down")
        if "mark-price" in url:
            return {"data": [{"markPx": "62669.5"}]}
        if "open-interest" in url:
            return {"data": [{"oi": "100.0", "oiUsd": "6000000.0"}]}
        return {}
    monkeypatch.setattr(cd, "_http_get_json", partial)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert "funding_rate" not in out
    assert out["mark_price"] == 62669.5
    assert out["open_interest"] == 100.0
    assert out["source"] == "okx"


def test_fetch_perp_metrics_all_fail_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd.fetch_perp_metrics("BTC", "USDT") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_provider.crypto_derivatives'`

- [ ] **Step 3: Write minimal implementation**

```python
# data_provider/crypto_derivatives.py
"""crypto 永续合约指标（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB。仅读取 CRYPTO_FETCH_TIMEOUT_SECONDS /
CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。数据源：OKX 公共接口（永续 SWAP）。
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"
OKX_MARK_URL = "https://www.okx.com/api/v5/public/mark-price"
OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"
_LINEAR_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价


def _fetch_timeout() -> float:
    raw = os.getenv("CRYPTO_FETCH_TIMEOUT_SECONDS")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return 10.0


def _fetch_max_retries() -> int:
    raw = os.getenv("CRYPTO_FETCH_MAX_RETRIES")
    if raw:
        try:
            v = int(raw)
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    return 0


def _http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> object:
    """GET JSON，复用 CRYPTO_FETCH_* 超时/重试语义（4xx 不重试）。失败抛 requests 异常。"""
    timeout = _fetch_timeout()
    max_retries = _fetch_max_retries()
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                raise
            if attempt >= max_retries:
                raise


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _okx_first(url: str, params: dict) -> dict:
    """GET OKX 公共接口，返回 data[0] dict；失败/空 → {}（不抛，供并发各路独立降级）。"""
    try:
        data = _http_get_json(url, params)
    except Exception as e:
        logger.warning("[合约指标] %s 抓取失败: %s", url, e)
        return {}
    arr = data.get("data") if isinstance(data, dict) else None
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        return arr[0]
    return {}


def fetch_perp_metrics(base: str, quote: str) -> dict:
    """spot BASE/QUOTE → OKX 永续 BASE-QUOTE-SWAP；并发拉 3 个公共接口；presence-only；失败/不支持 → {}。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return {}
    inst = f"{base}-{quote}-SWAP"
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_fr = ex.submit(_okx_first, OKX_FUNDING_URL, {"instId": inst})
        f_mp = ex.submit(_okx_first, OKX_MARK_URL, {"instType": "SWAP", "instId": inst})
        f_oi = ex.submit(_okx_first, OKX_OI_URL, {"instId": inst})
        fr, mp, oi = f_fr.result(), f_mp.result(), f_oi.result()
    out: dict = {}
    fr_v = _to_float(fr.get("fundingRate"))
    if fr_v is not None:
        out["funding_rate"] = fr_v
    nft = _to_int(fr.get("nextFundingTime"))
    if nft is not None:
        out["next_funding_time"] = nft
    mp_v = _to_float(mp.get("markPx"))
    if mp_v is not None:
        out["mark_price"] = mp_v
    oi_v = _to_float(oi.get("oi"))
    if oi_v is not None:
        out["open_interest"] = oi_v
    oiusd = _to_float(oi.get("oiUsd"))
    if oiusd is not None:
        out["open_interest_usd"] = oiusd
    if out:
        out["source"] = "okx"
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_derivatives_fetch.py
git commit -m "feat: crypto 永续合约指标抓取（OKX funding/mark/OI 并发，presence-only）"
```

---

### Task 2: service — `collect` + `attach_crypto_contracts`（门控注入，可单测）

**Files:**
- Create: `src/services/crypto_derivatives_service.py`
- Test: `tests/test_crypto_derivatives_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_derivatives_service.py
import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_service import CryptoDerivativesService, attach_crypto_contracts


class _Cfg:
    def __init__(self, enabled=True):
        self.crypto_derivatives_enabled = enabled


def test_collect_returns_metrics_for_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"funding_rate": 0.0001, "mark_price": 60000.0, "source": "okx"})
    out = CryptoDerivativesService(config=_Cfg()).collect("BTC/USDT")
    assert out == {"funding_rate": 0.0001, "mark_price": 60000.0, "source": "okx"}


def test_collect_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    assert CryptoDerivativesService(config=_Cfg(enabled=False)).collect("BTC/USDT") == {}


def test_collect_non_crypto_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    assert CryptoDerivativesService(config=_Cfg()).collect("600519") == {}


def test_attach_sets_context_for_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 60000.0, "source": "okx"})
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert ctx["crypto_contracts"] == {"mark_price": 60000.0, "source": "okx"}


def test_attach_skips_non_crypto(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {"mark_price": 1.0})
    ctx = {"code": "600519"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert "crypto_contracts" not in ctx


def test_attach_skips_when_empty_metrics(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda b, q: {})
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())
    assert "crypto_contracts" not in ctx


def test_attach_swallows_fetch_error(monkeypatch):
    def boom(b, q):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "fetch_perp_metrics", boom)
    ctx = {"code": "BTC/USDT"}
    attach_crypto_contracts(ctx, config=_Cfg())  # 不抛
    assert "crypto_contracts" not in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.crypto_derivatives_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crypto_derivatives_service.py
# -*- coding: utf-8 -*-
"""crypto 永续合约指标服务（编排层）。

职责：读 config 门控 + region 门控（仅 crypto），调 data_provider 纯抓取源，
presence-only。attach_crypto_contracts 供 pipeline 在分析前注入 context。
"""
import logging
from typing import Any, Dict, Optional

import data_provider.crypto_derivatives as cd
from data_provider.base import is_crypto_code
from src.config import get_config

logger = logging.getLogger(__name__)


class CryptoDerivativesService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def collect(self, code: str) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_derivatives_enabled", True):
            return {}
        if not is_crypto_code(code or ""):
            return {}
        base, _, quote = (code or "").partition("/")
        return cd.fetch_perp_metrics(base, quote)


def attach_crypto_contracts(context: Dict[str, Any], config: Optional[Any] = None) -> None:
    """crypto 标的：拉永续指标写入 context['crypto_contracts']（presence-only）。失败/非 crypto/禁用 → 不写。"""
    code = context.get("code") if isinstance(context, dict) else None
    if not code:
        return
    try:
        metrics = CryptoDerivativesService(config=config).collect(code)
    except Exception as e:
        logger.warning("[合约指标] 收集失败，跳过: %s", e)
        return
    if metrics:
        context["crypto_contracts"] = metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_service.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crypto_derivatives_service.py tests/test_crypto_derivatives_service.py
git commit -m "feat: crypto 永续合约指标服务 + attach 注入（门控/presence-only/降级）"
```

---

### Task 3: 配置 — field + registry + settingsHelp + .env.example

**Files:**
- Modify: `src/config.py`（field + `_load_from_env`，紧邻 `crypto_market_indicators_enabled`）
- Modify: `src/core/config_registry.py`（在 `CRYPTO_MARKET_INDICATORS_ENABLED` 条目之后）
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts`（zh + en）
- Modify: `.env.example`
- Test: `tests/test_crypto_derivatives_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_derivatives_config.py
from src.config import Config


def test_derivatives_default_enabled(monkeypatch):
    monkeypatch.delenv("CRYPTO_DERIVATIVES_ENABLED", raising=False)
    assert Config._load_from_env().crypto_derivatives_enabled is True


def test_derivatives_env_disable(monkeypatch):
    monkeypatch.setenv("CRYPTO_DERIVATIVES_ENABLED", "false")
    assert Config._load_from_env().crypto_derivatives_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'crypto_derivatives_enabled'`

- [ ] **Step 3a: config field** — 在 `src/config.py` 的 `crypto_market_indicators_enabled: bool = True` 之后新增：

```python
    crypto_derivatives_enabled: bool = True
```

- [ ] **Step 3b: from_env** — 在 `_load_from_env` 的 `crypto_market_indicators_enabled=...` 行之后新增：

```python
            crypto_derivatives_enabled=os.getenv('CRYPTO_DERIVATIVES_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'on'),
```

- [ ] **Step 3c: registry entry** — 在 `src/core/config_registry.py` 的 `CRYPTO_MARKET_INDICATORS_ENABLED` 条目（其闭合 `},`）之后新增：

```python
    "CRYPTO_DERIVATIVES_ENABLED": {
        "title": "Crypto Derivatives (Perpetual) Metrics",
        "description": "Enable injecting matching perpetual funding rate, mark price, and open interest into the crypto spot analysis prompt (sourced from OKX public APIs).",
        "category": "data_source",
        "data_type": "boolean",
        "ui_control": "switch",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "true",
        "options": [],
        "validation": {},
        "display_order": 77,
        "help_key": "settings.data_source.crypto_derivatives",
        "examples": [
            "CRYPTO_DERIVATIVES_ENABLED=true",
            "CRYPTO_DERIVATIVES_ENABLED=false",
        ],
        "docs": [
            {
                "label": "完整指南：环境变量完整列表",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#环境变量完整列表",
            },
        ],
        "warning_codes": [],
    },
```

- [ ] **Step 3d: settingsHelp.ts (zh)** — 在中文块 `'settings.data_source.crypto_market_indicators': { ... },` 之后新增：

```typescript
  'settings.data_source.crypto_derivatives': {
    title: '加密永续合约指标',
    summary: '分析加密现货标的时，注入对应永续合约的资金费率、标记价、未平仓量（来自 OKX 公开 API），供 LLM 判断杠杆情绪。',
    usage: 'CRYPTO_DERIVATIVES_ENABLED 开启或关闭（默认开启）。',
    valueNotes: [
      '数据源为 OKX 永续公开接口（funding-rate / mark-price / open-interest），免费、无需 API Key，三路并发拉取。',
      '每分析一个加密标的会额外拉取一次；任一指标失败自动省略，不影响分析其余部分。',
    ],
    impact: ['影响加密现货分析 prompt 中是否包含「合约市场指标」块。'],
    notes: [
      '仅加密标的（code 含 /）触发；A股/港股/美股不受影响。仅 USDT/USDC 计价存在对应线性永续。',
    ],
  },
```

- [ ] **Step 3e: settingsHelp.ts (en)** — 在英文块 `'settings.data_source.crypto_market_indicators': { ... },` 之后新增：

```typescript
  'settings.data_source.crypto_derivatives': {
    title: 'Crypto Derivatives (Perpetual) Metrics',
    summary: 'Injects the matching perpetual funding rate, mark price, and open interest (from OKX public APIs) into the crypto spot analysis prompt for leverage-sentiment context.',
    usage: 'CRYPTO_DERIVATIVES_ENABLED toggles the feature (enabled by default).',
    valueNotes: [
      'Source is OKX perpetual public endpoints (funding-rate / mark-price / open-interest); free, no API key, fetched concurrently.',
      'Adds one fetch per analyzed crypto symbol; any failed metric is omitted without affecting the rest of the analysis.',
    ],
    impact: ['Affects whether the crypto spot analysis prompt includes a "contract market metrics" block.'],
    notes: [
      'Only crypto symbols (code containing /) trigger this; A-share / HK / US are unaffected. Only USDT/USDC-quoted pairs have a matching linear perpetual.',
    ],
  },
```

确认 grep `settings.data_source.crypto_derivatives` 在该文件出现恰好 2 次。

- [ ] **Step 3f: .env.example** — 在 `# CRYPTO_MARKET_INDICATORS_ENABLED=true` 之后新增：

```bash

# 加密永续合约指标（CRYPTO_DERIVATIVES_ENABLED=true 时为 crypto 现货分析注入对应永续 资金费率/标记价/未平仓量）
# 数据源：OKX 永续公开接口，免费无 key，三路并发；每分析一个 crypto 标的额外拉取一次
# CRYPTO_DERIVATIVES_ENABLED=true
```

- [ ] **Step 4: Run tests（config + 双向 help-key 契约）**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_config.py tests/test_config_registry.py -v`
Expected: PASS（含 `test_registry_help_keys_exist_in_locales` 与 `test_locale_help_keys_are_registry_or_llm_channel_internal`，registry 与 locale 双向一致）。若 `display_order 77` 冲突致唯一性测试失败，改下一个未占用值。

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/core/config_registry.py apps/dsa-web/src/locales/settingsHelp.ts .env.example tests/test_crypto_derivatives_config.py
git commit -m "feat: crypto 永续合约指标配置项 CRYPTO_DERIVATIVES_ENABLED（registry+locale 双向一致）"
```

---

### Task 4: analyzer — `_format_prompt` 渲染「合约市场指标」块

**Files:**
- Modify: `src/analyzer.py`（`_format_prompt`，实时行情块之后）
- Test: `tests/test_crypto_derivatives_prompt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_derivatives_prompt.py
from src.analyzer import GeminiAnalyzer

a = GeminiAnalyzer()

_BASE_CTX = {
    "code": "BTC/USDT",
    "stock_name": "BTC/USDT",
    "date": "2026-06-09",
    "today": {"close": 60000.0, "open": 60000.0, "high": 61000.0, "low": 59000.0, "pct_chg": 1.0, "ma5": 60000.0, "ma10": 59000.0, "ma20": 58000.0},
}


def test_prompt_includes_contract_block_when_present():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "mark_price": 62669.5, "open_interest": 2861888.58, "open_interest_usd": 1793545573.08, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "合约市场指标" in prompt
    assert "62669.5" in prompt          # mark price
    assert "0.0100%" in prompt          # funding_rate 0.0001 → *100 = 0.0100%


def test_prompt_omits_contract_block_when_absent():
    prompt = a._format_prompt(dict(_BASE_CTX), "BTC/USDT", report_language="zh")
    assert "合约市场指标" not in prompt
```

> 注：若 `_format_prompt` 因 `_BASE_CTX` 缺某 key 抛 KeyError，参照 `tests/test_analyzer_news_prompt.py` 中可用的 context 补齐字段（它已用 `_format_prompt(context, name, ...)` 跑通）。

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py -v`
Expected: FAIL（`合约市场指标` 未渲染）。

- [ ] **Step 3: Implement render block** — 在 `src/analyzer.py` `_format_prompt` 内、实时行情块（`if 'realtime' in context:` 那段，约 2987-3001）**之后**插入：

```python
        # crypto 永续合约指标（presence-only；仅当 pipeline 注入 context['crypto_contracts'] 时渲染）
        contracts = context.get("crypto_contracts") if isinstance(context, dict) else None
        if isinstance(contracts, dict) and contracts:
            rows = []
            fr = contracts.get("funding_rate")
            if fr is not None:
                rows.append(f"| 资金费率 | {fr * 100:.4f}% | 正=多头付费 / 负=空头付费（约 8h 结算） |")
            mp = contracts.get("mark_price")
            if mp is not None:
                rows.append(f"| 标记价 | {mp} | 永续标记价（与现货价对比看基差） |")
            oi = contracts.get("open_interest")
            oi_usd = contracts.get("open_interest_usd")
            if oi is not None or oi_usd is not None:
                oi_txt = f"{oi} 张" if oi is not None else ""
                usd_txt = f"${oi_usd:,.0f}" if oi_usd is not None else ""
                joined = " / ".join([t for t in (oi_txt, usd_txt) if t])
                rows.append(f"| 未平仓量(OI) | {joined} | 持仓规模与杠杆活跃度 |")
            if rows:
                rows_text = "\n".join(rows)
                prompt += f"""
### 合约市场指标（永续，来源 OKX）
| 指标 | 数值 | 含义 |
|------|------|------|
{rows_text}
[加密货币专属] 结合资金费率与持仓判断杠杆情绪与挤压风险，不得编造数据。
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/analyzer.py tests/test_crypto_derivatives_prompt.py
git commit -m "feat: 分析 prompt 渲染 crypto 合约市场指标块（presence-only）"
```

---

### Task 5: pipeline — 在分析前注入 `crypto_contracts`

**Files:**
- Modify: `src/core/pipeline.py`（import + analyze 调用前一行）

- [ ] **Step 1: 加 import** — 在 `src/core/pipeline.py` 的 `from data_provider.base import normalize_stock_code` 行改为：

```python
from data_provider.base import normalize_stock_code, is_crypto_code
```
并在 import 区新增（与其它 service import 一起）：
```python
from src.services.crypto_derivatives_service import attach_crypto_contracts
```

- [ ] **Step 2: 注入调用** — 在 `src/core/pipeline.py` 调 `result = self.analyzer.analyze(enhanced_context, ...)`（约 572 行）**之前**插入一行：

```python
            attach_crypto_contracts(enhanced_context, get_config())
```
（`get_config` 已在文件顶部 import；`is_crypto_code` 门控在 `collect` 内已处理，此处直接调 attach 即可——非 crypto/禁用/失败均不写入，安全幂等。）

- [ ] **Step 3: 验证不破坏导入与编译**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m py_compile src/core/pipeline.py && echo OK`
Expected: `OK`（无语法/导入错误）。
说明：注入门控逻辑已由 Task 2 的 `attach_crypto_contracts` 单测全覆盖（crypto 注入 / 非 crypto 跳过 / 禁用跳过 / 空跳过 / 异常吞掉），渲染由 Task 4 覆盖；本任务仅一行 wiring，端到端由 Task 7 全量回归兜底。

- [ ] **Step 4: 快速回归相邻模块**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_derivatives_service.py tests/test_crypto_derivatives_prompt.py -v`
Expected: 全 PASS（确认 service + 渲染仍绿）。

- [ ] **Step 5: Commit**

```bash
git add src/core/pipeline.py
git commit -m "feat: pipeline 分析前注入 crypto 永续合约指标到 context"
```

---

### Task 6: 文档（crypto-guide + CHANGELOG）

**Files:**
- Modify: `docs/crypto-guide.md`（「加密市场宏观指标」节之后新增「加密永续合约指标」节）
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: crypto-guide 新增节** — 在 `## 加密市场宏观指标` 节之后、`## 9. 已知限制（后续阶段）` 之前新增：

```markdown
## 加密永续合约指标

`CRYPTO_DERIVATIVES_ENABLED=true`（默认开启）时，分析加密**现货**标的会并发拉取对应 **OKX 永续**（`BASE-QUOTE-SWAP`，仅 USDT/USDC 线性永续）的合约指标，注入分析 prompt 的「合约市场指标」块：

| 指标 | 来源 | 含义 |
|---|---|---|
| 资金费率 | OKX `/public/funding-rate` | 正=多头付费 / 负=空头付费（约 8h 结算） |
| 标记价 | OKX `/public/mark-price` | 与现货价对比看基差 |
| 未平仓量(OI / USD) | OKX `/public/open-interest` | 持仓规模与杠杆活跃度 |

- 免费、无需 API Key；三路并发；超时/重试复用 `CRYPTO_FETCH_TIMEOUT_SECONDS` / `CRYPTO_FETCH_MAX_RETRIES`。
- **presence-only**：任一指标失败即省略；全失败/非 USDT(USDC) 计价/禁用 → 不注入，分析照常。
- 每分析一个加密标的额外拉取一次；仅注入 LLM 分析 prompt（不改报告 schema/API/Web）。
- 仅加密标的触发；A股/港股/美股不受影响。
- 后续子项目（未做）：Binance fapi 备援（本环境 451）、结构化 surfacing、独立 perp 符号、perp klines/回测。
```

- [ ] **Step 2: CHANGELOG** — 在 `[Unreleased]`（扁平，独立一行）新增：

```markdown
- [新功能] crypto 现货分析注入对应永续合约指标（资金费率/标记价/未平仓量；OKX 公开接口，免费无 key，并发，presence-only，默认开，仅注入分析 prompt）
```

- [ ] **Step 3: 核对** 配置项名、来源接口、presence-only 与降级语义与实仓一致。Docs only, tests not run。

- [ ] **Step 4: Commit**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 加密永续合约指标节与 CHANGELOG"
```

---

### Task 7: 收尾 — 全量回归 + ci_gate

**Files:** 无（仅验证）

- [ ] **Step 1: 全量离线回归**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`
Expected: 全绿（2872 基线 + 本特性 ~13 用例）。

- [ ] **Step 2: ci_gate**

Run: `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh`
Expected: `all checks passed`。

- [ ] **Step 3: （无 commit）** 验证任务。若回归暴露问题，回对应 Task 修正后重跑。

---

## Self-Review

**1. Spec coverage：**
- §2.1 fetch_perp_metrics（OKX 三接口并发 presence-only）→ Task 1 ✓
- §2.2 service collect + 注入 → Task 2（含 attach_crypto_contracts）✓
- §2.3 pipeline 注入 → Task 5 ✓
- §2.4 _format_prompt 渲染 → Task 4 ✓
- §2.5 配置（field/registry/locale/.env + 双向契约）→ Task 3 ✓
- §3 数据流 → Task 5（注入）+ Task 4（渲染）✓
- §4 错误处理（单路/全失败/disabled/非 crypto/异常隔离）→ Task 1/2 用例 ✓
- §5 测试矩阵 → Task 1-4 + Task 7 回归 ✓（pipeline 注入逻辑经 Task 2 attach 单测覆盖，一行 wiring 经 py_compile + 全量回归）
- §6 分支/回滚 → header + 各 commit ✓
- §7 范围边界（OKX-only、零 schema/API/Web/符号/klines/回测）→ 全程遵循 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个 code step 含完整代码与确切命令。Task 5「无新生产实现细节之外」是有意的一行 wiring + 委托给已测 helper。

**3. Type/signature consistency：**
- `fetch_perp_metrics(base, quote)`、`CryptoDerivativesService.collect(code)`、`attach_crypto_contracts(context, config=None)` 在 Task 1/2/5 引用一致。
- context key `crypto_contracts`（Task 2 写入 ↔ Task 4 读取）一致；字段 `funding_rate/next_funding_time/mark_price/open_interest/open_interest_usd/source` 在 fetch（Task 1）/渲染（Task 4）一致。
- patch 目标：Task 1 patch `cd._http_get_json`；Task 2 patch `cd.fetch_perp_metrics`（模块引用，service 经 `import data_provider.crypto_derivatives as cd` 调用，可命中）。
- config `crypto_derivatives_enabled` 默认 True，registry default "true"，from_env 解析一致。
- funding_rate 存原始小数（Task 1），渲染 `*100:.4f%`（Task 4）一致；测试 0.0001→"0.0100%"。
