# intraday_data capability 显式声明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `DataFetcherManager._intraday_fetchers_for` 的分钟数据源路由从「日线派生 + stub 探测 + ad-hoc 白名单」改为每个 fetcher **显式声明** `intraday_markets`,生产路由字节级不变。

**Architecture:** 在 `BaseFetcher` 加类属性 `intraday_markets: frozenset = frozenset()`(默认空),各分钟源覆写(`CryptoExchangeBase={crypto}`、`OkxPerpetualFetcher` 覆写 `={crypto_perp}`、`Tushare={cn}`、`Akshare={cn,hk}`、`Yfinance={us,hk}`)。门面路由收敛成一句 `market in getattr(f, "intraday_markets", frozenset())`,替换三层启发式;市场判定、runtime 可用性探测、cn/hk 排序表全部保留。先立 golden 特征化基线锁当前路由,再加声明,最后翻转门面。

**Tech Stack:** Python 3、`frozenset` 类属性、pytest + parametrize。验证 `./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`)。全程离线可验(经实测:默认管理器离线各市场路由确为下方 golden)。

## Global Constraints

> 每个任务的要求都隐含本节;值从 spec `docs/superpowers/specs/2026-07-01-intraday-capability-declaration-design.md` 逐字抄录。

- **纯重构,生产路由字节级不变**:对全部真实 fetcher(均 BaseFetcher 子类),各市场分钟路由的 fetcher 列表+顺序不变,由默认管理器 golden + 两源可用管理器 oracle 锁定。
- **声明式过滤用 `getattr(f, "intraday_markets", frozenset())`,不用直接属性访问**(与既有 getattr 探针 `base.py:791` 一致):无该属性的 duck-typed 注入源回退空集 → 优雅排除、不抛 AttributeError。
- **声明值精确**:`BaseFetcher=frozenset()`、`CryptoExchangeBase=frozenset({"crypto"})`、`OkxPerpetualFetcher=frozenset({"crypto_perp"})`(必须覆写,否则经 MRO 继承 `{crypto}`)、`TushareFetcher=frozenset({"cn"})`、`AkshareFetcher=frozenset({"cn","hk"})`、`YfinanceFetcher=frozenset({"us","hk"})`。
- **当前有效路由(golden 基线,已实测)**:crypto=`[Binance,Okx,Coinbase]`、crypto_perp=`[OkxPerpetual]`、cn(默认无 token)=`[Akshare]`、us=`[Yfinance]`、hk=`[Akshare,Yfinance]`、不支持代码=`[]`;两源可用时 cn=`[Tushare,Akshare]`。
- **契约细化(唯一非严格 byte-identical 偏差)**:经 `DataFetcherManager(fetchers=...)` 注入的 duck-typed 源须显式声明 `intraday_markets` 才入分钟路由,否则优雅排除。须更新受影响的注入 stub 测试(`tests/test_manager_intraday_cn.py:84-102`)。
- **不动**:`_DAILY_MARKET_FETCHER_SUPPORT` 与 `_filter_daily_fetchers_for_market`(日线路径仍用);其它 capability 路径(realtime_quote/stock_name/stock_list/boards/daily_data);`BaseFetcher.get_intraday_data` 默认抛 `NotImplementedError`(安全网);cn/hk 排序表语义;市场判定链。
- commit message:英文类型前缀 + 中文体,**不加** `Co-Authored-By`,不加工具/agent 前缀。
- 行号会漂移:按符号(类名/方法名/属性名)定位,勿盲信行号。

---

### Task 1: 默认管理器 golden 特征化基线（重构前锁定生产路由）

**Files:**
- Create: `tests/test_intraday_capability_routing.py`

**Interfaces:**
- Consumes: 既有 `DataFetcherManager._intraday_fetchers_for(code: str) -> list[BaseFetcher]`（`data_provider/base.py`）；`DataFetcherManager()` 默认构造（离线无 token）。
- Produces: 默认管理器各市场分钟路由的精确有序 golden + 跨市场防泄漏断言（后续任务须保持 GREEN）。

- [ ] **Step 1: 写 golden 特征化测试**

