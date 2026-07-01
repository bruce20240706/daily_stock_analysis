# VPS interval 阈值覆盖机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 VPS（量价信号）引擎补一个 interval 维度的窗口阈值覆盖通道（`VPSConfig.for_market_interval` + 16 个 `VPS_<窗口>_<interval>` env 键，仅 env+docs），默认全不配 = 日线/分钟均字节级不变；顺带把放量突破 reason 文案「近 N 日高点」泛化为「近 N 根高点」。

**Architecture:** 在现有 `VPSConfig.for_market(market)`（含 crypto 旁路）之上叠一个新 classmethod `for_market_interval(market, interval)`，对受支持的分钟 interval 读 `VPS_<FIELD>_<INTERVAL>` env 覆盖 4 个 window 字段（`vol_ma_window/breakout_window/atr_period/swing_k`）；唯一接线点 `signal_backtest_service.py` 的分钟回测 config 选择切到新工厂。VPSConfig 字段集零改动（env 在方法内按需直读 + `dataclasses.replace`）。reason 字符串泛化为「根」（对日线/分钟都准确），不把 interval 注入引擎、不破坏「引擎 interval 无关」不变式。

**Tech Stack:** Python 3、frozen `@dataclass`、`parse_env_float`（`src/config.py`）、pytest + monkeypatch。验证 `./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`）。

## Global Constraints

> 每个任务的要求都隐含本节，值与格式从 spec 逐字抄录。

- **默认全不配 = 字节级复用现日线值**（稳定性优先；纯 opt-in）。日线 / 未配分钟 / `interval ∈ {"1d", None, 未知}` 均行为不变。
- **interval-only**（不叠 market 维度）；本期**仅** 4 个 window 字段：`vol_ma_window`、`breakout_window`、`atr_period`、`swing_k`。
- **下限（minimum）与 `from_env()` 现有同字段完全一致**：`vol_ma_window=5.0`、`breakout_window=2.0`、`atr_period=2.0`、`swing_k=1.0`。
- **优先级（自高到低）**：interval 覆盖（显式 set）> crypto 旁路（`for_market` 的 `crypto_*`）> 日线默认。
- **复用 canonical 分钟集合**：用 `src.core.intraday_backtest.is_intraday_interval`，**不**另立平行 `_INTRADAY_INTERVALS`（F2：在 `for_market_interval` 方法内**局部 import**，与现有 `import dataclasses` 局部 import 风格一致，零模块顶耦合）。
- **16 个 env 键仅 env + docs**：在 `VPSConfig` 方法内直读 + `.env.example` + `docs/volume-price-signals.md`，**不进 `config_registry`、不 Web 可调**（与现有全部 `VPS_*` 一致）。
- **`.env.example` 16 键必须仅以注释行出现**（`# VPS_..._5M=`），**严禁裸 `KEY=` 活动行**（裸行会 RED `tests/test_config_registry.py::TestEnvExampleWebSettingsCoverage::test_active_env_example_keys_are_registered_or_hidden_from_web_ui`）。
- **reason 仅泛化「日」→「根」**（`:856`、`:1418`、`:822` docstring）；**不动** `is_daily_approx=True` / 「日线近似」标记，**不动** `src/agent/tools/analysis_tools.py:451` 的 LLM pattern 字面量。
- commit message：英文类型前缀 + 中文体，**不加** `Co-Authored-By`，不加工具/agent 前缀。
- `docs/CHANGELOG.md` 的 `[Unreleased]` 用**扁平格式** `- [类型] 描述`，**禁止** `### 类目标题`。
- 行号会漂移：实现者按符号（函数/字符串）定位，勿盲信行号。

---

### Task 1: `VPSConfig.for_market_interval` 覆盖机制 + 纯 config 单测

**Files:**
- Modify: `src/services/volume_price_signals.py`（在 `_REQUIRED_COLUMNS` 后加模块常量 `_INTERVAL_OVERRIDE_FIELDS`；在 `VPSConfig.for_market` 后加 classmethod `for_market_interval`）
- Test: `tests/test_signal_backtest_config.py`（追加 for_market_interval 单测）

