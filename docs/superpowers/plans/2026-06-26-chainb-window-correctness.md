# 链路B 窗口正确性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复链路B（信号可信度三重门回测）两处窗口缺陷：`_eval` 右端截尾偏差（C1）与非 crypto 分钟历史深度不受控（C2）。

**Architecture:** C1 把纯函数 `signal_backtest._eval` 的评估上界 `n-1` 收紧为 `n-horizon`，保证每个被评估信号都有完整 horizon 前瞻（统一日线+分钟，interval-agnostic）；同步两处承重 golden 内联 oracle。C2 在 `signal_backtest_service` 引入 market×interval band 表，`_load_bars` 按 market 下传 `start_date`（美股夹 yfinance band、A股 tushare 加深、crypto 维持 days-only 字节级不变）。

**Tech Stack:** Python 3.10、pandas、pytest；venv 解释器 `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`。

**Spec:** `docs/superpowers/specs/2026-06-26-chainb-window-correctness-design.md`（v2，经对抗式审查收敛，见其 §9）。

## Global Constraints

- 运行测试用 venv 解释器，工作目录须为仓库/worktree 根（`src.` 包导入依赖 cwd）：`/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python -m pytest ...`。
- crypto 路径必须**字节级不变**：`_minute_fetch_start_date(market="crypto")` 返回 `None`，`_minute_fetch_days(market="crypto", ...)` 返回基线 730/365（与旧 `_minute_fetch_days(market_interval=)` 逐 interval 相等）。
- 无 DB schema 迁移、无 `.env`/配置项新增；`_INTRADAY_MAX_DAYS` 是源能力代码常量（同既有 `_YF_INTERVAL`/`MARKET_TRADING_MINUTES`），不是可调配置。
- C1 是**绝对边界** off-by-one 修复：回归测试必须用**绝对计数**，差分型断言对恒定偏移免疫（spec §5.1 ⚠）。
- 承重 golden 套件有**两处**逐窗 oracle 须随 C1 同步改（`test_signal_eval_vectorization.py:327` 与 `:362`）；仅 `:392` 不动（spec §5.2）。
- commit message：英文类型前缀 + 中文描述（与近期 git 历史一致），**不加** `Co-Authored-By`、不加工具/agent 前缀。
- 提交仅落在隔离 feature 分支；**不 push**，**不合 main**（合并须用户显式确认，由 finishing-a-development-branch 处理）。
- 单股取数失败不得拖垮整批（既有 `run` 的 `except Exception → errors++` 语义保持）。

---

## Task 1：C1 — `_eval` 右端截尾修复 + 两处 oracle 镜像 + 绝对边界回归

**Files:**
- Modify: `src/services/signal_backtest.py`（`_eval` 循环上界 + docstring，约 line 89-136）
- Modify: `tests/test_signal_eval_vectorization.py`（`:327` 与 `:362` 两处 oracle 上界）
- Test: `tests/test_signal_backtest.py`（新增绝对边界回归）

**Interfaces:**
- Consumes: `evaluate_baseline_outcomes(df, *, market, horizon, config=None, min_history=40)`、`evaluate_signal_outcomes(...)`（签名不变）。
- Produces: `_eval` 行为变更——评估 bar 集合由 `range(min_history, n-1)` 收紧为 `range(min_history, n-horizon)`；对 Task 2/3 无接口变化。

- [ ] **Step 1：写失败的绝对边界回归测试**

在 `tests/test_signal_backtest.py` 末尾（`_make_history_with_signals` 等既有 fixture 之后）追加：

