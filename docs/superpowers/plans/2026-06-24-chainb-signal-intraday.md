# 链路B 信号可信度分钟化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把信号三重门可信度回测(链路B/M3)扩展到分钟粒度——分钟 bar 上重算 VPS 信号 + 分钟前向三重门,产出 `(signal_type×market×interval×horizon)` 隔离的 `signal_stats`,并提供最小 interval-aware 读出。

**Architecture:** `interval` 贯穿 `SignalBacktestService.run`;分钟取数分叉到链路A 的 `get_intraday_data`(market 路由)并 `datetime→date` 适配;核心修 `_to_epoch_ms_shanghai` 使其分钟分辨率感知(向后兼容日线),让信号触发对齐在分钟 bar 上成立;`signal_stats`/`aggregate_signal_stats` 已支持 interval(零迁移);读路径加可选 `interval`(默认 1d 不变)。

**Tech Stack:** Python 3.10 / SQLAlchemy(SQLite)/ FastAPI / pytest;复用链路A `DataFetcherManager.get_intraday_data` 与 `intraday_backtest.validate_interval`。

## Global Constraints

- 用中文交流;代码/注释/commit 按文件语境;commit message 英文类型前缀 + 中文体,**不加 `Co-Authored-By`**、不加工具前缀。
- 未经确认不 `git push`/`git tag`;逐任务本地 commit。
- **零新增配置项**(仅新增 CLI flag `--signal-backtest-interval`);不写死密钥/路径/端口。
- **链路A 与日线链路B(interval='1d')行为字节级不变**;`interval` 默认 `1d` → 不传即现状。
- `interval` 词表统一复用 `src/core/intraday_backtest.validate_interval`(`{1d,1m,5m,15m,1h}`)。
- 优先复用、不造平行实现:分钟取数复用 `DataFetcherManager.get_intraday_data`;`_eval`/`aggregate_signal_stats` 评估与聚合逻辑**不改**(已 bar-interval 无关 + 已支持 interval)。
- `signal_stats` 已有 `interval` 列 + 唯一键 `(signal_type,market,interval,horizon)` → **零 schema 迁移**。
- 后端验证:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`;venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`(worktree 内 cwd 运行)。

---

### Task 1: `_to_epoch_ms_shanghai` 分钟分辨率感知(`src/services/volume_price_signals.py`)

把日线-only 的时间戳函数改为:纯日期→当日午夜(不变),带时分秒→保留。这是信号触发对齐(`marker.timestamp == _last_ts(window)`)在分钟 bar 上成立的前提。

**Files:**
- Modify: `src/services/volume_price_signals.py`(`_to_epoch_ms_shanghai`,约 line 120-132)
- Test: `tests/test_volume_price_signals.py`(追加;若不存在则创建)

**Interfaces:**
- Produces: `_to_epoch_ms_shanghai(date_value) -> int`——纯日期 → 当日午夜上海毫秒(行为不变);带时分秒(str 含 `:`、或非午夜 Timestamp/datetime)→ 保留时分秒。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_volume_price_signals.py`)

```python
import pandas as pd
from src.services.volume_price_signals import _to_epoch_ms_shanghai


def test_to_epoch_date_only_is_midnight_unchanged():
    a = _to_epoch_ms_shanghai("2026-06-22")
    assert _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22")) == a   # 午夜 Timestamp 与日期串一致
    assert _to_epoch_ms_shanghai("2026-06-22 00:00:00") == a        # 显式午夜 = 日期串


