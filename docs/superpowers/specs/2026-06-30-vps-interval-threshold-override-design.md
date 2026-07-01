# VPS 阈值分钟标定 — interval 覆盖机制 设计

> 状态：设计经两轮对抗式审查（4 视角 Workflow + 顶级评审 fresh-eyes），3I+8M+F1/F3 全部收敛，待落实现计划。
> 日期：2026-06-30
> 关联里程碑：链路B 信号可信度分钟化的 deferred follow-up（见 `docs/superpowers/specs/2026-06-25-signal-eval-vectorization-design.md:48`、`2026-06-26-chainb-window-correctness-design.md:424` 中显式 defer 的「VPS 阈值分钟标定」）。

## 1. 背景与问题

VPS（Volume-Price Signal，量价信号引擎，`src/services/volume_price_signals.py`）的全部阈值是**日线调参**且**market 维度可覆盖**（`VPSConfig.for_market` 仅对 `crypto` 分支），但**完全 interval 无关**——引擎源文件零个 `interval`，`signal_backtest.py` docstring 自述「bar-interval 无关」。链路B 分钟回测在分钟 bar 上重算 VPS 时，经 `VPSConfig.for_market(market)` 原样复用同一组日线数值。

核心错配在**窗口/根数类阈值**：它们按「交易 bar 数」计。日线 `breakout_window=20` ≈ 1 个月上下文；5m 下 20 根 ≈ 100 分钟。窗口含义随 interval 剧变，故官方记为 best-effort（`docs/signal-credibility.md:312`「VPS 阈值为日线调参，分钟下直接复用属 best-effort，未单独为分钟标定」）。

附带缺陷：放量突破 reason 串硬编码「近{N}**日**高点」，分钟 bar 下文案错误。

## 2. 范围（本期做什么 / 不做什么）

**做**：补一个 **interval 维度的窗口阈值覆盖机制**，默认全不配置 = 字节级复用现日线值（稳定性优先）。本期**只铺通道**，不做经验定值。顺带把分钟下错误的窗口标签泛化为对日线/分钟都正确的措辞。

**不做（out of scope，各自独立 follow-up）**：

- **真实经验标定 / 定值**：找出分钟下应取的具体阈值数值。需真实多市场分钟数据 + `--signal-backtest` 回测扫描；本环境网络阻断（crypto 经 `data-api.binance.vision` 前台有界可达，A股/HK/US 受 token/egress 阻断）。defer 到有数据环境。
- **market × interval 维度**：本期 interval-only（5m=5 分钟与市场无关）。crypto 已有 `for_market` bypass，可与 interval 覆盖组合。
- **乘数/比率类字段的 interval 化**：`eps`、`vol_low/shrink/up/high`、`breakout_rel_vol`、`pullback_rel_vol`、`pullback_atr_mult` 是比率/带宽，跨粒度可移植性强，本期不接 interval。`b_class_top_k` 是 top-k **计数限流器**（`_limit_b_class`），与粒度无关，亦不接 interval。
- **硬编码窗口的 interval 化**：`_PRICE_LEVEL_WINDOW=20`、`ma5/ma20`、`_DIV_CMF_WINDOW/_DIV_MFI_WINDOW=14`、`_SHRINK_PULLBACK_ATR_K=1.5` 不接 config（YAGNI；改动面大且非本期主要错配点）。
- **`horizon` / `min_history` 的 interval 化**：`signal_backtest_horizon_bars=10`、`min_history=40` 也含日线根数语义，但属 backtest 层非 VPSConfig，独立项。
- **`is_daily_approx` / 「日线近似」语义**：该结构化布尔字段硬编码 `True`，改它需把 interval 注入引擎（破坏「引擎 interval 无关」不变式）并触发下游消费方（Web/测试），独立项。
- **config_registry / Web 暴露**：与现有**全部** `VPS_*` 阈值一致（13 个 `from_env` 基础键 + 3 个 `crypto_*` 旁路键，均经核验不在 config_registry、不 Web 可调），只在 `from_env()` 直读 + `.env.example` + docs 文档化。新键沿此惯例。

