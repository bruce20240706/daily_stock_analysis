# 港股通南向维度(Inc 2)设计 spec

日期:2026-07-03。状态:**v3**——已过 4 视角对抗审查(28 findings)+ controller 逐条裁定折进
(2 Blocker、13 Important、含端点/列名本地 inspect 核实)。待用户审阅。
上游:docs/strategy-actionable-signal-system.md Inc 2(L4:港股通≠港股——成份/南向资金/持股统计;
汇率与日历差异**不在**范围)。前置:HK 市场数据面已有;合规档 2026-07-03 暂定「自用内部」。

## 0. 背景与目标

### 0.1 问题

系统有"港股"数据无"港股通"专属逻辑。对 A股/港股通投资者:①一只 HK 股票**不在港股通成份内
就买不了**(内地账户),报告零感知;②南向资金(个股持股、市场净流)是 HK 标的的重要资金面,
当前 HK 报告资金面为空(`_build_offshore_fundamental_context` 跳过 A股专属块)。

### 0.2 关键现实约束(本地 inspect akshare 1.18.64 源码已核,活性待真网)

三端点**真实列契约**(下方设计据此,非臆造):
- **成份** `stock_hk_ggt_components_em()`:行情榜单,列含 `代码`(东财 f12,**裸 5 位**如 "00700")/
  `名称`;单请求两腿(沪 DLMK0146+深 DLMK0144)合并,**无腿标识、无日期列、全有或全无**。
- **个股南向持股** `stock_hsgt_stock_statistics_em(symbol="南向持股", start_date, end_date)`:
  返回**全市场**南向持股表,列含 `股票代码`/`股票简称`/`持股日期`/`持股数量`/`持股市值`/
  `持股数量占发行股百分比`/`持股市值变化-1日|-5日|-10日`/`当日收盘价`/`当日涨跌幅`;
  **按 TRADE_DATE 降序(sortTypes=-1)分页(pageSize=1000)**,某股最新行=该股 `持股日期` 最大行。
  **注:原 v1 用的 `stock_hsgt_individual_em` 已证实是北向个股接口(默认参 002008),已弃。**
- **市场净流** `stock_hsgt_fund_flow_summary_em()`:列含 `类型`/`板块`/`资金净流入`/`成交净买额`/
  `当日资金余额`/...;南向为 `港股通(沪)`/`港股通(深)`两行;金额列均 `to_numeric(errors="coerce")`
  后 `/10000`——**coerce 产 NaN**。

其他约束:北向(HSGT 个股级)已 2024 交易所永久停更(M4-B-2 spec:17 核验并放弃)——**只做南向**;
南向 2024 起仅 **EOD** 无实时。本沙箱**东财不可达**→真网端到端 deferred(与 HK MVP 同约束,
策略文档验收原文允许);离线 fixtures 全覆盖为主。报告"额外特征"有完整蓝图:M4-B-2 融资融券
(adapter → manager context 门控 → **pipeline `fill_*_if_needed`** → analyzer presence-only builder
→ report_schema → report_language → notification;**margin 是 LLM 后填充,不喂 prompt、不碰
decision_stability**)。fundamental_* 配置全部不进 registry/locale。

### 0.3 目标(v3 范围)

HK 标的分析报告新增「港股通」段:成份 `eligible`(三态)+ 个股南向持股 + 市场级南向净流(EOD);
经 fundamental context → pipeline fill → 报告四层透出;**纯展示维度,不喂 prompt、不改任何
信号/回测/decision_stability**;cn/us/etf 门控 not_supported,非 HK 标的字节级零行为变化。

### 0.4 非目标 / defer(v3 明确边界)

