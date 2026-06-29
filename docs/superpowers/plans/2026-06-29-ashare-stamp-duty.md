# A股不对称印花税（盘中回测、卖出单边）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在链路A 盘中回测成本模型中补一个 A股(cn) 卖出单边印花税分量（opt-in 默认 0），仅 cn + long 仓出场计征，crypto/us/cash/日线一律不征。

**Architecture:** 扩展唯一成本枢纽 `apply_round_trip_cost` 追加可选参 `sell_side_bps`（单边 ×1，不造平行函数）；在 `backtest_service` 盘中后处理块加 `market=="cn" and position=="long"` 门控传入该参。新增配置项 `ashare_intraday_backtest_stamp_duty_bps`（config.py 运行态 + config_registry Web/API schema + settingsHelp.ts locale 三处同步）。默认 0 → 后处理整体跳过，字节级现状不变。

**Tech Stack:** Python（dataclass config + pytest）、TypeScript（Web locale，仅 settingsHelp.ts 文本）、既有回测引擎/服务层。

## Global Constraints

- **默认 0 = 字节级现状**：不配置时，C1 早退守卫 + C2 `if _fee or _slip or _stamp` 跳过，`simulated_return_pct` 与现状逐字节一致。crypto/us/日线/cash 路径数值不受影响。
- **门控恰好 `market == "cn"`**：禁止写成 `in {"cn","us"}` 或任何放宽——美股是已上线市场，误征会静默污染其回测结果。
- **卖出单边 ×1**：印花税只在出场计一次（`+ 1.0 * sell_side_bps / 100`），与对称 fee/slippage 的 ×2 区分；fee/slippage 数学不变。
- **浮点断言一律 `pytest.approx(...)`**：裸 `==` 对 `7.7 - 0.1` 得 `7.6000000000000005` 会假失败。
- **config_registry 注册项 = Web/API 暴露**：`build_schema_response` 全量遍历无隐藏过滤，新项会进 `/config/schema` 并在 Web 设置页 Backtest 分类渲染可改。注册即**必须**同步 `settingsHelp.ts` locale（否则 `test_registry_help_keys_exist_in_locales` 红）且补全 `help_key`/`examples`/`docs`（否则 `test_web_settings_visible_fields_have_help_metadata` 红）。
- **键命名**：env 键 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`；dataclass 字段 `ashare_intraday_backtest_stamp_duty_bps`；locale/help_key `settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`。三处必须逐字一致。
- **commit message**：英文类型前缀 + 中文描述，不加 `Co-Authored-By`，不加工具/agent 前缀。
- **不写死密钥/账号/路径/端口/模型名**。
- **A股印花税口径**：现行卖出单边 5bps（0.05%，2023-08 起由 0.1% 下调）。默认 0，文档标明 5 供用户设。佣金/双边成本仍走跨市场共享的 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`。

**关联 spec：** `docs/superpowers/specs/2026-06-29-ashare-stamp-duty-design.md`（v2，经对抗式审查收敛）。

---

## 文件结构（改动面）

| 文件 | 职责 | 改动 |
|---|---|---|
| `src/core/intraday_backtest.py` | 成本枢纽纯函数 | `apply_round_trip_cost` 增 `sell_side_bps` 参 + docstring（Task 1） |
| `tests/test_intraday_backtest_helpers.py` | 枢纽单测 | 加 T1–T4（Task 1） |
| `src/config.py` | 运行态配置 dataclass + env 加载器 | 加字段 + 加载器（Task 2） |
| `.env.example` | 配置样例（独立文件） | 加注释块（Task 2） |
| `tests/test_config_intraday_backtest.py` | 配置解析单测（**已存在，扩展**） | 默认 0 + env-override（Task 2） |
| `src/services/backtest_service.py` | 盘中后处理门控 | 后处理块加 cn+long 门控（Task 3） |
| `tests/test_backtest_service_intraday.py` | 后处理集成测（**含现有成本测**） | 扩展现有 helper + 加 G1–G6（Task 3） |
| `src/core/config_registry.py` | Web/API config schema | 加注册项（Task 4） |
| `apps/dsa-web/src/locales/settingsHelp.ts` | Web 设置页帮助文案（**唯一 locale 文件**） | 加 locale 条目（Task 4） |
| `docs/intraday-backtest.md` | 盘中回测专题文档 | 成本小节 + 限制项 + 配置表（Task 5） |
| `docs/CHANGELOG.md` | 变更日志 | `[Unreleased]` 扁平条目（Task 5） |