## 3. 配置面

4 个窗口字段 × 4 个分钟 interval = **16 个新 env key**，命名 `VPS_<FIELD>_<INTERVAL 大写>`（`5m`→`5M`、`15m`→`15M`、`1h`→`1H`、`1m`→`1M`）：

| 字段 | 现日线默认 | 下限（minimum） | 覆盖键 |
|---|---|---|---|
| `vol_ma_window` | 20 | 5 | `VPS_VOL_MA_WINDOW_{1M,5M,15M,1H}` |
| `breakout_window` | 20 | 2 | `VPS_BREAKOUT_WINDOW_{1M,5M,15M,1H}` |
| `atr_period` | 14 | 2 | `VPS_ATR_PERIOD_{1M,5M,15M,1H}` |
| `swing_k` | 3 | 1 | `VPS_SWING_K_{1M,5M,15M,1H}` |

- 下限值与 `from_env()` 中现有同字段的 `minimum=` 完全一致（`vol_ma_window` 5、`breakout_window` 2、`atr_period` 2、`swing_k` 1）。
- 全部为整数窗口，读法沿用 `from_env()` 既有模式 `int(parse_env_float(raw, default, field_name=..., minimum=...))`。
- 只在 `.env.example`（M1 VPS 段）+ `docs/volume-price-signals.md` 文档化，**不进 config_registry、不 Web 可调**。

## 4. 机制

新增工厂方法 `VPSConfig.for_market_interval(market, interval)`，**VPSConfig 字段集零改动**（不新增 16 个 dataclass 字段；env 在方法内按需直读，保持 dataclass 干净、可哈希）。

伪代码：

```python
# 复用 src.core.intraday_backtest 的 canonical 分钟集合（模块顶 import；
# intraday_backtest 只依赖 typing，无循环依赖），不另立平行 _INTRADAY_INTERVALS。
from src.core.intraday_backtest import is_intraday_interval

# 字段 → minimum 下限（与 from_env 一致）
_INTERVAL_OVERRIDE_FIELDS = {
    "vol_ma_window": 5.0,
    "breakout_window": 2.0,
    "atr_period": 2.0,
    "swing_k": 1.0,
}

@classmethod
def for_market_interval(cls, market: str | None, interval: str | None) -> "VPSConfig":
    base = cls.for_market(market)              # 先做 crypto bypass
    if not interval or not is_intraday_interval(interval):  # "1d" / None / 未知 → 原样（日线字节级不变）
        return base
    suffix = interval.upper()                  # 5m -> 5M, 1h -> 1H
    overrides = {}
    for field_name, floor in _INTERVAL_OVERRIDE_FIELDS.items():
        env_key = f"VPS_{field_name.upper()}_{suffix}"
        raw = os.getenv(env_key)
        if raw is None or not raw.strip():     # 与 parse_env_float 的 .strip() 语义对齐；纯空白短路返回 base 原对象
            continue
        cur = getattr(base, field_name)
        overrides[field_name] = int(parse_env_float(
            raw, float(cur), field_name=env_key, minimum=floor))
    if not overrides:
        return base
    import dataclasses
    return dataclasses.replace(base, **overrides)
```

**优先级（自高到低）**：interval 覆盖（若该 interval 的键被显式 set）> crypto bypass（`for_market` 的 `crypto_*`）> 日线默认。

**关键不变式**：