def test_to_epoch_minute_preserves_time():
    t0935 = _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22 09:35:00"))
    t0940 = _to_epoch_ms_shanghai(pd.Timestamp("2026-06-22 09:40:00"))
    midnight = _to_epoch_ms_shanghai("2026-06-22")
    assert t0935 != t0940 and t0935 != midnight        # 分钟 bar 不坍缩到午夜
    assert (t0940 - t0935) == 5 * 60 * 1000            # 5 分钟差
    assert _to_epoch_ms_shanghai("2026-06-22 09:35:00") == t0935   # 带时间字符串同样保留
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_volume_price_signals.py -k "to_epoch" -v`
Expected: FAIL — `test_to_epoch_minute_preserves_time`(当前 `[:10]`+置零午夜 → t0935==t0940==midnight)

- [ ] **Step 3: 实现**(替换 `_to_epoch_ms_shanghai`)

```python
def _to_epoch_ms_shanghai(date_value) -> int:
    """日期/时间 → Asia/Shanghai 毫秒时间戳。

    纯日期(无时间分量)→ 当日午夜(日线语义不变);带时分秒(分钟 bar)→ 保留时分秒。
    用作信号触发对齐的 join 键:marker.timestamp 与 _last_ts(window) 两侧同函数同列值,
    绝对时区不影响相等性(分钟 bar 不再坍缩到午夜)。
    """
    has_time = False
    if isinstance(date_value, str):
        s = date_value.strip()
        if len(s) > 10 and ":" in s:
            try:
                dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
                has_time = True
            except ValueError:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
    elif isinstance(date_value, pd.Timestamp):
        dt = date_value.to_pydatetime()
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    elif isinstance(date_value, datetime):
        dt = date_value
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    else:
        dt = pd.Timestamp(date_value).to_pydatetime()
        has_time = not (dt.hour == dt.minute == dt.second == dt.microsecond == 0)
    if dt.tzinfo is None:
        if has_time:
            dt = dt.replace(tzinfo=_SHANGHAI)
        else:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_SHANGHAI)
    return int(dt.timestamp() * 1000)
```

- [ ] **Step 4: 跑测试确认通过 + VPS 回归**

Run: `python -m pytest tests/test_volume_price_signals.py tests/test_signal_backtest.py -v`
Expected: PASS(日线 VPS/信号回测既有用例不变——日期串仍午夜)

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py
git commit -m "fix(signals): _to_epoch_ms_shanghai 分钟分辨率感知(日线午夜不变,分钟保留时分)"
```

---

### Task 2: `SignalBacktestService.run(interval=)` + 分钟取数 + CLI(`signal_backtest_service.py`、`main.py`)

`run` 增 `interval`,日线走原 `StockService.get_history_data`(不变),分钟走 `get_intraday_data` + `datetime→date`;`aggregate_signal_stats` 透传 interval;CLI 加 `--signal-backtest-interval`。

**Files:**
- Modify: `src/services/signal_backtest_service.py`(`run` + 新增 `_load_bars`)
- Modify: `main.py`(新增 CLI arg + 入口透传)
- Test: `tests/test_signal_backtest_service.py`(若不存在则创建)

**Interfaces:**
- Consumes: Task1 `_to_epoch_ms_shanghai`(分钟触发对齐);既有 `evaluate_signal_outcomes`/`evaluate_baseline_outcomes`/`aggregate_signal_stats(interval=)`;`DataFetcherManager.get_intraday_data(code, interval, days)`;`validate_interval`。
- Produces: `SignalBacktestService.run(*, codes=None, horizon=None, interval="1d") -> dict`;落 `signal_stats` 行 `interval=<interval>`。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_backtest_service.py`)

```python
import pandas as pd
import pytest
from types import SimpleNamespace
from src.services import signal_backtest_service as sbs
from src.services.signal_backtest import SignalOutcome, BASELINE_SIGNAL_TYPE


