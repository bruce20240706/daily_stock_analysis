# 链路B `verified` family-wise 多重检验校正(Inc 1c)设计

## 0. 定位与背景

**目标**:让链路B(信号可信度三重门回测)展示给用户的 `verified` 标记**经得起"多重比较/data-snooping"质疑**——一次批跑同时检验 ~20–30 个 `(signal_type × market)` 格子,每格独立按 95% Wilson CI 判 `verified`,零跨格校正,按 95% 独立检验预期 1–2 个格子**纯靠运气**"跑赢 baseline"被标 `verified=True` 并直接展示(`/signals/board`)。本增量给 `verified` 加 **family-wise(Bonferroni-CI)** 校正:一格只有在**对家族规模收紧后仍跑赢 baseline** 才算 verified。

这是 [[dsa-actionable-signal-strategy]] Inc1「回测严谨性=命门」的第二个代码增量(Inc 1a=风险指标已交付),对应战略文档 `docs/strategy-actionable-signal-system.md` 标注的 "1c 阈值 data-snooping 校正"。

**血缘**:上游看板连置信区间都没有;本项目已有 Wilson CI + baseline 超额 + `verified`。1c 在此之上加多重检验校正,是"可回测信号可信度"差异化的锋利一刀。

## 1. 现状(基于真实代码,file:line)

- `aggregate_signal_stats(outcomes, baseline_outcomes, *, horizon, interval="1d")`(`src/services/signal_backtest.py:271-341`):**纯函数**,按 `(signal_type, market)` 分桶(`:307`),每格 `wilson_ci(win, sample)`(默认 `z=1.96`,`:317`)得 `ci_low/ci_high`,`excess = round(ci_low - baseline, 4)`(`:322`),产出 `SignalStat` 列表(dataclass,`:232-243`)。**不含 `__baseline__` 自身**。
- `wilson_ci(wins, n, z=1.96)`(`signal_backtest.py:246-263`):`n<=0 → (0.0,0.0)`;否则标准 Wilson,下上界 clamp 到 `[0,1]`。
- `verified` 判定在**读路径** `resolve_marker_hit_fields(signal_type, code, *, interval="1d")`(`src/services/signal_hit_rate.py:73-117`):
  ```python
  min_sample = int(getattr(cfg, "signal_hit_verified_min_sample", 0) or 0) \
      or int(getattr(cfg, "backtest_eval_window_days", 10))          # 默认 10
  stat = SignalStatsRepository().get(signal_type, market, interval=interval, horizon=horizon)
  if stat is None or (stat.sample or 0) < min_sample:
      return dict(_none)                                             # :100 提前返回
  verified = bool(stat.sample >= min_sample and stat.ci_low is not None
                  and stat.baseline_win_rate is not None and stat.ci_low > stat.baseline_win_rate)   # :103-108
  ```
  返回 dict keys:`hit_rate, hit_sample, verified, ci_low, ci_high, baseline_excess, horizon`(`:109-117`)。
- **落库**:`SignalStatRow`(`src/storage.py:416-441`,列 `signal_type/market/interval/horizon/win/loss/sample/win_rate/ci_low/ci_high/baseline_win_rate/excess/computed_at`,唯一键 `(signal_type,market,interval,horizon)`);`aggregate` 的 `SignalStat`→`SignalStatRow` 转换在 `src/services/signal_backtest_service.py:170-186`;`save_batch(replace_existing=True)`(`src/repositories/signal_stats_repo.py:22-47`)同键覆盖写。
- **消费**:`signal_board_service.py:25,133,190-224` 把上述 dict 展开进看板行 → `build_board`(`:264-287`)→ `GET /api/v1/signals/board`(`api/v1/endpoints/signals.py:26-58`),前端/用户直接看到 `verified/ci_low/ci_high/baseline_excess`。
- **无任何多重检验校正**:全仓 grep `bonferroni|fdr|benjamini|holm|deflat|out.of.sample|walk.forward|holdout` 零命中(除同名不同义的 `min_sample`)。
- **family 规模**:一次 `run()`(`signal_backtest_service.py:109-196`)对整个自选池跑完后**一次** `aggregate_signal_stats`(`:167`),covering 1 个 `interval` × 1 个 `horizon`,格子数 = 该次出现的 `signal_type(~8 bullish 型) × market(≤4)` ≈ 20–30 个同时检验的假设。

## 2. 非目标(1c 范围外,明确不做)

- **不改 `min_sample`**(默认 10 偏低是**另一缺口**,留 1b/单独议)。
- **不做 FDR/Benjamini-Hochberg**(已选 Bonferroni-CI 保守派;FDR 是可能的更富 follow-up)。
- **不做样本外/walk-forward 切分**(= Inc 1b)。
- **不改 raw `ci_low/ci_high/excess` 的计算或展示**(保持可读性不回归)。
- **不动链路A**(纯回放无拟合,无 verified 体系)。
- **不追溯重算历史行**(老行无校正列 → 读路径优雅回退,见 §4.5;重跑 `--signal-backtest` 刷新)。