新建 `tests/test_intraday_capability_routing.py`：

```python
"""intraday_data capability 声明式路由 —— golden 等价 + 声明单测 + duck-typed 容错。

本文件锁定 DataFetcherManager._intraday_fetchers_for 的分钟源路由:
- 默认管理器(离线无 token)各市场精确有序列表(生产路由字节级不变基线);
- 跨市场防泄漏(yfinance 不入 cn、Tushare 不入 hk);
- 各 fetcher intraday_markets 声明值 + OkxPerpetual MRO 覆写;
- duck-typed 注入源容错(无声明优雅排除、有声明才入)。
"""
import pytest

from data_provider.base import DataFetcherManager


# 默认管理器(离线无 token)各市场分钟路由的精确有序 golden。经实测确认:
#   crypto=priority 顺序[Binance,Okx,Coinbase]、perp=[OkxPerpetual]、
#   cn=[Akshare](无 token,Tushare 被 is_available 剔除)、us=[Yfinance]、
#   hk=[Akshare,Yfinance](cn/hk 排序表)、510050(不支持 ETF)=[]。
_DEFAULT_GOLDEN = [
    ("BTC/USDT", ["BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"]),
    ("BTC/USDT:PERP", ["OkxPerpetualFetcher"]),
    ("600519", ["AkshareFetcher"]),
    ("AAPL", ["YfinanceFetcher"]),
    ("HK00700", ["AkshareFetcher", "YfinanceFetcher"]),
    ("510050", []),
]


@pytest.mark.parametrize("code,expected", _DEFAULT_GOLDEN)
def test_intraday_routing_default_manager_golden(code, expected):
    mgr = DataFetcherManager()
    assert [f.name for f in mgr._intraday_fetchers_for(code)] == expected


def test_intraday_routing_no_cross_market_leak():
    mgr = DataFetcherManager()
    cn = [f.name for f in mgr._intraday_fetchers_for("600519")]
    hk = [f.name for f in mgr._intraday_fetchers_for("HK00700")]
    assert "YfinanceFetcher" not in cn      # 补丁①:yfinance 日线支持 cn,但分钟不入 cn
    assert "TushareFetcher" not in hk       # 补丁②:Tushare 日线支持 hk,但分钟不入 hk
```

- [ ] **Step 2: 跑测试确认当前代码即 GREEN（特征化）**

Run: `.venv/bin/python -m pytest tests/test_intraday_capability_routing.py -v`
Expected: PASS（这是对**当前**路由的特征化基线,重构前必须先绿;若某行不符说明 golden 写错,先核对当前代码实际输出再改断言）。

- [ ] **Step 3: flake8 + commit**

Run: `.venv/bin/python -m flake8 tests/test_intraday_capability_routing.py`
Expected: 无输出。

```bash
git add tests/test_intraday_capability_routing.py
git commit -m "test: 锁定分钟路由默认管理器 golden 基线（重构前特征化,含跨市场防泄漏）"
```

---

### Task 2: 各数据源声明 `intraday_markets` + per-fetcher 单测

**Files:**
- Modify: `data_provider/base.py`（`BaseFetcher` 类体加类属性 `intraday_markets`）
- Modify: `data_provider/crypto_base.py`（`CryptoExchangeBase`）
- Modify: `data_provider/okx_perpetual_fetcher.py`（`OkxPerpetualFetcher` 覆写）
- Modify: `data_provider/tushare_fetcher.py`（`TushareFetcher`）
- Modify: `data_provider/akshare_fetcher.py`（`AkshareFetcher`）
- Modify: `data_provider/yfinance_fetcher.py`（`YfinanceFetcher`）
- Test: `tests/test_intraday_capability_routing.py`（追加 per-fetcher 声明单测）

**Interfaces:**
- Consumes: Task 1 golden（须保持 GREEN，证明声明为 dormant、未改路由）。
- Produces: 类属性 `BaseFetcher.intraday_markets: frozenset`（默认空）及各分钟源覆写值；供 Task 3 门面按其路由。

- [ ] **Step 1: 写失败测试（声明值 + OkxPerpetual MRO 覆写）**

