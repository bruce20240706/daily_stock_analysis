# 链路B 信号风险画像(收益管道 + per-cell 风险指标)设计

> 版本:v3(两轮对抗审查收敛:round1 4 视角 19 项;round2 二轮复审 1 Blocker + 5 Important + 3 Minor + 1 现役 bug,均已折进)

## 0. 定位与背景

**目标**:让链路B(信号三重门回测)从"只有胜率"升级为"有风险画像"——给每个 `(signal_type × market)` 格子计算不年化 Sharpe/Sortino/事件净值 maxDD/worst_single,与链路A 同口径。前置是**造数据管道**:三重门目前只返回 `'win'|'loss'|'expired'` 裸分类,无任何收益幅度产生或落库。

这是 [[dsa-actionable-signal-strategy]] Inc1「回测严谨性=命门」系列增量(1a/1b/1c 已交付)。

**运维前提(round2 NEW-6 披露)**:链路B 回测**仅手动触发**(`python main.py --signal-backtest`)——`signal_backtest_enabled` 现为零消费死开关,dsa-daily(systemd)调度不跑它;故本增量交付后,生产 `risk_metrics_json` **默认恒 NULL,直到手动跑一次**。本增量不改调度(非目标),文档写明生效前提。

**拍板记录**:收益口径=保守跳空感知(**用户 Q1 明确拍板**)。以下为推荐默认(超时/审查修正,spec 审阅门可翻):数学复用=抽共享纯函数;存储=`risk_metrics_json` 单列;范围=后端到 API;baseline 不算;**失真守卫=形态级整层剔除**(round2 Blocker 修正,见 D1);**expired 计入收益序列**(round2 NEW-2 修正,使"与链路A 同口径"为真);**marker 层不透 dict、响应层挂 map**(round2 载荷修正,见 D5)。

## 1. 现状(基于真实代码,file:line,已核验)

- `SignalOutcome`(`src/services/signal_backtest.py:39-50`):frozen dataclass,仅 `signal_type/market/outcome`,无收益、无日期。
- `classify_triple_barrier(forward_bars, *, stop, target) -> str`(`:53-84`):long 视角逐 bar,`high>=target→'win'`、`low<=stop→'loss'`、同 bar 双触保守判 loss、到期 `'expired'`。无入场价概念、返回裸字符串。
- `_eval`(`:92-140`):逐 bar 因果走查;`levels[t].stop/target` 是 per-bar 价位;前瞻窗=`df.iloc[t+1 : t+1+horizon]`;隐含入场价=`df.iloc[t]['close']`(未显式使用);`df` 含 `date` 列。信号/baseline 模式共用。
- **价位几何(round2 核验)**:主路径 `_price_levels_scalar_core` 产 `stop=entry_plan−1.5·ATR`、`target=entry_plan+3·ATR`,且 `entry_plan<=close` 但**无 `target>close` 约束** → 动量 bar 可合法产出 `stop<entry_plan<target<=close`(失真形态);回退路径 `target=close+3·ATR` 永不失真。
- `aggregate_signal_stats`(`:271-341` 区):per-cell win/loss 计数 + Wilson CI + Bonferroni 校正(Inc 1c);无收益输入。
- `SignalStatRow`(`src/storage.py:416-441` 区)+ `_ensure_signal_stats_columns`(Inc 1c)幂等补列模式现成。
- 链路A `_compute_risk_metrics`(`src/core/backtest_engine.py:768-825`):不年化 Sharpe/Sortino/复利事件净值 maxDD/worst_single,单笔下钳 ≥-100、round4、除零返 None;**总体含 first_hit="neither" 的窗末平仓行**(expired 等价物计入——round2 NEW-2 依据);耦合行对象,核心数学可抽。
- surfacing:Inc 1c 六层模式现成;`/signals` markers 为**逐 bar 信号点**(A 类检测器无全局上限,days=120 实测典型 3-16 个、对抗式可达 52,days=365 可达 ~100;仅 B 类 top_k=2 限流)。
- **现役 bug(round2 逮出,本增量前置修复)**:`signals_service.py:288-303` 的 per-call resolver cache **以 code 为键**,注释援引旧 M2c"聚合源只取决于 code"——但 M3-A6 后 `resolve_marker_hit_fields` 按 `(signal_type, market)` 查 signal_stats → **同股非首个 signal_type 的 markers 拿到错误 signal_type 的 hit_rate/verified/ci_low 等全部字段**。不修则本增量 risk_metrics 同样挂错格子。
- 每次 `run()` 单 (interval, horizon) 一次 aggregate;`save_batch(replace_existing=True)` 同键覆盖写。API 无 gzip(`api/app.py:253` 仅 CORS+Auth)。

