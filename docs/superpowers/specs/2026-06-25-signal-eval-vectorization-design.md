# 信号引擎走查向量化(O(n²)→O(n log k)) 设计 Spec

> 日期：2026-06-25(v3：2026-06-26 两轮对抗式专家评审后定稿)
> 阶段：盘中/分钟级回测 epic follow-up —— 修复链路B 分钟路径"功能正确但不可用"的性能缺陷。
> 前置：链路B 信号可信度分钟化已落地并合入 main(merge 6a03c202;测试质量修复 f4e3706b)。
> 评审：v1→v2 经 5 视角对抗审查(3 Blocker/9 Important/8 Minor);**v2→v3 再经 5 视角专审"因果 oracle 自身正确性"(2 Blocker/多 Important,含确定性反例)**。v3 见 §9 两轮追溯。

## 0. 缘起(端到端验证暴露的缺陷)

`坐实端到端` 验证(关沙箱、真实 BTC/USDT 5m)证实链路B 分钟路径**功能正确**(interval 贯通、真实信号算出、三重门产出 outcome),但 `src/services/signal_backtest.py::_eval` 的逐 bar 走查是 **O(n²)**:

| bars | eval 耗时 | 实测来源 |
| --- | --- | --- |
| 200 | 21.99s | diag |
| 500 | 148.27s | diag(2.5×bar → 6.7×time，即 O(n²)) |

外推:7 天 5m(2016 根)≈37 分钟,365 天默认窗(~10.5 万根)≈70 天。两次全量跑均"跑飞"(72 CPU 分钟未完成)。日线 n~250 时 O(n²) 无害,分钟尺度 n 爆炸才致命。

根因:`_eval` 对每个 bar t 用 `df.iloc[:t+1]` 全窗重算 `derive_price_levels` + `compute_volume_price_signals`,只取末根、其余全丢;且**每根一次 pandas 全流程调用的固定开销**(~137ms@200根、~322ms@500根)使得即便定长窗口 bound 后 10.5 万根 × ~300ms ≈ 8 小时,仍不可用。**唯一真正的解是把信号引擎一次性算出每根 bar 的因果信号(单遍,无逐 bar Python 全流程调用)。**

## 0.1 运行态拓扑(决定本次重构边界的事实)

信号引擎 `compute_volume_price_signals` / `derive_price_levels` 的**全部生产消费方只有两条,且互不相干**(已 grep+实读核实):

1. **图表/看板几何路径** —— `signal_board_service.py:105` 对**全 df 一次**调 `compute_volume_price_signals(df)`,`build_signals_payload`(`signals_service.py:299`)消费**全部** marker 画 K 线(逐 bar rule markers,非只取末根)。这条路径**本就是 compute-once**,**不经** `_eval`/逐窗。
2. **回测→命中率统计路径** —— `signal_backtest.py:153` 对**每个窗** `df.iloc[:t+1]` 调引擎(本次要优化的 O(n²) 路径)→ outcomes → `aggregate_signal_stats` → `signal_stats` 表 → `resolve_marker_hit_fields`(`signal_hit_rate.py:99`)**回流注入图表 marker 的命中率字段**(共 7 个,见 §5.2)。

`api/v1/endpoints/stocks.py:45` 仅 import `PriceLevels` 类型(非消费方);`signal_backtest_service.py` 仅 import `VPSConfig`。

**关键推论(本次重构的总纲)**:
- 图表 marker **几何**(signal_type / timestamp / price / direction)由路径 1 决定 → 见 §1 硬约束:**不改 `compute_volume_price_signals` 及其全部传递被调函数**,图表几何字节级不变,并由既有 `tests/test_volume_price_signals.py`(30+)守护 + §6.3 近边界 viz 回归。
- 图表 marker 的**命中率注解**由路径 2(回测产出落 DB)决定 → 本次修正回测语义,会改变这些数字(更因果正确),且仅在重跑 `--signal-backtest` 落库后对图表生效。预期行为变更,§5.2 文档化。