**任务顺序与依赖：** Task 1（纯函数）→ Task 2（配置字段）→ Task 3（消费 1+2：调用新参 + monkeypatch 新字段）→ Task 4（Web 暴露）→ Task 5（文档）。

---

### Task 1: 成本枢纽增卖出单边参 `sell_side_bps`

**Files:**
- Modify: `src/core/intraday_backtest.py:60-72`
- Test: `tests/test_intraday_backtest_helpers.py`（现有文件，追加用例）

**Interfaces:**
- Produces: `apply_round_trip_cost(return_pct: Optional[float], fee_bps: float, slippage_bps: float, sell_side_bps: float = 0.0) -> Optional[float]`——Task 3 以 `sell_side_bps=_stamp` 关键字传入。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_intraday_backtest_helpers.py` 末尾；`pytest` 与 `apply_round_trip_cost` 已 import）

```python
def test_apply_round_trip_cost_sell_side_only():
    # 仅卖出单边(印花税 5bp) → 扣 5/100 = 0.05
    assert apply_round_trip_cost(10.0, 0.0, 0.0, sell_side_bps=5.0) == pytest.approx(9.95)


def test_apply_round_trip_cost_three_components():
    # 双边 fee 2 + slip 3 → 2*(2+3)/100 = 0.10;加卖出单边 5 → 0.05;共扣 0.15
    assert apply_round_trip_cost(10.0, 2.0, 3.0, sell_side_bps=5.0) == pytest.approx(9.85)


def test_apply_round_trip_cost_default_sell_side_byte_identical():
    # 不传 sell_side_bps:全 0 走早退守卫,字节级不变
    assert apply_round_trip_cost(10.0, 0.0, 0.0) == pytest.approx(10.0)
    # 与旧两参实现同值(7.7 - 2*(2+3)/100 = 7.6)
    assert apply_round_trip_cost(7.7, 2.0, 3.0) == pytest.approx(7.6)


def test_apply_round_trip_cost_none_passthrough_with_sell_side():
    assert apply_round_trip_cost(None, 0.0, 0.0, sell_side_bps=5.0) is None
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py::test_apply_round_trip_cost_sell_side_only tests/test_intraday_backtest_helpers.py::test_apply_round_trip_cost_three_components -v`
Expected: FAIL — `TypeError: apply_round_trip_cost() got an unexpected keyword argument 'sell_side_bps'`

- [ ] **Step 3: 改实现**（`src/core/intraday_backtest.py:60-72` 整体替换）

```python
def apply_round_trip_cost(
    return_pct: Optional[float],
    fee_bps: float,
    slippage_bps: float,
    sell_side_bps: float = 0.0,
) -> Optional[float]:
    """对一进一出收益(百分比)扣减成本。默认全 0 → 原样返回(行为不变)。

    - fee_bps / slippage_bps：对称双边成本,一进一出各计一次 → ×2(基点,1bp=0.01%)。
    - sell_side_bps：单边卖出成本(如 A股印花税),仅出场计一次 → ×1。调用方负责仅在
      「确有卖出」(long 出场)时传非 0;cash/无成交不应传。
    """
    if return_pct is None:
        return None
    if not fee_bps and not slippage_bps and not sell_side_bps:
        return return_pct
    cost_pct = (
        2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0
        + 1.0 * float(sell_side_bps) / 100.0
    )
    return return_pct - cost_pct
```

- [ ] **Step 4: 运行验证通过（含既有用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py -v`
Expected: PASS（新 4 测 + 既有 `test_apply_round_trip_cost_default_zero_is_noop` / `test_apply_round_trip_cost_deducts_two_fills` 全绿）