def _minute_df(n=120, base=100.0):
    return pd.DataFrame([
        {"datetime": pd.Timestamp("2026-06-22 09:30:00") + pd.Timedelta(minutes=5 * i),
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def test_run_interval_5m_uses_intraday_and_tags(monkeypatch):
    captured = {}
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    saved = []
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: (saved.extend(rows), len(rows))[1])

    monkeypatch.setattr(sbs, "_read_watchlist_codes", lambda s: ["BTC/USDT"])
    monkeypatch.setattr(sbs, "get_market_for_stock", lambda code: "crypto")

    from data_provider.base import DataFetcherManager
    def fake_intraday(self, code, interval, **kw):
        captured["interval"] = interval
        return _minute_df(), "BinanceFetcher"
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_intraday)
    # StockService 日线路径不得被调用
    monkeypatch.setattr(sbs.StockService, "get_history_data",
                        lambda *a, **k: pytest.fail("daily path must not run for 5m"))

    # 捕获传入 evaluate_* 的 df(验证 datetime→date 适配)与 aggregate 的 interval
    seen = {}
    monkeypatch.setattr(sbs, "evaluate_signal_outcomes",
                        lambda df, **k: (seen.update(cols=set(df.columns)),
                                         [SignalOutcome("vps_x", "crypto", "win")])[1])
    monkeypatch.setattr(sbs, "evaluate_baseline_outcomes",
                        lambda df, **k: [SignalOutcome(BASELINE_SIGNAL_TYPE, "crypto", "win"),
                                         SignalOutcome(BASELINE_SIGNAL_TYPE, "crypto", "loss")])

    out = svc.run(interval="5m")
    assert captured["interval"] == "5m"
    assert "date" in seen["cols"] and "datetime" not in seen["cols"]   # datetime→date 适配
    assert any(getattr(r, "interval", None) == "5m" for r in saved)    # 落库行 interval=5m
    assert out["processed"] == 1


def test_run_interval_1d_uses_daily_path(monkeypatch):
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: len(rows))
    monkeypatch.setattr(sbs, "_read_watchlist_codes", lambda s: ["600519"])
    monkeypatch.setattr(sbs, "get_market_for_stock", lambda code: "cn")
    daily = {"data": [{"date": f"2024-01-{d:02d}", "open": 10, "high": 11, "low": 9,
                       "close": 10, "volume": 100} for d in range(1, 28)] * 3}
    monkeypatch.setattr(sbs.StockService, "get_history_data", lambda self, **k: daily)
    from data_provider.base import DataFetcherManager
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data",
                        lambda *a, **k: pytest.fail("intraday path must not run for 1d"))
    out = svc.run(interval="1d")        # 默认日线路径,不触发 get_intraday_data
    assert out["processed"] >= 0


def test_run_rejects_bad_interval():
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: len(rows))
    with pytest.raises(ValueError):
        svc.run(interval="2h")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_backtest_service.py -v`
Expected: FAIL — `run()` 无 `interval` 参数 / 分钟路径未实现

- [ ] **Step 3: 实现**(`src/services/signal_backtest_service.py`)

文件顶部 import 增:
```python
from src.core.intraday_backtest import validate_interval, is_intraday_interval
```
`run` 签名与取数分叉:
```python
def run(self, *, codes=None, horizon=None, interval="1d") -> dict:
    validate_interval(interval)
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
            df = self._load_bars(svc, code, interval)
            if df is None or len(df) < _MIN_BARS:
                skipped += 1; continue
            cfg_m = VPSConfig.for_market(market)
            all_sig.extend(evaluate_signal_outcomes(df, market=market, horizon=hz, config=cfg_m))
            all_base.extend(evaluate_baseline_outcomes(df, market=market, horizon=hz, config=cfg_m))
            processed += 1
        except Exception as exc:
            errors += 1
            logger.warning("信号回测跳过 %s: %s", code, exc)
    stats = aggregate_signal_stats(all_sig, all_base, horizon=hz, interval=interval)
    orm_rows = [SignalStatRow(signal_type=s.signal_type, market=s.market, interval=s.interval,
                              horizon=s.horizon, win=s.win, loss=s.loss, sample=s.sample,
                              win_rate=s.win_rate, ci_low=s.ci_low, ci_high=s.ci_high,
                              baseline_win_rate=s.baseline_win_rate, excess=s.excess) for s in stats]
    written = self.repo.save_batch(orm_rows, replace_existing=True)
    return {"processed": processed, "codes": len(codes), "stats_written": written,
            "skipped": skipped, "errors": errors, "interval": interval}