```python
# ---------------------------------------------------------------------------
# C1: 右端截尾边界（绝对计数锁定 n-horizon）
# ---------------------------------------------------------------------------

def _smooth_uptrend_df(n):
    """全有效价位 fixture：平滑上升 + OHLC 振幅（ATR>0），价位从 t=19 起非 None。

    derive_price_levels 仅依赖 rolling MA20 / 20根 swing-low / ATR14（不依赖 swing pivot），
    平滑趋势即可让 t>=19 全部产出有效 stop/target；high>low 保证 ATR>0（避免退化为 None）。
    """
    close = np.linspace(50.0, 50.0 + 0.4 * n, n)
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.1
    vol = np.full(n, 1000.0)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


@pytest.mark.parametrize("n,h,expected", [(50, 10, 0), (51, 10, 1), (120, 10, 70), (120, 5, 75)])
def test_eval_right_edge_absolute_count(n, h, expected):
    """C1: 评估上界为 n-horizon，每个被评估 bar 都有完整 horizon 前瞻。

    全有效价位 fixture 下，baseline 计数 == max(0, (n-h)-min_history)（min_history=40）。
    绝对计数对上界 off-by-one 敏感：n-1（旧）与 n-h±1 都会被 (50,10)/(51,10) 边界例 + (120,*) 检出。
    (51,10)→1 顺带证明唯一被评估 bar t=40 的前瞻 df.iloc[41:51] 恰 10 根（末根满 horizon）；
    (50,10)→0 证明不足完整 horizon 的 bar 一律不评估。
    """
    df = _smooth_uptrend_df(n)
    base = evaluate_baseline_outcomes(df, market="cn", horizon=h)
    assert len(base) == expected
```

- [ ] **Step 2：运行测试确认失败**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest.py::test_eval_right_edge_absolute_count -v`
Expected: FAIL。当前 `n-1` 边界下 (120,10) 得 79≠70、(51,10) 得 10≠1、(50,10) 得 9≠0。

- [ ] **Step 3：改 `_eval` 循环上界 + docstring**

在 `src/services/signal_backtest.py` 的 `_eval`：

```python
-    for t in range(min_history, n - 1):                                    # 至少留 1 根前瞻
+    for t in range(min_history, n - horizon):                              # 保证完整 horizon 根前瞻（右端欠龄不计入）
```

并把模块 docstring（line 7-15 区域）与 `_eval` docstring 中「至少留 1 根前瞻」相关表述订正为：保证每个被评估 bar 都有完整 horizon 根前瞻，消除右端截尾偏差。`evaluate_signal_outcomes`/`evaluate_baseline_outcomes` 的 `horizon` 参数语义由「前瞻 bar 数上限」改述为「完整前瞻 bar 数」。

- [ ] **Step 4：同步两处承重 golden 内联 oracle**

在 `tests/test_signal_eval_vectorization.py`，`_oracle_signal_outcomes`（line 327）：

```python
-    for t in range(min_history, n - 1):
+    for t in range(min_history, n - horizon):
```

`test_golden_baseline_unchanged` 的内联参照（line 362，该测试硬编码 horizon=10）：

```python
-    for t in range(40, n - 1):
+    for t in range(40, n - 10):
```

⚠ 仅改这两处循环上界；**line 392**（`assert any(k < n - 1 for k in sig.keys())`，关于 `compute_signals_for_all_bars` 的 sig.keys）与 horizon 截尾无关，**保持不动**。两处 oracle 的 `fwd` 截短逻辑（`df.iloc[t+1:t+1+horizon]` / `df2.iloc[t+1:t+1+10]`）已与 `_eval` 一致，无需改。

- [ ] **Step 5：运行新测试 + 承重 golden 确认通过**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest.py::test_eval_right_edge_absolute_count tests/test_signal_eval_vectorization.py -v`
Expected: PASS。新边界测试 4 例全过；`test_golden_signal_equiv_oracle_multimarket`（8 种子）与 `test_golden_baseline_unchanged` 转绿（B1 直接回归证据）。

- [ ] **Step 6：跑全部链路B 信号套件确认无回归**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest.py tests/test_signal_eval_vectorization.py tests/test_signal_backtest_service.py -q`
Expected: PASS（既有 `test_evaluate_signal_outcomes_exact_dedup_count` 的 bar60 ∈ range(40,70) 仍 `vb_count==1`；service 层平价 fixture 不受影响）。

- [ ] **Step 7：提交**

```bash
git add src/services/signal_backtest.py tests/test_signal_eval_vectorization.py tests/test_signal_backtest.py
git commit -m "fix(signals): 链路B _eval 消除右端截尾偏差(n-1→n-horizon)+ 两处 golden oracle 镜像 + 绝对边界回归"
```

---

## Task 2：C2 — band 表 + market 感知 helper + `_load_bars` 下传 start_date

**Files:**
- Modify: `src/services/signal_backtest_service.py`（顶部 import；`_minute_fetch_days` 签名；新增 `_INTRADAY_MAX_DAYS` 与 `_minute_fetch_start_date`；`_load_bars` 签名与调用；`run` 调用点 line 114）
- Test: `tests/test_signal_backtest_service.py`（helper 单测 + `_load_bars` 抓参）

**Interfaces:**
- Consumes: `DataFetcherManager().get_intraday_data(code, interval, start_date=None, end_date=None, days=30)`；`get_market_for_stock(code) -> 'cn'|'hk'|'us'|'crypto'|None`（crypto 含 perp）。
- Produces:
  - `_minute_fetch_days(*, market: str, interval: str) -> int`
  - `_minute_fetch_start_date(*, market: str, interval: str, today: Optional[date] = None) -> Optional[str]`
  - `_load_bars(self, svc, code, interval, market)`（新增 `market` 第 4 位置参数）

- [ ] **Step 1：写失败的 helper 单测 + `_load_bars` 抓参测试**

在 `tests/test_signal_backtest_service.py` 末尾追加（文件已 `import pandas as pd`、`from types import SimpleNamespace`、`from src.services import signal_backtest_service as sbs`、有 `_minute_df`）：

```python
from datetime import date as _date, timedelta as _td


