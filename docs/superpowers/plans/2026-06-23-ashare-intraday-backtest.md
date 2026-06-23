# A股 盘中/分钟级回测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已落地的 crypto 分钟回测扩展到 A股(沪深为主,北交 best-effort),复用同一引擎/服务/API/Web/CLI,仅做市场化适配。

**Architecture:** `bars_per_day`/`derive_window_bar_count` 市场化(中心查表 `MARKET_TRADING_MINUTES`);新增 `is_a_share_code`/`market_of`;新增 `TushareFetcher.get_intraday_data`(HTTP `stk_mins`,主源)+ `AkshareFetcher.get_intraday_data`(`stock_zh_a_hist_min_em`,免费兜底),都声明 `intraday_data` capability;门面 `get_intraday_data` 放行 cn 并显式 Tushare 优先;service 分钟门控放行 a_share 并传 market。窗口复用 crypto"分钟流即交易日历":取 analysis_date 之后 session bar 前向切 `eval交易日 × bars_per_day(cn,interval)`。

**Tech Stack:** Python 3.10 / SQLAlchemy(SQLite)/ FastAPI / pytest;Tushare HTTP(`_TushareHttpClient`)/ akshare。

## Global Constraints

- 用中文交流;代码/注释/commit 按文件语境;commit message 英文类型前缀 + 中文体,**不加 `Co-Authored-By`**、不加工具前缀。
- 未经确认不 `git push`/`git tag`;本计划逐任务本地 commit。
- 不写死密钥/token;**本阶段不新增配置项**(复用 `TUSHARE_TOKEN` 与现有 intraday 配置)。
- 稳定性优先:`interval` 默认 `1d`、成本默认 0、调度默认 false → 不配置即等于现状;**crypto 与日线路径字节级不变**。
- 优先复用、不造平行实现:复用 `_convert_stock_code`/`is_bse_code`/`normalize_stock_code`/`_check_rate_limit`/`_filter_daily_fetchers_for_market`/`_filter_fetchers_by_capability`/`build_engine_version_tag`/`apply_round_trip_cost`;**提取共享 `normalize_intraday_df`**,不复制。
- interval 词表统一 `{1d,1m,5m,15m,1h}`;provider 内部映射(Tushare `1h→60min`;akshare period `1h→'60'`);API/CLI/Web allow-list **零改动**。
- `MARKET_TRADING_MINUTES = {"crypto":1440,"cn":240}`;`bars_per_day(interval, market="crypto")`;crypto 默认不变。
- A股 leverage 恒 1(engine_version tag 形如 `v1-5m`,无 `-xN`);`BacktestResult.eval_window_days` 存交易日数(非 window_bar_cnt)。
- 后端验证:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`;venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`(worktree 内 cwd 运行)。

---

### Task 1: helper 市场化(`src/core/intraday_backtest.py`)

`bars_per_day`/`derive_window_bar_count` 加 `market` 形参 + 中心查表。crypto 默认不变。

**Files:**
- Modify: `src/core/intraday_backtest.py`
- Test: `tests/test_intraday_backtest_helpers.py`(追加)

**Interfaces:**
- Produces:
  - `MARKET_TRADING_MINUTES: dict[str,int]` = `{"crypto":1440, "cn":240}`
  - `bars_per_day(interval: str, market: str = "crypto") -> int`
  - `derive_window_bar_count(eval_window_days: int, interval: str, market: str = "crypto") -> int`

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_intraday_backtest_helpers.py`)

```python
from src.core.intraday_backtest import MARKET_TRADING_MINUTES, bars_per_day, derive_window_bar_count


def test_market_trading_minutes_table():
    assert MARKET_TRADING_MINUTES["crypto"] == 1440
    assert MARKET_TRADING_MINUTES["cn"] == 240


def test_bars_per_day_crypto_default_unchanged():
    assert bars_per_day("5m") == 288          # 1440//5,默认 market='crypto' 不变
    assert bars_per_day("1m") == 1440
    assert bars_per_day("1h") == 24


def test_bars_per_day_cn_market():
    assert bars_per_day("1m", "cn") == 240    # 240//1
    assert bars_per_day("5m", "cn") == 48
    assert bars_per_day("15m", "cn") == 16
    assert bars_per_day("1h", "cn") == 4      # 240//60