## 1. 目标与范围

把回测路径(路径 2)的逐 bar O(n²) 走查重构为单遍近线性实现,使链路B **分钟路径在现实窗口下可用**(分钟→秒级);**图表/看板几何零变化**;回测信号采用**因果修正语义**(§1.1)。

- **In scope**:
  - 新增 `compute_signals_for_all_bars(df, *, config, now=None) -> dict[int, list[str]]`:对全 df 单遍算出每根 bar **因果触发**的 bullish 信号(去重),替代 n 次逐窗调用。
  - 新增 `derive_price_levels_series(df, *, atr_mult, rr_target)`:价位序列(跑在**原始 df**,对齐现有 `derive_price_levels` 不 normalize 的行为)。
  - 新增**并列的因果检测器变体**(`_detect_upthrust_spring_causal` / `_detect_shrink_pullback_causal` / vfx 全 bar 变体等),**绝不就地改原检测器**(见硬约束)。
  - 改写 `_eval`:外层逐 bar 仍在(产 `SignalOutcome` + 三重门),信号/价位查询 O(1) 命中预计算。
  - 必要时给 `normalize_ohlcv` 增**可选** `keep_original_index`(默认关,行为不变)以支持 norm2raw(§4.1)。
  - golden:**(a)** 图表几何走既有套件 + 近边界 viz 回归;**(b)** 回测走**独立因果参照** oracle(§6.1)。
- **Out of scope**:
  - 任何对外 API / schema / CLI / 配置默认值变更。
  - 改 `compute_volume_price_signals` / 图表路径(路径 1)的任何**输出**。
  - VPS 阈值分钟标定、`end_date` 右边界截断、`_load_bars` 下传 start_date、`intraday_data` capability 声明(各自独立 follow-up)。
- **硬约束(v3 收紧)**:
  - **图表/看板几何(路径 1)零变化**。约束对象 = `compute_volume_price_signals` **及其全部传递被调函数**(`_detect_*`、`find_swing_pivots`、`atr`、`_compute_primitives`、`_limit_b_class`;`normalize_ohlcv` 仅允许**追加式可选参数**)。所有因果改写一律**新增并列函数**,不就地修改任何被路径 1 调用的函数。由既有套件 + §6.3 近边界 viz 回归双重守护。
  - **因果性不得放松**:bar t 的回测信号只能用 ≤t 数据。`find_swing_pivots` 的 k 根右确认滞后:bar t 只能用**确认索引 (center+k) ≤ t** 的 pivot。
  - 零新增**业务**配置项(`keep_original_index` 是函数级可选参,非环境配置)。

### 1.1 等价基准的重定义(方案 C 分层)

v1 设定"golden = 严格逐条等价于现有逐窗 `_eval`"。评审 Blocker B1 证明此目标与 O(n) **互斥**:逐窗 `_limit_b_class` 的 top-k 池含**中间 bar 的非因果 marker**,要逐条复现须每 t 重建 → 回到 O(n²)。

**决策(方案 C 分层):**
- **图表(路径 1)**:保持现状语义(全 df 单遍、含中间 bar 的"非因果"viz marker——这对可视化历史是正确的),字节级不变。
- **回测(路径 2)**:采用**因果修正语义**——top-k 竞争只在**因果 per-bar marker**之间进行,可单遍 O(n log k) 流式实现,B1 化解。

回测 golden 基准**不是**现有逐窗 `_eval`(它带 §1.2 的 quirk),而是 §6.1(b) 的**独立因果 oracle**。回测输出(命中率)随之改变,见 §5.2。

### 1.2 现有逐窗 `_eval` 中被有意修正的 quirk(两轮评审实证,枚举已确认完整)

