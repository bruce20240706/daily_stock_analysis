# M3 信号可信度 + 量价丰富度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把信号从"看图标注"升级为"有规则级三重门回测背书、按信号类型×市场分层胜率(Wilson CI+基准超额)、多量能指标共振研判"的可信、可操作买卖信号。

**Architecture:** 纯后端/引擎深耕，零新数据源。新增纯函数评估器(三重门) + 预计算批作业(落 `signal_stats` 表) + 命中率回填改读 (signal_type×market) 统计；引擎新增 CMF/MFI 指标与多源背离共振；前端三处(钻取/工作台信号tab/看板行)展示可信度。所有新纯函数 bar-interval 无关。

**Tech Stack:** Python(pandas/SQLAlchemy ORM/argparse/ThreadPoolExecutor) 后端；React+TS+vitest 前端。复用 `compute_volume_price_signals`、`derive_price_levels`、`StockService.get_history_data`、`get_market_for_stock`、ORM `Base`/repo 范式。

## Global Constraints

- 信号载体是 `VPSignal`(frozen, `volume_price_signals.py:72`)，**无** hit 字段；hit/ci 字段在 `signals_service.build_signals_payload` 组装的 marker dict、API `SignalMarker`(`api/v1/schemas/stocks.py:111`) 与前端 TS 类型里。
- market 分类**统一用** `get_market_for_stock(code) -> 'cn'|'hk'|'us'|'crypto'|None`(`src/core/trading_calendar.py:110`)；**不要**用 `signal_board_service._infer_market`(大写，口径不符)。
- baseline 单一定义：同 market、同 horizon、同三重门规则、**对全体 bar(非仅信号触发点)入场**的赢率；`excess = ci_low - baseline_win_rate`；`verified = sample≥min_sample 且 ci_low > baseline_win_rate`。
- `sample = win + loss`（expired 不计入分母，对齐既有 `hit_sample` 口径）。
- 历史取数统一 `StockService.get_history_data(code, period="daily", days=N) -> {"data":[{date,open,high,low,close,volume,...}]}`(`src/services/stock_service.py:88`)，与看板/引擎同源。
- 自选池 codes 统一 `_read_watchlist_codes(service)`(`api/v1/endpoints/stocks.py:69`，读 `STOCK_LIST`)。
- 新表只需定义 `class(Base)`；`Base.metadata.create_all`(`storage.py:866`) 自动建，新增表迁移安全。repo 仿 `backtest_repo.py`。
- 新配置：`SIGNAL_BACKTEST_*` 走范式①(config.py dataclass + from-env + config_registry)；`VPS_CRYPTO_*` 走范式②(`VPSConfig.from_env`)。两者都"不配置走默认"，同步 `.env.example`。
- A1 三重门评估器**自含新写**(`src/services/signal_backtest.py`)，复用 `compute_volume_price_signals`+`derive_price_levels`，**不**调用 `BacktestEngine.evaluate_single`(语义不符)；可借鉴其 `_evaluate_targets` 逐-bar 触线判定写法。
- 命中率回填 `resolve_marker_hit_fields(signal_type, code)` / `backfill_signal_hit_rate(signal_type, code)` **签名不变**，仅改聚合源(per-code BacktestResult → signal_stats by type×market)。
- 因果性：评估器逐 bar 只用 ≤t 数据(复用引擎既有防未来函数保证)。
- 提交信息英文类型前缀 + 中文，不加 `Co-Authored-By`。涉及配置/用户可见能力同步 `.env.example` + 文档 + `docs/CHANGELOG.md`(`[Unreleased]` 扁平 `- [类型] …`)。
- 执行：Subagent-Driven，无空格持久 worktree(`/root/<名>`)，前端 `npm ci` + 全量门禁。

## File Structure

- Create `src/services/signal_backtest.py` — 纯函数：三重门评估器 + 聚合 + Wilson CI(A1/A2)。
- Create `src/repositories/signal_stats_repo.py` — `signal_stats` 读写(A3)。
- Create `src/services/signal_backtest_service.py` — 自选池批作业编排(A5)。
- Modify `src/storage.py` — 新增 `SignalStatRow` ORM 表(A3)。
- Modify `src/config.py` + `src/core/config_registry.py` + `.env.example` — `SIGNAL_BACKTEST_*`(A4)。
- Modify `src/services/volume_price_signals.py` — CMF/MFI/形态/crypto 参数(B1/B3/B4) + 多源背离(B2)。
- Modify `src/services/signal_hit_rate.py` — 改读 signal_stats(A6)。
- Modify `src/services/signals_service.py` + `api/v1/schemas/stocks.py` + `src/services/signal_board_service.py` — ci 字段透传(A6)。
- Modify `main.py` — `--signal-backtest` CLI(A5)。
- Modify `apps/dsa-web/src/types/kline.ts` + `src/api/stocks.ts`(C1)；Create `apps/dsa-web/src/utils/credibility.ts`(C2)；Modify `SignalDrilldownPanel.tsx`/`StockSignalsPanel.tsx`/`SignalBoardGroup.tsx`(C2/C3)。
- Modify docs(D)。

---

# 阶段 M3-A · 可信度后端

### Task A1：三重门评估器（纯函数，bar-interval 无关）

**Files:**
- Create: `src/services/signal_backtest.py`
- Test: `tests/test_signal_backtest.py`

**Interfaces:**
- Consumes: `compute_volume_price_signals(df, *, config)`→`VPSResult.markers: list[VPSignal]`、`derive_price_levels(df, *, atr_mult, rr_target)`→`PriceLevels(entry,stop,target,risk_reward)`、`VPSConfig`（`volume_price_signals.py`）。
- Produces: `@dataclass(frozen=True) SignalOutcome(signal_type:str, market:str, outcome:str)`；`classify_triple_barrier(forward_bars: list[dict], *, stop: float, target: float) -> str`（'win'|'loss'|'expired'）；`evaluate_signal_outcomes(df, *, market: str, horizon: int, config=None, min_history: int = 40) -> list[SignalOutcome]`；`evaluate_baseline_outcomes(df, *, market: str, horizon: int, config=None, min_history: int = 40) -> list[SignalOutcome]`（signal_type 固定 `"__baseline__"`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_backtest.py
import pandas as pd
from src.services.signal_backtest import (
    classify_triple_barrier, evaluate_signal_outcomes, evaluate_baseline_outcomes, SignalOutcome,
)

def _bar(h, l, c=None): return {"high": h, "low": l, "close": c if c is not None else (h + l) / 2}

def test_triple_barrier_win_when_target_hit_first():
    fwd = [_bar(105, 99), _bar(112, 104)]  # 第2根先到 target=110
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "win"

def test_triple_barrier_loss_when_stop_hit_first():
    fwd = [_bar(104, 94), _bar(111, 100)]  # 第1根先破 stop=95
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "loss"

def test_triple_barrier_same_bar_both_is_conservative_loss():
    fwd = [_bar(111, 94)]  # 同根既触 target 又破 stop → 保守判 loss
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "loss"

def test_triple_barrier_expired_when_neither_touched():
    fwd = [_bar(106, 99), _bar(108, 101)]
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "expired"

def test_evaluate_signal_outcomes_tags_market_and_signal_type():
    # 构造足够长、含触发点的上升后回踩序列；断言产出带 market 与 signal_type
    df = _make_history_with_signals()
    outs = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert all(isinstance(o, SignalOutcome) and o.market == "cn" for o in outs)
    assert all(o.outcome in {"win", "loss", "expired"} for o in outs)

def test_baseline_uses_all_bars_not_only_triggers():
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    sig = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert all(o.signal_type == "__baseline__" for o in base)
    assert len(base) >= len(sig)  # 全体 bar 入场点 ≥ 信号触发点

