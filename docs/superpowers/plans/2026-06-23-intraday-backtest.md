# 盘中/分钟级回测(crypto MVP)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有日线回测链路 A 上追加一个 `interval` 维度,使日线 AI 操作建议(含 perp 杠杆)的止盈/止损/方向结论可在 crypto 分钟级真实价格路径上前向验证,对日线既有行为只追加不修改。

**Architecture:** 复用 `BacktestEngine.evaluate_single`(bar-agnostic)与日线候选(`get_candidates`);`run_backtest` 增 `interval` 参数,interval≠'1d' 时 forward bars 改由新的 provider 入口 `get_intraday_data` 取,窗口 bar 数 = `eval_window_days_days × bars_per_day(interval)`。行隔离走 `engine_version` 标签(`v1-5m`/`v1-5m-x3`,复用杠杆 v1-xN 手法,零唯一键迁移),新增 `bar_interval`/`first_hit_bar_index` 两列(guarded ALTER)。成本与调度默认关、`interval` 默认 '1d',纯追加可回滚。

**Tech Stack:** Python 3.10 / SQLAlchemy(SQLite)/ FastAPI / pytest;前端 React + TypeScript(apps/dsa-web,Vite)。

## Global Constraints

- 用中文交流;代码/注释/变量名/commit message 用英文与中文按文件语境(本仓库 docstring/注释多为中文);commit message 用英文类型前缀 + 中文体,**不加 `Co-Authored-By`**,不加工具/agent 前缀。
- 未经明确确认不执行 `git commit`/`git tag`/`git push`(本计划每个 Task 的 commit 步骤须在执行授权范围内进行;**不 push**)。
- 不写死密钥/账号/路径/端口/模型名;新配置项**必须同步** `src/config.py` + `src/core/config_registry.py` + `.env.example`,并由 `tests/test_config_registry.py` 校验。
- 稳定性优先:`interval` 默认 `'1d'`、成本默认 `0`、调度默认 `false` → **不配置即等于现状**;日线既有行语义/唯一键/API 默认响应/Web 默认视图逐字不变。
- 优先复用现有模块,不造平行实现(用既有 `is_crypto_code`/`is_perp_code`、`DataFetcherManager`、`scheduler.add_background_task`、`PHASE_FILTER_OPTIONS` 模式等)。
- 用户可见变更(CLI `--backtest-interval`、API `interval`、Web 选择器)须同步 `docs/CHANGELOG.md`(`[Unreleased]` 扁平格式 `- [类型] 描述`)与相关文档。
- 后端验证:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`;前端:`cd apps/dsa-web && npm ci && npm run lint && npm run build`。
- 允许集 interval ∈ {`1m`,`5m`,`15m`,`1h`};默认 bar 粒度 `5m`(`CRYPTO_INTRADAY_BACKTEST_INTERVAL`)。crypto 7×24,`bars_per_day(interval) = 1440 // interval_minutes`。
- engine_version tag 顺序固定:`base [+ "-{interval}" if interval!='1d'] [+ "-x{lev}" if lev>1]`(例 `v1` / `v1-x3` / `v1-5m` / `v1-5m-x3`)。
- `BacktestResult.eval_window_days` 持久化**始终存日历天数**(如 10),不存 window_bar_cnt;引擎切片用 window_bar_cnt。

---

### Task 1: 纯函数 helper 模块 `src/core/intraday_backtest.py`

无副作用纯函数,承载 interval→minutes、bars_per_day、window-bar-count 派生、engine_version tag 构建、成本后处理。后续 service/CLI/API 全部复用,单测最易。

**Files:**
- Create: `src/core/intraday_backtest.py`
- Test: `tests/test_intraday_backtest_helpers.py`