| 代号 | quirk | 修正方向 |
| --- | --- | --- |
| Q1 | top-k 池掺入中间 bar 的非因果 upthrust/spring marker(B1) | 仅用因果 per-bar marker 竞争 top-k |
| Q2 | `_detect_upthrust_spring`/`_detect_shrink_pullback` 字面 `p.index<i` 在全 df 跑会泄漏未确认 pivot(默认 k=3 泄漏 2 根) | 因果变体显式 `center+swing_k ≤ i` 门控 |
| Q3 | `_drop_partial_today` 墙钟依赖:逐窗把**每根**今日 bar 当作"窗口末 partial"清零 | compute-once 下今日**已收盘** bar(t<n-1)正常产信号 |
| Q4 | `_detect_shrink_pullback` 实为 last-bar-only,逐窗逐根产、单遍只产末根 | 改 per-bar 变体(同 vfx) |

例外(经实读确认天然因果、**无需**改):`_detect_obv_divergence`(在 `conf_idx=curr.index+swing_k` 出 marker,已含 k 滞后)。

## 2. 决策记录(v3)

- **D1 方法**:单遍因果信号派生 + 因果流式 top-k。
- **D2 pivot 因果**:消费者侧判定式从 `p.index < i` 改为 **`p.index + config.swing_k ≤ i`**。适用因果变体:`_detect_upthrust_spring_causal`、`_detect_shrink_pullback_causal`。例外:`_detect_obv_divergence`(conf_idx 归位,免门)。
- **D3 vfx 全 bar 变体**:每根用当根 causal primitive 跑 `_classify_vfx`,**复刻 `_detect_latest_vfx` 字段覆写**(confidence→'low'、is_daily_approx=True、is_anomalous=False、observed_value=classified.observed_value、price=close),保留 `neutral`/`is_anomalous` 跳过。vfx 旁路 top-k。
- **D3b shrink_pullback per-bar 变体**:每根 i 取"确认索引 ≤ i 的最近 high"(= 逐窗 highs[-1])为锚;段检查 `(seg_rel < thr).all()` 用 O(1)/bar 增量(前缀:最近违例 index + 非 NaN 计数)。
- **D4 因果流式 top-k**:对**因果 per-bar B marker**(vsa + upthrust/spring,各按 ≤i 因果)做流式 top-k。**实现要求(评审 important):**
  - **完整元组键** `(-abs(observed_value), block, bar)`,block: VSA=0 / upthrust&spring=1。**不得用 abs-only 堆**(跨块平局会翻转,有反例)。该键已证与生产 `_limit_b_class` 稳定排序逐元素等价(2万随机算例,positive)。
  - **先插完一根 bar 的全部 B marker,再做成员判定**(单根可同出 upthrust+spring+vsa)。
  - **竞争池含全方向** B marker(bearish/neutral 也占 top-k 槽);`direction=='bullish'` 过滤严格置于 top-k **之后**(镜像 `_eval`)。
  - **冻结语义**:bar t 对冻结在 t 的池 [0:t] 判定一次;后续 t'>t 的挤出不回改 bar t(高水位单调:越晚的 bar 越难进 top-k)。
- **D5 索引空间**:两套预计算分属两空间。`compute_signals_for_all_bars` 内部检测器跑在 **normalized frame**,marker **按原始 bar 行号归位**(不按 timestamp 回查,规避 B2);行号经 `normalize_ohlcv(keep_original_index=True)` 透出(§4.1)。`derive_price_levels_series` 跑在**原始 df**,按 raw t 对齐。
- **D6 退化三态**:(a) `_normalize` 缺列/空 → 全空;(b) `_check_sufficient_window` 窗口不足 → **per-bar warmup gate,落 norm 行号空间**:对 norm 行 j,`(j+1) < max(vol_ma_window, atr_period, breakout_window)+1` 时该 bar 不归位(等价逐窗 `len(norm)<min_bars`→degraded);(c) `rel_vol` 全 NaN → status=degraded 但**仍产 marker**(obv 不依赖 rel_vol),**不**据 status 清空。
- **D7 正确性裁决**:图表几何由既有套件 + 近边界 viz 回归锁死;回测由 §6.1 独立因果 oracle(已修双重 top-k)锁死。先写测试(含 §6.3 反例),再实现。