def test_minute_fetch_days_band_clamp():
    # us 夹 yfinance band；cn 保守夹取；crypto 走基线（字节级不变）
    assert sbs._minute_fetch_days(market="us", interval="5m") == 60
    assert sbs._minute_fetch_days(market="us", interval="15m") == 60
    assert sbs._minute_fetch_days(market="us", interval="1h") == 730
    assert sbs._minute_fetch_days(market="us", interval="1m") == 7
    assert sbs._minute_fetch_days(market="cn", interval="1m") == 30
    assert sbs._minute_fetch_days(market="cn", interval="5m") == 90
    assert sbs._minute_fetch_days(market="cn", interval="15m") == 365
    assert sbs._minute_fetch_days(market="cn", interval="1h") == 730
    assert sbs._minute_fetch_days(market="crypto", interval="5m") == 365
    assert sbs._minute_fetch_days(market="crypto", interval="1h") == 730


def test_minute_fetch_start_date_crypto_is_none():
    assert sbs._minute_fetch_start_date(market="crypto", interval="5m") is None
    assert sbs._minute_fetch_start_date(market="crypto", interval="1h") is None


def test_minute_fetch_start_date_non_crypto_anchored():
    today = _date(2026, 6, 26)
    assert sbs._minute_fetch_start_date(market="us", interval="5m", today=today) == (today - _td(days=60)).isoformat()
    assert sbs._minute_fetch_start_date(market="us", interval="1h", today=today) == (today - _td(days=730)).isoformat()
    assert sbs._minute_fetch_start_date(market="cn", interval="5m", today=today) == (today - _td(days=90)).isoformat()
    assert sbs._minute_fetch_start_date(market="cn", interval="1m", today=today) == (today - _td(days=30)).isoformat()


def test_load_bars_passes_market_aware_start_date(monkeypatch):
    """_load_bars 按 market 下传 start_date/days：crypto 字节级不变；us/cn 夹 band。"""
    from data_provider.base import DataFetcherManager
    captured = {}

    def fake_intraday(self, code, interval, start_date=None, days=30, **kw):
        captured["start_date"] = start_date
        captured["days"] = days
        return _minute_df(60), "FakeFetcher"

    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_intraday)
    # _load_bars 不透传 today → monkeypatch 模块级 date 锁定确定性
    FIXED = _date(2026, 6, 26)
    monkeypatch.setattr(sbs, "date", SimpleNamespace(today=lambda: FIXED))

    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)

    svc._load_bars(None, "BTC/USDT", "5m", "crypto")
    assert captured["start_date"] is None and captured["days"] == 365  # 字节级不变

    svc._load_bars(None, "AAPL", "5m", "us")
    assert captured["start_date"] == (FIXED - _td(days=60)).isoformat() and captured["days"] == 60

    svc._load_bars(None, "600519", "5m", "cn")
    assert captured["start_date"] == (FIXED - _td(days=90)).isoformat() and captured["days"] == 90
```

- [ ] **Step 2：运行测试确认失败**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest_service.py -k "minute_fetch or load_bars_passes" -v`
Expected: FAIL（`_minute_fetch_days(market=...)` 旧签名是 `market_interval` → TypeError；`_minute_fetch_start_date` 不存在 → AttributeError；`_load_bars` 仅 3 参 → TypeError）。