- **看板 `BoardEntry.ggt_eligible` 注解 defer 到 Inc 2b**(审查 CONTRACT-4/R3/R5/F5/F6/F9 揭示:
  模块级进程内缓存在文档推荐部署(systemd `main.py --schedule` 独立进程跑日报 vs uvicorn server
  跑看板)下**跨进程不可见**→看板恒 None,是结构性失效非冷启动;且撞 fundamental 超时 worker 槽
  争用与看板延迟)。**"标的可买性"目标由报告段的 `eligible` 字段达成**(读 HK 报告即见港股通成份
  状态);看板注解需 eligible 快照落库/随报告持久化的重设计,拆独立增量。**这是本 spec 最大范围
  收窄,用户可在审阅门反转(反转则改为"看板保留 + §6 诚实边界写明仅 --serve/web 触发同进程部署
  可暖、systemd 部署恒 None")。**
- 硬 universe 过滤(见旧决策:注解即达成,人工池+自用档下硬过滤反伤;TradeSignal 契约落地再升级 gate)。
- 北向任何形态(永久停更)、南向实时流(仅 EOD)。
- CNH 汇率、两地交易日历差异(L4 的④⑤)。
- agent-tools 层暴露(margin 先例无此层,YAGNI)。
- 前端渲染(报告字段经既有报告链渲染;无新增前端组件——本增量不改 apps/dsa-web,**免 web-gate**)。
- `change_5d` 持股变动字段(端点只有**持股市值变化**含价格效应,无纯股数变动列;误映射会造
  "南向大幅加仓"假象——YAGNI 砍,首个真网核验后按持股数量时序差分再补)。
- 统一仓库三套并行 HK 判定实现(已知疣,超范围;只复用 canonical `normalize_stock_code`)。

## 1. 决策记录(用户暂离取推荐默认;审阅门逐条可否决)

| # | 决策 | 取值 | 依据/审查来源 |
|---|---|---|---|
| G1 | 过滤 vs 注解 | 报告段呈现 `eligible` 三态,不做硬过滤 | 自用档+人工池;注解达成"可买性" |
| G2 | 数据面 | ①成份→eligible;②个股南向持股(数量/市值/占发行股比/持股日期);③市场级南向净流(EOD) | 策略文档;change 列砍(F8) |
| G3 | 端点 | 成份=`stock_hk_ggt_components_em`;持股=`stock_hsgt_stock_statistics_em(symbol="南向持股",start/end)` 全市场表按归一码查该股最新(持股日期最大)行;净流=`stock_hsgt_fund_flow_summary_em` 南向两行 | §0.2 本地 inspect 核实;无第二源(F2-scope:facade 兜底对南向不适用) |
| G4 | fail 语义 | 名单不可得→eligible=None(unknown,禁 False);持股/净流不可得或本股无行→对应子块 None;status ok/partial/failed/not_supported 沿 `_build_fundamental_block` | 不可达 fail-closed=不编造,显式 unknown |
| G5 | **共享键函数(Blocker F1)** | `_ggt_key(code)`:`normalize_stock_code` 后若结果仍为纯 1-5 位数字则补 `"HK"+zfill(5)`;成份表 ingest 侧与查询侧**同一函数**,禁两侧各写 | `normalize_stock_code('00700')=='00700'≠'HK00700'`(base.py:188),裸码建 set 会致 eligible 全假阴性;前科 yfinance c356d6b0 |
| G6 | **pipeline 接线(Blocker CONTRACT-2)** | 报告段进 data_perspective 靠 `fill_ggt_if_needed(result, fundamental_context)`(仿 analyzer.py:944)+ `src/core/pipeline.py` **两处调用点**(镜像 :631/:1154 margin);**LLM 后填充,不喂 prompt** | 只写 builder 会四层全绿但报告端到端不出现(核心交付静默落空) |
| G7 | 缓存 | 成份名单+南向持股日表+市场净流=三份模块级缓存(dict+lock+ts),共用 TTL `GGT_LIST_CACHE_TTL_SECONDS` 默认 43200s(12h);**失败负缓存短 TTL 300s**(R1);持股表一次抓服务当日全部 HK 标的 | 三端点全"全市场级",按股打端点放大请求;负缓存防死端点每报告重打 |
| G8 | 并发/锁纪律(R2/R5) | 三 leg 各自 `_run_with_retry` 共享一个 monotonic deadline,结果按 leg 独立装配(某 leg 超时不丢已完成 leg);**锁只护 dict 读写,网络抓取一律锁外;single-flight 去重(in-flight 时其他写路径用旧值/None 不等待)** | margin 单端点蓝本失配;泄漏超时线程带锁会卡看板/并发报告 |
| G9 | 合理性守卫(R4) | 成份表**最小行数门槛**(<50 行视为"表不可得"→eligible 全 None,港股通成份常年 500+);归一化失败比例过高→整表作废 | 截断/漂移不抛异常→小而错的 set→真标的被误判 False(比 None 更糟) |
| G10 | 配置 | 仅 `GGT_LIST_CACHE_TTL_SECONDS`(config 字段+env parse_env_int minimum=60+.env.example 注释行+docs);**不进 registry/locale**;timeout 复用 `fundamental_fetch_timeout_seconds`;无 enable 开关 | fundamental_* 先例;最小配置面免 web-gate |
| G11 | 报告消费面 | 镜像 margin 五层:report_schema `GgtContext`+analyzer `_build_ggt_from_context` presence-only+`fill_ggt_if_needed`+report_language 双语+notification;**无 prompt 注入**(CONTRACT-3/F3) | margin 蓝图逐层可对照 |
| G12 | 拒绝的替代 | (a)独立 GgtAdapter 文件——拒,方法入既有 `AkshareFundamentalAdapter`;(b)看板放整 dict——moot(看板 defer);(c)prompt 注入——拒(破坏"纯展示",CONTRACT-3) | 禁平行实现;载荷最小 |

## 2. 数据流

```
AkshareFundamentalAdapter(fundamental_adapter.py)   [三 leg 共享 deadline,锁外抓取,single-flight]
  +get_ggt_eligibility_set(deadline)  ──模块缓存 TTL 12h/负缓存 300s──▶ set[_ggt_key 码] | None
      │ (行数<50→None:R4 守卫)
  +get_ggt_holding(code, deadline)    ──南向持股日表缓存,按 _ggt_key 查最新行(持股日期 max)──▶ {...} | None
  +get_southbound_flow(deadline)      ──模块缓存──▶ {net_flow,flow_date} | None (NaN-first,禁假零)
        │
DataFetcherManager.get_ggt_context(code, budget)   [门控 _market_tag(code)!="hk"→not_supported 显式块]
        │  三 leg 各 _run_with_retry 共享 deadline → _build_fundamental_block(status,{eligible,holding,southbound_flow},chain,errs)
        ▼
_build_offshore_fundamental_context 增 "ggt" 键(hk 才算;us=显式 not_supported 块)
   + coverage['ggt'] 记录;ggt **不入** active_statuses(总 status 不受 ggt 拖累:F4-scope)
   + build_failed / _build_market_not_supported 两枚举工厂增 ggt 键(CONTRACT-5/6)
   + _should_cache_fundamental_context 块元组加 'ggt'(CONTRACT-6)
        ▼
pipeline.py: fill_ggt_if_needed(result, fundamental_context) [LLM 后,两处调用点]
        ▼
analyzer._build_ggt_from_context(presence-only:status 非 ok/partial→None;键名 D8 冻结)
  → dashboard.data_perspective["ggt_context"] → report_schema.GgtContext
        ▼
report_language 双语 label + notification 渲染(eligible 三态文案:None→ggt_unknown_label 非跳过)
```

## 3. 数据面语义(精确定义)

0. **status 分界(R6)**:`ok` = eligible 与两子块**三者全有值**;有值但不全→`partial`;
   全 None 且非门控→`failed`;非 hk/etf→`not_supported`。
1. **eligibility(G4/G5/G9)**:成份表 `代码` 列过 `_ggt_key` 建 set;`eligible = _ggt_key(code) in set`;
   **表不可得→None**(unknown);**表行数<50→None**(R4 截断守卫);表可得且不在内→False。
   名单两腿单请求合并,**eligibility 无 partial 态**(可得/不可得二分,叠 in/not-in 得三态,F5-domain)。
2. **个股南向持股(G3/F3/F8)**:南向持股日表按 `_ggt_key(股票代码)` 匹配本股,取 `持股日期` **最大**
   行(端点降序故 iloc[0],但按日期 max 取更稳);字段 `holding_shares`(持股数量)/
   `holding_value`(持股市值)/`holding_ratio_pct`(**持股数量占发行股百分比**,真实列名)/
   `trade_date`(持股日期);**当日表空→逐日回退最多 5 日历日**;表可得但本股无行→字段 None
   (南向未持仓,不推断);解析/端点异常→holding 子块 None+errors。**不取市值变化列**(含价格效应)。
3. **市场级南向净流(G3/F4/F7)**:南向两行(`类型=="港股通(沪)"`+`"港股通(深)"`);
   取 **`成交净买额`** 列(与 `资金净流入` 口径差:净买额=买卖成交差,净流入含额度口径——docs 注明);
   **NaN 判定先行**:腿值 `isna`→"该腿缺";**两腿皆 NaN→None(禁 `sum` skipna 得假零 0.0)**;
   单腿 NaN→partial 且只报可得腿;`southbound_net_flow`(亿,**币种以数据源口径为准通常港元,真网核验前不标"人民币/元"**,F6)+`flow_date`。
4. **缓存(G7/G8)**:三份模块缓存带 ts;成功 TTL 12h,**失败负缓存 TTL 300s**(死端点不每报告重打);
   single-flight:in-flight 时并发写路径用旧值/None 不等待;锁只护 dict,网络锁外。
5. **trade_date 来源(F5-domain)**:成份榜单无日期→eligible 无 trade_date;GgtContext 分设
   `holding_trade_date`(持股)/`flow_date`(净流)各自来源。

## 4. 各层改动

### 4.1 `data_provider/fundamental_adapter.py`
- 模块级 `_ggt_key(code)`(G5 共享键);`_GGT_LIST_CACHE`/`_SB_HOLDING_CACHE`/`_SB_FLOW_CACHE`
  (dict+lock+ts,成功/失败双 TTL);三方法 `get_ggt_eligibility_set(deadline)`/
  `get_ggt_holding(code, deadline)`/`get_southbound_flow(deadline)`(仿 `get_margin_detail` :569
  形态:deadline 预算+source_chain+errors;akshare 惰性 import;窄 try/except→errors);
  抓取锁外+single-flight(G8);eligibility 行数<50 守卫(G9);持股末端日回退(§3.2)。

### 4.2 `data_provider/base.py`
- `get_ggt_context(code, budget_seconds)`(仿 get_margin_context :3494:门控/timeout≤0 failed/
  三 leg 各 _run_with_retry 共享 deadline/_build_fundamental_block;ok/partial 按 §3.0)。
- `_build_offshore_fundamental_context`:result_ctx 增 `"ggt": {}`;hk 调 get_ggt_context 填充,
  us 保持显式 not_supported 块;**coverage['ggt'] 记录**;**ggt 不入 active_statuses**
  (base.py:3010 总 status 聚合不纳入 ggt,F4-scope);**errors/source_chain 聚合元组(base.py:3006-3008)
  加 'ggt'**(CONTRACT-5)。
- `build_failed_fundamental_context`(:3029)与 `_build_market_not_supported`(:2790)两枚举工厂
  **增 ggt 键**(CONTRACT-5,shape 契约);`_should_cache_fundamental_context` 块元组(:2766)
  **加 'ggt'**(CONTRACT-6)。

### 4.3 报告五层(镜像 margin,**无 prompt 注入**)
- `src/schemas/report_schema.py`:`class GgtContext(BaseModel)`(eligible: Optional[bool] 三态描述/
  holding_shares|holding_value|holding_ratio_pct|holding_trade_date/southbound_net_flow|flow_date 各
  Optional)+ dashboard 段 `ggt_context: Optional[GgtContext] = None`(:102 margin_trading 旁)。
- `src/analyzer.py`:`_build_ggt_from_context(fundamental_context, language)`(presence-only,仿 :918;
  status 非 ok/partial→None;**键名 D8 式冻结映射注释**防 builder↔schema 漂移,F10)+
  `fill_ggt_if_needed(result, fundamental_context)`(仿 :944,LLM 后、只读、**不喂 prompt、不碰
  decision_stability**)。**无 _ggt_prompt_line**(CONTRACT-3/F3:纯展示)。
- `src/core/pipeline.py`:import fill_ggt_if_needed;**两处调用点**接线(镜像 margin :631/:1154)。
- `src/report_language.py`:zh/en label(`ggt_label` 港股通/HKSC、`ggt_eligible_label`/
  `ggt_not_eligible_label` 非港股通标的(内地账户不可买)/`ggt_unknown_label` 成份状态未知(数据不可达)/
  `southbound_label` 南向资金)。**eligible=None→ggt_unknown_label**(非跳过,R6)。
- `src/notification.py`:margin 渲染(:1277)旁并列;eligible 三态文案(True/False/None 三互异串,
  **None 输出禁含 False 文案**,F10/§6);持股/净流**数值字段** None 跳过;整段 None 不渲染。

### 4.4 配置
- `src/config.py`:`ggt_list_cache_ttl_seconds: int = 43200` + env parse_env_int(minimum=60);
  `.env.example` 注释行;**不进 registry/locale**。

### 4.5 docs
- 新 `docs/ggt-southbound.md`(仿 margin-trading.md):三端点清单+**活性未核验声明**(签名/列名已本地
  inspect,活性待真网)、eligible 三态语义、EOD 口径、持股占比"占发行股"口径、净流列选择与**币种未决**、
  缓存 12h/负缓存 300s、min-row 守卫、fail-closed 边界、真网 deferred 清单、**看板注解 defer 到 2b 说明**。
- 顺带按 AGENTS.md 文档漂移规则订正 `docs/strategy-actionable-signal-system.md:119` 验收行
  (南向无第二源故"facade 兜底"不适用、"1m fail-closed"系 HK MVP 措辞残留,F2-scope)。
- `docs/CHANGELOG.md` [Unreleased] 一条 [新功能](扁平)。

## 5. 兼容性

- 非 HK 标的:get_ggt_context 门控 not_supported;offshore us 键显式 not_supported;报告 section None
  不渲染;两枚举工厂含 ggt 键→shape 契约不破;总 status 不含 ggt→**非 HK 与 HK-东财宕机均不拖垮**
  (F4-scope)——**字节级零行为变化**。
- HK 标的东财不可达(当前沙箱常态):ggt failed/None→报告无 ggt 段+coverage 记录+负缓存 300s
  防重打——不出错、不编造、不拖垮(deadline 内)。
- report_schema additive Optional;旧报告 JSON 无 ggt 键→Pydantic 默认 None,历史加载零破坏。

## 6. 诚实边界(文档必写)

- eligible 三态:False 必来自"表可得(≥50 行)且不在内";表不可得/截断→None——**禁把 None/False 混渲染成"不可买"**(三态文案三互异串)。
- 南向数据 **EOD**(2024 起无实时),报告标数据日期;端点活性未经本仓真网核验,首次跑通前可能整段缺席(设计行为)。
- 持股占比口径="占发行股百分比"(端点真实列);净流取"成交净买额"(与"资金净流入"口径差 docs 注明);
  **净流币种未决**(通常港元,真网核验前不标人民币/元)。
- 港股通"仅可卖出"证券该榜单不披露→`eligible=True` 弱化为"港股通成份(不区分仅可卖,以券商名单为准)"。
- 本增量不改任何信号/回测/decision_stability——ggt 纯展示、不喂 prompt。
- **看板可买性注解本版不含**(defer 2b);"可买性"经报告段 eligible 达成。

## 7. 测试计划(RED→GREEN,全离线 fixtures;列名以 akshare 1.18.64 源码契约为准)

1. **adapter 三方法**:DataFrame fixtures(**列名逐字=§0.2 真实列**)→字段解析 pin;端点异常/None/
   空表→各 fail 语义(set None/holding None/flow None+errors);**负缓存**:失败后 300s 内不重打(mock 计数)。
2. **_ggt_key 双向(Blocker F1)**:成份表 fixture 用**裸 "00700"/"01810"** 建 set,查询侧
   hk00700/0700.HK/00700 三写法均命中同键;不在表→False;表 None→None(三态);**禁用已归一 fixture**。
3. **eligibility 守卫(R4)**:截断表(<50 行)→eligible=None 而非 False(截断 fixture)。
4. **holding 最新行(F3)**:多日期时序 fixture(持股日期降序)→取 max 日期行;当日空→回退 5 日;
   本股无行→字段 None;**占比字段=占发行股百分比列**;不取市值变化列。
5. **净流 NaN 假零(F4)**:两腿正常→和;**两腿 NaN fixture→None 非 0.0**;单腿 NaN→partial+errors。
6. **manager 门控/status 分界(R6)**:cn/us/etf→not_supported;hk 三子块组合→ok(全有)/partial(部分)/
   failed(全 None);**三 leg 某 leg 超时不丢已完成 leg**(mock 一 leg 慢)。
7. **offshore 邻域(F4-scope,判别锚点)**:hk yfinance ok + ggt failed → `ctx['status']=='ok'` 且
   `coverage['ggt']=='failed'`(证 ggt 不拖垮总 status,现成锚 test_fundamental_context.py:143);
   us 的 ggt 块 `status=='not_supported'`(非空 dict,CONTRACT-5)。
8. **两枚举工厂(CONTRACT-5)**:build_failed / _build_market_not_supported 产物含 ggt 键+coverage
   记录(镜像 test_margin_context.py:45/:53)。
9. **fill 断链防护(Blocker CONTRACT-2)**:mock fundamental_context 含 ok 的 ggt 块→调
   `fill_ggt_if_needed`→`result.dashboard.data_perspective['ggt_context']` 出现;failed→不出现。
10. **presence-only + 键名冻结(F10)**:status ok→dict 全字段;failed/not_supported→None;
    **用 `_build_ggt_from_context` 真实产物直接 `GgtContext(**built)`** 钉键名奇偶(非手写 dict)。
11. **双语渲染(R6/F10)**:eligible True/False/None **三互异文案**,None 输出**禁含**"非港股通标的";
    整段 None 不渲染;数值 None 字段跳过;zh/en 各断言。
12. **配置**:TTL 解析默认/下限钳制。
13. **schema roundtrip**:含 ggt 段报告 JSON dump/load;旧 JSON 无键加载零破坏。
14. **纯展示不变式**:ggt 不进 prompt(断言 prompt 文本不含 ggt 段)、不改 decision_stability
    (镜像 test_margin_surface.py:97,F3-scope)。
15. **network 观测(-m network 非阻断)**:三端点各一条真网 probe——**至少一条走 adapter 公开方法
    +canonical 入参端到端**(不许直调 akshare,F2),可达断言 schema 形态+量级 sanity band
    (|南向净流|<5000 亿),不可达 SKIP(沿 test_*_network.py caplog 判类别)。

## 8. 验证矩阵

- 后端:`PATH=.venv/bin:$PATH ./scripts/ci_gate.sh` 全量(预计 +30~40)。
- **免 web-gate**(无 registry/locale/前端文件改动)。
- 真网:deferred(东财本沙箱不可达;有网环境跑 -m network 三 probe + 一次真实 HK 报告端到端 +
  **净流币种/量级核验**,列入 deferred 清单)。

## 9. 回滚

- 未合并删分支;合并后 revert merge commit——additive Optional 字段+独立新方法,ggt 不落库
  (报告 JSON 自带),无 schema 迁移,回滚零残留。
- 运行时止血:东财不可达=无 ggt 段(设计内),无需开关。

## 附:审查裁定台账(28 findings)

- **已折进(Blocker×2)**:F1/CONTRACT-1(→G5 _ggt_key)、CONTRACT-2(→G6 pipeline fill)。
- **已折进(Important)**:CONTRACT-3/F3-scope(删 prompt 注入)、F4-scope(status 不含 ggt+枚举工厂)、
  R2(三 leg 共享 deadline 独立装配)、R4(min-row 守卫)、R1(负缓存)、R5→并入 G8(锁纪律,看板部分随
  看板 defer 消解)、F2(symbol 剥前缀+probe 走 adapter)、F3/F8(真实列名+砍 change+iloc 订正)、
  F4(NaN 假零)、F2-scope(验收映射+订正策略文档)、F5-scope(真缓存测试→看板 defer 后 seam 测试
  改为 fill 断链测试#9)、F6-scope(→看板 defer 消解)。
- **已折进(Minor)**:F5/F6-domain(无 partial 态/trade_date 来源/币种)、CONTRACT-5/6(枚举工厂+
  should_cache+us 显式块)、R6(None→unknown 文案+ok/partial 分界)、F7(单腿 fixture+量级 band)、
  F10(键名冻结+真产物构造 schema)。
- **随"看板 defer"整体消解(用户反转则复活)**:CONTRACT-4、R3、F9、R5 看板部分、F6-scope、F5-scope。