## 3. 设计决策(brainstorming 已拍板)

| # | 决策 | 取值 |
|---|---|---|
| D1 | 校正哲学 | **Bonferroni-CI 保守派**(FWER),复用现成 Wilson,`verified` 收紧,宁漏勿误 |
| D2 | surfacing | **A 方案:重定义 `verified`=校正后**;保留 raw `ci_low/ci_high/excess` 展示不变;**全栈透出** `ci_low_corrected`(驱动 verified 的校正下界)+ `family_size` 两个透明字段(读路径 dict→marker→board_service→**Pydantic schema**→**前端类型/mapper/组件**),消解"raw ci_low>baseline 却未验证"矛盾靠**数字**而非文案(见 §4.6 复审加固) |
| D3 | family N | **可检验格子数**(`sample ≥ min_sample`),不足样本格子不计入 N(否则过度惩罚) |
| D4 | 存储 | 写时(aggregate)算,**冻结** `ci_low_corrected` + `family_size` 落库;读时只读(不重算统计) |
| D5 | alpha | `SIGNAL_BACKTEST_FWER_ALPHA` 默认 **0.05**(=当前 95% 的族错误率),注册进 config_registry(Web 可调) |

## 4. 方案

### 4.1 family 与 N 的定义

一次 `aggregate_signal_stats` 调用产出的所有 `(signal_type, market)` 格子构成一个 family(该 run 的 1 个 interval×horizon)。

```
N = 该批中 sample >= min_sample 的格子数        # 「被检验」的假设数
```

`min_sample` 用与读路径**完全一致**的解析(避免漂移,见 §4.7 抽共享 helper):`signal_hit_verified_min_sample or backtest_eval_window_days`(默认 10)。

### 4.2 Bonferroni-CI 机制(连续退化 + 精确 N≤1 不变)

对每格用族收紧的 Wilson 下界替代原始下界作为 verified 判据:

```python
if N <= 1:
    z_corr = 1.96                                          # 单/零假设:无多重检验,取 legacy 字面量 z
else:
    z_corr = NormalDist().inv_cdf(1 - (alpha / 2.0) / N)   # 双尾族水平 alpha,Bonferroni 均摊到每格
# 逐格(sample>0):
ci_low_corrected = wilson_ci(win, sample, z=z_corr)[0]     # 复用现成 wilson_ci
# sample==0:ci_low_corrected = None
```

**关键不变式(byte-identical 默认)**:
- **N≤1 时 `z_corr` 恰取字面量 `1.96`** → `wilson_ci(win, sample, 1.96)` 与 raw `ci_low`(默认 z=1.96)**逐字节相同** → `ci_low_corrected == ci_low` → `verified` 与当前**完全一致**。
  - **不能**用 `inv_cdf(0.975)=1.9599639…`:它与字面量 1.96 在第 5 位差开,razor-edge 下会翻 `verified`,破坏不变式。N≤1 分支必须用 `1.96`。
- **N≥2 且 alpha∈(0, 0.05] 时 `z_corr ≥ inv_cdf(0.9875)=2.2414 > 1.96`**(下界收窄,`verified` 只会**变严**,绝不比 legacy 95% 宽松)。这是把 alpha 上限钉到 **0.05**(=legacy per-comparison 水平)的直接推论:`(alpha/2)/N ≤ 0.05/2/2 = 0.0125`(N=2、alpha=0.05 处取最大)→ `1-(alpha/2)/N ≥ 0.9875` → `z_corr ≥ 2.2414`。alpha 越小或 N 越大,`z_corr` 越大(更严)。
  - **对抗审查逮到的反转陷阱**:若放开 alpha 到 0.5,则 alpha>0.05·N 时 `z_corr<1.96`(如 alpha=0.5、N=2→`z_corr=inv_cdf(0.875)=1.1503`),Wilson 下界反升、`verified` 比未校正**更宽松**,放大而非修复假 verified。故 **alpha 上限必须 ≤0.05**(§4.7 registry/loader 双钳),使"校正只收紧"成为不可绕过的不变式。
- **单格 run(N=1)、或整批只有 1 个可检验格子 → 零行为变化**。校正只在多格同检(N≥2)时咬合。

