# crypto 专属大盘指标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 crypto 大盘复盘中新增三项免费、无 key 的宏观指标（BTC/ETH 主导率、加密总市值含 24h 变化、恐贪指数），以 presence-only 字段并入复盘 payload，注入复盘 prompt，并在 Web 渲染。

**Architecture:** 镜像已落地的 `new_listings` 范式——`data_provider/` 纯抓取（不 import src.*/不碰 DB）→ `src/services` 薄编排合并 → `MarketAnalyzer` region-gated 钩子 → presence-only `payload["market_indicators"]` + prompt 事实块 + Web 卡片。两个免费源：CoinGecko `/global`、alternative.me `/fng`。

**Tech Stack:** Python 3.10（`requests`、`pytest`、`monkeypatch`）、React + TS（vitest）、SQLAlchemy 无关（本特性不落库）。

**环境约定：** 本仓库无 `python`，用 `.venv/bin/python`；pytest 需 `PYTHONPATH="$PWD"`。前端因工作区路径含空格，本地只跑 vitest（node 直调），完整 build 交 CI。

**Spec：** `docs/superpowers/specs/2026-06-09-crypto-market-indicators-design.md`

**分支：** `feat/crypto-market-indicators`（已建，基于 `feat/crypto-new-listings`）。

---

### Task 1: data_provider — `fetch_global_market`（CoinGecko /global，纯抓取）

**Files:**
- Create: `data_provider/crypto_market_indicators.py`
- Test: `tests/test_crypto_market_indicators_global.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_global.py
import data_provider.crypto_market_indicators as cmi


def test_fetch_global_market_parses_presence_only(monkeypatch):
    sample = {"data": {
        "market_cap_percentage": {"btc": 56.07, "eth": 8.98},
        "total_market_cap": {"usd": 2241017397766.0},
        "market_cap_change_percentage_24h_usd": -0.64,
        "total_volume": {"usd": 91620725291.0},
    }}
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: sample)
    out = cmi.fetch_global_market()
    assert out["btc_dominance"] == 56.07
    assert out["eth_dominance"] == 8.98
    assert out["total_market_cap_usd"] == 2241017397766.0
    assert out["market_cap_change_24h_pct"] == -0.64
    assert out["total_volume_usd"] == 91620725291.0


def test_fetch_global_market_non_dict_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: ["unexpected"])
    assert cmi.fetch_global_market() == {}


def test_fetch_global_market_missing_fields_presence_only(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": {"market_cap_percentage": {"btc": 50.0}}})
    assert cmi.fetch_global_market() == {"btc_dominance": 50.0}


def test_fetch_global_market_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(cmi, "_http_get_json", boom)
    assert cmi.fetch_global_market() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_global.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data_provider.crypto_market_indicators'`

- [ ] **Step 3: Write minimal implementation**

```python
# data_provider/crypto_market_indicators.py
"""数字货币大盘宏观指标（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB；合并/presence-only/配置门控在 src 服务层。
仅读取通用传输旋钮 CRYPTO_FETCH_TIMEOUT_SECONDS / CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


# 与 crypto_new_listings 同义；保持本模块零跨层依赖，故就地复制而非 import。
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


def fetch_global_market() -> dict:
    """CoinGecko /global → 宏观聚合；presence-only（缺字段不塞）；失败/非 dict → {}。"""
    try:
        data = _http_get_json(GLOBAL_URL)
    except Exception as e:
        logger.warning("[大盘指标-global] 抓取失败: %s", e)
        return {}
    d = data.get("data") if isinstance(data, dict) else None
    if not isinstance(d, dict):
        return {}
    out: dict = {}
    mcp = d.get("market_cap_percentage")
    if isinstance(mcp, dict):
        btc = _to_float(mcp.get("btc"))
        eth = _to_float(mcp.get("eth"))
        if btc is not None:
            out["btc_dominance"] = btc
        if eth is not None:
            out["eth_dominance"] = eth
    tmc = d.get("total_market_cap")
    if isinstance(tmc, dict):
        usd = _to_float(tmc.get("usd"))
        if usd is not None:
            out["total_market_cap_usd"] = usd
    chg = _to_float(d.get("market_cap_change_percentage_24h_usd"))
    if chg is not None:
        out["market_cap_change_24h_pct"] = chg
    tv = d.get("total_volume")
    if isinstance(tv, dict):
        vol = _to_float(tv.get("usd"))
        if vol is not None:
            out["total_volume_usd"] = vol
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_global.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add data_provider/crypto_market_indicators.py tests/test_crypto_market_indicators_global.py
git commit -m "feat: crypto 大盘指标 global 源抓取（coingecko /global，presence-only）"
```

---

### Task 2: data_provider — `fetch_fear_greed`（alternative.me /fng，字符串→int）

