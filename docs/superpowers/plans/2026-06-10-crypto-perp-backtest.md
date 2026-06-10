# crypto 永续合约回测（资金费 + 做空，1x）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 perp 标的（`BASE/QUOTE:PERP`）回测纳入资金费成本与做空盈亏（固定 1x），全程 additive、零 DB schema 改动，`is_perp=False` 时现货/股票回测逐字不变。

**Architecture:** 三层 additive：`data_provider/crypto_derivatives.py` 新增纯抓取 `fetch_funding_rate_history`（OKX funding-rate-history，半开持有窗口、自窗口右界向后分页、fail-soft）；`src/core/backtest_engine.py` 新增 `infer_perp_position`（long/short/cash）并给 `evaluate_single` 加 `is_perp/funding_cost_pct` 两个默认参数（position 选择器统一三处出口 + short 的窗口末方向盈亏 + funding 折算）；`src/services/backtest_service.py` 检测 `is_perp_code` 并按真实持仓窗口算 `funding_cost_pct`（门控复用 `crypto_derivatives_enabled`、forward_bars 足量才抓）。做空用现有 `position_recommendation="short"`、盈亏折进现有 `simulated_return_pct`，不加任何列。

**Tech Stack:** Python 3、SQLAlchemy（既有 ORM）、pytest + unittest（既有测试栈）、OKX 公共 REST（免 key）。

设计与查证依据：`docs/superpowers/specs/2026-06-10-crypto-perp-backtest-design.md`（经多智能体审查收敛）。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `data_provider/crypto_derivatives.py` | 修改 | 新增 `OKX_FUNDING_HISTORY_URL`、`_FUNDING_HISTORY_MAX_PAGES`、`fetch_funding_rate_history`（纯抓取/聚合，不 import src.*、不碰 DB） |
| `src/core/backtest_engine.py` | 修改 | 新增 `infer_perp_position`；`evaluate_single` 加 `is_perp/funding_cost_pct`、position 选择器（三处出口）、short 盈亏分支（纯逻辑、DB 无关） |
| `src/services/backtest_service.py` | 修改 | `run_backtest` 内 perp 检测 + funding 门控/足量守卫；新增 `_compute_perp_funding_cost_pct`（真实持仓窗口换算）；透传 `is_perp/funding_cost_pct` |
| `src/storage.py` | 修改 | 两处枚举注释同步（`# long/cash/short`、追加 `window_end_short`），无列/类型/迁移改动 |
| `tests/test_crypto_funding_history.py` | 新增 | `fetch_funding_rate_history` 单测（窗口/分页/边界/降级/截断） |
| `tests/test_perp_backtest_engine.py` | 新增 | `infer_perp_position` + `evaluate_single` perp long/short/cash/error 出口 + 现货默认回归 |
| `tests/test_perp_backtest_service.py` | 新增 | `_compute_perp_funding_cost_pct` 窗口 spy + perp e2e（short 落库/funding 调整）+ 门控/足量守卫 |
| `tests/test_backtest_engine.py` / `tests/test_crypto_backtest.py` / `tests/test_backtest_summary.py` | **不改** | 既有 lock 套件须原样全绿，作为 `is_perp=False` 逐字不变首要回归证据 |
| `docs/crypto-guide.md` | 修改 | 回测节补 perp 小节 + 修正三处与本期矛盾旧表述（:33/:229/:244） |
| `docs/CHANGELOG.md` | 修改 | `[Unreleased]` 追加一行扁平条目 |

每个 Task 自成可提交单元；TDD：先写失败测试，跑红，最小实现，跑绿，提交。提交信息英文类型 + 中文描述，**不加** `Co-Authored-By`、不加工具前缀。

---

## Task 1: `fetch_funding_rate_history`（OKX 资金费历史，纯抓取）

**Files:**
- Modify: `data_provider/crypto_derivatives.py`（在文件末尾、现有 `fetch_perp_market_snapshot` 之后追加；常量加在文件顶部 `_LINEAR_QUOTES` 附近）
- Test: `tests/test_crypto_funding_history.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_crypto_funding_history.py`：

