# A股不对称印花税(盘中回测、卖出单边)设计

- 主题：`ashare-stamp-duty`
- 日期：2026-06-29
- 状态：设计定稿（经对抗式审查收敛 v2，见 §10），待落实现计划
- 范围：链路A 盘中回测成本模型；A股(cn)卖出单边印花税；opt-in 默认 0
- 关联：「盘中/分钟级回测」epic 的成本真实性收尾；承接 A股盘中扩展。

---

## 0. 背景与现状

### 0.1 当前成本模型(已核验)

- 唯一成本入口：`crypto_intraday_backtest_fee_bps` / `crypto_intraday_backtest_slippage_bps`，默认 `0.0`（`src/config.py:905-906`）。
- 施加方式：`src/core/intraday_backtest.py:60-72` 的 `apply_round_trip_cost(return_pct, fee_bps, slippage_bps)`——**对称 round-trip**：

  ```python
  cost_pct = 2.0 * (float(fee_bps) + float(slippage_bps)) / 100.0
  return return_pct - cost_pct
  ```

- 施加位置：`src/services/backtest_service.py:304-312`，**仅盘中(intraday)路径**后处理 `simulated_return_pct`；**日线路径不施加任何成本**：

  ```python
  if intraday:
      from src.core.intraday_backtest import apply_round_trip_cost
      _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
      _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
      if _fee or _slip:
          evaluation["simulated_return_pct"] = apply_round_trip_cost(
              evaluation.get("simulated_return_pct"), _fee, _slip)
  ```

### 0.2 方向模型(已核验)

- A股现货走 `BacktestEngine.infer_position_recommendation`（`src/core/backtest_engine.py:134-154`）→ **long-only**（返回 `long` / `cash`，**永不返回 short**）。
- `evaluate_single` 返回 dict 含 `"position_recommendation": position`（`backtest_engine.py:323`）。
- cash 仓：`simulated_entry_price=None`、`simulated_return_pct=0.0`（`backtest_engine.py:283-285`）。

### 0.3 现实与缺口

A股**印花税(stamp duty)对卖方单边征收**，现行 **0.05% = 5bps**（2023-08 起由 0.1% 下调）。当前对称 fee 模型把成本按「一进一出 ×2」施加，无法表达卖出单边的印花税。本特性补一个**卖出单边**成本分量，仅 A股、仅盘中、opt-in。

### 0.4 cash 误扣的既有行为(不在本特性修复范围)

现有后处理对 cash 仓（`simulated_return_pct=0.0`）也会扣 `apply_round_trip_cost`（无 position 门控）→ `0.0 - cost`。这是**既有行为**，本特性**不改**它（属对称 fee/slippage 语义，改动会扩面）。本特性只保证**印花税分量只对真实卖出（long 出场）计征**，不向 cash 引入新的错误扣减。

---

## 1. 设计决策

- **D1 作用范围**：仅盘中路径（与现有 fee/slippage 一致；日线路径零改）。
- **D2 默认值**：`0`（opt-in，与 fee/slippage 一致）；**不配置即字节级现状**；文档标明现行 5bps 供用户设。
- **D3 机制**：扩展现有成本枢纽 `apply_round_trip_cost`，追加式可选参 `sell_side_bps`（单边 ×1），**不造平行函数**。
  - 否决备选：①独立 `apply_sell_side_cost` 函数（多一个并列函数，分散成本逻辑）；②放进 `evaluate_single`（混入引擎、需把 market 穿进纯引擎，破坏其市场无关性）。扩枢纽最 DRY、改动面最小。
- **D4 门控**：仅 `market == "cn"` 且 `position_recommendation == "long"` 时计征；cash / crypto / us / short(A股不可能) 一律不征。

---

## 2. 改动 C1：`apply_round_trip_cost` 增 `sell_side_bps`（`src/core/intraday_backtest.py`）

