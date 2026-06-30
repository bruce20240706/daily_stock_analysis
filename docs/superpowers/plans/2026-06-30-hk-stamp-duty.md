# 港股(HK)盘中回测双边印花税 knob 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 opt-in 港股盘中回测印花税 knob `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`(默认 0),按 HK 税制**买卖双边对称(×2)**计征,补全港股成本模型。

**Architecture:** 给 `apply_round_trip_cost` 加 `both_side_bps`(×2)参数;`backtest_service` 成本块加 `elif market=="hk"` 分支走 `both_side_bps`;config 三处同步(config.py + config_registry@86 全量镜像 A股 + settingsHelp.ts locale 含 impact)+ `.env.example`;镜像 2026-06-29 A股单边印花税那套,唯一本质差异=双边对称(×2 vs A股 ×1)。

**Tech Stack:** Python(pytest);成本为纯计算后处理;config_registry 注册触发 /config/schema + Web 设置页;settingsHelp.ts 改动触发 web-gate。

## Global Constraints

- **双边对称:** HK 印花税现行 **10bps(0.1%)**,买入与卖出**各计一次** → `both_side_bps` 按 **×2** 计入(同 fee/slip 桶);A股是卖出**单边 ×1**(`sell_side_bps`),勿混。
- **opt-in 默认 0:** 默认 0 → `apply_round_trip_cost` 早退守卫 + 后处理早退条件均短路 → 所有既有路径(crypto/cn/us/日线)**字节级不变**。
- **门控:** `market == "hk"` + **确有成交**(`simulated_entry_price is not None`),**不限 long**(HK 在链路A 永不可达 short,今日功能等同 long-only,entry-based 仅为前向防御);`cn`/`hk` 互斥(elif),无双计;crypto/us/cash/日线不征。
- **config 三处 + env 全同步、全量镜像 A股:** config_registry 条目**必含 examples/docs/options/warning_codes**(否则 `test_web_settings_visible_fields_have_help_metadata` 红);**display_order=86**(85 已被 INTRADAY_BACKTEST_SCHEDULE_MINUTES 占用,无唯一性测试 CI 不拦,须用真空位);settingsHelp.ts 必含 `impact` 字段且 key 与 help_key 同名(否则 registry↔locale 一致性测试两红)。
- **parse_env_float:** `minimum=0.0` 把负值**钳制**到 0.0(记 warning,不抛错、不回退 default)。
- **commit message:** 英文类型前缀 + 中文体,不加 `Co-Authored-By`,不加工具/agent 前缀。
- **CHANGELOG `[Unreleased]` 扁平**(`- [类型] 描述`,无 `### 标题`)。

**关联 spec:** `docs/superpowers/specs/2026-06-30-hk-stamp-duty-design.md`(经对抗式审查 4 视角核验,0 Blocker,2 Important + 5 Minor 已折入)。

---

## 文件结构(改动面)

| 文件 | 改动 | Task |
|---|---|---|
| `src/core/intraday_backtest.py` | `apply_round_trip_cost` 加 `both_side_bps`(×2)+ 早退守卫 + 公式 + docstring | 1 |
| `tests/test_intraday_backtest_helpers.py` | 函数级 both_side 测试(追加) | 1 |
| `src/config.py` | attr `hk_intraday_backtest_stamp_duty_bps` + load | 2 |
| `src/core/config_registry.py` | `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 全量条目(display_order=86) | 2 |
| `apps/dsa-web/src/locales/settingsHelp.ts` | `'settings.backtest.HK_...'` locale(含 impact) | 2 |
| `.env.example` | `# HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0` | 2 |
| `tests/test_config_intraday_backtest.py` | HK env 解析测(默认 0 + override + 负值钳制) | 2 |
| `src/services/backtest_service.py` | 成本块加 `_hk_stamp`(elif market=="hk")+ 早退条件 + 传 `both_side_bps` | 3 |
| `tests/test_backtest_service_intraday.py` | harness 加 `hk_stamp_bps` + 新参数化 `test_hk_stamp_duty_gate` | 3 |
| `docs/intraday-backtest.md` / `docs/CHANGELOG.md` | §12 成本节 + §5 公式 + hold→long 注记 + CHANGELOG | 4 |

