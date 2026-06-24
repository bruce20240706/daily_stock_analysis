# 美股盘中/分钟级回测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已落地的分钟回测扩展到美股个股,复用同一引擎/服务/API/Web/CLI,仅做市场化适配。

**Architecture:** `bars_per_day` 由 floor 改 ceil(计入会话末半根,us 1h=7)并加 `us=390`;`market_of` 加 `us` 分支(复用 `is_us_stock_code`);新增 `YfinanceFetcher.get_intraday_data`(yfinance 免 key,`.`→`-` 类股映射、MultiIndex 拍平、tz 去除、`1m` fail-closed、`@retry`);门面放行 us(仅 yfinance);service 放行 us + 市场化窗口 + `end_date` 缓冲推广(cn/us 同走)。

**Tech Stack:** Python 3.10 / SQLAlchemy(SQLite)/ pytest;yfinance(免 key)。

## Global Constraints

- 用中文交流;代码/注释/commit 按文件语境;commit message 英文类型前缀 + 中文体,**不加 `Co-Authored-By`**、不加工具前缀。
- 未经确认不 `git push`/`git tag`;本计划逐任务本地 commit。
- **本阶段不新增配置项**(yfinance 免 key);不写死密钥/路径/端口。
- 稳定性优先:`interval` 默认 `1d`、成本默认 0、调度默认 false → 不配置即等于现状;**crypto / A股 / 日线路径字节级不变**。
- `interval` 词表统一 `{1d,1m,5m,15m,1h}`;**API/CLI/Web allow-list 零改动**。
- 优先复用、不造平行实现:复用 `_convert_stock_code`/`is_us_stock_code`/`normalize_intraday_df`/`_filter_daily_fetchers_for_market`/`_filter_fetchers_by_capability`/日线入口的 `@retry`。
- `bars_per_day` 改 ceil 仅影响有余数的 (市场,粒度)——当前唯一是 us 1h(6→7);crypto(1440)/cn(240)所有粒度整除,ceil==floor 值不变。
- 美股 leverage 恒 1(engine_version 形如 `v1-5m`,无 `-xN`);`BacktestResult.eval_window_days` 存交易日数(非 window_bar_cnt)。
- 美股 `1m` **fail-closed**(不在 `_YF_INTERVAL`→`NotImplementedError`);`1m` 仍在全局词表供 crypto/A股。
- 后端验证:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`;venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`。

---

### Task 1: `bars_per_day` 改 ceil + `us=390`(`src/core/intraday_backtest.py`)

**Files:**
- Modify: `src/core/intraday_backtest.py`(`MARKET_TRADING_MINUTES`、`bars_per_day`)
- Test: `tests/test_intraday_backtest_helpers.py`(追加)

**Interfaces:**
- Consumes: 既有 `INTRADAY_INTERVAL_MINUTES`
- Produces:
  - `MARKET_TRADING_MINUTES` 增 `"us": 390`
  - `bars_per_day(interval, market="crypto") -> int`(ceil 语义)
  - `derive_window_bar_count(eval_window_days, interval, market="crypto")` 行为随之变(对 us 1h)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_intraday_backtest_helpers.py`)

```python
from src.core.intraday_backtest import MARKET_TRADING_MINUTES, bars_per_day, derive_window_bar_count


def test_market_trading_minutes_us():
    assert MARKET_TRADING_MINUTES["us"] == 390


def test_bars_per_day_us_market():
    assert bars_per_day("5m", "us") == 78     # 390/5
    assert bars_per_day("15m", "us") == 26    # 390/15
    assert bars_per_day("1h", "us") == 7      # ceil(390/60)=7(末根半根)
    assert bars_per_day("1m", "us") == 390


def test_bars_per_day_ceil_keeps_crypto_cn_unchanged():
    # 整除场景 ceil==floor,既有市场值不变
    assert bars_per_day("5m") == 288 and bars_per_day("1m") == 1440 and bars_per_day("1h") == 24
    assert bars_per_day("5m", "cn") == 48 and bars_per_day("1h", "cn") == 4