**Interfaces:**
- Consumes: 既有 `VPSConfig.for_market(market) -> VPSConfig`、`VPSConfig.from_env()`、`parse_env_float(raw, default, *, field_name, minimum) -> float`（`src/config.py`，对非数字/纯空白返回 `default`，并按 `minimum` 钳制）、`src.core.intraday_backtest.is_intraday_interval(interval) -> bool`（`interval in {"1m","5m","15m","1h"}`）。
- Produces:
  - 模块常量 `_INTERVAL_OVERRIDE_FIELDS: dict[str, float]`（字段名 → minimum 下限）。
  - `VPSConfig.for_market_interval(market: str | None, interval: str | None) -> VPSConfig`。

- [ ] **Step 1: 写失败测试（默认字节级 + 单字段覆盖 + crypto 组合 + 钳制 + 回落 + 隔离 + 1d/None 不变 + warmup 耦合）**

在 `tests/test_signal_backtest_config.py` 末尾追加（文件已 import `VPSConfig`；如未 import `pytest` 则补 `import pytest`）：

```python
from src.services.volume_price_signals import VPSConfig, _INTERVAL_OVERRIDE_FIELDS


@pytest.mark.parametrize("interval", ["1d", "1m", "5m", "15m", "1h"])
@pytest.mark.parametrize("market", ["cn", "crypto"])
def test_for_market_interval_default_matches_for_market(interval, market):
    # 无任何 VPS_*_<interval> env → 与 for_market 字段级一致（F3：用字段相等，非 identity）
    assert VPSConfig.for_market_interval(market, interval) == VPSConfig.for_market(market)


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1h"])
def test_for_market_interval_single_field_override(monkeypatch, interval):
    # F4：参数化跑全 4 个分钟 interval，catch 1h->1H 等后缀映射 bug
    monkeypatch.setenv(f"VPS_BREAKOUT_WINDOW_{interval.upper()}", "10")
    cfg = VPSConfig.for_market_interval("cn", interval)
    assert cfg.breakout_window == 10
    # 其余 window 字段保持日线默认
    assert cfg.vol_ma_window == 20
    assert cfg.atr_period == 14
    assert cfg.swing_k == 3
    # 乘数类不受影响
    assert cfg.breakout_rel_vol == 2.0


def test_for_market_interval_crypto_unset_keeps_crypto_window():
    expected = VPSConfig.from_env().crypto_breakout_window
    assert VPSConfig.for_market_interval("crypto", "5m").breakout_window == expected


def test_for_market_interval_override_beats_crypto(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "7")
    assert VPSConfig.for_market_interval("crypto", "5m").breakout_window == 7


def test_for_market_interval_clamps_to_minimum(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "1")  # 低于下限 2
    assert VPSConfig.for_market_interval("cn", "5m").breakout_window == 2


def test_for_market_interval_invalid_falls_back_cn(monkeypatch):
    monkeypatch.setenv("VPS_ATR_PERIOD_15M", "abc")
    assert VPSConfig.for_market_interval("cn", "15m").atr_period == 14


def test_for_market_interval_invalid_falls_back_to_crypto_base(monkeypatch):
    # F4：crypto 非法值变体——回落到 crypto base（21），而非日线 14
    monkeypatch.setenv("VPS_CRYPTO_ATR_PERIOD", "21")
    monkeypatch.setenv("VPS_ATR_PERIOD_5M", "abc")
    assert VPSConfig.for_market_interval("crypto", "5m").atr_period == 21


def test_for_market_interval_isolation_across_intervals(monkeypatch):
    monkeypatch.setenv("VPS_SWING_K_5M", "5")
    assert VPSConfig.for_market_interval("cn", "5m").swing_k == 5
    assert VPSConfig.for_market_interval("cn", "15m").swing_k == 3  # 5m 不泄漏到 15m


def test_for_market_interval_1d_and_none_unchanged(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "10")  # 不应影响 1d / None
    base = VPSConfig.for_market("cn")
    assert VPSConfig.for_market_interval("cn", "1d") == base
    assert VPSConfig.for_market_interval("cn", None) == base


def test_for_market_interval_warmup_gate_coupling(monkeypatch):
    # M7：warmup min_bars = max(vol_ma_window, atr_period, breakout_window)+1。
    # 仅调小 breakout_window 不缩短（vol_ma_window=20 主导）；三者同调才缩短。
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "5")
    cfg = VPSConfig.for_market_interval("cn", "5m")
    assert max(cfg.vol_ma_window, cfg.atr_period, cfg.breakout_window) == 20
    monkeypatch.setenv("VPS_VOL_MA_WINDOW_5M", "6")
    monkeypatch.setenv("VPS_ATR_PERIOD_5M", "4")
    cfg2 = VPSConfig.for_market_interval("cn", "5m")
    assert max(cfg2.vol_ma_window, cfg2.atr_period, cfg2.breakout_window) == 6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_config.py -k for_market_interval -v`