```
新增 `_load_bars`:
```python
def _load_bars(self, svc, code, interval):
    """按 interval 取 bar:日线走 StockService(不变),分钟走链路A get_intraday_data 并 datetime→date。"""
    if not is_intraday_interval(interval):       # '1d'
        hist = svc.get_history_data(stock_code=code, period="daily", days=_FETCH_DAYS)
        rows = (hist or {}).get("data") or []
        return pd.DataFrame(rows) if rows else None
    from data_provider.base import DataFetcherManager
    df, _src = DataFetcherManager().get_intraday_data(
        code, interval, days=_minute_fetch_days(market_interval=interval))
    if df is None or df.empty:
        return None
    return df.rename(columns={"datetime": "date"})   # _eval/VPS 复用 'date' 列(保留分钟时间戳)
```
新增模块级 helper(取数近窗上界,源自身封顶):
```python
# 分钟回测近窗上界(各源自身会按可得范围封顶;crypto 深、美股 5m/15m≈60d/1h≈730d、A股 Tushare 深)
def _minute_fetch_days(*, market_interval: str) -> int:
    return 730 if market_interval == "1h" else 365
```
> 实现期核对:`get_intraday_data` 无 start_date 的"近 N 天"语义按各 fetcher 实际为准;`days` 取值偏大、由源封顶即可。`market` 在 except 外作用域:`_load_bars` 不需要 market(取数与 market 无关,路由在 get_intraday_data 内按 code 判定)。

`main.py` 增 CLI arg(`--signal-backtest` 定义附近):
```python
parser.add_argument(
    '--signal-backtest-interval', type=str, default='1d',
    choices=["1d", "1m", "5m", "15m", "1h"],
    help="信号回测 bar 粒度(默认 1d=日线;分钟在分钟 bar 上重算信号+评估)",
)
```
入口透传:
```python
stats = SignalBacktestService().run(
    interval=getattr(args, 'signal_backtest_interval', '1d'))
```

- [ ] **Step 4: 跑测试确认通过 + 日线回归**

Run: `python -m pytest tests/test_signal_backtest_service.py tests/test_signal_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest_service.py main.py tests/test_signal_backtest_service.py
git commit -m "feat(signals): 信号回测分钟化 run(interval=) + 分钟取数(复用 get_intraday_data)+ CLI"
```

---

### Task 3: 读路径 interval-aware(`signal_hit_rate.py`、`signal_board_service.py`、`api/v1/endpoints/signals.py`)

读出按 interval 取桶,默认 `1d` 不变,使分钟可信度可查。

**Files:**
- Modify: `src/services/signal_hit_rate.py`(`resolve_marker_hit_fields`)
- Modify: `src/services/signal_board_service.py`(`build_signals_for_code`、`build_board`)
- Modify: `api/v1/endpoints/signals.py`(board 端点加 `interval` query)
- Test: `tests/test_signal_hit_rate.py`(追加;若不存在则创建)

**Interfaces:**
- Consumes: 既有 `SignalStatsRepository.get(signal_type, market, *, interval="1d", horizon=...)`。
- Produces: `resolve_marker_hit_fields(signal_type, code, *, interval="1d") -> dict`;`build_signals_for_code(code, *, days=120, interval="1d")`;`build_board(codes, *, days=120, refresh=False, interval="1d")`;board 端点 `?interval=`。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_hit_rate.py`)