- [ ] **Step 5: 提交**

```bash
git add src/core/intraday_backtest.py tests/test_intraday_backtest_helpers.py
git commit -m "feat: apply_round_trip_cost 增卖出单边成本参数 sell_side_bps"
```

---

### Task 2: 新增运行态配置 `ashare_intraday_backtest_stamp_duty_bps`

**Files:**
- Modify: `src/config.py:906`（dataclass 字段）、`src/config.py:1904-1907`（env 加载器）
- Modify: `.env.example:714`（紧邻 `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS`）
- Test: `tests/test_config_intraday_backtest.py`（**已存在，扩展两测**）

**Interfaces:**
- Produces: `config.ashare_intraday_backtest_stamp_duty_bps: float`（默认 0.0，env `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`，`minimum=0.0`）——Task 3 后处理与测试 helper 读取/monkeypatch 此字段。

- [ ] **Step 1: 写失败测试**（修改 `tests/test_config_intraday_backtest.py`）

把 `test_intraday_defaults_are_status_quo` 的 docstring 与 pop 列表、断言扩为含新字段：

```python
def test_intraday_defaults_are_status_quo():
    """All 7 new fields must have status-quo defaults when no env var is set."""
    env_overrides = {}
    for k in [
        "CRYPTO_INTRADAY_BACKTEST_INTERVAL", "CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S",
        "CRYPTO_INTRADAY_BACKTEST_FEE_BPS", "CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS",
        "INTRADAY_BACKTEST_ENABLED", "INTRADAY_BACKTEST_SCHEDULE_MINUTES",
        "ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS",
    ]:
        # Ensure these vars are absent from env during the test
        os.environ.pop(k, None)

    cfg = _load_config(**env_overrides)
    assert cfg.crypto_intraday_backtest_interval == "5m"
    assert cfg.crypto_intraday_minute_cache_ttl_s == 900
    assert cfg.crypto_intraday_backtest_fee_bps == 0.0
    assert cfg.crypto_intraday_backtest_slippage_bps == 0.0
    assert cfg.intraday_backtest_enabled is False
    assert cfg.intraday_backtest_schedule_minutes == 60
    assert cfg.ashare_intraday_backtest_stamp_duty_bps == 0.0
```

把 `test_intraday_env_override` 增一条 override + 断言：

```python
def test_intraday_env_override():
    """Env vars must override each field correctly."""
    cfg = _load_config(
        CRYPTO_INTRADAY_BACKTEST_INTERVAL="15m",
        CRYPTO_INTRADAY_BACKTEST_FEE_BPS="4",
        INTRADAY_BACKTEST_ENABLED="true",
        INTRADAY_BACKTEST_SCHEDULE_MINUTES="30",
        ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS="5",
    )
    assert cfg.crypto_intraday_backtest_interval == "15m"
    assert cfg.crypto_intraday_backtest_fee_bps == 4.0
    assert cfg.intraday_backtest_enabled is True
    assert cfg.intraday_backtest_schedule_minutes == 30
    assert cfg.ashare_intraday_backtest_stamp_duty_bps == 5.0
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_config_intraday_backtest.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'ashare_intraday_backtest_stamp_duty_bps'`

- [ ] **Step 3: 改实现 — dataclass 字段**（`src/config.py:906` 之后插入）

把：

```python
    crypto_intraday_backtest_slippage_bps: float = 0.0
    intraday_backtest_enabled: bool = False
```

改为：

```python
    crypto_intraday_backtest_slippage_bps: float = 0.0
    # A股盘中回测卖出单边印花税(基点);默认 0=理想化无成本;A股现行 5bps(0.05%)
    ashare_intraday_backtest_stamp_duty_bps: float = 0.0
    intraday_backtest_enabled: bool = False
```

- [ ] **Step 4: 改实现 — env 加载器**（`src/config.py:1904-1907` 之后插入）

把：

```python
            crypto_intraday_backtest_slippage_bps=parse_env_float(
                os.getenv('CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS'), 0.0,
                field_name='CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS', minimum=0.0,
            ),
            intraday_backtest_enabled=parse_env_bool(
```