Expected: FAIL —— `ImportError: cannot import name '_INTERVAL_OVERRIDE_FIELDS'` 或 `AttributeError: type object 'VPSConfig' has no attribute 'for_market_interval'`。

- [ ] **Step 3: 加模块常量 `_INTERVAL_OVERRIDE_FIELDS`**

在 `src/services/volume_price_signals.py` 的 `_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")` 行之后插入：

```python
# for_market_interval 可按 interval 覆盖的窗口字段 → minimum 下限（与 from_env 一致）
_INTERVAL_OVERRIDE_FIELDS: dict[str, float] = {
    "vol_ma_window": 5.0,
    "breakout_window": 2.0,
    "atr_period": 2.0,
    "swing_k": 1.0,
}
```

- [ ] **Step 4: 加 classmethod `for_market_interval`**

在 `VPSConfig.for_market` 方法（以 `return base` 结尾）之后、`VPSConfig` 类定义结束处插入：

```python
    @classmethod
    def for_market_interval(cls, market: str | None, interval: str | None) -> "VPSConfig":
        """返回适合 (market, interval) 的 VPSConfig。

        在 for_market(market)（含 crypto 旁路）之上，对受支持的分钟 interval 叠加窗口阈值
        覆盖：读 VPS_<FIELD>_<INTERVAL 大写> env（仅 4 个 window 字段），set 的才覆盖。
        默认不配 / interval 为 '1d'/None/未知 → 原样返回 for_market(market)，日线与未配分钟
        均字节级不变。优先级：interval 覆盖 > crypto 旁路 > 日线默认。
        """
        from src.core.intraday_backtest import is_intraday_interval

        base = cls.for_market(market)
        if not interval or not is_intraday_interval(interval):
            return base
        suffix = interval.upper()
        overrides: dict = {}
        for field_name, floor in _INTERVAL_OVERRIDE_FIELDS.items():
            env_key = f"VPS_{field_name.upper()}_{suffix}"
            raw = os.getenv(env_key)
            if raw is None or not raw.strip():
                continue
            cur = getattr(base, field_name)
            overrides[field_name] = int(parse_env_float(
                raw, float(cur), field_name=env_key, minimum=floor))
        if not overrides:
            return base
        import dataclasses
        return dataclasses.replace(base, **overrides)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_config.py -k for_market_interval -v`
Expected: PASS（全部 for_market_interval 用例通过，含 5×2 参数化默认 + 4 参数化覆盖 + crypto/钳制/回落/隔离/1d-None/warmup）。

- [ ] **Step 6: flake8 改动文件**

Run: `.venv/bin/python -m flake8 src/services/volume_price_signals.py tests/test_signal_backtest_config.py`
Expected: 无输出（0 violations）。