- `interval in {None, "1d", 未知}` → early-return 直接返回 `for_market(market)`，**日线与既有行为字节级一致**。
- 任一 interval 的覆盖键**均未 set** → 返回 `for_market(market)`，**分钟行为亦与当前 best-effort 完全一致**（纯 opt-in）。
- 注（F3 澄清）：上述「不经 `dataclasses.replace`」是**内部**保证（省一次构造）。因 `for_market(market)` 每次新建对象，**外部不可用 `is` 跨调用断言 identity**；测试 #1/#7 用**字段相等**校验即可（勿写不可能的 identity 测试）。
- 非法/空值 → `parse_env_float` 回落到 base 当前值（crypto 时为 crypto 值，否则日线值）。
- 下限钳制由 `minimum=` 负责（如 `VPS_BREAKOUT_WINDOW_5M=1` → 钳到 2）。
- **interval-only 限制**：设 `VPS_BREAKOUT_WINDOW_5M` 会影响所有市场的 5m（含 crypto，若 crypto 同时未被其它机制覆盖）。这是 MVP 的已知局限，文档明示。
- **warmup 门耦合**：`_check_sufficient_window` 的 `min_bars = max(vol_ma_window, atr_period, breakout_window) + 1`（不含 `swing_k`）。仅调小 `breakout_window`/`atr_period` 而不动 `vol_ma_window=20` **不会**缩短分钟 warmup（20 仍主导）；要真正缩短须一并设 `VPS_VOL_MA_WINDOW_<interval>`。文档明示。
- **通道部分性**：本通道只去偏引擎 4 个 window 参数；`ma5/ma20` 趋势门、背离窗口 `_DIV_CMF_WINDOW/_DIV_MFI_WINDOW=14`、`_PRICE_LEVEL_WINDOW=20` 仍为日线硬编码，分钟下不受 `VPS_<窗口>_<interval>` 影响。文档明示（见 §2 out of scope）。
- **F2 实现注记（分层，二选一明确即可）**：把 `intraday_backtest` import 进自述「纯函数引擎」的 `volume_price_signals` 是**有意的 DRY 决定**（`intraday_backtest` 仅依赖 `typing`、dep-light、无环，已核验）；若偏好保持模块顶干净，可仿现有 `import dataclasses` 在 `for_market_interval` **方法内局部 import**。两者皆可，实现时明确取舍、勿默认。

## 5. 接线

唯一接线点：`src/services/signal_backtest_service.py:157`

```python
# 现：
cfg_m = VPSConfig.for_market(market)
# 改：
cfg_m = VPSConfig.for_market_interval(market, interval)
```

`interval` 已是 `SignalBacktestService.run(*, interval="1d")` 的形参，在该循环作用域内可用。

**不动**：`src/services/signal_board_service.py:105`（`VPSConfig.for_market(engine_market)`）——看板/K 线按日线计算，interval 仅用于解析可信度桶，保持日线 config 语义。

## 6. reason 标签修正

`src/services/volume_price_signals.py` 两处放量突破 reason 串（主 `:856`，向量化 `_rows` 孪生 `:1418`）：

```
"放量突破近{config.breakout_window}日高点（不含当日）[量能形态:{...}]"
→ "放量突破近{config.breakout_window}根高点（不含当日）[量能形态:{...}]"
```

「根」对日线（20 根=20 日）与分钟（20 根=N 分钟）均准确，无需把 interval 注入引擎、不破坏「引擎 interval 无关」不变式。同 PR 顺带把检测器 docstring `:822`「过去 N 日 high」→「过去 N 根 high」，保持内部措辞一致（无行为/输出影响）。

- **日线可见副作用（已扩面核实）**：`sig.reason` 经 `signals_service.py:145` 序列化，呈现在**所有 signal-reason 展示面**（看板 / 下钻面板 / 工作台面板 / K线 tooltip），文案统一「日」→「根」。均为信息性文本、下游无解析，无害。
- **Python 测试零断言**：grep `tests/`（`.py`）对「近 N 日高点」零命中，不破坏后端测试。**F5 实现注记**：翻「日」→「根」前，实现者须再放宽 grep（`20日` / `日（不含` / `日高`）跨 `src/` + `tests/` + `apps/dsa-web`，确保无子串断言残留后再改。
- **前端 fixtures 不受影响**：`apps/dsa-web` 的 `KLineChartPanel.signals.test.tsx` / `SignalDrilldownPanel.test.tsx` / `stocks.signals.test.ts` 含字面量「放量突破20日高」，但均为各自独立 `vi.mock`（自设自读），且与引擎真实输出「放量突破近20日高点（不含当日）[...]」字面不同——泛化引擎不会触红，**确认无需 web-gate**；实现时**不得**把这些 fixtures 与引擎措辞耦合。
- **不动** `src/agent/tools/analysis_tools.py:451` 的字面量「放量突破20日高点」（LLM 工具 pattern 描述符，与引擎无关）。
- **不动** `is_daily_approx=True` 与「日线近似」相关标记（见 §2 out of scope）。