## 3. 架构

现状:`evaluate_signal_outcomes`/`evaluate_baseline_outcomes` → `_eval(df,*,market,horizon,config,all_bars,min_history)`;`_eval` 逐 bar `window=df.iloc[:t+1]` → `derive_price_levels(window)` → 信号模式 `compute_volume_price_signals(window)` 取末根 bullish → `classify_triple_barrier`。

重构后:
- 新增预计算(单遍):`derive_price_levels_series`(**raw 空间**)→ `levels_by_bar[t]`;`compute_signals_for_all_bars`(**norm 空间→raw 行号归位**)→ `bullish_by_bar[t]`。
- `_eval` 外层循环:逐 bar O(1) 取预计算,三重门/`SignalOutcome`/`all_bars` 分支**原样保留**。
- `compute_volume_price_signals`/`derive_price_levels`(标量版)/图表路径(路径 1)**完全不动**;因果逻辑全部走**新增并列函数**。

## 4. 详细设计

### 4.1 `compute_signals_for_all_bars(df, *, config, now=None) -> dict[int, list[str]]`(新)

返回 `{raw_bar_index: [bullish signal_type, ...]}`(去重;list 仅为确定性/可读,**golden 按集合比较、不校验顺序**——见 §6.1(b)/§9 round2)。

实现(全 df 单遍):
1. `normalize_ohlcv(df, keep_original_index=True, now=now)` → `(norm, norm2raw)`(原始行号经新可选参透出;`now` 注入使 `_drop_partial_today` 可确定性测试,默认 None=现状墙钟)。`_compute_primitives(norm)`。退化按 D6 三态(warmup gate 落 **norm 行号** j)。
2. **A 类全 bar 检测器**(真·全 bar,新并列变体或直接复用纯函数):`_detect_obv_divergence`(conf_idx 归位,D2 例外)、`_detect_breakouts`(纯 rolling;并列变体内把 `atr_series.iloc[:i+1]` 切片改 `atr_series.iloc[i]`——**对 signal_type 等价**,reason/vol_pattern 仅在 ATR seed 前极端 config 下不同,不影响回测、且不触碰路径 1;原函数不动)。
3. **last-bar→all-bar 变体**:vfx 全 bar 变体(D3)、`_detect_shrink_pullback_causal`(D3b)。
4. **B 类因果 per-bar marker + 流式 top-k**(D4):`_detect_vsa_bars`(纯同 bar,hoist `astype(float)` 出循环消除 O(n²))+ `_detect_upthrust_spring_causal`(D2 门 + 单调指针定位最近已确认 pivot,消除每根 O(n·p))→ 因果 per-bar RAW B marker → 流式 top-k(D4 实现要求)→ vfx 旁路并入。
5. 汇总:A 类 + top-k 后 B 类 + vfx,去重,经 `norm2raw` 映回原始 bar 行号,只收 `direction=='bullish'`。

> 复杂度逐检测器(两轮 positive 汇总):primitives/atr O(n);find_swing_pivots O(n·k);obv/breakout/vsa(hoist 后)O(n);upthrust(指针后)O(n+p);shrink(增量后)O(n);anchored_vwap 见 §4.3。整体 **O(n log k)**(流式 top-k 堆)。

### 4.2 `derive_price_levels_series(df, *, atr_mult, rr_target)`(新)

