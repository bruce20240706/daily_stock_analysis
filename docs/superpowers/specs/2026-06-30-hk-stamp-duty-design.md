# 港股(HK)盘中回测双边印花税 knob 设计

> 状态:设计定稿待落实现计划。HK 分钟回测 MVP(merge `bd9bb6b0`)的成本模型 follow-up。

## 0. 背景与动机

港股分钟回测 MVP 的成本暂走跨市场 `fee/slippage`(默认 0),HK 双边印花税明确 deferred。本特性补全港股成本模型:新增一个 opt-in 印花税 knob。

**HK 税制事实(本设计的核心约束):** 港股印花税现行 **0.1%(10bps)**,**买入与卖出各计一次(双边对称)**。这与 A股(2023-08 起卖出**单边** 5bps)结构不同——A股单边、港股双边。本特性必须正确建模这一差异。

(MVP 范围仅印花税;证监会交易征费 0.0027%、联交所交易费 0.00565%、FRC 征费等极小费项不建模,需要时可经现有跨市场 `fee_bps` 近似——见 §8 YAGNI。)

## 0.1 既有基准(本设计镜像/扩展的对象)

- **成本函数** `src/core/intraday_backtest.py:60` `apply_round_trip_cost(return_pct, fee_bps, slippage_bps, sell_side_bps=0.0)`:
  - `fee/slippage` 对称双边 ×2;`sell_side_bps` 单边 ×1(A股印花税);默认全 0 早退守卫 → 原样返回(字节级不变)。
  - 公式:`cost_pct = 2.0*(fee+slip)/100 + 1.0*sell_side/100`;`return_pct - cost_pct`;`return_pct is None` 短路返回 None。
- **后处理门控** `src/services/backtest_service.py:307-319`(import :308、gate 条件 :316、apply 调用 :317-319):
  ```python
  _fee = float(getattr(config, "crypto_intraday_backtest_fee_bps", 0.0))
  _slip = float(getattr(config, "crypto_intraday_backtest_slippage_bps", 0.0))
  _stamp = 0.0
  if market == "cn" and evaluation.get("position_recommendation") == "long":
      _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
  if (_fee or _slip or _stamp) and evaluation.get("simulated_entry_price") is not None:
      evaluation["simulated_return_pct"] = apply_round_trip_cost(
          evaluation.get("simulated_return_pct"), _fee, _slip, sell_side_bps=_stamp)
  ```
  - `market = market_of(analysis.code)`(:153);cash-gate(`simulated_entry_price is not None`)豁免无成交仓(2026-06-29 cash 成本误扣修复)。
- **A股印花税配置三处接线(本设计精确镜像):**
  - `src/config.py`:attr `ashare_intraday_backtest_stamp_duty_bps: float = 0.0`(:908)+ load `parse_env_float(os.getenv('ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'), 0.0, field_name=..., minimum=0.0)`(:1910-1912)。
  - `src/core/config_registry.py`:`"ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS"` 条目(category="backtest"、data_type="number"、ui_control="number"、is_sensitive=False、is_required=False、is_editable=True、default_value="0"、validation={"min":0}、display_order=84、help_key="settings.backtest.…")。**实证(对抗审查):真实条目还含 `options=[]`、`examples=[…]`、`docs=[…]`、`warning_codes=[]`——HK 镜像须全量包含,否则 `test_web_settings_visible_fields_have_help_metadata`(要求每个可见字段有 help_key+examples+docs)会红 backend-gate。**
  - `apps/dsa-web/src/locales/settingsHelp.ts`:`'settings.backtest.ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS'` locale(title/summary/usage/valueNotes/**impact**/notes——实证条目含 `impact` 字段)。
  - **经 A股那轮对抗审查确证:config_registry 注册即等于 /config/schema API + Web 设置页可改 → 必须同步补 settingsHelp.ts locale,否则 registry↔locale 一致性测试两红 + 触发 web-gate。**

## 1. 设计决策