**Files:**
- Modify: `data_provider/crypto_market_indicators.py`
- Test: `tests/test_crypto_market_indicators_fng.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_fng.py
import data_provider.crypto_market_indicators as cmi


def test_fetch_fear_greed_parses_string_value(monkeypatch):
    sample = {"data": [{"value": "10", "value_classification": "Extreme Fear", "timestamp": "1780963200"}]}
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: sample)
    assert cmi.fetch_fear_greed() == {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200}


def test_fetch_fear_greed_empty_data_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": []})
    assert cmi.fetch_fear_greed() == {}


def test_fetch_fear_greed_missing_field_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "_http_get_json", lambda *a, **k: {"data": [{"value": "10"}]})
    assert cmi.fetch_fear_greed() == {}


def test_fetch_fear_greed_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cmi, "_http_get_json", boom)
    assert cmi.fetch_fear_greed() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_fng.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'fetch_fear_greed'`

- [ ] **Step 3: Write minimal implementation** (append to `data_provider/crypto_market_indicators.py`)

```python
FNG_URL = "https://api.alternative.me/fng/"


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_fear_greed() -> dict:
    """alternative.me /fng → 情绪；失败/空/字段缺失 → {}。

    注意：API 的 value / timestamp 为字符串，需 int() 解析；classification 原样取字符串。
    """
    try:
        data = _http_get_json(FNG_URL, params={"limit": 1})
    except Exception as e:
        logger.warning("[大盘指标-fng] 抓取失败: %s", e)
        return {}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return {}
    first = items[0]
    if not isinstance(first, dict):
        return {}
    value = _to_int(first.get("value"))
    classification = first.get("value_classification")
    timestamp = _to_int(first.get("timestamp"))
    if value is None or not classification or timestamp is None:
        return {}
    return {"value": value, "classification": str(classification), "timestamp": timestamp}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_fng.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add data_provider/crypto_market_indicators.py tests/test_crypto_market_indicators_fng.py
git commit -m "feat: crypto 大盘指标 fng 源抓取（alternative.me，字符串→int，presence-only）"
```

---

### Task 3: service — `CryptoMarketIndicatorService.collect()`（合并 presence-only）

**Files:**
- Create: `src/services/crypto_market_indicator_service.py`
- Test: `tests/test_crypto_market_indicator_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicator_service.py
import data_provider.crypto_market_indicators as cmi
from src.services.crypto_market_indicator_service import CryptoMarketIndicatorService


class _Cfg:
    def __init__(self, enabled=True):
        self.crypto_market_indicators_enabled = enabled


def test_collect_merges_presence_only(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 56.0, "total_market_cap_usd": 2.0e12})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200})
    out = CryptoMarketIndicatorService(config=_Cfg()).collect()
    assert out["btc_dominance"] == 56.0
    assert out["total_market_cap_usd"] == 2.0e12
    assert out["fear_greed"] == {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200}


def test_collect_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 56.0})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "X", "timestamp": 1})
    assert CryptoMarketIndicatorService(config=_Cfg(enabled=False)).collect() == {}


def test_collect_single_source_failure_keeps_other(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})  # global 降级
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 5, "classification": "Extreme Fear", "timestamp": 1})
    out = CryptoMarketIndicatorService(config=_Cfg()).collect()
    assert out == {"fear_greed": {"value": 5, "classification": "Extreme Fear", "timestamp": 1}}


def test_collect_both_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    assert CryptoMarketIndicatorService(config=_Cfg()).collect() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicator_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.crypto_market_indicator_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/crypto_market_indicator_service.py
# -*- coding: utf-8 -*-
"""crypto 大盘宏观指标服务（编排层）。

职责：读 config 门控，调度 data_provider 纯抓取源（global + fng），合并为单 dict（presence-only）。
data_provider 保持纯抓取。单源失败已在源内降级为 {}，合并天然保留另一源。
"""
import logging
from typing import Any, Dict

import data_provider.crypto_market_indicators as cmi
from src.config import get_config

logger = logging.getLogger(__name__)


class CryptoMarketIndicatorService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def collect(self) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_market_indicators_enabled", True):
            return {}
        out: Dict[str, Any] = {}
        out.update(cmi.fetch_global_market())   # 失败已在源内降级为 {}
        fng = cmi.fetch_fear_greed()
        if fng:
            out["fear_greed"] = fng
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicator_service.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/crypto_market_indicator_service.py tests/test_crypto_market_indicator_service.py
git commit -m "feat: crypto 大盘指标服务合并两源（presence-only，disabled→{}）"
```

---

### Task 4: 配置 — config 字段 + registry + settingsHelp + .env.example

**Files:**
- Modify: `src/config.py:933`（字段定义区）与 `src/config.py:1775`（`_load_from_env` 区）
- Modify: `src/core/config_registry.py`（在最后一个 `CRYPTO_NEW_LISTING_*` 条目之后追加）
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts`（zh 块 + en 块各加一条）
- Modify: `.env.example:305` 之后
- Test: `tests/test_crypto_market_indicators_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_config.py
from src.config import Config