- [ ] **Step 3：加 band 表 + 改 `_minute_fetch_days` + 新增 `_minute_fetch_start_date`**

在 `src/services/signal_backtest_service.py` 顶部 import 区加：

```python
from datetime import date, timedelta
```

把现有 `_minute_fetch_days`（line 37-45）整体替换为：

```python
# 各源分钟历史「单次安全回看上限」(日历天)：据 start_date 加深历史时按 市场×interval 夹取，
# 避免向源请求其单次调用无法稳定返回的过深窗口。
#   us(yfinance)：5m/15m≈60d、1h≈730d 为文档硬上限；1m=7d(且 1m 已在 fetcher fail-closed)。
#   cn(tushare)：stk_mins 单次有行数上限，下列为保守值——
#     ⚠ 实现期须在线核验 tushare 对 today-N 的 1m/5m/15m/1h 真实单次返回(优雅近端子集 / 报错 /
#       返回错窗)，据实校准本表(可放宽)；核验前以保守上限避免 cn 高频从「浅窗可用」恶化为单股 errors。
#       关键：tushare get_intraday_data 体内不读 days(no-op)，但真正消费 start_date，故旧「days 偏大
#       不取错数」的安全性不可迁移到 start_date——这正是本表对 cn 也必须夹取的原因。
# crypto 不在表中：按 days 锚定、不下传 start_date(见 _minute_fetch_start_date)，行为字节级不变。
_INTRADAY_MAX_DAYS = {
    "us": {"1m": 7, "5m": 60, "15m": 60, "1h": 730},
    "cn": {"1m": 30, "5m": 90, "15m": 365, "1h": 730},
}


def _minute_fetch_days(*, market: str, interval: str) -> int:
    """分钟取数回看天数（传给 get_intraday_data 的 days，并据此推 start_date）。

    1h 历史更深取 730、其余取 365 为基线；再按 _INTRADAY_MAX_DAYS[market][interval] 夹取
    (us/cn 各源单次安全上限)。crypto 不在表中→返回基线(与旧 _minute_fetch_days 逐 interval 相等)。
    """
    base = 730 if interval == "1h" else 365
    band = _INTRADAY_MAX_DAYS.get(market, {})
    return min(base, band.get(interval, base))


def _minute_fetch_start_date(*, market: str, interval: str, today=None):
    """非 crypto 分钟取数的历史起点（ISO date 字符串）。

    crypto 返回 None → 维持 get_intraday_data 的 days-only 近窗行为(字节级不变)；
    非 crypto 返回 today - _minute_fetch_days，使 tushare/yfinance 真正加深历史。
    today 默认 date.today()；helper 单测可显式注入 today，_load_bars 集成测试经 monkeypatch
    模块级 date 锁定(本函数不透传 today)。
    """
    if market == "crypto":
        return None
    ref = today or date.today()
    return (ref - timedelta(days=_minute_fetch_days(market=market, interval=interval))).isoformat()
```

- [ ] **Step 4：改 `_load_bars` 接 market 并下传 start_date + `run` 调用点**

`_load_bars`（line 162-179）替换为：

```python
    def _load_bars(self, svc, code, interval, market):
        """按 interval 取 bar：日线走 StockService（不变），分钟走链路A get_intraday_data。

        分钟路径按 market 计算回看深度与历史起点：crypto 维持 days-only(字节级不变)，
        非 crypto 下传 start_date 真正加深历史(美股夹 yfinance band)。把 'datetime' 列重命名为
        'date' 复用既有 'date' 列契约。取不到数据返回 None(交由 run 计入 skipped)。
        """
        if not is_intraday_interval(interval):       # '1d'
            hist = svc.get_history_data(stock_code=code, period="daily", days=_FETCH_DAYS)
            rows = (hist or {}).get("data") or []
            return pd.DataFrame(rows) if rows else None
        from data_provider.base import DataFetcherManager
        days = _minute_fetch_days(market=market, interval=interval)
        start_date = _minute_fetch_start_date(market=market, interval=interval)
        df, _src = DataFetcherManager().get_intraday_data(
            code, interval, start_date=start_date, days=days)
        if df is None or df.empty:
            return None
        return df.rename(columns={"datetime": "date"})
```

`run` 内 line 114 调用点：

```python
-                df = self._load_bars(svc, code, interval)
+                df = self._load_bars(svc, code, interval, market)
```