## 7. 测试设计

新增/扩展（建议 `tests/test_signal_backtest_config.py` 与 `tests/test_signal_backtest_service.py`，对齐现有 `VPS_*` / `for_market` 测试位置）：

1. **默认字节级**：对 `interval in {"1d","1m","5m","15m","1h"}`，在无任何 `VPS_*_<INTERVAL>` env 下，`for_market_interval(market, interval)` 各字段 == `for_market(market)`（覆盖 cn 与 crypto 两个 market）。
2. **单字段覆盖**：设 `VPS_BREAKOUT_WINDOW_5M=10`，`for_market_interval("cn","5m").breakout_window == 10`，且其余字段（`vol_ma_window/atr_period/swing_k` 及所有乘数）不变。
3. **crypto 组合优先级**：
   - `VPS_BREAKOUT_WINDOW_5M` 未 set → `for_market_interval("crypto","5m").breakout_window == crypto_breakout_window`（保留 crypto 值）；
   - `VPS_BREAKOUT_WINDOW_5M=7` set → `for_market_interval("crypto","5m").breakout_window == 7`（interval 压过 crypto）。
4. **下限钳制**：`VPS_BREAKOUT_WINDOW_5M=1` → 结果 == 2（钳到 minimum）。
5. **非法值回落**：`VPS_ATR_PERIOD_15M=abc` → `for_market_interval("cn","15m").atr_period == 14`（回落 base）。
6. **interval 隔离**：设 `VPS_SWING_K_5M=5`，`for_market_interval("cn","15m").swing_k == 3`（只影响 5m）。
7. **"1d" 与未知 interval 不变**：`for_market_interval("cn","1d")` 与 `for_market_interval("cn",None)` 各字段 == `for_market("cn")`。
8. **服务接线**：扩现有 `test_a5_config_passthrough_uses_for_market_per_code` 的 interval 版——`run(interval="5m")` 在设了 `VPS_BREAKOUT_WINDOW_5M` 时引擎确实拿到覆盖值。**必须 patch 分钟取数路径**：`5m` 走 `_load_bars` 的 intraday 分支调 `DataFetcherManager().get_intraday_data(...)`（**不**走日线测试 patch 的 `StockService.get_history_data`），故须 monkeypatch `data_provider.base.DataFetcherManager.get_intraday_data`（或 `_load_bars`）返回 **≥ `_MIN_BARS`(50) 行**的分钟 DataFrame，否则该股落 `skipped` 永不触达 `:158` 配置捕获点（测试会假绿）。
9. **reason 串**：放量突破 reason 含「根」不含「日高点」（主路径 + 向量化 `_rows` 路径各一）。
10. **interval 集合防漂移（锁 I1，须有效闭环）**：**不可**写成 `assert is_intraday_interval('5m') and not is_intraday_interval('1d')`——那只是把 stdlib 函数对着自己测、抓不住真实漂移。真正漂移在**文档**：中心给 `INTRADAY_INTERVAL_MINUTES` 加 `30m` 后代码不崩（`for_market_interval(_, "30m")` 读未配的 `VPS_*_30M` → 返回 base），但 `.env.example` 仍只列 16 键、`30m` 的 4 键无人文档化，且无反向覆盖测试发现。故 #10 须断言：对 `INTRADAY_INTERVAL_MINUTES` 中**每个** interval，`.env.example` 含全部 4 个字段的注释行 `# VPS_<FIELD>_<INTERVAL 大写>=`（cross-product = canonical × 4 字段；建议直接从 `INTRADAY_INTERVAL_MINUTES.keys()` × `_INTERVAL_OVERRIDE_FIELDS` 生成期望键集再比对 `.env.example`）。中心新增 interval → 该测试 RED → 强制补文档，方为 I1 的有效防漂移闭环。
11. **.env.example 仅注释（锁 I2）**：断言 16 个 `VPS_*_<INTERVAL>` 键在 `.env.example` 中**仅以注释行**（`# VPS_..._5M=`）出现、无任何裸 `KEY=` 活动行（否则触红 `tests/test_config_registry.py` 的 `test_active_env_example_keys_are_registered_or_hidden_from_web_ui`）。
12. **warmup 门耦合（可选，锁 M7 语义）**：同时设 `VPS_VOL_MA_WINDOW_5M`+`VPS_ATR_PERIOD_5M`+`VPS_BREAKOUT_WINDOW_5M` 三者更小 → `_check_sufficient_window` 的 `min_bars` 确实缩短；仅设 `VPS_BREAKOUT_WINDOW_5M` 不缩短（`vol_ma_window=20` 主导）。