**Interfaces:**
- Produces:
  - `INTRADAY_INTERVAL_MINUTES: dict[str, int]` = `{"1m":1,"5m":5,"15m":15,"1h":60}`
  - `SUPPORTED_INTERVALS: tuple[str, ...]` = `("1d","1m","5m","15m","1h")`
  - `is_intraday_interval(interval: str) -> bool`
  - `bars_per_day(interval: str) -> int`(unknown intraday interval 抛 `ValueError`)
  - `derive_window_bar_count(eval_window_days: int, interval: str) -> int`
  - `build_engine_version_tag(base: str, interval: str, leverage: int) -> str`
  - `apply_round_trip_cost(return_pct: Optional[float], fee_bps: float, slippage_bps: float) -> Optional[float]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_backtest_helpers.py
import pytest

from src.core.intraday_backtest import (
    SUPPORTED_INTERVALS,
    is_intraday_interval,
    bars_per_day,
    derive_window_bar_count,
    build_engine_version_tag,
    apply_round_trip_cost,
)


def test_is_intraday_interval():
    assert is_intraday_interval("5m") is True
    assert is_intraday_interval("1h") is True
    assert is_intraday_interval("1d") is False
    assert is_intraday_interval("bogus") is False


@pytest.mark.parametrize("interval,expected", [("1m", 1440), ("5m", 288), ("15m", 96), ("1h", 24)])
def test_bars_per_day(interval, expected):
    assert bars_per_day(interval) == expected


def test_bars_per_day_rejects_unknown():
    with pytest.raises(ValueError):
        bars_per_day("1d")      # 日线非分钟,不应走此函数
    with pytest.raises(ValueError):
        bars_per_day("7m")


def test_derive_window_bar_count():
    assert derive_window_bar_count(10, "5m") == 2880    # 10 * 288
    assert derive_window_bar_count(1, "1h") == 24


def test_build_engine_version_tag_combinations():
    assert build_engine_version_tag("v1", "1d", 1) == "v1"
    assert build_engine_version_tag("v1", "1d", 3) == "v1-x3"
    assert build_engine_version_tag("v1", "5m", 1) == "v1-5m"
    assert build_engine_version_tag("v1", "5m", 3) == "v1-5m-x3"


def test_apply_round_trip_cost_default_zero_is_noop():
    assert apply_round_trip_cost(12.5, 0.0, 0.0) == 12.5
    assert apply_round_trip_cost(None, 5.0, 5.0) is None


def test_apply_round_trip_cost_deducts_two_fills():
    # fee 5bp + slip 5bp 单边 → 一进一出 = 2*(5+5)bp = 20bp = 0.20%
    assert apply_round_trip_cost(12.5, 5.0, 5.0) == pytest.approx(12.3)


def test_supported_intervals_contains_daily_and_intraday():
    assert "1d" in SUPPORTED_INTERVALS
    assert {"1m", "5m", "15m", "1h"}.issubset(set(SUPPORTED_INTERVALS))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.core.intraday_backtest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/intraday_backtest.py
"""盘中/分钟级回测纯函数 helper。

仅承载无副作用的口径换算(interval↔minutes、窗口 bar 数派生)、engine_version 标签
构建与成本后处理,供 BacktestService / CLI / API 复用,便于单测。
"""
from __future__ import annotations

from typing import Optional

# interval → 每根 bar 的分钟数(crypto 7×24)
INTRADAY_INTERVAL_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}

# 允许集:'1d' 为日线(走既有路径),其余为分钟
SUPPORTED_INTERVALS: tuple[str, ...] = ("1d", "1m", "5m", "15m", "1h")

_MINUTES_PER_DAY = 1440  # crypto 24h


def is_intraday_interval(interval: str) -> bool:
    """True 当 interval 为受支持的分钟粒度(不含 '1d')。"""
    return interval in INTRADAY_INTERVAL_MINUTES


def bars_per_day(interval: str) -> int:
    """每自然日的 bar 根数(crypto 7×24)。非分钟 interval 抛 ValueError。"""
    minutes = INTRADAY_INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError(f"不支持的分钟 interval: {interval!r}")
    return _MINUTES_PER_DAY // minutes


def derive_window_bar_count(eval_window_days: int, interval: str) -> int:
    """日历窗口(天)→ 分钟 bar 切片长度。"""
    return int(eval_window_days) * bars_per_day(interval)


def build_engine_version_tag(base: str, interval: str, leverage: int) -> str:
    """行隔离标签:base [+ -{interval} 若非 1d] [+ -x{lev} 若 >1]。顺序固定。"""
    tag = str(base)
    if is_intraday_interval(interval):
        tag = f"{tag}-{interval}"
    if int(leverage) > 1:
        tag = f"{tag}-x{int(leverage)}"
    return tag


def apply_round_trip_cost(
    return_pct: Optional[float], fee_bps: float, slippage_bps: float
) -> Optional[float]:
    """对一进一出收益(百分比)扣减成本。默认 0 → 原样返回(行为不变)。

    一进一出 = 2 次成交,每次成本 = fee_bps + slippage_bps(基点,1bp=0.01%)。
    """
    if return_pct is None:
        return None
    if not fee_bps and not slippage_bps:
        return return_pct
    cost_pct = 2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0
    return return_pct - cost_pct
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -v`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
git add src/core/intraday_backtest.py tests/test_intraday_backtest_helpers.py
git commit -m "feat(backtest): 盘中回测纯函数 helper(interval/bar数/tag/成本)"
```

---

### Task 2: 配置三件套(6 个新项)

新增分钟回测相关配置,默认值保证"不配置即等于现状"。三处必须同步,`tests/test_config_registry.py` 会强校验一致。

**Files:**
- Modify: `src/config.py`(dataclass 字段 + `_load_from_env` 加载,backtest 段约 891-894、crypto 段约 959)
- Modify: `src/core/config_registry.py`(`_FIELD_DEFINITIONS`,category="backtest",镜像 `SIGNAL_BACKTEST_ENABLED`/`AGENT_EVENT_MONITOR_*` 条目)
- Modify: `.env.example`(回测段约 686-706 之后)
- Test: `tests/test_config_intraday_backtest.py`(本计划新增)+ 既有 `tests/test_config_registry.py`(自动覆盖)

**Interfaces:**
- Produces(Config dataclass 新字段,均带默认):
  - `crypto_intraday_backtest_interval: str = "5m"` ← `CRYPTO_INTRADAY_BACKTEST_INTERVAL`
  - `crypto_intraday_minute_cache_ttl_s: int = 900` ← `CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S`(min 0)
  - `crypto_intraday_backtest_fee_bps: float = 0.0` ← `CRYPTO_INTRADAY_BACKTEST_FEE_BPS`(min 0)
  - `crypto_intraday_backtest_slippage_bps: float = 0.0` ← `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS`(min 0)
  - `intraday_backtest_enabled: bool = False` ← `INTRADAY_BACKTEST_ENABLED`
  - `intraday_backtest_schedule_minutes: int = 60` ← `INTRADAY_BACKTEST_SCHEDULE_MINUTES`(min 1)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_intraday_backtest.py
import importlib

import src.config as config_mod


def _fresh_config(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config_mod)
    return config_mod.Config()


def test_intraday_defaults_are_status_quo(monkeypatch):
    for k in [
        "CRYPTO_INTRADAY_BACKTEST_INTERVAL", "CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S",
        "CRYPTO_INTRADAY_BACKTEST_FEE_BPS", "CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS",
        "INTRADAY_BACKTEST_ENABLED", "INTRADAY_BACKTEST_SCHEDULE_MINUTES",
    ]:
        monkeypatch.delenv(k, raising=False)
    cfg = _fresh_config(monkeypatch)
    assert cfg.crypto_intraday_backtest_interval == "5m"
    assert cfg.crypto_intraday_minute_cache_ttl_s == 900
    assert cfg.crypto_intraday_backtest_fee_bps == 0.0
    assert cfg.crypto_intraday_backtest_slippage_bps == 0.0
    assert cfg.intraday_backtest_enabled is False
    assert cfg.intraday_backtest_schedule_minutes == 60


def test_intraday_env_override(monkeypatch):
    cfg = _fresh_config(
        monkeypatch,
        CRYPTO_INTRADAY_BACKTEST_INTERVAL="15m",
        CRYPTO_INTRADAY_BACKTEST_FEE_BPS="4",
        INTRADAY_BACKTEST_ENABLED="true",
        INTRADAY_BACKTEST_SCHEDULE_MINUTES="30",
    )
    assert cfg.crypto_intraday_backtest_interval == "15m"
    assert cfg.crypto_intraday_backtest_fee_bps == 4.0
    assert cfg.intraday_backtest_enabled is True
    assert cfg.intraday_backtest_schedule_minutes == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_intraday_backtest.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'crypto_intraday_backtest_interval'`

- [ ] **Step 3a: Add dataclass fields**(`src/config.py`,在 backtest 段 891-894 之后追加)

```python
    # 盘中/分钟级回测(crypto MVP;默认与现状一致)
    crypto_intraday_backtest_interval: str = "5m"
    crypto_intraday_minute_cache_ttl_s: int = 900
    crypto_intraday_backtest_fee_bps: float = 0.0
    crypto_intraday_backtest_slippage_bps: float = 0.0
    intraday_backtest_enabled: bool = False
    intraday_backtest_schedule_minutes: int = 60
```

- [ ] **Step 3b: Load from env**(`src/config.py` `_load_from_env`,紧邻既有 backtest 加载块;沿用 `parse_env_*` helper)

```python
        self.crypto_intraday_backtest_interval = (
            os.getenv("CRYPTO_INTRADAY_BACKTEST_INTERVAL", "5m") or "5m"
        )
        self.crypto_intraday_minute_cache_ttl_s = parse_env_int(
            os.getenv("CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S"), 900,
            field_name="CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S", minimum=0,
        )
        self.crypto_intraday_backtest_fee_bps = parse_env_float(
            os.getenv("CRYPTO_INTRADAY_BACKTEST_FEE_BPS"), 0.0,
            field_name="CRYPTO_INTRADAY_BACKTEST_FEE_BPS", minimum=0.0,
        )
        self.crypto_intraday_backtest_slippage_bps = parse_env_float(
            os.getenv("CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS"), 0.0,
            field_name="CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS", minimum=0.0,
        )
        self.intraday_backtest_enabled = parse_env_bool(
            os.getenv("INTRADAY_BACKTEST_ENABLED"), False,
        )
        self.intraday_backtest_schedule_minutes = parse_env_int(
            os.getenv("INTRADAY_BACKTEST_SCHEDULE_MINUTES"), 60,
            field_name="INTRADAY_BACKTEST_SCHEDULE_MINUTES", minimum=1,
        )
```