**任务顺序:** 1(函数)→ 2(config,使 attr 存在)→ 3(门控 + 集成,消费 1+2)→ 4(docs)。

---

### Task 1: `apply_round_trip_cost` 加 `both_side_bps`(×2)

**Files:**
- Modify: `src/core/intraday_backtest.py:60-80`(函数体)
- Test: `tests/test_intraday_backtest_helpers.py`(追加,既有 apply_round_trip_cost 测试在 :45-128)

**Interfaces:**
- Produces: `apply_round_trip_cost(return_pct, fee_bps, slippage_bps, sell_side_bps=0.0, both_side_bps=0.0)` —— Task 3 的门控以 `both_side_bps=_hk_stamp` 消费。

- [ ] **Step 1: 追加失败测试**(`tests/test_intraday_backtest_helpers.py`,在既有 `test_apply_round_trip_cost_*` 末尾,即 `:128` 之后追加)

```python
def test_apply_round_trip_cost_both_side_doubles():
    # both_side 10bp 买卖各一次 → 2*10/100 = 0.20
    assert apply_round_trip_cost(10.0, 0.0, 0.0, both_side_bps=10.0) == pytest.approx(9.80)


def test_apply_round_trip_cost_both_side_with_sell_side():
    # both_side ×2 + sell_side ×1 叠加:2*10/100 + 1*5/100 = 0.25
    assert apply_round_trip_cost(10.0, 0.0, 0.0, sell_side_bps=5.0, both_side_bps=10.0) == pytest.approx(9.75)


def test_apply_round_trip_cost_both_side_with_fee_slip():
    # both_side 与 fee/slip 同入 ×2 桶:2*(2+3+10)/100 = 0.30
    assert apply_round_trip_cost(10.0, 2.0, 3.0, both_side_bps=10.0) == pytest.approx(9.70)


def test_apply_round_trip_cost_both_side_default_byte_identical():
    # 不传 both_side_bps:全 0 走早退守卫,字节级不变
    assert apply_round_trip_cost(10.0, 0.0, 0.0) == pytest.approx(10.0)
    assert apply_round_trip_cost(7.7, 2.0, 3.0) == pytest.approx(7.6)


def test_apply_round_trip_cost_both_side_none_passthrough():
    assert apply_round_trip_cost(None, 0.0, 0.0, both_side_bps=10.0) is None
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py -q -k both_side`
Expected: FAIL(`apply_round_trip_cost() got an unexpected keyword argument 'both_side_bps'`)。

- [ ] **Step 3: 改实现**(`src/core/intraday_backtest.py:60-80`)