- [ ] **Step 7: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_backtest_config.py
git commit -m "feat: VPSConfig.for_market_interval 提供 interval 维度窗口阈值覆盖（默认不配字节级不变）"
```

---

### Task 2: 接线分钟回测 config 选择到 `for_market_interval`

**Files:**
- Modify: `src/services/signal_backtest_service.py`（`SignalBacktestService.run` 内的 `cfg_m = VPSConfig.for_market(market)` 一行）
- Test: `tests/test_signal_backtest_service.py`（追加 interval 版透传测试）

**Interfaces:**
- Consumes: `VPSConfig.for_market_interval(market, interval)`（Task 1）；既有 `SignalBacktestService.run(*, codes=None, horizon=None, interval="1d")`；测试 helper `_minute_df(n=120, base=100.0)`（已存在于 `tests/test_signal_backtest_service.py`，返回含 `datetime` 列、`n` 行的分钟 DataFrame）。
- Produces: 分钟回测路径用 `for_market_interval(market, interval)` 选 config（日线 interval="1d" 时 `for_market_interval` 内部 early-return == 原 `for_market`，行为不变）。

- [ ] **Step 1: 写失败测试（interval 覆盖值确实抵达引擎）**

在 `tests/test_signal_backtest_service.py` 末尾追加（文件已 import `pandas as pd`、`pytest`、`from src.services import signal_backtest_service as sbs`、`_minute_df`；`patch` 来自既有 `from unittest.mock import patch`，若无则补）：

```python
def test_run_interval_5m_passthrough_uses_for_market_interval(monkeypatch):
    """链路B 分钟回测必须用 for_market_interval(market, interval) 选 config：
    设 VPS_BREAKOUT_WINDOW_5M=9 时，evaluate_* 收到的 config.breakout_window 必须 == 9。
    若回退为 for_market(market)（无 interval 维度），该断言失败。
    I3：patch _load_bars 返回 >= _MIN_BARS(50) 行的分钟 df，确保触达 :157/:158 捕获点。
    """
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "9")
    captured: list = []

    def spy_eval_sig(df, *, market, horizon, config=None, **kw):
        captured.append(("sig", market, config))
        return []

    def spy_eval_base(df, *, market, horizon, config=None, **kw):
        return []

    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes",
        return_value=["600519"],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ), patch(
        "src.services.signal_backtest_service.get_market_for_stock",
        return_value="cn",
    ), patch.object(
        sbs.SignalBacktestService, "_load_bars", return_value=_minute_df(120),
    ), patch(
        "src.services.signal_backtest_service.evaluate_signal_outcomes",
        side_effect=spy_eval_sig,
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        side_effect=spy_eval_base,
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        Repo.return_value.save_batch.return_value = 0
        sbs.SignalBacktestService().run(horizon=10, interval="5m")

    cn_sig = next(c for c in captured if c[0] == "sig" and c[1] == "cn")
    assert cn_sig[2] is not None
    assert cn_sig[2].breakout_window == 9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_service.py::test_run_interval_5m_passthrough_uses_for_market_interval -v`
Expected: FAIL —— `assert 20 == 9`（当前 `:157` 用 `for_market("cn")`，breakout_window=20，未应用 `VPS_BREAKOUT_WINDOW_5M`）。

- [ ] **Step 3: 改接线点**

在 `src/services/signal_backtest_service.py` 的 `SignalBacktestService.run` 内（`for code in codes:` 循环里，`evaluate_signal_outcomes` 调用之前）把：

```python
                cfg_m = VPSConfig.for_market(market)
```

改为：

```python
                cfg_m = VPSConfig.for_market_interval(market, interval)
```

（`interval` 是 `run(*, interval="1d")` 形参，循环作用域内可用。）

- [ ] **Step 4: 跑新测试 + 既有 interval/passthrough 回归**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_service.py -v`
Expected: PASS（新测试通过；既有 `test_a5_config_passthrough_uses_for_market_per_code`（日线，interval 默认 "1d" → early-return 等价 for_market）、`test_run_interval_5m_uses_intraday_and_tags`、`test_run_interval_1d_uses_daily_path` 等全部不回归）。

- [ ] **Step 5: flake8 改动文件**

Run: `.venv/bin/python -m flake8 src/services/signal_backtest_service.py tests/test_signal_backtest_service.py`
Expected: 无输出。

- [ ] **Step 6: Commit**

```bash
git add src/services/signal_backtest_service.py tests/test_signal_backtest_service.py
git commit -m "feat: 链路B 分钟回测改用 for_market_interval 选 config（日线 early-return 等价不变）"
```

---

### Task 3: 放量突破 reason 文案泛化「日」→「根」

**Files:**
- Modify: `src/services/volume_price_signals.py`（`_detect_breakouts` 主路径 reason `:856`、向量化孪生 `_detect_breakouts_rows` reason `:1418`、`_detect_breakouts` docstring `:822`）
- Test: `tests/test_volume_price_signals.py`（追加 reason 文案测试）

**Interfaces:**
- Consumes: 既有 helper `_make_df(rows, start="2024-01-01")`、`_bar(open_, high, low, close, volume)`（均在 `tests/test_volume_price_signals.py`）；引擎 `compute_volume_price_signals(df, *, config=None)`、`_normalize(df, cfg) -> (norm, _)`、`_compute_primitives(norm, cfg)`、`_detect_breakouts_rows(prim, cfg) -> list[tuple[int, VPSignal]]`。
- Produces: 两条 reason 字符串 + 一处 docstring 由「日」改「根」。无行为/信号集变化。

- [ ] **Step 1: 写失败测试（主路径 + 向量化孪生 reason 均为「根」）**

在 `tests/test_volume_price_signals.py` 末尾追加（文件顶已 import `compute_volume_price_signals`、`VPSConfig`、`_make_df`/`_bar` 为本地函数）：

```python
def test_breakout_reason_uses_bars_not_days():
    # 主路径(:856)：文案泛化为「根」，不再硬编码「日」
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    breakout = _bar(100, 110.0, 99.0, 100.0, 5000)
    df = _make_df(pad + [breakout])
    res = compute_volume_price_signals(df, config=VPSConfig(breakout_window=20, breakout_rel_vol=2.0))
    bks = [m for m in res.markers if m.signal_type == "volume_breakout"]
    assert len(bks) == 1
    assert "根高点" in bks[0].reason
    assert "日高点" not in bks[0].reason


def test_breakout_rows_reason_uses_bars_not_days():
    # 向量化孪生(:1418)：同样泛化（虽 compute_signals_for_all_bars 丢弃 reason，仍保持一致）
    from src.services.volume_price_signals import (
        _normalize, _compute_primitives, _detect_breakouts_rows)
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    breakout = _bar(100, 110.0, 99.0, 100.0, 5000)
    df = _make_df(pad + [breakout])
    cfg = VPSConfig(breakout_window=20, breakout_rel_vol=2.0)
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    bks = [s for _, s in _detect_breakouts_rows(prim, cfg) if s.signal_type == "volume_breakout"]
    assert len(bks) == 1
    assert "根高点" in bks[0].reason
    assert "日高点" not in bks[0].reason
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_volume_price_signals.py -k "breakout_reason or breakout_rows_reason" -v`
Expected: FAIL —— `assert '根高点' in '放量突破近20日高点（不含当日）[...]'`（当前为「日高点」）。

- [ ] **Step 3: 翻转前再放宽 grep（F5，确认无子串断言残留）**

Run: `grep -rn "20日\|日（不含\|日高" src/ tests/ apps/dsa-web/src 2>/dev/null`
Expected: 命中仅为 (a) `volume_price_signals.py` 待改的 `:856/:1418/:822`；(b) `src/agent/tools/analysis_tools.py:451` 的 LLM pattern 字面量（**不动**）；(c) `apps/dsa-web` 的 `*.signals.test.tsx`/`stocks.signals.test.ts` 独立 `vi.mock` fixtures（自设自读、与引擎输出字面不同，**不动**，无需 web-gate）。确认无 Python 后端测试按子串断言引擎 reason 后再继续。

- [ ] **Step 4: 改两条 reason + docstring**

`_detect_breakouts`（主路径，约 `:856`）：

```python
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）[量能形态:{vol_pattern}]",
```
改为：
```python
                reason=f"放量突破近{config.breakout_window}根高点（不含当日）[量能形态:{vol_pattern}]",
```

`_detect_breakouts_rows`（向量化孪生，约 `:1418`）：

```python
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）[量能形态:{vp}]",
```
改为：
```python
                reason=f"放量突破近{config.breakout_window}根高点（不含当日）[量能形态:{vp}]",
```

`_detect_breakouts` docstring（约 `:822`）：

```python
    """放量突破检测：close >= 过去 N 日 high 最大值（shift(1) 不含当日）且 rel_vol >= 阈值。
```
改为：
```python
    """放量突破检测：close >= 过去 N 根 high 最大值（shift(1) 不含当日）且 rel_vol >= 阈值。
```

- [ ] **Step 5: 跑新测试 + 既有突破测试回归**

Run: `.venv/bin/python -m pytest tests/test_volume_price_signals.py -k "breakout" -v`
Expected: PASS（新两条通过；既有 `test_breakout_excludes_current_day` / `test_breakout_requires_rel_vol_threshold` 等不回归——它们不按 reason 子串断言）。

- [ ] **Step 6: flake8 改动文件**

Run: `.venv/bin/python -m flake8 src/services/volume_price_signals.py tests/test_volume_price_signals.py`
Expected: 无输出。

- [ ] **Step 7: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py
git commit -m "fix: 放量突破 reason 文案泛化为'近N根高点'，分钟/日线均准确"
```

---

### Task 4: `.env.example` + 文档 + 防漂移/仅注释测试

**Files:**
- Modify: `.env.example`（M1 VPS 段，约 `:869`，追加 16 个**注释**键）
- Modify: `docs/volume-price-signals.md`（§7 阈值表附近加 interval 覆盖小节）
- Modify: `docs/signal-credibility.md`（§11.5 / §12 更新 best-effort 措辞）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平一行）
- Test: `tests/test_signal_backtest_config.py`（追加防漂移 + 仅注释两测）

**Interfaces:**
- Consumes: `src.core.intraday_backtest.INTRADAY_INTERVAL_MINUTES`（dict，keys = `{"1m","5m","15m","1h"}`）、`src.services.volume_price_signals._INTERVAL_OVERRIDE_FIELDS`（Task 1）；`pathlib.Path`、`re`。
- Produces: `.env.example` 含全 16 注释键；文档 3 处更新；2 个防回归测试（依赖 `.env.example` 已填，故置于本任务）。

- [ ] **Step 1: 写失败测试（防漂移闭环 + 仅注释）**

在 `tests/test_signal_backtest_config.py` 末尾追加（补 `import re`、`from pathlib import Path`）：

```python
def _interval_override_keys():
    from src.core.intraday_backtest import INTRADAY_INTERVAL_MINUTES
    return {
        f"VPS_{field.upper()}_{interval.upper()}"
        for interval in INTRADAY_INTERVAL_MINUTES
        for field in _INTERVAL_OVERRIDE_FIELDS
    }


def _env_example_path():
    return Path(__file__).resolve().parents[1] / ".env.example"


def test_env_example_documents_all_interval_override_keys():
    # 防漂移闭环（I1）：canonical 每个分钟 interval × 4 字段，均须在 .env.example 有注释行。
    # 中心给 INTRADAY_INTERVAL_MINUTES 加新 interval（如 30m）→ 此测试 RED → 强制补文档。
    text = _env_example_path().read_text(encoding="utf-8")
    missing = sorted(k for k in _interval_override_keys() if f"# {k}=" not in text)
    assert missing == [], f"缺注释行的 interval 覆盖键: {missing}"


def test_interval_override_keys_only_commented_in_env_example():
    # I2：16 键严禁裸 KEY= 活动行（否则 RED env-example 覆盖门）。
    keys = _interval_override_keys()
    active = set()
    for line in _env_example_path().read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip())
        if m and m.group(1) in keys:
            active.add(m.group(1))
    assert active == set(), f"这些键必须为注释行、不得为活动赋值: {sorted(active)}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_config.py -k "env_example" -v`