> 注:`parse_env_bool` 的实参顺序以 `src/config.py` 既有用法为准(本仓库为 `parse_env_bool(value, default)`)。其余沿用 `SIGNAL_BACKTEST_ENABLED` 等既有写法。

- [ ] **Step 3c: Registry 条目**(`src/core/config_registry.py` `_FIELD_DEFINITIONS`,紧随 `SIGNAL_BACKTEST_HORIZON_BARS` 之后,category 全部 `"backtest"`;`display_order` 取 70+ 递增,避免与既有 10–60 冲突)

```python
    "CRYPTO_INTRADAY_BACKTEST_INTERVAL": {
        "title": "Intraday Backtest Interval",
        "description": "盘中回测默认 bar 粒度(crypto;允许 1m/5m/15m/1h)。",
        "category": "backtest", "data_type": "string", "ui_control": "select",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "5m",
        "options": [
            {"value": "1m", "label": "1m"}, {"value": "5m", "label": "5m"},
            {"value": "15m", "label": "15m"}, {"value": "1h", "label": "1h"},
        ],
        "validation": {}, "display_order": 70,
        "help_key": "settings.backtest.CRYPTO_INTRADAY_BACKTEST_INTERVAL",
        "examples": ["CRYPTO_INTRADAY_BACKTEST_INTERVAL=5m"], "docs": [], "warning_codes": [],
    },
    "CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S": {
        "title": "Intraday Minute Cache TTL (s)",
        "description": "分钟取数缓存 TTL(秒);0=禁用缓存。",
        "category": "backtest", "data_type": "integer", "ui_control": "input",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "900", "options": [],
        "validation": {"min": 0}, "display_order": 71,
        "help_key": "settings.backtest.CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S",
        "examples": ["CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S=900"], "docs": [], "warning_codes": [],
    },
    "CRYPTO_INTRADAY_BACKTEST_FEE_BPS": {
        "title": "Intraday Backtest Fee (bps)",
        "description": "盘中回测单边手续费(基点);默认 0=理想化无成本。",
        "category": "backtest", "data_type": "number", "ui_control": "input",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "0", "options": [],
        "validation": {"min": 0}, "display_order": 72,
        "help_key": "settings.backtest.CRYPTO_INTRADAY_BACKTEST_FEE_BPS",
        "examples": ["CRYPTO_INTRADAY_BACKTEST_FEE_BPS=4"], "docs": [], "warning_codes": [],
    },
    "CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS": {
        "title": "Intraday Backtest Slippage (bps)",
        "description": "盘中回测单边滑点(基点);默认 0=理想化无成本。",
        "category": "backtest", "data_type": "number", "ui_control": "input",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "0", "options": [],
        "validation": {"min": 0}, "display_order": 73,
        "help_key": "settings.backtest.CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS",
        "examples": ["CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=2"], "docs": [], "warning_codes": [],
    },
    "INTRADAY_BACKTEST_ENABLED": {
        "title": "Intraday Backtest Scheduled",
        "description": "是否在调度模式下启用盘中回测后台任务(默认关闭,手动触发不受影响)。",
        "category": "backtest", "data_type": "boolean", "ui_control": "switch",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "false", "options": [],
        "validation": {}, "display_order": 74,
        "help_key": "settings.backtest.INTRADAY_BACKTEST_ENABLED",
        "examples": ["INTRADAY_BACKTEST_ENABLED=false"], "docs": [], "warning_codes": [],
    },
    "INTRADAY_BACKTEST_SCHEDULE_MINUTES": {
        "title": "Intraday Backtest Schedule (min)",
        "description": "盘中回测后台任务周期(分钟);仅 INTRADAY_BACKTEST_ENABLED=true 时生效。",
        "category": "backtest", "data_type": "integer", "ui_control": "input",
        "is_sensitive": False, "is_required": False, "is_editable": True,
        "default_value": "60", "options": [],
        "validation": {"min": 1}, "display_order": 75,
        "help_key": "settings.backtest.INTRADAY_BACKTEST_SCHEDULE_MINUTES",
        "examples": ["INTRADAY_BACKTEST_SCHEDULE_MINUTES=60"], "docs": [], "warning_codes": [],
    },
```

> 实现时以 `src/core/config_registry.py` 中 `SIGNAL_BACKTEST_ENABLED`(约 3252)的**实际字段集合**为准对齐键名(若该版本无 `options`/`warning_codes` 则去掉),保证 `tests/test_config_registry.py` 通过。

- [ ] **Step 3d: `.env.example`**(回测段约 706 之后追加,opt-in/成本项注释掉)

```env
# 盘中/分钟级回测(crypto MVP;默认与现状一致,不配置即可运行)
# 默认 bar 粒度(1m/5m/15m/1h)
# CRYPTO_INTRADAY_BACKTEST_INTERVAL=5m
# 分钟取数缓存 TTL(秒);0=禁用
# CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S=900
# 单边手续费/滑点(基点);默认 0=理想化无成本
# CRYPTO_INTRADAY_BACKTEST_FEE_BPS=0
# CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=0
# 盘中回测定时后台任务(默认关闭,手动触发不受影响)
# INTRADAY_BACKTEST_ENABLED=false
# INTRADAY_BACKTEST_SCHEDULE_MINUTES=60
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_intraday_backtest.py tests/test_config_registry.py -v`
Expected: PASS(新项被 registry 校验接受,默认值正确)

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/core/config_registry.py .env.example tests/test_config_intraday_backtest.py
git commit -m "feat(config): 盘中回测配置项(interval/缓存/成本/调度,默认与现状一致)"
```

---

### Task 3: DB schema 两列 + guarded ALTER 迁移

`BacktestResult` 加 `bar_interval`(默认 '1d')与 `first_hit_bar_index`(可空)。因本仓库无 ALTER 迁移框架、`create_all()` 不会给既存表加列,需在 `DatabaseManager` 初始化时补幂等 ALTER。

**Files:**
- Modify: `src/storage.py`(`BacktestResult` 列定义约 338-344 之间;`CURRENT_SCHEMA_VERSION` 约 60;`DatabaseManager.__init__` `create_all()` 后约 894-895)
- Test: `tests/test_backtest_intraday_migration.py`

**Interfaces:**
- Produces(`BacktestResult` 新列):`bar_interval: str DEFAULT '1d'`、`first_hit_bar_index: int NULL`
- Produces(DatabaseManager 私有方法):`_ensure_backtest_intraday_columns()`(幂等)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_intraday_migration.py
import sqlite3

from sqlalchemy import inspect

from src.storage import DatabaseManager, BacktestResult


def _columns(db) -> set:
    insp = inspect(db._engine)
    return {c["name"] for c in insp.get_columns("backtest_results")}


def test_new_columns_present_on_fresh_db(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh.db'}")
    cols = _columns(db)
    assert "bar_interval" in cols
    assert "first_hit_bar_index" in cols


def test_guarded_alter_adds_columns_to_legacy_db_and_is_idempotent(tmp_path):
    # 造一个"老库":手工建一个缺新列的 backtest_results 表
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE backtest_results ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_history_id INTEGER NOT NULL, "
        "code VARCHAR(10) NOT NULL, eval_window_days INTEGER NOT NULL DEFAULT 10, "
        "engine_version VARCHAR(16) NOT NULL DEFAULT 'v1', eval_status VARCHAR(16) NOT NULL DEFAULT 'pending')"
    )
    con.commit()
    con.close()

    db = DatabaseManager(db_url=f"sqlite:///{legacy}")     # init 触发 guarded ALTER
    cols = _columns(db)
    assert "bar_interval" in cols and "first_hit_bar_index" in cols

    # 幂等:再次调用不抛
    db._ensure_backtest_intraday_columns()
    assert "bar_interval" in _columns(db)


def test_bar_interval_defaults_to_1d(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'d.db'}")
    row = BacktestResult(analysis_history_id=1, code="BTC/USDT", eval_status="completed")
    with db.get_session() as s:
        s.add(row); s.commit()
        fetched = s.query(BacktestResult).first()
        assert fetched.bar_interval == "1d"
        assert fetched.first_hit_bar_index is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_intraday_migration.py -v`