在 `tests/test_intraday_capability_routing.py` 末尾追加：

```python
from data_provider.base import BaseFetcher
from data_provider.binance_fetcher import BinanceFetcher
from data_provider.okx_fetcher import OkxFetcher
from data_provider.coinbase_fetcher import CoinbaseFetcher
from data_provider.okx_perpetual_fetcher import OkxPerpetualFetcher
from data_provider.tushare_fetcher import TushareFetcher
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher


def test_base_fetcher_intraday_markets_empty_by_default():
    # 纯日线源(Efinance/Pytdx/Baostock/Longbridge/Finnhub/AlphaVantage)继承此空集 → 不入任何分钟路由
    # (纯日线源被排除已由 Task 1 golden 的 cn=[Akshare]/hk=[Akshare,Yfinance] 间接锁定)
    assert BaseFetcher.intraday_markets == frozenset()


@pytest.mark.parametrize("cls,expected", [
    (BinanceFetcher, frozenset({"crypto"})),
    (OkxFetcher, frozenset({"crypto"})),
    (CoinbaseFetcher, frozenset({"crypto"})),
    (OkxPerpetualFetcher, frozenset({"crypto_perp"})),
    (TushareFetcher, frozenset({"cn"})),
    (AkshareFetcher, frozenset({"cn", "hk"})),
    (YfinanceFetcher, frozenset({"us", "hk"})),
])
def test_fetcher_declares_intraday_markets(cls, expected):
    assert cls.intraday_markets == expected


def test_okx_perpetual_overrides_not_inherits_crypto():
    # OkxPerpetualFetcher(OkxFetcher(CryptoExchangeBase)):漏覆写会经 MRO 继承 {crypto}
    assert OkxPerpetualFetcher.intraday_markets == frozenset({"crypto_perp"})
    assert "crypto" not in OkxPerpetualFetcher.intraday_markets
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_intraday_capability_routing.py -k "intraday_markets or overrides" -v`
Expected: FAIL —— `AttributeError: type object 'BaseFetcher' has no attribute 'intraday_markets'`（或各 `cls.intraday_markets` 不存在）。

- [ ] **Step 3: 在 BaseFetcher 加默认空集类属性**

在 `data_provider/base.py` 的 `class BaseFetcher(ABC):` 类体靠前的类属性区（`name`/`priority` 等附近，按符号定位）加：

```python
    # 该源 get_intraday_data 服务的市场集合(空=不提供分钟数据);与 _DAILY_MARKET_FETCHER_SUPPORT
    # (日线)相互独立、互不派生。门面 _intraday_fetchers_for 按 market in getattr(f, "intraday_markets", ...) 路由。
    intraday_markets: frozenset = frozenset()
```

- [ ] **Step 4: 各分钟源覆写声明**

`data_provider/crypto_base.py` 的 `class CryptoExchangeBase(BaseFetcher):` 类体加：
```python
    intraday_markets = frozenset({"crypto"})
```
`data_provider/okx_perpetual_fetcher.py` 的 `class OkxPerpetualFetcher(OkxFetcher):` 类体加（**必须覆写**，否则继承 `{crypto}`）：
```python
    intraday_markets = frozenset({"crypto_perp"})
```
`data_provider/tushare_fetcher.py` 的 `class TushareFetcher(BaseFetcher):` 类体加：
```python
    intraday_markets = frozenset({"cn"})
```
`data_provider/akshare_fetcher.py` 的 `class AkshareFetcher(BaseFetcher):` 类体加：
```python
    intraday_markets = frozenset({"cn", "hk"})
```
`data_provider/yfinance_fetcher.py` 的 `class YfinanceFetcher(BaseFetcher):` 类体加：
```python
    intraday_markets = frozenset({"us", "hk"})
```

- [ ] **Step 5: 跑 per-fetcher 单测 + Task 1 golden 回归**

Run: `.venv/bin/python -m pytest tests/test_intraday_capability_routing.py -v`
Expected: PASS —— 声明单测全绿；**Task 1 golden 仍全绿**（声明为 dormant，门面尚未改，路由不变）。

- [ ] **Step 6: flake8 + commit**