env 测试必须在 teardown 清理 `VPS_*_<INTERVAL>`，避免污染其它用例（沿用现有 `monkeypatch.setenv` 或 fixture 模式）。

**F4 实现注记（测试完备性）**：测试 #2「单字段覆盖生效」须**参数化跑全 4 个 interval**（`1m/5m/15m/1h`），catch `1h→1H` 等后缀映射 bug，勿只测 5m；测试 #5「非法值回落」补 **crypto 变体**（`VPS_ATR_PERIOD_5M=abc` on `crypto` → 回落 `crypto_atr_period` 而**非**日线 14），与 cn 变体互补，锁定「回落到 base（含 crypto 旁路值）」。

## 8. 文档

- **`.env.example`**：M1 VPS 段（约 `:869`）追加 16 个键，**必须全部为完整注释行**（`# VPS_BREAKOUT_WINDOW_5M=`），**严禁裸 `KEY=` 活动行**（裸行会触红 backend-gate 的 env-example 覆盖测试，见 §7 #11），并说明「默认不配=复用日线值」与 interval-only 局限。
- **`docs/volume-price-signals.md`**（§7 阈值表附近）：加 interval 覆盖小节——键命名规则、优先级（interval > crypto > 日线）、interval-only 限制、**仅覆盖 4 个 window 字段**、本期不定值。**明示通道部分性**：`ma5/ma20`、背离窗口（14）、`_PRICE_LEVEL_WINDOW`（20）仍为日线硬编码、不受本通道影响；**warmup 门**由 `max(vol_ma_window, atr_period, breakout_window)+1` 驱动，缩短分钟 warmup 须一并调 `VPS_VOL_MA_WINDOW_<interval>`。
- **`docs/signal-credibility.md §11.5 / §12`**：把「VPS 阈值为日线调参，分钟下直接复用属 best-effort，未单独标定」更新为「已提供 interval 维度窗口覆盖通道（`VPS_<窗口>_<interval>`，默认不配=复用日线值）；具体分钟定值仍待真实数据标定」。
- **`docs/CHANGELOG.md`**：`[Unreleased]` 扁平 `[新功能]` 一行。

## 9. 验证矩阵

- Python 后端改动：`./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`）。
- 受影响路径：信号回测分钟 config 选择、reason 文案；不触 API/Web/Schema/认证/调度。
- 不需 web-gate（未进 config_registry/settingsHelp/Web）。

## 10. 风险与回滚

- **风险**：极低。默认全不配 → 分钟与日线均字节级不变（纯 opt-in，early-return + 未 set 短路双重保证）。唯一行为变化是 reason 文案「日」→「根」（无测试断言、信息性文本）。
- **回滚**：单 PR，`git revert` 即可；env 键未配时本就无效。

## 11. 交付结构（实现完成时）

改了什么 / 为什么 / 验证情况（ci_gate 结果）/ 未验证项（真实分钟定值待数据）/ 风险点 / 回滚方式。