```python
# -*- coding: utf-8 -*-
"""fetch_funding_rate_history：半开窗口求和/边界、向后分页、降级、超页截断。"""
import data_provider.crypto_derivatives as cd


def _page_fake(settlements):
    """模拟 OKX：按 after=cursor 返回 fundingTime < cursor 的最多 100 条，最新在前。
    settlements 须按 ts 降序。返回 (fake, calls)。"""
    calls = []

    def fake(url, params=None, headers=None):
        after = int(params["after"])
        calls.append(after)
        recs = [s for s in settlements if s[0] < after][:100]
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "fundingTime": str(ts), "fundingRate": str(fr)}
            for ts, fr in recs
        ]}

    return fake, calls


def test_half_open_window_sum_and_boundaries(monkeypatch):
    # 窗口 [1000, 4000)：含 ts==1000，排除 ts==4000；窗口外 500 排除
    settlements = [(4000, 0.04), (3000, 0.03), (2000, 0.02), (1000, 0.01), (500, 0.005)]
    fake, _calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000)
    assert abs(sum(rates) - (0.03 + 0.02 + 0.01)) < 1e-9
    assert len(rates) == 3


def test_backward_pagination_concatenates(monkeypatch):
    step = 28_800_000  # 8h ms
    end_ms = 100 * step
    # 130 个结算，全部 < end_ms，降序；窗口含前 120 个
    settlements = [(end_ms - (i + 1) * step, 0.0001) for i in range(130)]
    start_ms = end_ms - 120 * step  # ts >= start_ms 命中 i=0..119（半开含 start）
    fake, calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", start_ms, end_ms)
    assert len(rates) == 120
    assert abs(sum(rates) - 0.012) < 1e-9
    assert len(calls) >= 2  # 跨页发生


def test_non_linear_quote_short_circuits_no_call(monkeypatch):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(cd, "_http_get_json", spy)
    assert cd.fetch_funding_rate_history("BTC", "USD", 0, 1) == []
    assert called["n"] == 0


def test_exception_fails_soft_to_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("okx down")

    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []


def test_max_pages_truncation(monkeypatch):
    # 1300 个结算全部落窗口内 → 12 页 ×100 截断为 1200
    end_ms = 2000
    settlements = [(end_ms - (i + 1), 0.0001) for i in range(1300)]
    start_ms = end_ms - 1300 - 1
    fake, calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", start_ms, end_ms)
    assert len(rates) == 1200
    assert len(calls) == 12
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_crypto_funding_history.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'fetch_funding_rate_history'`）

- [ ] **Step 3: 最小实现**

在 `data_provider/crypto_derivatives.py` 顶部常量区（紧邻 `OKX_OI_URL` / `_LINEAR_QUOTES`）追加：

```python
OKX_FUNDING_HISTORY_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
# 首页即自窗口右界(after=end_ms)向后翻，故 12 页约束的是“窗口跨度”(~400 天)而非“现在→窗口”距离；
# 实际 eval 窗口远小于此，正常不会截断；极端超界返回已采集部分（偏低估，fail-soft）。
_FUNDING_HISTORY_MAX_PAGES = 12  # 100 结算/页 ≈ 33 天/页
```

在文件末尾（`fetch_perp_market_snapshot` 之后）追加：

```python
def fetch_funding_rate_history(base: str, quote: str, start_ms: int, end_ms: int) -> list:
    """OKX 永续 BASE-QUOTE-SWAP 在半开窗口 [start_ms, end_ms) 内的资金费率列表（fundingRate 小数）。
    自 after=end_ms 起向后分页（after=更早），按窗口过滤；非线性计价/参数非法/无数据 → []。fail-soft。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return []
    inst = f"{base}-{quote}-SWAP"
    rates: list = []
    cursor = int(end_ms)  # OKX after: 返回 fundingTime 早于该值的记录；自窗口右界起翻，预算用在窗口内
    for _ in range(_FUNDING_HISTORY_MAX_PAGES):
        params = {"instId": inst, "limit": "100", "after": str(cursor)}
        try:
            data = _http_get_json(OKX_FUNDING_HISTORY_URL, params)
        except Exception as e:
            logger.warning("[资金费历史] %s 抓取失败: %s", inst, e)
            break
        arr = data.get("data") if isinstance(data, dict) else None
        if not isinstance(arr, list) or not arr:
            break
        page_min_ts = None
        for item in arr:
            ts = _to_float(item.get("fundingTime"))
            fr = _to_float(item.get("fundingRate"))
            if ts is None:
                continue
            page_min_ts = ts if page_min_ts is None else min(page_min_ts, ts)
            if fr is not None and start_ms <= ts < end_ms:  # 半开 [start, end)：含 start、排除 end 边界结算
                rates.append(fr)
        if page_min_ts is None or page_min_ts <= start_ms or len(arr) < 100:
            break
        cursor = int(page_min_ts)
    return rates
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_crypto_funding_history.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_funding_history.py
git commit -m "feat: 新增 OKX 资金费历史抓取 fetch_funding_rate_history（半开窗口/向后分页/fail-soft）"
```