Run: `.venv/bin/python -m flake8 data_provider/base.py data_provider/crypto_base.py data_provider/okx_perpetual_fetcher.py data_provider/tushare_fetcher.py data_provider/akshare_fetcher.py data_provider/yfinance_fetcher.py tests/test_intraday_capability_routing.py`
Expected: 无输出。

```bash
git add data_provider/base.py data_provider/crypto_base.py data_provider/okx_perpetual_fetcher.py data_provider/tushare_fetcher.py data_provider/akshare_fetcher.py data_provider/yfinance_fetcher.py tests/test_intraday_capability_routing.py
git commit -m "feat: 各数据源声明 intraday_markets 能力位（BaseFetcher 默认空 + 各分钟源覆写，OkxPerpetual 覆写为 crypto_perp）"
```

---

### Task 3: 门面路由改声明式 + duck-typed stub 更新 + 容错/可用性测试

**Files:**
- Modify: `data_provider/base.py`（`DataFetcherManager._intraday_fetchers_for` 方法体，约 `:1539-1558`）
- Modify: `tests/test_manager_intraday_cn.py`（`test_intraday_fetchers_for_cn_orders_tushare_first_when_both_present` 的 `_Stub` 补 `intraday_markets`）
- Test: `tests/test_intraday_capability_routing.py`（追加 runtime 可用性 + duck-typed 容错）

**Interfaces:**
- Consumes: Task 2 的 `intraday_markets` 声明（真实 fetcher 均有）；既有 `_get_fetchers_snapshot()`、`_filter_fetchers_by_capability(fetchers, capability="intraday_data")`、市场判定链 `is_perp_code/is_crypto_code/is_a_share_code/is_us_stock_code/_is_hk_market`。
- Produces: 声明式 `_intraday_fetchers_for`（路由生产字节级不变，且对 duck-typed 注入源 getattr 容错）。

- [ ] **Step 1: 更新既有 duck-typed stub 测试（防重构后 AttributeError/被排除）**

在 `tests/test_manager_intraday_cn.py` 的 `test_intraday_fetchers_for_cn_orders_tushare_first_when_both_present` 内，把 `_Stub` 改为携带 `intraday_markets`（stub 本就模拟 Tushare/Akshare，声明其真实市场是忠实适配）：

```python
    class _Stub:
        def __init__(self, name, prio, intraday_markets=frozenset()):
            self.name = name
            self.priority = prio
            self.intraday_markets = intraday_markets

        def is_available(self):
            return True

        def get_intraday_data(self, stock_code, interval, **k):  # 覆写以通过 override 过滤
            return None

    # 故意让 akshare 优先级数字更小(若无 cn 排序会排在前),以证明排序生效
    ak = _Stub("AkshareFetcher", 0, frozenset({"cn", "hk"}))
    ts = _Stub("TushareFetcher", 5, frozenset({"cn"}))
```

（其余断言 `names == ["TushareFetcher", "AkshareFetcher"]` 不变。此测试即两源可用管理器的 cn 排序 oracle。）

先确认全仓无其它「注入 duck-typed 分钟 stub 且走真实 `_intraday_fetchers_for`」的测试：
Run: `grep -rn "DataFetcherManager(fetchers=" tests/`
Expected: 除 `tests/test_manager_intraday_cn.py:100`（本步已处理）外，其余命中均走 daily/realtime/其它 capability 路径、不调用 `_intraday_fetchers_for`（无需改）。若发现新的分钟路由注入 stub，同样补 `intraday_markets`。

- [ ] **Step 2: 写 runtime 可用性 + duck-typed 容错测试**

在 `tests/test_intraday_capability_routing.py` 末尾追加：