def test_derive_window_bar_count_us():
    assert derive_window_bar_count(10, "5m", "us") == 780
    assert derive_window_bar_count(10, "1h", "us") == 70
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -k "us or ceil" -v`
Expected: FAIL — `KeyError: 'us'` / `bars_per_day("1h","us")` 返回 6 而非 7

- [ ] **Step 3: 实现**(`src/core/intraday_backtest.py`)

把 `MARKET_TRADING_MINUTES` 改为含 us:
```python
MARKET_TRADING_MINUTES: dict[str, int] = {"crypto": 1440, "cn": 240, "us": 390}
```
把 `bars_per_day` 的 floor 改 ceil(末根半根计入,匹配数据源实际 bar 数):
```python
def bars_per_day(interval: str, market: str = "crypto") -> int:
    """每交易日的 bar 根数(ceil,计入会话末尾半根)。未知 interval 抛 ValueError,未知 market 抛 KeyError。"""
    minutes = INTRADAY_INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError(f"不支持的分钟 interval: {interval!r}")
    return -(-MARKET_TRADING_MINUTES[market] // minutes)
```

- [ ] **Step 4: 跑测试确认通过 + crypto/cn 回归**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -v`
Expected: PASS(含原有 crypto/cn 用例不变)

- [ ] **Step 5: Commit**

```bash
git add src/core/intraday_backtest.py tests/test_intraday_backtest_helpers.py
git commit -m "feat(backtest): bars_per_day 改 ceil + us=390(美股 1h=7;crypto/cn 不变)"
```

---

### Task 2: `market_of` 加 `us` 分支(`data_provider/base.py`)

**Files:**
- Modify: `data_provider/base.py`(顶部 import 增 `is_us_stock_code`;`market_of` 加 us 分支)
- Test: `tests/test_market_detection_us.py`

**Interfaces:**
- Consumes: 既有 `is_crypto_code`/`is_perp_code`/`is_a_share_code`;`us_index_mapping.is_us_stock_code`
- Produces: `market_of(code)` 现可返回 `"us"`(AAPL 等);指数/HK/其它仍抛 `ValueError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_market_detection_us.py
import pytest
from data_provider.base import market_of


@pytest.mark.parametrize("code", ["AAPL", "TSLA", "MSFT", "NVDA", "BRK.B"])
def test_market_of_us(code):
    assert market_of(code) == "us"


def test_market_of_us_index_raises():
    # 美股指数无 operation_advice、非回测候选;is_us_stock_code 排除指数 → market_of 抛 ValueError
    with pytest.raises(ValueError):
        market_of("SPX")


def test_market_of_order_no_collision():
    assert market_of("600519") == "cn"
    assert market_of("BTC/USDT") == "crypto"
    with pytest.raises(ValueError):
        market_of("HK00700")   # 港股不归类
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_market_detection_us.py -v`
Expected: FAIL — `market_of("AAPL")` 抛 ValueError(尚无 us 分支)

- [ ] **Step 3: 实现**(`data_provider/base.py`)

在 base.py 顶部既有 import 区追加模块级导入(us_index_mapping 仅依赖 `re`,无循环导入):
```python
from .us_index_mapping import is_us_stock_code
```
在 `market_of` 的 `is_a_share_code` 分支后、`raise` 前加 us 分支:
```python
    if is_us_stock_code(code):
        return "us"
```

- [ ] **Step 4: 跑测试确认通过 + 无循环导入**

Run: `python -m pytest tests/test_market_detection_us.py tests/test_market_detection_ashare.py -v`
Expected: PASS(A股 归类不受影响;`import data_provider.base` 正常)

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py tests/test_market_detection_us.py
git commit -m "feat(data): market_of 加 us 分支(is_us_stock_code 复用)"
```

---

### Task 3: `YfinanceFetcher.get_intraday_data`(`data_provider/yfinance_fetcher.py`)

**Files:**
- Modify: `data_provider/yfinance_fetcher.py`
- Test: `tests/test_yfinance_intraday.py`

**Interfaces:**
- Consumes: `normalize_intraday_df`(共享)、既有 `_convert_stock_code`/`is_us_stock_code`/日线入口的 `@retry` 装饰器栈(`tenacity`)
- Produces:
  - `YfinanceFetcher.get_intraday_data(stock_code, interval, start_date=None, end_date=None, days=30) -> pd.DataFrame`(标准 intraday df,datetime tz-naive)
  - 内部 `_YF_INTERVAL = {"5m":"5m","15m":"15m","1h":"60m"}`(**无 1m**)
  - capability `intraday_data`(覆写 get_intraday_data 即被门面 override 过滤保留)

- [ ] **Step 1: 写失败测试**(mock `yfinance.download`,sys.modules 注入)

```python
# tests/test_yfinance_intraday.py
import sys
import types

import pandas as pd
import pytest

from data_provider.yfinance_fetcher import YfinanceFetcher
from data_provider.base import DataFetchError


def _install_fake_yf(monkeypatch, captured, rows="default", tz="America/New_York"):
    fake_yf = types.SimpleNamespace()

    def _download(tickers, start=None, end=None, interval=None, auto_adjust=True, progress=False):
        captured.update(tickers=tickers, start=start, end=end, interval=interval)
        if rows == "default":
            idx = pd.to_datetime(["2026-06-22 09:30:00", "2026-06-22 09:35:00"]).tz_localize(tz)
            df = pd.DataFrame(
                {"Open": [100, 101], "High": [102, 103], "Low": [99, 100],
                 "Close": [101, 102], "Volume": [1000, 1100]},
                index=idx,
            )
            df.index.name = "Datetime"
            df.columns = pd.MultiIndex.from_product([df.columns, [tickers]])  # 新版 yfinance MultiIndex
            return df
        return pd.DataFrame(rows)

    fake_yf.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    return fake_yf


def test_yfinance_intraday_5m_normalize_and_tz_naive(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    f = YfinanceFetcher()
    df = f.get_intraday_data("AAPL", interval="5m", start_date="2026-06-22", end_date="2026-06-25", days=3)
    assert captured["interval"] == "5m" and captured["tickers"] == "AAPL"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert list(df["datetime"]) == sorted(df["datetime"])
    assert getattr(pd.to_datetime(df["datetime"]).dt, "tz", None) is None   # tz-naive(美东墙钟)
    assert "ma20" not in df.columns
    assert df.iloc[0]["close"] == 101.0


def test_yfinance_intraday_1h_maps_60m(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    YfinanceFetcher().get_intraday_data("AAPL", interval="1h", days=3)
    assert captured["interval"] == "60m"


def test_yfinance_intraday_class_share_dot_to_hyphen(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    YfinanceFetcher().get_intraday_data("BRK.B", interval="5m", days=3)
    assert captured["tickers"] == "BRK-B"   # Yahoo 用连字符


def test_yfinance_intraday_1m_fail_closed(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured)
    with pytest.raises(NotImplementedError):
        YfinanceFetcher().get_intraday_data("AAPL", interval="1m", days=3)
    assert captured == {}   # 未触达 download(映射阶段 fail-closed)


def test_yfinance_intraday_empty_raises(monkeypatch):
    captured = {}
    _install_fake_yf(monkeypatch, captured, rows=[])
    with pytest.raises(DataFetchError):
        YfinanceFetcher().get_intraday_data("AAPL", interval="5m", days=3)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_yfinance_intraday.py -v`
Expected: FAIL — `AttributeError`/默认 `NotImplementedError`(尚无覆写)

- [ ] **Step 3: 实现**(`data_provider/yfinance_fetcher.py`)

在模块级(类外)加映射常量:
```python
# 1m 不纳入:yfinance 1m 历史仅 7 天且单请求 ≤8 天,与回测 min_age+窗口缓冲恒冲突(spec §4.3.1)
_YF_INTERVAL = {"5m": "5m", "15m": "15m", "1h": "60m"}
```
在 `YfinanceFetcher` 类内新增方法(复用日线入口同款 `@retry`;`pd`/`logger`/`DataFetchError`/`is_us_stock_code`/retry 装饰器均已在文件顶部导入):
```python
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
        """美股分钟级 K 线(免 key)。返回纯 OHLCV+datetime(tz-naive 美东墙钟,不算指标)。

        1m fail-closed(spec §4.3.1);interval→yfinance 映射;类股 '.'→'-';MultiIndex 拍平;去时区。
        """
        yf_interval = _YF_INTERVAL.get(interval)
        if yf_interval is None:
            raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}(美股 1m 不支持)")
        import yfinance as yf
        yf_code = self._convert_stock_code(stock_code)
        if is_us_stock_code(stock_code):
            yf_code = yf_code.replace(".", "-")   # BRK.B → BRK-B(Yahoo 用连字符)
        df = yf.download(tickers=yf_code, start=start_date, end=end_date,
                         interval=yf_interval, auto_adjust=True, progress=False)
        if df is None or df.empty:
            raise DataFetchError(f"[{self.name}] {stock_code} 无分钟数据(interval={interval})")
        # 显式标准化(不走日线 _normalize_data——后者产 'date' 列且注入 amount 估算)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)   # ('Close','AAPL') → 'Close'
        df = df.reset_index()
        df = df.rename(columns={"Datetime": "datetime", "Date": "datetime",
                                "Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume"})
        # 去时区:yfinance 分钟为 tz-aware(美东)→ 保留墙钟去 tz,与 crypto/A股 naive 对齐
        df["datetime"] = pd.to_datetime(df["datetime"])
        if getattr(df["datetime"].dt, "tz", None) is not None:
            df["datetime"] = df["datetime"].dt.tz_localize(None)
        from .intraday_normalize import normalize_intraday_df
        return normalize_intraday_df(df, stock_code)   # amount 缺省 → 填 None
```
> 实现期核对:文件顶部已有 `from tenacity import ...`(日线入口 `_fetch_raw_data` 用同款)、`import pandas as pd`、`from .base import DataFetchError`、`from .us_index_mapping import is_us_stock_code`(line 114 已用)。若 retry 名称导入路径不同,以文件实际为准。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_yfinance_intraday.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/yfinance_fetcher.py tests/test_yfinance_intraday.py
git commit -m "feat(data): YfinanceFetcher.get_intraday_data(美股分钟,免 key;1m fail-closed/类股映射/去时区)"
```

---

### Task 4: 门面 `get_intraday_data` 放行美股(`data_provider/base.py`)

**Files:**
- Modify: `data_provider/base.py`(`_intraday_fetchers_for` 加 us 分支;facade `get_intraday_data` gate 加 `is_us_stock_code`)
- Test: `tests/test_manager_intraday_us.py`

**Interfaces:**
- Consumes: Task2 `is_us_stock_code`(已模块导入)、Task3 `YfinanceFetcher.get_intraday_data`
- Produces: 门面对 us 码返回 `(df, "YfinanceFetcher")`;非 crypto/cn/us 抛 `DataFetchError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_manager_intraday_us.py
import pandas as pd
import pytest

from data_provider.base import DataFetcherManager, DataFetchError


def _clear_cache():
    import data_provider.base as base_mod
    if hasattr(base_mod, "_INTRADAY_CACHE"):
        base_mod._INTRADAY_CACHE.clear()


def _df():
    return pd.DataFrame([{
        "code": "AAPL", "datetime": pd.Timestamp("2026-06-22 09:30:00"),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
        "amount": None, "pct_chg": 0.0,
    }])


def test_us_intraday_fetchers_only_yfinance():
    # 真实 _intraday_fetchers_for:us 仅含覆写 get_intraday_data 的 yfinance
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("AAPL")]
    assert names == ["YfinanceFetcher"]


def test_us_facade_routes_to_yfinance(monkeypatch):
    _clear_cache()
    mgr = DataFetcherManager()

    class _YF:
        name = "YfinanceFetcher"
        def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
            return _df()

    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_YF()])
    df, src = mgr.get_intraday_data("AAPL", interval="5m", days=3)
    assert src == "YfinanceFetcher"


def test_unsupported_market_raises():
    with pytest.raises(DataFetchError):
        DataFetcherManager().get_intraday_data("HK00700", interval="5m", days=3)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_manager_intraday_us.py -v`
Expected: FAIL(us 被拒 / `_intraday_fetchers_for("AAPL")` 返回 [])

- [ ] **Step 3: 实现**(`data_provider/base.py`)

在 `_intraday_fetchers_for` 的 `elif is_a_share_code(code): market = "cn"` 之后、`else: return []` 之前加:
```python
        elif is_us_stock_code(code):
            market = "us"
```
在 facade `get_intraday_data` 的 gate 增 `is_us_stock_code`:
```python
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)
                or is_a_share_code(stock_code) or is_us_stock_code(stock_code)):
            raise DataFetchError(f"{stock_code} 暂不支持分钟级数据(仅 crypto / A股 / 美股)")
```
(us 单源,`_intraday_fetchers_for` 的 cn-only 排序分支不触及 us;override 过滤天然只留 yfinance。)

- [ ] **Step 4: 跑测试确认通过 + crypto/cn 门面回归**

Run: `python -m pytest tests/test_manager_intraday_us.py tests/test_manager_intraday_cn.py tests/test_get_intraday_data.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py tests/test_manager_intraday_us.py
git commit -m "feat(data): 门面 get_intraday_data 放行美股(us→yfinance 单源)"
```

---

### Task 5: service 放行美股 + `end_date` 缓冲推广(`src/services/backtest_service.py`)

**Files:**
- Modify: `src/services/backtest_service.py`(分钟分支 import/gate/end_date)
- Test: `tests/test_backtest_service_us_intraday.py`

**Interfaces:**
- Consumes: Task1 `derive_window_bar_count(.., "us")`、Task2 `market_of`/`is_us_stock_code`、Task4 门面
- Produces: 美股分钟回测可跑;落库 `engine_version=v1-5m`、`bar_interval`、`first_hit_bar_index`、`eval_window_days`=交易日数

- [ ] **Step 1: 写失败测试**

```python
# tests/test_backtest_service_us_intraday.py
import json
import os
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from src.services.backtest_service import BacktestService


def _minute_df(n, base=100.0):
    return pd.DataFrame([
        {"datetime": f"2026-06-22 {9 + i // 60:02d}:{i % 60:02d}:00",
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def _fake_analysis(code="AAPL", aid=1):
    return SimpleNamespace(id=aid, code=code, operation_advice="买入",
                           stop_loss=90.0, take_profit=120.0,
                           context_snapshot=json.dumps({"enhanced_context": {"date": "2026-06-01"}}))


def _build(monkeypatch, tmp_path, captured):
    os.environ["DATABASE_PATH"] = str(tmp_path / "us_intraday.db")
    from src.config import Config
    from src.storage import DatabaseManager
    Config._instance = None
    DatabaseManager.reset_instance()
    svc = BacktestService(db_manager=DatabaseManager.get_instance())

    analysis_date = date(2026, 6, 1)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [_fake_analysis()])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily",
                        lambda code, analysis_date: SimpleNamespace(date=analysis_date, close=100.0))

    from data_provider.base import DataFetcherManager
    def fake_intraday(self, code, interval, **kw):
        captured.update(kw)
        return _minute_df(780).copy(), "YfinanceFetcher"
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_intraday)

    captured_eval: Dict[str, Any] = {}
    def fake_eval(**kwargs):
        captured_eval.update(kwargs)
        return {"eval_status": "completed", "analysis_date": analysis_date,
                "eval_window_days": kwargs["config"].eval_window_days,
                "engine_version": kwargs["config"].engine_version,
                "operation_advice": "买入", "position_recommendation": "long",
                "start_price": kwargs["start_price"], "end_close": 110.0, "max_high": 115.0,
                "min_low": 95.0, "stock_return_pct": 10.0, "direction_expected": "up",
                "direction_correct": True, "outcome": "win", "stop_loss": 90.0, "take_profit": 120.0,
                "hit_stop_loss": False, "hit_take_profit": False, "first_hit": "neither",
                "first_hit_date": None, "first_hit_trading_days": 42,
                "simulated_entry_price": kwargs["start_price"], "simulated_exit_price": 110.0,
                "simulated_exit_reason": "window_end", "simulated_return_pct": 10.0}
    from src.core import backtest_engine as beng
    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_eval))

    saved: List[Any] = []
    monkeypatch.setattr(svc.repo, "save_results_batch",
                        lambda results, **kw: (saved.extend(results), len(results))[1])
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)
    return svc, saved, captured_eval, analysis_date


def test_us_intraday_processed_with_us_window(monkeypatch, tmp_path):
    captured: Dict[str, Any] = {}
    svc, saved, captured_eval, analysis_date = _build(monkeypatch, tmp_path, captured)
    out = svc.run_backtest(interval="5m", eval_window_days=10)

    assert out["processed"] == 1 and out["completed"] == 1, out
    assert out.get("skipped_unsupported", 0) == 0
    assert captured_eval["config"].eval_window_days == 780     # 10 * bars_per_day(5m,us)=78
    r = saved[0]
    assert r.eval_window_days == 10 and r.bar_interval == "5m"
    assert r.engine_version == "v1-5m" and r.first_hit_bar_index == 42
    assert r.first_hit_trading_days is None
    # end_date 缓冲(us 走 cn 同款):start=analysis_date+1,end 宽出 >= 24 天
    start, end = captured["start_date"], captured["end_date"]
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    assert start == analysis_date + timedelta(days=1)
    assert (end - start).days >= 24
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_backtest_service_us_intraday.py -v`
Expected: FAIL(美股被 skipped_unsupported / market_of 抛错 / window 不符)

- [ ] **Step 3: 实现**(`src/services/backtest_service.py`)

import 行(分钟分支前的 `from data_provider.base import is_perp_code, is_crypto_code, is_a_share_code, market_of`)增 `is_us_stock_code`:
```python
        from data_provider.base import is_perp_code, is_crypto_code, is_a_share_code, is_us_stock_code, market_of
```
gate 增 us:
```python
            if intraday and not (
                is_crypto_code(analysis.code)
                or is_perp_code(analysis.code)
                or is_a_share_code(analysis.code)
                or is_us_stock_code(analysis.code)
            ):
                skipped_unsupported += 1
                continue
```
`end_date` 缓冲推广(把 `if market == "cn"` 改为「crypto 用 N,其余(cn/us)用缓冲」):
```python
                    if market == "crypto":
                        _end_offset = int(eval_window_days)
                    else:
                        _end_offset = max(int(eval_window_days) * 2, int(eval_window_days) * 3 // 2 + 14)
                    _window_end_date = _minute_window_start + timedelta(days=_end_offset)
```

- [ ] **Step 4: 跑测试确认通过 + crypto/A股/日线回归**

Run: `python -m pytest tests/test_backtest_service_us_intraday.py tests/test_backtest_service_ashare_intraday.py tests/test_backtest_service_intraday.py tests/test_backtest_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_us_intraday.py
git commit -m "feat(backtest): run_backtest 放行美股分钟 + end_date 缓冲推广 cn/us"
```

---

### Task 6: 文档 + CHANGELOG + 联网观测

**Files:**
- Modify: `docs/intraday-backtest.md`(增美股小节)、`docs/CHANGELOG.md`(`[Unreleased]` 扁平条目)
- Create: `tests/test_us_intraday_network.py`(`-m network`,yfinance 免 key 真拉,带重试)
- Test: 全量门禁

**Interfaces:** 无

- [ ] **Step 1: 文档**:`docs/intraday-backtest.md` 增 "## 11. 美股盘中/分钟级回测" 小节:数据源(yfinance 免 key)、bars_per_day(us=390;1h=7 含末根半根)、可用 band(5/15m≈60d、1h≈730d 推荐、1m 不支持)、超窗优雅降级、类股 `.`→`-`、批量限频 caveat、复权基准漂移 caveat;并把 §9.2/§3 的"美股不支持"更新为"美股已支持(见 §11)"。原 §11 回滚说明顺延为 §12(若存在编号冲突按实际顺延)。

- [ ] **Step 2: CHANGELOG**(`[Unreleased]` 追加一行,扁平,无 `###`):
```markdown
- [新功能] 美股分钟级回测:扩展美股个股分钟前向回测,yfinance 免 key 单源;bars_per_day 市场化(us=390,1h 计末根半根=7);美股 1m 因 yfinance 历史/请求上限 fail-closed;interval/默认/成本/调度沿用,默认 1d 与现状一致
```

- [ ] **Step 3: 联网观测测试**(`tests/test_us_intraday_network.py`,`@pytest.mark.network`,连接异常带重试后 skip):
```python
"""美股分钟取数真实联网观测(-m network,非阻断)。yfinance 免 key 真拉近段 5m。"""
import time
from datetime import datetime, timedelta

import pandas as pd
import pytest

pytestmark = pytest.mark.network

_CONN_HINTS = ("Connection", "Max retries", "timed out", "Temporary failure",
               "name resolution", "RemoteDisconnected", "rate", "Too Many")


def _fetch_or_skip(fn, retries=6, delay=2.0):
    last = None
    for _ in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            if "无分钟数据" in str(e):
                pytest.skip(f"yfinance 无分钟数据,跳过: {e}")
            if any(k in str(e) for k in _CONN_HINTS):
                time.sleep(delay)
                continue
            raise
    pytest.skip(f"yfinance 端点不可达/限频,重试 {retries} 次仍失败: {last}")


def test_yfinance_intraday_5m_real():
    from data_provider.yfinance_fetcher import YfinanceFetcher
    start = (datetime.now() - timedelta(days=5)).date().isoformat()
    f = YfinanceFetcher()
    df = _fetch_or_skip(lambda: f.get_intraday_data("AAPL", interval="5m", start_date=start, days=3))
    assert not df.empty
    assert {"open", "high", "low", "close", "volume", "datetime"} <= set(df.columns)
    assert "ma20" not in df.columns
    dts = pd.to_datetime(df["datetime"]).sort_values().reset_index(drop=True)
    assert dts.is_unique and dts.is_monotonic_increasing
    assert getattr(dts.dt, "tz", None) is None       # tz-naive
    diffs = dts.diff().dropna().dt.total_seconds()
    assert (diffs == 300).mean() > 0.5               # 多数相邻 5m
```

- [ ] **Step 4: 全量门禁**
```bash
cd /root/<worktree> && PATH=".../.venv/bin:$PATH" ./scripts/ci_gate.sh && PATH=".../.venv/bin:$PATH" python -m pytest -m "not network" -q
```
Expected: 全绿(含本计划新增用例;crypto/A股/日线零回归)。

- [ ] **Step 5: Commit**
```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md tests/test_us_intraday_network.py
git commit -m "docs+test: 美股分钟回测专题/CHANGELOG + -m network 观测"
```

---

## Self-Review(已执行)

**1. Spec coverage:** spec §4.1→T1;§4.2→T2;§4.3/§4.3.1→T3(含 1m fail-closed/类股映射/去时区/retry);§4.3 门面 us 路由→T4;§4.4→T5(gate+window+end_date);§4.5 零配置(无任务,约束已记);§4.6→T6(网络观测)+各 T 单测;§4.7 测试→各 T;§5→T6 文档。无遗漏。

**2. Placeholder scan:** 各步均含真实代码/命令。T3 顶部 import 核对、T6 文档编号顺延标注"以实际为准"为取证对齐提示,非占位 TODO。

**3. Type consistency:** `bars_per_day(interval, market)`/`derive_window_bar_count(.., market)`(T1)在 T5 一致;`market_of`/`is_us_stock_code`(T2)在 T4/T5 一致;`get_intraday_data(stock_code, interval, start_date, end_date, days)`(T3)与门面/service 调用一致;`_YF_INTERVAL` 仅 T3 内用;window_bar_cnt(5m,us,N=10)=780 在 T5 测试与引擎切片一致。

> 实现期若与实际代码细节漂移(yfinance 返回结构、retry 导入名、文档章节编号),以实际代码为准并顺手订正本计划与 spec。