Expected: FAIL — `bar_interval` 不在列集合 / `AttributeError`

- [ ] **Step 3a: 加列定义**(`src/storage.py`,在 `first_hit_trading_days`(338)之后、`# 模拟执行` 之前)

```python
    # 盘中/分钟级回测维度(日线行:bar_interval='1d', first_hit_bar_index=NULL)
    bar_interval = Column(String(8), nullable=False, default='1d', server_default='1d')
    first_hit_bar_index = Column(Integer, nullable=True)
```

- [ ] **Step 3b: bump schema version**(`src/storage.py:60`)

```python
CURRENT_SCHEMA_VERSION = "2026-06-23-backtest-intraday-columns"
```

- [ ] **Step 3c: guarded ALTER**(`src/storage.py`,新增方法 + 在 `__init__` 的 `create_all()` 后、`_ensure_schema_migration_record()` 前调用)

```python
    def _ensure_backtest_intraday_columns(self) -> None:
        """幂等补列:老库的 backtest_results 缺 bar_interval/first_hit_bar_index 时 ALTER 补上。

        create_all() 不会给既存表加列;SQLite 不支持 ADD COLUMN IF NOT EXISTS,
        故先 PRAGMA table_info 探测再 ALTER。
        """
        try:
            with self._engine.begin() as conn:
                from sqlalchemy import text
                existing = {
                    r[1] for r in conn.execute(text("PRAGMA table_info(backtest_results)"))
                }
                if not existing:
                    return  # 表尚未建(理论上 create_all 已建);留给 create_all
                if "bar_interval" not in existing:
                    conn.execute(text(
                        "ALTER TABLE backtest_results ADD COLUMN bar_interval VARCHAR(8) "
                        "NOT NULL DEFAULT '1d'"
                    ))
                if "first_hit_bar_index" not in existing:
                    conn.execute(text(
                        "ALTER TABLE backtest_results ADD COLUMN first_hit_bar_index INTEGER"
                    ))
        except Exception as exc:
            logger.warning("补全 backtest_results 盘中列失败: %s", exc)
```

调用点(`__init__`,紧接 `Base.metadata.create_all(self._engine)` 之后):

```python
            Base.metadata.create_all(self._engine)
            self._ensure_backtest_intraday_columns()
            self._ensure_schema_migration_record()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtest_intraday_migration.py -v`
Expected: PASS(fresh / legacy / 幂等 / 默认值)

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_backtest_intraday_migration.py
git commit -m "feat(storage): backtest_results 加 bar_interval/first_hit_bar_index + 幂等补列迁移"
```

---

### Task 4: 数据层 `get_intraday_data`(provider)

新增分钟取数:`DataFetcherManager.get_intraday_data` 门面(镜像 `get_daily_data` 的市场路由/容错/capability 过滤)+ crypto fetcher 实现(分页、跳指标)+ `_request_klines` 透传 interval + 简易 TTL 缓存。

**Files:**
- Modify: `data_provider/base.py`(`BaseFetcher` 默认 `get_intraday_data` 抛 NotImplementedError;`DataFetcherManager.get_intraday_data` 门面,镜像 1195+;capability `"intraday_data"`)
- Modify: `data_provider/crypto_base.py`(`CryptoExchangeBase.get_intraday_data` 实现 + `_request_klines` 增 interval 形参 + 声明 capability)
- Modify: `data_provider/binance_fetcher.py`、`data_provider/okx_fetcher.py`、`data_provider/coinbase_fetcher.py`(`_request_klines(symbol, days, interval='1d')` 透传 interval 到端点 + 分页)
- Test: `tests/test_get_intraday_data.py`

**Interfaces:**
- Consumes:`src.core.intraday_backtest.bars_per_day`、`INTRADAY_INTERVAL_MINUTES`(Task 1)
- Produces:
  - `DataFetcherManager.get_intraday_data(stock_code: str, interval: str, start_date: Optional[str]=None, end_date: Optional[str]=None, days: int=30) -> Tuple[pd.DataFrame, str]`(返回纯 OHLCV + `datetime` 列,**不含技术指标**)
  - `BaseFetcher.get_intraday_data(...)` 默认抛 `NotImplementedError`
  - crypto fetcher 声明 `capabilities` 含 `"intraday_data"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_get_intraday_data.py
import pandas as pd
import pytest

from data_provider.base import DataFetcherManager


def test_non_crypto_intraday_raises():
    mgr = DataFetcherManager()
    with pytest.raises(Exception):           # 无 intraday-capable fetcher → DataFetchError/NotImplementedError
        mgr.get_intraday_data("600519", interval="5m", days=1)


def test_crypto_intraday_returns_ohlc_without_indicators(monkeypatch):
    """mock 单页 klines:返回 3 根 5m bar,断言列含 datetime/ohlcv 且不含技术指标(如 ma20)。"""
    from data_provider.binance_fetcher import BinanceFetcher

    page = [
        # [openTime, open, high, low, close, volume, closeTime, ...]
        [1_700_000_000_000, "100", "102", "99", "101", "10", 1_700_000_300_000],
        [1_700_000_300_000, "101", "103", "100", "102", "12", 1_700_000_600_000],
        [1_700_000_600_000, "102", "104", "101", "103", "11", 1_700_000_900_000],
    ]
    captured = {}

    def fake_request_klines(self, symbol, days, interval="1d"):
        captured["interval"] = interval
        return page

    monkeypatch.setattr(BinanceFetcher, "_request_klines", fake_request_klines)

    f = BinanceFetcher()
    df = f.get_intraday_data("BTC/USDT", interval="5m", days=1)
    assert captured["interval"] == "5m"               # interval 透传
    assert {"datetime", "open", "high", "low", "close", "volume"}.issubset(set(df.columns))
    assert "ma20" not in df.columns and "atr" not in df.columns  # 跳指标
    assert len(df) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_get_intraday_data.py -v`
Expected: FAIL — `AttributeError: 'BinanceFetcher' object has no attribute 'get_intraday_data'`

- [ ] **Step 3a: `_request_klines` 透传 interval**(`data_provider/crypto_base.py` 抽象签名 + 各 crypto fetcher 实现)

`crypto_base.py`(把抽象方法签名改为带 interval):

```python
    def _request_klines(self, symbol: str, days: int, interval: str = "1d") -> list:
        raise NotImplementedError