---

## Task 2: `infer_perp_position`（引擎，long/short/cash）

**Files:**
- Modify: `src/core/backtest_engine.py`（在 `infer_position_recommendation` 之后、`evaluate_single` 之前新增 classmethod）
- Test: `tests/test_perp_backtest_engine.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_perp_backtest_engine.py`：

```python
# -*- coding: utf-8 -*-
"""perp 回测引擎：infer_perp_position + evaluate_single 的 is_perp/funding 分支。
现货默认路径回归见既有 lock 套件（tests/test_backtest_engine.py 等，不在此重复）。"""
import unittest
from dataclasses import dataclass
from datetime import date, timedelta

from src.core.backtest_engine import BacktestEngine, EvaluationConfig


@dataclass
class Bar:
    date: date
    high: float
    low: float
    close: float


def _bars(start: date, closes, highs=None, lows=None):
    highs = highs or closes
    lows = lows or closes
    return [Bar(date=start + timedelta(days=i + 1), high=highs[i], low=lows[i], close=closes[i])
            for i in range(len(closes))]


class InferPerpPositionTestCase(unittest.TestCase):
    def test_bearish_maps_to_short(self):
        self.assertEqual(BacktestEngine.infer_perp_position("卖出"), "short")
        self.assertEqual(BacktestEngine.infer_perp_position("strong sell"), "short")

    def test_bullish_and_hold_map_to_long(self):
        self.assertEqual(BacktestEngine.infer_perp_position("买入"), "long")
        self.assertEqual(BacktestEngine.infer_perp_position("持有"), "long")

    def test_wait_and_default_map_to_cash(self):
        self.assertEqual(BacktestEngine.infer_perp_position("观望"), "cash")
        self.assertEqual(BacktestEngine.infer_perp_position("some gibberish"), "cash")
        for advice in [None, "", "   "]:
            self.assertEqual(BacktestEngine.infer_perp_position(advice), "cash")

    def test_only_bearish_diverges_from_long_only_inference(self):
        # 除 bearish 外，与 infer_position_recommendation 完全一致
        for advice in ["买入", "持有", "观望", "震荡观望", "先观望再买入", "some gibberish", "", None]:
            perp = BacktestEngine.infer_perp_position(advice)
            spot = BacktestEngine.infer_position_recommendation(advice)
            self.assertEqual(perp, spot, f"non-bearish advice should match: {advice!r}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_perp_backtest_engine.py::InferPerpPositionTestCase -q`
Expected: FAIL（`AttributeError: ... has no attribute 'infer_perp_position'`）

- [ ] **Step 3: 最小实现**

在 `src/core/backtest_engine.py` 的 `infer_position_recommendation`（结束于 `:154` 的 `return "cash"`）之后、`evaluate_single` 之前插入：

```python
    @classmethod
    def infer_perp_position(cls, operation_advice: Optional[str]) -> str:
        """Infer perp position: long/short/cash. bearish -> short; otherwise mirrors long-only inference."""
        text = cls._normalize_text(operation_advice)
        if cls._matches_intent(text, cls._BEARISH_KEYWORDS):
            return "short"
        wait_pos = cls._first_intent_position(text, cls._WAIT_KEYWORDS)
        if wait_pos is not None:
            bullish_pos = cls._first_intent_position(text, cls._BULLISH_KEYWORDS)
            hold_pos = cls._first_intent_position(text, cls._HOLD_KEYWORDS)
            if (bullish_pos is None or wait_pos < bullish_pos) and (
                hold_pos is None or wait_pos < hold_pos
            ):
                return "cash"
        if cls._matches_intent(text, cls._BULLISH_KEYWORDS) or cls._matches_intent(text, cls._HOLD_KEYWORDS):
            return "long"
        if cls._matches_intent(text, cls._WAIT_KEYWORDS):
            return "cash"
        return "cash"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_perp_backtest_engine.py::InferPerpPositionTestCase -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/core/backtest_engine.py tests/test_perp_backtest_engine.py
git commit -m "feat: 引擎新增 infer_perp_position（bearish→short，其余镜像多头推断）"
```