**alpha 语义精度(复审 M-2,勿误标假阳率)**:`verified` 是**单尾**判据(`effective_low > baseline`),而 `z_corr = inv_cdf(1 - (alpha/2)/N)` 按**双尾** CI 均摊——沿用既有"双尾 95% Wilson CI 下界当单尾检验用"的惯例(N=1 时 legacy z=1.96 对应双尾 0.05 = 单尾 0.025)。故 `SIGNAL_BACKTEST_FWER_ALPHA=0.05` 实际控制的**单尾** family-wise 假 verified 率 ≈ **0.025**(非 0.05);又因 alpha 上限钳 0.05,用户**配不到** 5% 单尾 FWER(最松 2.5%)。这与既有约定自洽、不是 bug,但 §4.7 config help 与文档措辞**必须精确**:alpha 是"双尾族水平/等价单尾≈alpha/2",**不得**写成"5% 假阳率"以免误导配置者。

**边界/守卫**:
- **N=0**(无格子达 `min_sample`):`z_corr` 取 1.96(不进 inv_cdf 除零分支);这些 sub-threshold 格子读路径 `:100` 提前 `_none` 返回,`ci_low_corrected` 不被读,取值无害。
- **alpha 定义域**:`0.0001 ≤ alpha ≤ 0.05`(§4.7 双端**钳制**;下界 0.0001 与 registry 对齐、上界 0.05 保"只收紧")。此域内 `1 - (alpha/2)/N ∈ [0.9875, 1)`(N≥2),`inv_cdf` 恒有限正值(`z_corr ∈ [2.2414, ~4.06]`),**绝不 inf/NaN、绝不 StatisticsError**。
  - **下界必须 0.0001 而非 0**:`parse_env_float` 的 clamp 是 `parsed < minimum`(含 0.0=0.0 不触发),若 `minimum=0.0` 则 env `SIGNAL_BACKTEST_FWER_ALPHA=0` 放行 → `inv_cdf(1-(0/2)/N)=inv_cdf(1.0)` **抛 StatisticsError**;极小 alpha(<~1e-16)使 `1-(alpha/2)/N` 浮点舍入到恰好 1.0 亦同崩。`minimum=0.0001` 彻底关掉此边缘。
- `wilson_ci` 自身 `n<=0 → (0,0)`、下上界 clamp[0,1] 不变,不会溢出。
- `ci_low_corrected` **不 round**:raw `ci_low` 亦未 round(`wilson_ci` 返回原始 float 直接落 `ci_low` 列),`ci_low_corrected` 与之对称落原始 float,避免精度错配;`excess`(=`round(ci_low-base,4)`)保持现状不动、不新增校正版 excess。

### 4.3 计算位置(写时,aggregate 内)

`aggregate_signal_stats` 新增两个 keyword 参数(带默认,保持纯函数可独立测试):

```python
def aggregate_signal_stats(outcomes, baseline_outcomes, *, horizon, interval="1d",
                           fwer_alpha: float = 0.05, min_sample: int = 10) -> List[SignalStat]:
```

> **M-6 默认漂移防护**:签名默认 `min_sample=10` 仅为纯函数独测便利;**生产路径必须显式传** `min_sample=resolve_verified_min_sample(cfg)`(§4.7 service 接线),否则若 `backtest_eval_window_days≠10`,写时 N 用 10、读路径 verified 用真实值 → N 与 verified 判据漂移。docstring 须标注"生产必传,默认 10 仅供测试";§7.9 断言读写同值。

流程:Step 3 拆为两遍——
1. 先构建各格 `win/loss/sample/win_rate/ci_low/ci_high/baseline/excess`(不变);
2. `N = sum(1 for c in cells if c.sample >= min_sample)`;`z_corr` 按 §4.2;逐格 `ci_low_corrected = wilson_ci(win, sample, z_corr)[0]`(sample>0)`else None`;`family_size = N`;
3. 组装 `SignalStat`(新增两字段)。

`SignalStat` dataclass(`signal_backtest.py:232-243`)**追加末尾**两字段(带默认,保持既有关键字/位置构造不破):
```python
    ci_low_corrected: Optional[float] = None      # family-wise 校正后 Wilson 下界;N<=1 时==ci_low
    family_size: int = 0                          # 本 run 可检验格子数(N)
```

### 4.4 存储

- `SignalStatRow`(`storage.py:416-441`)**追加两列**,**随既有 stat 列 house style = plain nullable 无 default**(复审 M-1:`ci_low`@:430、`excess`@:433 皆 `Column(Float)` plain nullable):`ci_low_corrected = Column(Float)`、`family_size = Column(Integer)`(均 nullable)。
  - **不用 `NOT NULL DEFAULT 0`**(原稿 ORM `default=0` + ALTER `NOT NULL` 会致 fresh `create_all`[nullable 无 server_default] 与 migrated 库[NOT NULL]**schema 不一致**);plain nullable 让**老行 = NULL = legacy 哨兵**(与 §4.6/§6 "family_size None/0 渲染为 legacy 而非 N=0" 一致),而新写行 aggregate 恒显式赋 `family_size=N(≥1)`,不依赖列 default。