改为：

```python
            crypto_intraday_backtest_slippage_bps=parse_env_float(
                os.getenv('CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS'), 0.0,
                field_name='CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS', minimum=0.0,
            ),
            ashare_intraday_backtest_stamp_duty_bps=parse_env_float(
                os.getenv('ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0,
                field_name='ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS', minimum=0.0,
            ),
            intraday_backtest_enabled=parse_env_bool(
```

- [ ] **Step 5: 改实现 — `.env.example`**（`.env.example:714` 之后插入）

把：

```
# 单边手续费/滑点(基点);默认 0=理想化无成本
# CRYPTO_INTRADAY_BACKTEST_FEE_BPS=0
# CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=0
# 盘中回测定时后台任务(默认关闭,手动触发不受影响)
```

改为：

```
# 单边手续费/滑点(基点);默认 0=理想化无成本
# CRYPTO_INTRADAY_BACKTEST_FEE_BPS=0
# CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=0
# A股盘中回测卖出单边印花税(基点,仅分钟级生效);默认 0=理想化无成本;A股现行 5(0.05%)
# 注:佣金/双边成本仍走 CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS(跨市场共享),A股真实总成本需一并设
# ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0
# 盘中回测定时后台任务(默认关闭,手动触发不受影响)
```

- [ ] **Step 6: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_config_intraday_backtest.py -v`
Expected: PASS（两测全绿）

- [ ] **Step 7: 提交**

```bash
git add src/config.py .env.example tests/test_config_intraday_backtest.py
git commit -m "feat: 新增 A股盘中印花税配置 ashare_intraday_backtest_stamp_duty_bps"
```

---

### Task 3: 后处理门控（仅 cn + long 计卖出单边税）+ G1–G6 集成测

**Files:**
- Modify: `src/services/backtest_service.py:304-312`
- Test: `tests/test_backtest_service_intraday.py`（扩展现有 helper `_make_intraday_svc_with_engine_return` + 加参数化 `test_stamp_duty_gate`）

**Interfaces:**
- Consumes: Task 1 的 `apply_round_trip_cost(..., sell_side_bps=)`；Task 2 的 `config.ashare_intraday_backtest_stamp_duty_bps`；既有 `market = market_of(analysis.code)`（`backtest_service.py:151`，盘中块首条，到达 `:304` 必已赋值且本轮重赋，无 NameError/无残留）。

> **测试归位说明（对 spec §6.2 的细化）：** G1–G6 全部落在 `tests/test_backtest_service_intraday.py`，**复用该文件既有的成本后处理 helper `_make_intraday_svc_with_engine_return`**（与既有 `test_cost_zero_keeps_return_unchanged` / `test_cost_positive_deducts_round_trip` 同址同源——这才是「成本后处理」测试的内聚位置）。市场覆盖（cn/us/crypto）通过 `code` 参驱动 `market_of(code)` 实现（`600519`→cn、`AAPL`→us、`BTC/USDT:PERP`→crypto），比按市场分散到三个文件更 DRY，且不需要在每个市场文件重复 monkeypatch 样板。

- [ ] **Step 1: 写失败测试 — 扩展 helper**（`tests/test_backtest_service_intraday.py`，`_make_intraday_svc_with_engine_return` 改 3 处）

(1) 函数签名追加关键字-only 参（向后兼容，既有两测不受影响）：

把：

```python
def _make_intraday_svc_with_engine_return(monkeypatch, tmp_path, engine_return_pct: float, fee_bps: float = 0.0, slippage_bps: float = 0.0):
```

改为：

```python
def _make_intraday_svc_with_engine_return(
    monkeypatch, tmp_path, engine_return_pct: float,
    fee_bps: float = 0.0, slippage_bps: float = 0.0,
    *, code: str = "BTC/USDT:PERP", position: str = "long", stamp_bps: float = 0.0,
):
```

(2) 在两行 fee/slip monkeypatch 之后追加印花税 monkeypatch，并用 `code` 建候选：

把：

```python
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_fee_bps", fee_bps)
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_slippage_bps", slippage_bps)

    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=101)