---

## Task 3: `evaluate_single` 加 `is_perp/funding_cost_pct`（选择器统一三处出口 + short 盈亏）

**Files:**
- Modify: `src/core/backtest_engine.py`（`evaluate_single` 签名 + 函数顶部选择器 + 三处 `infer_position_recommendation` 调用点 :180/:193/:213 + entry/exit 块 :237-244）
- Test: `tests/test_perp_backtest_engine.py`（追加用例）

- [ ] **Step 1: 写失败测试**

在 `tests/test_perp_backtest_engine.py` 末尾（`if __name__` 之前）追加：

```python
class EvaluateSinglePerpTestCase(unittest.TestCase):
    def _cfg(self):
        return EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)

    def test_perp_short_profits_on_drop_with_funding(self):
        # 看空、价格下跌：做空盈利 = (start-end)/start*100 + funding；持有至窗口末
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=110, take_profit=90, config=self._cfg(),
            is_perp=True, funding_cost_pct=0.3,
        )
        self.assertEqual(res["position_recommendation"], "short")
        self.assertEqual(res["direction_expected"], "down")
        self.assertEqual(res["outcome"], "win")  # 跌 5% > band
        self.assertAlmostEqual(res["simulated_entry_price"], 100)
        self.assertAlmostEqual(res["simulated_exit_price"], 95)  # 窗口末收盘
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], (100 - 95) / 100 * 100 + 0.3)  # 5.3
        self.assertIsNone(res["hit_stop_loss"])
        self.assertIsNone(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "not_applicable")

    def test_perp_long_subtracts_funding_and_matches_spot_when_zero(self):
        bars = _bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[103, 105, 106])
        kwargs = dict(operation_advice="买入", analysis_date=date(2024, 1, 1), start_price=100,
                      forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg())
        perp = BacktestEngine.evaluate_single(is_perp=True, funding_cost_pct=0.5, **kwargs)
        spot = BacktestEngine.evaluate_single(**kwargs)  # is_perp=False 默认
        # 多头 TP 命中路径与现货一致；perp 仅多扣 funding
        self.assertEqual(perp["position_recommendation"], "long")
        self.assertEqual(perp["simulated_exit_reason"], "take_profit")
        self.assertAlmostEqual(perp["simulated_return_pct"], spot["simulated_return_pct"] - 0.5)
        # funding=0 时数值等同现货
        perp0 = BacktestEngine.evaluate_single(is_perp=True, funding_cost_pct=0.0, **kwargs)
        self.assertAlmostEqual(perp0["simulated_return_pct"], spot["simulated_return_pct"])

    def test_perp_wait_is_cash_zero_return(self):
        bars = _bars(date(2024, 1, 1), [101, 102, 103], highs=[102, 103, 104], lows=[100, 101, 102])
        res = BacktestEngine.evaluate_single(
            operation_advice="观望", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg(),
            is_perp=True, funding_cost_pct=0.9,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["simulated_return_pct"], 0.0)
        self.assertIsNone(res["simulated_entry_price"])

    def test_perp_error_and_insufficient_keep_short_label(self):
        cfg = self._cfg()
        # error：start_price<=0
        err = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=0,
            forward_bars=[], stop_loss=None, take_profit=None, config=cfg, is_perp=True,
        )
        self.assertEqual(err["eval_status"], "error")
        self.assertEqual(err["position_recommendation"], "short")
        # insufficient：bars 不足
        short_bars = _bars(date(2024, 1, 1), [98])
        insf = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=short_bars, stop_loss=None, take_profit=None, config=cfg, is_perp=True,
        )
        self.assertEqual(insf["eval_status"], "insufficient_data")
        self.assertEqual(insf["position_recommendation"], "short")

    def test_spot_default_is_unchanged_for_bearish(self):
        # is_perp=False（默认）：看空仍 cash、return 0.0（逐字不变）
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg(),
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["simulated_return_pct"], 0.0)
        self.assertEqual(res["first_hit"], "not_applicable")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_perp_backtest_engine.py::EvaluateSinglePerpTestCase -q`