- **幂等补列**:仿 `_ensure_backtest_intraday_columns`(`storage.py:919-943`)新增 `_ensure_signal_stats_columns`,`create_all()` 后调用(`storage.py:899` 邻位):`PRAGMA table_info(signal_stats)` 探测,缺则 `ALTER TABLE signal_stats ADD COLUMN ci_low_corrected FLOAT` / `ADD COLUMN family_size INTEGER`(**plain nullable,与 `first_hit_bar_index`@:938-941 同款,不加 NOT NULL/DEFAULT**——SQLite 对已填充表加 NOT NULL 列须带 DEFAULT,plain nullable 规避且语义更清晰)。老库无痛升级,补列即 NULL。
- `signal_backtest_service.py:170-186` 的 `SignalStatRow(...)` 构造追加 `ci_low_corrected=s.ci_low_corrected, family_size=s.family_size`。
- `save_batch/get`(`signal_stats_repo.py`)基于 ORM 列,无需改。

### 4.5 读路径(verified 改用校正下界 + 老行回退)

`resolve_marker_hit_fields`(`signal_hit_rate.py:103-117`):
```python
# 用 getattr 容错:老行(migration 前)ci_low_corrected 为 NULL、非 ORM 桩可能缺属性 → 回退 raw ci_low
_corr = getattr(stat, "ci_low_corrected", None)
effective_low = _corr if _corr is not None else stat.ci_low
verified = bool(stat.sample >= min_sample and effective_low is not None
                and stat.baseline_win_rate is not None and effective_low > stat.baseline_win_rate)
```
返回 dict **追加两个 additive key**:`"family_size": getattr(stat, "family_size", None)` 与 **`"ci_low_corrected": _corr`**(= 上面已取的 `getattr(stat,"ci_low_corrected",None)`,即驱动 verified 的校正下界)。`ci_low/ci_high/baseline_excess/hit_rate/hit_sample/horizon` 全不变。
- **为何必须也透 `ci_low_corrected`(复审 I-1)**:verified 现由校正下界驱动,但 raw `ci_low`/`baseline_excess` 仍原样展示。若只透 family_size 不透校正下界,用户会看到 raw `ci_low>baseline`(正 `baseline_excess`)却 `verified=False`,屏上无数字解释(前端 `formatExcess` 只看 raw excess、`verifiedLabel` 只看 verified,二者并排渲染——见 §4.6)。透出 `ci_low_corrected` 让矛盾靠**数字**消解("校正后下界 ≤ baseline 故未验证"),兑现 D2 透明承诺、契合"经得起 data-snooping 质疑"目标。

**⚠️ 读路径改动的既有桩必改(对抗审查逮到的 Blocker,§7.8 强制项)**:`getattr` 只救 `SimpleNamespace`(缺属性→回退 raw `ci_low`),**救不了 `MagicMock` 桩**——`getattr(MagicMock(), "ci_low_corrected", None)` 返回自动子 mock(非 None)→ `effective_low = <mock>` → `mock > float` 抛 `TypeError`。故**必须显式给以下既有 stat 桩补 `ci_low_corrected`**(建议 = `ci_low` 同值以保退化语义),否则 backend-gate 红:
- `tests/test_signal_hit_rate.py` 的 `_make_stat`(`:195-205`,MagicMock)与 `_stat`(`:372-380`,MagicMock)—— 被 `test_sample_at_threshold_sets_verified_true`(`:207`)、`test_resolve_reads_signal_stats_by_market_and_sets_verified_on_excess`(`:382`)、`test_resolve_not_verified_when_ci_low_below_baseline`(`:393`)使用,sample≥min_sample 会进入 verified 计算 → 现状 TypeError。
- `tests/test_signal_hit_rate.py:512-513` 与 `tests/test_signal_finer_fields.py:91-95` 的 `SimpleNamespace` 桩(getattr 可回退,但仍建议显式补 `ci_low_corrected=None` 或 = ci_low 以语义清晰)。
- 真实 ORM 行(`test_..._real_repo_roundtrip:456`)因新增 `Column(Float)` 默认 NULL,`getattr` 得 None → 回退 raw `ci_low`,**不受影响**。

**回退语义**:migration 前的老行 `ci_low_corrected=NULL` → verified 沿用旧的未校正判据(与升级前一致,不倒退不假收紧);重跑 `--signal-backtest` 后新行带校正值,verified 变严。诚实且平滑。

### 4.6 surfacing(⚠️ 全栈 6 层,复审 I-1/I-3 加固:透出 `ci_low_corrected` + `family_size` 两字段)