- **D1(双边建模):** 给 `apply_round_trip_cost` 新增 `both_side_bps: float = 0.0` 参数,按 ×2 计入(同 fee/slip 的对称性,但语义=印花税、配置独立)。HK 印花税走 `both_side_bps`;A股仍走 `sell_side_bps`。**否决备选**:(B)塞进 fee/slip ×2 桶——丢语义/配置分离;(C)拆 buy_side+sell_side——YAGNI,HK 印花税本对称。
- **D2(门控,entry-based 不限 long):** 后处理门控为 `market == "hk"` + **确有成交**(`simulated_entry_price is not None`),**不加 `position_recommendation=="long"` 过滤**。理由:HK 印花税买卖双边对称,任何成交的 round-trip 两腿都计征 → 2× stamp;cash(无成交,entry None)由 entry-gate 豁免。**实证(对抗审查,backtest_engine.py:134-154/202 + base.py:66-74):链路A 中 market=="hk" 永不可达 `short`**(`is_perp_code` 仅匹配 crypto → `infer_position_recommendation` 只产 `long`/`cash`),故"不限 long"**今日功能上等同 long-only**;采用 entry-based 门控而非显式 long-only,是为防御假想的未来 HK short(前向兼容)且语义更准(成交即计,与方向解耦)。注:G-test 矩阵无法构造真实 HK short 输入(无此可达态)。`cn` 与 `hk` 分支互斥(elif,单市场归类),无双计。
- **D3(配置 opt-in + Web 暴露):** 新增 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`,opt-in **默认 0**(字节级不变),HK 现行 10bps(0.1%)。三处同步 config.py + config_registry(→ Web 可改)+ settingsHelp.ts locale + `.env.example`。
- **D4(成本叠加语义):** HK 真实总成本 = 印花税(本 knob,双边)+ 佣金/其它(走跨市场 `fee/slippage`,默认 0)。文档须说明二者关系(同 A股)。

## 2. 改动面

| 文件 | 改动 |
|---|---|
| `src/core/intraday_backtest.py` | `apply_round_trip_cost` 加 `both_side_bps`(×2)+ 早退守卫 + 公式 + docstring |
| `src/services/backtest_service.py` | 成本块加 `_hk_stamp`(elif market=="hk")+ 早退条件含 `_hk_stamp` + 传 `both_side_bps=_hk_stamp` |
| `src/config.py` | attr `hk_intraday_backtest_stamp_duty_bps: float = 0.0` + load `parse_env_float(..., minimum=0.0)` |
| `src/core/config_registry.py` | `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 注册条目(**全量镜像 A股字段**含 examples/docs/options/warning_codes;**display_order=86**——已核验 85 被 INTRADAY_BACKTEST_SCHEDULE_MINUTES 占用、84 已重复,86 为 backtest band 首个空位;**注意无 display_order 唯一性测试,CI 不拦冲突,须用真空位**) |
| `apps/dsa-web/src/locales/settingsHelp.ts` | `'settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS'` locale(双边措辞) |
| `.env.example` | `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=0`(注释:HK 现行 10bps 双边,默认 0) |
| `tests/` | 函数级 + 集成 + config 解析测试(见 §6) |
| `docs/intraday-backtest.md` / `docs/CHANGELOG.md` | §12 HK 章成本节 + §5 成本公式补 both-side + CHANGELOG 扁平 |

## 3. 函数变更(`apply_round_trip_cost`)

```python
def apply_round_trip_cost(
    return_pct: Optional[float],
    fee_bps: float,
    slippage_bps: float,
    sell_side_bps: float = 0.0,
    both_side_bps: float = 0.0,
) -> Optional[float]:
    """...新增 both_side_bps:对称双边成本(如港股印花税,买卖各一次)→ ×2。..."""
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
- 默认 `both_side_bps=0.0` + 早退守卫含它 → 既有所有调用(A股/crypto/默认)字节级不变。

## 4. 门控变更(`backtest_service.py`)

```python
_stamp = 0.0
_hk_stamp = 0.0
if market == "cn" and evaluation.get("position_recommendation") == "long":
    _stamp = float(getattr(config, "ashare_intraday_backtest_stamp_duty_bps", 0.0))
elif market == "hk":
    _hk_stamp = float(getattr(config, "hk_intraday_backtest_stamp_duty_bps", 0.0))
if (_fee or _slip or _stamp or _hk_stamp) and evaluation.get("simulated_entry_price") is not None:
    evaluation["simulated_return_pct"] = apply_round_trip_cost(
        evaluation.get("simulated_return_pct"), _fee, _slip,
        sell_side_bps=_stamp, both_side_bps=_hk_stamp,
    )