def test_indicators_default_enabled(monkeypatch):
    monkeypatch.delenv("CRYPTO_MARKET_INDICATORS_ENABLED", raising=False)
    cfg = Config._load_from_env()
    assert cfg.crypto_market_indicators_enabled is True


def test_indicators_env_disable(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "false")
    cfg = Config._load_from_env()
    assert cfg.crypto_market_indicators_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'crypto_market_indicators_enabled'`

- [ ] **Step 3a: Add config field** — 在 `src/config.py` 的 `crypto_new_listing_max: int = 20`（约 936 行）之后新增一行：

```python
    crypto_market_indicators_enabled: bool = True
```

- [ ] **Step 3b: Add from_env wiring** — 在 `src/config.py` 的 `crypto_new_listing_max=parse_env_int(...)`（约 1775 行）之后新增一行（镜像 `crypto_new_listing_enabled` 写法）：

```python
            crypto_market_indicators_enabled=os.getenv('CRYPTO_MARKET_INDICATORS_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'on'),
```

- [ ] **Step 3c: Add registry entry** — 在 `src/core/config_registry.py` 最后一个 `CRYPTO_NEW_LISTING_*` 条目（`CRYPTO_NEW_LISTING_MAX`，约 3846 行 help_key 处的条目结尾 `},`）之后追加：

```python
    "CRYPTO_MARKET_INDICATORS_ENABLED": {
        "title": "Crypto Market Indicators",
        "description": "Enable crypto macro market indicators (BTC/ETH dominance, total market cap, fear & greed). When enabled, the daily crypto market review fetches these from free public APIs.",
        "category": "data_source",
        "data_type": "boolean",
        "ui_control": "switch",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "true",
        "options": [],
        "validation": {},
        "display_order": 76,
        "help_key": "settings.data_source.crypto_market_indicators",
        "examples": [
            "CRYPTO_MARKET_INDICATORS_ENABLED=true",
            "CRYPTO_MARKET_INDICATORS_ENABLED=false",
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

- [ ] **Step 3d: Add settingsHelp.ts (zh)** — 在 `apps/dsa-web/src/locales/settingsHelp.ts` 的中文块（`'settings.data_source.crypto_new_listing': { ... },` 之后，约 280 行附近的该对象结尾后）追加：

```typescript
  'settings.data_source.crypto_market_indicators': {
    title: '加密市场宏观指标',
    summary: '在加密货币大盘复盘中展示 BTC/ETH 主导率、加密总市值（含 24h 变化）与恐贪指数，来自免费公开 API。',
    usage: 'CRYPTO_MARKET_INDICATORS_ENABLED 开启或关闭该功能（默认开启）。',
    valueNotes: [
      '数据源为 CoinGecko /global（主导率、总市值）与 alternative.me（恐贪指数），均免费、无需 API Key。',
      '任一数据源失败时对应指标自动省略，不影响复盘其余部分。',
    ],
    impact: ['影响加密货币大盘复盘中宏观指标的展示，以及注入复盘 prompt 的宏观背景。'],
    notes: [
      '仅加密货币（crypto）大盘复盘触发；A股/港股/美股不受影响。',
    ],
  },
```

- [ ] **Step 3e: Add settingsHelp.ts (en)** — 在英文块（`'settings.data_source.crypto_new_listing': { ... },` 之后，约 1240 行附近的该对象结尾后）追加：

```typescript
  'settings.data_source.crypto_market_indicators': {
    title: 'Crypto Market Indicators',
    summary: 'Shows BTC/ETH dominance, total market cap (with 24h change), and the Fear & Greed index in the crypto market review, sourced from free public APIs.',
    usage: 'CRYPTO_MARKET_INDICATORS_ENABLED toggles the feature (enabled by default).',
    valueNotes: [
      'Sources are CoinGecko /global (dominance, market cap) and alternative.me (fear & greed); both are free and require no API key.',
      'If a source fails, the corresponding indicator is omitted without affecting the rest of the review.',
    ],
    impact: ['Affects the macro indicators shown in the crypto market review and the macro context injected into the review prompt.'],
    notes: [
      'Only the crypto market review triggers this; A-share / HK / US reviews are unaffected.',
    ],
  },
```

- [ ] **Step 3f: Add .env.example** — 在 `.env.example` 的 `# CRYPTO_NEW_LISTING_MAX=20 ...`（305 行）之后追加：

```bash

# 加密市场宏观指标（CRYPTO_MARKET_INDICATORS_ENABLED=true 时在加密大盘复盘展示并注入 prompt）
# 数据源：CoinGecko /global（BTC/ETH 主导率、总市值）+ alternative.me（恐贪指数），均免费无 key
# CRYPTO_MARKET_INDICATORS_ENABLED=true
```

- [ ] **Step 4: Run tests (config + 双向 help-key 契约)**

Run:
```bash
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_config.py tests/test_config_registry.py -v
```
Expected: PASS（含 `test_registry_help_keys_exist_in_locales` 与 `test_locale_help_keys_are_registry_or_llm_channel_internal` —— 二者要求 registry help_key 与 settingsHelp.ts 键**双向一致**，故 3c 与 3d/3e 必须同时落地）。
若 `display_order 76` 与同类目冲突导致唯一性测试失败，改用下一个未占用值并重跑。

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/core/config_registry.py apps/dsa-web/src/locales/settingsHelp.ts .env.example tests/test_crypto_market_indicators_config.py
git commit -m "feat: crypto 大盘指标配置项 CRYPTO_MARKET_INDICATORS_ENABLED（registry+locale 双向一致）"
```

---

### Task 5: MarketAnalyzer — region 钩子 + payload presence-only 字段

**Files:**
- Modify: `src/market_analyzer.py`（新增 `_get_crypto_market_indicators`；`build_market_review_payload` 增参与字段）
- Test: `tests/test_crypto_market_indicators_payload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_payload.py
import data_provider.crypto_market_indicators as cmi
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_payload_includes_market_indicators_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    ind = {"btc_dominance": 56.07, "fear_greed": {"value": 10, "classification": "Extreme Fear", "timestamp": 1}}
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=ind)
    assert payload["market_indicators"] == ind


def test_payload_omits_market_indicators_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators={})
    assert "market_indicators" not in payload


