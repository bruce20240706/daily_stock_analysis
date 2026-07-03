# 链路B 样本外 holdout 切分(Inc 1e)设计 spec

日期:2026-07-03。状态:v2——已过对抗式审查(4 视角 16 发现→9 确认全部折进:
1 Blocker[F1 接线死配置无测试]+4 Important[STAT-1 跨市场异构跨度→cutoff 改 per-market/
C1 payload 键集断言站点/F2 cutoff 判别式弱/F3 window_end 判别式弱]+4 Minor;7 证伪)。
待用户审阅。
上游:docs/strategy-actionable-signal-system.md Inc 1(L2 差距①②:walk-forward+purged/embargo、样本外无污染评测)。
前置:Inc 1c 多重检验校正(4590964c)、Inc 1d per-cell 风险画像(2fdce3b5)已合入。

## 0. 背景与目标

### 0.1 问题

链路B 信号可信度统计目前**完全时间盲**:每次 `--signal-backtest` 以当天为锚回看固定窗
(日线 365 天,分钟按 band),全部股票全部 bar 的 outcomes 混在一起单次聚合
(`aggregate_signal_stats`,src/services/signal_backtest.py:343);`SignalOutcome.date`
仅用于 maxDD 时序排序,胜率/CI/Bonferroni/verified 判定不看日期。后果:一个格子的
verified 可能完全由某段行情(如前半窗单边市)贡献,近期已失效却仍顶着"已验证"徽章——
时间稳健性缺口。Inc 1c 解决了"跨格子挑选"的 data-snooping,本增量解决"跨时间持续性"。

### 0.2 语义定位(与经典 walk-forward 的差异)

链路B 信号是**固定阈值规则**,无参数拟合,故此处"样本外"验证的不是训练参数,而是
**格子历史超额的时间稳健性**:把每格事件按触发时间切成 train(早段)/OOS(晚段),
并排披露两段的胜率与超额——OOS 段不参与 verified 判定所依赖的统计吗?**不**,见 D1:
本增量 verified/win_rate 保持全样本口径字节级不变,OOS 是新增披露维度。

### 0.3 目标

每个 (signal_type×market×interval×horizon) 格子新增一份 holdout 切分报告
(`oos_json` 列,NULL=legacy/未启用),含 train/OOS 两段的胜率、样本数、基准、超额,
以及切点日期、embargo 剔除数;经 resolver→marker→board→Pydantic 透出;opt-in 配置
`SIGNAL_BACKTEST_OOS_FRACTION`(默认 0.0=关闭,**默认字节级不变**)。

### 0.4 非目标

- 滚动多窗 walk-forward(窗长/步长)——样本现实(min_sample=10 已"偏低"、默认自选池
  3 只)会把 K 折饿死;顺延到样本池扩大后(rolling 结构可日后折进 oos_json 扩展键)。
- as-of 元验证报告(verified 徽章的前瞻追踪)——A 落地后可由"筛 verified 格看
  oos_excess"近似;独立报告形态顺延。
- verified 门控变更(见 D1)。
- 前端渲染(显式非目标,与 Inc 1d 同决策;数据经 API 可达,渲染独立小增量跟进)。
- OOS 段的 Wilson CI 与多重检验校正(描述性披露,见 §6 诚实边界)。
- 链路A(其逐次分析天然前瞻,无本缺口)。

## 1. 决策记录(用户暂离,取推荐默认;回来可改)