Expected: FAIL —— `test_env_example_documents_all_interval_override_keys` 报缺 16 个键（`.env.example` 尚未追加）。

- [ ] **Step 3: 追加 16 注释键到 `.env.example`**

在 `.env.example` 的 M1 量价信号引擎段（`# ============ 量价信号引擎（M1，volume_price_signals.py）============` 附近、现有 `VPS_*` 之后）追加：

```bash
# --- interval 维度窗口阈值覆盖（默认不配=复用日线值；仅 4 个 window 字段；interval-only）---
# 优先级：interval 覆盖 > crypto 旁路（VPS_CRYPTO_*）> 日线默认。仅作用于链路B 分钟回测。
# 仅去偏这 4 个 window 参数；ma5/ma20、背离窗口(14)、价位窗口(20)仍为日线硬编码、不受影响。
# warmup 门 = max(vol_ma_window, atr_period, breakout_window)+1，缩短分钟 warmup 须一并调 VPS_VOL_MA_WINDOW_<interval>。
# VPS_VOL_MA_WINDOW_1M=
# VPS_VOL_MA_WINDOW_5M=
# VPS_VOL_MA_WINDOW_15M=
# VPS_VOL_MA_WINDOW_1H=
# VPS_BREAKOUT_WINDOW_1M=
# VPS_BREAKOUT_WINDOW_5M=
# VPS_BREAKOUT_WINDOW_15M=
# VPS_BREAKOUT_WINDOW_1H=
# VPS_ATR_PERIOD_1M=
# VPS_ATR_PERIOD_5M=
# VPS_ATR_PERIOD_15M=
# VPS_ATR_PERIOD_1H=
# VPS_SWING_K_1M=
# VPS_SWING_K_5M=
# VPS_SWING_K_15M=
# VPS_SWING_K_1H=
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_backtest_config.py -k "env_example" -v`
Expected: PASS（16 键齐、均为注释行）。