**要透出两个字段**:`ci_low_corrected`(I-1,驱动 verified 的校正下界,消解矛盾)+ `family_size`(N,透明提示"在几个同检假设中")。**复审逮到透出链比原稿多两层**——原稿止于 `signal_board_service`,但字段还要过 **Pydantic 响应 schema** 与 **前端类型/mapper/组件** 才到用户;漏任一层,字段被静默丢弃 → 沦为死字段(正是 §4.6 本要防的,只是深两层)。既有 `horizon`(resolver `'horizon'` → `marker['horizon_bars']`@:168 → `m.get('horizon_bars')`@:200)走的是**内部 dict 链**,而它能到前端是因为 Pydantic `SignalMarker.horizon_bars` + 前端类型都已声明;新字段须复刻**全链 6 层**:

1. **`signals_service._marker_from_vpsignal`**(`:120-174`,白名单拷贝,`:162-168` **无 `**fields` spread**):基础 marker dict(`:135-158`)与 `_llm_marker`(`:177-214`)各加 `'ci_low_corrected': None, 'family_size': None`(键一致);resolver 回填块(`:162-168`)加 `marker['ci_low_corrected'] = fields.get('ci_low_corrected')`、`marker['family_size'] = fields.get('family_size')`。
2. **`signal_board_service._hit_fields_from_markers`**(`:190-205` 两处 return)加 `'ci_low_corrected': m.get('ci_low_corrected')`、`'family_size': m.get('family_size')` → 经 `_entry_from_board_signals`(`:220` `**_hit_fields_from_markers(...)`)展进看板行。`_degraded_entry`(`:233-234`)对应补两键默认 None(否则 build_board 并行组装字段集不齐)。
3. **Pydantic 响应 schema(复审 I-3,原稿漏此层)**:`api/v1/schemas/stocks.py` 的 `SignalMarker`(`:128-131` 区)与 `BoardEntry`(`:196-199` 区)各追加 `ci_low_corrected: Optional[float] = Field(None, ...)` 与 `family_size: Optional[int] = Field(None, ...)`。**不加则 `SignalsResponse(**...)`/`SignalsBoardResponse(**build_board(...))`(`signals.py:52`)按显式字段校验时静默丢弃这两键**,前端永远收不到。
4. **修 schema 字段描述(复审 I-1,防文档变假话)**:`stocks.py:128`(SignalMarker)与 `:196`(BoardEntry)的 `verified` Field 描述现**逐字写**「hit_sample 达阈值且 ci_low>baseline 则 True」——1c 改用校正下界后此句失真,须改为「hit_sample 达阈值且**校正后下界** `ci_low_corrected`(family-wise Bonferroni,老行回退 raw ci_low)`> baseline` 则 True」。
5. **前端类型 + mapper**:`apps/dsa-web/src/types/kline.ts` 的 `SignalMarker`(`:42-50`)与 `BoardEntry`(`:84-94`)加 `ciLowCorrected: number | null`、`familySize: number | null`;`apps/dsa-web/src/api/stocks.ts` 的 `RawSignalMarker`/`RawBoardEntry` 加 snake_case `ci_low_corrected`/`family_size`,`mapSignalMarker`(`:22-78`)/`mapBoardEntry`(`:80-112`)加 snake→camel 映射。
6. **前端渲染消解矛盾(复审 I-1 核心)**:`apps/dsa-web/src/utils/credibility.ts` 现有 `formatExcess`(`:28-32`,仅看 raw `baselineExcess`)与 `verifiedLabel`(`:35-37`,仅看 verified)**互不引用**,三处组件(`SignalBoardGroup.tsx:53-59`、`StockSignalsPanel.tsx:52-61`、`SignalDrilldownPanel.tsx:63-81`)把绿色"超额+Xpp"与灰色"未验证"并排渲染 → 收紧后必现矛盾。消解方案:新增 `formatCorrectedCi(ciLowCorrected)` 与/或在 `verifiedLabel`/tooltip 处展示"校正后下界 a% vs baseline b%(N 格同检)";当 `verified=False` 但 `baselineExcess>0` 时,用 `ciLowCorrected`+`familySize` 给出"raw 超额但族校正后未达标"的解释文案(而非留下无解释的绿+灰并列)。

**M-5 legacy 渲染**:可 surface 的 verified-eligible 行 N 恒 ≥1;`family_size` 为 `None`(老行 NULL,未重跑)时前端渲染为"legacy/未重算"而非"N=0";`ci_low_corrected` 为 `None` 同理(读路径已回退 raw ci_low 判 verified)。

**§7 端到端断言终点延伸到 API/前端**(复审 I-3):不止于 board entry dict,须断言两字段一路到 **API 响应 model**(Pydantic 序列化后仍在)与**前端 mapper 输出**(vitest);删任一层接线该测试必红。