- 跑在**原始 df**(不 normalize)。MA20=`close.rolling(20).mean()`、swing_low20=`low.rolling(20).min()`(带 `len≥20` 门)、ATR=`atr(df)` **默认 period=14**。**硬编码 20/14,与 `VPSConfig` 解耦**(照搬现有 `derive_price_levels`,不接 config)。
- per bar t 取 `series.iloc[t]`(NaN→None),= 现有 `_last_finite`,**非 ffill**。
- 把现有标量 entry/stop/target + `is_invalid_price_level`→`_fallback_atr_levels` 回退链抽成 **scalar core**,逐 bar 调用,保证回退分支字节一致。
- golden 与逐 bar `derive_price_levels(df.iloc[:t+1])` 对齐(价位逐窗本就因果)。

### 4.3 anchored_vwap 复杂度 + dup-ts 锚定

`_anchored_vwap_signals` 是 O(B·n)(`ts.index(b.timestamp)` 线扫 + 累计内循环)。**新并列变体**让 `_detect_breakouts` 回传锚点 **bar 行号**,avwap 用整数索引锚定,消除线扫。**约束**:在**无重复时间戳**数据上必须与原 `ts.index` 选同一锚 bar(字节一致),使 golden 等价在干净 fixture 成立;重复时间戳下 bar-index 锚定更正确(B2),作为**单侧生产行为**断言(§6.3),不与 oracle 强等价。

### 4.4 `_eval` 改写

```
levels = derive_price_levels_series(df, ...)                              # raw 空间, O(n)
sig_by_bar = {} if all_bars else compute_signals_for_all_bars(df, config=cfg)  # norm→raw, O(n log k)
for t in range(min_history, n-1):
    lv = levels[t]
    if lv.stop is None or lv.target is None: continue
    fwd = _bars_as_dicts(df.iloc[t+1:t+1+horizon])
    if not fwd: continue
    if all_bars:
        out.append(SignalOutcome(BASELINE, market, classify_triple_barrier(fwd, lv.stop, lv.target)))
    else:
        for sig_type in sig_by_bar.get(t, ()):   # 已过滤 bullish
            out.append(SignalOutcome(sig_type, market, classify_triple_barrier(fwd, lv.stop, lv.target)))
```

三重门、`fwd` 截断、`SignalOutcome` 字段**完全不变**。`min_history`(默认 40)≥ `min_bars`(默认 21)是等价前提;D6(b) 的 norm 空间 per-bar gate 使该不变量被 env 破坏时仍正确,§6.3 有反例。

## 5. 错误处理 / 边界 / 行为变更

### 5.1 边界(沿用现状)
- `min_history`、`n-1`(留 1 根前瞻)、末段 horizon 截断(不补零)、NaN 处理:沿用现有循环边界。
- 退化:按 D6 三态。`derive_price_levels_series` 对应位 `PriceLevels(None)` → 该 bar `continue`。
- 空 df / 单根 / 全 NaN:返回空 outcome。

### 5.2 文档化行为变更(方案 C 的预期结果,非 bug)
1. **回测命中率统计变化**:回测改因果修正语义(Q1–Q4),`signal_stats` 变化。图表 marker 经 `resolve_marker_hit_fields` 注入的 **7 个字段**(`hit_rate` / `hit_sample` / `verified` / `ci_low` / `ci_high` / `baseline_excess` / `horizon`,`signal_hit_rate.py:109-117`)中,**hit_rate / hit_sample / ci_low / ci_high / baseline_excess / verified 都会变**,**在重跑 `--signal-backtest` 落库后生效**。
2. **注解出现/消失**:Q3/Q4 增减样本会让部分 signal_type **跨过 `min_sample` 阈值**(`signal_hit_rate.py:100`),从"无命中注解(all-None)"变为"显示命中率",或反之——比数字变化更可见。
3. **今日已收盘 bar 产信号**:逐窗对每根今日 bar 清零(Q3 墙钟),修正后今日已收盘 bar(t<n-1)正常参与回测。
4. **影响面**:仅回测/命中率;CLI/API/schema/图表几何/看板路径零变化。须在 `docs/CHANGELOG.md` `[Unreleased]` 记 `- [改进] ...` + 专题文档同步,避免用户误判图表"无故变化"。

## 6. 验证矩阵