def test_non_crypto_get_market_indicators_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_market_indicators() == {}


def test_crypto_get_market_indicators_uses_service(monkeypatch):
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {"btc_dominance": 50.0})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_market_indicators() == {"btc_dominance": 50.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_payload.py -v`
Expected: FAIL — `TypeError: build_market_review_payload() got an unexpected keyword argument 'market_indicators'`

- [ ] **Step 3a: Add region 钩子** — 在 `src/market_analyzer.py` 的 `_get_crypto_new_listings`（约 493 行结尾）之后新增：

```python
    def _get_crypto_market_indicators(self) -> Dict[str, Any]:
        """crypto 大盘宏观指标；非 crypto 返回 {}，任何失败优雅降级为 {}。"""
        if self.region != "crypto":
            return {}
        try:
            from src.services.crypto_market_indicator_service import CryptoMarketIndicatorService
            return CryptoMarketIndicatorService().collect()
        except Exception as e:
            logger.warning("[大盘指标] 收集失败，跳过: %s", e)
            return {}
```

- [ ] **Step 3b: Extend payload builder** — 修改 `build_market_review_payload` 签名（约 571 行），在 `new_listings` 参数之后新增 `market_indicators`：

```python
    def build_market_review_payload(
        self,
        overview: MarketOverview,
        news: List,
        report: str,
        market_light_snapshot: Optional[Dict[str, Any]] = None,
        new_listings: Optional[List[Dict[str, Any]]] = None,
        market_indicators: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
```

并在 `if new_listings: payload["new_listings"] = new_listings`（约 639-640 行）之后新增：

```python
        if market_indicators:
            payload["market_indicators"] = market_indicators
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_payload.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/market_analyzer.py tests/test_crypto_market_indicators_payload.py
git commit -m "feat: crypto 大盘指标 region 钩子 + payload presence-only 字段"
```

---

### Task 6: MarketAnalyzer — prompt 注入（事实块 + 接线）

**Files:**
- Modify: `src/market_analyzer.py`（新增 `_get_crypto_indicators_prompt_block`；`generate_market_review` / `_build_review_prompt` 增参；两处 prompt 模板插入；`_run_daily_review_parts` 重排取数顺序并传参）
- Test: `tests/test_crypto_market_indicators_prompt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_prompt.py
from src.market_analyzer import MarketAnalyzer, MarketOverview

IND = {
    "btc_dominance": 56.07, "eth_dominance": 8.98,
    "total_market_cap_usd": 2241017397766.0, "market_cap_change_24h_pct": -0.64,
    "fear_greed": {"value": 10, "classification": "Extreme Fear", "timestamp": 1},
}


def test_indicators_prompt_block_zh_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_indicators_prompt_block(IND, "zh")
    assert "加密市场宏观指标" in block
    assert "BTC 主导率：56.07%" in block
    assert "恐贪指数：10（Extreme Fear）" in block


def test_indicators_prompt_block_empty_when_no_data():
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_indicators_prompt_block({}, "zh") == ""


def test_indicators_prompt_block_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_indicators_prompt_block(IND, "zh") == ""


def test_review_prompt_includes_indicators_block():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    prompt = a._build_review_prompt(ov, [], IND)
    assert "加密市场宏观指标" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_prompt.py -v`
Expected: FAIL — `AttributeError: 'MarketAnalyzer' object has no attribute '_get_crypto_indicators_prompt_block'`

- [ ] **Step 3a: Add the block builder** — 在 `src/market_analyzer.py` 的 `_get_crypto_addendum_prompt`（约 481 行结尾）之后新增：

```python
    def _get_crypto_indicators_prompt_block(self, indicators: Optional[Dict[str, Any]], review_language: str | None = None) -> str:
        """crypto 宏观指标事实块（注入 prompt 供 LLM 引用）；非 crypto 或无数据返回空。"""
        if self.region != "crypto" or not indicators:
            return ""
        lang = review_language or self._get_review_language()
        btc = indicators.get("btc_dominance")
        eth = indicators.get("eth_dominance")
        tmc = indicators.get("total_market_cap_usd")
        chg = indicators.get("market_cap_change_24h_pct")
        fng = indicators.get("fear_greed") if isinstance(indicators.get("fear_greed"), dict) else None
        parts: List[str] = []
        if lang == "en":
            if btc is not None:
                parts.append(f"- BTC dominance: {btc:.2f}%")
            if eth is not None:
                parts.append(f"- ETH dominance: {eth:.2f}%")
            if tmc is not None:
                chg_txt = f" ({chg:+.2f}% 24h)" if chg is not None else ""
                parts.append(f"- Total market cap: ${tmc:,.0f}{chg_txt}")
            if fng:
                parts.append(f"- Fear & Greed: {fng.get('value')} ({fng.get('classification')})")
            if not parts:
                return ""
            return ("\n## Crypto Macro Indicators\n" + "\n".join(parts)
                    + "\n[Crypto] Comment on market sentiment and structure using the indicators above; do not invent data.")
        if btc is not None:
            parts.append(f"- BTC 主导率：{btc:.2f}%")
        if eth is not None:
            parts.append(f"- ETH 主导率：{eth:.2f}%")
        if tmc is not None:
            chg_txt = f"（24h {chg:+.2f}%）" if chg is not None else ""
            parts.append(f"- 加密总市值：${tmc:,.0f}{chg_txt}")
        if fng:
            parts.append(f"- 恐贪指数：{fng.get('value')}（{fng.get('classification')}）")
        if not parts:
            return ""
        return ("\n## 加密市场宏观指标\n" + "\n".join(parts)
                + "\n[加密货币专属] 结合上述宏观指标点评市场情绪与结构，不得编造数据。")
```

- [ ] **Step 3b: Thread param through generate_market_review** — 修改签名（约 541 行）与内部调用（约 557 行）：

```python
    def generate_market_review(self, overview: MarketOverview, news: List, indicators: Optional[Dict[str, Any]] = None) -> str:
```
```python
        prompt = self._build_review_prompt(overview, news, indicators)
```

- [ ] **Step 3c: Thread param through _build_review_prompt + insert block** — 修改签名（约 1111 行）：

```python
    def _build_review_prompt(self, overview: MarketOverview, news: List, indicators: Optional[Dict[str, Any]] = None) -> str:
```

在 **英文模板** 的 `{self._get_crypto_addendum_prompt(review_language)}`（约 1221 行）那一行之前插入一行：

```
{self._get_crypto_indicators_prompt_block(indicators, review_language)}
```

在 **中文模板** 的 `{self._get_crypto_addendum_prompt(review_language)}`（约 1286 行）那一行之前同样插入一行：

```
{self._get_crypto_indicators_prompt_block(indicators, review_language)}
```

- [ ] **Step 3d: Reorder _run_daily_review_parts** — 修改 `_run_daily_review_parts`（约 1438-1456 行），在报告生成之前取指标并把它分别传入报告与 payload：

```python
        # 1. 获取市场概览
        overview = self.get_market_overview()

        # 2. 搜索市场新闻
        news = self.search_market_news()

        # 2.5 crypto 大盘宏观指标（需在报告生成前取，以注入 prompt）
        indicators = self._get_crypto_market_indicators()

        # 3. 生成复盘报告（crypto 时 prompt 含宏观指标事实块）
        report = self.generate_market_review(overview, news, indicators)
        # crypto skips MarketLightSnapshot; build_market_review_payload also guards this
        # via `if self.region == "crypto": light = None` — keep both in sync when changing.
        snapshot = None if self.region == "crypto" else self.build_market_light_snapshot(overview)
        new_listings = self._get_crypto_new_listings()
        structured_payload = self.build_market_review_payload(
            overview,
            news,
            report,
            snapshot,
            new_listings=new_listings,
            market_indicators=indicators,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_prompt.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/market_analyzer.py tests/test_crypto_market_indicators_prompt.py
git commit -m "feat: crypto 大盘指标注入复盘 prompt（事实块 + 取数顺序前置）"
```

---

### Task 7: e2e — crypto 复盘 payload 含 market_indicators（离线，两源 mock）

**Files:**
- Test: `tests/test_crypto_market_indicators_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto_market_indicators_e2e.py
"""端到端（离线，无网络/无 LLM）：crypto 复盘 payload 经服务链路含 market_indicators。"""
import data_provider.crypto_market_indicators as cmi
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_crypto_review_payload_contains_market_indicators(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "true")
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {
        "btc_dominance": 56.07, "eth_dominance": 8.98,
        "total_market_cap_usd": 2241017397766.0, "market_cap_change_24h_pct": -0.64,
        "total_volume_usd": 91620725291.0,
    })
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {"value": 10, "classification": "Extreme Fear", "timestamp": 1})

    a = MarketAnalyzer(region="crypto")
    indicators = a._get_crypto_market_indicators()
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=indicators)

    mi = payload["market_indicators"]
    assert mi["btc_dominance"] == 56.07
    assert mi["total_market_cap_usd"] == 2241017397766.0
    assert mi["fear_greed"]["classification"] == "Extreme Fear"