def test_bars_per_day_unknown_market_raises():
    import pytest
    with pytest.raises((KeyError, ValueError)):
        bars_per_day("5m", "hk")              # hk 未登记


def test_derive_window_bar_count_market_aware():
    assert derive_window_bar_count(10, "5m") == 2880        # crypto 默认
    assert derive_window_bar_count(10, "5m", "cn") == 480   # 10*48
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -k "market or cn" -v`
Expected: FAIL — `bars_per_day() takes 1 positional argument` / `ImportError: MARKET_TRADING_MINUTES`

- [ ] **Step 3: 实现**(`src/core/intraday_backtest.py`,替换 `_MINUTES_PER_DAY` 用法)

```python
# 各市场每日交易分钟数(crypto 7×24;A股沪深两段 09:30-11:30 + 13:00-15:00 = 240)
MARKET_TRADING_MINUTES: dict[str, int] = {"crypto": 1440, "cn": 240}


def bars_per_day(interval: str, market: str = "crypto") -> int:
    """每交易日的 bar 根数。未知 interval 抛 ValueError,未知 market 抛 KeyError。"""
    minutes = INTRADAY_INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ValueError(f"不支持的分钟 interval: {interval!r}")
    return MARKET_TRADING_MINUTES[market] // minutes


def derive_window_bar_count(eval_window_days: int, interval: str, market: str = "crypto") -> int:
    """日历/交易日窗口(天)→ 分钟 bar 切片长度。"""
    return int(eval_window_days) * bars_per_day(interval, market)
```
(删除旧 `_MINUTES_PER_DAY = 1440` 常量;若仍被引用则保留为 `MARKET_TRADING_MINUTES["crypto"]`。)

- [ ] **Step 4: 跑测试确认通过 + crypto 回归**

Run: `python -m pytest tests/test_intraday_backtest_helpers.py -v`
Expected: PASS(含原有 crypto 用例不变)

- [ ] **Step 5: Commit**

```bash
git add src/core/intraday_backtest.py tests/test_intraday_backtest_helpers.py
git commit -m "feat(backtest): bars_per_day/window 市场化(MARKET_TRADING_MINUTES;cn=240)"
```

---

### Task 2: 市场检测 `is_a_share_code` + `market_of`(`data_provider/base.py`)

**Files:**
- Modify: `data_provider/base.py`(紧邻 `is_bse_code`/`is_crypto_code` 定义处)
- Test: `tests/test_market_detection_ashare.py`

**Interfaces:**
- Consumes: 既有 `normalize_stock_code`、`is_bse_code`、`is_crypto_code`、`is_perp_code`、`_is_hk_market`、`us_index_mapping.is_us_stock_code/is_us_index_code`
- Produces:
  - `is_a_share_code(code: str) -> bool`
  - `market_of(code: str) -> str`(返回 `"crypto"` | `"cn"`;其他抛 `ValueError`)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_market_detection_ashare.py
import pytest
from data_provider.base import is_a_share_code, market_of


@pytest.mark.parametrize("code", ["600519", "601318", "603288", "605499", "688981",
                                   "000001", "001979", "002415", "003816", "300750", "301029"])
def test_is_a_share_true_sh_sz(code):
    assert is_a_share_code(code) is True


def test_is_a_share_bse():
    assert is_a_share_code("830799") is True or is_a_share_code("920819") is True  # 北交(is_bse_code)


@pytest.mark.parametrize("code", ["BTC/USDT", "ETH/USDT:PERP", "00700", "HK00700", "AAPL"])
def test_is_a_share_false_others(code):
    assert is_a_share_code(code) is False


def test_market_of():
    assert market_of("600519") == "cn"
    assert market_of("BTC/USDT") == "crypto"
    assert market_of("ETH/USDT:PERP") == "crypto"
    with pytest.raises(ValueError):
        market_of("AAPL")
```
> 北交所样例以 `is_bse_code` 实际规则为准(8/4/92 前缀);若 `830799`/`920819` 不匹配真实规则,改用 grounding 中 `is_bse_code` 接受的样例。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_market_detection_ashare.py -v`
Expected: FAIL — `ImportError: is_a_share_code`

- [ ] **Step 3: 实现**(`data_provider/base.py`)

```python
_SH_A_PREFIXES = ("600", "601", "603", "605", "688")
_SZ_A_PREFIXES = ("000", "001", "002", "003", "300", "301")