`verified` 字段名/类型不变(仅语义收紧 + 描述订正)。

### 4.7 配置

- **`config.py`**:`ConfigModel` 加 `signal_backtest_fwer_alpha: float = 0.05`(邻 `signal_backtest_horizon_bars`,`:900`);loader(`:1745` 邻位)用 `parse_env_float(os.getenv('SIGNAL_BACKTEST_FWER_ALPHA'), 0.05, field_name='SIGNAL_BACKTEST_FWER_ALPHA', minimum=0.0001, maximum=0.05)` 解析。**语义=钳制(clamp)非回退**(复用既有 `parse_env_float` idiom:越界值钳到 `[0.0001, 0.05]`,仅非数字才回退默认 0.05;例:输入 `0.9`→`0.05`、`0`→`0.0001`、`-1`→`0.0001`)。**上限 0.05 保证 §4.2 "只收紧" 不变式,下界 0.0001 关掉 `inv_cdf(1.0)` 崩溃边缘**(§4.2 边界)。
- **共享 helper**:抽 `resolve_verified_min_sample(cfg) -> int`(= `signal_hit_verified_min_sample or backtest_eval_window_days`),读路径(`signal_hit_rate.py:95-96`)与写路径(service 传给 aggregate 的 `min_sample`)**共用**,避免 N 与 verified 的 min_sample 漂移。
- **`config_registry.py`**:注册 `SIGNAL_BACKTEST_FWER_ALPHA`(category `backtest`,`data_type` `float`,`default_value` `"0.05"`,`validation {"min":0.0001,"max":0.05}`[与 loader 钳制域一致],`display_order` 取 backtest 类下一个空位[**实现时核验现有 order 占用,勿凭猜——参照 HK 印花税踩坑**],`help_key settings.backtest.SIGNAL_BACKTEST_FWER_ALPHA`,`examples`/`docs`/`warning_codes` 全量补齐——否则 web-metadata 覆盖测试红)。
- **`settingsHelp.ts`**:`settings.backtest.SIGNAL_BACKTEST_FWER_ALPHA` locale(镜像 `SIGNAL_BACKTEST_HORIZON_BARS`,含 impact 字段;否则 registry↔locale 覆盖测试红)。**文案精度(M-2)**:描述为"family-wise 多重检验族水平(双尾;等价单尾假 verified 率≈alpha/2),越小越严;上限 0.05=沿用现 95% CI 水平",**不得**写"5% 假阳率"。
- **`.env.example`**:`SIGNAL_BACKTEST_FWER_ALPHA=0.05`(裸 KEY 样例,与 registry 覆盖门一致)。
- **service 接线**:`signal_backtest_service.py:167` `aggregate_signal_stats(..., fwer_alpha=cfg.signal_backtest_fwer_alpha, min_sample=resolve_verified_min_sample(cfg))`。

## 5. 兼容性

- **byte-identical 默认**:N≤1(单信号型或单市场或单可检验格子的 run)→ `z_corr=1.96` → `ci_low_corrected==ci_low` → `verified` 逐格与当前完全一致;多格 run 才收紧(这正是修复目标)。
- **追加不破坏**:`SignalStat`/`SignalStatRow` 末尾追加带默认字段;新增 DB 列幂等补;读路径 dict 追加 key;`verified` 字段名/类型不变。
- **老行优雅回退**:`ci_low_corrected=NULL` → 用 raw `ci_low`(升级前行为),不假收紧、不崩。
- **无 API 破坏**:`ci_low_corrected` + `family_size` 追加字段(Pydantic 均 `Optional[...]=None`);既有 `verified/ci_low/ci_high/baseline_excess` 类型不变(`verified` 语义收紧 + schema 描述订正,见 §4.6 步 4)。**verified 单向性**:因 alpha 钳到 ≤0.05,校正后 verified **只会更严或不变、绝不比 legacy 95% 宽松**(§4.2 已证 `z_corr ≥ 2.2414 > 1.96` for N≥2;N≤1 恒等)——这是"正确性修复"的正确方向,不会误新增假 verified。
- **verified 为纯展示字段,无 filter 消费(复审 I-2 已核)**:全后端 `verified` **从不做过滤/门控/分支**——`market_analyzer.py` 不消费(仅 LLM prompt 里 "unverified" 措辞)、`task_queue.py` 仅注释含该词、`signal_board_service.build_board` 按 `action_group` 分组计数(`:283`)不读 verified、`api/v1/endpoints/signals.py:52` 整表原样序列化不 post-filter。故收紧 verified **不压制**任何信号/告警/报告/LLM,board 之外零行为变化;唯一用户可见影响 = 徽章值 + §4.6 消解后的展示。
- **历史订正**:需重跑 `run_backtest`/`--signal-backtest` 刷新校正列(不自动迁移旧统计,与 signal_stats 覆盖写惯例一致)。