```
- `cn`/`hk` 互斥;HK 不限 long;cash 由 entry-gate 豁免;默认全 0 → 早退条件 False → 字节级不变。

## 5. 配置接线(精确镜像 A股)

- `config.py`:attr + load(`minimum=0.0`,field_name=`HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`)。
- `config_registry.py`(**全量镜像 A股条目字段,不可只列到 help_key**):
  - title "HK Intraday Stamp Duty (bps)";description 含"港股盘中回测买卖双边印花税(基点);默认 0=理想化;HK 现行 10bps(0.1%);佣金另走 fee/slippage";category="backtest";data_type/ui_control="number";is_sensitive=False;is_required=False;is_editable=True;default_value="0";validation={"min":0};**display_order=86(已核验空位;85 被 INTRADAY_BACKTEST_SCHEDULE_MINUTES 占用)**;help_key="settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS"。
  - **必含(否则 `test_web_settings_visible_fields_have_help_metadata` 红):** `examples=["HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=10"]`、`docs=<全量镜像 A股条目的"回测功能"指南链接>`、`options=[]`、`warning_codes=[]`。
- `settingsHelp.ts`:key `'settings.backtest.HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS'`;字段 **title/summary/usage/valueNotes/impact/notes**(全量镜像 A股形状);title "港股盘中印花税";summary/usage 强调**买卖双边各计一次**(区别于 A股单边);valueNotes 注"1bp=0.01%;HK 现行 10bps=0.1%(双边)";`impact: ['影响港股盘中回测的收益率和胜率计算。']`;notes "仅影响盘中回测结果,不影响真实下单"。
- `.env.example`:新增项 + 注释。

## 6. 测试(TDD,含 A股那轮对抗审查的教训)

**函数级**(`tests/test_intraday_backtest_*` 或既有 apply_round_trip_cost 测试同处):
- `both_side_bps=10` → 扣 `2*10/100 = 0.20`(用 `pytest.approx` 避免浮点 ==)。
- `both_side_bps` 与 `sell_side_bps` 共存 → `2*both/100 + 1*sell/100` 叠加正确。
- `both_side_bps` 与 `fee/slip` 共存 → 三者并入 ×2 桶正确。
- 默认 `both_side_bps=0` → 与改动前数值一致(字节级回归)。
- `return_pct is None` → None 短路。

**集成**(`tests/test_backtest_service_hk_intraday.py` 扩 或新文件,离线 mock):
- **G1** hk + long + filled + `hk_stamp=10` → `simulated_return_pct` 扣 2× stamp(approx)。
- **G2** hk + cash(entry None)+ `hk_stamp=10` → **不扣**(entry-gate 豁免,return 不变)。
- **G3** hk + fee/slip + hk_stamp 共存 → 全部计入,数值正确。
- **G4(反例)** cn + long + `hk_stamp=10`(但 ashare_stamp=0)→ **不走 hk 分支**(elif 互斥),HK stamp 不计征。
- **G5(反例)** us / crypto + filled + `hk_stamp=10` → **不计征**(market 非 hk)。
- **G6** hk + `hk_stamp=0`(默认)→ 字节级不变(早退)。

**config 解析**:`HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` env → `config.hk_intraday_backtest_stamp_duty_bps`;**负值 env → 断言 `== 0.0`**(实证 config.py:224-232:`parse_env_float` 把 `< minimum` 的值**钳制**到 0.0 并记 warning,**不抛错、不回退 default**;故断言钳制结果而非 raise,镜像 A股解析测)。

**registry↔locale 一致性**:既有一致性测试会校验新注册键必须有 locale 条目 → 必须同步 settingsHelp.ts,否则两红 + web-gate。

## 7. 文档

- `docs/intraday-backtest.md`:§12(HK 章)成本节注明 HK 印花税**买卖双边各计一次**(0.1%/10bps,opt-in 默认 0),与 A股单边对比;§5 成本公式补 both-side 项 `+ 2 × hk_stamp_bps`。
- **"hold→long" 语义注记**(§12 成本节或 §5):`infer_position_recommendation` 把"持有/hold"建议映射为 `long`(backtest_engine.py:150),引擎对每个 long 均按 `entry@start_price` 全 round-trip 建模 → both-side 印花税会对"hold"也计买腿(相对真实 hold 多计一腿)。**与 A股 knob 同源**(A股已对 hold→long 计卖腿,HK 仅幅度翻倍 ×2);此为回测"long 即一次模拟 round-trip"的既有约定,**非本特性新增缺陷,不改码**。若产品意图豁免 hold,须同时处理 A股 knob,非仅 HK(超出本范围)。
- `docs/CHANGELOG.md`:`[Unreleased]` 扁平 `- [新功能] 港股盘中回测双边印花税 knob(HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS,opt-in 默认0,HK 现行10bps买卖双边对称×2,区别于A股单边;apply_round_trip_cost 加 both_side_bps;market==hk+确有成交门控不限long;Web 设置页可改;cash 豁免、cn/us/crypto/日线不征)`。

## 8. YAGNI / 非目标

- 不建模 HK 证监会征费/联交所交易费/FRC 征费(极小,需要时走 fee_bps 近似)。
- 不建模印花税最小收费(HK$1 向上取整)与逐笔取整——延续 A股的 flat-bps 近似。
- 不引入 buy_side/sell_side 拆分(HK 印花税对称,YAGNI)。
- 不改日线路径(仅盘中)、不改 crypto/cn/us 既有成本语义。

## 9. 风险 / 回滚

- 默认 0 → opt-in,所有既有路径字节级不变;风险面=新 knob 计算 + 一处函数签名扩展(向后兼容,默认参数)。
- 历史聚合(`avg_simulated_return_pct`)需 `run_backtest(force=True)` 重跑订正(同 A股,不自动迁移)。
- 回滚:revert 单 commit。

## 10. 验证矩阵

- 后端:`./scripts/ci_gate.sh`(flake8 + `pytest -m "not network"` 全绿,记录增量)。
- 前端:settingsHelp.ts 改动 → `cd apps/dsa-web && npm ci && npm run lint && npm run build`(web-gate)。
- 无网络依赖(成本为纯计算后处理)。

## 11. 审查可追溯性

**对抗式 spec 审查(2026-06-30,Workflow 4 视角读真实代码 + 综合)——判定 NEEDS FIXES(轻量),0 Blocker / 2 Important / 5 Minor,全部已折入本 spec:**

正确性骨架全部 CONFIRMED:函数签名/公式/None 短路/默认全0早退;`both_side_bps` 尾部带默认 keyword **字节级向后兼容**(唯一生产调用点 backtest_service.py:317,3 positional + `sell_side_bps=` keyword;无签名断言/autospec/partial/再导出/动态查找);`market_of(hk)=="hk"`;cn/hk 单市场互斥;HK 在链路A 永不可达 short(只 long/cash);filled⟺long round-trip;cash/insufficient_data 被 entry-gate 豁免;registry↔locale 一致性测试(test_config_registry.py:444-458)强制 help_key 同名 locale;`.env.example` 互锁测试(:403-415)要求注册;无 schema 快照测试;web-gate 由 apps/dsa-web/** 触发。

- **IMPORTANT-1(已修):** display_order=85 **已被 INTRADAY_BACKTEST_SCHEDULE_MINUTES 占用**、84 已重复,无唯一性测试 CI 不拦 → 改 **86**(已核验空位)。
- **IMPORTANT-2(已修):** `test_web_settings_visible_fields_have_help_metadata`(test_config_registry.py:281-297)要求可见字段必含 help_key+examples+docs → §5 字段清单补 examples/docs/options/warning_codes(全量镜像 A股),否则 backend-gate 红。
- **MINOR-1(已修):** settingsHelp.ts 形状漏 `impact` 字段 → 补。
- **MINOR-2(已修):** §6 `parse_env_float` 是**钳制**到 0.0(非拒绝/回退)→ 改断言 `== 0.0`(config.py:224-232)。
- **MINOR-3(已修):** 成本块行锚 `:308-318` → `:307-319`。
- **MINOR-4(已修):** D2「无论方向」对 HK 空泛(HK 永不可达 short)→ 改述为"今日等同 long-only + 前向防御",注明 G-test 无法构造 HK short。
- **MINOR-5(已修):** "hold→long" 按完整 round-trip 双边计征,与 A股同源、非新增缺陷 → §7 注记。

实现后 task review/终审继续填充(预期沿用 A股那轮教训:浮点 approx、us/crypto/cn 反例、门控变异检测)。