```python
def apply_round_trip_cost(
    return_pct: Optional[float],
    fee_bps: float,
    slippage_bps: float,
    sell_side_bps: float = 0.0,
) -> Optional[float]:
    """对一进一出收益(百分比)扣减成本。默认全 0 → 原样返回(行为不变)。

    - fee_bps / slippage_bps：对称双边成本，一进一出各计一次 → ×2。
    - sell_side_bps：单边卖出成本(如 A股印花税)，仅出场计一次 → ×1。调用方负责
      仅在「确有卖出」(long 出场)时传非 0；cash/无成交不应传。
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

**字节级不变证明**：新增参数默认 `0.0`；`if not fee_bps and not slippage_bps and not sell_side_bps` 守卫在旧调用（不传 sell_side_bps → 0.0）下与旧 `if not fee_bps and not slippage_bps` 等价；`cost_pct` 的 `+ 1.0*0.0/100` 恒为 0。既有两参调用结果逐字节一致。

**口径说明**：卖出税本应以**出场名义额**计，此处以 bps 施加于以**入场名义额**为基的收益百分比（`(exit-entry)/entry*100`），是与既有 round-trip 模型一致的标准近似（既有 fee 亦以 bps×2 近似双边名义额）。近似误差 ≈ `stamp_bps × |涨跌幅|`：5bps 印花税即便 ±100% 行情亦 ≤0.05%，可忽略。

---

## 3. 改动 C2：后处理门控（`src/services/backtest_service.py`）

把 `:304-312` 后处理块改为：

```python
# ★ 分钟路径可选成本后处理（日线路径不变）
if intraday:
    from src.core.intraday_backtest import apply_round_trip_cost
    _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
    _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
    # A股印花税：卖出单边，仅 cn 且 long 仓出场计征(cash/crypto/us 不征)
    _stamp = 0.0
    if market == "cn" and evaluation.get("position_recommendation") == "long":
        _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
    if _fee or _slip or _stamp:
        evaluation["simulated_return_pct"] = apply_round_trip_cost(
            evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp)