## 6. 诚实边界(内嵌/文档,防误读)

- **family = 单次 run 的可检验格子**;跨多次 run、跨多 interval/horizon 的检验**未合并校正**(per-run family 为可计算的诚实单元,与"运营方一生看过的所有信号"这个不可知全集有别)。`family_size` 字段透出让用户知道"在 N 个同检假设中"。
- `family_size` 与 `ci_low_corrected` 是**写时快照**;若事后改 `min_sample`,读路径 verified 用新 min_sample 但两者仍为旧值(重跑刷新)——与 signal_stats 覆盖写、risk_metrics "写时冻结" 同源取舍。
  - **精确边缘(复审 M-3)**:若事后**下调** min_sample 而未重跑,原 sub-threshold 格子(`sample>0` 但 < 旧 min_sample,写时未计入 N、但 `ci_low_corrected` 仍按写时 N 算过)会变 verified-eligible,用一个**未把自己计入 N** 的校正下界 → 相对新 min_sample 轻微**欠校正**。方向保守(N 偏小=校正偏弱=偏宽松而非偏严),但仍应重跑 `--signal-backtest` 使 N 与 min_sample 一致。上调 min_sample 无此问题(格子直接 `:100` 提前返回)。
- **baseline 是每市场共享、且当已知常量(复审 M-4)**:`baseline_win_rate` 由 `aggregate` 按**市场**算(`signal_backtest.py:291-300`),同市场所有 signal_type 格子共享一个 baseline(`:320`);verified 判据把它当**无误差的已知常量**比较。本次校正只收紧**信号侧**抽样误差(Wilson CI),**不含 baseline 自身的估计误差**——属既有建模选择(§2 不改 baseline),1c 不扩展,诚实标注以免过度解读"校正后"的严格性。
- Bonferroni 是**保守**校正(FWER),小样本(低至 min_sample=10)× 多格下可能几乎无格 verified;这是"宁漏勿误"的刻意选择,不是 bug。
- `min_sample=10` 偏低仍在(1c 不改),校正在其之上叠加。

## 7. 测试(离线确定性)