def is_a_share_code(code: str) -> bool:
    """A股(沪深主板/科创/创业 + 北交所)。排除 HK 5 位、crypto、US。"""
    if is_crypto_code(code) or is_perp_code(code):
        return False
    if _is_hk_market(code):
        return False
    c = normalize_stock_code(code)
    if not (len(c) == 6 and c.isdigit()):
        return False
    if c.startswith(_SH_A_PREFIXES) or c.startswith(_SZ_A_PREFIXES):
        return True
    return is_bse_code(c)


def market_of(code: str) -> str:
    """分钟回测市场归类:crypto(含 perp)/ cn。其他抛 ValueError。"""
    if is_crypto_code(code) or is_perp_code(code):
        return "crypto"
    if is_a_share_code(code):
        return "cn"
    raise ValueError(f"无分钟市场归类: {code!r}")
```
> 实现时确认 `is_bse_code`/`normalize_stock_code`/`_is_hk_market` 的实际签名与导入位置(均在 `data_provider/base.py`)。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_market_detection_ashare.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py tests/test_market_detection_ashare.py
git commit -m "feat(data): is_a_share_code + market_of(沪深/北交 检测)"
```

---

### Task 3: 提取共享 `normalize_intraday_df`(`data_provider`)

把 `CryptoExchangeBase._normalize_intraday` 的标准化(已有 `datetime` 列后的数值化/去空/排序/pct_chg/选列)提取为共享函数,crypto 与 A股 fetcher 共用。**关键**:crypto 源是 ms 时间戳(`date` 列),A股源是 datetime 字符串 → 共享函数约定输入 df **已含 `datetime` 列**,由各 fetcher 先构建。

**Files:**
- Create: `data_provider/intraday_normalize.py`
- Modify: `data_provider/crypto_base.py`(`_normalize_intraday` 改为构建 datetime 后调用共享函数)
- Test: `tests/test_intraday_normalize.py`

**Interfaces:**
- Produces: `normalize_intraday_df(df: pd.DataFrame, code: str) -> pd.DataFrame`
  - 入参 df 必须含 `datetime` 列(任意可被 pandas 解析的时间)+ OHLCV 列(open/high/low/close/volume[/amount])
  - 出参列:`code, datetime, open, high, low, close, volume, amount, pct_chg`;数值化、dropna(close,volume)、按 datetime 升序、pct_chg、无技术指标

- [ ] **Step 1: 写失败测试**

```python
# tests/test_intraday_normalize.py
import pandas as pd
from data_provider.intraday_normalize import normalize_intraday_df


def test_normalize_from_string_datetime():
    raw = pd.DataFrame([
        {"datetime": "2024-01-15 09:35:00", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100"},
        {"datetime": "2024-01-15 09:30:00", "open": "10", "high": "10.5", "low": "9.5", "close": "10", "volume": "120"},
    ])
    out = normalize_intraday_df(raw, "600519")
    assert list(out["datetime"]) == sorted(out["datetime"])      # 升序
    assert out.iloc[0]["close"] == 10.0 and out.iloc[1]["close"] == 10.5
    assert {"code", "datetime", "open", "high", "low", "close", "volume", "amount", "pct_chg"} == set(out.columns)
    assert "ma20" not in out.columns
    assert (out["code"] == "600519").all()


def test_normalize_drops_null_close_volume():
    raw = pd.DataFrame([
        {"datetime": "2024-01-15 09:30:00", "open": 10, "high": 11, "low": 9, "close": None, "volume": 100},
        {"datetime": "2024-01-15 09:31:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
    ])
    out = normalize_intraday_df(raw, "600519")
    assert len(out) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_intraday_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: data_provider.intraday_normalize`

- [ ] **Step 3: 实现 + 改造 crypto_base**