- [ ] **Step 5: 更新 `docs/volume-price-signals.md`（§7 阈值表附近加小节）**

在 §7 阈值表后新增小节，内容须含：键命名 `VPS_<FIELD>_<INTERVAL 大写>`（4 字段 × `1m/5m/15m/1h`）、优先级（interval > crypto > 日线）、默认不配=复用日线值、interval-only、**仅覆盖 4 个 window 字段**；并**明示通道部分性**：`ma5/ma20`、背离窗口 `_DIV_CMF_WINDOW/_DIV_MFI_WINDOW=14`、`_PRICE_LEVEL_WINDOW=20` 仍为日线硬编码、不受本通道影响；**warmup 门**由 `max(vol_ma_window, atr_period, breakout_window)+1` 驱动，缩短分钟 warmup 须一并设 `VPS_VOL_MA_WINDOW_<interval>`；本期不做经验定值（待真实数据标定）。

- [ ] **Step 6: 更新 `docs/signal-credibility.md`（§11.5 / §12）**

把 §11.5 / §12 中「VPS 阈值为日线调参，分钟下直接复用属 best-effort，未单独标定」一类措辞，更新为：「已提供 interval 维度**窗口**覆盖通道（`VPS_<窗口>_<interval>`，默认不配=复用日线值，仅 4 个 window 字段）；具体分钟定值仍待真实数据标定」。保留原有 crypto 旁路说明。

