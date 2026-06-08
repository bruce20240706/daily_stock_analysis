# 结构化新币上新发现 + 上新行情表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 crypto 大盘复盘新增结构化新币发现——自动识别近期新上线现货币种、附行情，产出 payload 的 additive `new_listings` 字段与 Web "上新行情表"。

**Architecture:** 分层——`data_provider/crypto_new_listings.py` 放**纯抓取**函数（OKX `listTime` / Coinbase `new_at` / Binance spot symbol 集，无 DB、无 src 依赖）；`src/services/crypto_new_listing_service.py` 做**编排**（读 config、Binance base-asset 差分、去重、窗口、富化）；`src/repositories/crypto_listing_repo.py` + `src/storage.py` 新表做 Binance 差分持久化；`src/market_analyzer.py` 把结果以 additive 字段挂入 crypto 复盘 payload；Web 渲染上新行情表。Binance 默认关（每日 CI 临时 DB 无输出），OKX+Coinbase 无状态默认开。

**Tech Stack:** Python（pytest）、SQLAlchemy（`Base.metadata.create_all`，非 Alembic）、FastAPI、React+TS（vitest）。

**对应设计：** `docs/superpowers/specs/2026-06-08-crypto-new-listings-design.md`
**基线分支：** 从 `feat/crypto-market-review` 切出（本特性扩展该复盘）。

**全局验证：** venv 仅 `.venv/bin/python`（无 `python`）；后端测试 `PYTHONPATH="$PWD" .venv/bin/python -m pytest <file> -q`；工作区路径含空格，前端完整 build/vitest 走无空格副本或交 CI。每任务 TDD red→green→commit；commit 英文类型 + 中文描述，无 `Co-Authored-By`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `data_provider/crypto_new_listings.py` | 纯抓取：NewListing + OKX/Coinbase/Binance fetchers + http helper | Create |
| `src/storage.py` | 新增 `CryptoSymbolSnapshot` model（差分快照） | Modify |
| `src/repositories/crypto_listing_repo.py` | 快照读写 repo | Create |
| `src/services/crypto_new_listing_service.py` | 编排：config/差分/去重/窗口/富化 | Create |
| `src/config.py` | 4 个新 env 字段 + from_env | Modify |
| `src/core/config_registry.py` | 4 个 registry 条目 | Modify |
| `src/market_analyzer.py` | `_get_crypto_new_listings` + payload additive + 主流程接线 | Modify |
| `apps/dsa-web/src/types/analysis.ts` | `NewListing` 类型 + `newListings?` | Modify |
| `apps/dsa-web/src/components/report/MarketReviewReportView.tsx` | 上新行情表 + i18n | Modify |
| `.env.example` / `docs/crypto-guide.md` / `docs/CHANGELOG.md` | 配置与文档 | Modify |
| `tests/test_crypto_new_listings_*.py` | 单测 + e2e | Create |

---

## Task 1: 配置 — 4 个新 env

**Files:**
- Modify: `src/config.py`（字段定义区，crypto 字段附近 ~line 926-930；`from_env`/`_load_from_env` crypto 段 ~line 1758-1764）
- Modify: `src/core/config_registry.py`（MARKET_REVIEW / crypto 配置区）
- Test: `tests/test_crypto_new_listings_config.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listings_config.py
from src.config import Config


def test_new_listing_defaults(monkeypatch):
    for k in ("CRYPTO_NEW_LISTING_ENABLED", "CRYPTO_NEW_LISTING_WINDOW_DAYS",
              "CRYPTO_NEW_LISTING_SOURCES", "CRYPTO_NEW_LISTING_MAX"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_enabled is True
    assert cfg.crypto_new_listing_window_days == 7
    assert cfg.crypto_new_listing_sources == "okx,coinbase"   # 默认不含 binance
    assert cfg.crypto_new_listing_max == 20


def test_new_listing_env_override(monkeypatch):
    monkeypatch.setenv("CRYPTO_NEW_LISTING_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_WINDOW_DAYS", "14")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_SOURCES", "okx,coinbase,binance")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_MAX", "5")
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_enabled is False
    assert cfg.crypto_new_listing_window_days == 14
    assert cfg.crypto_new_listing_sources == "okx,coinbase,binance"
    assert cfg.crypto_new_listing_max == 5


def test_new_listing_invalid_int_falls_back(monkeypatch):
    monkeypatch.setenv("CRYPTO_NEW_LISTING_WINDOW_DAYS", "notanint")
    monkeypatch.setenv("CRYPTO_NEW_LISTING_MAX", "")
    cfg = Config._load_from_env()
    assert cfg.crypto_new_listing_window_days == 7
    assert cfg.crypto_new_listing_max == 20
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_config.py -q`
Expected: FAIL（属性不存在）

- [ ] **Step 3: 加字段** — 在 `src/config.py` crypto 字段（`crypto_market_review_symbols` 附近）后加：
```python
    crypto_new_listing_enabled: bool = True
    crypto_new_listing_window_days: int = 7
    crypto_new_listing_sources: str = "okx,coinbase"
    crypto_new_listing_max: int = 20
```

- [ ] **Step 4: from_env 读取（带健壮解析）** — 在 `_load_from_env` 的 crypto 段（`crypto_market_review_symbols=...` 附近）加：
```python
            crypto_new_listing_enabled=os.getenv('CRYPTO_NEW_LISTING_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'on'),
            crypto_new_listing_window_days=cls._safe_int(os.getenv('CRYPTO_NEW_LISTING_WINDOW_DAYS'), 7),
            crypto_new_listing_sources=os.getenv('CRYPTO_NEW_LISTING_SOURCES', 'okx,coinbase'),
            crypto_new_listing_max=cls._safe_int(os.getenv('CRYPTO_NEW_LISTING_MAX'), 20),
```
若 `Config` 无 `_safe_int` 辅助，则在类上加一个（紧邻其它 `_parse_*` classmethod）：
```python
    @staticmethod
    def _safe_int(raw, default: int) -> int:
        try:
            v = int(str(raw).strip())
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default
```