## 2. 非目标(明确不做)

- **前端渲染**:后端 API 透出为止(Pydantic 声明按 D5 设计,防静默丢弃/防膨胀双向权衡)。
- **baseline(`__baseline__`)风险指标**:收益管道自动覆盖 baseline outcome,但不为其计算/落库指标。
- **成本扣减**:毛收益(链路B 历来无成本概念),诚实标注。
- **不改 win/loss/expired 判定语义**(含同 bar 双触保守 loss):纯追加收益维度。
- **不年化、不做组合归因、不做 uniqueness 加权**。
- **不追溯重算历史行**:NULL=legacy,重跑刷新。
- **不改调度**(signal_backtest_enabled 死开关现状保持,仅文档披露)。

## 3. 设计决策

| # | 决策 | 取值 |
|---|---|---|
| D1 | 收益口径(**用户拍板 + round2 Blocker 修正**) | **保守跳空感知**:win=`(target-entry)/entry`;loss=`(min(触障bar open, stop)-entry)/entry`(open 无效回退 stop);expired=`(窗末close-entry)/entry`;entry=触发 bar close;百分比,下钳 ≥-100。**失真守卫(形态级,outcome 无关)**:`target <= entry` 的事件**不论 win/loss/expired 一律 `return_pct=None`**——round1 的 win-only 守卫会单边截断(赢的不计、输的全计=系统性负偏,round2 定 Blocker);整层剔除后收益序列=良构计划子集(`target>entry`),`excluded` 计数披露 |
| D2 | 兼容策略 | 拆共核 `_classify_core(fwd, stop, target) -> (outcome, hit_idx)`;既有 `classify_triple_barrier` 变薄 wrapper(零变);新增 `classify_triple_barrier_with_return(...) -> TripleBarrierResult` |
| D3 | 数学复用(默认可翻) | 抽 `risk_metrics_from_returns(returns, sort_keys=None)`(两参:moment 统计按输入序=逐行等价,maxDD/equity 按 sort_keys 序;**非有限值成对过滤**——round2 NEW-3:入口单侧过滤会致 returns/sort_keys 索引错位,须 zip 后成对剔除);链路A 改薄调用方 + golden 快照全等担保 |
| D4 | 存储(默认可翻) | `SignalStatRow.risk_metrics_json`(Text nullable);**格子存在即落全键 dict**(round2 NEW-4:空收益序列落 `sample=0` 全键 dict 而非 NULL——NULL 仅 legacy 一义,excluded 在 100% 剔除时仍可见,与链路A n==0 全键口径对称) |
| D5 | surfacing(round2 载荷修正) | **marker 层不透**(SignalMarker **不声明** `risk_metrics`,Pydantic 自动剥离——内存 marker dict 仍携带供 board 链);`SignalsResponse` 顶层加 `risk_metrics_by_signal_type: Dict[str, Dict[str, Any]]`(O(K≤~10) 而非 O(N markers≤~100),消 100% 冗余);`BoardEntry` 声明单 `risk_metrics` dict(per-entry 一份无膨胀);note 不入 dict(移 Field description + 文档,dict 瘦身 42%) |
| D6 | maxDD 事件序 | `SignalOutcome` 追加 `date`;排序 `(date is None, date, 输入序idx)`;无 code tie-break(诚实标注) |
| D7 | **前置修复(现役 bug)** | `signals_service` per-call resolver cache 键 `code` → `(signal_type, code)`:修复跨 signal_type 错挂 hit_rate/verified 等全部字段的现役缺陷(RED 测试先证 bug 再修);本增量 risk_metrics 依赖此正确性 |
| D8 | expired 口径(round2 NEW-2,默认可翻) | **expired 计入收益序列**(窗末 close 平仓=真实跟单者的完整交易流;链路A 总体含 neither 窗末行,"同口径"宣称由此成真);聚合过滤条件=`return_pct is not None`(不再限 win/loss) |

## 4. 方案

### 4.1 三重门收益(`signal_backtest.py`)