```python
def test_intraday_routing_respects_runtime_availability():
    # 声明过滤之后仍跑 capability 可用性探测:无 token Tushare(is_available→False)从 cn 剔除,只剩 Akshare
    class _Stub:
        def __init__(self, name, im, avail):
            self.name = name
            self.priority = 0
            self.intraday_markets = im
            self._avail = avail

        def is_available(self):
            return self._avail

        def get_intraday_data(self, *a, **k):
            return None

    ts = _Stub("TushareFetcher", frozenset({"cn"}), False)
    ak = _Stub("AkshareFetcher", frozenset({"cn", "hk"}), True)
    mgr = DataFetcherManager(fetchers=[ts, ak])
    assert [f.name for f in mgr._intraday_fetchers_for("600519")] == ["AkshareFetcher"]


def test_intraday_routing_tolerates_and_requires_declaration():
    # 契约细化:无 intraday_markets 的 duck-typed 源 → getattr 回退空集 → 优雅排除、不抛 AttributeError;
    # 声明后即被纳入(证路由确按 intraday_markets 而非日线派生)。
    class _NoAttr:            # 无 intraday_markets、非 BaseFetcher 子类、名字不在日线表
        name = "NoAttrSrc"
        priority = 0

        def is_available(self):
            return True

        def get_intraday_data(self, *a, **k):
            return None

    class _Declared:          # 声明 crypto,但名字同样不在日线表 —— 旧「日线派生」路由不会收它
        name = "DeclaredSrc"
        priority = 0
        intraday_markets = frozenset({"crypto"})

        def is_available(self):
            return True

        def get_intraday_data(self, *a, **k):
            return None

    assert DataFetcherManager(fetchers=[_NoAttr()])._intraday_fetchers_for("BTC/USDT") == []
    assert [f.name for f in DataFetcherManager(fetchers=[_Declared()])._intraday_fetchers_for("BTC/USDT")] == ["DeclaredSrc"]
```

- [ ] **Step 3: 跑测试确认失败（RED 驱动重构）**

Run: `.venv/bin/python -m pytest tests/test_intraday_capability_routing.py -k "tolerates_and_requires or respects_runtime" -v`
Expected: `test_intraday_routing_tolerates_and_requires_declaration` **FAIL** —— 第二个断言 `["DeclaredSrc"]` 失败得 `[]`：当前 `_intraday_fetchers_for` 按**日线派生**路由（`DeclaredSrc` 不在 `_DAILY_MARKET_FETCHER_SUPPORT` → 被 `_filter_daily_fetchers_for_market` 剔除），不消费 `intraday_markets`。（`respects_runtime` 当前即 GREEN，属特征化。）

- [ ] **Step 4: 门面路由改声明式**

在 `data_provider/base.py` 的 `DataFetcherManager._intraday_fetchers_for` 内（市场判定段 `if is_perp_code(code): ... else: return []` **不变**），把当前中段：

```python
        fetchers = self._get_fetchers_snapshot()
        fetchers = self._filter_daily_fetchers_for_market(fetchers, market)
        fetchers = self._filter_fetchers_by_capability(fetchers, capability="intraday_data")
        fetchers = [
            f for f in fetchers
            if type(f).get_intraday_data is not BaseFetcher.get_intraday_data
        ]
        if market not in ("us", "hk"):
            fetchers = [f for f in fetchers if f.name != "YfinanceFetcher"]
        if market == "hk":
            _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
            fetchers = [f for f in fetchers if f.name in _hk_order]
            fetchers.sort(key=lambda f: _hk_order[f.name])
        if market == "cn":
            _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
            fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
        return fetchers
```

改为：

```python
        fetchers = self._get_fetchers_snapshot()
        # 声明式过滤,替换 日线派生+stub 探测+补丁①②。用 getattr 容错(与既有探针一致):
        # 无 intraday_markets 的 duck-typed 注入源回退空集 → 优雅排除,不抛 AttributeError。
        fetchers = [f for f in fetchers if market in getattr(f, "intraday_markets", frozenset())]
        fetchers = self._filter_fetchers_by_capability(fetchers, capability="intraday_data")
        if market == "hk":
            _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
            fetchers.sort(key=lambda f: _hk_order.get(f.name, 2))
        if market == "cn":
            _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
            fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
        return fetchers
```

同步更新该方法 docstring：把「剔除未覆写 get_intraday_data 的源」「非 (us,hk) 排除 yfinance」「hk 白名单」等描述改为「按各源 `intraday_markets` 声明路由；排序表保留」。