1. **退化 N=1 byte-identical**:fixture **钉死 `0<win<sample`(用 `win=6, sample=8`)** 的单可检验格子 → `ci_low_corrected == ci_low`(精确 `==`)。**该 fixture 下 `wilson_ci(6,8,1.96)[0]=0.409270 ≠ wilson_ci(6,8,inv_cdf(0.975))[0]=0.409275`(第5位差开),故精确 == 真能证伪"N≤1 误用 inv_cdf(0.975)"分支**;`win=0` 会 clamp 到 0.0 使两者恒等退化成 tautology,**禁用**。`verified` 与未校正逐格一致。
2. **校正咬合(非 tautology)**:N=20、alpha=0.05 + 一个**边界**格子(raw `ci_low > baseline` 但校正后 `ci_low_corrected ≤ baseline`)→ `verified` True→False;另一**强**格子(校正后仍 > baseline)→ 仍 True。手算 Wilson-corrected 下界锁定(`z_corr=inv_cdf(1-(0.05/2)/20)=3.0233`)。删掉校正逻辑该测试必红。
3. **N=2 首次咬合边界**:N=2(校正首次生效点,alpha=0.05→`z_corr=inv_cdf(0.9875)=2.2414`)构造边界格子验证 True→False,补 §7.2 只测 N=20 的空档。
4. **alpha 上限"只收紧"不变式**:遍历 alpha∈{0.0001, 0.01, 0.05}(域内)× N∈{2, 5, 20},断言 `z_corr ≥ 1.96` 恒成立、`ci_low_corrected ≤ ci_low`(校正后 verified **绝不比 legacy 宽松**)→ 锁死对抗审查逮到的"大 alpha 反转"陷阱(证 alpha 上限 0.05 是承重守卫,非装饰)。
5. **N=0 不崩**:全格子 `sample < min_sample` → 无异常、无 inf/NaN/StatisticsError、`family_size=0`、读路径 `_none`。
6. **老行回退**:`ci_low_corrected=NULL` 的行 → verified 用 raw `ci_low`(升级前行为),不倒退不误收紧。
7. **落库/读取贯通**:`family_size`/`ci_low_corrected` 写入 → `get` 读回 → `resolve_marker_hit_fields` 返回 `family_size`;`_ensure_signal_stats_columns` 对缺列老库幂等补(建临时缺列表→调用→列出现)。
8. **两字段全栈贯通(复审 I-1/I-3:防死字段,终点延伸到 API/前端)**:`ci_low_corrected` 与 `family_size` 从 resolver → `_marker_from_vpsignal`(marker 带两键)→ `_hit_fields_from_markers` → board entry → **Pydantic `SignalMarker`/`BoardEntry` 序列化后仍非 None** → **`SignalsBoardResponse`/`SignalsResponse` 响应体含两字段**;断言两字段**非 None 到达 API 响应**(删任一跳接线或漏 Pydantic 声明该测试必红——原稿止于 board entry dict 会漏掉 Pydantic 静默丢弃层)。前端 mapper 一侧:vitest 断言 `mapBoardEntry`/`mapSignalMarker` 把 `ci_low_corrected/family_size` 映成 `ciLowCorrected/familySize`。
9. **配置**:`SIGNAL_BACKTEST_FWER_ALPHA` 解析 = **钳制** `[0.0001, 0.05]`(`0.9→0.05`、`0→0.0001`、`-1→0.0001`、极小 `1e-100→0.0001` 不崩、非数字→回退默认 0.05);`resolve_verified_min_sample` 读写一致(读路径与 aggregate 传入同值);`config_registry`↔`settingsHelp` locale 覆盖 + `.env.example` 覆盖门(镜像 `SIGNAL_BACKTEST_HORIZON_BARS` 的三向覆盖测试)。
10. **alpha 单调方向(域内)**:alpha 在 `[0.0001, 0.05]` 内越小(越严)→ `z_corr` 越大 → `ci_low_corrected` 越低 → verified 越难。
11. **读路径既有桩必改(修 Blocker,plan-mandated)**:读路径新增 `ci_low_corrected` 访问后,**必须**给 `tests/test_signal_hit_rate.py` 的 `_make_stat`(`:195-205`)、`_stat`(`:372-380`)MagicMock 桩显式补 `ci_low_corrected`(= `ci_low` 值,保退化语义),并给 `tests/test_signal_hit_rate.py:512-513`、`tests/test_signal_finer_fields.py:91-95` 的 SimpleNamespace 桩补 `ci_low_corrected`;落地前跑 `pytest tests/test_signal_hit_rate.py tests/test_signal_finer_fields.py` 验证全绿(MagicMock 缺属性会 TypeError、SimpleNamespace 会 AttributeError,getattr 只救后者)。
12. **零回归 grep**:grep `tests/`(含 `apps/dsa-web/src/**/__tests__`)中 `aggregate_signal_stats`/`SignalStat(`/`resolve_marker_hit_fields`/`signal_stats`/`verified` 断言,**两类风险各核**:(a)精确 dict/字段集断言因新增 `ci_low_corrected`/`family_size` key 红 → plan-mandated 补 key(后端 Pydantic schema 断言、前端 `stocks.*.test.ts` 字段集断言均在列);(b)**stat 桩对象**(MagicMock/SimpleNamespace)缺 `ci_low_corrected` 致 TypeError/AttributeError(见测试#11)。结论写进交付。
13. **前端矛盾消解 + schema 描述订正(复审 I-1)**:(a)vitest 断言当 `verified=false` 且 `baselineExcess>0`(raw 超额)时,渲染层用 `ciLowCorrected`/`familySize` 给出解释(不再是无解释的绿"超额"+灰"未验证"并列);`familySize=null`/`ciLowCorrected=null` 渲染为 "legacy/未重算"(M-5)。(b)后端断言 `api/v1/schemas/stocks.py` 的 `SignalMarker`/`BoardEntry` `verified` 字段描述已订正为"校正后下界 > baseline"(防文档假话;可用字符串包含断言锁描述关键词)。

## 8. 交付结构

改了什么(**全栈 additive**:后端统计 + DB 两列 + Pydantic schema + 前端类型/mapper/组件/locale + config)/ 为什么(修正 20-30 格无校正致假 verified 误导用户,并透出校正下界消解矛盾 UI)/ 验证情况(退化+咬合+边界+回退+配置+全栈贯通+**ci_gate + web-gate**)/ 未验证项 / 风险点(低:byte-identical 默认、追加字段、老行回退、verified 无 filter 消费)/ 回滚(单分支 revert;verified 恢复未校正,DB 多两列 + 前端多两字段无害)。

**验证矩阵**:后端 `PATH=.venv/bin:$PATH ./scripts/ci_gate.sh`;前端 `cd apps/dsa-web && npm ci && npm run lint && npm run build`(本次含前端类型/mapper/组件改动,**web-gate 触发,必跑**);registry↔locale↔.env.example 三向覆盖门 + web-metadata 门。

CHANGELOG `[改进]` 或 `[修复]` 扁平一条(verified 语义收紧属正确性修复面,倾向 `[修复]`;新增 `ci_low_corrected`/`family_size` 透出属 `[改进]`——按主导面择一,倾向 `[修复]`)。