- [ ] **Step 7: 更新 `docs/CHANGELOG.md`（`[Unreleased]` 扁平一行）**

在 `[Unreleased]` 段追加（扁平格式，不加 `### 标题`）：

```markdown
- [新功能] VPS 量价信号引擎新增 interval 维度窗口阈值覆盖通道（VPS_<vol_ma_window|breakout_window|atr_period|swing_k>_<1m|5m|15m|1h>，默认不配=复用日线值，仅链路B 分钟回测生效，不进 Web 设置）；放量突破 reason 文案由"近N日高点"泛化为"近N根高点"
```

- [ ] **Step 8: flake8 测试文件 + 跑本任务两测**

Run: `.venv/bin/python -m flake8 tests/test_signal_backtest_config.py && .venv/bin/python -m pytest tests/test_signal_backtest_config.py -k "env_example" -v`
Expected: flake8 无输出；两测 PASS。

- [ ] **Step 9: Commit**

```bash
git add .env.example docs/volume-price-signals.md docs/signal-credibility.md docs/CHANGELOG.md tests/test_signal_backtest_config.py
git commit -m "docs: VPS interval 覆盖通道 .env.example/文档/CHANGELOG + 防漂移与仅注释回归测试"
```

---

## 收尾：整批门禁

- [ ] **Step A: 跑 ci_gate（flake8 + pytest -m "not network"）**