- [ ] **Step 5：运行新测试确认通过**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest_service.py -k "minute_fetch or load_bars_passes" -v`
Expected: PASS（4 个新测试全过）。

- [ ] **Step 6：跑 service 全套确认既有用例无回归**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest_service.py -q`
Expected: PASS（既有 `test_run_interval_5m_uses_intraday_and_tags`/`_real_eval_offline`/`_1d_uses_daily_path` 不变——它们 mock `get_market_for_stock`→crypto/cn，crypto 走 start_date=None/days=365、cn 日线分支不受影响；fake_intraday 用 `**kw` 吸收新 kwargs）。

- [ ] **Step 7：提交**

```bash
git add src/services/signal_backtest_service.py tests/test_signal_backtest_service.py
git commit -m "feat(signals): 链路B 分钟回测非 crypto 历史深度受控(band 表+market 感知 start_date),crypto 字节级不变"
```

---

## Task 3：文档 + 链路A 核验 + CHANGELOG + 陈旧 docstring 顺手订正

**Files:**
- Modify: `tests/test_signal_backtest.py`（订正 `test_evaluate_signal_outcomes_exact_dedup_count` 陈旧 docstring）
- Modify: `docs/signal-credibility.md`（§5.2 续记 C1 行为变更 + 非 crypto 深度）
- Modify: `docs/intraday-backtest.md`（链路A end_date 闭合核验 + C2 非 crypto 深度行为变更）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

**Interfaces:** 无代码接口；仅文档与测试 docstring。

- [ ] **Step 1：链路A `end_date 右边界` 核验（只读，不改码）**

读以下代码确认 spec §0.4 证据链成立（结论写入 Step 3 文档）：
- `src/services/backtest_service.py:185-204`：分钟路径算 `_minute_window_start`/`_window_end_date` 并向 `get_intraday_data` 下传 `start_date`+`end_date`。
- `data_provider/binance_fetcher.py::_page_klines`：crypto 忽略 `end_date`，但 `limit≈days×bars_per_day` 正向翻页封顶右边界。
- `data_provider/tushare_fetcher.py` / `akshare_fetcher.py` / `yfinance_fetcher.py` 的 `get_intraday_data`：A股/美股 `end_date` 直接传源 API。
- `evaluate_single`（链路A 引擎）：`forward_bars[:eval_days]` 二次截断。

预期结论：链路A 右边界已等价闭合，本特性不改链路A 代码。

- [ ] **Step 2：订正 `test_evaluate_signal_outcomes_exact_dedup_count` 陈旧 docstring**

`tests/test_signal_backtest.py` 该测试的 docstring（提及已删除的 `m.timestamp == last_bar_ts` 过滤）替换为：

```python
    """去重计数守卫：fixture 在 bar 60 唯一触发 1 条 volume_breakout。

    向量化 _eval 经 compute_signals_for_all_bars 单遍预计算每根 bar 的因果信号集合，每根 bar
    的同类信号至多计一次；此测试用精确计数(==1 而非 >0)守护该去重语义：若预计算把相邻 bar 的
    volume_breakout 重复计入，Counter 将 > 1。bar 60 落在 C1 新边界 range(40, 70) 内，截尾修复
    不影响本断言。
    """
```

- [ ] **Step 3：更新 `docs/signal-credibility.md` §5.2**

在 §5.2（向量化行为变更小节）追加（中文）：

```markdown
- **链路B 右端截尾修复（2026-06-26）**：`_eval` 评估上界由 `n-1` 收紧为 `n-horizon`，仅统计有完整
  horizon 前瞻的信号，消除「慢解析者记 expired 被排除、快解析者计入」的右端截尾偏差。影响**日线 +
  分钟** signal_stats：`win/loss/sample/win_rate/ci_low/ci_high/baseline_win_rate/excess` 小幅变化，
  最近 `horizon-1` 根欠龄信号不再计入。图表 marker 与几何不变；重跑 `--signal-backtest` 落库后命中率
  注解数值随之刷新，并可能跨 `min_sample` 阈值出现/消失。
- **非 crypto 分钟历史深度（2026-06-26）**：链路B 分钟回测对非 crypto 显式下传 `start_date` 加深历史
  （美股夹 yfinance band 5m/15m=60d、1h=730d；A股 tushare 经 start_date 加深，5m/1m 夹保守上限）。
  crypto 不变。美股分钟可信度统计由「源默认浅窗」变为可用。
```