Expected: FAIL（`TypeError: evaluate_single() got an unexpected keyword argument 'is_perp'`）

- [ ] **Step 3: 最小实现**

在 `src/core/backtest_engine.py::evaluate_single`：

(3a) 签名追加两个默认参数（在 `config: EvaluationConfig,` 之后）：

```python
        config: EvaluationConfig,
        is_perp: bool = False,
        funding_cost_pct: float = 0.0,
    ) -> Dict[str, Any]:
```

(3b) 在函数体最顶部（docstring 之后、`if start_price is None or start_price <= 0:` 之前）定义选择器：

```python
        infer_position = cls.infer_perp_position if is_perp else cls.infer_position_recommendation
```

(3c) 三处出口的 `cls.infer_position_recommendation(operation_advice)` 改为 `infer_position(operation_advice)`：
- error 路径（原 `:180`）：`"position_recommendation": infer_position(operation_advice),`
- insufficient_data 路径（原 `:193`）：`"position_recommendation": infer_position(operation_advice),`
- completed 路径（原 `:213`）：`position = infer_position(operation_advice)`

(3d) 替换 entry/exit/return 块（原 `:237-244`，即 `simulated_entry_price = start_price if position == "long" else None` 起到 `simulated_return_pct = (simulated_exit_price - start_price) / start_price * 100` 止）为：

```python
        funding = float(funding_cost_pct or 0.0) if is_perp else 0.0
        simulated_return_pct: Optional[float]
        if position == "long":
            simulated_entry_price = start_price
            if simulated_exit_price is None:
                simulated_return_pct = None
            else:
                simulated_return_pct = (simulated_exit_price - start_price) / start_price * 100
                if is_perp:
                    simulated_return_pct -= funding            # 多头付资金费(正费率)
        elif position == "short":                              # 仅 is_perp 可达（现货推断永不返回 short）
            simulated_entry_price = start_price
            simulated_exit_price = end_close                   # 做空持有至窗口末
            simulated_exit_reason = "window_end_short"
            if end_close is None:
                simulated_return_pct = None
            else:
                simulated_return_pct = (start_price - end_close) / start_price * 100 + funding  # 跌则盈 + 收资金费
        else:  # cash
            simulated_entry_price = None
            simulated_return_pct = 0.0
```