def test_crypto_review_payload_omits_when_both_sources_empty(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_INDICATORS_ENABLED", "true")
    monkeypatch.setattr(cmi, "fetch_global_market", lambda: {})
    monkeypatch.setattr(cmi, "fetch_fear_greed", lambda: {})
    a = MarketAnalyzer(region="crypto")
    indicators = a._get_crypto_market_indicators()
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", market_indicators=indicators)
    assert "market_indicators" not in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_e2e.py -v`
Expected: 若 Task 5/6 已合入则可能直接 PASS；否则 FAIL。本任务确认端到端链路（钩子→服务→源 mock→payload presence-only）成立。

- [ ] **Step 3: (无需新实现)** 若 Step 2 已 PASS，跳过。若 FAIL，依报错回到对应 Task 修正（不在此任务新增生产代码）。

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_e2e.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_crypto_market_indicators_e2e.py
git commit -m "test: crypto 大盘指标端到端（payload 含 market_indicators，presence-only）"
```

---

### Task 8: Web — 类型 + 渲染指标条 + vitest

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts`（新增 `CryptoFearGreed`/`MarketIndicators`，`MarketReviewPayload.marketIndicators?`）
- Modify: `apps/dsa-web/src/components/report/MarketReviewReportView.tsx`（import、`StructuredMarketData` 字段、两处 mapping、i18n、渲染条）
- Test: `apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx`（追加用例）

- [ ] **Step 1: Write the failing test**（在现有测试文件末尾追加；沿用文件已有的 `render` 与 payload 构造工具）

```tsx
// 追加到 apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx
import { render, screen } from '@testing-library/react';
import { MarketReviewReportView } from '../MarketReviewReportView';

describe('MarketReviewReportView crypto market indicators', () => {
  const base = {
    version: 1, kind: 'market_review', region: 'crypto', title: '加密货币大盘复盘',
    indices: [{ code: 'BTC/USDT', name: 'BTC/USDT', current: 60000, changePct: 1.2, high: 61000, low: 59000 }],
  };

  it('renders indicators for crypto when present', () => {
    const payload = { ...base, marketIndicators: {
      btcDominance: 56.07, totalMarketCapUsd: 2241017397766,
      marketCapChange24hPct: -0.64,
      fearGreed: { value: 10, classification: 'Extreme Fear', timestamp: 1 },
    } };
    render(<MarketReviewReportView payload={payload as never} isLoading={false} />);
    expect(screen.getByText(/56\.07/)).toBeInTheDocument();
    expect(screen.getByText(/Extreme Fear/)).toBeInTheDocument();
  });

  it('does not render indicators when absent', () => {
    render(<MarketReviewReportView payload={base as never} isLoading={false} />);
    expect(screen.queryByText(/Extreme Fear/)).not.toBeInTheDocument();
  });
});
```

> 注：组件 props 名以现有测试文件首条用例为准（若现有用例传 `payload`/`isLoading` 之外参数，对齐之）。`as never` 仅为绕过严格类型，渲染逻辑不受影响。

- [ ] **Step 2: Run test to verify it fails**

Run（路径含空格，进入目录后用 node 直调 vitest）:
```bash
cd "apps/dsa-web" && node node_modules/vitest/vitest.mjs run src/components/report/__tests__/MarketReviewReportView.test.tsx
```
Expected: FAIL（指标文本未渲染）。

- [ ] **Step 3a: analysis.ts 类型** — 在 `apps/dsa-web/src/types/analysis.ts` 的 `NewListing` 接口（约 167 行结尾）之后新增：

```typescript
export interface CryptoFearGreed {
  value?: number;
  classification?: string;
  timestamp?: number;
}

export interface MarketIndicators {
  btcDominance?: number;
  ethDominance?: number;
  totalMarketCapUsd?: number;
  marketCapChange24hPct?: number;
  totalVolumeUsd?: number;
  fearGreed?: CryptoFearGreed;
}
```

并在 `MarketReviewPayload` 接口的 `newListings?: NewListing[];`（约 187 行）之后新增：

```typescript
  marketIndicators?: MarketIndicators;
```

- [ ] **Step 3b: 组件 import + 类型** — 在 `MarketReviewReportView.tsx` 顶部 import（约 9 行 `NewListing,`）追加 `MarketIndicators,`；在 `StructuredMarketData` 类型（约 48 行 `newListings?: NewListing[];`）之后新增：

```typescript
  marketIndicators?: MarketIndicators;
```

- [ ] **Step 3c: 两处 mapping** — 在 `MarketReviewReportView.tsx` 的 `markets` 分支映射（约 188 行 `newListings: marketPayload.newListings,`）之后新增：

```typescript
        marketIndicators: marketPayload.marketIndicators,
```
在单 payload 分支（约 202 行 `newListings: payload.newListings,`）之后新增：

```typescript
    marketIndicators: payload.marketIndicators,
```

- [ ] **Step 3d: i18n 文本** — 在 `MARKET_REVIEW_TEXT` 类型定义（约 224-227 行）追加键：

```typescript
  marketIndicators: string;
  btcDominance: string;
  ethDominance: string;
  totalMarketCap: string;
  fearGreed: string;
```

在 `zh` 块（`newListings: '新币与上新行情',` 等附近）追加：

```typescript
    marketIndicators: '加密市场指标',
    btcDominance: 'BTC 主导率',
    ethDominance: 'ETH 主导率',
    totalMarketCap: '加密总市值',
    fearGreed: '恐贪指数',
```

在 `en` 块（`newListings: 'New Listings',` 等附近）追加：

```typescript
    marketIndicators: 'Market Indicators',
    btcDominance: 'BTC Dominance',
    ethDominance: 'ETH Dominance',
    totalMarketCap: 'Total Market Cap',
    fearGreed: 'Fear & Greed',
```

- [ ] **Step 3e: 渲染指标条** — 在 `MarketReviewReportView.tsx` 的 breadth 条件块结尾（约 505 行 `))}`，即 `(marketData.region === 'crypto' ? null : (...))}` 之后）与 indices 表（约 506 行 `{marketData.indices.length > 0 ? (`）之间插入：

```tsx
                {marketData.region === 'crypto' && marketData.marketIndicators ? (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {marketData.marketIndicators.btcDominance !== undefined ? (
                      <div className="rounded-lg border border-subtle p-3">
                        <p className="label-uppercase">{marketReviewText.btcDominance}</p>
                        <p className="mt-1 font-semibold text-foreground">{marketData.marketIndicators.btcDominance}%</p>
                      </div>
                    ) : null}
                    {marketData.marketIndicators.ethDominance !== undefined ? (
                      <div className="rounded-lg border border-subtle p-3">
                        <p className="label-uppercase">{marketReviewText.ethDominance}</p>
                        <p className="mt-1 font-semibold text-foreground">{marketData.marketIndicators.ethDominance}%</p>
                      </div>
                    ) : null}
                    {marketData.marketIndicators.totalMarketCapUsd !== undefined ? (
                      <div className="rounded-lg border border-subtle p-3">
                        <p className="label-uppercase">{marketReviewText.totalMarketCap}</p>
                        <p className="mt-1 font-semibold text-foreground">
                          ${marketData.marketIndicators.totalMarketCapUsd.toLocaleString()}
                          {marketData.marketIndicators.marketCapChange24hPct !== undefined
                            ? ` (${marketData.marketIndicators.marketCapChange24hPct}%)`
                            : ''}
                        </p>
                      </div>
                    ) : null}
                    {marketData.marketIndicators.fearGreed && marketData.marketIndicators.fearGreed.value !== undefined ? (
                      <div className="rounded-lg border border-subtle p-3">
                        <p className="label-uppercase">{marketReviewText.fearGreed}</p>
                        <p className="mt-1 font-semibold text-foreground">
                          {marketData.marketIndicators.fearGreed.value}
                          {marketData.marketIndicators.fearGreed.classification
                            ? ` (${marketData.marketIndicators.fearGreed.classification})`
                            : ''}
                        </p>
                      </div>
                    ) : null}
                  </div>
                ) : null}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd "apps/dsa-web" && node node_modules/vitest/vitest.mjs run src/components/report/__tests__/MarketReviewReportView.test.tsx
```
Expected: PASS（含新增 2 用例与原有用例）。
再跑 eslint：`cd "apps/dsa-web" && node node_modules/eslint/bin/eslint.js src/components/report/MarketReviewReportView.tsx src/types/analysis.ts`
Expected: 无 error。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/types/analysis.ts apps/dsa-web/src/components/report/MarketReviewReportView.tsx apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx
git commit -m "feat: crypto 大盘指标 Web 渲染（指标条 + region 守卫 + presence-only）"
```

---

### Task 9: 文档 — crypto-guide + CHANGELOG

**Files:**
- Modify: `docs/crypto-guide.md`（新增「加密市场宏观指标」节；§9 已知限制移除「crypto 专属大盘指标」一项）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式）

- [ ] **Step 1: crypto-guide 新增节** — 在 `docs/crypto-guide.md` 的 §8/§9 之间（新币上新节之后、已知限制节之前）新增：

```markdown
## 加密市场宏观指标

加密货币大盘复盘在 `CRYPTO_MARKET_INDICATORS_ENABLED=true`（默认开启）时，额外抓取并展示宏观指标：

| 指标 | 来源 | payload 字段 |
|---|---|---|
| BTC / ETH 主导率 | CoinGecko `/global` | `btc_dominance` / `eth_dominance` |
| 加密总市值（含 24h 变化） | CoinGecko `/global` | `total_market_cap_usd` / `market_cap_change_24h_pct` |
| 总成交额（24h） | CoinGecko `/global` | `total_volume_usd` |
| 恐贪指数 | alternative.me `/fng` | `fear_greed: {value, classification, timestamp}` |

- 两源均免费、无需 API Key；超时/重试复用 `CRYPTO_FETCH_TIMEOUT_SECONDS` / `CRYPTO_FETCH_MAX_RETRIES`。
- **presence-only**：任一源或字段失败即省略，不塞 0、不编造；两源全失败时 payload 不含 `market_indicators`。
- 指标值同时注入复盘 prompt（「## 加密市场宏观指标」事实块），供 LLM 叙事引用市场情绪与结构。
- 仅 crypto 大盘复盘触发；A股/港股/美股不受影响。
```

- [ ] **Step 2: §9 移除已实现项** — 在 `docs/crypto-guide.md` §9「已知限制（后续阶段）」中删除这一行：

```markdown
- crypto 专属大盘指标（BTC 主导率 / 总市值 / 恐贪指数，需引入新数据源）。
```

- [ ] **Step 3: CHANGELOG** — 在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段（扁平格式，独立一行）新增：

```markdown
- [新功能] 加密货币大盘复盘新增宏观指标（BTC/ETH 主导率、加密总市值含 24h 变化、恐贪指数；CoinGecko /global + alternative.me，免费无 key，presence-only，默认开，注入复盘 prompt）
```

- [ ] **Step 4: 核对** — 确认命令/配置项/文件名与实仓一致（`CRYPTO_MARKET_INDICATORS_ENABLED`、字段名、源 URL）。本任务 docs only，tests not run。

- [ ] **Step 5: Commit**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 加密市场宏观指标指南与 CHANGELOG（§9 移除已实现项）"
```

---

### Task 10: 收尾 — 全量离线回归 + ci_gate

**Files:** 无（仅验证）

- [ ] **Step 1: 全量离线回归**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`
Expected: 全绿（在 new-listings 基线 2842 之上新增本特性用例后仍 0 failed）。

- [ ] **Step 2: ci_gate**

Run: `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh`
Expected: `all checks passed`。

- [ ] **Step 3: (无 commit)** 验证任务，不产生代码改动。若回归暴露问题，回对应 Task 修正后重跑。

---

## Self-Review

**1. Spec coverage（逐节核对 spec → task）：**
- §2.1 data_provider 两源 → Task 1（global）、Task 2（fng）✓
- §2.2 service collect 合并/presence-only/disabled → Task 3 ✓
- §2.3 region 钩子 + payload 字段 → Task 5；prompt 注入 → Task 6 ✓
- §2.4 配置（field/from_env/registry/settingsHelp/.env）→ Task 4 ✓（含双向 help-key 契约测试）
- §2.5 Web 类型 + 渲染 → Task 8 ✓
- §2.6 文档 → Task 9 ✓
- §3 数据流（取数前置）→ Task 6 Step 3d ✓
- §4 错误处理（降级）→ Task 1/2（源内 try/except）、Task 3/5（链路降级）✓
- §5 payload 契约 → Task 5/7 ✓
- §6 测试矩阵 → Task 1-3/5-8 全覆盖 + Task 10 回归 ✓
- §7 分支/回滚 → header + 各 Task commit ✓
- §8 范围边界（单一 enable 旗标、不动表格注入）→ 遵循 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个 code step 均含完整代码与确切命令。Task 7 Step 3「无需新实现」是有意的端到端验证任务（依赖前序生产代码），非占位。

**3. Type/signature consistency：**
- `fetch_global_market()` / `fetch_fear_greed()` / `CryptoMarketIndicatorService.collect()` 在 Task 3/5/7 引用一致。
- payload 字段 `market_indicators`（snake）↔ Web `marketIndicators`（camel，`camelcase-keys` 自动转）；子字段 `fear_greed`↔`fearGreed`、`total_market_cap_usd`↔`totalMarketCapUsd`、`market_cap_change_24h_pct`↔`marketCapChange24hPct` 一致。
- `_get_crypto_market_indicators`（Task 5）/ `_get_crypto_indicators_prompt_block`（Task 6）命名在 payload/prompt/test 中一致。
- `build_market_review_payload(..., market_indicators=None)` 与 `generate_market_review(..., indicators=None)` / `_build_review_prompt(..., indicators=None)` 参数贯通一致。