`data_provider/intraday_normalize.py`:
```python
"""分钟数据共享标准化(crypto + A股 通用)。

约定输入 df 已含 `datetime` 列(各 fetcher 先从自身源构建:crypto 由 ms 时间戳、
A股由 datetime 字符串)。本函数只做数值化/去空/升序/pct_chg/选列,不算技术指标。
"""
from __future__ import annotations

import pandas as pd

_KEEP = ["code", "datetime", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def normalize_intraday_df(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if "datetime" not in df.columns:
        raise ValueError("normalize_intraday_df 需要 df 已含 datetime 列")
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = None
    df = df.dropna(subset=["close", "volume"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0.0)
    df["code"] = code
    return df[[c for c in _KEEP if c in df.columns]]
```

`data_provider/crypto_base.py` `_normalize_intraday` 改为(构建 datetime 后委托):
```python
def _normalize_intraday(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    from .intraday_normalize import normalize_intraday_df
    df = df.copy()
    if "date" not in df.columns:
        raise DataFetchError(f"{self.name} 分钟数据缺少 date 列")
    df["datetime"] = pd.to_datetime(df["date"], unit="ms")   # crypto: ms 时间戳
    return normalize_intraday_df(df, stock_code)
```

- [ ] **Step 4: 跑测试确认通过 + crypto 回归**

Run: `python -m pytest tests/test_intraday_normalize.py tests/test_get_intraday_data.py -v`
Expected: PASS(crypto get_intraday_data 行为不变)

- [ ] **Step 5: Commit**

```bash
git add data_provider/intraday_normalize.py data_provider/crypto_base.py tests/test_intraday_normalize.py
git commit -m "refactor(data): 提取共享 normalize_intraday_df(crypto 复用,A股将复用)"
```

---

### Task 4: `TushareFetcher.get_intraday_data`(HTTP `stk_mins`,主源)

**Files:**
- Modify: `data_provider/tushare_fetcher.py`
- Test: `tests/test_tushare_intraday.py`

**Interfaces:**
- Consumes: `normalize_intraday_df`(T3)、既有 `_convert_stock_code`/`_check_rate_limit`/`self._api`(`_TushareHttpClient`,`self._api.stk_mins(**params)` 经 `__getattr__`)
- Produces:
  - `TushareFetcher.get_intraday_data(stock_code, interval, start_date=None, end_date=None, days=30) -> pd.DataFrame`(标准 intraday df)
  - `TushareFetcher` 声明 capability `intraday_data`(对 cn 可用)
  - 内部 `_TS_FREQ = {"1m":"1min","5m":"5min","15m":"15min","1h":"60min"}`

- [ ] **Step 1: 写失败测试**(mock `_api.stk_mins`)

```python
# tests/test_tushare_intraday.py
import pandas as pd
import pytest
from data_provider.tushare_fetcher import TushareFetcher


def _fetcher_with_stub(monkeypatch, captured):
    f = TushareFetcher()
    class _Api:
        def stk_mins(self, **params):
            captured.update(params)
            return pd.DataFrame([
                {"ts_code": "600519.SH", "trade_time": "2024-01-15 09:31:00",
                 "open": "1700", "high": "1705", "low": "1699", "close": "1702", "vol": "10", "amount": "1"},
                {"ts_code": "600519.SH", "trade_time": "2024-01-15 09:30:00",
                 "open": "1700", "high": "1703", "low": "1698", "close": "1700", "vol": "12", "amount": "1"},
            ])
    f._api = _Api()
    monkeypatch.setattr(f, "_check_rate_limit", lambda: None)
    return f


def test_tushare_intraday_freq_mapping_and_normalize(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    df = f.get_intraday_data("600519", interval="5m", start_date="2024-01-15", end_date="2024-01-16", days=1)
    assert captured["freq"] == "5min"                  # 1h→60min 等映射
    assert captured["ts_code"] == "600519.SH"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert list(df["datetime"]) == sorted(df["datetime"])     # 升序
    assert df.iloc[0]["close"] == 1700.0
    assert "ma20" not in df.columns


def test_tushare_intraday_1h_maps_60min(monkeypatch):
    captured = {}
    f = _fetcher_with_stub(monkeypatch, captured)
    f.get_intraday_data("600519", interval="1h", days=1)
    assert captured["freq"] == "60min"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_tushare_intraday.py -v`
Expected: FAIL — `AttributeError: get_intraday_data`