```

改为：

```python
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_fee_bps", fee_bps)
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_slippage_bps", slippage_bps)
    monkeypatch.setattr(real_cfg, "ashare_intraday_backtest_stamp_duty_bps", stamp_bps)

    candidate = _fake_analysis(code=code, analysis_id=101)
```

(3) 让 mock 引擎按 `position` 回传 `position_recommendation` 与 cash 的空入场价：

把：

```python
            "operation_advice": "买入",
            "position_recommendation": "long",
            "start_price": 100.0,
```

改为：

```python
            "operation_advice": "买入",
            "position_recommendation": position,
            "start_price": 100.0,
```

并把：

```python
            "simulated_entry_price": 100.0,
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": engine_return_pct,
```

改为：

```python
            "simulated_entry_price": None if position == "cash" else 100.0,
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": engine_return_pct,
```

- [ ] **Step 2: 写失败测试 — 加 G1–G6**（紧跟既有 `test_cost_positive_deducts_round_trip` 之后追加；`pytest` 已 import）

```python
@pytest.mark.parametrize(
    "label,code,position,engine_return,stamp_bps,fee_bps,slip_bps,expected",
    [
        # G1 cn + long:计一次卖出印花税 5bp → 10.0 - 0.05
        ("G1_cn_long",      "600519",        "long", 10.0, 5.0, 0.0, 0.0, 9.95),
        # G2 cn + cash:门控挡掉印花税(非 long),原值不变
        ("G2_cn_cash",      "600519",        "cash",  0.0, 5.0, 0.0, 0.0, 0.0),
        # G3 crypto + long:market!=cn,即便 stamp=5 也不征
        ("G3_crypto_long",  "BTC/USDT:PERP", "long", 10.0, 5.0, 0.0, 0.0, 10.0),
        # G4 cn + long + stamp=0:全 0 → 后处理跳过,字节级不变
        ("G4_cn_long_zero", "600519",        "long", 10.0, 0.0, 0.0, 0.0, 10.0),
        # G5 us + long:已上线美股,门控须为 == "cn",不得误征(防 in{cn,us} 变异)
        ("G5_us_long",      "AAPL",          "long", 10.0, 5.0, 0.0, 0.0, 10.0),
        # G6 cn + long + 三项叠加:2*(2+3)/100 + 5/100 = 0.15 → 10.0 - 0.15
        ("G6_cn_long_3way", "600519",        "long", 10.0, 5.0, 2.0, 3.0, 9.85),
    ],
)
def test_stamp_duty_gate(monkeypatch, tmp_path, label, code, position, engine_return, stamp_bps, fee_bps, slip_bps, expected):
    """A股印花税后处理门控:仅 cn+long 计卖出单边税;crypto/us/cash 不征;默认 0 字节不变。"""
    svc, saved_results = _make_intraday_svc_with_engine_return(
        monkeypatch, tmp_path, engine_return_pct=engine_return,
        fee_bps=fee_bps, slippage_bps=slip_bps,
        code=code, position=position, stamp_bps=stamp_bps,
    )
    out = svc.run_backtest(interval="5m", eval_window_days=1)
    assert out["completed"] == 1, f"{label}: expected completed=1, got {out}"
    assert len(saved_results) == 1, f"{label}: expected 1 saved, got {len(saved_results)}"
    r = saved_results[0]
    assert r.simulated_return_pct == pytest.approx(expected), (
        f"{label}: expected simulated_return_pct≈{expected}, got {r.simulated_return_pct}"
    )
```

- [ ] **Step 3: 运行验证失败**

Run: `.venv/bin/python -m pytest "tests/test_backtest_service_intraday.py::test_stamp_duty_gate" -v`
Expected: FAIL — `G1_cn_long` 期望 9.95 实得 10.0（门控尚未实现，印花税未扣）

- [ ] **Step 4: 改实现 — 后处理门控**（`src/services/backtest_service.py:304-312` 整体替换）

把：

```python
                # ★ Task 6: 分钟路径可选成本后处理（日线路径不变）
                if intraday:
                    from src.core.intraday_backtest import apply_round_trip_cost
                    _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
                    _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
                    if _fee or _slip:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip
                        )