> 注：`simulated_exit_price` / `simulated_exit_reason` 已在上方由 `_evaluate_targets` 解包赋值；long/cash 沿用其值，short 在此覆写为窗口末。`first_hit`/`hit_*` 对 short 维持 `_evaluate_targets` 的 `not_applicable`/`None`（做空不评估 TP/SL）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_perp_backtest_engine.py -q`
Expected: PASS（全部）

- [ ] **Step 5: 现货逐字回归——既有 lock 套件必须原样全绿**

Run: `python -m pytest tests/test_backtest_engine.py tests/test_crypto_backtest.py tests/test_backtest_summary.py -q`
Expected: PASS（无 1 例失败、未改任何 fixture/断言）。若任一红，说明 `is_perp=False` 路径被破坏，必须修实现而非改测试。

- [ ] **Step 6: 提交**

```bash
git add src/core/backtest_engine.py tests/test_perp_backtest_engine.py
git commit -m "feat: evaluate_single 支持 perp（is_perp/funding_cost_pct，做空窗口末盈亏+资金费，三处出口统一）"
```

---

## Task 4: service perp 检测 + 真实持仓窗口资金费

**Files:**
- Modify: `src/services/backtest_service.py`（顶部 import 加 `timezone`；`run_backtest` 调 `evaluate_single` 前插入检测/门控；新增 `_compute_perp_funding_cost_pct`）
- Test: `tests/test_perp_backtest_service.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_perp_backtest_service.py`：

```python
# -*- coding: utf-8 -*-
"""perp 回测 service：资金费窗口换算 spy、perp e2e（short 落库/funding 调整）、门控/足量守卫。"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from src.config import Config
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager, StockDaily

PERP_CODE = "BTC/USDT:PERP"


class PerpBacktestServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "perp_bt.db")
        os.environ.pop("CRYPTO_DERIVATIVES_ENABLED", None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        os.environ.pop("CRYPTO_DERIVATIVES_ENABLED", None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed(self, advice="卖出"):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id="pq1", code=PERP_CODE, name="BTC perp", report_type="simple",
                sentiment_score=40, operation_advice=advice, trend_prediction="看空",
                analysis_summary="perp short test", stop_loss=110000.0, take_profit=90000.0,
                created_at=datetime(2024, 1, 1),
                context_snapshot=json.dumps({"enhanced_context": {"date": "2024-01-01"}}),
            ))
            # 起始 bar + 3 前向 bar，价格下跌（做空盈利）
            session.add(StockDaily(code=PERP_CODE, date=date(2024, 1, 1), open=100000.0, high=100500.0, low=99500.0, close=100000.0))
            session.add_all([
                StockDaily(code=PERP_CODE, date=date(2024, 1, 2), open=100000.0, high=100000.0, low=97000.0, close=98000.0),
                StockDaily(code=PERP_CODE, date=date(2024, 1, 3), open=98000.0, high=98000.0, low=95000.0, close=96000.0),
                StockDaily(code=PERP_CODE, date=date(2024, 1, 4), open=96000.0, high=96000.0, low=94000.0, close=95000.0),
            ])
            session.commit()

    def _result(self):
        with self.db.get_session() as session:
            return session.query(BacktestResult).filter(BacktestResult.code == PERP_CODE).one()

    def test_funding_window_args_and_pct(self):
        self._seed()
        captured = {}

        def spy(base, quote, start_ms, end_ms):
            captured.update(base=base, quote=quote, start_ms=start_ms, end_ms=end_ms)
            return [0.0001, 0.0002]  # sum=0.0003 → *100 = 0.03

        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history", side_effect=spy):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)

        # 入场 = start_date+1 00:00 UTC；出场 = start_date+(N+1) 00:00 UTC
        entry = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        exit_ = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(captured["base"], "BTC")
        self.assertEqual(captured["quote"], "USDT")
        self.assertEqual(captured["start_ms"], entry)
        self.assertEqual(captured["end_ms"], exit_)

    def test_perp_short_persisted_with_funding(self):
        self._seed()
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history", return_value=[0.0003]):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        r = self._result()
        self.assertEqual(r.eval_status, "completed")
        self.assertEqual(r.position_recommendation, "short")
        self.assertEqual(r.simulated_exit_reason, "window_end_short")
        # (100000-95000)/100000*100 + 0.0003*100 = 5.03
        self.assertAlmostEqual(r.simulated_return_pct, 5.03, places=4)
        self.assertEqual(r.outcome, "win")

    def test_gating_off_skips_fetch_but_keeps_short_pnl(self):
        os.environ["CRYPTO_DERIVATIVES_ENABLED"] = "false"
        Config._instance = None
        self._seed()
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history") as m:
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        m.assert_not_called()
        r = self._result()
        self.assertEqual(r.position_recommendation, "short")
        self.assertAlmostEqual(r.simulated_return_pct, 5.0, places=4)  # 无 funding

    def test_insufficient_bars_skips_fetch(self):
        self._seed()
        # 要求 5 bar 但只有 3 前向 bar → insufficient，且不应发起 funding 抓取
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history") as m, \
             patch("data_provider.base.DataFetcherManager.get_daily_data", return_value=(__import__("pandas").DataFrame(), "")):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=5, min_age_days=0, limit=10)
        m.assert_not_called()
        self.assertEqual(self._result().eval_status, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_perp_backtest_service.py -q`
Expected: FAIL（`fetch_funding_rate_history` 未被调用 / `position_recommendation` 不是 `"short"`，因 service 尚未传 `is_perp`）

- [ ] **Step 3: 最小实现**

(3a) 顶部 import 增加 `timezone`：

```python
from datetime import date, datetime, timedelta, timezone
```

(3b) 在 `run_backtest` 的 `for analysis in candidates:` 循环**之前**（如紧接 `results_to_save: List[BacktestResult] = []` 之后）一次性惰性导入：

```python
        from data_provider.base import is_perp_code
```

(3c) 在 `evaluation = BacktestEngine.evaluate_single(` 调用（原 `:131`）**之前**插入检测与门控，并给该调用追加两个实参：

```python
                is_perp = is_perp_code(analysis.code)
                funding_cost_pct = 0.0
                # 仅在 forward_bars 足量（不会落 insufficient_data）时才发起 OKX 资金费抓取，避免对将被丢弃的行做无谓网络 I/O
                if is_perp and len(forward_bars) >= int(eval_window_days) and getattr(config, "crypto_derivatives_enabled", True):
                    funding_cost_pct = self._compute_perp_funding_cost_pct(
                        code=analysis.code,
                        start_date=start_daily.date,
                        eval_window_days=int(eval_window_days),
                    )

                evaluation = BacktestEngine.evaluate_single(
                    operation_advice=analysis.operation_advice,
                    analysis_date=start_daily.date,
                    start_price=float(start_daily.close),
                    forward_bars=forward_bars,
                    stop_loss=analysis.stop_loss,
                    take_profit=analysis.take_profit,
                    config=eval_config,
                    is_perp=is_perp,
                    funding_cost_pct=funding_cost_pct,
                )
```

(3d) 新增私有方法（置于 `run_backtest` 之后、`get_recent_evaluations` 之前等合适位置）：

```python
    def _compute_perp_funding_cost_pct(self, *, code: str, start_date: date, eval_window_days: int) -> float:
        """perp 标的真实持有窗口 [入场=start_date 收盘, 出场=末 bar 收盘) 的资金费成本(百分比, 多头视角)。
        窗口半开、≈ eval_window_days×3 个 8h 结算；非 perp/禁用/异常 → 0.0。
        注：窗口按日历日推算，假定 OKX 24/7 日线无内部缺口（缺口期口径退化为近似）。"""
        try:
            from data_provider.base import parse_perp_code
            import data_provider.crypto_derivatives as cd
            base, quote = parse_perp_code(code)
            # 持有区间对齐真实持仓：OKX 1D candle 收盘对齐次日 00:00 UTC。
            midnight = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
            start_dt = midnight + timedelta(days=1)                    # 入场 = start_date 收盘
            end_dt = midnight + timedelta(days=eval_window_days + 1)   # 出场 = 末 bar 收盘
            start_ms = int(start_dt.timestamp() * 1000)
            end_ms = int(end_dt.timestamp() * 1000)
            rates = cd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
            return sum(rates) * 100.0
        except Exception as exc:
            logger.warning(f"perp 资金费抓取失败({code}): {exc}")
            return 0.0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_perp_backtest_service.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/services/backtest_service.py tests/test_perp_backtest_service.py
git commit -m "feat: 回测 service 接入 perp 资金费（真实持仓窗口换算，门控复用 crypto_derivatives_enabled，足量才抓）"
```

---

## Task 5: storage 枚举注释同步（无迁移）

**Files:**
- Modify: `src/storage.py`（:307、:333 两处行内注释）

- [ ] **Step 1: 改注释**

`src/storage.py:307`：

```python
    position_recommendation = Column(String(8))  # long/cash/short
```

`src/storage.py:333`：

```python
    simulated_exit_reason = Column(String(24))  # stop_loss/take_profit/window_end/window_end_short/cash/ambiguous_stop_loss
```

> 仅注释、无列/类型/迁移改动。`"short"`(5) ≤ String(8)，`"window_end_short"`(16) ≤ String(24)。

- [ ] **Step 2: 编译校验**

Run: `python -m py_compile src/storage.py`
Expected: 无输出（成功）

- [ ] **Step 3: 提交**

```bash
git add src/storage.py
git commit -m "docs: 同步 BacktestResult 枚举注释纳入 short/window_end_short（无迁移）"
```

---

## Task 6: 文档（crypto-guide 小节 + 反漂移修正 + CHANGELOG）

**Files:**
- Modify: `docs/crypto-guide.md`（回测节补 perp 小节；修正 :33、:229、:244 与本期矛盾旧表述）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 追加一行）

- [ ] **Step 1: crypto-guide 回测节补小节**

在 `docs/crypto-guide.md` 的「回测（Backtest）」节末尾追加：

```markdown
### 永续回测（资金费 + 做空，1x）

perp 标的（`BASE/QUOTE:PERP`）回测在现货流程之上叠加两项永续机制（固定 1x，无杠杆/强平）：

- **做空盈亏**：看空建议记 `position="short"`，持有至窗口末，`simulated_return_pct = (入场−出场)/入场×100 + 资金费`（跌则盈）。做空不评估 TP/SL（analysis 价位为多头框架，反向套用会虚构精度）。
- **资金费成本**：按真实持有窗口（入场=起始 bar 收盘 → 出场=末 bar 收盘，≈ `eval_window_days×3` 个 8h 结算）对 OKX `funding-rate-history` 求和；多头 `−资金费`、空头 `+资金费`（正费率＝多头付空头）。
- **门控/降级**：资金费抓取复用 `CRYPTO_DERIVATIVES_ENABLED`（默认开），抓取失败或关闭时资金费记 0、方向盈亏照常；现货/股票回测不受影响。
```

- [ ] **Step 2: 反漂移修正三处旧表述**

- `docs/crypto-guide.md:33`：删除/改写 “暂不含 perp 回测”（perp 回测已由本期支持）。
- `docs/crypto-guide.md:229`：从「后续子项目（未做）」移除 “perp klines/回测”（klines 属 C+D、回测属本期，均已落地）。
- `docs/crypto-guide.md:244`：「合约 / 永续 / 杠杆（当前仅现货）」精修为 “perp 标的分析与回测已支持；杠杆仍不做”。

- [ ] **Step 3: CHANGELOG 追加（扁平、置于 `[Unreleased]` 现有条目下，不新增 `###` 标题）**

```markdown
- [新功能] crypto 永续合约回测纳入资金费成本与做空盈亏（1x，additive，零 schema，复用 crypto_derivatives_enabled；现货/股票回测不变）
```

- [ ] **Step 4: 核对引用一致**

Run: `grep -n "perp 回测\|暂不含 perp\|perp klines/回测\|当前仅现货" docs/crypto-guide.md`
Expected: 不再出现否定 perp 回测的旧表述。

- [ ] **Step 5: 提交**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 补 perp 永续回测说明并修正 crypto-guide 与本期矛盾的旧表述"
```

---

## Final Verification

- [ ] **Step 1: 全量后端 gate**

Run: `./scripts/ci_gate.sh`
Expected: 全绿（含既有 lock 套件 + 新增 perp 套件）；记录通过用例数。

- [ ] **Step 2: 改动文件编译**

Run: `python -m py_compile data_provider/crypto_derivatives.py src/core/backtest_engine.py src/services/backtest_service.py src/storage.py`
Expected: 无输出。

- [ ] **Step 3: 收尾**

REQUIRED SUB-SKILL: Use superpowers:finishing-a-development-branch。

---

## Self-Review

**1. Spec coverage（逐节核对 spec → 任务）：**
- §2.1 `fetch_funding_rate_history`（半开窗口/分页/降级/截断）→ Task 1 ✅
- §2.2 `infer_perp_position` → Task 2 ✅
- §2.3 `evaluate_single`（is_perp/funding、选择器三处出口、short 盈亏、long −funding、cash 0）→ Task 3 ✅
- §2.4 service 检测/门控/`_compute_perp_funding_cost_pct`（真实持仓窗口、足量守卫）→ Task 4 ✅
- §2.5 summary 零改动（position-agnostic 自动纳入 short）→ 无需改 `compute_summary`；由 Task 3/4 e2e 的 `outcome="win"`/return 间接覆盖；既有 summary lock 套件回归（Task 3 Step 5）✅
- §2.6 配置零新增 + 两处枚举注释 → Task 5 ✅
- §5 测试矩阵 7+ 行 → Task 1/3/4 测试逐条对应（含 error/insufficient 出口、funding 窗口 spy、门控 off、足量守卫、现货 lock 回归）✅
- §6 文档（小节 + 反漂移 + CHANGELOG）→ Task 6 ✅

**2. Placeholder scan：** 无 TBD/TODO；每个代码步骤含完整可运行代码；命令均给出 expected。

**3. Type/契约一致性：**
- `infer_perp_position` 返回 `"long"|"short"|"cash"`（Task 2/3 一致）；选择器 `infer_position` 三处出口签名一致（均 `(operation_advice)`）。
- `fetch_funding_rate_history(base, quote, start_ms, end_ms) -> list`（Task 1 定义）与 Task 4 调用签名一致；`_compute_perp_funding_cost_pct` 返回 `float`、`sum(rates)*100`。
- `simulated_return_pct` 单位百分比；`funding_cost_pct = Σrate×100`（service）与引擎 `−/+funding` 单位一致。
- 新增持久值 `position="short"` ≤ String(8)、`"window_end_short"` ≤ String(24)，注释已同步（Task 5）。
- `is_perp=False` 默认 → 现货/股票逐字不变，由 Task 3 Step 5 既有 lock 套件原样全绿强约束。