- [ ] **Step 3: 实现**(`data_provider/tushare_fetcher.py`)

```python
_TS_FREQ = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "60min"}

def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
    if self._api is None:
        from .base import DataFetchError
        raise DataFetchError(f"[{self.name}] Tushare 未配置 token")
    freq = _TS_FREQ.get(interval)
    if freq is None:
        raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}")
    self._check_rate_limit()
    ts_code = self._convert_stock_code(stock_code)
    params = {"ts_code": ts_code, "freq": freq}
    if start_date:
        params["start_date"] = f"{start_date} 09:00:00" if len(str(start_date)) == 10 else str(start_date)
    if end_date:
        params["end_date"] = f"{end_date} 16:00:00" if len(str(end_date)) == 10 else str(end_date)
    raw = self._api.stk_mins(**params)
    if raw is None or raw.empty:
        from .base import DataFetchError
        raise DataFetchError(f"[{self.name}] {stock_code} 无分钟数据(freq={freq})")
    from .intraday_normalize import normalize_intraday_df
    raw = raw.rename(columns={"trade_time": "datetime", "vol": "volume"})
    return normalize_intraday_df(raw, stock_code)
```
- capability 声明:确认 `TushareFetcher` 如何声明能力(若有 `capabilities`/`is_available_for_request(capability)` 机制则加 `intraday_data`;若机制为方法存在性,`get_intraday_data` 存在即可,沿用 crypto 阶段的判定,实现时与 `_filter_fetchers_by_capability` 对齐)。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_tushare_intraday.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/tushare_fetcher.py tests/test_tushare_intraday.py
git commit -m "feat(data): TushareFetcher.get_intraday_data(stk_mins,A股分钟主源)"
```

---

### Task 5: `AkshareFetcher.get_intraday_data`(`stock_zh_a_hist_min_em`,免费兜底)

**Files:**
- Modify: `data_provider/akshare_fetcher.py`
- Test: `tests/test_akshare_intraday.py`

**Interfaces:**
- Consumes: `normalize_intraday_df`(T3)、`normalize_stock_code`
- Produces:
  - `AkshareFetcher.get_intraday_data(stock_code, interval, start_date=None, end_date=None, days=30) -> pd.DataFrame`
  - capability `intraday_data`
  - 内部 `_AK_PERIOD = {"1m":"1","5m":"5","15m":"15","1h":"60"}`

- [ ] **Step 1: 写失败测试**(mock `akshare.stock_zh_a_hist_min_em`)

```python
# tests/test_akshare_intraday.py
import sys, types
import pandas as pd
from data_provider.akshare_fetcher import AkshareFetcher