| # | 决策 | 取值 | 依据 |
|---|---|---|---|
| D1 | OOS 消费方式 | **纯披露,不动 verified**:全样本 win_rate/CI/校正/verified 字节级不变;oos_json 为纯新增 | 零行为风险;与 1c/1d additive 惯例一致;门控可后续增量升级 |
| D2 | 切分形态 | **单一时间 holdout**(一个切点),滚动 walk-forward 顺延 | 样本现实;策略文档大项允许拆增量交付(1a/1c/1d 先例) |
| D3 | 切点定义 | **per-market 去重日期网格分位点**:baseline outcome 按 market 分组,各组 distinct 日期字符串字典序升序,取 `sorted_dates[floor((1-f)×(len-1))]` 为该市场 cutoff;每格用自己市场的 cutoff | 零日期解析(纯字典序,复用 maxDD 排序的同格式假设);交易时间分位优于日历分位;**审查 STAT-1:全 family 共享 cutoff 在跨市场异构回看深度下(us/hk 5m=58 天 vs crypto 365 天)会让短跨度市场 train 结构性为空、cn 实际切分比例静默偏离名义值,且 crypto(UTC)/us(美东)/cn·hk(当地)钟基混比切点漂移可达 ~13h——per-market 推导一并消解;格子本就 signal_type×market,baseline 已按 market 分桶,cutoff_date 逐行落库,零 schema 变更** |
| D4 | purge/embargo | **精确 bar 级**:`SignalOutcome` 新增末尾字段 `window_end_date`(bar t+horizon 的日期);train=`window_end_date≤cutoff`,OOS=`date>cutoff`,其余=embargo 剔除并计数 | 响应策略文档 purged/embargo 要求;窗跨切点者两边都不算,杜绝泄漏 |
| D5 | 判据形态 | train/OOS 两段各报 `win_rate/sample/baseline_win_rate/excess`,外加 `cutoff_date/fraction/embargoed/undated`;无衍生布尔判定字段;**子键 excess=胜率点估计差(win_rate−baseline_win_rate),与既有 SignalStat.excess 的保守口径不同名义域,Field description 与文档必须点明此差异防跨字段误比** | 描述性披露,判断留给读者;避免又造一个未校正的"oos_verified"伪判据 |
| D6 | 落库 | 单列 `oos_json`(Text nullable,NULL=legacy/未启用),幂等补列;启用时全键 dict 恒落(空段 sample=0,不落 NULL) | 镜像 risk_metrics_json 全套基建(序列化降级/防御解析/迁移模式);None 单义=未启用 |
| D7 | 透出深度 | resolver 第 11 键 `oos` → marker 内存 dict → board 三站点 → `SignalsResponse.oos_by_signal_type` 顶层 map + `BoardEntry.oos`;**SignalMarker 不声明**(D5' 防逐 bar 膨胀,同 1d) | 完整复刻 1d 管道形态,防"死字段深一层"(1c 教训) |
| D8 | 开关 | `SIGNAL_BACKTEST_OOS_FRACTION`,float,默认 0.0=关闭,loader+函数双钳 [0.0, 0.5];registry(backtest 类,**display_order=72**,已核 70/71 后首个空位)+locale+env.example 注释行 | 印花税/FWER 先例:注册即 Web 可暴露,须齐 locale/examples/docs 否则两测红 |
| D9 | 拒绝的替代 | 把 OOS 折进 risk_metrics_json 子键(白嫖 1d 管道) | risk_metrics 键集契约已被测试/文档钉死;语义混淆(风险画像≠时间稳健性);拒绝 |

## 2. 数据流

```
_eval(:166)                    aggregate_signal_stats           service→storage
SignalOutcome                  f=0 → 现路径字节级不变            oos_json=None(off/legacy)
 +window_end_date  ──────────▶ f>0 → cutoff(D3)                 ┌────────────────┐
 (bar t+horizon 日期,           per cell: train/OOS/embargo(D4) │ SignalStat.oos │→ 序列化(镜像
  末尾默认 None)                两段子统计(D5)→ SignalStat.oos  └────────────────┘   _serialize_risk_metrics)
                                                                      │
resolver 第11键 oos ◀── json 防御解析(镜像 risk_metrics) ◀── SignalStatRow.oos_json
      │
marker 内存 dict['oos'] → board 三站点 → SignalsResponse.oos_by_signal_type / BoardEntry.oos
                          (SignalMarker 不声明,Pydantic extra=ignore 剥离)
```

## 3. 切分语义(精确定义)

设 f = 钳后 oos_fraction(钳域 [0.0, 0.5],**oos_json 的 fraction 键落钳后值**而非
原始入参),f=0 → 整条切分逻辑短路(early-return,现路径字节级不变)。

0. **分类宇宙(钉死)**:三分**仅对 `outcome ∈ {win, loss}` 的事件**进行,expired 不
   参与任何 OOS 计数(与现行 SignalStat.sample=win+loss 一致;对比:risk_metrics 的
   收益序列含 expired——两口径差异在文档点明)。由此得**守恒恒等式**:每格
   `train.sample + oos.sample + embargoed + undated == SignalStat.sample`(精确划分,
   §7.3 的承重回归锚)。
1. **cutoff 推导(per-market,审查 STAT-1 修订)**:baseline outcomes 按 `o.market`
   分组;每市场 `dates_m = sorted({o.date for o in baseline_m if o.date is not None})`;
   若 `len(dates_m) < 2` → 该市场全部格子 oos_json 落"退化 dict"
   `{"cutoff_date": None, "fraction": f, "degenerate": true}`(非 NULL——None 单义=未启用);
   否则 `cutoff_m = dates_m[floor((1-f)*(len(dates_m)-1))]`(Python `math.floor` 于
   浮点乘积上,实现与测试同一语义)。每格使用**自己市场**的 cutoff。无 baseline 事件
   的市场(理论不可达,防御)同落退化 dict。
   用 baseline(全 bar 网格,最稠密)而非信号事件推导,避免稀疏信号扭曲分位。
2. **每格三分**(signal 与 baseline 的 win/loss 事件同规则;谓词按序短路,undated 优先):
   - undated:`date is None`(legacy 手工构造),计数披露,不入任何段
   - train:`window_end_date is not None and window_end_date <= cutoff_m`
   - OOS:`date > cutoff_m`
   - embargo:其余(窗跨切点:date≤cutoff_m<window_end_date;或 window_end_date
     缺失但 date≤cutoff_m——保守归 embargo 不归 train)
3. **两段子统计**(每格):`win_rate = win/(win+loss)`(0 样本→None)、`sample=win+loss`、
   `baseline_win_rate`(该段**同市场** baseline 事件同式)、
   `excess = win_rate - baseline_win_rate`(任一为 None→None;**点估计口径**,区别于
   SignalStat.excess 的 ci_low 保守口径)。均 round4。
4. **oos_json dict 键集**(启用且非退化时恒落全键):
   `{"cutoff_date": str, "fraction": float(钳后), "embargoed": int, "undated": int,
     "train": {"win_rate","sample","baseline_win_rate","excess"},
     "oos": {"win_rate","sample","baseline_win_rate","excess"}}`
5. **不变式**:f=0 时 aggregate 返回值与现版本逐字段相等(含 risk_metrics);f>0 时
   仅 SignalStat 新增 oos 字段有值,其余全部字段(win/loss/sample/ci/校正/risk_metrics)
   与 f=0 完全一致——切分**只读** outcomes,不改任何既有统计的输入。

## 4. 各层改动

### 4.1 `src/services/signal_backtest.py`
- `SignalOutcome` 末尾追加 `window_end_date: Optional[str] = None`(docstring 同步;
  frozen dataclass 末尾默认字段,legacy 构造零破坏——1d 先例)。
- `_eval`(:166 循环体):构造 outcome 时补 `window_end_date=str(df.iloc[t + horizon]["date"])`
  (循环上界 `n - horizon` 保证索引有效;baseline 与 per-signal 两分支同补)。
- `SignalStat` 末尾追加 `oos: Optional[dict] = None`(同 risk_metrics 先例,含不可哈希注释)。
- `aggregate_signal_stats` 尾参 `oos_fraction: float = 0.0`(docstring:默认仅供独测,
  生产必须显式传 config 值);函数内钳 [0.0, 0.5];f>0 时按 §3 计算并挂 `oos=...`。

### 4.2 `src/services/signal_backtest_service.py`
- run():aggregate 调用处传 `oos_fraction=cfg.signal_backtest_oos_fraction`。
- ORM 构造:`oos_json=...`——**不新增平行 helper**(AGENTS.md 禁平行实现;顶级复审 R1):
  把既有 `_serialize_risk_metrics`(:101,通用 json.dumps+降级)泛化为
  `_serialize_cell_json(payload, field_label)`(warning 文案带 field_label 与格子身份,
  吸收 1d Minor),risk_metrics 与 oos 两个调用点共用。已核 warning 文案无测试锁定
  (全仓 grep 唯一命中为源码行),泛化零回归风险;1d 调用点输出字节不变。

### 4.3 `src/storage.py`
- `SignalStatRow` 追加 `oos_json = Column(Text)  # holdout 切分报告 JSON;NULL=legacy/未启用`。
- `_ensure_signal_stats_columns` 追加第四列幂等 ALTER;docstring 三列→四列。

### 4.4 `src/services/signal_hit_rate.py`
- `_none` 追加第 11 键 `"oos": None`;读路径防御解析**抽共用局部 helper**
  `_parse_json_dict(raw) -> Optional[dict]`(顶级复审 R1:9 行防御块第二份逐字拷贝
  违反禁平行实现,抽出后 risk_metrics 与 oos 两处共用;getattr 容错桩/坏 JSON/
  非 dict→None 语义不变);返回 dict 追加 `"oos": oos`;docstring 10→11 键。
- 既有桩(_make_stat/_stat/SimpleNamespace 各处)补 `oos_json=None`;
  两处精确 dict 断言 10→11 键(1c/1d 两次先例,漏补必红)。

### 4.5 `src/services/signals_service.py` + `signal_board_service.py` + `api/v1/schemas/stocks.py`
- 完整复刻 1d D5 管道:`_marker_from_vpsignal` 基础 dict `'oos': None`+回填
  `fields.get('oos')`;`_llm_marker` `'oos': None`;`build_signals_payload` 收敛
  `oos_by_signal_type`(isinstance dict + first-wins,与 risk_by_type 同构);
  `_hit_fields_from_markers` 两 return+`_degraded_entry` 三站点;
  `SignalsResponse.oos_by_signal_type: Dict[str, Dict[str, Any]]`(default_factory=dict)、
  `BoardEntry.oos: Optional[Dict[str, Any]]`(None);**SignalMarker 不加**。
  Field description 必含:样本外为描述性统计、无 CI、未经校正、切点与口径自描述
  (per-market cutoff)、子键 excess=胜率点估计差(非 SignalStat.excess 的 ci_low
  保守口径)、空 dict/null=legacy 或未启用、degenerate 语义。
- **既有精确键集断言必改站点(审查 C1,漏补必红)**:
  `tests/test_signal_board_service.py:51` 顶层 payload 键集断言补 `oos_by_signal_type`
  (8→9 键);§4.4 所列 resolver 层两处精确 dict 断言 10→11 键。

### 4.6 配置四件套
- `src/config.py`:字段 `signal_backtest_oos_fraction: float = 0.0`;loader
  `parse_env_float` + 钳 [0.0, 0.5](函数内二次钳,双钳与 FWER 先例一致)。
- `src/core/config_registry.py`:backtest 类,display_order=72(已核空位),
  validation {min:0.0, max:0.5},examples/docs/help_key 全齐(缺则 web-metadata 测红)。
- locale(settingsHelp)双语文案:明示"0=关闭;启用后既有徽章与胜率口径不变,
  仅新增披露";禁用"样本外验证通过"之类判定式措辞;**notes 必含"修改后需重新运行
  --signal-backtest(值>0)才会落 oos_json";impact 字段按 backtest 类内惯例填写
  (影响 oos_json 与 API oos_by_signal_type/BoardEntry.oos 披露,不影响 verified
  徽章与胜率口径)——审查 CFG-2**。
- `.env.example`:加注释行 `# SIGNAL_BACKTEST_OOS_FRACTION=0.3`,沿用 FWER opt-in
  键注释行约定(**审查 CFG-1 订正:覆盖门只约束未注册键的活动行,本键注册后活动行
  不红,注释行是约定非强制**)。

### 4.7 docs
- `docs/signal-credibility.md` 追加"样本外 holdout 切分"节:切点口径(交易日期网格
  分位)、embargo 语义、undated、退化情形、与 verified 的关系(不影响)、生效前提
  (配置>0 且重跑 --signal-backtest)、legacy NULL 语义、跨 interval 不可比。
- `docs/CHANGELOG.md` [Unreleased] 一条 [新功能](扁平)。

## 5. 兼容性

- 默认(f=0)全链字节级不变:aggregate early-return;oos_json 恒 NULL;resolver 键
  多一个恒 None;Pydantic 字段 additive(旧客户端忽略新键);无 schema 破坏。
- legacy 行(增量前落库):oos_json NULL → resolver None → 全链 None-safe(与
  risk_metrics 同路径,Inc 1d 已实证该管道)。
- verified 消费面:已证全后端纯展示无 filter(1c 复审 I-2),本增量不触碰。

## 6. 诚实边界(文档必写)

- OOS 段是**描述性统计**:无 CI、未经多重检验校正、样本极小(默认池下常为个位数),
  不得单独作为格子取舍依据;它回答的是"超额是否集中在早段"这一个问题。
- 窗口重叠自相关在两段内部依旧存在(与全样本口径同);跨标的混流同。
- 切点随每次批跑的数据窗滑动(锚=当天),两次跑的 OOS 集不同——oos_json 是写时快照。
- 两段 sample 之和 ≠ 全样本 sample(差额=embargo+undated,均如实披露;四类对
  win/loss 事件构成精确划分,守恒恒等式见 §3.0);embargo 是防泄漏的刻意丢弃,
  undated 是数据缺陷披露;全样本头部统计不受影响。
- 切点 per-market 各自推导:跨市场的 train/OOS 段覆盖的日历区间不同(回看深度
  本就不齐),oos_json 只能与**同市场**格子横比,跨市场比较无意义。
- min_sample 只约束全样本 verified;OOS 段无最小样本门槛(sample 如实披露,读者自判)。
- fraction 是**交易日期格点比例,非事件比例**(顶级复审 R2):信号事件在时间上非均匀
  分布,且 embargo 再剔一块,故 `oos.sample` 不必 ≈ fraction×sample——切的是时间轴,
  不是样本配额。
- baseline 侧 undated 事件(生产不可达,legacy 桩才有)从两段基准率中静默剔除且不计数
  (顶级复审 R3);cell 侧 undated 有显式计数,两侧口径差异以此句为准。

## 7. 测试计划(RED→GREEN,关键判别式)

1. **字节级默认**:f=0 时 aggregate 输出与既有邻域测试全绿互证(golden/精确断言
   基线即"现版本行为"的操作性定义,非自比自);服务层默认 config 跑 run() 断言
   落库 oos_json 全 NULL(与 #13 对偶)。
2. **cutoff 分位判别式(审查 F2 强化,参数化≥2 组杀变体)**:
   (len=5, f=0.25) → floor(0.75×4)=3(杀 len 基变体 floor(3.75)-1=2);
   (len=5, f=0.3) → floor(2.8)=2(杀 round 变体 round(2.8)=3)。
   字典序无解析判别:用字典序≠时间序的串(如非补零 "2026-9-05" vs "2026-10-01",
   字典序倒挂)或非日期 token 'a'/'b'/'c',断言纯字典序行为。
3. **精确 purge 三分 + 守恒恒等式(审查 STAT-4 强化)**:构造覆盖
   train/OOS/embargo/undated 四类各≥1(含 expired 事件混入,断言其不进任何计数),
   承重断言 `train.sample + oos.sample + embargoed + undated == s.sample`;
   特别锁"窗跨切点(date≤cutoff<end)必 embargo 不入 train"(泄漏回归锚)。
4. **window_end_date 缺失保守归 embargo**(date≤cutoff 且 end=None)。
5. **两段子统计**:手算 pin win_rate/baseline/excess;0 样本段 win_rate=None、
   excess=None 不崩。
6. **退化情形**:某市场 distinct 日期<2 → 该市场格子 degenerate dict 非 NULL,
   同 run 其他市场正常切分(可与 #14 双市场用例合并构造)。
7. **既有统计不动**:同一 outcomes 列表先 f=0 后 f>0 两次调用,头部字段逐一相等,
   且两次调用间断言输入列表未被变异(id/顺序/元素同一)。
8. **迁移**:legacy 库(无 oos_json 列)打开幂等补列、既有行 NULL;roundtrip。
9. **resolver**:11 键;防御四路(None/坏 JSON/数组/标量)→None;精确 dict 断言 10→11。
10. **surfacing**:marker 内存携带/SignalMarker 剥离反向断言/顶层 map first-wins/
    board 三站点/BoardEntry 保留 dict;**payload 顶层键集断言 8→9 键
    (test_signal_board_service.py:51,审查 C1)**。
11. **配置(审查 F7/CFG-1 修订)**:loader 钳制(负值→0、0.9→0.5);**函数侧二次钳
    直调断言**:aggregate(oos_fraction=0.9) 与 0.5 同 cutoff 且 dict fraction 键=0.5
    (钳后值),aggregate(oos_fraction=-0.1) 走 f=0 早退 oos is None;
    registry↔locale 双测;env.example 为注释行约定(不依赖覆盖门 RED)。
12. **_eval 贯通(审查 F3 强化)**:逐 bar 唯一日期夹具、horizon≥2,由 outcome.date
    反查 t,断言 `window_end_date == str(df.iloc[t+horizon]["date"])`(精确相等,
    弃 ">" 弱判别);evaluate_baseline_outcomes 与 evaluate_signal_outcomes
    **两分支同型断言**(锁双构造点同补)。
13. **服务级接线端到端(审查 F1-Blocker,防静默死配置)**:monkeypatch config
    `signal_backtest_oos_fraction=0.3`,复用既有 run() 测试夹具跑通,双断言:
    (a) spy 证 aggregate 收到 oos_fraction==0.3(镜像 for_market_interval spy 先例);
    (b) 落库 SignalStatRow.oos_json 非 NULL 且 json.loads 含 cutoff_date/train/oos
    全键(同时逮 ORM 序列化行遗漏)。
14. **双市场异构跨度(审查 STAT-1 回归锚)**:构造市场 A 长跨度(365 格点)+市场 B
    短跨度(58 格点)混池,断言两市场 cutoff 各异、B 市场 train 段非空
    (per-market 分位对 B 仍切出名义比例,而全局池化会把 B 全推入 OOS——判别式)。

## 8. 验证矩阵

- 后端:`PATH=.venv/bin:$PATH ./scripts/ci_gate.sh`(全量,预计 +25~35 测试)。
- 前端:无组件渲染改动;若 Pydantic/locale 触及 web 元数据测试面则跑 web-gate
  (lint+build)——registry+locale 变更按印花税先例**会**触发,计划内跑。
- 真网端到端:可选(crypto 关沙箱前台 + BINANCE_BASE_URL=data-api.binance.vision,
  f=0.3 跑通 processed=1);离线确定性用例为主。

## 9. 回滚

- 未合并:删分支。合并后:revert merge commit(additive 契约,oos_json 列残留无害——
  旧代码 ORM 忽略多余列,Inc 1d 回滚彩排已实证同型兼容)。
- 数据:无需操作;配置回 0 即行为关闭。