```python
@dataclass(frozen=True)
class TripleBarrierResult:
    outcome: str                      # 'win' | 'loss' | 'expired'
    return_pct: Optional[float]       # 保守跳空感知收益(%);失真形态/无效数据为 None


def _classify_core(forward_bars, *, stop, target):
    """共核:返回 (outcome, hit_idx);hit_idx=触障 bar 下标,expired 为 None。"""
    for i, bar in enumerate(forward_bars):
        hit_target = bar["high"] >= target
        hit_stop = bar["low"] <= stop
        if hit_target and hit_stop:
            return "loss", i     # 同 bar 两触:保守判 loss(语义不变)
        if hit_stop:
            return "loss", i
        if hit_target:
            return "win", i
    return "expired", None


def classify_triple_barrier(forward_bars, *, stop, target) -> str:
    """(既有签名/语义零变,变薄 wrapper)"""
    return _classify_core(forward_bars, stop=stop, target=target)[0]


def classify_triple_barrier_with_return(forward_bars, *, stop, target, entry) -> TripleBarrierResult:
    """三重门分类 + 保守跳空感知收益(D1 用户拍板 + 形态级失真守卫)。

    失真守卫(outcome 无关,round2 Blocker 修正):target <= entry(触发 close 已越过回踩锚
    target 的动量/突破形态)→ 不论 win/loss/expired 一律 return_pct=None——win-only 剔除会
    单边截断(赢的不计、输的全计),整层剔除才保收益序列无选择偏差。

    win:     exit = target(跳空高开不多计盈利)
    loss:    exit = min(触障 bar open, stop)(跳空低开按更差的 open;open 非有限或 <=0 回退 stop,
             0.0 哨兵/NaN 不产假 -100;含 open>=target 高开双杀子案,同取保守,见 §6)
    expired: exit = forward_bars[-1]['close'](窗末平仓,D8:计入收益序列)
    return_pct = (exit - entry)/entry*100,下钳 >= -100;
    entry/exit 非有限或 <=0 → None(防御,outcome 分类不受影响)。
    """
    outcome, hit_idx = _classify_core(forward_bars, stop=stop, target=target)
    if entry is None or not math.isfinite(entry) or entry <= 0 or not forward_bars:
        return TripleBarrierResult(outcome, None)
    if target <= entry:                        # 形态级失真守卫(D1,outcome 之外)
        return TripleBarrierResult(outcome, None)
    if outcome == "win":
        exit_price = target
    elif outcome == "loss":
        o = forward_bars[hit_idx]["open"]
        exit_price = min(o, stop) if (isinstance(o, (int, float)) and math.isfinite(o) and o > 0) else stop
    else:
        exit_price = forward_bars[-1]["close"]
    if not (isinstance(exit_price, (int, float)) and math.isfinite(exit_price) and exit_price > 0):
        return TripleBarrierResult(outcome, None)   # 终门:NaN/0 哨兵一律 None,绝不毒化聚合
    return TripleBarrierResult(outcome, max((exit_price - entry) / entry * 100.0, -100.0))
```

(模块 imports 追加 `import math`。)

**前提改动**:`_bars_as_dicts` 加 `"open"`。**影响面(round1 F2 归因订正)**:`forward_bars` 仅被 `classify_*` 消费;open 键恒在但值不保证有效——日线 `stock_service.py:127` 可为 0.0 哨兵/NaN,分钟 `intraday_normalize.py:28` 仅 dropna close/volume,baseline 模式不经 normalize_ohlcv → isfinite/正值守卫是承重防御;NaN high/low 在 `_classify_core` 比较恒 False 与现行为一致。

### 4.2 SignalOutcome 携带收益与日期

```python
@dataclass(frozen=True)
class SignalOutcome:
    signal_type: str
    market: str
    outcome: str
    return_pct: Optional[float] = None   # 保守跳空感知收益(%);legacy 构造不传 = None
    date: Optional[str] = None           # 触发 bar 的 df.date 原值(str);maxDD 排序键
```

末尾带默认,既有构造零破坏。`_eval` 循环体:`entry = float(df.iloc[t]["close"])`、`date = str(df.iloc[t]["date"])`,改调 `classify_triple_barrier_with_return`,产 `SignalOutcome(..., return_pct=r.return_pct, date=date)`。信号/baseline 模式同改。

### 4.3 聚合(`aggregate_signal_stats`)