```

改为：

```python
                # ★ Task 6: 分钟路径可选成本后处理（日线路径不变）
                if intraday:
                    from src.core.intraday_backtest import apply_round_trip_cost
                    _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
                    _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
                    # A股印花税:卖出单边,仅 cn 且 long 仓出场计征(cash/crypto/us 不征)
                    _stamp = 0.0
                    if market == "cn" and evaluation.get("position_recommendation") == "long":
                        _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
                    if _fee or _slip or _stamp:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp
                        )
```

- [ ] **Step 5: 运行验证通过（含既有成本测不回归）**

Run: `.venv/bin/python -m pytest "tests/test_backtest_service_intraday.py::test_stamp_duty_gate" "tests/test_backtest_service_intraday.py::test_cost_zero_keeps_return_unchanged" "tests/test_backtest_service_intraday.py::test_cost_positive_deducts_round_trip" -v`
Expected: PASS（G1–G6 全绿 + 既有两成本测不回归）

- [ ] **Step 6: 提交**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_intraday.py
git commit -m "feat: A股盘中印花税后处理门控(仅 cn+long 计卖出单边税)"
```

---

### Task 4: Web/API 暴露（config_registry 注册项 + settingsHelp.ts locale）

**Files:**
- Modify: `src/core/config_registry.py:3400`（在 `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` 条目之后、`INTRADAY_BACKTEST_ENABLED` 之前插入）
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts:945`（在 `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` 条目之后、`INTRADAY_BACKTEST_ENABLED` 之前插入）
- Verify: `tests/test_config_registry.py`（既有 `test_registry_help_keys_exist_in_locales` / `test_web_settings_visible_fields_have_help_metadata` / `test_locale_help_keys_are_registry_or_llm_channel_internal` 实现后由红转绿，不新增用例）

**Interfaces:**
- Consumes: Task 2 的 env 键名 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`（registry 键与 help_key 须与之一致）。

> **顺序说明：** 注册项与 locale 必须**同一提交内一起加**——只加 registry 会触发 `test_registry_help_keys_exist_in_locales` 红；只加 locale 会触发 `test_locale_help_keys_are_registry_or_llm_channel_internal` 红。故下面先各自落地，再一次性跑测试。

- [ ] **Step 1: 加 config_registry 注册项**（`src/core/config_registry.py`，紧跟 `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` 条目 `},` 之后）

```python
    "ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS": {
        "title": "A-share Intraday Stamp Duty (bps)",
        "description": "A股盘中回测卖出单边印花税(基点);默认 0=理想化;A股现行 5bps(0.05%);佣金另走 fee/slippage。",
        "category": "backtest",
        "data_type": "number",
        "ui_control": "number",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "0",
        "options": [],
        "validation": {"min": 0},
        "display_order": 84,
        "help_key": "settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS",
        "examples": ["ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=5"],
        "docs": [
            {
                "label": "完整指南：回测配置",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#回测功能",
            },
        ],
        "warning_codes": [],
    },
```

- [ ] **Step 2: 加 settingsHelp.ts locale 条目**（`apps/dsa-web/src/locales/settingsHelp.ts`，紧跟 `'settings.backtest.CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS'` 条目 `},` 之后）

```typescript
  'settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS': {
    title: 'A股盘中印花税',
    summary: 'A股盘中回测卖出单边印花税（基点），默认 0 表示理想化无成本回测。',
    usage: '默认 0；A股现行印花税为卖出单边 5bps（0.05%）。仅 A股盘中回测的多头(long)出场计征一次。',
    valueNotes: [
      '1 基点 = 0.01%；A股现行 5bps = 0.05%（2023-08 起由 0.1% 下调）。',
      '仅卖出单边计一次；佣金/滑点仍走盘中手续费/滑点（跨市场共享），A股真实总成本需一并设。',
    ],
    impact: ['影响 A股盘中回测的收益率和胜率计算。'],
    notes: ['仅影响盘中回测结果，不影响真实下单；crypto/美股不征此税。'],
  },
```