### 6.1 golden(核心,双轨)
- **(a) 图表几何不变**:既有 `tests/test_volume_price_signals.py`(30+)+ `test_volume_price_levels.py` 全绿,diff 不触碰路径 1 传递调用树(§1 硬约束)。**新增近边界 viz 回归**:构造 pivot `center+swing_k == i+1`(对 bar i 非因果但路径 1 应保留)的 upthrust/spring,断言 `compute_volume_price_signals` 全 df 调用仍在 bar i 渲染该 marker(锁死"因果变体是并列新函数、未就地改共享检测器")。
- **(b) 回测因果等价**:`tests/test_signal_eval_vectorization.py` —— 测试内**独立因果参照** `_eval_reference_causal`(已修 v2 双重 top-k 缺陷):
  - **B 类**:对每根 i,直接调 `_detect_vsa_bars`/`_detect_upthrust_spring`(**原始检测器,绕过 `_limit_b_class`**)于 `norm(df.iloc[:i+1])`,按**行号**取末根 i 的 RAW B marker(逐窗截断天然因果)。对 `⋃_{i≤t}` RAW B marker 做**唯一一次**朴素 top-k,键 `(-abs, block, bar)`(与 D4 同键),全方向竞争、bullish 过滤在后。
  - **A 类 / vfx / shrink**:由 `compute_volume_price_signals(df.iloc[:i+1])` 取末根 i(positive 已证它们旁路 `_limit_b_class`,提取忠实)。
  - 断言新 `_eval`(signal+baseline)与 oracle 的 `SignalOutcome` **按 bar 比较 bullish 集合等价 + outcome 值等价**(集合/排序后比较,不做哈希序逐条断言)。
  - **等价适用域**:golden(b) 仅在**无重复时间戳 + 历史日期(或注入 `now≥16:00`)**的 fixture 上做双侧等价(此时 oracle 的 ts.index 锚 == 生产 bar-index 锚,`_drop_partial_today` 为 no-op)。dup-ts 锚定(§4.3)、Q3 今日 bar 走 §6.3 的**单侧生产行为**断言。
  - 参照仅存在于测试,生产侧无 O(n²) 死代码。

### 6.2 性能复验
diag 脚本 200/500/2000/10000 根确认近线性;**额外高突破密度分钟 fixture**(B 大)压测 anchored_vwap/top-k。10.5 万根目标 < 数分钟。

### 6.3 区分性反例 fixture 表(每条附最小构造约束,防 golden 空过)
| fixture | 锁死风险 | 最小构造要点 |
| --- | --- | --- |
| F1 pivot 确认跨评估 bar | Q2/D2 泄漏 | swing_k=2,**spring(bullish,低点 pivot)** center=t-1(center+k=t+1),断言该 bar **无** bullish;且 spring 须能存活 top-k |
| F2 top-k 平局跨块序 | D4 键 | top_k=1,VSA(晚 bar,rel_vol)与 upthrust/spring(早 bar,价格)abs **恰相等**且在 **k 边界**,写出预期胜者(按 `(-abs,block,bar)`) |
| F3 同 bar 多 bullish | 集合成员(非"保序") | 一根 bar 同出 ≥2 bullish(如 breakout+spring),断言集合全保留;**同 bar upthrust+spring 一进一出 top-k** |
| F4 大窗+dropna 交互 | D6(b) norm 空间 gate | VPS_VOL_MA_WINDOW=50 + [0:min_bars] 内插 ≥1 NaN-volume 行,warmup 间隙内放一个 **obv_bottom_divergence**(rel_vol 无关),断言 gated;oracle 以**原始 raw df** 逐窗为基 |
| F5 零成交量 | D6(c) obv 仍产 | volume 全 0 + 双下降低点 pivot,断言 `obv_bottom_divergence` 仍产出 |
| F6a 重复时间戳 | D5 norm2raw 行号 | 含重复 epoch,断言按**行号**归位正确 |
| F6b 含 NaN 行 | dropna 对齐 | NaN 行经 dropna,断言该 raw bar 走 continue/空集合、`derive_price_levels_series` 在 raw NaN 位 None |
| F6c 未排序输入 | stable sort 归位 | 乱序输入,断言排序后 marker 归位正确 |
| F7 fallback 回退 | §4.2 回退分支 | entry>current 触发 `_fallback_atr_levels` |
| F8 warmup 边界 | _last_finite 非 ffill | n 略大于 min_history、min_bars=21 附近 |
| F9 shrink 中间 bar | D3b last-bar→all-bar | shrink_pullback 在中间 bar 触发,逐根产 |
| **F10 vfx 全 bar(单侧+等价)** | D3 | 中间 bar 的 `vfx_*_up` bullish 因果逐根产出,字段覆写与 `_detect_latest_vfx` 一致、neutral/anomalous 跳过 |
| **F11 anchored_vwap reclaim** | §4.3 | reclaim 在中间 bar 因果触发;**dup-ts 下 bar-index 锚定**走单侧生产断言 |
| **F12 Q3 今日 partial** | Q3 + oracle 一致性 | 注入 `now<16:00` + 今日多根分钟 bar,**单侧**断言 t<n-1 今日已收盘 bar 产信号、仅全局末根受 partial;oracle 与 impl 用同一 `now` |