把:
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
改为:
```python
def apply_round_trip_cost(
    return_pct: Optional[float],
    fee_bps: float,
    slippage_bps: float,
    sell_side_bps: float = 0.0,
    both_side_bps: float = 0.0,
) -> Optional[float]:
    """对一进一出收益(百分比)扣减成本。默认全 0 → 原样返回(行为不变)。

    - fee_bps / slippage_bps：对称双边成本,一进一出各计一次 → ×2(基点,1bp=0.01%)。
    - sell_side_bps：单边卖出成本(如 A股印花税),仅出场计一次 → ×1。调用方负责仅在
      「确有卖出」(long 出场)时传非 0;cash/无成交不应传。
    - both_side_bps：对称双边成本(如港股印花税,买入与卖出各计一次)→ ×2。调用方负责仅在
      「确有成交」时传非 0;cash/无成交不应传。
    """
    if return_pct is None:
        return None
    if not fee_bps and not slippage_bps and not sell_side_bps and not both_side_bps:
        return return_pct
    cost_pct = (
        2.0 * (float(fee_bps) + float(slippage_bps) + float(both_side_bps)) / 100.0
        + 1.0 * float(sell_side_bps) / 100.0
    )
    return return_pct - cost_pct
```

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_intraday_backtest_helpers.py -q`
Expected: 全 PASS(新 both_side 用例 + 既有 sell_side/fee-slip 用例不回归)。

- [ ] **Step 5: 提交**

```bash
git add src/core/intraday_backtest.py tests/test_intraday_backtest_helpers.py
git commit -m "feat: apply_round_trip_cost 加 both_side_bps 对称双边成本(港股印花税×2)"
```

---

### Task 2: 配置接线(config.py + config_registry@86 + settingsHelp.ts + .env.example)

**Files:**
- Modify: `src/config.py:908`(attr)、`:1910-1913` 后(load)
- Modify: `src/core/config_registry.py`(在 ASHARE 条目之后插入 HK 条目)
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts`(在 ASHARE locale 之后插入 HK locale)
- Modify: `.env.example:717` 后
- Test: `tests/test_config_intraday_backtest.py`

**Interfaces:**
- Produces: `config.hk_intraday_backtest_stamp_duty_bps: float`(默认 0.0)—— Task 3 门控以 `getattr(config, "hk_intraday_backtest_stamp_duty_bps", 0.0)` 消费;集成测以 `monkeypatch.setattr(cfg, "hk_intraday_backtest_stamp_duty_bps", ...)` 设置(故 attr 必须存在)。

- [ ] **Step 1: 写失败测试**(`tests/test_config_intraday_backtest.py`,镜像 A股解析测)

在 `test_intraday_defaults_are_status_quo`(:22)的默认断言处追加 HK 默认断言;新增 override + 负值钳制测试。具体:在 `:41`(`assert cfg.ashare_intraday_backtest_stamp_duty_bps == 0.0`)之后追加一行:
```python
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 0.0
```
并在 `test_intraday_env_override`(:44)的 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS="5"`(:51)同一 env 设置块加入 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS="10"`,并在断言区(:57 附近)追加:
```python
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 10.0
```
并在文件末尾新增负值钳制测试:
```python
def test_hk_stamp_duty_negative_clamped_to_zero(monkeypatch):
    # parse_env_float minimum=0.0:负值被钳制到 0.0(记 warning,不抛错、不回退 default)
    monkeypatch.setenv("HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS", "-3")
    from src.config import Config
    Config._instance = None
    cfg = Config.from_env()
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 0.0
```
(注:若 `test_intraday_env_override` 的构造方式不是直接 setenv,请按该文件既有 A股 override 的同形写法对齐 —— 读 `:44-57` 确认其 env 注入方式后镜像。负值测试的 `Config.from_env()` 调用方式同样以该文件既有用法为准。)

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_config_intraday_backtest.py -q`
Expected: FAIL(`AttributeError: ... 'hk_intraday_backtest_stamp_duty_bps'` / 默认值断言失败)。

- [ ] **Step 3: 改实现 — config.py attr**(`src/config.py:908` 之后)

把:
```python
    # A股盘中回测卖出单边印花税(基点);默认 0=理想化无成本;A股现行 5bps(0.05%)
    ashare_intraday_backtest_stamp_duty_bps: float = 0.0
```
改为:
```python
    # A股盘中回测卖出单边印花税(基点);默认 0=理想化无成本;A股现行 5bps(0.05%)
    ashare_intraday_backtest_stamp_duty_bps: float = 0.0
    # 港股盘中回测买卖双边印花税(基点,×2);默认 0=理想化无成本;HK 现行 10bps(0.1%)
    hk_intraday_backtest_stamp_duty_bps: float = 0.0
