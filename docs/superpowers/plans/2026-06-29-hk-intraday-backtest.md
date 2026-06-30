# 港股(HK)分钟级回测(MVP) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把盘中/分钟级回测扩展到港股个股(链路 A + 链路 B),双源 akshare 东财(主)+ yfinance(兜底),仅市场化适配,crypto/cn/us/日线零回归。

**Architecture:** 新增 "hk" 市场到分钟回测栈,镜像 cn/us 接入:`MARKET_TRADING_MINUTES["hk"]=330` + `market_of` 加 hk + 门面 guard/路由(白名单收窄,排除 Tushare)+ `AkshareFetcher` HK 分钟分支(yfinance 零改动靠解开过滤)+ 两 service 放行/band。MVP 成本走现有 fee/slip(默认 0),HK 双边印花税另立 follow-up。

**Tech Stack:** Python(pytest);数据源 akshare `stock_hk_hist_min_em`(东财)+ yfinance(`0700.HK`)。无前端代码改动。

## Global Constraints

- **数据源双源**:akshare 东财主(`stock_hk_hist_min_em`,5m/15m/1h kline)+ yfinance 兜底(`0700.HK`);**HK 1m fail-closed**(两源均无法锚定 1m 历史)。
- **路由白名单**:hk 分钟仅 `[AkshareFetcher, YfinanceFetcher]`,**必须排除 TushareFetcher**(其 `stk_mins` 仅 A股,日线表含 hk,不收窄会漏入)。
- **门控基准统一**:市场判定用 `_is_hk_market`(base.py 内,私有)/ `is_hk_stock_code`(公开,`data_provider` 暴露,**不在 `data_provider.base`** → 服务层须 `from data_provider import is_hk_stock_code`)。
- **HK 每日交易分钟=330**(09:30–12:00=150 + 13:00–16:00=180);`bars_per_day` 1m=330/5m=66/15m=22/**1h=ceil(330/60)=6**。
- **默认 1d 即现状**;只新增 hk 分支/键,不动 crypto/cn/us 既有值;白名单只对 market=="hk" 生效。
- **MVP 不加 HK 成本分支**(market!="cn" 自然不计 A股印花税;走 fee/slip 默认 0;cash 门控自动适用)。
- **commit message**:英文类型前缀 + 中文体,不加 `Co-Authored-By`,不加工具/agent 前缀。
- **CHANGELOG `[Unreleased]` 扁平**(`- [类型] 描述`,无 `### 标题`)。

**关联 spec:** `docs/superpowers/specs/2026-06-29-hk-intraday-backtest-design.md`(经对抗式审查 4 视角核验,4-change 0 Blocker;§6 列 6 处必改测试)。

---

## 文件结构(改动面)

| 文件 | 改动 | Task |
|---|---|---|
| `src/core/intraday_backtest.py` | `MARKET_TRADING_MINUTES["hk"]=330` + ceil 注释 | 1 |
| `data_provider/base.py` | `market_of` hk(C2.1);门面 guard(C2.2);`_intraday_fetchers_for` 路由+白名单+yfinance 过滤(C2.3) | 1(market_of)/2(guard+路由) |
| `data_provider/akshare_fetcher.py` | `get_intraday_data` HK 分支 | 2 |
| `src/services/backtest_service.py` | gate 放行 hk + import | 3 |
| `src/services/signal_backtest_service.py` | `_INTRADAY_MAX_DAYS["hk"]` + docstring | 4 |
| `tests/test_intraday_backtest_helpers.py` | hk 正向 + 负向改 jp | 1 |
| `tests/test_market_detection_us.py` / `test_market_detection_ashare.py` | market_of HK raise→hk | 1 |
| `tests/test_akshare_hk_intraday.py`(新) / 路由测试 | akshare HK 分支 + 路由白名单 | 2 |
| `tests/test_backtest_service_hk_intraday.py`(新) / `test_backtest_service_intraday.py` | hk 集成 + 2 处 skip 测试换码 510050 | 3 |
| `tests/test_signal_backtest_service.py` | hk band 断言 365→60 | 4 |
| `docs/intraday-backtest.md` / `signal-credibility.md` / `CHANGELOG.md` | 港股章 + 逐行订正 | 5 |

**任务顺序:** 1(骨架+归类)→ 2(数据源+路由)→ 3(链路 A)→ 4(链路 B)→ 5(docs)。Task 1 与 2 均改 `base.py` 但不同函数(market_of vs guard/路由),顺序提交无冲突。

---

### Task 1: 市场骨架 + market_of 归类

**Files:**
- Modify: `src/core/intraday_backtest.py:16-18`(MARKET_TRADING_MINUTES)、`:37` 附近(bars_per_day ceil 注释)
- Modify: `data_provider/base.py:313-321`(market_of)
- Test: `tests/test_intraday_backtest_helpers.py`、`tests/test_market_detection_us.py:26-27`、`tests/test_market_detection_ashare.py:55-59`

**Interfaces:**
- Produces: `bars_per_day(interval,"hk")`(1m=330/5m=66/15m=22/1h=6)、`market_of(HK)=="hk"`——Task 3 的 `window_bar_cnt`/gate 消费。

- [ ] **Step 1: 调整 Task 1 全部测试**

(1) `tests/test_intraday_backtest_helpers.py` 末尾追加 hk 正向:
```python
def test_market_trading_minutes_hk():
    assert MARKET_TRADING_MINUTES["hk"] == 330


def test_bars_per_day_hk_market():
    assert bars_per_day("1m", "hk") == 330
    assert bars_per_day("5m", "hk") == 66      # (150+180)/5
    assert bars_per_day("15m", "hk") == 22
    assert bars_per_day("1h", "hk") == 6       # ceil(330/60)=6(早市末半根)


def test_derive_window_bar_count_hk():
    assert derive_window_bar_count(10, "5m", "hk") == 660   # 10*66
```

(2) `tests/test_intraday_backtest_helpers.py` 既有 `test_bars_per_day_unknown_market_raises`(`:78-80`)把 `"hk"` 改为真未登记市场 `"jp"`:
```python
def test_bars_per_day_unknown_market_raises():
    with pytest.raises((KeyError, ValueError)):
        bars_per_day("5m", "jp")              # jp 未登记
```

(3) `tests/test_market_detection_us.py:26-27` 把 raise 改为正向:
```python
    assert market_of("HK00700") == "hk"   # 港股已纳入分钟回测
```
(即删除 `with pytest.raises(ValueError):` 与缩进的 `market_of("HK00700")`,替换为上面一行。)

(4) `tests/test_market_detection_ashare.py:55-59` 把 unsupported-raises 改为 hk 正向:
```python
# AAPL 已归类 us;港股(00700/HK00700)已纳入分钟回测,归类 hk
@pytest.mark.parametrize("code", ["00700", "HK00700"])
def test_market_of_hk(code):
    assert market_of(code) == "hk"
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py tests/test_market_detection_us.py tests/test_market_detection_ashare.py -q`
Expected: 新 hk 正向用例与 market_of hk 用例 FAIL(`KeyError: 'hk'` / `market_of(...)` raises ValueError);`bars_per_day(... "jp")` 用例 PASS。

- [ ] **Step 3: 改实现 — MARKET_TRADING_MINUTES(`src/core/intraday_backtest.py:16-18`)**

把:
```python
# 各市场每日交易分钟数(crypto 7×24;A股沪深两段 09:30-11:30 + 13:00-15:00 = 240;
# 美股常规时段 09:30-16:00 ET = 390,排除盘前盘后)
MARKET_TRADING_MINUTES: dict[str, int] = {"crypto": 1440, "cn": 240, "us": 390}
```
改为:
```python
# 各市场每日交易分钟数(crypto 7×24;A股沪深两段 09:30-11:30 + 13:00-15:00 = 240;
# 美股常规时段 09:30-16:00 ET = 390,排除盘前盘后;港股 09:30-12:00 + 13:00-16:00 = 330,排除午休/竞价)
MARKET_TRADING_MINUTES: dict[str, int] = {"crypto": 1440, "cn": 240, "us": 390, "hk": 330}
```

- [ ] **Step 4: 改实现 — bars_per_day ceil 注释(`src/core/intraday_backtest.py:37` 附近)**

把:
```python
    未知 interval 抛 ValueError,未知 market 抛 KeyError。ceil 仅影响有余数的 (市场,粒度)——
    当前唯一是 us 1h(ceil(390/60)=7);crypto/cn 各粒度均整除,ceil==floor 值不变。
```
改为:
```python
    未知 interval 抛 ValueError,未知 market 抛 KeyError。ceil 仅影响有余数的 (市场,粒度)——
    有余数档:us 1h(ceil(390/60)=7)、hk 1h(ceil(330/60)=6);crypto/cn 及其余档均整除,ceil==floor 值不变。
```

- [ ] **Step 5: 改实现 — market_of(`data_provider/base.py:313-321`)**

把:
```python
def market_of(code: str) -> str:
    """分钟回测市场归类:crypto(含 perp)/ cn / us。其他(港股、美股指数等)抛 ValueError。"""
    if is_crypto_code(code) or is_perp_code(code):
        return "crypto"
    if is_a_share_code(code):
        return "cn"
    if is_us_stock_code(code):
        return "us"
    raise ValueError(f"无分钟市场归类: {code!r}")
```
改为:
```python
def market_of(code: str) -> str:
    """分钟回测市场归类:crypto(含 perp)/ cn / us / hk。其他(美股指数等)抛 ValueError。"""
    if is_crypto_code(code) or is_perp_code(code):
        return "crypto"
    if is_a_share_code(code):
        return "cn"
    if is_us_stock_code(code):
        return "us"
    if _is_hk_market(code):
        return "hk"
    raise ValueError(f"无分钟市场归类: {code!r}")
```

- [ ] **Step 6: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py tests/test_market_detection_us.py tests/test_market_detection_ashare.py -q`
Expected: 全 PASS(crypto/cn/us 既有断言不回归)。

- [ ] **Step 7: 提交**

```bash
git add src/core/intraday_backtest.py data_provider/base.py tests/test_intraday_backtest_helpers.py tests/test_market_detection_us.py tests/test_market_detection_ashare.py
git commit -m "feat: 港股分钟回测市场骨架(MARKET_TRADING_MINUTES hk=330 + market_of 归类 hk)"
```

---

### Task 2: 数据源接入(akshare HK 分支 + 门面 guard/路由白名单)

**Files:**
- Modify: `data_provider/akshare_fetcher.py:415-450`(get_intraday_data 加 HK 分支)
- Modify: `data_provider/base.py:1524-1549`(_intraday_fetchers_for)、`:1576-1578`(门面 guard)
- Test: `tests/test_akshare_hk_intraday.py`(新);路由断言(放 `tests/test_akshare_hk_intraday.py` 内或相邻)

**Interfaces:**
- Consumes: `is_hk_stock_code`(akshare_fetcher 同模块,`:196`)、`_is_hk_market`(base.py)、`_AK_PERIOD`(无 1m)。
- Produces: `DataFetcherManager().get_intraday_data(HK,"5m",...)` 端到端可取(经 akshare 主源);`_intraday_fetchers_for(HK)` → `[AkshareFetcher, YfinanceFetcher]`。

- [ ] **Step 1: 写失败测试**(新建 `tests/test_akshare_hk_intraday.py`)

```python
# -*- coding: utf-8 -*-
"""港股分钟取数:AkshareFetcher HK 分支(stock_hk_hist_min_em)+ 门面路由白名单。全程离线 mock。"""
import pandas as pd
import pytest

from data_provider.akshare_fetcher import AkshareFetcher


def _fake_hk_min_df(n=66):
    # stock_hk_hist_min_em kline 路径返回 11 列(含额外 振幅/涨跌幅/涨跌额/换手率)
    return pd.DataFrame([
        {
            "时间": f"2026-05-06 {9 + i // 60:02d}:{i % 60:02d}:00",
            "开盘": 100.0, "收盘": 100.0, "最高": 101.0, "最低": 99.0,
            "成交量": 1.0, "成交额": 100.0,
            "振幅": 0.0, "涨跌幅": 0.0, "涨跌额": 0.0, "换手率": 0.0,
        }
        for i in range(n)
    ])


def test_akshare_hk_intraday_5m(monkeypatch):
    captured = {}

    def fake_min(symbol, period, start_date, end_date, adjust):
        captured.update(symbol=symbol, period=period, adjust=adjust)
        return _fake_hk_min_df()

    import akshare as ak
    monkeypatch.setattr(ak, "stock_hk_hist_min_em", fake_min, raising=True)

    df = AkshareFetcher().get_intraday_data("HK00700", "5m", start_date="2026-05-01", end_date="2026-05-10")
    assert captured["symbol"] == "00700"          # 归一去 hk 前缀 + zfill(5)
    assert captured["period"] == "5"              # _AK_PERIOD 映射
    assert captured["adjust"] == "qfq"
    assert {"datetime", "open", "close", "high", "low", "volume"}.issubset(df.columns)
    assert len(df) == 66                          # 额外列被 normalize 丢弃,行数不变


def test_akshare_hk_intraday_symbol_variants(monkeypatch):
    seen = []
    import akshare as ak
    monkeypatch.setattr(ak, "stock_hk_hist_min_em",
                        lambda symbol, period, start_date, end_date, adjust: (seen.append(symbol), _fake_hk_min_df())[1],
                        raising=True)
    for code in ("HK00700", "hk00700", "00700"):
        AkshareFetcher().get_intraday_data(code, "5m")
    assert seen == ["00700", "00700", "00700"]    # 三形归一一致


def test_akshare_hk_intraday_1m_fail_closed():
    with pytest.raises(NotImplementedError):
        AkshareFetcher().get_intraday_data("HK00700", "1m")


def test_intraday_fetchers_for_hk_whitelist():
    from data_provider.base import DataFetcherManager
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("HK00700")]
    assert "AkshareFetcher" in names
    assert "YfinanceFetcher" in names
    assert "TushareFetcher" not in names          # A股专用 stk_mins,白名单排除
    assert names.index("AkshareFetcher") < names.index("YfinanceFetcher")  # akshare 主、yfinance 兜底


def test_intraday_fetchers_for_cn_still_excludes_yfinance():
    # market not in (us,hk) 改动不回归 cn:cn 仍排除 yfinance
    from data_provider.base import DataFetcherManager
    names = [f.name for f in DataFetcherManager()._intraday_fetchers_for("600519")]
    assert "YfinanceFetcher" not in names
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_akshare_hk_intraday.py -q`
Expected: FAIL —— akshare HK 分支未实现(`DataFetchError`/调到 A股接口)、`_intraday_fetchers_for("HK00700")` 返回空列表。

- [ ] **Step 3: 改实现 — akshare HK 分支(`data_provider/akshare_fetcher.py`,在 `import akshare as ak` 之后、A股 `symbol = normalize_stock_code(...)` 之前插入)**

把:
```python
        import akshare as ak

        symbol = normalize_stock_code(stock_code)
```
改为:
```python
        import akshare as ak

        if is_hk_stock_code(stock_code):
            if interval == "1m":   # 独立守卫:HK 1m fail-closed,不依赖 _AK_PERIOD 共享表(防 A股 1m 将来入表静默激活)
                raise NotImplementedError(f"[{self.name}] 港股 1m 不支持(东财 trends2 仅 ndays=5,无法锚定历史窗口)")
            hk_symbol = stock_code.lower().replace("hk", "").zfill(5)   # 同日线 _fetch_hk_data:876
            hk_sd = f"{start_date} 09:00:00" if start_date else "1970-01-01 09:00:00"
            hk_ed = f"{end_date} 16:00:00" if end_date else "2099-01-01 16:00:00"
            raw_hk = ak.stock_hk_hist_min_em(
                symbol=hk_symbol, period=period, start_date=hk_sd, end_date=hk_ed, adjust="qfq"
            )
            if raw_hk is None or raw_hk.empty:
                raise DataFetchError(f"[{self.name}] {stock_code} 无港股分钟数据（period={period}）")
            from .intraday_normalize import normalize_intraday_df
            raw_hk = raw_hk.rename(columns={
                "时间": "datetime", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
            })
            return normalize_intraday_df(raw_hk, stock_code)

        symbol = normalize_stock_code(stock_code)
```
(`period = _AK_PERIOD.get(interval)` 的 None 检查保持在最上方不变 → 1m 在 A股/HK 均先抛 NotImplementedError;HK 分支内独立守卫为前向防御。)

- [ ] **Step 4: 改实现 — 门面 guard(`data_provider/base.py:1576-1578`)**

把:
```python
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)
                or is_a_share_code(stock_code) or is_us_stock_code(stock_code)):
            raise DataFetchError(f"{stock_code} 暂不支持分钟级数据（仅 crypto / A股 / 美股）")
```
改为:
```python
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)
                or is_a_share_code(stock_code) or is_us_stock_code(stock_code)
                or _is_hk_market(stock_code)):
            raise DataFetchError(f"{stock_code} 暂不支持分钟级数据（仅 crypto / A股 / 港股 / 美股）")
```

- [ ] **Step 5: 改实现 — _intraday_fetchers_for 路由+白名单(`data_provider/base.py:1524-1549`)**

把:
```python
        elif is_us_stock_code(code):
            market = "us"
        else:
            return []
```
改为:
```python
        elif is_us_stock_code(code):
            market = "us"
        elif _is_hk_market(code):
            market = "hk"
        else:
            return []
```
并把:
```python
        # yfinance 日线支持 cn/hk/us,但其分钟数据仅服务美股(A股分钟走 Tushare/akshare);
        # 非 us 市场排除 yfinance,避免其漏入 A股分钟路径改变既有契约。
        if market != "us":
            fetchers = [f for f in fetchers if f.name != "YfinanceFetcher"]
        if market == "cn":
            _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
            fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
        return fetchers
```
改为:
```python
        # yfinance 日线支持 cn/hk/us;其分钟数据服务美股与港股(A股分钟走 Tushare/akshare);
        # 非 (us, hk) 市场排除 yfinance,避免其漏入 A股分钟路径改变既有契约。
        if market not in ("us", "hk"):
            fetchers = [f for f in fetchers if f.name != "YfinanceFetcher"]
        if market == "hk":
            # HK 分钟仅 akshare(东财) + yfinance;Tushare stk_mins 仅 A股(日线表含 hk 会漏入)
            _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
            fetchers = [f for f in fetchers if f.name in _hk_order]
            fetchers.sort(key=lambda f: _hk_order[f.name])
        if market == "cn":
            _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
            fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
        return fetchers
```

- [ ] **Step 6: 运行验证通过(含既有 akshare/路由测试不回归)**

Run: `.venv/bin/python -m pytest tests/test_akshare_hk_intraday.py -q && .venv/bin/python -m pytest tests/ -q -k "intraday_fetchers or akshare" -p no:cacheprovider`
Expected: 新文件全 PASS;既有 akshare/路由相关测试不回归。

- [ ] **Step 7: 提交**

```bash
git add data_provider/akshare_fetcher.py data_provider/base.py tests/test_akshare_hk_intraday.py
git commit -m "feat: 港股分钟数据源接入(akshare stock_hk_hist_min_em + 门面 guard/路由白名单)"
```

---

### Task 3: 链路 A 服务放行 + 集成测

**Files:**
- Modify: `src/services/backtest_service.py:108`(import)、`:115-122`(gate)
- Test: `tests/test_backtest_service_hk_intraday.py`(新);`tests/test_backtest_service_intraday.py:234-269` 与 `:318-349`(换码 510050)

**Interfaces:** Consumes Task 1(market_of hk / bars_per_day hk)+ Task 2(fetcher 路由,集成测中 mock)。

- [ ] **Step 1: 写/改测试**

(1) 既有两 skip 测试换不支持码 `510050`(改后仍 skipped_unsupported):
- `tests/test_backtest_service_intraday.py:253`:`code="HK00700"` → `code="510050"`;docstring `:235-237` 把「港股」改「非支持标的(如 ETF 510050)」。
- `tests/test_backtest_service_intraday.py:337`:`code="HK00700"` → `code="510050"`;docstring `:319-321` 同改;`:348` 文案「美股 code」改「非支持 code」。

(2) 新建 `tests/test_backtest_service_hk_intraday.py`(镜像 ashare 集成测):
```python
# -*- coding: utf-8 -*-
"""港股分钟回测 service 集成:放行 HK + 市场化窗口(330/日)。全程离线 mock。"""
import json
import os
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from src.services.backtest_service import BacktestService


def _minute_df(n: int, base: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame([
        {"datetime": f"2026-05-06 {9 + i // 60:02d}:{i % 60:02d}:00",
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def _completed_eval(**kwargs):
    return {
        "eval_status": "completed", "analysis_date": kwargs.get("analysis_date"),
        "eval_window_days": kwargs["config"].eval_window_days,
        "engine_version": kwargs["config"].engine_version,
        "operation_advice": "买入", "position_recommendation": "long",
        "start_price": kwargs["start_price"], "end_close": 105.0,
        "max_high": 110.0, "min_low": 95.0, "stock_return_pct": 5.0,
        "direction_expected": "up", "direction_correct": True, "outcome": "win",
        "stop_loss": 90.0, "take_profit": 120.0,
        "hit_stop_loss": False, "hit_take_profit": False,
        "first_hit": "neither", "first_hit_date": None, "first_hit_trading_days": 12,
        "simulated_entry_price": kwargs["start_price"], "simulated_exit_price": 105.0,
        "simulated_exit_reason": "window_end", "simulated_return_pct": 5.0,
    }


def test_hk_intraday_processed_with_hk_market_window(monkeypatch, tmp_path):
    """HK00700 + 5m + 10 交易日:被处理(非跳过),引擎切片=660(hk 330/日),落库语义正确。"""
    os.environ["DATABASE_PATH"] = str(tmp_path / "hk_intraday.db")
    from src.config import Config
    from src.storage import DatabaseManager
    Config._instance = None
    DatabaseManager.reset_instance()
    svc = BacktestService(db_manager=DatabaseManager.get_instance())

    candidate = SimpleNamespace(
        id=7, code="HK00700", operation_advice="买入", stop_loss=90.0, take_profit=120.0,
        context_snapshot=json.dumps({"enhanced_context": {"date": "2026-05-01"}}),
    )
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: date(2026, 5, 1))
    monkeypatch.setattr(svc.stock_repo, "get_start_daily",
                        lambda code, analysis_date: SimpleNamespace(date=analysis_date, close=100.0))

    from data_provider.base import DataFetcherManager
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data",
                        lambda self, code, interval, **kw: (_minute_df(660).copy(), "AkshareFetcher"))

    captured_eval: Dict[str, Any] = {}

    def fake_eval(**kwargs):
        captured_eval.update(kwargs)
        return _completed_eval(**kwargs)

    from src.core import backtest_engine as beng
    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_eval))

    saved: List[Any] = []
    monkeypatch.setattr(svc.repo, "save_results_batch",
                        lambda results, **kw: (saved.extend(results), len(results))[1])
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    out = svc.run_backtest(interval="5m", eval_window_days=10)

    assert out["processed"] == 1 and out["completed"] == 1, f"HK 应被处理: {out}"
    assert out.get("skipped_unsupported", 0) == 0
    assert captured_eval["config"].eval_window_days == 660    # 10 * bars_per_day(5m, hk)=66
    assert len(saved) == 1
    r = saved[0]
    assert r.eval_window_days == 10
    assert r.bar_interval == "5m"
    assert r.engine_version == "v1-5m"
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_backtest_service_hk_intraday.py "tests/test_backtest_service_intraday.py::test_intraday_non_crypto_skipped" "tests/test_backtest_service_intraday.py::test_intraday_non_crypto_skip_counter" -q`
Expected: `test_hk_intraday_processed...` FAIL(HK 被 skipped_unsupported,processed=0);两 skip 测试用 510050 现在也会因 gate 未含 hk 而... 实为 510050 本就 unsupported → 这两测**改后即应 PASS**(510050 仍跳过)。故 Step2 主要红点是新 hk 集成测。

- [ ] **Step 3: 改实现 — gate 放行 + import(`src/services/backtest_service.py`)**

在 `:108` 之后新增一行 import:
```python
        from data_provider.base import is_perp_code, is_crypto_code, is_a_share_code, is_us_stock_code, market_of
        from data_provider import is_hk_stock_code
```
把 gate(`:115-122`):
```python
            if intraday and not (
                is_crypto_code(analysis.code)
                or is_perp_code(analysis.code)
                or is_a_share_code(analysis.code)
                or is_us_stock_code(analysis.code)
            ):
                # 分钟路径支持 crypto/perp、A股沪深/北交、美股个股;其余市场跳过（计入 skipped_unsupported）
                skipped_unsupported += 1
```
改为:
```python
            if intraday and not (
                is_crypto_code(analysis.code)
                or is_perp_code(analysis.code)
                or is_a_share_code(analysis.code)
                or is_us_stock_code(analysis.code)
                or is_hk_stock_code(analysis.code)
            ):
                # 分钟路径支持 crypto/perp、A股沪深/北交、美股个股、港股个股(best-effort);其余市场跳过（计入 skipped_unsupported）
                skipped_unsupported += 1
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_backtest_service_hk_intraday.py tests/test_backtest_service_intraday.py -q`
Expected: 全 PASS(新 hk 集成测 + 两 skip 测试[510050]+ 既有 intraday 全套不回归)。

- [ ] **Step 5: 提交**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_hk_intraday.py tests/test_backtest_service_intraday.py
git commit -m "feat: 链路A 放行港股个股分钟回测 + 集成测(skip 测试换码 510050)"
```

---

### Task 4: 链路 B band + docstring

**Files:**
- Modify: `src/services/signal_backtest_service.py:47-50`(_INTRADAY_MAX_DAYS)、`:117`(docstring)
- Test: `tests/test_signal_backtest_service.py:268-270`

**Interfaces:** Consumes 无(band 独立);`_load_bars` 经 `get_market_for_stock→hk` 自动路由(Task 2 已解锁)。

- [ ] **Step 1: 改测试(`tests/test_signal_backtest_service.py:268-270`)**

把:
```python
    # hk/None 不在 band 表 → band.get 回退基线（同 crypto fallback 分支，显式锁定回退）
    assert sbs._minute_fetch_days(market="hk", interval="5m") == 365
    assert sbs._minute_fetch_days(market="hk", interval="1h") == 730
```
改为:
```python
    # hk 已入 band 表(yfinance 上限保守):5m/15m→60、1h→730;1m 无键回退基线 365(fetch 层 fail-closed)
    assert sbs._minute_fetch_days(market="hk", interval="5m") == 60
    assert sbs._minute_fetch_days(market="hk", interval="15m") == 60
    assert sbs._minute_fetch_days(market="hk", interval="1h") == 730
    assert sbs._minute_fetch_days(market="hk", interval="1m") == 365
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest "tests/test_signal_backtest_service.py" -q -k minute_fetch_days`
Expected: FAIL —— `_minute_fetch_days("hk","5m")` 现回退 365 ≠ 60。

- [ ] **Step 3: 改实现 — band(`src/services/signal_backtest_service.py:47-50`)**

把:
```python
_INTRADAY_MAX_DAYS = {
    "us": {"1m": 7, "5m": 60, "15m": 60, "1h": 730},
    "cn": {"1m": 30, "5m": 90, "15m": 365, "1h": 730},
}
```
改为:
```python
_INTRADAY_MAX_DAYS = {
    "us": {"1m": 7, "5m": 60, "15m": 60, "1h": 730},
    "cn": {"1m": 30, "5m": 90, "15m": 365, "1h": 730},
    # hk 双源(akshare 东财主 + yfinance 兜底):保守对齐 yfinance 上限;1m fail-closed 不设键
    "hk": {"5m": 60, "15m": 60, "1h": 730},
}
```

- [ ] **Step 4: 改实现 — docstring(`src/services/signal_backtest_service.py:117`)**

把:
```python
                      分钟仅覆盖 crypto/A股沪深/美股个股；HK/指数无分钟取数,单股计入 errors。
```
改为:
```python
                      分钟覆盖 crypto/A股沪深/美股个股/港股个股(best-effort);指数等无分钟取数,单股计入 errors。
```

- [ ] **Step 5: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_service.py -q`
Expected: 全 PASS(hk band 60/60/730/365 + cn/us/crypto 不回归)。

- [ ] **Step 6: 提交**

```bash
git add src/services/signal_backtest_service.py tests/test_signal_backtest_service.py
git commit -m "feat: 链路B 港股分钟 band 表 + docstring(覆盖港股个股 best-effort)"
```

---

### Task 5: 文档

**Files:**
- Modify: `docs/intraday-backtest.md`(港股章 + §3 市场表 + `:3`/`:217`/`:279`/`:292` 逐行订正)、`docs/signal-credibility.md`(HK 注)、`docs/CHANGELOG.md`(`[Unreleased]` 扁平)

**Interfaces:** 无代码接口;文案须与已实现键名/语义一致。

- [ ] **Step 1: `docs/intraday-backtest.md` 订正与新增**

(1) §3 市场表(`bars_per_day`/`MARKET_TRADING_MINUTES` 处)加一行 `港股(hk) | 330 | 09:30-12:00 + 13:00-16:00(午休不计)`(对齐既有 cn/us 行格式)。
(2) `:279`「**港股仍不支持**分钟路径(美股见 §11)」改为:「**港股**分钟路径已支持(akshare 东财主 + yfinance 兜底,见港股章);1m fail-closed」。
(3) `:217`「**港股不在计划内**;港股标的在分钟路径下会被跳过(skipped_unsupported 计数)」整句删除或改为「港股个股已纳入分钟路径(best-effort)」。
(4) `:3` 头部若含「港股不在计划内」类表述,改为包含港股。
(5) `:292` 门面注释相关文档行「yfinance ... 在非 us 市场排除」改为「在非 (us, hk) 市场排除;港股分钟由 akshare 主 + yfinance 兜底」。
(6) 新增港股章(镜像美股 §11):数据源(akshare `stock_hk_hist_min_em` 主 + yfinance `0700.HK` 兜底)、330 分钟/日、1m fail-closed、best-effort 代码边界、成本走现有 fee/slip(默认 0,HK 双边印花税另立)、band 在线深度 deferred。

- [ ] **Step 2: `docs/signal-credibility.md`**

把港股「无分钟取数/计入 errors」表述改为「港股个股已支持分钟(best-effort)」。

- [ ] **Step 3: `docs/CHANGELOG.md`(`## [Unreleased]` 段首,扁平,无 `### 标题`)**

插入一行:
```
- [新功能] 港股(HK)分钟级回测:扩展港股个股分钟前向回测(链路A 操作建议 + 链路B 信号可信度),双源 akshare 东财(stock_hk_hist_min_em)主 + yfinance(0700.HK)兜底;bars_per_day 市场化(hk=330,1h ceil=6);港股 1m fail-closed;Tushare 经路由白名单排除(仅 A股);interval/默认/成本沿用,默认 1d 与现状一致;成本暂走跨市场 fee/slip(默认 0),港股双边印花税另立
```

- [ ] **Step 4: 核对 + 提交**

Run: `grep -n "港股\|hk" docs/intraday-backtest.md | head && grep -n "港股" docs/CHANGELOG.md`
Expected: 港股章/表/CHANGELOG 命中且无残留「不支持/不在计划内」旧表述。

```bash
git add docs/intraday-backtest.md docs/signal-credibility.md docs/CHANGELOG.md
git commit -m "docs: 港股分钟回测专题(数据源/330/1m fail-closed)+ 逐行订正 + CHANGELOG"
```

---

## 全量门禁(全部任务完成后)

- [ ] **后端 ci_gate**

Run: `./scripts/ci_gate.sh`(venv 在 PATH)
Expected: flake8 0 error;`pytest -m "not network"` 全绿;passed 数 = 基线 + 新增(hk helpers/market_of/akshare/路由/集成/band 用例 − 改写的负向用例数变化)。记录增量。
- **无前端代码改动 → 不需 web-gate**。

---

## Self-Review(plan vs. spec)

**1. Spec 覆盖:**
- §2 C1(MARKET hk + ceil 注释)→ Task 1 ✅
- §3 C2.1 market_of → Task 1;C2.2 guard + C2.3 路由白名单 → Task 2 ✅
- §4 C3 akshare HK 分支(1m 独立守卫)→ Task 2 ✅;yfinance 零改动靠 C2.3 ✅
- §5.1 链路A gate + import(`from data_provider import is_hk_stock_code`)→ Task 3 ✅
- §5.2 链路B band + docstring → Task 4 ✅
- §6 六处必改测试 → Task 1(test 1/2/5)+ Task 3(test 3/4 换 510050)+ Task 4(test 6)✅
- §7 新增测试(akshare hk/路由/集成)→ Task 2/3 ✅
- §8 文件清单 → 文件结构表全覆盖 ✅
- §10 YAGNI(不加 HK 成本/short/仅个股过滤/30m)→ 计划无相关改动 ✅

**2. Placeholder 扫描:** 无 TBD;每改码步给完整 old→new + 命令/预期。文档步(Task 5)给逐行订正点 + 新章要点(文案非代码,实现按要点撰写)。

**3. 类型/命名一致性:** `_is_hk_market`(base 内)用于 market_of/guard/路由;`is_hk_stock_code`(`from data_provider import`)用于 akshare 分支/链路A gate;`_AK_PERIOD` 无 1m → HK 1m 独立守卫;band `hk={5m:60,15m:60,1h:730}` ↔ Task4 测试 60/60/730/365;`bars_per_day(5m,hk)=66`/集成测 660;替换码 `510050`(已核验 unsupported)。