```python
from types import SimpleNamespace
from src.services import signal_hit_rate as shr


def test_resolve_marker_hit_fields_passes_interval(monkeypatch):
    seen = {}
    class _Repo:
        def get(self, signal_type, market, *, interval="1d", horizon=None):
            seen.update(interval=interval, horizon=horizon)
            return SimpleNamespace(sample=999, win_rate=0.6, ci_low=0.55,
                                   ci_high=0.7, baseline_win_rate=0.5, excess=0.05)
    monkeypatch.setattr(shr, "SignalStatsRepository", lambda *a, **k: _Repo())
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "crypto")

    shr.resolve_marker_hit_fields("vps_x", "BTC/USDT", interval="5m")
    assert seen["interval"] == "5m"
    shr.resolve_marker_hit_fields("vps_x", "BTC/USDT")     # 默认
    assert seen["interval"] == "1d"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_hit_rate.py -k interval -v`
Expected: FAIL — `resolve_marker_hit_fields` 无 `interval` 参数

- [ ] **Step 3: 实现**

`src/services/signal_hit_rate.py`——`resolve_marker_hit_fields` 加 `interval` 并透传:
```python
def resolve_marker_hit_fields(signal_type: str, code: str, *, interval: str = "1d") -> dict:
    ...
    stat = SignalStatsRepository().get(signal_type, market, interval=interval, horizon=horizon)
    ...
```
(函数体其余不变;`interval` 仅加在 `.get(...)` 调用。)

`src/services/signal_board_service.py`——`build_signals_for_code`/`build_board` 加 `interval` 并把 resolver 绑定 interval:
```python
def build_signals_for_code(code: str, *, days: int = 120, interval: str = "1d") -> BoardSignals:
    ...
    payload = build_signals_payload(
        ..., code=code,
        hit_fields_resolver=lambda st, c: resolve_marker_hit_fields(st, c, interval=interval),
    )
    ...

def build_board(codes: list, *, days: int = 120, refresh: bool = False, interval: str = "1d") -> dict:
    # 在逐 code 构建处把 interval 透传给 build_signals_for_code(...)
```
> 实现期核对 `build_board` 内部如何遍历 codes 调 `build_signals_for_code`,把 `interval=interval` 透传到位;缓存键若含参数,加入 interval 避免 1d/5m 串桶。

`api/v1/endpoints/signals.py`——board 端点加可选 query:
```python
def get_signals_board(
    days: int = Query(120, ge=1, le=365, ...),
    refresh: bool = Query(False, ...),
    interval: str = Query("1d", description="K线粒度(1d/1m/5m/15m/1h);分钟需先跑 --signal-backtest-interval"),
) -> SignalsBoardResponse:
    ...
    return SignalsBoardResponse(**build_board(codes, days=days, refresh=refresh, interval=interval))
```

- [ ] **Step 4: 跑测试确认通过 + 看板回归**

Run: `python -m pytest tests/test_signal_hit_rate.py tests/test_signal_board_service.py -v`
Expected: PASS(默认 1d 行为不变)

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_hit_rate.py src/services/signal_board_service.py api/v1/endpoints/signals.py tests/test_signal_hit_rate.py
git commit -m "feat(signals): 读路径 interval-aware(resolve/看板/API 加可选 interval,默认 1d 不变)"
```

---

### Task 4: 文档 + CHANGELOG + 联网观测 + 全量门禁

**Files:**
- Modify: `docs/signal-finer-fields.md` 或信号可信度相关专题(增分钟化小节);`docs/CHANGELOG.md`(`[Unreleased]` 扁平条目)
- Create: `tests/test_chainb_signal_intraday_network.py`(`-m network`,真拉一支 crypto 跑 run(5m))
- Test: 全量门禁

**Interfaces:** 无

- [ ] **Step 1: 文档**:在信号可信度专题(`docs/signal-finer-fields.md`,若无则 `docs/` 下相关文档)增"信号可信度分钟化"小节:语义(分钟信号+分钟评估)、用法(`python main.py --signal-backtest --signal-backtest-interval 5m`)、市场/历史(复用链路A 取数,各源近窗;crypto 深、美股 5m/15m≈60d/1h≈730d)、horizon bars 相对(5m×10=50min)、读出(`/signals/board?interval=5m`、`signal_stats` 按 `(type×market×interval×horizon)` 隔离)、限制(VPS 日线调参用于分钟 best-effort、批量限频)。

- [ ] **Step 2: CHANGELOG**(`[Unreleased]` 追加一行,扁平,无 `###`):
```markdown
- [新功能] 信号可信度回测分钟化:--signal-backtest-interval 在分钟 bar 上重算 VPS 信号+三重门,产出 (signal_type×market×interval×horizon) 隔离的 signal_stats(零迁移);_to_epoch_ms_shanghai 分钟分辨率感知(日线不变);读路径(/signals/board?interval=)与 resolve_marker_hit_fields 加可选 interval,默认 1d 与现状一致
```