- [ ] **Step 4：更新 `docs/intraday-backtest.md`**

- 在链路A/end_date 相关章节登记：链路A `end_date 右边界`经核验已随 A股/美股扩展闭合（Step 1 结论），分钟取数右边界由 `end_date`（A股/美股源 API）与 `limit`（crypto）+ 引擎 `[:eval_days]` 共同受控，本特性不改链路A 代码。
- 在「A股/美股分钟数据深度」小节登记 C2 行为变更：自本特性起，链路B 分钟回测非 crypto 历史窗口通过 `start_date` 显式锚定（美股夹 `_INTRADAY_MAX_DAYS["us"]`，A股 today-365/730，5m/1m 夹保守上限），解决此前源默认浅窗导致样本量不足的问题；**cn band 保守值待 tushare 在线核验校准**（见 spec §5.7）。

- [ ] **Step 5：更新 `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式，逐行 `- [类型] 描述`，不加 `###` 标题）**

```markdown
- [修复] 链路B 信号可信度回测消除右端截尾偏差：_eval 评估上界由 n-1 收紧为 n-horizon，仅统计有完整 horizon 前瞻的信号(日线+分钟，signal_stats 重跑后命中率注解微调)
- [改进] 链路B 分钟回测非 crypto 历史深度受控：_load_bars 按 market×interval 下传 start_date(美股夹 yfinance band、A股 tushare 加深、crypto 字节级不变)
- [文档] 登记链路A end_date 右边界已闭合(核验结论) + 链路B 窗口正确性行为变更(signal-credibility §5.2 / intraday-backtest)
- [测试] 补 _eval 右端截尾绝对边界回归 + _minute_fetch_days/_minute_fetch_start_date 单测 + _load_bars 抓参
```

- [ ] **Step 6：跑 docstring 改动的测试 + 文档语法快检**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_signal_backtest.py::test_evaluate_signal_outcomes_exact_dedup_count -v`
Expected: PASS（仅 docstring 改动，行为不变）。

- [ ] **Step 7：提交**

```bash
git add tests/test_signal_backtest.py docs/signal-credibility.md docs/intraday-backtest.md docs/CHANGELOG.md
git commit -m "docs(signals): 登记链路B 窗口正确性行为变更 + 链路A end_date 闭合核验 + 订正去重测试陈旧 docstring"
```

---

## 收尾门禁（全部 Task 完成后）

- [ ] **门禁：ci_gate 全绿**

Run: `cd /root/AI/WorkSpace/cursor/AI\ _Trading_System && ./scripts/ci_gate.sh`
Expected: flake8 clean + `pytest -m "not network"` 全绿（含承重 golden）。记录 passed 数相对 3766 基线的增量（新增 ~6 个测试）。承重 golden `test_golden_signal_equiv_oracle_multimarket` / `test_golden_baseline_unchanged` 必须转绿（B1 直接回归证据）。

- [ ] **cn band 在线核验（I3 前置，网络可达时执行；沙箱无网则登记未验证）**

若有外网：用 `-m network` 观测测试或一次性脚本核验 tushare `stk_mins` 对 `today-30`(1m)/`today-90`(5m)/`today-365`(15m)/`today-730`(1h) 的真实单次返回（行数 / 优雅子集 / 报错 / 错窗），据结果校准 `_INTRADAY_MAX_DAYS["cn"]` 并回写 spec §3.1/§3.6。无网（沙箱）则在交付说明登记「cn band 在线核验未执行，保守值成立、待有网补跑」。此项**不阻断**离线门禁（保守 band 由构造安全）。

---

## 交付说明清单（finishing 前据实填写）

- 改了什么 / 为什么 / 验证情况（ci_gate passed 数、承重 golden 转绿）/ 未验证项（cn band 在线核验是否执行）/ 风险点（日线 signal_stats 行为变更已文档化）/ 回滚方式（revert 三个 commit；signal_stats 重跑 `--signal-backtest` 回旧值）。
- 服务层真引擎集成测试盲性登记：`test_run_interval_5m_real_eval_offline` 等用平价 fixture，C1 计数收缩对其断言不可见（守卫由 Task 1 绝对计数 + golden 等价承担）。