per-cell 收益序列(实现于 Step 2 分桶时顺路收集,避免重扫):

- **收益序列 = 该格全部 outcome(win/loss/expired)中 `return_pct` 非 None 者**(D8;失真形态与无效数据已在 classify 层置 None)。
- **`excluded` = 该格 `return_pct` 为 None 的事件总数**(含失真 win/loss/expired 与防御 None)。
- **sample 口径(round2 修订)**:`risk_metrics['sample']` = 非 None 收益数 = `win+loss+expired − excluded`,与胜率分母(`SignalStat.sample = win+loss`)**不定序**(通常 ≥,因 expired 计入;动量格子可 <)。三口径关系写进文档防误读(§6)。
- **全键 dict 恒落(round2 NEW-4)**:格子存在(有任何 outcome)即落全键 dict——空收益序列时 `sample=0`、数值全 None、`excluded=N`;**`risk_metrics=None`/DB NULL 仅一义=legacy 未重跑**(与链路A n==0 全键口径对称,100% 剔除时 excluded 仍可见)。
- dict 追加自描述键 `interval`/`horizon`(round2 NEW-7:跨 interval 量纲不可比,脱离行上下文时防误读)。
- **不含 note**(D5:note 移 Field description 与文档,dict 瘦身 42%、落库同步瘦身)。

`risk_metrics = risk_metrics_from_returns(cell_return_list, sort_keys=cell_sort_keys)` 后补 `excluded/interval/horizon` 三键(末位)。`cell_sort_keys = [(date is None, date or "", idx), ...]`(leading-bool 防 None 比较)。`SignalStat` 追加 `risk_metrics: Optional[dict] = None`(末尾带默认;**契约声明**:dict 字段使非 None 实例不可哈希——现无消费方哈希 SignalStat,grep 已核)。

### 4.4 共享数学(D3,默认可翻)

```python
def risk_metrics_from_returns(returns: List[float], sort_keys=None) -> Dict[str, Any]:
    """returns=已下钳收益(调用方输入序,moment 统计按此序求和——与链路A 重构前逐行等价);
    sort_keys=等长排序键,仅 maxDD/equity 按 sorted(zip(...)) 序遍历(None=输入序)。
    非有限值防御(round2 NEW-3 修正):zip(returns, sort_keys) 后**成对过滤**非有限项
    ——单侧过滤会致索引错位、maxDD 排序静默错配;成对剔除保持对齐。"""
```

放 `src/core/backtest_engine.py` 模块级(全 stdlib,无循环 import:signal_backtest ↔ backtest_engine 现互不 import,新增单向边安全)。键集:`sample/mean_return_pct/return_std_pct/sharpe/sortino/max_drawdown_pct/equity_final_pct/worst_single_return_pct`(无 note——调用方按语境处理)。

链路A `_compute_risk_metrics` 重构:过滤 cash/None + 下钳(输入序)→ 共享函数(sort_keys=(date is None, analysis_date, code, idx))→ **末位追加 note**(键序=共享函数输出序+note 末位,与现 `:815-824` 插入序一致——json.dumps 无 sort_keys,键序即 diagnostics_json 落库字节,"字节级不变"由此对落库字节真正成立)。