```

- [ ] **Step 4: 改实现 — config.py load**(`src/config.py:1910-1913` 的 ashare load 块之后)

把:
```python
            ashare_intraday_backtest_stamp_duty_bps=parse_env_float(
                os.getenv('ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0,
                field_name='ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS', minimum=0.0,
            ),
```
改为:
```python
            ashare_intraday_backtest_stamp_duty_bps=parse_env_float(
                os.getenv('ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0,
                field_name='ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS', minimum=0.0,
            ),
            hk_intraday_backtest_stamp_duty_bps=parse_env_float(
                os.getenv('HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0,
                field_name='HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS', minimum=0.0,
            ),
```

- [ ] **Step 5: 改实现 — config_registry 全量条目**(`src/core/config_registry.py`,在 `"ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS"` 条目的闭合 `},` 之后、`"INTRADAY_BACKTEST_ENABLED"` 之前插入)

```python
    "HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS": {
        "title": "HK Intraday Stamp Duty (bps)",
        "description": "港股盘中回测买卖双边印花税(基点,买卖各计一次=×2);默认 0=理想化;HK 现行 10bps(0.1%);佣金另走 fee/slippage。",
        "category": "backtest",
        "data_type": "number",
        "ui_control": "number",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "0",
        "options": [],
        "validation": {"min": 0},
        "display_order": 86,
        "help_key": "settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS",
        "examples": ["HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=10"],
        "docs": [
            {
                "label": "完整指南：回测配置",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#回测功能",
            },
        ],
        "warning_codes": [],
    },
```
(display_order=86 已核验空位;85=INTRADAY_BACKTEST_SCHEDULE_MINUTES。docs href 与 A股条目逐字一致。)

- [ ] **Step 6: 改实现 — settingsHelp.ts locale**(`apps/dsa-web/src/locales/settingsHelp.ts`,在 `'settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'` 条目闭合 `},` 之后插入)

```typescript
  'settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS': {
    title: '港股盘中印花税',
    summary: '港股盘中回测买卖双边印花税（基点），默认 0 表示理想化无成本回测。',
    usage: '默认 0；HK 现行印花税为买卖双边各 10bps（0.1%）。仅港股盘中回测的成交标的计征，买卖各一次（×2）。',
    valueNotes: [
      '1 基点 = 0.01%；HK 现行 10bps = 0.1%（买入与卖出各计一次，合计 ×2）。',
      '区别于 A股单边：港股买卖双边均计；佣金/滑点仍走盘中手续费/滑点（跨市场共享），港股真实总成本需一并设。',
    ],
    impact: ['影响 港股盘中回测的收益率和胜率计算。'],
    notes: ['仅影响盘中回测结果，不影响真实下单；crypto/A股/美股不征此税。'],
  },
```

- [ ] **Step 7: 改实现 — .env.example**(`:717` 的 ashare 行之后追加)

把:
```
# ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0
```
改为:
```
# ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0
# 港股盘中回测买卖双边印花税(基点);默认 0;HK 现行 10bps(0.1%,买卖各计一次=×2)
# HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0
```

- [ ] **Step 8: 运行验证通过(含 registry↔locale 一致性 + .env.example 互锁)**

Run: `.venv/bin/python -m pytest tests/test_config_intraday_backtest.py tests/test_config_registry.py -q`
Expected: 全 PASS(HK 解析测 + `test_registry_help_keys_exist_in_locales` + `test_web_settings_visible_fields_have_help_metadata` + `test_active_env_example_keys_are_registered_or_hidden_from_web_ui` 均通过)。

- [ ] **Step 9: 提交**

```bash
git add src/config.py src/core/config_registry.py apps/dsa-web/src/locales/settingsHelp.ts .env.example tests/test_config_intraday_backtest.py
git commit -m "feat: 港股盘中印花税配置 HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS(config/registry@86/locale/env 全同步)"
```

---

### Task 3: 后处理门控放行 HK + 集成测

**Files:**
- Modify: `src/services/backtest_service.py:307-319`(成本块)
- Test: `tests/test_backtest_service_intraday.py`(harness `_make_intraday_svc_with_engine_return` 加 `hk_stamp_bps` + 新参数化 `test_hk_stamp_duty_gate`)

**Interfaces:**
- Consumes: Task 1 `apply_round_trip_cost(..., both_side_bps=)`、Task 2 `config.hk_intraday_backtest_stamp_duty_bps`、既有 `market_of`(:153)。

- [ ] **Step 1: 改测试 — harness 加 `hk_stamp_bps`**(`tests/test_backtest_service_intraday.py:456-487`)

把 harness 签名(:456-460):
```python
def _make_intraday_svc_with_engine_return(
    monkeypatch, tmp_path, engine_return_pct: float,
    fee_bps: float = 0.0, slippage_bps: float = 0.0,
    *, code: str = "BTC/USDT:PERP", position: str = "long", stamp_bps: float = 0.0,
):
```
改为(加 `hk_stamp_bps`):
```python
def _make_intraday_svc_with_engine_return(
    monkeypatch, tmp_path, engine_return_pct: float,
    fee_bps: float = 0.0, slippage_bps: float = 0.0,
    *, code: str = "BTC/USDT:PERP", position: str = "long", stamp_bps: float = 0.0,
    hk_stamp_bps: float = 0.0,
):
```
并在 ashare monkeypatch(:487)之后追加一行:
```python
    monkeypatch.setattr(real_cfg, "ashare_intraday_backtest_stamp_duty_bps", stamp_bps)
    monkeypatch.setattr(real_cfg, "hk_intraday_backtest_stamp_duty_bps", hk_stamp_bps)
```

- [ ] **Step 2: 加失败测试 — 新参数化 `test_hk_stamp_duty_gate`**(`tests/test_backtest_service_intraday.py`,在既有 `test_stamp_duty_gate`(:609)之后追加)

```python
@pytest.mark.parametrize(
    "label,code,position,engine_return,hk_stamp_bps,ashare_stamp_bps,fee_bps,slip_bps,expected",
    [
        # HK1 hk + long:买卖双边各 10bp → 2*10/100 = 0.20 → 10.0 - 0.20
        ("HK1_hk_long",       "HK00700", "long", 10.0, 10.0, 0.0, 0.0, 0.0, 9.80),
        # HK2 hk + cash:无成交,entry None → 门控挡掉,原值不变
        ("HK2_hk_cash",       "HK00700", "cash",  0.0, 10.0, 0.0, 0.0, 0.0, 0.0),
        # HK3 hk + long + 三项叠加:both 入 ×2 桶 → 2*(2+3+10)/100 = 0.30 → 10.0 - 0.30
        ("HK3_hk_long_3way",  "HK00700", "long", 10.0, 10.0, 0.0, 2.0, 3.0, 9.70),
        # HK4 hk + long + hk_stamp=0:全 0 → 后处理早退,字节级不变
        ("HK4_hk_long_zero",  "HK00700", "long", 10.0,  0.0, 0.0, 0.0, 0.0, 10.0),
        # HK5(反例) cn + long + hk_stamp=10(ashare=0):cn 走 if 分支不取 hk → 不计征
        ("HK5_cn_long",       "600519",  "long", 10.0, 10.0, 0.0, 0.0, 0.0, 10.0),
        # HK6(反例) us + long + hk_stamp=10:market!=hk(也!=cn)→ 不计征
        ("HK6_us_long",       "AAPL",    "long", 10.0, 10.0, 0.0, 0.0, 0.0, 10.0),
        # HK7(反例) crypto + long + hk_stamp=10:market!=hk → 不计征
        ("HK7_crypto_long",   "BTC/USDT:PERP", "long", 10.0, 10.0, 0.0, 0.0, 0.0, 10.0),
    ],
)
def test_hk_stamp_duty_gate(monkeypatch, tmp_path, label, code, position, engine_return, hk_stamp_bps, ashare_stamp_bps, fee_bps, slip_bps, expected):
    """港股双边印花税后处理门控:仅 hk+确有成交计双边税(×2);cn/us/crypto/cash 不征;默认 0 字节不变。"""
    svc, saved_results = _make_intraday_svc_with_engine_return(
        monkeypatch, tmp_path, engine_return_pct=engine_return,
        fee_bps=fee_bps, slippage_bps=slip_bps,
        code=code, position=position, stamp_bps=ashare_stamp_bps, hk_stamp_bps=hk_stamp_bps,
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

Run: `.venv/bin/python -m pytest "tests/test_backtest_service_intraday.py::test_hk_stamp_duty_gate" -q`
Expected: HK1/HK3 FAIL(门控未含 hk → hk_stamp 未计征,9.80/9.70 实得 10.0);HK2/HK4/HK5/HK6/HK7 PASS(本就不计征)。

- [ ] **Step 4: 改实现 — 门控**(`src/services/backtest_service.py:312-318`)

把:
```python
                    _stamp = 0.0
                    if market == "cn" and evaluation.get("position_recommendation") == "long":
                        _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))

                    if (_fee or _slip or _stamp) and evaluation.get("simulated_entry_price") is not None:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp
```
改为:
```python
                    _stamp = 0.0
                    _hk_stamp = 0.0
                    if market == "cn" and evaluation.get("position_recommendation") == "long":
                        _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
                    elif market == "hk":
                        # 港股印花税买卖双边对称(×2);链路A 中 hk 仅 long/cash 可达,
                        # cash 由下方 entry-gate 豁免,故 market 级门控即可(不限 long)
                        _hk_stamp = float(getattr(config, "hk_intraday_backtest_stamp_duty_bps", 0.0))

                    if (_fee or _slip or _stamp or _hk_stamp) and evaluation.get("simulated_entry_price") is not None:
                        evaluation["simulated_return_pct"] = apply_round_trip_cost(
                            evaluation.get("simulated_return_pct"), _fee, _slip,
                            sell_side_bps=_stamp, both_side_bps=_hk_stamp
```
(保留原调用的换行/缩进风格;`both_side_bps=_hk_stamp` 接在 `sell_side_bps=_stamp` 之后,闭合括号不变。实现时读 `:316-319` 确认闭合行原样。)

- [ ] **Step 5: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_backtest_service_intraday.py -q`
Expected: 全 PASS(新 `test_hk_stamp_duty_gate` 7 例 + 既有 `test_stamp_duty_gate`/`test_cost_charged_only_when_filled`/全套 intraday 不回归)。

- [ ] **Step 6: 提交**

```bash
git add src/services/backtest_service.py tests/test_backtest_service_intraday.py
git commit -m "feat: 链路A 放行港股双边印花税(market==hk+确有成交门控,both_side_bps×2)+ 集成测"
```

---

### Task 4: 文档

**Files:**
- Modify: `docs/intraday-backtest.md`(§12 成本节 + §5 成本公式 + hold→long 注记)、`docs/CHANGELOG.md`(`[Unreleased]` 扁平)

**Interfaces:** 无代码接口;文案须与已实现键名/语义一致(`HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`、双边 ×2、10bps、market==hk 门控)。

- [ ] **Step 1: `docs/intraday-backtest.md` §5 成本公式**

§5 成本后处理公式(现为 `net_return = gross_return − 2 × (fee_bps + slippage_bps) / 100 − 1 × stamp_duty_bps / 100`)补 both-side 项,改为:
```
net_return = gross_return
           − 2 × (fee_bps + slippage_bps + hk_stamp_bps) / 100   # 对称双边(含港股印花税)
           − 1 × ashare_stamp_bps / 100                          # A股卖出单边
```
并在 §5 现有「A股卖出印花税」段后追加一段「**港股双边印花税(opt-in)**:HK 现行印花税为**买卖双边各 10bps(0.1%)**。设 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=10` 后,仅港股盘中回测的**成交**标的按买卖双边各计一次(×2);crypto/A股/美股/cash/日线一律不征。区别于 A股单边。该 knob 也可在 Web 设置页 Backtest 分类调整。」

- [ ] **Step 2: `docs/intraday-backtest.md` §12 港股章成本节 + hold→long 注记**

在 §12(港股章)成本相关小节加:港股印花税双边 ×2(10bps,opt-in 默认 0,`HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`),门控 `market==hk` + 确有成交(cash 豁免);并加 hold→long 注记:「`持有/hold` 建议在回测中映射为 long 且按 `entry@start` 全 round-trip 建模,故 both-side 印花税会对 hold 也计买腿——与 A股 knob 同源(A股已对 hold→long 计卖腿),HK 仅幅度翻倍 ×2;此为回测既有约定,非本特性新增。」

- [ ] **Step 3: `docs/CHANGELOG.md`(`## [Unreleased]` 段首,扁平,无 `### 标题`)**

插入一行:
```
- [新功能] 港股盘中回测双边印花税 knob(HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS,opt-in 默认0,HK 现行10bps买卖双边对称×2,区别于A股单边;apply_round_trip_cost 加 both_side_bps;market==hk+确有成交门控不限long;Web 设置页可改;cash 豁免、cn/us/crypto/日线不征)
```

- [ ] **Step 4: 核对 + 提交**

Run: `grep -n "HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS\|双边\|both_side" docs/intraday-backtest.md docs/CHANGELOG.md | head`
Expected: §5/§12/CHANGELOG 命中,键名/语义与实现一致。

```bash
git add docs/intraday-backtest.md docs/CHANGELOG.md
git commit -m "docs: 港股双边印花税 knob 专题(§5 公式 + §12 成本节 + hold→long 注记)+ CHANGELOG"
```

---

## 全量门禁(全部任务完成后)

- [ ] **后端 ci_gate**

Run: `./scripts/ci_gate.sh`(venv 在 PATH)
Expected: flake8 0 error;`pytest -m "not network"` 全绿;passed 数 = 基线 + 新增(both_side 函数测 5 + HK config 解析 ~2 + HK 门控 7)。记录增量。

- [ ] **前端 web-gate(settingsHelp.ts 改动触发)**

Run: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
Expected: lint 0 error;build 成功。

---

## Self-Review(plan vs. spec)

**1. Spec 覆盖:**
- §3 函数 both_side_bps → Task 1 ✅
- §4 门控 elif market=="hk" + both_side_bps → Task 3 ✅
- §5 配置三处 + env(display_order=86、examples/docs/options/warning_codes 全量、impact 字段)→ Task 2 ✅
- §6 测试(函数 approx、G1-G7 含 cn/us/crypto 反例、cash 豁免、config 解析负值钳制)→ Task 1/2/3 ✅
- §7 文档(§5 公式 + §12 + hold→long 注记 + CHANGELOG)→ Task 4 ✅
- §8 YAGNI(不建模其它费项/最小收费/buy-sell 拆分/日线)→ 计划无相关改动 ✅
- §11 对抗审查 2 Important(display_order=86、examples/docs)+ 5 Minor(impact/钳制/行锚/D2 措辞/hold 注记)→ 已分别落到 Task 2/Task 3 门控注释/Task 4 ✅

**2. Placeholder 扫描:** 无 TBD;每改码步给完整 old→new + 命令/预期。Task 2 Step 1 对「既有 A股 override env 注入方式」留了「读文件确认后镜像」的指示(因该测试文件的 env 构造方式未逐字展开),非 placeholder 而是对齐既有写法的明确指令。

**3. 类型/命名一致性:** `both_side_bps`(函数,×2)↔ Task3 `both_side_bps=_hk_stamp`;`hk_intraday_backtest_stamp_duty_bps`(config attr)↔ registry key `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` ↔ locale `settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`(同名);门控 `elif market=="hk"` 与 cn `if` 互斥;HK1=9.80/HK3=9.70 与函数 ×2 公式一致;display_order=86。