- [ ] **Step 3: 后端验证 registry↔locale 一致性转绿**

Run: `.venv/bin/python -m pytest tests/test_config_registry.py -v`
Expected: PASS（`test_registry_help_keys_exist_in_locales`、`test_web_settings_visible_fields_have_help_metadata`、`test_locale_help_keys_are_registry_or_llm_channel_internal` 全绿）

- [ ] **Step 4: 前端 web-gate（因 Web 暴露必跑）**

> 须在**无空格 worktree** 内执行（路径含空格会致 `npm ci` 残缺安装）。subagent-driven 执行时 worktree 已建在 `/root/<name>`。

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint 0 error、build 成功（settingsHelp.ts 为合法 TS 对象，无语法错误）

- [ ] **Step 5: 提交**

```bash
git add src/core/config_registry.py apps/dsa-web/src/locales/settingsHelp.ts
git commit -m "feat: A股印花税 knob 注册 config_registry + Web settingsHelp locale"
```

---

### Task 5: 文档（盘中回测专题 + CHANGELOG）

**Files:**
- Modify: `docs/intraday-backtest.md`（§5 成本小节、§10.5 限制项、§12 配置表）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

**Interfaces:** 无代码接口；仅文案，须与已实现键名/默认值/语义一致。

- [ ] **Step 1: 更新 §5 成本小节**（`docs/intraday-backtest.md:106-110`）

把：

```
成本后处理公式：

```
net_return = gross_return − 2 × (fee_bps + slippage_bps) / 100
```
```

改为：

```
成本后处理公式（手续费/滑点对称双边，印花税单边）：

```
net_return = gross_return − 2 × (fee_bps + slippage_bps) / 100 − 1 × stamp_duty_bps / 100
```

**A股卖出印花税（单边，opt-in）：** A股现行印花税为**卖出单边 5bps（0.05%）**。设 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=5` 后，仅 A股盘中回测的多头(long)出场会额外扣一次卖出税；crypto/美股/cash/日线一律不征。注意 `fee/slippage` 是跨市场共享的对称佣金分量（默认 0），A股真实总成本需**同时**设 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`。该 knob 也可在 Web 设置页 Backtest 分类直接调整。
```

- [ ] **Step 2: 更新 §10.5 限制项**（`docs/intraday-backtest.md:272`）

把：

```
- **印花税未建模**：A 股卖出印花税（单边）等不对称成本暂未单独建模，成本开关沿用 crypto 的对称 `fee/slippage`（默认 0）。
```

改为：

```
- **印花税可选建模（默认 0）**：A 股卖出印花税（单边）已由 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 支持（opt-in，默认 0，仅 cn + long 出场计征；现行 5bps）；佣金等其它分量仍沿用跨市场共享的对称 `fee/slippage`（默认 0）。过户费（沪市）等暂未单独建模。
```

- [ ] **Step 3: 更新 §12 配置表**（`docs/intraday-backtest.md:352` 之后插入一行）

把：

```
| `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` | `0` | 单边滑点（基点，默认 0 理想化） |
| `INTRADAY_BACKTEST_ENABLED` | `false` | 是否启用后台定时任务 |
```

改为：

```
| `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` | `0` | 单边滑点（基点，默认 0 理想化） |
| `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS` | `0` | A股卖出单边印花税（基点，默认 0；现行 5；仅 cn+long） |
| `INTRADAY_BACKTEST_ENABLED` | `false` | 是否启用后台定时任务 |
```

并把 §12 末句（`docs/intraday-backtest.md:356`）：

```
所有配置项均有合理默认值，**不配置即可运行**，现有行为不变。A 股分钟回测复用 `TUSHARE_TOKEN` 与上述 intraday 配置，**本阶段不新增配置项**。
```

改为：