### 6.4 其它
- 链路B 回归:`pytest -m "not network"` 既有 7 相关文件 + 全套绿。
- 门禁:`./scripts/ci_gate.sh`(flake8 + 全套)全绿。
- `normalize_ohlcv(keep_original_index=)` 改动:补 alert 路径(`evaluate_technical_alert` 等消费方)回归,确认仅追加列、不破坏 `iloc[-1]/[-2]` 语义。
- 对抗终审:Workflow 复审 v3(重点:修正后的 oracle 是否真独立且正确、§6.3 是否真覆盖、O(n log k))。
- 真实端到端(关沙箱,opt-in):重跑 `tests/test_chainb_signal_intraday_network.py -m network`,确认分钟路径 `processed>=1` 且合理时间完成。

## 7. 回滚

`signal_backtest.py` + `volume_price_signals.py` 新增函数 + `_eval` 改写 + `normalize_ohlcv` 追加可选参,单提交粒度;`git revert` 即恢复。新增函数仅 `_eval` 调用,图表路径零依赖。回滚后若已重跑 `--signal-backtest`,`signal_stats` 为修正语义版(命中率仅注解,不影响图表几何与下游主流程)。

## 8. 风险

- **R1 因果 oracle 自身正确性(最高,v3 已大幅缓解)**:v2 oracle 经由 `compute_volume_price_signals` 取 B marker → 双重 top-k + 重引非因果污染(确定性反例),v3 改为**绕过 `_limit_b_class`、直接调原始检测器取逐窗末根 RAW B marker、唯一一次 top-k**。残余:oracle 与生产在 dup-ts/Q3 上故意不同,已用**单侧断言 + 等价适用域限定**隔离。终审须再验 oracle 独立性。
- **R2 检测器内部超线性未消除**:VSA astype / upthrust pivot / anchored_vwap ts.index 若漏改,O(n) 不成立。缓解:§4.1/§4.3 逐点改法 + §6.2 高密度压测。
- **R3 索引空间错位 + norm2raw 落地**:`normalize_ohlcv` 现状 `reset_index(drop=True)` 销毁行号 → 须加 `keep_original_index` 可选参(追加式,默认关);warmup gate 落 norm 行号空间。缓解:§4.1/D5/D6 + §6.4 alert 回归 + §6.3 F4/F6。
- **R4 图表不变性被就地改破坏(v3 新识别)**:因果改写若就地改共享检测器,路径 1 viz 几何变。缓解:§1 硬约束(全传递树 + 并列新函数)+ §6.3 近边界 viz 回归。
- **R5 命中率行为变更未传达**:§5.2 七字段 + 注解出现/消失若未同步 CHANGELOG/文档,用户误判。缓解:§5.2 列为交付必做。
- **R6 浮点一致性**:向量化 rolling 与逐窗理论同值;outcome 离散枚举。遇浮点边界差定位对齐,不放宽断言。