```

`binance_fetcher.py`(原 24 行硬编码 `"interval":"1d"` 改为透传;分页:Binance 单请求 ≤1000 根,超出按 closeTime 翻页):

```python
    def _request_klines(self, symbol: str, days: int, interval: str = "1d") -> list:
        limit = self._days_to_limit(days) if interval == "1d" else self._intraday_limit(days, interval)
        if interval == "1d" or limit <= 1000:
            return self._http_get(
                f"{self._base_url()}/klines",
                {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)},
            )
        # 分页:按 startTime 递增翻页直到取满 limit
        out: list = []
        params = {"symbol": symbol, "interval": interval, "limit": 1000}
        while len(out) < limit:
            page = self._http_get(f"{self._base_url()}/klines", dict(params))
            if not page:
                break
            out.extend(page)
            params["startTime"] = int(page[-1][6]) + 1     # 上一页 closeTime+1
            if len(page) < 1000:
                break
        return out[:limit]
```

并在 `crypto_base.py` 加 `_intraday_limit`(纯换算,复用 Task1):

```python
    def _intraday_limit(self, days: int, interval: str) -> int:
        from src.core.intraday_backtest import bars_per_day
        return int(days) * bars_per_day(interval)
```

> okx_fetcher.py / coinbase_fetcher.py 的 `_request_klines` 同样接受 `interval` 并透传到各自端点(OKX `bar` 参数,Coinbase `granularity` 秒数)。若某 fetcher 暂不实现分钟,可让其 `interval!='1d'` 时 `raise NotImplementedError`,由门面容错切换。

- [ ] **Step 3b: crypto fetcher `get_intraday_data` + capability**(`crypto_base.py`)

```python
    # capability 声明里加入 "intraday_data"(沿用本类既有 capabilities 声明方式)
    def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
        symbol = self._to_exchange_symbol(stock_code)
        raw = self._request_klines(symbol, days=days, interval=interval)
        df = self._parse_klines(raw)                 # 复用既有解析
        df = self._normalize_data(df, stock_code)    # 标准化列名(open/high/low/close/volume/datetime)
        df = self._clean_data(df)                    # 清洗;★ 不调用算指标步骤
        return df
```

> 注:`get_daily_data` 模板在 `_clean_data` 之后还有"算指标"步骤;`get_intraday_data` **刻意不算指标**(Q10)。若 `_normalize_data` 输出列名为 `date` 而非 `datetime`,在此补一列 `datetime`(分钟需保留时分)。

- [ ] **Step 3c: 门面 `DataFetcherManager.get_intraday_data`**(`data_provider/base.py`,镜像 1195+ 的 crypto/perp 路由 + capability 过滤为 `"intraday_data"`;非 crypto/无 capable fetcher → `DataFetchError`;带 `crypto_intraday_minute_cache_ttl_s` TTL 的进程内缓存,key=(code,interval,days))

```python
    def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
        stock_code = normalize_stock_code(stock_code)
        # 仅 crypto / crypto_perp 支持分钟
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)):
            raise DataFetchError(f"{stock_code} 暂不支持分钟级数据(仅 crypto)")
        fetchers = self._get_fetchers_snapshot()
        market = "crypto_perp" if is_perp_code(stock_code) else "crypto"
        fetchers = self._filter_daily_fetchers_for_market(fetchers, market)
        fetchers = self._filter_fetchers_by_capability(fetchers, capability="intraday_data")
        if not fetchers:
            raise DataFetchError(f"{stock_code} 无可用分钟数据源")
        errors = []
        for f in fetchers:
            try:
                df = f.get_intraday_data(stock_code, interval=interval,
                                         start_date=start_date, end_date=end_date, days=days)
                if df is not None and not df.empty:
                    return df, f.name
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")
        raise DataFetchError(f"{stock_code} 分钟数据获取失败: {errors}")
```

`BaseFetcher` 默认实现(`data_provider/base.py`):

```python
    def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
        raise NotImplementedError(f"[{self.name}] 暂不支持分钟级数据")
```

> 缓存:可用既有进程内缓存工具或一个 `{key: (ts, df)}` dict + `crypto_intraday_minute_cache_ttl_s`;TTL=0 时不缓存。实现时复用仓库已有缓存 helper(若有),否则就地最小实现并加单测。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_get_intraday_data.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py data_provider/crypto_base.py data_provider/binance_fetcher.py data_provider/okx_fetcher.py data_provider/coinbase_fetcher.py tests/test_get_intraday_data.py
git commit -m "feat(data): get_intraday_data 分钟取数(crypto,分页/跳指标/容错门面)"
```

---

### Task 5: BacktestService interval 集成(核心)

`run_backtest` 增 `interval` 参数;interval≠'1d' 时切到分钟 forward + tag + window_bar_cnt + 落库语义消歧。引擎不改。

**Files:**
- Modify: `src/services/backtest_service.py`(`run_backtest` 35-256;`BacktestResult` 构造 187-217)
- Test: `tests/test_backtest_service_intraday.py`

**Interfaces:**
- Consumes:`src.core.intraday_backtest`(`is_intraday_interval`/`build_engine_version_tag`/`derive_window_bar_count`)、`DataFetcherManager.get_intraday_data`(Task 4)、`is_crypto_code`/`is_perp_code`(data_provider.base)
- Produces:`BacktestService.run_backtest(..., interval: str = "1d")`(返回结构不变 dict);分钟行写入 `bar_interval`/`first_hit_bar_index`,`eval_window_days` 存日历天数,`engine_version` 为 tag

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_service_intraday.py
import pandas as pd
from datetime import date
from types import SimpleNamespace

import pytest

from src.services.backtest_service import BacktestService