```

**`market` 来源**：盘中路径已在入场价块（`backtest_service.py:151` 附近）算出 `market = market_of(analysis.code)`（返回 `crypto`/`cn`/`us`；到达本后处理即该赋值已执行，因 insufficient_data 分支在其后 `continue`）。本块复用该 `market` 变量，不重算。

**门控正确性**：
- cn + long → `_stamp` 取配置值（默认 0）；确有买入→卖出，计征一次卖出税。
- cn + cash → 不进 `if` 分支，`_stamp=0`，印花税不征（cash 无成交）。既有 fee/slippage 对 cash 的扣减行为不变（§0.4）。
- crypto / us → `market != "cn"`，`_stamp=0`，不征。
- 默认 0：`_stamp=0`，且 fee/slip 默认 0 → `if _fee or _slip or _stamp` 为假 → 后处理跳过，字节级不变。

---

## 4. 配置与 Web 暴露（新增 1 项，默认 0；与 fee/slippage 同为 Web 可调）

⚠ **关键事实（对抗审查纠正）**：`config_registry` 注册项会经 `/api/v1/system/config`、`/config/schema` API 暴露，并在 **Web 设置页 Backtest 分类**渲染可改（`build_schema_response` 全量遍历 `_FIELD_DEFINITIONS` 无隐藏过滤；`WEB_SETTINGS_HIDDEN_FROM_UI` 是零引用死常量）。fee/slippage 本就如此。故本 knob 与它们一致 = **Web/API 可见可调**，须配套 `settingsHelp.ts` locale 并跑 web-gate。**四处同步**：

### 4.1 `src/config.py`（数据类字段 + 加载器）

- 数据类字段（紧邻 `crypto_intraday_backtest_*`，约 `:905-906`）：

  ```python
  ashare_intraday_backtest_stamp_duty_bps: float = 0.0
  ```

- 加载器（紧邻 crypto 的 `parse_env_float`，约 `:1900-1908`）：

  ```python
  ashare_intraday_backtest_stamp_duty_bps=parse_env_float(
      os.getenv('ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0,
      field_name='ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS', minimum=0.0,
  ),
  ```

### 4.2 `.env.example`（仓库根**独立文件**，不在 config.py 内）

紧邻 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS` 注释（`.env.example` 约 `:712-713`）新增：

```
# A股盘中回测卖出单边印花税(基点，仅分钟级生效)；默认 0=理想化无成本；A股现行 5(0.05%)
# 注：佣金/双边成本仍走 CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS(跨市场共享)，A股真实总成本需一并设
# ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0
```

### 4.3 `src/core/config_registry.py`（注册项，完整 schema）

复制 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS` 注册项（`:3355-3377`）整体结构后改键值，**逐字段对齐 registry 实际键**（非 config.py loader 语义）：
- 键名 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`；`category="backtest"`、`data_type="number"`、`ui_control="number"`
- `is_sensitive=False`、`is_required=False`、`is_editable=True`
- `default_value="0"`（**字符串**）、`validation={"min": 0}`
- `display_order=84`（承 fee=82 / slippage=83）
- `help_key="settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS"`、`examples`、`docs`、`warning_codes=[]`、`title`、`description`
- 描述要点：A股盘中回测卖出单边印花税(基点);默认 0=理想化;A股现行 5bps(0.05%);**佣金另走 fee/slip**。

### 4.4 `apps/dsa-web/src/locales/settingsHelp.ts`（Web locale，**测试强制**）

镜像 `'settings.backtest.CRYPTO_INTRADAY_BACKTEST_FEE_BPS'`（`settingsHelp.ts:924`）新增 `'settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'`，含其**全部子键**（以该 fee 条目实际结构为准；若存在多 locale 兄弟文件须一并加）。**否则 `test_registry_help_keys_exist_in_locales` / `test_web_settings_visible_fields_have_help_metadata` 必红**（ci_gate 阻断）。

---

## 5. 行为变更与兼容性

- **默认 0 → 后处理数值字节级不变**（C1 守卫 + C2 `if` 跳过）。crypto/us/日线/cash 的 `simulated_return_pct` 不受影响。
- **配置 >0 后**：仅 A股(cn) 盘中回测的 **long 仓**样本，其 `simulated_return_pct` 额外扣一次卖出税（与对称 fee/slippage 叠加）。
- **用户可见 / Web-API 暴露（对抗审查纠正，原 spec 误判为 config-only）**：新 knob 随 config_registry 注册经 `/config`、`/config/schema` API 暴露，并在 **Web 设置页 Backtest 分类可见可改**（同 fee/slippage）。属用户可见能力变化 → 须 `docs/CHANGELOG.md` 记「[新功能]」、跑 **web-gate**、补 `settingsHelp.ts` locale。对 API schema 是**追加字段**（老客户端忽略未知字段，无破坏）。
- **无 DB schema 迁移 / 无 BacktestResult 新字段**（内部扣进 `simulated_return_pct`，同 fee）。
- **成本口径澄清（重要）**：fee/slippage 是 `crypto_*` 命名但**跨市场全局**（无 market 门控，crypto/cn/us 共享同一 bps）；本 knob 只补**卖出印花税**分量。用户若只设 `ASHARE_..._STAMP_DUTY_BPS=5`，得到的是「仅卖出印花税、零佣金」；A股真实总成本需**同时**设 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`（佣金双边）。文档须点明，避免「成本真实性」被误读。
- 文档：`docs/intraday-backtest.md` 成本小节补该 knob + A股 5bps + 佣金另设说明；`docs/CHANGELOG.md` 扁平条目。

---

## 6. 测试设计

### 6.1 `apply_round_trip_cost` 单测（纯函数，确定性）

⚠ **浮点**：扣减结果一律用 `== pytest.approx(...)`（与既有 `tests/test_intraday_backtest_helpers.py` 一致；裸 `==` 对 `7.7-0.1` 得 `7.6000000000000005` 会失败）。

- **T1 单边数学**：`apply_round_trip_cost(10.0, 0, 0, sell_side_bps=5) == pytest.approx(9.95)`（扣 `5/100`）。
- **T2 三项叠加**：`apply_round_trip_cost(10.0, 2, 3, sell_side_bps=5) == pytest.approx(9.85)`（扣 `2*(2+3)/100 + 5/100 = 0.15`）。
- **T3 默认字节不变**：`apply_round_trip_cost(10.0, 0, 0) == pytest.approx(10.0)`（不传 sell_side_bps，走早退守卫）；`apply_round_trip_cost(7.7, 2, 3) == pytest.approx(7.6)`（与旧实现同值）。
- **T4 None 透传**：`apply_round_trip_cost(None, 0, 0, sell_side_bps=5) is None`。

### 6.2 后处理门控测（`backtest_service` 集成，mock 引擎）

G1–G6 全部归集到 `tests/test_backtest_service_intraday.py`，**复用该文件既有成本后处理 helper `_make_intraday_svc_with_engine_return`**（与既有 `test_cost_zero_keeps_return_unchanged` / `test_cost_positive_deducts_round_trip` 同址同源——成本后处理测试的内聚位置）。市场覆盖（cn/us/crypto）由 `code` 参驱动 `market_of(code)`（`600519`→cn、`AAPL`→us、`BTC/USDT:PERP`→crypto），比分散到三个市场文件更 DRY。helper 追加关键字-only 参 `code`/`position`/`stamp_bps`，monkeypatch `BacktestEngine.evaluate_single` 返回受控 evaluation dict + 设 `ashare_intraday_backtest_stamp_duty_bps`，断言落库 `simulated_return_pct`（用 `pytest.approx`）：

- **G1 cn + long**：`position_recommendation="long"`、`simulated_return_pct=10.0`，stamp=5、fee/slip=0 → `approx(9.95)`（扣一次卖出税）。
- **G2 cn + cash**：`position_recommendation="cash"`、`simulated_return_pct=0.0`，stamp=5 → `approx(0.0)`（门控挡掉印花税分量）。
- **G3 crypto + long**：market=crypto、`position_recommendation="long"`、stamp=5 → 不含印花税（`market!="cn"`）。
- **G4 默认 0 字节不变**：cn + long、stamp=0、fee/slip=0 → 后处理跳过，原值不变。
- **G5 us + long（防白名单误增，关键）**：market=us（如 AAPL）、`position_recommendation="long"`、stamp=5 → **不征**。锁定门控为 `== "cn"` 而非 `in {"cn","us"}`——美股是已上线市场，误征会静默污染其回测结果，G1-G4 无法检出此变异。
- **G6 cn + long + 三项叠加**：stamp=5、fee=2、slip=3 → `approx(10.0 - 0.15)`（验证印花税与对称 fee/slip 正确叠加，不重复计）。

> 注：G1-G6 用 mock evaluation + 控制 market/intraday 入口；不依赖真实分钟取数（离线确定性）。

### 6.3 门禁

- **后端**：`./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`，含 `tests/test_config_registry.py` 的 registry↔locale 一致性两测）全绿；记录 passed 数增量。
- **前端 web-gate（因 Web 暴露，必跑）**：`cd apps/dsa-web && npm ci && npm run lint && npm run build` 全绿（settingsHelp.ts 改动）。须在**无空格 worktree** 跑（路径含空格会致 npm ci 残缺安装）。

---

## 7. 文件清单

- `src/core/intraday_backtest.py`：`apply_round_trip_cost` 增 `sell_side_bps` 参 + docstring。
- `src/services/backtest_service.py`：后处理块加 cn+long 门控、传 `sell_side_bps`。
- `src/config.py`：数据类字段 + 加载器。
- `.env.example`（**独立文件**）：注释块（约 `:712-713`，紧邻 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS`）。
- `src/core/config_registry.py`：注册项（完整 schema，§4.3）。
- `apps/dsa-web/src/locales/settingsHelp.ts`：Web locale（§4.4，测试强制）。
- `tests/test_intraday_backtest_helpers.py`：`apply_round_trip_cost` 单测（§6.1，pytest.approx）。
- `tests/test_backtest_service_intraday.py`：后处理门控测 G1-G6（§6.2；扩展既有成本 helper，`code` 参覆盖 cn/us/crypto）。
- `tests/test_config_intraday_backtest.py`（**已存在，扩展**）：新字段默认 0 + env-override(=5→5.0) 并入既有两测。
- `tests/test_config_registry.py`：registry↔locale 一致性由既有两测覆盖（实现后确认转绿，不新增用例）。
- `docs/intraday-backtest.md`：成本小节补印花税 knob + A股 5bps + 佣金另设说明。
- `docs/CHANGELOG.md`：`[Unreleased]` 扁平条目（[新功能] Web 可调 A股印花税 / [文档] / [测试]）。

---

## 8. 风险与回滚

- **默认 0 数值字节级不变**：后处理数值无既有路径回归面；config_registry 注册项对 API schema 是**追加字段**（老客户端忽略未知字段）。
- **`market` 变量作用域**：对抗审查已确认**无 NameError、无跨轮残留**（`:151` 是 intraday 块首条、恒先于 `:304`、每轮重赋）。§3 复用 `market` 安全；如需消除耦合可改 `is_a_share_code(analysis.code)`（备选）。
- **Web locale 同步**：`settingsHelp.ts` 若有多 locale 兄弟文件须一并加（实现核对其结构）；registry↔locale 由 `test_config_registry.py` 强制守护（漏则 CI 红）。
- **口径近似**：卖出税以入场名义额计 bps（误差 ≤0.05% @±100%，§2），文档登记。
- **成本语义**：fee/slip 跨市场共享、crypto 命名（既有），本特性不改其门控；A股佣金需用户另设（§5 文档点明）。
- **回滚**：纯代码 + 配置 + locale，无 schema/迁移；revert 即恢复；配置置 0 即关闭。

---

## 9. 不做（YAGNI / 范围外）

- 日线路径成本（D1 仅盘中；日线零成本是既有刻意设计）。
- 修复 §0.4 cash 的既有对称 fee 误扣（属既有行为，扩面，另议）。
- 佣金(双边)/过户费(沪市)等其它 A股成本分量（本特性只做印花税；如需可后续追加同类单边/双边参）。
- **CLI flag** 暴露成本参数（成本走 config + Web 设置页，不加 `--stamp-duty` 之类 CLI 参数；与 fee/slippage 一致）。注：Web/API 暴露**已纳入范围**（§4.4/§5，对抗审查纠正）。
- 港股/美股印花税或交易税（范围外）。
- A股专属佣金/过户费 knob（本特性只做印花税；佣金暂复用 crypto 命名的跨市场 fee/slip，§5 已澄清；如需 A股专属另议）。

---

## 10. 对抗式审查可追溯（2026-06-29，spec v1→v2）

4 视角并行读真实代码取证 + 对每条 Blocker/Important 独立对抗复核（5 confirmed / 0 refuted / 9 minor）。处置：

- **config:1（Blocker，确认）** 注册 config_registry 强制要求 `settingsHelp.ts` locale（`test_registry_help_keys_exist_in_locales`）+ 非隐藏字段须 help_key/examples/docs（`test_web_settings_visible_fields_have_help_metadata`）；「注册 + 不碰 Web」两条路都红。→ §4.4 增 locale、§7 增 settingsHelp.ts、§6.3 增 web-gate。
- **config:2（Important，确认）** §5 原「无 API/Web 暴露」事实错误（config_registry → `/config/schema` → Web 设置页；fee/slip 本就 Web 可调）。→ §4 抬头 + §5 + §9 全面订正为「Web 可调、用户可见」。决策：**拥抱 Web 暴露**。
- **test:1（Important，确认+复现）** T3 `7.7-0.1 == 7.6` 浮点失败（=7.6000000000000005）。→ §6.1 全改 `pytest.approx`。
- **test:2（Important，确认）** G1-G4 缺 `us+long` 反例，`market in {"cn","us"}` 变异静默漏过、错征已上线美股。→ §6.2 增 G5 us+long（+ G6 三项叠加）。
- **test:3（Important，确认）** §7 漏 `test_config_intraday_backtest.py`（门控测用 monkeypatch 绕过 parse_env_float，getattr 0.0 fallback 掩盖漏字段）。→ §7 增配置解析单测。
- **Minor 已收**：§4.3 列全 registry schema（`data_type:"number"`/`validation:{"min":0}`/`default_value:"0"`/`display_order=84` 等，纠「float/minimum」措辞）；§4.2/§7 订正 `.env.example` 为独立文件；§5 增佣金跨市场共享口径澄清（semantics:1）；§2/§8 增近似误差量级（semantics:2）；§7 增 `test_backtest_service_ashare_intraday.py` 回归锚 + fixture 模板引用。
- **确认无缺陷（对抗复核）**：`market` 作用域/可达性无 NameError/无残留；`apply_round_trip_cost` 仅 1 生产调用 + 3 测试（3 参位置）→ 追加默认参向后兼容。