Run: `./scripts/ci_gate.sh`
Expected: `backend-gate: all checks passed`；pytest 全绿（基线 3820 passed，本计划净增约 +14 用例：Task1 ~12 参数化展开更多、Task2 1、Task3 2、Task4 2）。

- [ ] **Step B: 交付说明**

按 spec §11：改了什么 / 为什么 / 验证情况（ci_gate 结果）/ 未验证项（真实分钟定值待数据环境）/ 风险点（极低，默认 opt-in 字节级不变；唯一行为变化是 reason 文案「日」→「根」）/ 回滚方式（单分支 `git revert`，env 键未配本就无效）。

---

## Self-Review（plan vs spec）

**1. Spec 覆盖：**
- §3 配置面（16 键/下限/命名）→ Task 1（常量+方法）+ Task 4（.env.example）✓
- §4 机制（for_market_interval、is_intraday_interval 复用、raw.strip()、优先级、不变式）→ Task 1 ✓
- §5 接线（signal_backtest_service `:157`，board 不动）→ Task 2 ✓（board 路径未列入任何任务 = 不动，符合）
- §6 reason 标签（:856/:1418/:822、不动 is_daily_approx/analysis_tools/前端 fixtures）→ Task 3 ✓
- §7 测试 #1–#12 → Task1（#1-#7,#12）+ Task2（#8）+ Task3（#9）+ Task4（#10,#11）✓
- §8 文档（.env.example 仅注释、volume-price-signals、signal-credibility、CHANGELOG）→ Task 4 ✓
- §2 out-of-scope（乘数/硬编码窗口/horizon/min_history/is_daily_approx/registry-Web）→ 无任务触碰，符合 ✓
- F2/F4/F5 plan-level 注记 → Global Constraints（F2 局部 import）+ Task1 Step1（F4 参数化/crypto 回落）+ Task3 Step3（F5 grep）✓

**2. Placeholder 扫描：** 无 TBD/TODO；每个 code step 均含完整代码与确切命令/期望输出。✓

**3. 类型一致性：** `for_market_interval(market: str|None, interval: str|None) -> VPSConfig` 在 Task1 定义、Task2 调用一致；`_INTERVAL_OVERRIDE_FIELDS` 在 Task1 产出、Task4 测试消费一致；`_detect_breakouts_rows` 返回 `list[tuple[int, VPSignal]]`、测试按 `for _, s in ...` 解构一致；`_normalize(df,cfg)->(norm,_)`、`_compute_primitives(norm,cfg)` 调用与既有向量化测试一致。✓