- [ ] **Step 3: 联网观测测试**(`tests/test_chainb_signal_intraday_network.py`,`@pytest.mark.network`,连接异常带重试后 skip):
```python
import time
import pytest

pytestmark = pytest.mark.network
_HINTS = ("Connection", "Max retries", "timed out", "Temporary failure", "RemoteDisconnected", "451")


def test_chainb_run_5m_crypto_real():
    from src.services.signal_backtest_service import SignalBacktestService
    last = None
    for _ in range(4):
        try:
            out = SignalBacktestService().run(codes=["BTC/USDT"], interval="5m")
            assert out["interval"] == "5m" and out["processed"] >= 0
            return
        except Exception as e:
            last = e
            if any(k in str(e) for k in _HINTS):
                time.sleep(3); continue
            raise
    pytest.skip(f"crypto 分钟端点不可达,跳过观测: {last}")
```
> 注:该测试落库到默认 DB;如需隔离可在测试内设 `DATABASE_PATH` 临时库(参照既有 service 测试夹具)。

- [ ] **Step 4: 全量门禁**
```bash
cd /root/<worktree> && PATH=".../.venv/bin:$PATH" ./scripts/ci_gate.sh && PATH=".../.venv/bin:$PATH" python -m pytest -m "not network" -q
```
Expected: 全绿(含本计划新增用例;链路A/日线链路B/看板零回归)。

- [ ] **Step 5: Commit**
```bash
git add docs/ docs/CHANGELOG.md tests/test_chainb_signal_intraday_network.py
git commit -m "docs+test: 信号可信度分钟化专题/CHANGELOG + -m network 观测"
```

---

## Self-Review(已执行)

**1. Spec coverage:** spec §4.1 取数分叉→T2(`_load_bars`);§4.2 `_to_epoch` 修复→T1;§4.3 `_eval`/aggregate interval→T2(透传,评估逻辑不改);§4.4 CLI→T2;§4.5 落库→T2(零迁移,约束已记);§4.6 读路径→T3;§4.7 零配置→约束已记;§5 市场/语义→T2/T4 文档;§6 测试→各 T;§7 验证→T4 门禁。无遗漏。

**2. Placeholder scan:** 各步含真实代码/命令。"实现期核对 build_board 遍历/缓存键""get_intraday_data 无 start_date 语义以实际为准"为取证对齐提示,非占位 TODO。T4 文档文件名标注"若无则 docs/ 下相关文档"为实际定位提示。

**3. Type consistency:** `run(*, codes, horizon, interval="1d")`(T2)与 main.py 透传、network 测试调用一致;`_load_bars(svc, code, interval)`(T2)内部一致;`resolve_marker_hit_fields(signal_type, code, *, interval="1d")`(T3)与 board lambda 调用一致;`aggregate_signal_stats(..., interval=)`(既有)在 T2 调用一致;`_to_epoch_ms_shanghai`(T1)被 T2 分钟路径隐式依赖(触发对齐)。`signal_stats` 列名与既有 ORM 一致。

> 实现期若与实际细节漂移(get_intraday_data 无 start_date 语义、build_board 遍历结构与缓存键、VPSConfig 列契约、测试文件是否已存在),以实际代码为准并顺手订正本计划与 spec。