```
所有配置项均有合理默认值，**不配置即可运行**，现有行为不变。A 股分钟回测复用 `TUSHARE_TOKEN` 与上述 intraday 配置；A股卖出印花税为可选追加项 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`（默认 0，不配置即字节级现状）。
```

- [ ] **Step 4: 加 CHANGELOG 扁平条目**（`docs/CHANGELOG.md` 的 `## [Unreleased]` 段首条之前插入，扁平格式，不加 `### 标题`）

在 `## [Unreleased]` 行（`docs/CHANGELOG.md:10`）与其后第一条 `- [新功能]`（`:12`）之间插入：

```
- [新功能] A股盘中回测支持卖出单边印花税：新增 ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS（opt-in 默认 0，现行 5bps），仅 cn + long 仓出场计征一次卖出税，crypto/美股/cash/日线不征；扩展成本枢纽 apply_round_trip_cost 追加 sell_side_bps 单边分量；随 config_registry 注册经 /config/schema 暴露并在 Web 设置页 Backtest 分类可调；佣金仍走跨市场共享的 fee/slippage，默认 0 时数值字节级不变
```

- [ ] **Step 5: 核对一致性**

Run: `grep -n "ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS" docs/intraday-backtest.md docs/CHANGELOG.md .env.example src/config.py src/core/config_registry.py`
Expected: 键名在 5 个文件中出现且拼写一致（文档 3+ 处、.env.example 1、config.py 2、config_registry 2）

- [ ] **Step 6: 提交**

```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md
git commit -m "docs: A股盘中印花税 knob 文档(成本小节/限制/配置表) + CHANGELOG"
```

---

## 全量门禁（全部任务完成后）

- [ ] **后端 ci_gate**

Run: `./scripts/ci_gate.sh`
Expected: flake8 0 error；`pytest -m "not network"` 全绿（passed 数 = 基线 + 新增；记录增量）

- [ ] **前端 web-gate**（无空格 worktree 内）

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint 0 error、build 成功

---

## Self-Review（plan vs. spec）

**1. Spec 覆盖：**
- §2 C1（`sell_side_bps`）→ Task 1 ✅
- §3 C2（cn+long 门控）→ Task 3 ✅
- §4.1 config.py 字段+加载器 → Task 2 ✅
- §4.2 `.env.example` 独立文件 → Task 2 Step 5 ✅
- §4.3 config_registry 完整 schema → Task 4 Step 1 ✅
- §4.4 settingsHelp.ts locale（唯一 locale 文件）→ Task 4 Step 2 ✅
- §5 行为/兼容/口径 → Task 5（docs）✅
- §6.1 T1–T4 → Task 1 ✅；§6.2 G1–G6 → Task 3（含 G5 us、G6 三项叠加）✅；§6.3 门禁 → 全量门禁段 ✅
- §7 文件清单 → 文件结构表全覆盖 ✅
- §9 YAGNI（不动日线/不修 §0.4 cash/无 CLI flag）→ 计划无相关任务 ✅

**与 spec 的两处事实订正（实现层细化，已在对应任务标注）：**
- `tests/test_config_intraday_backtest.py` **已存在**（spec §7 描述为新字段单测）→ Task 2 改为**扩展**既有两测，不新建文件。
- locale 文件仅 `settingsHelp.ts` 一个（spec §4.4 留了「多兄弟文件」余地）→ Task 4 只改这一个。
- G1–G6 **归集**到 `test_backtest_service_intraday.py` 既有成本 helper（spec §6.2 曾提 cn/us 各用其市场文件模板）→ 经内聚/DRY 权衡改为单文件参数化（`code` 驱动 `market_of`），市场覆盖不减。已在 Task 3 显式说明。

**2. Placeholder 扫描：** 无 TBD/TODO；每个改码步骤均给出完整 old→new 代码块与确切命令/预期。

**3. 类型/命名一致性：** `apply_round_trip_cost(..., sell_side_bps=)`（Task 1 定义）= Task 3 调用；`ashare_intraday_backtest_stamp_duty_bps`（Task 2）= Task 3 monkeypatch/getattr；env 键 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 与 help_key `settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`（Task 2/4）三处逐字一致；`display_order=84`（承 fee=82/slip=83）。