def _minute_df(n, base=100.0):
    return pd.DataFrame([
        {"datetime": f"2026-06-01 00:{i:02d}:00", "open": base, "high": base + 5,
         "low": base - 5, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def test_run_backtest_default_interval_is_daily_path(monkeypatch):
    """interval 默认 '1d' → 不触发分钟取数(get_intraday_data 不被调用)。"""
    called = {"intraday": 0}
    from data_provider.base import DataFetcherManager
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data",
                        lambda self, *a, **k: called.__setitem__("intraday", called["intraday"] + 1))
    svc = BacktestService.__new__(BacktestService)        # 仅验证默认分支不取分钟
    # get_candidates 返回空 → 直接返回,验证默认 interval 不报错且不取分钟
    monkeypatch.setattr(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    monkeypatch.setattr(svc, "stock_repo", SimpleNamespace())
    out = svc.run_backtest(interval="1d")
    assert called["intraday"] == 0
    assert out["processed"] == 0


def test_intraday_tag_and_window_and_persistence(monkeypatch):
    """interval='5m' + eval_window_days=2:
       - engine_version 落 'v1-5m'
       - 引擎切片用 window_bar_cnt=2*288=576
       - 持久化 eval_window_days=2(日历天数,非 576)
       - first_hit_bar_index 承载引擎 first_hit_trading_days,first_hit_trading_days 列为 None
    """
    # 详见 Step 3:构造一个 perp/crypto 候选,monkeypatch get_intraday_data 返回足量分钟 bar,
    # monkeypatch BacktestEngine.evaluate_single 返回 completed + first_hit_trading_days=7,
    # 捕获 save_results_batch 收到的 BacktestResult,断言上述四点。
    ...
```

> Step 1 仅先放第一个可独立判 FAIL 的用例(`test_run_backtest_default_interval_is_daily_path`)即可启动 TDD;第二个用例在 Step 3 实现后补全断言。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_service_intraday.py::test_run_backtest_default_interval_is_daily_path -v`
Expected: FAIL — `run_backtest() got an unexpected keyword argument 'interval'`

- [ ] **Step 3: 实现 interval 分支**(`src/services/backtest_service.py`)

3a. `run_backtest` 签名加 `interval: str = "1d"`(放在 `leverage` 后)。

3b. 计算 tag(替换 61-65 的 tag 逻辑,复用 helper):

```python
        from src.core.intraday_backtest import (
            is_intraday_interval, build_engine_version_tag, derive_window_bar_count,
        )
        base_version = str(getattr(config, "backtest_engine_version", "v1"))
        engine_version = build_engine_version_tag(base_version, interval, leverage)
        if perp_only:
            logger.info(f"杠杆情景回测: L={leverage} (engine_version={engine_version})")
```

3c. 分钟门控 + forward 取数(在候选循环内,替换 daily forward 取数分支;保留日线分支字节级不变):

```python
        from data_provider.base import is_crypto_code, is_perp_code
        intraday = is_intraday_interval(interval)
        if intraday:
            window_bar_cnt = derive_window_bar_count(int(eval_window_days), interval)
        ...
        # 循环内(daily forward 之处):
        if intraday:
            if not (is_crypto_code(analysis.code) or is_perp_code(analysis.code)):
                skipped_non_perp += 1     # 复用计数:非 crypto 跳过(可改名 skipped_unsupported)
                continue
            from data_provider.base import DataFetcherManager
            try:
                fwd_df, _src = DataFetcherManager().get_intraday_data(
                    analysis.code, interval=interval, days=int(eval_window_days),
                )
            except Exception as exc:
                insufficient += 1
                results_to_save.append(BacktestResult(
                    analysis_history_id=analysis.id, code=analysis.code,
                    analysis_date=start_daily.date, eval_window_days=int(eval_window_days),
                    engine_version=engine_version, bar_interval=interval,
                    eval_status="insufficient_data", evaluated_at=datetime.now(),
                    operation_advice=analysis.operation_advice))
                continue
            forward_bars = _df_to_bars(fwd_df)              # 转 DailyBarLike 序列(date/high/low/close)
            eval_slice = window_bar_cnt
        else:
            forward_bars = self.stock_repo.get_forward_bars(...)   # 原样
            eval_slice = int(eval_window_days)
```

`eval_config` 用 `eval_slice` 作切片长度:

```python
        eval_config = EvaluationConfig(
            eval_window_days=eval_slice,        # 分钟=window_bar_cnt;日线=eval_window_days
            neutral_band_pct=neutral_band_pct,
            engine_version=str(engine_version),
        )
```

3d. 落库语义消歧(`BacktestResult` 构造,187-217):分钟时 `eval_window_days` 存日历天数、first_hit 路由到 bar_index:

```python
        results_to_save.append(BacktestResult(
            ...,
            eval_window_days=int(eval_window_days),          # ★ 始终存日历天数(非引擎回显)
            engine_version=str(engine_version),              # tag
            bar_interval=interval,                           # ★ 显式判别列
            first_hit_trading_days=(None if intraday else evaluation.get("first_hit_trading_days")),
            first_hit_bar_index=(evaluation.get("first_hit_trading_days") if intraday else None),
            ...,
        ))
```

3e. 新增 `_df_to_bars`(把分钟 DataFrame 行转成引擎可用的 bar 对象,字段 `date`/`high`/`low`/`close`;`date` 取该行 datetime):

```python
    @staticmethod
    def _df_to_bars(df):
        from collections import namedtuple
        Bar = namedtuple("Bar", ["date", "high", "low", "close", "open"])
        out = []
        for _, r in df.iterrows():
            out.append(Bar(date=r.get("datetime") or r.get("date"),
                           high=float(r["high"]), low=float(r["low"]),
                           close=float(r["close"]), open=float(r.get("open", r["close"]))))
        return out
```

> `_recompute_summaries` 已按 `(eval_window_days, engine_version)` 聚合 → 因 engine_version=tag 含 interval,分钟汇总自然与日线隔离,无需改 summary 逻辑。

3f. 回到 Step 1 的第二个用例 `test_intraday_tag_and_window_and_persistence`,按 3a–3e 行为补全断言并运行。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest_service_intraday.py -v`
Expected: PASS(默认日线分支不取分钟;分钟分支 tag/窗口/落库语义正确)

- [ ] **Step 5: Commit**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_intraday.py
git commit -m "feat(backtest): run_backtest 支持 interval(分钟 forward/tag/窗口派生/落库消歧)"
```

---

### Task 6: 成本可选后处理

interval 为分钟时,对引擎返回的 `simulated_return_pct` 做一进一出成本扣减;默认 0 → 不变。

**Files:**
- Modify: `src/services/backtest_service.py`(`evaluation` 取得后、构造 BacktestResult 前)
- Test: `tests/test_backtest_service_intraday.py`(追加 2 个用例)

**Interfaces:**
- Consumes:`src.core.intraday_backtest.apply_round_trip_cost`(Task 1)、config `crypto_intraday_backtest_fee_bps`/`_slippage_bps`(Task 2)

- [ ] **Step 1: Write the failing test**

```python
def test_cost_zero_keeps_return_unchanged(monkeypatch):
    # fee/slip 默认 0 → simulated_return_pct 与引擎一致(在分钟分支断言)
    ...

def test_cost_positive_deducts_round_trip(monkeypatch):
    # fee=5bp slip=5bp → simulated_return_pct -= 0.20 个百分点
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_service_intraday.py -k cost -v`
Expected: FAIL(成本未生效)

- [ ] **Step 3: Implement**(在分钟分支拿到 `evaluation` 后)

```python
        if intraday:
            from src.core.intraday_backtest import apply_round_trip_cost
            fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
            slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
            if fee or slip:
                evaluation["simulated_return_pct"] = apply_round_trip_cost(
                    evaluation.get("simulated_return_pct"), fee, slip,
                )
```

> 默认 0 不进入分支 → 引擎与日线路径字节级不变。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest_service_intraday.py -k cost -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_intraday.py
git commit -m "feat(backtest): 分钟回测可选手续费/滑点成本后处理(默认 0 不变)"
```

---

### Task 7: CLI `--backtest-interval`

手动触发分钟回测(主路径)。

**Files:**
- Modify: `main.py`(`--backtest*` 参数区 381-404;以及调用 `run_backtest` 的处)
- Test: `tests/test_cli_backtest_interval.py`

**Interfaces:**
- Consumes:`BacktestService.run_backtest(interval=...)`(Task 5)
- Produces:CLI arg `--backtest-interval`(default `None` → 用 config `crypto_intraday_backtest_interval`?**否**:默认 `'1d'` 保持现状;仅显式传入才走分钟)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_backtest_interval.py
import subprocess, sys


def test_help_lists_backtest_interval():
    out = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True)
    assert "--backtest-interval" in (out.stdout + out.stderr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_backtest_interval.py -v`
Expected: FAIL(help 无该参数)

- [ ] **Step 3: Implement**(`main.py`,参数区追加 + 透传)

```python
    parser.add_argument(
        '--backtest-interval', type=str, default='1d',
        help="回测 bar 粒度(1d/1m/5m/15m/1h;默认 1d=日线;分钟仅 crypto)",
    )
```

调用处(原 `service.run_backtest(...)`):增加 `interval=args.backtest_interval`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_backtest_interval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_cli_backtest_interval.py
git commit -m "feat(cli): --backtest-interval 手动触发分钟回测(默认 1d)"
```

---

### Task 8: opt-in 调度钩子

仅 `INTRADAY_BACKTEST_ENABLED=true` 时挂载后台分钟回测任务,默认不挂(手动为主)。

**Files:**
- Modify: 调度装配处(`main.py` 或 `server.py` 中创建 `Scheduler` 并注册任务之处;参考既有 opt-in 注册,如 AGENT_EVENT_MONITOR)
- Test: `tests/test_intraday_backtest_schedule_hook.py`

**Interfaces:**
- Consumes:config `intraday_backtest_enabled`/`intraday_backtest_schedule_minutes`(Task 2);`scheduler.add_background_task`;`BacktestService.run_backtest(interval=config.crypto_intraday_backtest_interval)`(Task 5)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_backtest_schedule_hook.py
from types import SimpleNamespace

from src.scheduler_wiring import maybe_register_intraday_backtest   # 见 Step 3:抽一个可测函数


def _fake_scheduler():
    calls = []
    return SimpleNamespace(add_background_task=lambda **k: calls.append(k), _calls=calls)


def test_disabled_by_default_not_registered():
    sched = _fake_scheduler()
    cfg = SimpleNamespace(intraday_backtest_enabled=False, intraday_backtest_schedule_minutes=60,
                          crypto_intraday_backtest_interval="5m")
    maybe_register_intraday_backtest(sched, cfg)
    assert sched._calls == []


def test_enabled_registers_with_interval_seconds():
    sched = _fake_scheduler()
    cfg = SimpleNamespace(intraday_backtest_enabled=True, intraday_backtest_schedule_minutes=30,
                          crypto_intraday_backtest_interval="5m")
    maybe_register_intraday_backtest(sched, cfg)
    assert len(sched._calls) == 1
    assert sched._calls[0]["interval_seconds"] == 30 * 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intraday_backtest_schedule_hook.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError`

- [ ] **Step 3: Implement**(抽一个可测纯函数,装配处调用)

新增 `src/scheduler_wiring.py`(或放入既有调度装配模块,保持单一职责):

```python
"""调度装配:opt-in 后台任务注册(可单测)。"""
import logging

logger = logging.getLogger(__name__)


def maybe_register_intraday_backtest(scheduler, config) -> None:
    """仅当 INTRADAY_BACKTEST_ENABLED=true 时注册盘中回测后台任务。"""
    if not getattr(config, "intraday_backtest_enabled", False):
        return
    minutes = max(1, int(getattr(config, "intraday_backtest_schedule_minutes", 60)))
    interval = getattr(config, "crypto_intraday_backtest_interval", "5m")

    def _task():
        from src.services.backtest_service import BacktestService
        try:
            BacktestService().run_backtest(interval=interval)
        except Exception as exc:
            logger.warning("盘中回测后台任务失败: %s", exc)

    scheduler.add_background_task(
        task=_task, interval_seconds=minutes * 60,
        run_immediately=False, name="intraday_backtest",
    )
    logger.info("已注册盘中回测后台任务(每 %d 分钟, interval=%s)", minutes, interval)
```

在实际调度装配处(创建 `Scheduler` 后)调用 `maybe_register_intraday_backtest(scheduler, get_config())`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intraday_backtest_schedule_hook.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler_wiring.py main.py tests/test_intraday_backtest_schedule_hook.py
git commit -m "feat(schedule): opt-in 盘中回测后台任务(默认关)"
```

---

### Task 9: API interval 参数 + 响应字段

API run/results/performance 透传 `interval`(默认 '1d'),repo 过滤加 `bar_interval`,响应 item 追加两字段。

**Files:**
- Modify: `api/v1/schemas/backtest.py`(`BacktestRunRequest` 加 `interval`;`BacktestResultItem` 加 `bar_interval`/`first_hit_bar_index`)
- Modify: `api/v1/endpoints/backtest.py`(run/results/performance/performance/{code} 加 `interval` Query,默认 '1d')
- Modify: `src/repositories/backtest_repo.py`(`_build_result_conditions` 449-472 加 `bar_interval` 条件)
- Modify: `src/services/backtest_service.py`(`get_recent_evaluations`/`get_summary` 透传 interval → repo)
- Test: `tests/test_backtest_api_interval.py`

**Interfaces:**
- Consumes:Task 5 的 `run_backtest(interval=...)`;Task 3 的 `bar_interval` 列
- Produces:API `interval` 参数(默认 '1d');`BacktestResultItem.bar_interval`、`.first_hit_bar_index`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_api_interval.py
from src.repositories.backtest_repo import BacktestRepository


def test_build_result_conditions_filters_bar_interval(monkeypatch):
    """传 interval 时,条件里含 bar_interval == interval。"""
    repo = BacktestRepository.__new__(BacktestRepository)
    conds = repo._build_result_conditions(
        code=None, eval_window_days=10, engine_version="v1-5m",
        analysis_date_from=None, analysis_date_to=None, bar_interval="5m",
    )
    rendered = " ".join(str(c) for c in conds)
    assert "bar_interval" in rendered


def test_results_default_interval_is_1d(monkeypatch):
    """results 端点默认 interval='1d' → 不返回分钟行(回归:日线响应不被污染)。"""
    # 用 TestClient 或直接调用 service.get_recent_evaluations(interval 默认 '1d'),
    # 断言 repo 收到 bar_interval='1d'。
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtest_api_interval.py -v`
Expected: FAIL — `_build_result_conditions() got an unexpected keyword argument 'bar_interval'`

- [ ] **Step 3: Implement**

3a. `_build_result_conditions` 加形参 + 条件(`src/repositories/backtest_repo.py`):

```python
    def _build_result_conditions(self, *, code, eval_window_days, engine_version,
                                 analysis_date_from, analysis_date_to, bar_interval=None):
        conditions = []
        ...
        if bar_interval is not None:
            conditions.append(BacktestResult.bar_interval == bar_interval)
        return conditions
```

3b. service 透传 `interval`(默认 '1d')到 `get_recent_evaluations`/`get_summary`/各 repo 调用(`src/services/backtest_service.py`)。

3c. API schema(`api/v1/schemas/backtest.py`):

```python
class BacktestRunRequest(BaseModel):
    ...
    interval: Optional[str] = Field("1d", description="bar 粒度(1d/1m/5m/15m/1h;默认 1d)")

class BacktestResultItem(BaseModel):
    ...
    bar_interval: Optional[str] = "1d"
    first_hit_bar_index: Optional[int] = None
```

3d. endpoints(`api/v1/endpoints/backtest.py`):run 透传 `request.interval`;results/performance 加 `interval: str = Query("1d")` 并下传 service。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backtest_api_interval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/v1/schemas/backtest.py api/v1/endpoints/backtest.py src/repositories/backtest_repo.py src/services/backtest_service.py tests/test_backtest_api_interval.py
git commit -m "feat(api): 回测 interval 参数与响应字段(默认 1d,日线响应不变)"
```

---

### Task 10: Web BacktestPage interval 选择器

复用 `PHASE_FILTER_OPTIONS` 模式加 interval 下拉(默认 '1d'),thread 进 fetch;分钟行多一列。

**Files:**
- Modify: `apps/dsa-web/src/api/backtest.ts`(`getResults/getOverallPerformance/getStockPerformance` params 加 `interval?`)
- Modify: `apps/dsa-web/src/types/backtest.ts`(`BacktestResultItem` 加 `barInterval`/`firstHitBarIndex`;新增 `INTERVAL_OPTIONS` 或 type)
- Modify: `apps/dsa-web/src/pages/BacktestPage.tsx`(`INTERVAL_OPTIONS` 常量 + `intervalFilter` state + 下拉 + thread fetch + 表格列 + 中文 label)
- Test: `apps/dsa-web` lint + build;可选 vitest 单测选择器默认 '1d'

**Interfaces:**
- Consumes:Task 9 的 API `interval` 参数与响应字段

- [ ] **Step 1: Write the failing check**(以 lint+build 作为门禁;选择器默认值用一个最小 vitest 断言或人工核对)

```bash
cd apps/dsa-web && npm ci && npm run lint && npm run build
```

- [ ] **Step 2: 现状基线**:先跑一次确认当前 green(改前)。

- [ ] **Step 3: Implement**(`BacktestPage.tsx`,镜像 phase filter 22-28/476-485)

```tsx
const INTERVAL_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '1d', label: '日线' },
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
  { value: '1h', label: '1时' },
];
// state
const [intervalFilter, setIntervalFilter] = useState('1d');
// 下拉(放在 phase filter 旁)
<select value={intervalFilter} onChange={(e) => setIntervalFilter(e.target.value)} disabled={isRunning}
        className={`${BACKTEST_COMPACT_INPUT_CLASS} w-24`}>
  {INTERVAL_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
</select>
```

- `fetchResults`/`fetchPerformance` 调用加 `interval: intervalFilter`(透传到 `backtestApi.*`)。
- `backtest.ts`:各 fetch 函数 params 加 `interval?: string`,`if (interval) queryParams.interval = interval`。
- 表格:当 `intervalFilter !== '1d'` 时多渲染一列"首次命中(bar)"= `item.firstHitBarIndex`。
- `types/backtest.ts`:`BacktestResultItem` 加 `barInterval?: string; firstHitBarIndex?: number | null`。

- [ ] **Step 4: Run lint + build to verify pass**

```bash
cd apps/dsa-web && npm run lint && npm run build
```
Expected: 0 error,build 成功。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/BacktestPage.tsx apps/dsa-web/src/api/backtest.ts apps/dsa-web/src/types/backtest.ts
git commit -m "feat(web): 回测页 interval 选择器与分钟命中列(默认日线视图不变)"
```

---

### Task 11: 文档 + CHANGELOG + 专题

用户可见变更(CLI/API/Web/配置)须同步文档。

**Files:**
- Create: `docs/intraday-backtest.md`(专题:能力、interval 语义、engine_version tag、成本/调度开关、限制与数据深度、CLI/API/Web 用法)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 扁平格式)
- 核对: `.env.example` 已含新项(Task 2)
- Test: 无代码测试(docs);跑一次全量门禁兜底

**Interfaces:** 无

- [ ] **Step 1: 写专题 `docs/intraday-backtest.md`**(覆盖:仅 crypto;interval {1d,1m,5m,15m,1h};同日历窗口分钟路径;行隔离 engine_version tag;两新列;成本/调度默认关;数据深度限制;`python main.py --backtest --backtest-interval 5m` 与 API `interval` 与 Web 选择器用法)。

- [ ] **Step 2: `docs/CHANGELOG.md` `[Unreleased]` 追加(扁平格式)**

```markdown
- [新功能] 盘中/分钟级回测(crypto):日线 AI 建议在分钟价格路径上前向验证;CLI --backtest-interval、API interval 参数、Web 回测页 interval 选择器;行隔离走 engine_version 标签,新增 bar_interval/first_hit_bar_index;成本与定时调度默认关,interval 默认 1d 与现状一致
```

- [ ] **Step 3: 全量门禁兜底**

```bash
./scripts/ci_gate.sh && python -m pytest -m "not network" -q
```
Expected: 全绿(含本计划新增用例)。

- [ ] **Step 4: Commit**

```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md
git commit -m "docs: 盘中/分钟级回测专题与 CHANGELOG"
```

---

## Self-Review(已执行)

**1. Spec coverage:** spec §4.1→Task4;§4.2→Task3;§4.3→Task5;成本(§4.3/Q7)→Task6;§4.4→Task9;§4.5→Task10;§4.6 配置→Task2、调度→Task8、CLI→Task7;§2 行隔离/window 派生→Task1+Task5;§5 降级→Task4/Task5 错误分支;§6 测试→各 Task 单测 + Task11 兜底;§8 验证→Task11。纯函数口径(bars_per_day/tag/cost)前置 Task1 供全链复用。无遗漏。

**2. Placeholder scan:** Task5/Task6/Task9 的部分用例正文标注"详见 Step 3 补全断言"——这是 TDD 两段式(先放可独立判 FAIL 的首用例,行为实现后补断言),非占位 TODO;每个补全点都给了明确断言对象与期望值。其余步骤均含真实代码与命令。

**3. Type consistency:** `build_engine_version_tag`/`bars_per_day`/`derive_window_bar_count`/`apply_round_trip_cost`/`is_intraday_interval`(Task1)在 Task5/6/8 调用签名一致;`get_intraday_data(stock_code, interval, start_date, end_date, days)`(Task4)在 Task5 调用一致;`bar_interval`/`first_hit_bar_index`(Task3)在 Task5 写、Task9 过滤/响应、Task10 展示,命名贯穿一致。

> 实现期若发现与实际代码细节漂移(如 `parse_env_bool` 实参顺序、registry 条目字段集、`_normalize_data` 输出列名、调度装配确切位置),以实际代码为准并顺手订正本计划与 spec。