- [ ] **Step 5: 跑全部分钟路由测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/test_intraday_capability_routing.py tests/test_manager_intraday_cn.py tests/test_manager_intraday_us.py tests/test_get_intraday_data.py -v`
Expected: 全 PASS —— Task 1 golden、per-fetcher 声明、更新后的 cn 两源 oracle、runtime 可用性、duck-typed 容错(含第二断言现 GREEN)、既有「不支持市场 raises」全绿。
（注:若把 Step 4 写成 `market in f.intraday_markets`（无 getattr），`tolerates_and_requires` 第一断言会对 `_NoAttr` 抛 AttributeError → 该测试正是强制 getattr 的守卫。）

- [ ] **Step 6: 确认 stub 探测已彻底移除、无遗留依赖**

Run: `grep -rn "get_intraday_data is not BaseFetcher" data_provider/ tests/`
Expected: 无输出（stub 探测式只此一处，已删；无其它调用者依赖它）。

- [ ] **Step 7: flake8 + commit**

Run: `.venv/bin/python -m flake8 data_provider/base.py tests/test_manager_intraday_cn.py tests/test_intraday_capability_routing.py`
Expected: 无输出。

```bash
git add data_provider/base.py tests/test_manager_intraday_cn.py tests/test_intraday_capability_routing.py
git commit -m "refactor: 分钟路由改声明式 intraday_markets（getattr 容错，替换日线派生+stub 探测+ad-hoc 白名单）"
```

---

## 收尾：整批门禁

- [ ] **Step A: 跑 ci_gate**

Run: `./scripts/ci_gate.sh`
Expected: `backend-gate: all checks passed`；pytest 全绿（基线 3847 passed，本计划净增约 +18 用例：Task1 golden 参数化 6 + 防泄漏 1、Task2 声明 7+2、Task3 runtime 1 + 容错 1；cn 两源 oracle 为既有测试更新非新增）。

- [ ] **Step B: 交付说明**

按 spec §10：改了什么 / 为什么（消除「日线派生」路由漂移根因）/ 验证情况（ci_gate + golden 默认+两源可用 + 既有 raises 回归）/ 未验证项（无，离线全覆盖）/ 风险点（低，多重锁定；唯一偏差=duck-typed 注入源须声明，生产零影响）/ 回滚方式（单分支 `git revert`）。

---

## Self-Review（plan vs spec）

**1. Spec 覆盖：**
- §4 声明表（BaseFetcher 空 + 5 覆写）→ Task 2 ✓
- §5 门面路由改写（getattr 声明式 + 排序保留）→ Task 3 Step 4 ✓
- §6 保留不变项（市场判定/可用性/排序表/NotImplementedError/日线表）→ Task 3 未触碰这些 ✓
- §7.1 golden（默认管理器 + 两源可用）→ Task 1（默认）+ Task 3 Step 1（两源 cn oracle）✓
- §7.2 防泄漏 → Task 1 ✓；§7.3 默认空 → Task 2 ✓；§7.4 per-fetcher → Task 2 ✓；§7.5 perp 覆写 → Task 2 ✓
- §7.6 runtime 可用性 → Task 3 Step 2 ✓;§7.7 既有回归 + grep → Task 3 Step 1/Step 5 ✓;§7.8 duck-typed 容错 → Task 3 Step 2 ✓
- §7.9 声明↔门面双真值防漂移 → **有意略去（YAGNI）**：golden（改 frozenset → RED、改门面 → RED）已双侧覆盖该漂移,独立重算式 §7.9 与门面逻辑重复、偏 tautology,不写。
- §9 契约细化 → Task 3 Step 1 stub 更新 + Step 2 容错测试 ✓

**2. Placeholder 扫描：** 无 TBD/TODO；每个 code step 均含完整代码与确切命令/期望输出；golden 值均经实测。✓

**3. 类型一致性：** `intraday_markets: frozenset` 在 Task 2 定义、Task 3 门面 `getattr(f, "intraday_markets", frozenset())` 消费一致；`_intraday_fetchers_for(code)->list` 签名不变；stub `intraday_markets` 属性名与生产一致。✓