def test_akshare_intraday_period_mapping_and_normalize(monkeypatch):
    captured = {}
    fake_ak = types.SimpleNamespace()
    def _min(symbol, period, start_date, end_date, adjust):
        captured.update(symbol=symbol, period=period, adjust=adjust)
        return pd.DataFrame([
            {"时间": "2024-01-15 09:31:00", "开盘": 1700, "收盘": 1702, "最高": 1705, "最低": 1699, "成交量": 10, "成交额": 1},
            {"时间": "2024-01-15 09:30:00", "开盘": 1700, "收盘": 1700, "最高": 1703, "最低": 1698, "成交量": 12, "成交额": 1},
        ])
    fake_ak.stock_zh_a_hist_min_em = _min
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    f = AkshareFetcher()
    df = f.get_intraday_data("600519", interval="5m", start_date="2024-01-15", end_date="2024-01-16", days=1)
    assert captured["period"] == "5"            # 1h→'60'
    assert captured["symbol"] == "600519"
    assert {"datetime", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert df.iloc[0]["close"] == 1700.0 and list(df["datetime"]) == sorted(df["datetime"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_akshare_intraday.py -v`
Expected: FAIL — `AttributeError: get_intraday_data`

- [ ] **Step 3: 实现**(`data_provider/akshare_fetcher.py`;确认 akshare 的 import 方式与现有 daily 一致)

```python
_AK_PERIOD = {"1m": "1", "5m": "5", "15m": "15", "1h": "60"}

def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
    period = _AK_PERIOD.get(interval)
    if period is None:
        raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}")
    import akshare as ak
    from .base import normalize_stock_code, DataFetchError
    symbol = normalize_stock_code(stock_code)
    sd = f"{start_date} 09:00:00" if start_date else "1970-01-01 09:00:00"
    ed = f"{end_date} 16:00:00" if end_date else "2099-01-01 16:00:00"
    raw = ak.stock_zh_a_hist_min_em(symbol=symbol, period=period, start_date=sd, end_date=ed, adjust="qfq")
    if raw is None or raw.empty:
        raise DataFetchError(f"[{self.name}] {stock_code} 无分钟数据(period={period})")
    from .intraday_normalize import normalize_intraday_df
    raw = raw.rename(columns={"时间": "datetime", "开盘": "open", "收盘": "close",
                              "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount"})
    return normalize_intraday_df(raw, stock_code)
```
> 确认 `stock_zh_a_hist_min_em` 的 start_date/end_date 格式与列名(以实际 akshare 版本为准;若列名/参数不同,按实际调整 rename 与日期格式)。capability 声明同 T4 与 `_filter_fetchers_by_capability` 对齐。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_akshare_intraday.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/akshare_fetcher.py tests/test_akshare_intraday.py
git commit -m "feat(data): AkshareFetcher.get_intraday_data(免费 A股分钟兜底)"
```

---

### Task 6: 门面 `get_intraday_data` 放行 cn + Tushare 优先(`data_provider/base.py`)

**Files:**
- Modify: `data_provider/base.py`(`DataFetcherManager.get_intraday_data`)
- Test: `tests/test_manager_intraday_cn.py`

**Interfaces:**
- Consumes: T2 `is_a_share_code`、T4/T5 fetcher `get_intraday_data` + capability `intraday_data`
- Produces: 门面对 cn 码返回 `(df, source)`,Tushare 优先、akshare 兜底;非 crypto/cn 抛 `DataFetchError`

- [ ] **Step 1: 写失败测试**(用桩 fetcher 验证路由 + 优先级 + failover)

```python
# tests/test_manager_intraday_cn.py
import pandas as pd
import pytest
from data_provider.base import DataFetcherManager, DataFetchError


def _df():
    return pd.DataFrame([{"code": "600519", "datetime": pd.Timestamp("2024-01-16 09:30:00"),
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
                          "amount": 1.0, "pct_chg": 0.0}])


def test_cn_routes_tushare_first(monkeypatch):
    mgr = DataFetcherManager()
    calls = []
    class _TS:
        name = "TushareFetcher"
        def get_intraday_data(self, code, interval, **k): calls.append("ts"); return _df()
    class _AK:
        name = "AkshareFetcher"
        def get_intraday_data(self, code, interval, **k): calls.append("ak"); return _df()
    # 让门面过滤后的 cn intraday fetcher 仅这两者,顺序 Tushare 在前
    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_TS(), _AK()])
    df, src = mgr.get_intraday_data("600519", interval="5m", days=1)
    assert src == "TushareFetcher" and calls == ["ts"]    # Tushare 命中即停


def test_cn_failover_to_akshare(monkeypatch):
    mgr = DataFetcherManager()
    class _TS:
        name = "TushareFetcher"
        def get_intraday_data(self, code, interval, **k): raise DataFetchError("no points")
    class _AK:
        name = "AkshareFetcher"
        def get_intraday_data(self, code, interval, **k): return _df()
    monkeypatch.setattr(mgr, "_intraday_fetchers_for", lambda code: [_TS(), _AK()])
    df, src = mgr.get_intraday_data("600519", interval="5m", days=1)
    assert src == "AkshareFetcher"


def test_non_crypto_non_cn_raises():
    with pytest.raises(Exception):
        DataFetcherManager().get_intraday_data("AAPL", interval="5m", days=1)
```
> 测试假设引入一个小 helper `_intraday_fetchers_for(code)` 返回排序后的 intraday fetcher 列表(便于测试与显式排序)。若实现不抽该 helper,则改为 monkeypatch 现有 fetcher 快照 + 排序点。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_manager_intraday_cn.py -v`
Expected: FAIL(cn 被拒 / 顺序不符)

- [ ] **Step 3: 实现**(`DataFetcherManager.get_intraday_data`)

- 门控:`if not (is_crypto_code(c) or is_perp_code(c) or is_a_share_code(c)): raise DataFetchError(...)`。
- market:`"crypto_perp" if perp else "crypto" if crypto else "cn"`。
- 抽 `_intraday_fetchers_for(code)`:`_filter_daily_fetchers_for_market(snapshot, market)` → `_filter_fetchers_by_capability(.., "intraday_data")` → **cn 时把 Tushare 排到 akshare 之前**(显式 sort key:Tushare 优先;无 token 的 Tushare 已被 `_is_fetcher_available`/`is_available()` 过滤掉)。
- 遍历该列表逐个 `get_intraday_data`,首个非空返回 `(df, name)`;全失败抛 `DataFetchError(errors)`。
- 复用现有 `crypto_intraday_minute_cache_ttl_s` 缓存逻辑(键 `(code,interval,days)`),保持 crypto 行为不变。

- [ ] **Step 4: 跑测试确认通过 + crypto 门面回归**

Run: `python -m pytest tests/test_manager_intraday_cn.py tests/test_get_intraday_data.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py tests/test_manager_intraday_cn.py
git commit -m "feat(data): 门面 get_intraday_data 放行 A股(cn 路由,Tushare 优先 akshare 兜底)"
```

---

### Task 7: service 分钟门控放行 A股 + 市场化窗口(`src/services/backtest_service.py`)

**Files:**
- Modify: `src/services/backtest_service.py`(分钟分支)
- Test: `tests/test_backtest_service_ashare_intraday.py`

**Interfaces:**
- Consumes: T1 `derive_window_bar_count(.., market)`、T2 `is_a_share_code`/`market_of`、T6 门面
- Produces: A股分钟回测可跑;落库 engine_version=`v1-{interval}`、bar_interval=interval、first_hit_bar_index、eval_window_days=交易日数

- [ ] **Step 1: 写失败测试**(mock 门面返回足量分钟 df + mock evaluate_single,验证放行 + 市场化 window + 落库)

```python
# tests/test_backtest_service_ashare_intraday.py
# 构造一个 a_share 候选(code='600519'),monkeypatch DataFetcherManager.get_intraday_data 返回
# >= window_bar_cnt 根分钟 bar(5m,cn → 10 交易日 ×48 = 480),monkeypatch BacktestEngine.evaluate_single
# 返回 completed + first_hit_trading_days=N,捕获 save_results_batch 的 BacktestResult,断言:
#   - 该 a_share 候选被处理(非 skipped_unsupported)
#   - 传给 evaluate_single 的 config.eval_window_days == 480(= derive_window_bar_count(10,'5m','cn'))
#   - 落库 r.eval_window_days==10(交易日数)、r.bar_interval=='5m'、r.engine_version=='v1-5m'、r.first_hit_bar_index==N
# 另一用例:interval='1d' 默认 → 不触发分钟取数(get_intraday_data 不被调用),日线分支不变。
```
> 复用 `tests/test_backtest_service_intraday.py` 既有的桩与夹具结构(crypto 版),改 code 为 A股、market='cn'、window_bar_cnt=480。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_backtest_service_ashare_intraday.py -v`
Expected: FAIL(A股被 skipped_unsupported / window_bar_cnt 用 crypto 1440)

- [ ] **Step 3: 实现**(`run_backtest` 分钟分支)

- 门控:`if intraday and not (is_crypto_code|is_perp_code|is_a_share_code): skipped_unsupported += 1; continue`(加 `is_a_share_code`)。
- `market = market_of(analysis.code)`;`window_bar_cnt = derive_window_bar_count(int(eval_window_days), interval, market)`。
- 其余不变(start_price=日线收盘;fetch start=analysis_date+1、end 宽余;`EvaluationConfig(eval_window_days=window_bar_cnt)`;落库 eval_window_days=交易日数、bar_interval、first_hit_bar_index;tag=`build_engine_version_tag(base, interval, 1)`)。
- 确认 `end_date` 宽余:cn 用 `analysis_date + max(eval_window_days*2, eval_window_days+10)` 日历日,确保覆盖 N 交易日(节假日缓冲)。

- [ ] **Step 4: 跑测试确认通过 + crypto/日线回归**

Run: `python -m pytest tests/test_backtest_service_ashare_intraday.py tests/test_backtest_service_intraday.py tests/test_backtest_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_ashare_intraday.py
git commit -m "feat(backtest): run_backtest 放行 A股分钟 + 市场化 window_bar_cnt"
```

---

### Task 8: 文档 + CHANGELOG + 全量门禁 + 联网观测

**Files:**
- Modify: `docs/intraday-backtest.md`(增 A股小节)、`docs/CHANGELOG.md`(`[Unreleased]` 扁平条目)
- Create: `tests/test_ashare_intraday_network.py`(`-m network`,akshare 免费真拉,Tushare 仅有 token 才真拉否则 skip)
- Test: 全量门禁

**Interfaces:** 无

- [ ] **Step 1: 文档**:`docs/intraday-backtest.md` 增 "A股(沪深为主,北交 best-effort)" 小节:数据源(Tushare 主/akshare 兜底、token 与积分、无 token 自动 akshare)、bars_per_day(cn=240)、interval 映射、窗口语义(交易日)、用法(CLI `--backtest-interval 5m` 对 A股码;API/Web 同 crypto)、限制(积分门槛/akshare 限频/印花税未建模/北交不确定)。

- [ ] **Step 2: CHANGELOG**(`[Unreleased]` 追加一行,扁平,无 `###`):
```markdown
- [新功能] A股分钟级回测:在 crypto 之后扩展沪深(北交 best-effort)分钟前向回测,Tushare stk_mins 主源 + akshare 免费兜底;bars_per_day 市场化(cn=240);interval/默认/成本/调度沿用,默认 1d 与现状一致
```

- [ ] **Step 3: 联网观测测试**(`tests/test_ashare_intraday_network.py`,`@pytest.mark.network`,失败/不可达 skip):
```python
import pytest
pytestmark = pytest.mark.network
# 用 AkshareFetcher().get_intraday_data("600519", "5m", start_date=<近10交易日>, days=2) 真拉,
# 断言非空、datetime 升序无重复、5m 间距为主;连接异常→pytest.skip。
# Tushare:仅当 os.getenv("TUSHARE_TOKEN") 存在时真拉一条,否则 skip。
```

- [ ] **Step 4: 全量门禁**
```bash
cd /root/<worktree> && PATH=".../.venv/bin:$PATH" ./scripts/ci_gate.sh && PATH=".../.venv/bin:$PATH" python -m pytest -m "not network" -q
```
Expected: 全绿(含本计划新增用例;crypto/日线零回归)。

- [ ] **Step 5: Commit**
```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md tests/test_ashare_intraday_network.py
git commit -m "docs+test: A股分钟回测专题/CHANGELOG + -m network 观测"
```

---

## Self-Review(已执行)

**1. Spec coverage:** spec §4.1→T1;§4.2→T2;§4.3 共享 normalize→T3、Tushare→T4、akshare→T5、门面路由→T6;§4.4 service→T7;§4.5 零配置(无任务,约束已记);§4.6 测试→各 T 单测 + T8 网络/门禁;§4.7 降级→T4/T5/T6/T7 错误分支;§5→T8 门禁 + 文档。无遗漏。

**2. Placeholder scan:** T7 Step1 用注释描述断言点(沿用 crypto 版桩结构),非占位 TODO——给了明确断言对象(window_bar_cnt=480、eval_window_days=10、tag=v1-5m)。其余步骤均含真实代码/命令。北交样例码、akshare 列名/日期格式标注"以实际为准"是取证对齐提示,非占位。

**3. Type consistency:** `bars_per_day(interval, market='crypto')`/`derive_window_bar_count(.., market='crypto')`(T1)在 T7 调用一致;`is_a_share_code`/`market_of`(T2)在 T6/T7 一致;`normalize_intraday_df(df, code)`(T3)在 T4/T5 调用一致;`get_intraday_data(stock_code, interval, start_date, end_date, days)`(T4/T5)与门面/ service 调用一致;`_TS_FREQ`/`_AK_PERIOD` 仅各自任务内用。

> 实现期若与实际代码细节漂移(`stk_mins`/akshare 列名与参数、capability 声明机制、cn fetcher 快照排序点、is_bse_code 样例),以实际代码为准并顺手订正本计划与 spec。