- [ ] **Step 5: config_registry 条目** — 在 `src/core/config_registry.py` 的 crypto / market-review 配置区，按现有条目结构追加 4 条（键、类型、默认、描述、分组与现有 `CRYPTO_*`/`MARKET_REVIEW_*` 一致）。READ 一条现有 `CRYPTO_*` 条目作模板，保持字段齐全（`key`/`type`/`default`/`description`/分组）。`CRYPTO_NEW_LISTING_SOURCES` 描述写明默认不含 binance（需持久卷才生效）。

- [ ] **Step 6: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_config.py -q`
Expected: PASS（3 passed）

- [ ] **Step 7: Commit**
```bash
git add src/config.py src/core/config_registry.py tests/test_crypto_new_listings_config.py
git commit -m "feat: 新币上新配置 — 4 个 CRYPTO_NEW_LISTING_* env（binance 默认关）"
```

---

## Task 2: data_provider/crypto_new_listings.py — NewListing + http helper + OKX fetcher

**Files:**
- Create: `data_provider/crypto_new_listings.py`
- Test: `tests/test_crypto_new_listings_okx.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listings_okx.py
import data_provider.crypto_new_listings as nl


def test_okx_parses_listtime_within_window(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    sample = {"data": [
        {"instId": "NEW-USDT", "baseCcy": "NEW", "quoteCcy": "USDT", "state": "live", "listTime": str(now - 2 * day)},
        {"instId": "OLD-USDT", "baseCcy": "OLD", "quoteCcy": "USDT", "state": "live", "listTime": str(now - 30 * day)},
        {"instId": "PRE-USDT", "baseCcy": "PRE", "quoteCcy": "USDT", "state": "preopen", "listTime": str(now - 1 * day)},
        {"instId": "FUT-USDT", "baseCcy": "FUT", "quoteCcy": "USDT", "state": "live", "listTime": str(now + 5 * day)},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_okx_instruments(window_days=7, now_ms=now)
    bases = {r.base for r in out}
    assert bases == {"NEW"}                 # OLD 超窗口、PRE 非 live、FUT 未来 listTime 全部排除
    assert out[0].exchange == "okx" and out[0].listed_at == now - 2 * day


def test_okx_fetch_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(nl, "_http_get_json", boom)
    assert nl.fetch_okx_instruments(7, 1_000_000_000_000) == []
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_okx.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 建模块（NewListing + http helper + OKX）**
```python
# data_provider/crypto_new_listings.py
"""数字货币新上线发现（纯抓取，免 API Key）。

分层纪律：本模块不 import src.*、不碰 DB；差分/持久化/去重/富化/特性配置在 src 服务层。
仅读取通用传输旋钮 CRYPTO_FETCH_TIMEOUT_SECONDS / CRYPTO_FETCH_MAX_RETRIES（与 crypto_base 一致）。
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"


@dataclass
class NewListing:
    base: str
    quote: str
    symbol: str
    exchange: str
    listed_at: Optional[int]  # epoch ms；Binance 差分无上市时间时为 None
    source: str


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


def _http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None):
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


def fetch_okx_instruments(window_days: int, now_ms: int) -> List[NewListing]:
    """OKX 现货 instruments：state=live 且 listTime 落在 (now-window, now]（排除未来）。"""
    out: List[NewListing] = []
    try:
        data = _http_get_json(OKX_INSTRUMENTS_URL, {"instType": "SPOT"})
    except Exception as e:
        logger.warning("[新上新-OKX] 抓取失败: %s", e)
        return out
    lo = now_ms - window_days * _DAY_MS
    for it in (data or {}).get("data", []) or []:
        if it.get("state") != "live":
            continue
        try:
            lt_ms = int(it.get("listTime"))
        except (TypeError, ValueError):
            continue
        if not (lo < lt_ms <= now_ms):
            continue
        base = (it.get("baseCcy") or "").upper()
        quote = (it.get("quoteCcy") or "").upper()
        if not base or not quote:
            continue
        out.append(NewListing(base=base, quote=quote,
                              symbol=it.get("instId") or f"{base}-{quote}",
                              exchange="okx", listed_at=lt_ms, source="okx"))
    return out
```

- [ ] **Step 4: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_okx.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add data_provider/crypto_new_listings.py tests/test_crypto_new_listings_okx.py
git commit -m "feat: 新币上新 — NewListing 与 OKX instruments 纯抓取（listTime 窗口过滤）"
```

---

## Task 3: Coinbase fetcher

**Files:**
- Modify: `data_provider/crypto_new_listings.py`
- Test: `tests/test_crypto_new_listings_coinbase.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listings_coinbase.py
from datetime import datetime, timezone, timedelta
import data_provider.crypto_new_listings as nl


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_coinbase_new_at_window_and_dedup(monkeypatch):
    now = datetime(2026, 6, 8, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    sample = {"products": [
        {"product_id": "NEW-USD", "base_currency_id": "NEW", "quote_currency_id": "USD", "status": "online", "new_at": _iso(now - timedelta(days=2))},
        {"product_id": "NEW-USDC", "base_currency_id": "NEW", "quote_currency_id": "USDC", "status": "online", "new_at": _iso(now - timedelta(days=2))},
        {"product_id": "OLD-USD", "base_currency_id": "OLD", "quote_currency_id": "USD", "status": "online", "new_at": _iso(now - timedelta(days=40))},
        {"product_id": "NONE-USD", "base_currency_id": "NONE", "quote_currency_id": "USD", "status": "online", "new_at": None},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_coinbase_products(window_days=7, now_ms=now_ms)
    bases = [r.base for r in out]
    assert bases == ["NEW"]                  # OLD 超窗口、NONE 无 new_at；NEW 多对去重为一条
    assert out[0].exchange == "coinbase"


def test_coinbase_error_returns_empty(monkeypatch):
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert nl.fetch_coinbase_products(7, 1) == []
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_coinbase.py -q`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现（追加到模块）**
```python
COINBASE_PRODUCTS_URL = "https://api.coinbase.com/api/v3/brokerage/market/products"
_COINBASE_UA = {"User-Agent": "dsa-market-review/1.0"}


def _iso_to_ms(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def fetch_coinbase_products(window_days: int, now_ms: int) -> List[NewListing]:
    """Coinbase Advanced Trade products：new_at 落在窗口内，按 base_currency_id 去重。"""
    out: List[NewListing] = []
    try:
        data = _http_get_json(COINBASE_PRODUCTS_URL, headers=_COINBASE_UA)
    except Exception as e:
        logger.warning("[新上新-Coinbase] 抓取失败: %s", e)
        return out
    lo = now_ms - window_days * _DAY_MS
    seen_base = set()
    for p in (data or {}).get("products", []) or []:
        ms = _iso_to_ms(p.get("new_at"))
        if ms is None or not (lo < ms <= now_ms):
            continue
        base = (p.get("base_currency_id") or "").upper()
        quote = (p.get("quote_currency_id") or "").upper()
        if not base or base in seen_base:
            continue
        seen_base.add(base)
        out.append(NewListing(base=base, quote=quote,
                              symbol=p.get("product_id") or f"{base}-{quote}",
                              exchange="coinbase", listed_at=ms, source="coinbase"))
    return out
```

- [ ] **Step 4: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_coinbase.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add data_provider/crypto_new_listings.py tests/test_crypto_new_listings_coinbase.py
git commit -m "feat: 新币上新 — Coinbase products 纯抓取（new_at 窗口 + base 去重）"
```

---

## Task 4: Binance spot base-asset 抓取（纯，返回集合）

**Files:**
- Modify: `data_provider/crypto_new_listings.py`
- Test: `tests/test_crypto_new_listings_binance.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listings_binance.py
import data_provider.crypto_new_listings as nl


def test_binance_base_assets_filters_trading(monkeypatch):
    sample = {"symbols": [
        {"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
        {"symbol": "AAABTC", "baseAsset": "AAA", "quoteAsset": "BTC", "status": "TRADING", "isSpotTradingAllowed": True},
        {"symbol": "BBBUSDT", "baseAsset": "BBB", "quoteAsset": "USDT", "status": "BREAK", "isSpotTradingAllowed": True},
        {"symbol": "CCCUSDT", "baseAsset": "CCC", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": False},
    ]}
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: sample)
    out = nl.fetch_binance_spot_base_assets()
    assert set(out.keys()) == {"AAA"}        # BREAK 与非 spot 排除
    assert out["AAA"][1] == "USDT"           # 代表对优先 USDT


def test_binance_error_returns_empty(monkeypatch):
    monkeypatch.setattr(nl, "_http_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("451")))
    assert nl.fetch_binance_spot_base_assets() == {}
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_binance.py -q`
Expected: FAIL

- [ ] **Step 3: 实现（追加到模块）**
```python
BINANCE_EXCHANGEINFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"  # .vision 避 451


def fetch_binance_spot_base_assets() -> Dict[str, Tuple[str, str]]:
    """当前 Binance spot 在交易的 baseAsset → (代表 symbol, quote)，代表对优先 *USDT。

    纯抓取——不做差分、不碰 DB（差分在 src 服务层）。失败返回 {}。
    """
    out: Dict[str, Tuple[str, str]] = {}
    try:
        data = _http_get_json(BINANCE_EXCHANGEINFO_URL)
    except Exception as e:
        logger.warning("[新上新-Binance] exchangeInfo 抓取失败: %s", e)
        return out
    for s in (data or {}).get("symbols", []) or []:
        if s.get("status") != "TRADING" or not s.get("isSpotTradingAllowed"):
            continue
        base = (s.get("baseAsset") or "").upper()
        quote = (s.get("quoteAsset") or "").upper()
        if not base or not quote:
            continue
        if base not in out or quote == "USDT":   # 代表对优先 USDT
            out[base] = (s.get("symbol") or f"{base}{quote}", quote)
    return out
```

- [ ] **Step 4: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_binance.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**
```bash
git add data_provider/crypto_new_listings.py tests/test_crypto_new_listings_binance.py
git commit -m "feat: 新币上新 — Binance spot base-asset 纯抓取（.vision 避 451）"
```

---

## Task 5: 快照持久化 — model + repo

**Files:**
- Modify: `src/storage.py`（紧邻其它 model，如 `AlertTriggerRecord` 之后；`Base.metadata.create_all` 会自动建表）
- Create: `src/repositories/crypto_listing_repo.py`
- Test: `tests/test_crypto_listing_repo.py`（Create）

- [ ] **Step 1: 先调研测试 DB 隔离方式** — READ `tests/` 中现有 repo 测试（如 `tests/test_alert_repo*.py` 或 `tests/test_analysis_repo*.py` / `conftest.py`），确认如何拿到隔离的 `DatabaseManager`（临时 sqlite 文件 / fixture）。下方测试按该模式构造 `db`。若发现现成 fixture，复用它替换下面的 `_tmp_db`。

- [ ] **Step 2: 写失败测试**
```python
# tests/test_crypto_listing_repo.py
from src.repositories.crypto_listing_repo import CryptoListingRepository


def _repo(tmp_path, monkeypatch):
    # 用临时 sqlite 隔离；若仓库已有 repo 测试 fixture，改用之
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    from src.storage import DatabaseManager
    DatabaseManager._instance = None  # reset singleton for isolated DB（若 fixture 已处理则删此行）
    return CryptoListingRepository(db_manager=DatabaseManager.get_instance())


def test_snapshot_roundtrip(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    assert repo.get_base_assets("binance") is None      # 无快照
    repo.save_base_assets("binance", {"AAA", "BBB"})
    assert repo.get_base_assets("binance") == {"AAA", "BBB"}
    repo.save_base_assets("binance", {"AAA", "CCC"})     # 覆盖
    assert repo.get_base_assets("binance") == {"AAA", "CCC"}
```
> 注：`DatabaseManager` 是单例（metaclass）。若 Step 1 发现的 fixture 已能隔离 DB，请用 fixture，不要手动重置 `_instance`。这里给出的 reset 仅为无 fixture 时的兜底，实现者须按实际单例机制调整（可能是 `DatabaseManager._instances`/类属性名不同——按 `src/storage.py:767-775` 的 metaclass 实际字段改）。

- [ ] **Step 3: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_listing_repo.py -q`
Expected: FAIL（model/repo 不存在）

- [ ] **Step 4: 加 model** — `src/storage.py`，在 `AlertTriggerRecord` 之后：
```python
class CryptoSymbolSnapshot(Base):
    """Per-exchange spot base-asset snapshot for new-listing diff（缓存性质，非业务数据）。"""

    __tablename__ = 'crypto_symbol_snapshots'

    exchange = Column(String(32), primary_key=True)
    base_assets = Column(Text, nullable=False)   # JSON array of base asset strings
    captured_at = Column(DateTime, default=datetime.now)
```
（`Column/String/Text/DateTime/datetime` 在 storage.py 已 import；`Base.metadata.create_all(self._engine)` 会自动建该表。）

- [ ] **Step 5: 加 repo**
```python
# src/repositories/crypto_listing_repo.py
# -*- coding: utf-8 -*-
"""crypto 新上线快照仓储（Binance 差分用）。"""
import json
import logging
from datetime import datetime
from typing import Optional, Set

from src.storage import DatabaseManager, CryptoSymbolSnapshot

logger = logging.getLogger(__name__)


class CryptoListingRepository:
    """读写每交易所 spot base-asset 快照。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def get_base_assets(self, exchange: str) -> Optional[Set[str]]:
        with self.db.get_session() as session:
            row = session.get(CryptoSymbolSnapshot, exchange)
            if row is None:
                return None
            try:
                return set(json.loads(row.base_assets))
            except (TypeError, ValueError):
                return set()

    def save_base_assets(self, exchange: str, bases: Set[str]) -> None:
        payload = json.dumps(sorted(bases))
        with self.db.get_session() as session:
            row = session.get(CryptoSymbolSnapshot, exchange)
            if row is None:
                session.add(CryptoSymbolSnapshot(exchange=exchange, base_assets=payload, captured_at=datetime.now()))
            else:
                row.base_assets = payload
                row.captured_at = datetime.now()
            session.commit()
```

- [ ] **Step 6: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_listing_repo.py -q`
Expected: PASS（1 passed）

- [ ] **Step 7: Commit**
```bash
git add src/storage.py src/repositories/crypto_listing_repo.py tests/test_crypto_listing_repo.py
git commit -m "feat: 新币上新 — CryptoSymbolSnapshot 表与仓储（Binance 差分持久化）"
```

---

## Task 6: 服务层 — discover 编排（OKX+Coinbase，去重/窗口/截断，暂不含 Binance/富化）

**Files:**
- Create: `src/services/crypto_new_listing_service.py`
- Test: `tests/test_crypto_new_listing_service.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listing_service.py
import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


def _cfg(**kw):
    c = Config._load_from_env()
    c.crypto_new_listing_enabled = kw.get("enabled", True)
    c.crypto_new_listing_window_days = kw.get("window", 7)
    c.crypto_new_listing_sources = kw.get("sources", "okx,coinbase")
    c.crypto_new_listing_max = kw.get("max", 20)
    return c


def test_disabled_returns_empty():
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(enabled=False))
    assert svc.discover(now_ms=1_000) == []


def test_merge_dedup_by_base(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("NEW", "USDT", "NEW-USDT", "okx", now - day, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products",
                        lambda w, n: [NewListing("NEW", "USD", "NEW-USD", "coinbase", now - day, "coinbase")])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg())
    out = svc.discover(now_ms=now)
    assert len(out) == 1                       # 同 base 合并
    assert set(out[0]["exchanges"]) == {"okx", "coinbase"}
    assert out[0]["listed_at"] == now - day


def test_collision_split_when_far_apart(monkeypatch):
    now = 1_000_000_000_000
    day = 86_400_000
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("X", "USDT", "X-USDT", "okx", now - day, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products",
                        lambda w, n: [NewListing("X", "USD", "X-USD", "coinbase", now - 100 * day, "coinbase")])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(window=200))
    out = svc.discover(now_ms=now)
    assert len(out) == 2                        # 同名但上市时间差太远→不合并


def test_max_cap(monkeypatch):
    now = 1_000_000_000_000
    items = [NewListing(f"C{i}", "USDT", f"C{i}-USDT", "okx", now - i, "okx") for i in range(5)]
    monkeypatch.setattr(nl, "fetch_okx_instruments", lambda w, n: items)
    monkeypatch.setattr(nl, "fetch_coinbase_products", lambda w, n: [])
    svc = CryptoNewListingService(data_manager=None, repo=None, config=_cfg(max=2))
    out = svc.discover(now_ms=now)
    assert len(out) == 2
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_service.py -q`
Expected: FAIL（服务不存在）

- [ ] **Step 3: 实现服务（先不含 binance/富化）**
```python
# src/services/crypto_new_listing_service.py
# -*- coding: utf-8 -*-
"""crypto 新上线发现服务（编排层）。

职责：读 config、调度纯抓取源、Binance base-asset 差分（Task 7）、按 base 去重 +
同名碰撞防护 + 窗口/截断、行情富化（Task 8）。data_provider 保持纯抓取。
"""
import logging
import time
from typing import Any, Dict, List, Optional

import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.config import get_config

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000
COLLISION_MERGE_HOURS = 72   # 跨所同 base 上市时间在此窗口内才合并，否则视为可能不同项目


class CryptoNewListingService:
    def __init__(self, data_manager=None, repo=None, config=None):
        self.config = config or get_config()
        self.data_manager = data_manager   # DataFetcherManager，用于富化（Task 8）
        self._repo = repo                   # CryptoListingRepository（Task 7），惰性

    def discover(self, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        if not getattr(self.config, "crypto_new_listing_enabled", True):
            return []
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        window = int(getattr(self.config, "crypto_new_listing_window_days", 7))
        sources = [s.strip().lower() for s in (getattr(self.config, "crypto_new_listing_sources", "") or "").split(",") if s.strip()]

        records: List[NewListing] = []
        if "okx" in sources:
            records += nl.fetch_okx_instruments(window, now)
        if "coinbase" in sources:
            records += nl.fetch_coinbase_products(window, now)
        if "binance" in sources:
            records += self._binance_new(now)            # Task 7

        merged = self._dedupe_by_base(records, now)
        merged.sort(key=lambda r: r["_sort"], reverse=True)
        max_n = int(getattr(self.config, "crypto_new_listing_max", 20))
        capped = merged[:max_n]
        dropped = len(merged) - len(capped)
        if dropped > 0:
            logger.info("[新上新] 截断 %d 条（max=%d）", dropped, max_n)
        return self._enrich(capped)                       # Task 8

    def _binance_new(self, now_ms: int) -> List[NewListing]:   # 占位，Task 7 实现
        return []

    def _dedupe_by_base(self, records: List[NewListing], now_ms: int) -> List[Dict[str, Any]]:
        by_base: Dict[str, List[NewListing]] = {}
        for r in records:
            by_base.setdefault(r.base, []).append(r)
        out: List[Dict[str, Any]] = []
        for base, group in by_base.items():
            natives = sorted([r for r in group if r.listed_at is not None], key=lambda r: r.listed_at)
            if natives:
                anchor = natives[0].listed_at
                close = [r for r in group if r.listed_at is None or abs(r.listed_at - anchor) <= COLLISION_MERGE_HOURS * _HOUR_MS]
                far = [r for r in group if r.listed_at is not None and abs(r.listed_at - anchor) > COLLISION_MERGE_HOURS * _HOUR_MS]
                out.append(self._merge(base, close, now_ms))
                for r in far:
                    out.append(self._merge(base, [r], now_ms))
            else:
                out.append(self._merge(base, group, now_ms))
        return out

    @staticmethod
    def _merge(base: str, group: List[NewListing], now_ms: int) -> Dict[str, Any]:
        natives = [r.listed_at for r in group if r.listed_at is not None]
        listed_at = min(natives) if natives else None        # 优先真实原生时间戳
        quote = next((r.quote for r in group if r.quote), "USDT")
        return {
            "base": base,
            "exchanges": sorted({r.exchange for r in group}),
            "pairs": sorted({r.symbol for r in group}),
            "listed_at": listed_at,
            "quote": quote,
            "_sort": listed_at if listed_at is not None else now_ms,
        }

    def _enrich(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:   # 占位，Task 8 实现
        out = []
        for it in items:
            rec = {k: v for k, v in it.items() if k != "_sort" and not (k == "listed_at" and v is None)}
            rec.pop("quote", None)
            out.append(rec)
        return out
```

- [ ] **Step 4: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_service.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**
```bash
git add src/services/crypto_new_listing_service.py tests/test_crypto_new_listing_service.py
git commit -m "feat: 新币上新服务 — OKX+Coinbase 聚合/按base去重/同名碰撞防护/窗口截断"
```

---

## Task 7: 服务层 — Binance base-asset 差分 + 持久化

**Files:**
- Modify: `src/services/crypto_new_listing_service.py`（实现 `_binance_new`）
- Test: `tests/test_crypto_new_listing_binance_diff.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listing_binance_diff.py
import data_provider.crypto_new_listings as nl
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


class _FakeRepo:
    def __init__(self, prior=None):
        self._prior = prior
        self.saved = None
    def get_base_assets(self, exchange):
        return self._prior
    def save_base_assets(self, exchange, bases):
        self.saved = set(bases)


def _cfg():
    c = Config._load_from_env()
    c.crypto_new_listing_sources = "binance"
    return c


def test_binance_first_run_seeds_no_report(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets", lambda: {"AAA": ("AAAUSDT", "USDT")})
    repo = _FakeRepo(prior=None)
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    out = svc.discover(now_ms=1_000)
    assert out == []                       # 首跑无基线 → 不报
    assert repo.saved == {"AAA"}           # 播种基线


def test_binance_reports_only_new_base(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets",
                        lambda: {"AAA": ("AAAUSDT", "USDT"), "NEW": ("NEWUSDT", "USDT")})
    repo = _FakeRepo(prior={"AAA"})
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    out = svc.discover(now_ms=1_000)
    assert [r["base"] for r in out] == ["NEW"]   # 仅全新 base；AAA 既有计价对不误报
    assert repo.saved == {"AAA", "NEW"}


def test_binance_fetch_empty_no_crash_no_save(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets", lambda: {})
    repo = _FakeRepo(prior={"AAA"})
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    assert svc.discover(now_ms=1_000) == []
    assert repo.saved is None              # 抓取失败不覆盖基线
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_binance_diff.py -q`
Expected: FAIL（`_binance_new` 是占位返回 []）

- [ ] **Step 3: 实现 `_binance_new`（替换 Task 6 的占位）**
```python
    def _binance_new(self, now_ms: int) -> List[NewListing]:
        current = nl.fetch_binance_spot_base_assets()
        if not current:                       # 抓取失败/空：不动基线、不报
            return []
        repo = self._repo
        if repo is None:
            from src.repositories.crypto_listing_repo import CryptoListingRepository
            repo = CryptoListingRepository()
        prior = repo.get_base_assets("binance")
        repo.save_base_assets("binance", set(current.keys()))
        if prior is None:
            logger.info("[新上新-Binance] 首次播种基线 %d 个 base，本次不报新上", len(current))
            return []
        new_bases = set(current.keys()) - prior
        out: List[NewListing] = []
        for base in sorted(new_bases):
            symbol, quote = current[base]
            out.append(NewListing(base=base, quote=quote, symbol=symbol,
                                  exchange="binance", listed_at=None, source="binance"))
        return out
```

- [ ] **Step 4: 跑测试确认通过**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_binance_diff.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add src/services/crypto_new_listing_service.py tests/test_crypto_new_listing_binance_diff.py
git commit -m "feat: 新币上新 — Binance base-asset 差分 + 首跑播种基线"
```

---

## Task 8: 服务层 — 行情富化（presence-only）

**Files:**
- Modify: `src/services/crypto_new_listing_service.py`（实现 `_enrich`）
- Test: `tests/test_crypto_new_listing_enrich.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listing_enrich.py
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


class _Q:
    def __init__(self, price, change_pct=None, volume=None):
        self.price = price
        self.change_pct = change_pct
        self.volume = volume


class _FakeMgr:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []
    def get_realtime_quote(self, code, log_final_failure=True):
        self.calls.append(code)
        return self.mapping.get(code)


def _cfg():
    return Config._load_from_env()


def test_enrich_presence_only():
    items = [
        {"base": "AAA", "exchanges": ["okx"], "pairs": ["AAA-USDT"], "listed_at": 123, "quote": "USDT", "_sort": 123},
        {"base": "BBB", "exchanges": ["binance"], "pairs": ["BBBUSDT"], "listed_at": None, "quote": "USDT", "_sort": 9},
    ]
    mgr = _FakeMgr({"AAA/USDT": _Q(1.5, 2.0, 1000.0)})   # BBB 无行情
    svc = CryptoNewListingService(data_manager=mgr, repo=None, config=_cfg())
    out = svc._enrich(items)
    a = next(r for r in out if r["base"] == "AAA")
    assert a["price"] == 1.5 and a["change_pct"] == 2.0 and a["quote_pair"] == "AAA/USDT"
    assert a["listed_at"] == 123 and "_sort" not in a and "quote" not in a
    b = next(r for r in out if r["base"] == "BBB")
    assert "price" not in b and "listed_at" not in b      # 无行情→omit 价格；listed_at None→omit
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_enrich.py -q`
Expected: FAIL（占位 `_enrich` 不富化价格）

- [ ] **Step 3: 实现 `_enrich`（替换 Task 6 的占位）**
```python
    _SUPPORTED_QUOTES = {"USDT", "USDC", "USD", "BUSD", "BTC", "ETH"}

    def _enrich(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for it in items:
            rec: Dict[str, Any] = {"base": it["base"], "exchanges": it["exchanges"], "pairs": it["pairs"]}
            if it.get("listed_at") is not None:
                rec["listed_at"] = it["listed_at"]
            quote = it.get("quote") or "USDT"
            if quote not in self._SUPPORTED_QUOTES:
                quote = "USDT"
            code = f'{it["base"]}/{quote}'
            if self.data_manager is not None:
                try:
                    q = self.data_manager.get_realtime_quote(code, log_final_failure=False)
                except Exception as e:
                    logger.info("[新上新] %s 富化失败: %s", code, e)
                    q = None
                if q is not None and getattr(q, "price", None) is not None:
                    rec["quote_pair"] = code
                    rec["price"] = float(q.price)
                    if getattr(q, "change_pct", None) is not None:
                        rec["change_pct"] = float(q.change_pct)
                    if getattr(q, "volume", None) is not None:
                        rec["volume"] = float(q.volume)
            out.append(rec)
        return out
```

- [ ] **Step 4: 跑测试确认通过 + 服务全量回归**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listing_service.py tests/test_crypto_new_listing_binance_diff.py tests/test_crypto_new_listing_enrich.py -q`
Expected: PASS（全绿；注意 Task 6 的 `test_merge_dedup_by_base` 现在 data_manager=None → 不富化价格，仍通过，因为它只断言 exchanges/listed_at）

- [ ] **Step 5: Commit**
```bash
git add src/services/crypto_new_listing_service.py tests/test_crypto_new_listing_enrich.py
git commit -m "feat: 新币上新 — 行情富化（复用 get_realtime_quote，presence-only）"
```

---

## Task 9: market_analyzer 接线 — payload additive new_listings

**Files:**
- Modify: `src/market_analyzer.py`（`build_market_review_payload` ~559-626 加参数与字段；新增 `_get_crypto_new_listings`；`_run_daily_review_parts` ~1418-1447 接线）
- Test: `tests/test_crypto_new_listings_payload.py`（Create）

- [ ] **Step 1: 写失败测试**
```python
# tests/test_crypto_new_listings_payload.py
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_payload_includes_new_listings_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-08")
    nl = [{"base": "NEW", "exchanges": ["okx"], "pairs": ["NEW-USDT"], "listed_at": 123, "price": 1.0}]
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", new_listings=nl)
    assert payload["new_listings"] == nl


def test_payload_omits_new_listings_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-08")
    payload = a.build_market_review_payload(ov, news=[], report="# 加密货币大盘复盘", new_listings=[])
    assert "new_listings" not in payload


def test_non_crypto_get_new_listings_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_new_listings() == []
```

- [ ] **Step 2: 跑测试确认失败**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_payload.py -q`
Expected: FAIL（参数/方法不存在）

- [ ] **Step 3: build_market_review_payload 加参数 + additive 字段** — 方法签名（~559）加形参：
```python
    def build_market_review_payload(
        self,
        overview: MarketOverview,
        news: List,
        report: str,
        market_light_snapshot: Optional[Dict[str, Any]] = None,
        new_listings: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
```
在末尾 `if light is not None: payload["market_light"] = light` 之后、`return payload` 之前加：
```python
        if new_listings:
            payload["new_listings"] = new_listings

```

- [ ] **Step 4: 新增 `_get_crypto_new_listings`**（放在 `search_market_news` 附近）：
```python
    def _get_crypto_new_listings(self) -> List[Dict[str, Any]]:
        """crypto 结构化新上线发现；非 crypto 返回 []，任何失败优雅降级为 []。"""
        if self.region != "crypto":
            return []
        try:
            from src.services.crypto_new_listing_service import CryptoNewListingService
            service = CryptoNewListingService(data_manager=self.data_manager)
            return service.discover()
        except Exception as e:
            logger.warning("[新上新] 发现失败，跳过: %s", e)
            return []
```

- [ ] **Step 5: `_run_daily_review_parts` 接线**（~1418-1447）— 在 `structured_payload = self.build_market_review_payload(...)` 前后改为：
```python
        new_listings = self._get_crypto_new_listings()
        structured_payload = self.build_market_review_payload(
            overview,
            news,
            report,
            snapshot,
            new_listings=new_listings,
        )
```

- [ ] **Step 6: 让既有 crypto 离线 e2e 保持离线（防回归网络调用）** — 接线后，`tests/test_crypto_market_review_e2e.py::test_crypto_review_end_to_end_offline` 会经 `run_daily_review_with_snapshot` → `_get_crypto_new_listings` → 服务发起真实 OKX/Coinbase 网络请求（虽失败降级为 `[]` 不致失败，但"离线"测试不应触网）。在该测试里加一行 mock，使发现短路：
```python
    monkeypatch.setattr(a, "_get_crypto_new_listings", lambda: [])
```
（紧接其它 `monkeypatch.setattr(a, ...)` 之后；READ 该测试确认变量名 `a` 与 `monkeypatch` fixture 已在用。）

- [ ] **Step 7: 跑测试确认通过 + 回归既有 payload/e2e 测试**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_payload.py tests/test_crypto_payload_omits.py tests/test_crypto_market_review_e2e.py tests/test_market_analyzer_generate_text.py -q`
Expected: PASS（新 3 + 既有全绿；cn/us/hk 不受影响；既有 crypto e2e 仍离线）

- [ ] **Step 8: Commit**
```bash
git add src/market_analyzer.py tests/test_crypto_new_listings_payload.py tests/test_crypto_market_review_e2e.py
git commit -m "feat: 新币上新接线 — crypto 复盘 payload additive new_listings"
```

---

## Task 10: Web — 上新行情表

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts`（`MarketReviewPayload` + `NewListing` 类型）
- Modify: `apps/dsa-web/src/components/report/MarketReviewReportView.tsx`
- Test: `apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx`（追加用例）

- [ ] **Step 1: 调研 snake→camel 映射** — 后端 payload 是 snake_case（`new_listings`、`change_pct`、`listed_at`、`quote_pair`），TS 类型是 camelCase（`changePct`、`marketLight`、`markdownReport`）。READ 客户端如何归一化 payload（搜 `changePct`/`camel`/`markdownReport` 的赋值处，可能是自动 camelize 或显式映射）。确认 `new_listings` 及其子字段会被映射为 `newListings`/`changePct`/`listedAt`/`quotePair`，否则在映射处补齐。把发现写进交付说明。

- [ ] **Step 2: 写失败测试（vitest）** — 在 `MarketReviewReportView.test.tsx` 追加（payload/props 形状对照文件内既有 crypto 用例）：
```tsx
it('crypto 渲染上新行情表，listedAt 缺失显示 —', () => {
  const payload = {
    version: 1, kind: 'market_review', region: 'crypto', language: 'zh',
    title: '加密货币大盘复盘', date: '2026-06-08',
    indices: [{ code: 'BTC/USDT', name: 'BTC/USDT', current: 64000, changePct: 1.0 }],
    newListings: [
      { base: 'NEW', exchanges: ['okx'], pairs: ['NEW-USDT'], listedAt: 1733616000000, price: 1.5, changePct: 5.0 },
      { base: 'NOQ', exchanges: ['binance'], pairs: ['NOQUSDT'] },
    ],
    sections: [{ key: 'full_review', title: 'Review', markdown: '## 一、概览\n内容' }],
    markdown_report: '# 加密货币大盘复盘',
  };
  render(<MarketReviewReportView payload={payload as any} />);
  expect(screen.getByText('NEW')).toBeInTheDocument();
  expect(screen.getByText('NOQ')).toBeInTheDocument();
  // NOQ 无 listedAt → 显示 —
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
});

it('非 crypto 或无 newListings 不渲染上新表', () => {
  const payload = {
    version: 1, kind: 'market_review', region: 'us', language: 'zh',
    title: 'US', date: '2026-06-08', indices: [],
    sections: [], markdown_report: '#',
  };
  render(<MarketReviewReportView payload={payload as any} />);
  expect(screen.queryByText(/上新|New Listings/i)).toBeNull();
});
```

- [ ] **Step 3: 跑测试确认失败**（无空格副本或 CI）
Run: `cd /tmp/dsa-web-nl && node node_modules/.bin/vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx`
Expected: FAIL（无上新表渲染）

- [ ] **Step 4: 类型** — `apps/dsa-web/src/types/analysis.ts`，在 `MarketReviewPayload` 内加 `newListings?: NewListing[];`，并新增：
```ts
export interface NewListing {
  base: string;
  exchanges: string[];
  pairs: string[];
  listedAt?: number;
  quotePair?: string;
  price?: number;
  changePct?: number;
  volume?: number;
}
```

- [ ] **Step 5: 组件** — `MarketReviewReportView.tsx`：
  1. `StructuredMarketData` 类型加 `newListings?: NonNullable<MarketReviewPayload['newListings']>;`
  2. `getStructuredMarketData` 两条路径各加 `newListings: marketPayload.newListings`（多市场）/ `newListings: payload.newListings`（单市场）。
  3. 在 indices 表渲染块（~490-513）之后，加上新行情表卡（仅 `marketData.newListings?.length`）：
```tsx
                {marketData.newListings && marketData.newListings.length > 0 ? (
                  <div className="overflow-x-auto">
                    <h4 className="mb-2 text-sm font-semibold text-foreground">{marketReviewText.newListings}</h4>
                    <table className="min-w-full text-sm">
                      <thead className="text-left text-xs uppercase text-muted-text">
                        <tr>
                          <th className="px-2 py-2">{marketReviewText.coin}</th>
                          <th className="px-2 py-2">{marketReviewText.exchanges}</th>
                          <th className="px-2 py-2">{marketReviewText.listedAt}</th>
                          <th className="px-2 py-2">{marketReviewText.last}</th>
                          <th className="px-2 py-2">{marketReviewText.change}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-subtle">
                        {marketData.newListings.map((item) => (
                          <tr key={item.base}>
                            <td className="px-2 py-2 font-medium text-foreground">{item.base}</td>
                            <td className="px-2 py-2 text-secondary-text">{item.exchanges.join(', ')}</td>
                            <td className="px-2 py-2 text-secondary-text">{item.listedAt ? new Date(item.listedAt).toISOString().slice(0, 10) : '—'}</td>
                            <td className="px-2 py-2 text-secondary-text">{item.price ?? '-'}</td>
                            <td className="px-2 py-2 text-secondary-text">{item.changePct !== undefined ? `${item.changePct}%` : '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
```
  4. `MARKET_REVIEW_TEXT` zh/en 各加：`newListings`（zh `新币与上新行情` / en `New Listings`）、`coin`（`币种`/`Coin`）、`exchanges`（`交易所`/`Exchanges`）、`listedAt`（`上市时间`/`Listed`）。在类型定义里同步补这 4 个键。

- [ ] **Step 6: 跑测试确认通过 + lint**（无空格副本或 CI）
Run: `cd /tmp/dsa-web-nl && node node_modules/.bin/vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx && node node_modules/.bin/eslint src/components/report/MarketReviewReportView.tsx src/types/analysis.ts`
Expected: PASS + lint 0。若本地工具链因路径空格不可用，则做静态校验并注明交 CI `web-gate`（参见验证矩阵）。

- [ ] **Step 7: Commit**
```bash
git add apps/dsa-web/src/types/analysis.ts apps/dsa-web/src/components/report
git commit -m "feat: 新币上新 Web — 上新行情表（listedAt 缺失显示 —）"
```

---

## Task 11: 文档 + .env.example + CHANGELOG

**Files:**
- Modify: `.env.example`、`docs/crypto-guide.md`、`docs/CHANGELOG.md`

- [ ] **Step 1: .env.example** — 在 crypto 段追加 4 项（注释 + 默认值）：
```
# CRYPTO_NEW_LISTING_ENABLED=true                  # 是否启用结构化新币上新发现（crypto 复盘内）
# CRYPTO_NEW_LISTING_WINDOW_DAYS=7                  # "新上"窗口天数
# CRYPTO_NEW_LISTING_SOURCES=okx,coinbase          # 发现源；binance 需持久卷(每日CI临时环境无输出)，持久部署可设 okx,coinbase,binance
# CRYPTO_NEW_LISTING_MAX=20                         # 上新行情表最大行数
```

- [ ] **Step 2: docs/crypto-guide.md** — 在大盘复盘小节下新增"结构化新币上新发现"：启用与 4 个 env、三源(OKX listTime / Coinbase new_at / Binance 差分)、Binance 需持久卷且默认关(每日 CI 无输出)、presence-only、去重按 base 与同名碰撞局限、Coinbase new_at 可能滞后数小时、与新闻叙事段互补。

- [ ] **Step 3: CHANGELOG `[Unreleased]`（扁平格式，勿加类目标题）**
```
- [新功能] crypto 大盘复盘新增结构化新币上新发现：OKX listTime + Coinbase new_at（默认开、无状态）+ Binance 快照差分（默认关、需持久卷）；产出 payload `new_listings` 与 Web 上新行情表；presence-only、按 base 去重、可经 CRYPTO_NEW_LISTING_* 配置。
```

- [ ] **Step 4: Commit**
```bash
git add .env.example docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 新币上新配置与指南（CRYPTO_NEW_LISTING_*，binance 默认关）"
```

---

## Task 12: 端到端集成 + 全量回归

**Files:**
- Test: `tests/test_crypto_new_listings_e2e.py`（Create）

- [ ] **Step 1: 写端到端测试（离线，mock 源 + 富化）**
```python
# tests/test_crypto_new_listings_e2e.py
import data_provider.crypto_new_listings as nl
from data_provider.crypto_new_listings import NewListing
from src.market_analyzer import MarketAnalyzer, MarketIndex


def test_crypto_review_includes_new_listings_offline(monkeypatch):
    a = MarketAnalyzer(region="crypto")   # analyzer=None→模板报告；search_service=None→无新闻
    monkeypatch.setattr(a, "_get_main_indices",
                        lambda: [MarketIndex(code="BTC/USDT", name="BTC/USDT", current=64000.0, change_pct=1.0)])
    now = 1_000_000_000_000
    monkeypatch.setattr(nl, "fetch_okx_instruments",
                        lambda w, n: [NewListing("NEW", "USDT", "NEW-USDT", "okx", now - 86_400_000, "okx")])
    monkeypatch.setattr(nl, "fetch_coinbase_products", lambda w, n: [])
    # 富化：让 manager 对 NEW/USDT 返回 None（新币无行情）→ presence-only omit 价格
    monkeypatch.setattr(a.data_manager, "get_realtime_quote", lambda code, log_final_failure=True: None)

    payload = a.run_daily_review_with_snapshot().structured_payload
    assert payload["region"] == "crypto"
    assert "new_listings" in payload
    assert payload["new_listings"][0]["base"] == "NEW"
    assert "price" not in payload["new_listings"][0]    # 无行情→omit
```
> 注：若服务 `discover` 取 `now_ms` 为系统时间而非传入，上面 OKX 的 `listed_at=now-1d` 需相对**真实 now** 在窗口内——把 `now` 改为 `int(time.time()*1000)` 并相应构造（`now - 86_400_000`）。实现者按 `discover()` 实际取时方式对齐（服务默认用 `time.time()`；测试可 `monkeypatch` `time.time` 或直接用真实 now 构造窗口内时间戳）。

- [ ] **Step 2: 跑测试**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_listings_e2e.py -q`
Expected: PASS（在 Task 1-9 完成后）。FAIL 则按 traceback 报告真实接线缺口，勿弱化断言。

- [ ] **Step 3: Commit e2e**
```bash
git add tests/test_crypto_new_listings_e2e.py
git commit -m "test: 新币上新端到端（crypto 复盘 payload 含 new_listings，presence-only）"
```

- [ ] **Step 4: 全量离线回归**
Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`（~7min，长超时）
Expected: 全绿（既有 2815 + 本特性新增用例）。任何失败逐条报告并判断是否本特性引入。

- [ ] **Step 5: ci_gate（可选但推荐）**
Run: `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh`
Expected: `all checks passed`。

---

## 实施后交付说明（执行者填写）
改了什么 / 为什么 / 验证（贴 `pytest -m "not network"` 末行 + 前端 vitest 结果或 CI 缺口）/ 未验证项（前端是否本地跑成）/ 风险（外网源依赖、Binance 仅持久卷生效）/ 回滚（revert；快照表 create_all 自动建、空表无害；默认 binance 关）。