def _make_history_with_signals():
    import numpy as np
    n = 80
    close = list(np.linspace(100, 130, 40)) + list(np.linspace(130, 120, 40))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": [1_000_000 + i * 10_000 for i in range(n)],
    })
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_backtest.py -q` Expected: FAIL（模块不存在）。
- [ ] **Step 3: 实现**

```python
# src/services/signal_backtest.py
"""信号规则级三重门回测（纯函数，bar-interval 无关）。
对历史逐 bar 因果重跑信号规则 + derive_price_levels，前瞻 horizon 根 bar 判 赢/输/平。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
from src.services.volume_price_signals import (
    compute_volume_price_signals, derive_price_levels, VPSConfig,
)

BASELINE_SIGNAL_TYPE = "__baseline__"

@dataclass(frozen=True)
class SignalOutcome:
    signal_type: str
    market: str
    outcome: str  # 'win' | 'loss' | 'expired'

def classify_triple_barrier(forward_bars: List[dict], *, stop: float, target: float) -> str:
    """逐根前瞻：先触 target=win，先破 stop=loss，同根两触保守判 loss，到期未触=expired。"""
    for bar in forward_bars:
        hit_target = bar["high"] >= target
        hit_stop = bar["low"] <= stop
        if hit_target and hit_stop:
            return "loss"  # 同 bar 两触：保守
        if hit_stop:
            return "loss"
        if hit_target:
            return "win"
    return "expired"

def _bars_as_dicts(df: pd.DataFrame) -> List[dict]:
    return df[["high", "low", "close"]].to_dict("records")

def _eval(df: pd.DataFrame, *, market: str, horizon: int, config: Optional[VPSConfig],
          all_bars: bool, min_history: int) -> List[SignalOutcome]:
    cfg = config or VPSConfig.from_env()
    df = df.reset_index(drop=True)
    out: List[SignalOutcome] = []
    n = len(df)
    for t in range(min_history, n - 1):  # 需留前瞻空间
        window = df.iloc[: t + 1]          # 仅用 ≤t 数据（因果）
        levels = derive_price_levels(window)
        if levels.stop is None or levels.target is None:
            continue
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue
        if all_bars:
            out.append(SignalOutcome(BASELINE_SIGNAL_TYPE, market,
                                     classify_triple_barrier(fwd, stop=levels.stop, target=levels.target)))
        else:
            res = compute_volume_price_signals(window, config=cfg)
            triggered = {m.signal_type for m in res.markers
                         if m.direction == "bullish" and m.timestamp == _last_ts(window)}
            for sig_type in triggered:
                out.append(SignalOutcome(sig_type, market,
                                         classify_triple_barrier(fwd, stop=levels.stop, target=levels.target)))
    return out

def _last_ts(window: pd.DataFrame) -> int:
    # 与引擎一致的"最新 bar 触发"判定：实现期核实 marker.timestamp 与 window 末 bar 的对齐方式
    from src.services.volume_price_signals import _to_epoch_ms_shanghai  # noqa
    return _to_epoch_ms_shanghai(str(window.iloc[-1]["date"]))

def evaluate_signal_outcomes(df, *, market, horizon, config=None, min_history=40):
    return _eval(df, market=market, horizon=horizon, config=config, all_bars=False, min_history=min_history)

def evaluate_baseline_outcomes(df, *, market, horizon, config=None, min_history=40):
    return _eval(df, market=market, horizon=horizon, config=config, all_bars=True, min_history=min_history)
```

> 实现期核实：marker"在最新 bar 触发"的判别（`_last_ts` 与 `m.timestamp` 对齐口径；A 类 detector 多在最末 bar 出点，若某 detector 出历史点需按 timestamp 精确匹配 t）。这是因果回测的关键，必须与引擎语义一致（参考 `_detect_latest_vfx`/`_detect_breakouts` 出点位置）。逐 bar 全量重跑较重——A5 限自选池+离线跑（见风险）。

- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_signal_backtest.py -q` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest.py
git commit -m "feat: 信号三重门回测评估器(因果前向走查/赢输平/全体bar基准)(M3-A)"
```

### Task A2：统计聚合 + Wilson CI + 基准超额（纯函数）

**Files:**
- Modify: `src/services/signal_backtest.py`
- Test: `tests/test_signal_backtest_stats.py`

**Interfaces:**
- Consumes: `SignalOutcome`、`BASELINE_SIGNAL_TYPE`（A1）。
- Produces: `wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]`；`@dataclass(frozen=True) SignalStat(signal_type, market, interval, horizon, win, loss, sample, win_rate, ci_low, ci_high, baseline_win_rate, excess)`；`aggregate_signal_stats(outcomes, baseline_outcomes, *, horizon: int, interval: str = "1d") -> list[SignalStat]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_backtest_stats.py
from src.services.signal_backtest import wilson_ci, aggregate_signal_stats, SignalOutcome, SignalStat

def test_wilson_ci_bounds_within_0_1_and_low_below_high():
    lo, hi = wilson_ci(7, 10)
    assert 0.0 <= lo < hi <= 1.0

def test_wilson_ci_small_sample_is_wide():
    lo_small, hi_small = wilson_ci(1, 2)
    lo_big, hi_big = wilson_ci(50, 100)
    assert (hi_small - lo_small) > (hi_big - lo_big)

def test_aggregate_groups_by_type_and_market_excludes_expired_from_sample():
    outs = [SignalOutcome("volume_breakout", "cn", "win")] * 6 + \
           [SignalOutcome("volume_breakout", "cn", "loss")] * 2 + \
           [SignalOutcome("volume_breakout", "cn", "expired")] * 5
    base = [SignalOutcome("__baseline__", "cn", "win")] * 5 + [SignalOutcome("__baseline__", "cn", "loss")] * 5
    stats = aggregate_signal_stats(outs, base, horizon=10)
    s = next(x for x in stats if x.signal_type == "volume_breakout" and x.market == "cn")
    assert s.win == 6 and s.loss == 2 and s.sample == 8        # expired 不计入
    assert abs(s.win_rate - 0.75) < 1e-9
    assert abs(s.baseline_win_rate - 0.5) < 1e-9
    assert abs(s.excess - (s.ci_low - 0.5)) < 1e-9
    assert s.interval == "1d" and s.horizon == 10

def test_aggregate_baseline_per_market():
    outs = [SignalOutcome("x", "crypto", "win")]
    base = [SignalOutcome("__baseline__", "crypto", "win"), SignalOutcome("__baseline__", "cn", "loss")]
    stats = aggregate_signal_stats(outs, base, horizon=5)
    s = next(x for x in stats if x.signal_type == "x")
    assert s.market == "crypto" and abs(s.baseline_win_rate - 1.0) < 1e-9  # 只用同 market 的 baseline
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_backtest_stats.py -q` Expected: FAIL。
- [ ] **Step 3: 实现**（追加到 `signal_backtest.py`）

```python
import math
from collections import defaultdict

@dataclass(frozen=True)
class SignalStat:
    signal_type: str
    market: str
    interval: str
    horizon: int
    win: int
    loss: int
    sample: int
    win_rate: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    baseline_win_rate: Optional[float]
    excess: Optional[float]

def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))

def _winrate(wins: int, n: int) -> Optional[float]:
    return round(wins / n, 4) if n > 0 else None

def aggregate_signal_stats(outcomes, baseline_outcomes, *, horizon, interval="1d"):
    # baseline per market（全体 bar 入场赢率）
    base_w = defaultdict(int); base_n = defaultdict(int)
    for o in baseline_outcomes:
        if o.outcome == "expired":
            continue
        base_n[o.market] += 1
        if o.outcome == "win":
            base_w[o.market] += 1
    baseline_rate = {m: _winrate(base_w[m], base_n[m]) for m in base_n}

    buckets = defaultdict(lambda: {"win": 0, "loss": 0})
    for o in outcomes:
        if o.outcome == "expired":
            continue
        buckets[(o.signal_type, o.market)][o.outcome] += 1

    stats = []
    for (sig_type, market), wl in buckets.items():
        win, loss = wl["win"], wl["loss"]
        sample = win + loss
        wr = _winrate(win, sample)
        ci_low, ci_high = wilson_ci(win, sample) if sample > 0 else (None, None)
        base = baseline_rate.get(market)
        excess = round(ci_low - base, 4) if (ci_low is not None and base is not None) else None
        stats.append(SignalStat(sig_type, market, interval, horizon, win, loss, sample,
                                wr, ci_low, ci_high, base, excess))
    return stats
```

- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_signal_backtest_stats.py -q` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_backtest_stats.py
git commit -m "feat: 信号统计聚合(type×市场)+Wilson CI+全体bar基准超额(M3-A)"
```

### Task A3：`signal_stats` 存储 + repo

**Files:**
- Modify: `src/storage.py`（新增 `SignalStatRow(Base)`，仿 `BacktestSummary`@357）
- Create: `src/repositories/signal_stats_repo.py`
- Test: `tests/test_signal_stats_repo.py`

**Interfaces:**
- Produces ORM `SignalStatRow`（列：`signal_type, market, interval, horizon, win, loss, sample, win_rate, ci_low, ci_high, baseline_win_rate, excess, computed_at`，唯一约束 `(signal_type, market, interval, horizon)`）。
- Produces `SignalStatsRepository(db_manager=None)`：`save_batch(rows: list[SignalStatRow], *, replace_existing: bool = True) -> int`；`get(signal_type: str, market: str, *, interval: str = "1d", horizon: Optional[int] = None) -> Optional[SignalStatRow]`（horizon None→取最新 computed_at）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_stats_repo.py
from src.storage import SignalStatRow
from src.repositories.signal_stats_repo import SignalStatsRepository

def _row(**kw):
    base = dict(signal_type="volume_breakout", market="cn", interval="1d", horizon=10,
                win=6, loss=2, sample=8, win_rate=0.75, ci_low=0.4, ci_high=0.9,
                baseline_win_rate=0.5, excess=-0.1)
    base.update(kw); return SignalStatRow(**base)

def test_save_and_get_roundtrip(tmp_db):  # tmp_db fixture: 隔离的 DatabaseManager
    repo = SignalStatsRepository(tmp_db)
    repo.save_batch([_row()])
    got = repo.get("volume_breakout", "cn", interval="1d", horizon=10)
    assert got is not None and got.win == 6 and got.sample == 8 and got.market == "cn"

def test_save_batch_replace_existing_overwrites_same_key(tmp_db):
    repo = SignalStatsRepository(tmp_db)
    repo.save_batch([_row(win=6, sample=8)])
    repo.save_batch([_row(win=9, sample=12)], replace_existing=True)
    got = repo.get("volume_breakout", "cn", horizon=10)
    assert got.win == 9 and got.sample == 12  # 不重复累积

def test_get_missing_returns_none(tmp_db):
    assert SignalStatsRepository(tmp_db).get("nope", "cn", horizon=10) is None
```

> 实现期：`tmp_db` fixture 复用 `tests/conftest.py` 既有的隔离 DatabaseManager 范式（若无，仿现有 repo 测试构造内存/临时库）。

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_stats_repo.py -q` Expected: FAIL。
- [ ] **Step 3: 实现**

```python
# src/storage.py —— 在 BacktestSummary 附近新增
class SignalStatRow(Base):
    """信号规则三重门回测统计（按 signal_type × market × interval × horizon 聚合）。"""
    __tablename__ = 'signal_stats'
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_type = Column(String(64), nullable=False, index=True)
    market = Column(String(16), nullable=False, index=True)   # cn/hk/us/crypto
    interval = Column(String(8), nullable=False, default='1d')
    horizon = Column(Integer, nullable=False)
    win = Column(Integer, default=0)
    loss = Column(Integer, default=0)
    sample = Column(Integer, default=0)
    win_rate = Column(Float)
    ci_low = Column(Float)
    ci_high = Column(Float)
    baseline_win_rate = Column(Float)
    excess = Column(Float)
    computed_at = Column(DateTime, default=datetime.now, index=True)
    __table_args__ = (
        UniqueConstraint('signal_type', 'market', 'interval', 'horizon',
                         name='uix_signal_stats_type_market_interval_horizon'),
    )
```

```python
# src/repositories/signal_stats_repo.py
from typing import List, Optional
import logging
from sqlalchemy import and_, delete, desc, select
from src.storage import SignalStatRow, DatabaseManager
logger = logging.getLogger(__name__)

class SignalStatsRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_batch(self, rows: List[SignalStatRow], *, replace_existing: bool = True) -> int:
        if not rows:
            return 0
        with self.db.get_session() as session:
            try:
                if replace_existing:
                    keys = sorted({(r.signal_type, r.market, r.interval, r.horizon) for r in rows})
                    for st, mk, iv, hz in keys:
                        session.execute(delete(SignalStatRow).where(and_(
                            SignalStatRow.signal_type == st, SignalStatRow.market == mk,
                            SignalStatRow.interval == iv, SignalStatRow.horizon == hz)))
                session.add_all(rows)
                session.commit()
                return len(rows)
            except Exception as exc:
                session.rollback(); logger.error(f"保存 signal_stats 失败: {exc}"); raise

    def get(self, signal_type: str, market: str, *, interval: str = "1d",
            horizon: Optional[int] = None) -> Optional[SignalStatRow]:
        with self.db.get_session() as session:
            cond = [SignalStatRow.signal_type == signal_type, SignalStatRow.market == market,
                    SignalStatRow.interval == interval]
            if horizon is not None:
                cond.append(SignalStatRow.horizon == int(horizon))
            q = select(SignalStatRow).where(and_(*cond)).order_by(desc(SignalStatRow.computed_at))
            return session.execute(q).scalars().first()
```

- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_signal_stats_repo.py -q` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/storage.py src/repositories/signal_stats_repo.py tests/test_signal_stats_repo.py
git commit -m "feat: 新增 signal_stats 表与 repo(type×市场×interval×horizon 唯一,覆盖写)(M3-A)"
```

### Task A4：配置（`SIGNAL_BACKTEST_*` 范式① + `VPS_CRYPTO_*` 范式②）

**Files:**
- Modify: `src/config.py`（dataclass 字段 + from-env，仿 `backtest_*`@889/@1712）
- Modify: `src/core/config_registry.py`（登记条目，仿 BACKTEST 条目@3148）
- Modify: `src/services/volume_price_signals.py`（`VPSConfig` 增 crypto 字段 + `from_env` 读 `VPS_CRYPTO_*`）
- Modify: `.env.example`（回测段@686 追加 + 量价段@848 追加）
- Test: `tests/test_signal_backtest_config.py`

**Interfaces:**
- Produces config 属性：`signal_backtest_enabled: bool = False`、`signal_backtest_horizon_bars: int = 10`。
- Produces `VPSConfig` 新增 crypto 旁路字段（如 `crypto_atr_period`、`crypto_breakout_window`、`crypto_breakout_rel_vol`，缺省=非 crypto 默认），并经 `VPSConfig.from_env` 读 `VPS_CRYPTO_*`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_backtest_config.py
import os
from src.config import Config
from src.services.volume_price_signals import VPSConfig

def test_signal_backtest_config_defaults(monkeypatch):
    for k in ("SIGNAL_BACKTEST_ENABLED", "SIGNAL_BACKTEST_HORIZON_BARS"):
        monkeypatch.delenv(k, raising=False)
    c = Config.from_env()
    assert c.signal_backtest_enabled is False
    assert c.signal_backtest_horizon_bars == 10

def test_signal_backtest_horizon_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_BACKTEST_HORIZON_BARS", "15")
    assert Config.from_env().signal_backtest_horizon_bars == 15

def test_vpsconfig_crypto_params_from_env(monkeypatch):
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    cfg = VPSConfig.from_env()
    assert cfg.crypto_breakout_window == 30
    # 非 crypto 默认不被污染
    monkeypatch.delenv("VPS_CRYPTO_BREAKOUT_WINDOW", raising=False)
    assert VPSConfig.from_env().crypto_breakout_window == VPSConfig().crypto_breakout_window
```

> 实现期核实 `Config` 工厂方法名（`Config.from_env` 或既有等价；按 `src/config.py:1712` 实际入口）。

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_backtest_config.py -q` Expected: FAIL。
- [ ] **Step 3: 实现**
  - `config.py` dataclass（仿 @889-896）加：`signal_backtest_enabled: bool = False`、`signal_backtest_horizon_bars: int = 10`。
  - `config.py` from-env（仿 @1712-1727）加：`signal_backtest_enabled=os.getenv('SIGNAL_BACKTEST_ENABLED','false').lower()=='true'`、`signal_backtest_horizon_bars=parse_env_int(os.getenv('SIGNAL_BACKTEST_HORIZON_BARS'),10,field_name='SIGNAL_BACKTEST_HORIZON_BARS',minimum=1)`。
  - `config_registry.py` 加 `SIGNAL_BACKTEST_ENABLED`(boolean)、`SIGNAL_BACKTEST_HORIZON_BARS`(integer, validation min1 max60) 两条目（仿 @3122/@3148；`category` 显式写 `"backtest"` 或在 @4567 加 `SIGNAL_BACKTEST_` 分支）。
  - `VPSConfig`(@29-43) 加 crypto 字段（默认=各自非 crypto 默认值）；`from_env`(@45-61) 末尾加 `crypto_breakout_window=int(parse_env_float(os.getenv("VPS_CRYPTO_BREAKOUT_WINDOW"), float(cls().breakout_window if hasattr ... ), ...))` 等（实现期按 VPSConfig 现有字段名对齐；crypto 默认回落到对应非 crypto 默认）。
  - `.env.example`：回测段(@686-701)追加 `# SIGNAL_BACKTEST_ENABLED=false` + `# SIGNAL_BACKTEST_HORIZON_BARS=10`；量价段(@848)追加 `VPS_CRYPTO_*` 注释块（全部注释掉，"不配走默认回落日线口径"）。

- [ ] **Step 4: 跑验证通过 + ai-assets** Run: `python -m pytest tests/test_signal_backtest_config.py -q` + `python scripts/check_ai_assets.py` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/config.py src/core/config_registry.py src/services/volume_price_signals.py .env.example tests/test_signal_backtest_config.py
git commit -m "feat: 新增 SIGNAL_BACKTEST_* 配置与 VPS_CRYPTO_* 旁路阈值(不配走默认)(M3-A)"
```

### Task A5：批作业服务 + CLI

**Files:**
- Create: `src/services/signal_backtest_service.py`
- Modify: `main.py`（`parse_arguments` 加 `--signal-backtest`，`main()` 加分派，仿 `--backtest`@915）
- Test: `tests/test_signal_backtest_service.py`

**Interfaces:**
- Consumes: `_read_watchlist_codes`、`StockService.get_history_data`、`get_market_for_stock`、`evaluate_signal_outcomes`/`evaluate_baseline_outcomes`/`aggregate_signal_stats`、`SignalStatsRepository`、`config.signal_backtest_horizon_bars`。
- Produces: `SignalBacktestService(db_manager=None)`：`run(*, codes: Optional[list] = None, horizon: Optional[int] = None) -> dict`（返回 `{processed, codes, stats_written, skipped, errors}`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_backtest_service.py
from unittest.mock import patch, MagicMock
from src.services.signal_backtest_service import SignalBacktestService
from src.services.signal_backtest import SignalOutcome

def _hist(): return {"data": [{"date": f"2026-01-{i+1:02d}", "open": 100, "high": 101,
                               "low": 99, "close": 100, "volume": 1_000_000} for i in range(80)]}

def test_run_aggregates_watchlist_and_writes_stats():
    with patch("src.services.signal_backtest_service._read_watchlist_codes", return_value=["600519", "BTC/USDT"]), \
         patch("src.services.signal_backtest_service.StockService") as SS, \
         patch("src.services.signal_backtest_service.get_market_for_stock", side_effect=["cn", "crypto"]), \
         patch("src.services.signal_backtest_service.evaluate_signal_outcomes",
               return_value=[SignalOutcome("volume_breakout", "cn", "win")]), \
         patch("src.services.signal_backtest_service.evaluate_baseline_outcomes",
               return_value=[SignalOutcome("__baseline__", "cn", "loss")]), \
         patch("src.services.signal_backtest_service.SignalStatsRepository") as Repo:
        SS.return_value.get_history_data.return_value = _hist()
        Repo.return_value.save_batch.return_value = 1
        out = SignalBacktestService().run(horizon=10)
        assert out["processed"] == 2
        assert Repo.return_value.save_batch.called

def test_run_skips_unknown_market_and_continues_on_error():
    with patch("src.services.signal_backtest_service._read_watchlist_codes", return_value=["X", "600519"]), \
         patch("src.services.signal_backtest_service.StockService") as SS, \
         patch("src.services.signal_backtest_service.get_market_for_stock", side_effect=[None, "cn"]), \
         patch("src.services.signal_backtest_service.evaluate_signal_outcomes", return_value=[]), \
         patch("src.services.signal_backtest_service.evaluate_baseline_outcomes", return_value=[]), \
         patch("src.services.signal_backtest_service.SignalStatsRepository"):
        SS.return_value.get_history_data.return_value = _hist()
        out = SignalBacktestService().run(horizon=10)
        assert out["skipped"] >= 1 and out["processed"] >= 1  # 未知市场跳过，其余继续
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_backtest_service.py -q` Expected: FAIL。
- [ ] **Step 3: 实现**

```python
# src/services/signal_backtest_service.py
"""自选池信号三重门回测批作业：逐股评估 → 按 (type×market) 聚合 → 落 signal_stats。"""
import logging
from typing import List, Optional
import pandas as pd
from src.config import get_config
from src.services.stock_service import StockService
from src.core.trading_calendar import get_market_for_stock
from src.services.signal_backtest import (
    evaluate_signal_outcomes, evaluate_baseline_outcomes, aggregate_signal_stats,
)
from src.repositories.signal_stats_repo import SignalStatsRepository
from src.storage import SignalStatRow
from api.v1.endpoints.stocks import _read_watchlist_codes
from src.services.system_config_service import SystemConfigService
logger = logging.getLogger(__name__)

class SignalBacktestService:
    def __init__(self, db_manager=None):
        self.repo = SignalStatsRepository(db_manager)

    def run(self, *, codes: Optional[List[str]] = None, horizon: Optional[int] = None) -> dict:
        cfg = get_config()
        hz = int(horizon or getattr(cfg, "signal_backtest_horizon_bars", 10))
        if codes is None:
            codes = _read_watchlist_codes(SystemConfigService())
        svc = StockService()
        all_sig, all_base = [], []
        processed = skipped = errors = 0
        for code in codes:
            try:
                market = get_market_for_stock(code)
                if market is None:
                    skipped += 1; continue
                hist = svc.get_history_data(stock_code=code, period="daily", days=365)
                rows = (hist or {}).get("data") or []
                if len(rows) < 50:
                    skipped += 1; continue
                df = pd.DataFrame(rows)
                all_sig.extend(evaluate_signal_outcomes(df, market=market, horizon=hz))
                all_base.extend(evaluate_baseline_outcomes(df, market=market, horizon=hz))
                processed += 1
            except Exception as exc:  # 单股失败不拖垮整批
                errors += 1; logger.warning("信号回测跳过 %s: %s", code, exc)
        stats = aggregate_signal_stats(all_sig, all_base, horizon=hz)
        orm_rows = [SignalStatRow(signal_type=s.signal_type, market=s.market, interval=s.interval,
                                  horizon=s.horizon, win=s.win, loss=s.loss, sample=s.sample,
                                  win_rate=s.win_rate, ci_low=s.ci_low, ci_high=s.ci_high,
                                  baseline_win_rate=s.baseline_win_rate, excess=s.excess) for s in stats]
        written = self.repo.save_batch(orm_rows, replace_existing=True)
        return {"processed": processed, "codes": len(codes), "stats_written": written,
                "skipped": skipped, "errors": errors}
```

```python
# main.py —— parse_arguments() 加（仿 --backtest）：
#   parser.add_argument('--signal-backtest', action='store_true', help='对自选池跑信号三重门回测并落 signal_stats')
# main() 加分派块（仿 @915-929）：
#   if getattr(args, 'signal_backtest', False):
#       from src.services.signal_backtest_service import SignalBacktestService
#       stats = SignalBacktestService().run()
#       logger.info("信号回测完成: %s", stats); print(stats); return 0
```

> 实现期：`_read_watchlist_codes` 从 `api/v1/endpoints/stocks.py` import 可能引入 FastAPI 依赖链——若 import 过重/循环，改为在 service 内复用其等价逻辑（读 `SystemConfigService().get_config()` 的 `STOCK_LIST` split），核实后择一。CLI 测试可加一条 `monkeypatch` `SignalBacktestService.run` 断言分派命中。

- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_signal_backtest_service.py -q` + `python -m py_compile main.py` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest_service.py main.py tests/test_signal_backtest_service.py
git commit -m "feat: 信号回测批作业(自选池逐股→type×市场聚合落库)+--signal-backtest CLI(M3-A)"
```

### Task A6：命中率改源 + 契约扩 CI/超额字段（端到端打通可信度）

**Files:**
- Modify: `src/services/signal_hit_rate.py`（`resolve_marker_hit_fields` 改读 signal_stats by (signal_type, market)）
- Modify: `api/v1/schemas/stocks.py`（`SignalMarker`@111 + `BoardEntry`@162 增 `ci_low/ci_high/baseline_excess`）
- Modify: `src/services/signals_service.py`（`_marker_from_vpsignal`@120 透传新字段）
- Modify: `src/services/signal_board_service.py`（`_hit_fields_from_markers`@147 透传新字段）
- Test: `tests/test_signal_hit_rate.py`（扩充）+ `tests/test_signals_service.py`（扩充）

**Interfaces:**
- `resolve_marker_hit_fields(signal_type, code) -> dict`：键扩为 `{hit_rate, hit_sample, verified, ci_low, ci_high, baseline_excess}`；聚合源改为 `SignalStatsRepository().get(signal_type, market(code))`；`verified = sample≥min_sample 且 ci_low > baseline_win_rate`。
- `backfill_signal_hit_rate(signal_type, code)` 同样改读 signal_stats（或被 resolve 直接取代内部实现，签名不变）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_hit_rate.py —— 追加（mock signal_stats 源）
from unittest.mock import patch, MagicMock
from src.services.signal_hit_rate import resolve_marker_hit_fields

def _stat(**kw):
    m = MagicMock(); d = dict(sample=20, win_rate=0.68, ci_low=0.55, ci_high=0.80,
                              baseline_win_rate=0.50, excess=0.05); d.update(kw)
    for k, v in d.items(): setattr(m, k, v)
    return m

def test_resolve_reads_signal_stats_by_market_and_sets_verified_on_excess(monkeypatch):
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat()
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["hit_rate"] == 0.68 and f["hit_sample"] == 20
        assert f["ci_low"] == 0.55 and f["baseline_excess"] == 0.05
        assert f["verified"] is True   # 样本足 且 ci_low(0.55) > baseline(0.50)
        Repo.return_value.get.assert_called_with("volume_breakout", "cn")

def test_resolve_not_verified_when_ci_low_below_baseline(monkeypatch):
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(ci_low=0.45, baseline_win_rate=0.50, excess=-0.05)
        assert resolve_marker_hit_fields("x", "600519")["verified"] is False  # 无超额

def test_resolve_missing_bucket_is_sample_insufficient(monkeypatch):
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = None
        f = resolve_marker_hit_fields("x", "600519")
        assert f == {"hit_rate": None, "hit_sample": None, "verified": False,
                     "ci_low": None, "ci_high": None, "baseline_excess": None}
```

```python
# tests/test_signals_service.py —— 追加：marker 透传 ci 字段
def test_marker_carries_ci_fields_from_resolver():
    from src.services.signals_service import build_signals_payload
    def resolver(signal_type, code):
        return {"hit_rate": 0.68, "hit_sample": 20, "verified": True,
                "ci_low": 0.55, "ci_high": 0.80, "baseline_excess": 0.05}
    payload = build_signals_payload(engine_result=_engine_with_one_rule_marker(), rule_signal=None,
                                    latest_bar_date="2026-06-16", llm_record=None,
                                    trading_days_elapsed=None, code="600519", hit_fields_resolver=resolver)
    m = next(x for x in payload["markers"] if x["source"] == "rule")
    assert m["ci_low"] == 0.55 and m["ci_high"] == 0.80 and m["baseline_excess"] == 0.05
```

> 实现期：补 `_engine_with_one_rule_marker()` 辅助（构造含一个 bullish VPSignal 的 `VPSResult`）。

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_signal_hit_rate.py tests/test_signals_service.py -q` Expected: FAIL。
- [ ] **Step 3: 实现**
  - `signal_hit_rate.py`：import `get_market_for_stock`、`SignalStatsRepository`；重写 `resolve_marker_hit_fields`：
    ```python
    def resolve_marker_hit_fields(signal_type: str, code: str) -> dict:
        none_fields = {"hit_rate": None, "hit_sample": None, "verified": False,
                       "ci_low": None, "ci_high": None, "baseline_excess": None}
        market = get_market_for_stock(code)
        if market is None:
            return dict(none_fields)
        stat = SignalStatsRepository().get(signal_type, market)
        if stat is None or (stat.sample or 0) <= 0:
            return dict(none_fields)
        cfg = get_config()
        min_sample = int(getattr(cfg, "signal_hit_verified_min_sample", 0) or 0) \
            or int(getattr(cfg, "backtest_eval_window_days", 10))
        verified = (stat.sample >= min_sample and stat.ci_low is not None
                    and stat.baseline_win_rate is not None and stat.ci_low > stat.baseline_win_rate)
        return {"hit_rate": stat.win_rate, "hit_sample": stat.sample, "verified": bool(verified),
                "ci_low": stat.ci_low, "ci_high": stat.ci_high, "baseline_excess": stat.excess}
    ```
    （`backfill_signal_hit_rate` 旧 per-code BacktestResult 实现保留或标注 deprecated；marker 链路只走 `resolve_marker_hit_fields`。）
  - `api/v1/schemas/stocks.py`：`SignalMarker` 加 `ci_low: Optional[float] = None`、`ci_high: Optional[float] = None`、`baseline_excess: Optional[float] = None`；`BoardEntry` 同加三字段。
  - `signals_service.py` `_marker_from_vpsignal`@154-164：回填段补 `marker["ci_low"]=fields.get("ci_low"); marker["ci_high"]=fields.get("ci_high"); marker["baseline_excess"]=fields.get("baseline_excess")`；并在 marker dict 默认值(@135-153)加这三键默认 None。
  - `signal_board_service.py` `_hit_fields_from_markers`@147-152：返回 dict 补这三键（取首个 rule marker 的对应值）。

- [ ] **Step 4: 跑验证通过 + 回归** Run: `python -m pytest tests/test_signal_hit_rate.py tests/test_signals_service.py tests/test_signal_board_service.py tests/test_signals_endpoint.py -q` Expected: PASS（含既有用例：缺桶→样本不足 与旧"无样本"表现一致）。
- [ ] **Step 5: Commit**

```bash
git add src/services/signal_hit_rate.py api/v1/schemas/stocks.py src/services/signals_service.py src/services/signal_board_service.py tests/
git commit -m "feat: 命中率改读 signal_stats(type×市场)+契约增 CI/基准超额,verified=超额(M3-A)"
```

---

# 阶段 M3-B · 量价丰富度引擎

### Task B1：CMF / MFI 量能指标（纯函数）

**Files:**
- Modify: `src/services/volume_price_signals.py`（`_cmf`/`_mfi` 仿 `_obv`@582；中间量进 `_compute_primitives`@132）
- Test: `tests/test_volume_price_signals.py`（追加）

**Interfaces:**
- Produces: `_cmf(high, low, close, volume, window) -> pd.Series`、`_mfi(high, low, close, volume, window) -> pd.Series`（模块级私有纯指标）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_volume_price_signals.py —— 追加
import pandas as pd
from src.services.volume_price_signals import _cmf, _mfi

def test_cmf_all_closes_at_high_is_positive():
    n = 30
    df = pd.DataFrame({"high": [10]*n, "low": [8]*n, "close": [10]*n, "volume": [1000]*n})
    s = _cmf(df["high"], df["low"], df["close"], df["volume"], window=20)
    assert s.iloc[-1] > 0  # 收在最高 → 资金流为正

def test_mfi_bounded_0_100():
    n = 40
    import numpy as np
    close = pd.Series(np.linspace(10, 20, n))
    df = pd.DataFrame({"high": close*1.01, "low": close*0.99, "close": close, "volume": [1000]*n})
    s = _mfi(df["high"], df["low"], df["close"], df["volume"], window=14)
    v = s.dropna()
    assert ((v >= 0) & (v <= 100)).all()
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_volume_price_signals.py -k "cmf or mfi" -q` Expected: FAIL。
- [ ] **Step 3: 实现**（仿 `_obv`@582 放指标区）

```python
def _cmf(high, low, close, volume, window: int) -> pd.Series:
    rng = (high - low).where((high - low) != 0)
    mfm = ((close - low) - (high - close)) / rng        # Money Flow Multiplier
    mfv = (mfm.fillna(0.0)) * volume                     # Money Flow Volume
    return mfv.rolling(window).sum() / volume.rolling(window).sum().where(lambda s: s != 0)

def _mfi(high, low, close, volume, window: int) -> pd.Series:
    tp = (high + low + close) / 3.0                      # typical price
    rmf = tp * volume                                    # raw money flow
    delta = tp.diff()
    pos = rmf.where(delta > 0, 0.0).rolling(window).sum()
    neg = rmf.where(delta < 0, 0.0).rolling(window).sum()
    mr = pos / neg.where(neg != 0)
    return 100 - (100 / (1 + mr))
```

- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_volume_price_signals.py -k "cmf or mfi" -q` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py
git commit -m "feat: 量价引擎新增 CMF/MFI 量能指标纯函数(M3-B)"
```

### Task B2：多源背离共振 + 强度分级

**Files:**
- Modify: `src/services/volume_price_signals.py`（`_detect_obv_divergence`@588 推广为多源共振 + 强度）
- Test: `tests/test_volume_price_signals.py`（追加）

**Interfaces:**
- Consumes: `_obv`/`_cmf`/`_mfi`、`find_swing_pivots`。
- Produces: 背离 detector 在同一对 pivot 上要求 ≥2 源同向背离才出高置信 marker；marker `reason` 含强度档（weak/medium/strong），共振源数越多 confidence 越高；单源仅弱提示（不出 high 置信）。signal_type 保持 `obv_top_divergence`/`obv_bottom_divergence`（兼容看板/契约）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_volume_price_signals.py —— 追加
from src.services.volume_price_signals import compute_volume_price_signals, VPSConfig

def test_single_source_divergence_does_not_emit_high_confidence():
    df = _df_with_only_obv_divergence()   # 仅 OBV 背离、CMF/MFI 不背离
    res = compute_volume_price_signals(df, config=VPSConfig())
    div = [m for m in res.markers if "divergence" in m.signal_type]
    assert all(m.confidence != "high" for m in div)

def test_multi_source_resonance_emits_higher_confidence_and_strength():
    df = _df_with_obv_cmf_mfi_all_diverging()
    res = compute_volume_price_signals(df, config=VPSConfig())
    div = [m for m in res.markers if "divergence" in m.signal_type]
    assert any(m.confidence in ("high", "medium") for m in div)
    assert any(("strong" in m.reason or "medium" in m.reason or "weak" in m.reason) for m in div)
```

> 实现期：补 `_df_with_only_obv_divergence` / `_df_with_obv_cmf_mfi_all_diverging` 构造器（参考既有背离测试 fixture，确保防未来函数判别力，沿用 c6959869 风格）。

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_volume_price_signals.py -k divergence -q` Expected: FAIL。
- [ ] **Step 3: 实现**：在 `_detect_obv_divergence`（或重命名 `_detect_volume_divergence`，保留旧名为别名）的 pivot 对迭代里，对每对 pivot 同时检查 OBV/CMF/MFI 是否同向背离；统计共振源数 `k`；`k>=2` 出 marker：`confidence = "high" if k>=3 else "medium"`，`reason` 含强度档（按背离幅度×源数映射 weak/medium/strong）；`k==1` 出 `confidence="low"` 弱提示（或按 spec 不出，二选一并测试固定）。保持 `is_daily_approx`、anchor、direction、signal_type 不变。

- [ ] **Step 4: 跑验证通过 + 全引擎回归** Run: `python -m pytest tests/test_volume_price_signals.py -q` Expected: PASS（既有背离/八法/突破测试不破）。
- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py
git commit -m "feat: 量价背离升级为 OBV+CMF+MFI 多源共振+强度分级(单源降为弱提示)(M3-B)"
```

### Task B3：量能形态分级

**Files:**
- Modify: `src/services/volume_price_signals.py`（基于 rel_vol×ATR 给量能形态档，进 marker reason/语义）
- Test: `tests/test_volume_price_signals.py`（追加）

**Interfaces:**
- Produces: `classify_volume_pattern(rel_vol: float, pct_chg: float, atr_norm: float, config) -> str`（'mild_expand'|'climax_volume'|'dry_up'|'shrink_pullback'|'normal'），并把档位文案注入相关 marker。

- [ ] **Step 1: 写失败测试**

```python
def test_volume_pattern_classification_boundaries():
    from src.services.volume_price_signals import classify_volume_pattern, VPSConfig
    c = VPSConfig()
    assert classify_volume_pattern(3.0, 0.05, 1.0, c) == "climax_volume"   # 天量
    assert classify_volume_pattern(0.5, -0.01, 0.3, c) == "dry_up"         # 地量
    assert classify_volume_pattern(0.7, -0.02, 0.5, c) == "shrink_pullback"
    assert classify_volume_pattern(1.3, 0.02, 0.6, c) == "mild_expand"
    assert classify_volume_pattern(1.0, 0.0, 0.5, c) == "normal"
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_volume_price_signals.py -k volume_pattern -q` Expected: FAIL。
- [ ] **Step 3: 实现** `classify_volume_pattern`（阈值复用/扩展 `VPSConfig` 量档常量）+ 在出 marker 处把档位拼进 `reason`（不新增 signal_type，避免契约扩散）。
- [ ] **Step 4: 跑验证通过** Run: `python -m pytest tests/test_volume_price_signals.py -k volume_pattern -q` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py
git commit -m "feat: 量能形态分级(天量/地量/缩量回踩/温和放量)注入信号语义(M3-B)"
```

### Task B4：crypto 量价参数差异化

**Files:**
- Modify: `src/services/volume_price_signals.py`（`compute_volume_price_signals` 支持按 market 取参数；或调用方传 crypto 版 `VPSConfig`）
- Modify: `src/services/signal_board_service.py`（`build_signals_for_code`@81 传市场对应 config）
- Test: `tests/test_volume_price_signals.py` + `tests/test_signal_board_service.py`（追加）

**Interfaces:**
- Produces: `VPSConfig.for_market(market: str) -> VPSConfig`（crypto 用 `VPS_CRYPTO_*` 值，其它回落默认）；`build_signals_for_code` 用 `compute_volume_price_signals(df, config=VPSConfig.for_market(market))`。

- [ ] **Step 1: 写失败测试**

```python
def test_vpsconfig_for_market_crypto_differs(monkeypatch):
    from src.services.volume_price_signals import VPSConfig
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    assert VPSConfig.for_market("crypto").breakout_window == 30
    assert VPSConfig.for_market("cn").breakout_window == VPSConfig().breakout_window  # 非 crypto 不变
```

- [ ] **Step 2: 跑验证失败** Run: `python -m pytest tests/test_volume_price_signals.py -k for_market -q` Expected: FAIL。
- [ ] **Step 3: 实现** `VPSConfig.for_market`（classmethod：market=='crypto' 时用 `VPS_CRYPTO_*`（A4 已加）覆盖突破窗口/ATR/量档，否则 `from_env()`）；`build_signals_for_code` 已知 `market`（`BoardSignals.market`），改用 `for_market(market)`。
- [ ] **Step 4: 跑验证通过 + 回归** Run: `python -m pytest tests/test_volume_price_signals.py tests/test_signal_board_service.py -q` Expected: PASS（非 crypto 逐字节不变）。
- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py src/services/signal_board_service.py tests/
git commit -m "feat: crypto 量价参数差异化(VPSConfig.for_market,非crypto回落默认)(M3-B)"
```

---

# 阶段 M3-C · 前端可信度展示

### Task C1：前端类型 + mapper 扩 CI/超额字段

**Files:**
- Modify: `apps/dsa-web/src/types/kline.ts`（`SignalMarker`@28 + `BoardEntry`@65 加 `ciLow/ciHigh/baselineExcess`）
- Modify: `apps/dsa-web/src/api/stocks.ts`（`RawSignalMarker`@22 + `mapSignalMarker`@49 + `RawBoardEntry`@68 + `mapBoardEntry`@82）
- Test: `apps/dsa-web/src/api/__tests__/stocks.signals.test.ts` + `stocks.board.test.ts`（追加）

**Interfaces:**
- Produces: TS `SignalMarker` 与 `BoardEntry` 增 `ciLow: number|null; ciHigh: number|null; baselineExcess: number|null;`；mapper 从 `ci_low/ci_high/baseline_excess` 映射（`?? null`）。

- [ ] **Step 1: 写失败测试**（在 stocks.signals.test.ts 现有 mapper 断言旁追加）

```ts
it('maps credibility CI and baseline excess (snake→camel)', async () => {
  get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
    price_lines: { entry: 1, stop: 0.9, target: 1.2 },
    markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish', signal_type: 'volume_breakout',
      source: 'rule', confidence: 'high', is_daily_approx: false, is_anomalous: false, reason: 'x',
      threshold: null, observed_value: null, hit_rate: 0.68, hit_sample: 20, verified: true, as_of: null,
      ci_low: 0.55, ci_high: 0.8, baseline_excess: 0.05 }] } });
  const res = await stocksApi.getSignals('600519');
  expect(res.markers[0].ciLow).toBe(0.55);
  expect(res.markers[0].ciHigh).toBe(0.8);
  expect(res.markers[0].baselineExcess).toBe(0.05);
});
```

- [ ] **Step 2: 跑验证失败** Run: `cd apps/dsa-web && npx vitest run src/api/__tests__/stocks.signals.test.ts` Expected: FAIL（tsc/断言）。
- [ ] **Step 3: 实现** `types/kline.ts` 两 interface 加三字段；`stocks.ts` `RawSignalMarker`/`RawBoardEntry` 加 `ci_low/ci_high/baseline_excess: number|null`，`mapSignalMarker`/`mapBoardEntry` 加 `ciLow: raw.ci_low ?? null` 等。
- [ ] **Step 4: 跑验证通过** Run: `npx vitest run src/api/__tests__/stocks.signals.test.ts src/api/__tests__/stocks.board.test.ts` + `npx tsc --noEmit` Expected: PASS / clean。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/
git commit -m "feat: 前端 SignalMarker/BoardEntry 增 ci/基准超额类型与 mapper(M3-C)"
```

### Task C2：共享可信度格式化 util + 收敛三处口径

**Files:**
- Create: `apps/dsa-web/src/utils/credibility.ts`
- Modify: `SignalDrilldownPanel.tsx`(@13)、`StockSignalsPanel.tsx`(@46)、`SignalBoardGroup.tsx`(@11) 改用共享 util
- Test: `apps/dsa-web/src/utils/__tests__/credibility.test.ts`

**Interfaces:**
- Produces: `formatHitRate({ hitRate, hitSample }): string`（统一口径：无样本→`'暂无样本'`，否则 `'X% · n'`）；`formatCi({ ciLow, ciHigh }): string|null`（`'[a–b]'` 或 null）；`formatExcess(baselineExcess): string|null`（`'超额 +Δpp'`/`'无超额'`/null）；`verifiedLabel(verified): string`（`'已验证'`/`'未验证'`）。接受最小字段对象，不绑定具体 interface。

- [ ] **Step 1: 写失败测试**

```ts
// apps/dsa-web/src/utils/__tests__/credibility.test.ts
import { describe, it, expect } from 'vitest';
import { formatHitRate, formatCi, formatExcess, verifiedLabel } from '../credibility';

describe('credibility format', () => {
  it('formats hit rate with sample, empty when no sample', () => {
    expect(formatHitRate({ hitRate: 0.62, hitSample: 18 })).toBe('62% · 18');
    expect(formatHitRate({ hitRate: null, hitSample: null })).toBe('暂无样本');
    expect(formatHitRate({ hitRate: 0.62, hitSample: 0 })).toBe('暂无样本');
  });
  it('formats CI band and excess', () => {
    expect(formatCi({ ciLow: 0.55, ciHigh: 0.8 })).toBe('[55%–80%]');
    expect(formatCi({ ciLow: null, ciHigh: null })).toBeNull();
    expect(formatExcess(0.05)).toBe('超额 +5pp');
    expect(formatExcess(-0.03)).toBe('无超额');
    expect(formatExcess(null)).toBeNull();
  });
  it('verified label', () => {
    expect(verifiedLabel(true)).toBe('已验证');
    expect(verifiedLabel(false)).toBe('未验证');
  });
});
```

- [ ] **Step 2: 跑验证失败** Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/credibility.test.ts` Expected: FAIL。
- [ ] **Step 3: 实现** `credibility.ts`（纯函数，仿 `utils/cn.ts` 单文件范式）；把三处本地 `fmtHit`/`formatHitRate`/inline 三元改为调用共享 `formatHitRate`（统一口径 `'X% · n'`，verified badge 仍各自渲染）。**注意**：三处现有口径不同（钻取带「命中率/样本」、工作台带「已验证」、看板带样本数）；统一后会改动现有断言——同步更新 `SignalDrilldownPanel.test.tsx`/`StockSignalsPanel.test.tsx`/`SignalBoard.test.tsx` 里命中率文案断言。

- [ ] **Step 4: 跑验证通过** Run: `npx vitest run src/utils src/components/kline src/components/workstation src/components/board` + `npx tsc --noEmit` + `npx eslint src/utils/credibility.ts` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/utils/credibility.ts apps/dsa-web/src/utils/__tests__/ apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx apps/dsa-web/src/components/board/SignalBoardGroup.tsx apps/dsa-web/src/components/*/__tests__/
git commit -m "refactor: 抽共享可信度格式化 util,收敛三处命中率口径(M3-C)"
```

### Task C3：三处展示 CI / 样本 / 超额 / verified

**Files:**
- Modify: `SignalDrilldownPanel.tsx`(@64 可信度行)、`StockSignalsPanel.tsx`(@46 命中率单元)、`SignalBoardGroup.tsx`(@49 命中率单元)
- Test: 对应三个 `__tests__`（追加）

**Interfaces:**
- Consumes: `formatCi`/`formatExcess`/`verifiedLabel`（C2）、marker/entry 的 `ciLow/ciHigh/baselineExcess`（C1）。

- [ ] **Step 1: 写失败测试**（各加一条，以钻取为例）

```tsx
// SignalDrilldownPanel.test.tsx —— 追加
it('shows CI band and excess for a verified rule marker', () => {
  render(<SignalDrilldownPanel marker={ruleMarker({ hitRate: 0.68, hitSample: 20, verified: true,
    ciLow: 0.55, ciHigh: 0.8, baselineExcess: 0.05 })} />);
  expect(screen.getByTestId('drilldown-ci')).toHaveTextContent('[55%–80%]');
  expect(screen.getByTestId('drilldown-excess')).toHaveTextContent('超额 +5pp');
  expect(screen.getByTestId('drilldown-verified')).toHaveTextContent('已验证');
});
```

> 工作台/看板各加同形断言（`StockSignalsPanel`：命中率单元含 CI/超额；`SignalBoard`：行内含 CI/超额 span，并确保新增 span 不破坏既有列/排序断言）。

- [ ] **Step 2: 跑验证失败** Run: `cd apps/dsa-web && npx vitest run src/components/kline src/components/workstation src/components/board` Expected: FAIL。
- [ ] **Step 3: 实现** 三处在命中率/可信度区追加 `formatCi`/`formatExcess` 输出（带 `data-testid` `drilldown-ci`/`drilldown-excess` 等）；`verified` 用 `text-success`，无超额/样本不足用 `text-secondary-text`；空值不渲染该 span。看板若加列需同步 `colSpan`/表头（建议同 td 内追加 span，不新增列以免动 colSpan）。

- [ ] **Step 4: 跑验证通过** Run: `npx vitest run src/components` + `npx tsc --noEmit` Expected: PASS。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx apps/dsa-web/src/components/board/SignalBoardGroup.tsx apps/dsa-web/src/components/*/__tests__/
git commit -m "feat: 钻取/工作台信号tab/看板行展示 CI/样本/基准超额/已验证(M3-C)"
```

---

# 阶段 M3-D · 门禁 + 文档

### Task D1：全量门禁 + CHANGELOG + 专题文档

**Files:**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加）
- Modify: `docs/volume-price-signals.md`（CMF/MFI/多源共振/形态/crypto 参数）
- Create: `docs/signal-credibility.md`（三重门回测/type×市场胜率/Wilson CI+基准超额/批作业/配置/展示）

- [ ] **Step 1: 后端全量门禁** Run: `./scripts/ci_gate.sh`（syntax + flake8 关键项 + 确定性 + 离线 pytest）Expected: 全绿（新表/新服务/新测试通过）。
- [ ] **Step 2: 前端全量门禁（无空格 worktree）** Run: `cd apps/dsa-web && npx vitest run && npx eslint . && npm run build` Expected: 全绿。
- [ ] **Step 3: CHANGELOG（扁平、无类目标题）**

```
- [新功能] 信号引擎新增可信度层(M3)：对自选池信号做规则级三重门回测(目标/止损/到期)，按「信号类型×市场」给出 Wilson 置信区间胜率与"相对全体bar入场基准"的超额，verified 升级为"样本足且置信下界超基准"；命中率回填改读预计算 signal_stats；新增 SIGNAL_BACKTEST_* 配置与 --signal-backtest 批作业
- [新功能] 量价引擎新增 CMF/MFI 量能指标、OBV+CMF+MFI 多源背离共振与强度分级、量能形态分级，crypto 量价参数差异化(VPS_CRYPTO_*)
- [改进] 钻取面板/工作台信号tab/信号看板行展示可信度(置信区间/样本/基准超额/已验证)，并抽出共享格式化口径
```

- [ ] **Step 4: 专题文档** 写 `docs/signal-credibility.md`（核实命令/配置项/表名/字段与实现一致）+ 更新 `docs/volume-price-signals.md`。
- [ ] **Step 5: Commit**

```bash
git add docs/CHANGELOG.md docs/volume-price-signals.md docs/signal-credibility.md
git commit -m "docs: M3 信号可信度+量价丰富度 CHANGELOG 与专题文档(M3-D)"
```

---

## 验证矩阵（交付说明）
- **改了什么**：新增三重门回测评估器(纯函数) + signal_stats 表/repo + 自选池批作业(--signal-backtest) + 配置；命中率回填改读 (type×市场) 统计并扩 CI/基准超额；引擎新增 CMF/MFI、多源背离共振、量能形态、crypto 参数；前端三处展示可信度。
- **为什么**：把信号从"看图标注"升级为"有回测背书、分层胜率、多源研判"的可信可操作买卖信号(M3，零新数据源)。
- **验证情况**：后端 `ci_gate.sh`(含新单测)；前端 `vitest`+`eslint .`+`build`；无空格 worktree。
- **未验证项**：批作业在真实自选池+真实历史下的端到端跑(需 `python main.py --signal-backtest` 实跑一轮，度量耗时)；前端三处真实后端目检。
- **风险点**：逐 bar 全量重跑回测成本(限自选池+离线)；命中率改源回归(签名不变+缺桶=样本不足兜底+回归测试)；多源共振减少信号数(单源降为弱提示);crypto 参数污染(默认回落+回归);三处口径统一改动既有断言。
- **回滚方式**：纯增量；回退引擎改动/评估器/批作业/前端展示即可；signal_stats 为新表，命中率 resolver 可回退到旧 per-code 实现。

## Self-Review（撰写者自检）

- **Spec 覆盖**：A1/A2(三重门+Wilson+基准 D1/D4)、A3(存储 D3/§6)、A4(配置 §6)、A5(批作业 D3/D7)、A6(命中率改源 type×市场 D2 + 契约)、B1/B2/B3(D5 量价丰富度)、B4(D9 crypto)、C1/C2/C3(D8 三处展示)、D(门禁+文档)。✅ 覆盖 D1–D9 + 非目标未越界(无换手率/资金面/周月线/实时)。
- **占位扫描**：标注了 5 处"实现期核实"(marker 触发对齐、tmp_db fixture、Config 工厂名、_read_watchlist_codes import、背离/形态阈值固化)——均为对接现有代码的核实点而非空缺设计，附了判别依据。
- **类型一致**：`SignalOutcome`/`SignalStat`/`SignalStatRow` 字段贯穿 A1→A2→A3→A5 一致；`resolve_marker_hit_fields` 返回键(6 个)在 A6 与 C1/C2/C3 前端字段(`ciLow/ciHigh/baselineExcess`)一一对应；`get_market_for_stock` 四值域统一。