**字节级担保机制(round1 F3' 修正)**:重构 task 内删除内联数学**前**,对固定+**seed 固定的**随机化样本(§7 确定性纪律)直调旧实现快照 golden dict;重构后断言 `==` 全等 + `json.dumps(old)==json.dumps(new)`(锁键序);golden 留作永久回归。

### 4.5 落库与读路径

- `SignalStatRow.risk_metrics_json = Column(Text)`(nullable,NULL=legacy);`_ensure_signal_stats_columns` 追加第三列检查(docstring/告警同步)。
- ORM 构造:`risk_metrics_json=(json.dumps(s.risk_metrics, ensure_ascii=False, allow_nan=False) if s.risk_metrics is not None else None)`——`is not None` 非 truthy(sample=0 dict 是 truthy 且**必须落库**,D4);`allow_nan=False` fail-closed,外层 per-cell try/except 落 NULL。
- `resolve_marker_hit_fields` 追加 `"risk_metrics"` key:getattr → try json.loads except None → **isinstance(parsed, dict) 检查**(非 dict JSON 防 Pydantic ValidationError)→ `_none` 分支同步加 key。
- **既有桩应改(措辞:非"不补即红")**:`_make_stat`/`_stat`/SimpleNamespace 补 `risk_metrics_json=None`(json try/except 使缺桩静默 None;补桩为语义明确);两处精确 dict 断言 9→10 keys。

### 4.6 surfacing(D5,round2 载荷修正后)

1. **D7 前置修复**:`signals_service` effective_resolver 缓存键 `resolver_code` → `(signal_type, resolver_code)`(修现役跨 signal_type 错挂;独立 task,RED 测试先证 bug:两 signal_type 构造不同 stats,断言修前第二个 signal_type 拿到第一个的 fields、修后各拿各的)。
2. marker 内存 dict:基础/`_llm_marker` 加 `'risk_metrics': None`,回填块 `marker['risk_metrics'] = fields.get('risk_metrics')`(**供 board 链与响应组装用**)。
3. **`SignalMarker` Pydantic 不声明 `risk_metrics`**(刻意——序列化自动剥离,`/signals` markers 零膨胀;这是 Pydantic 显式字段过滤第一次作为瘦身工具而非陷阱使用,测试 §7.9 反向断言"不含")。
4. `SignalsResponse` 顶层追加 `risk_metrics_by_signal_type: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description=...)`——组装层(`build_signals_for_code` 响应组装处)从 rule markers 按 signal_type 收敛一次(O(K≤~10));description 携带口径 note 文案(§6)。
5. `signal_board_service._hit_fields_from_markers` 两处 return + `_degraded_entry` 加 `'risk_metrics': ...`(首 rule marker 一份);`BoardEntry` 声明 `risk_metrics: Optional[Dict[str, Any]]`(per-entry 无膨胀)。
6. `api/v1/schemas/stocks.py` 补 `Dict`/`Any` import。
7. Field description 必含(round2 NEW-5):**"描述性统计,无置信区间、未经多重检验校正——不得作为跨格子挑选依据(verified 才是校正后判据)"**。
8. 前端不动(mapper 未映射键自然忽略)。

### 4.7 配置

零新配置。

## 5. 兼容性

- 纯追加(dataclass 末尾默认字段/一列 nullable/additive keys);classify_triple_barrier 零变;既有 win_rate/CI/校正列计算路径不动;链路A 输出经 golden 全等锁字节级不变。
- **D7 是唯一的行为修复**:跨 signal_type 的 hit_rate/verified 等字段从"错挂"变"正确"——board/signals 展示会变(修复性变化,CHANGELOG [修复] 单列一条)。
- legacy 行 NULL → API null(唯一语义);重跑 `--signal-backtest` 后出现(**手动,无调度自动刷新**,§0)。
- 回滚:单分支 revert;DB 一列无害。

## 6. 诚实边界(文档 + Field description)

- **重叠窗口自相关**:相邻 bar 信号前瞻窗高度重叠,收益序列非独立——Sharpe iid 假设破坏,数值偏乐观(LdP uniqueness 加权不做)。
- **跨标的混流**:一格混合自选池多标的,maxDD 是"信号型事件流"回撤,非单标的非组合。
- **毛收益**:无 fee/slip/税。
- **exit 侧宁低估不高估**(round2 措辞限定):win 弃跳空增益、loss 吃满跳空损失——非市价撮合仿真。
- **收盘瞬间成交假设(round2 LOOKAHEAD)**:信号 bar t 收盘数据确认,entry 取同一 close,隐含"确认瞬间按确认价成交"(日线≈MOC 近似,分钟≈bar 关闭即市价);真实可得入场更近 t+1 open,close→next-open 跳空收益被计入——动量类 bullish 信号入场侧平均偏乐观,与 exit 侧保守方向相反,净向混合。
- **失真形态整层剔除(D1)**:`target<=entry` 事件(动量/突破形态)win/loss/expired 一律不入收益序列,`excluded` 计数;收益指标只代表**良构计划子集**;win_rate 仍计全体(两轴总体不同,由 excluded 显式桥接)。
- **同 bar 双触且 open>=target 子案**:高开双杀,真实时序本可开盘止盈,保守仍 loss@stop——三个双触子形态中失真最大者,方向仍宁低估。
- **sample 三口径**:`SignalStat.sample`(胜率分母=win+loss)与 `risk_metrics['sample']`(=win+loss+expired−excluded)**不定序**;`hit_sample`=前者。
- **无 CI 无校正的点估计(round2 NEW-5)**:per-cell Sharpe/Sortino 等 8 指标无置信区间、未经 Bonferroni 校正——与校正后的 `verified` 并排透出时,**不得作为跨格子挑选/排序依据**(那会重开 Inc 1c 刚封堵的 data-snooping 通道);定位=已选格子的描述性画像。Field description + 文档双写。
- **跨 interval 量纲不可比(round2 NEW-7)**:分钟桶(5m×10≈50 分钟)与日线桶(10 天)的 mean/maxDD 差数量级——dict 内嵌 `interval`/`horizon` 自描述,文档明示不可跨桶比较。
- **maxDD tie-break 无 code 维度**;**生效前提=手动跑 `--signal-backtest`**(§0)。

## 7. 测试(离线确定性)

1. **classify wrapper 零回归**:既有测试不改一字全绿;新增 wrapper == `_classify_core[0]` 逐 case。
2. **收益口径逐分支锁定**(直调纯函数,手算 pin):win 无跳空;**失真守卫三态**(target<=entry 的 win/loss/expired 各 → None,outcome 不变——round2 Blocker 回归锚);loss 跳空按 open/无跳空按 stop;双触三变体(open<stop→open;stop<open<target→stop;open>=target→loss@stop pin 符号);expired 计收益(D8);守卫全套(entry NaN/<=0、open NaN/0 回退 stop、exit NaN/0→None);负 exit(非物理)→ 终门拦截返 None;钳位保留为与链路A 对齐的防御,long 语义下不可达。
3. **_eval 贯通**:信号/baseline 模式产出带 return_pct/date。
4. **共享数学 golden 全等**:固定+seed 固定随机样本,旧实现快照 == 新实现 + json.dumps 键序全等;链路A Inc 1a 测试不改一字全绿;直调单测(空/单元素/pin 手算/**成对过滤**:含 NaN 项剔除后 returns 与 sort_keys 仍对齐,maxDD 正确/sort_keys 只影响 maxDD 不影响 mean)。
5. **聚合**:混合格 risk_metrics 独立;**expired 计入收益序列**(pin 含 expired 的 sharpe);excluded 覆盖失真三态(构造 1 失真 win + 1 失真 loss 断言 excluded=2);**全剔格落全键 dict**(sample=0/数值 None/excluded=N——非 None,round2 NEW-4 回归锚);dict 含 interval/horizon 键。
6. **maxDD 排序判别式**:3 元 date 序≠输入序 maxDD 不同;mean/sharpe 不随序变;date 全 None 回退不 TypeError。
7. **落库/迁移/roundtrip**:幂等补列/NULL=legacy/写读回/坏 JSON→None/非 dict JSON→None/allow_nan=False 落 NULL;**sample=0 dict 落库非 NULL**(truthy 陷阱反向锚)。
8. **读路径桩改**:补 `risk_metrics_json=None`;精确 dict 断言 9→10 keys;两测试文件全绿。
9. **surfacing 贯通(D5 形态)**:resolver→marker 内存 dict→board entry→`BoardEntry` model_dump 含 dict;**`SignalMarker` model_dump 不含 `risk_metrics`(反向断言,刻意剥离)**;`SignalsResponse.risk_metrics_by_signal_type` 按 signal_type 收敛正确(两 signal_type 各自的 dict)。
10. **D7 cache 键修复**:RED 先证现役 bug(两 signal_type 不同 stats,修前第二个拿错)→ 修后各拿各的;并证 SELECT 次数 = distinct signal_type 数(缓存仍有效)。
11. **零回归 grep**:四函数消费面两类风险各核。

## 8. 交付结构

改了什么(收益管道+共享数学+per-cell 指标落库+API map 透出+D7 现役 bug 修复)/ 为什么 / 验证(逐分支 pin+golden 全等+贯通+ci_gate 全量)/ 未验证项(真网可选;生产生效需手动跑一次)/ 风险(低:纯追加+零变 wrapper+字节级锁;D7 为修复性行为变化)/ 回滚(单分支 revert)。

CHANGELOG 两条扁平:`[新功能]` 链路B 风险画像;`[修复]` resolver 缓存跨 signal_type 错挂。文档:`docs/signal-credibility.md` 追加风险画像节(口径/诚实边界/手动触发前提)。