## 9. 评审追溯

### Round 1(v1→v2)
| 发现 | 级别 | 处置 |
| --- | --- | --- |
| B1 top-k 非因果池与 O(n) 互斥 | Blocker | §1.1 方案 C:回测改因果池 |
| B2 bar-index vs timestamp(重复时间戳) | Blocker | D5 norm2raw 行号;§6.3 F6a/F11 |
| B3 norm vs raw 空间未对齐 | Blocker | D5 两空间分治;§6.3 F4/F6 |
| shrink 实为 last-bar-only | Important | D3b per-bar 变体 |
| upthrust `p.index<i` 泄漏 | Important×3 | D2 `center+swing_k≤i` |
| `_check_sufficient_window` 门遗漏(实测 bar16) | Important×2 | D6(b);§6.3 F4 |
| `_drop_partial_today` 墙钟 | Important | Q3;§6.3 F12 |
| top-k 平局序非 bar 序 | Important×2 | D4 键 `(-abs,block,bar)` |
| 返回 dict[int,set] 丢序 | Important | §4.1 list + §6.1 集合比较 |
| B 类内部 O(n²) | Important×3 | §4.1 hoist+指针 |
| golden 逐条顺序过严 | Important | §6.1(b) 集合比较 |
| degraded 三态 / `_last_finite` 非 ffill / 硬编码 20/14 / fallback 分支 / avwap dup-ts / vfx 字段覆写 / fixture 缺反例 | Minor×8 | D6(c)/§4.2/D3/§4.3/§6.3 |

### Round 2(v2→v3,专审 oracle)
| 发现 | 级别 | 处置 |
| --- | --- | --- |
| oracle 经编排器取 B marker = 双重 top-k + 重引非因果污染(确定性反例,3 lens) | **Blocker** | §6.1(b) B 类绕过 `_limit_b_class`、直接调原始检测器取末根 RAW、唯一一次 top-k |
| "不改 compute_volume_price_signals 即图表不变"蕴含不成立 + §4.1 就地改风险 | **Blocker** | §1 约束扩到全传递树 + 因果变体一律并列新函数;§6.3 近边界 viz 回归 |
| 流式 top-k 须完整元组键 + 先插完整 bar + 全方向池 + bullish 后过滤 | Important×2 | D4 实现要求 |
| warmup gate 落 norm 行号空间(非 raw t) | Important | D6(b) |
| norm2raw 受阻于 `reset_index(drop=True)` | Important | §1/§4.1 `keep_original_index` 可选参 + §6.4 回归 |
| oracle 在 dup-ts/Q3 与生产必然分歧 | Important | §6.1(b) 等价适用域 + §6.3 F11/F12 单侧断言;`now` 可注入 |
| §5.2 漏 hit_sample/baseline_excess + 注解出现/消失 | Important | §5.2 七字段 + 注解项 |
| Q3/vfx/avwap/大窗交互 无 fixture;F2/F3 缝 | Important×3 | §6.3 F10/F11/F12/F4 + F2/F3 重定义 |
| atr iloc[i] 非严格字节等价 / fixture 欠构造约束 / fixture6 合一 / oracle join 路径 | Minor/Nit | §4.1 措辞 + §6.3 构造要点 + F6 拆三 + 行号 join |
| **Positives(锁定)**:D4 键等价(2万算例)、冻结语义、A/vfx/shrink 提取忠实、缺陷只在 B 类、D2 obv 例外、breakout/avwap/shrink/D2 自洽、Q1–Q4 完整、D6(c)/D5-levels | — | 保留,不再动 |
