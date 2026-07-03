# GGT spec 对抗审查 findings(28 项,verify 阶段因额度耗尽未跑=未经反驳复核,均属**原始待裁定**)
# 来源 workflow wf_0aa0923f-774;完整 JSON 在 tool-results/bq71sgmeh.txt 及 workflow journal.jsonl

## [Blocker] F1 (domain)
**claim**: G9 的归一化前提是事实错误:成份表返回裸 5 位码("00700"),而 normalize_stock_code 对裸 5 位数字码原样返回不加 HK 前缀,与查询侧 canonical "HK00700" 永不同键——按 spec 字面实现,set 成员判定恒 miss,所有 HK 标的 eligible=False(假阴性),报告会对腾讯等真港股通标的渲染「非港股通标的(内地账户不可买)」,是主动编造的错误结论,直接违反 G4 fail-closed 语义(False 只能来自"表可得且不在表内")
**evidence**: spec §1 G9/§3.1/§7.2 声称"成份表代码同样归一后建 set"且"hk00700/0700.HK/00700 三写法均命中同键";但 data_provider/base.py:141-188 现场核验:"00700" 不命中任何分支,:188 `return code` 原样返回 "00700"≠"HK00700"(HK 前缀化仅在 :148-151 输入带 HK 前缀、:181-182 输入带 .HK 后缀时发生);akshare stock_hk_ggt_components_em 源码确认 `代码` 列为东财 f12 裸 5 位形态
**fix**: spec §3.1/G9 改为:成份表侧建 set 前显式做 `f"HK{code.zfill(5)}"` 前缀化(或双方统一剥前缀取 zfill(5) 裸码作键),不能直接把 normalize_stock_code 当"三写法同键"真源;§7.2 测试用例保留三写法断言但明确表侧 fixture 用裸 "00700"/"01810" 形态以钉住该陷阱

## [Blocker] GGT-CONTRACT-1 (contract)
**claim**: G9/§3.1/§7.2 的匹配机制建立在对 normalize_stock_code 的误读上:该函数只对 'HK' 前缀或 '.HK' 后缀输入产出 HK 补零形态,裸 5 位数字('00700')原样返回不变——而东财成份表恰恰输出裸 5 位代码。按 spec 写法,成份 set 键为 '00700' 裸形态,查询侧 'hk00700'/'0700.HK' 归一为 'HK00700' 永不命中 → 真港股通标的被注解 eligible=False 并渲染成『非港股通标的(内地账户不可买)』,直接违反 spec §6 自己的诚实边界(False 必须来自'名单可得且不在内')。
**evidence**: spec G9(『成份表代码同样归一后建 set』『hk00700/0700.HK/00700 三写法同键』)+§7 测试2;data_provider/base.py:148-151 与 :181-182(仅 HK 前缀/.HK 后缀分支补零),base.py:188(裸数字落到 `return code` 原样返回:normalize_stock_code('00700')=='00700'≠'HK00700');base.py:217-218(_is_hk_market 接受裸 5 位为 hk,故该形态是合法输入);同型前科:yfinance _convert_stock_code 裸 5 位误映射曾被终审逮出(c356d6b0)。
**fix**: spec 明确定义共享键函数(如 fundamental_adapter 内 _ggt_key(code):先 normalize_stock_code,若结果仍为纯 1-5 位数字则补 'HK'+zfill(5)),成份表 ingest 侧与查询侧(含 get_ggt_eligible_cached)都过同一函数;§7 测试2 增加裸 '00700' 出现在 set 侧与查询侧的双向用例。禁止两侧各写归一逻辑(否则违反 §0.4 只复用 canonical 的自我约束)。

## [Blocker] GGT-CONTRACT-2 (contract)
**claim**: spec 改动清单(§4)与数据流图(§2)完全遗漏 margin 蓝图的真实接线层:src/core/pipeline.py。margin 的 section 之所以进报告,是 pipeline 在 LLM 后调 fill_margin_if_needed(result, fundamental_context)(两条路径各一处)。spec 只列 _build_ggt_from_context 构建器(§4.3『仿 :918』),既无 fill_ggt_if_needed 包装器也无 pipeline.py 文件项;§7 测试6 只直测构建器。若照 spec 落任务,四层全部写完、所有 spec 内测试全绿,但 data_perspective 永远没有 ggt_context 键 → 报告/notification/schema 全链 presence-only 静默跳过,本增量的核心交付(§0.3 报告新增港股通段)端到端不出现且无任何测试变红。
**evidence**: spec §2 数据流图与 §4.1-4.6 文件清单(零处提及 src/core/pipeline.py 或 fill 包装器);src/core/pipeline.py:33-34(import fill_capital_flow_if_needed/fill_margin_if_needed)、:627-631 与 :1150-1154(两条路径的 fill 调用点);src/analyzer.py:944-967(fill_margin_if_needed 包装器,是 :918 构建器与 pipeline 之间的必经层)。
**fix**: §4.3 补 fill_ggt_if_needed(仿 analyzer.py:944)+ §4 新增 src/core/pipeline.py 条目(两处调用点都要接,镜像 :631/:1154);§7 补一条 fill 级测试(mock fundamental_context 含 ok 的 ggt 块 → result.dashboard.data_perspective['ggt_context'] 出现;failed → 不出现),防构建器单测掩盖断链。

## [Blocker] F1 (scope)
**claim**: G9 的设计断言事实错误:normalize_stock_code 对裸 5 位 HK 码不产出「HK 补零形态」,按 G9 双侧归一建 set 会导致所有 HK 前缀/后缀写法的查询恒 False(编造『非港股通标的』,直接违反 §6 诚实边界);且 §7 item 2 的 fixture 表形态无外部真源锚定(『等形态』留了后门),实现者测试转红后最顺手的『修法』是把 fixture 改成 HK00700 形态让测试变绿——fixture 自说自话,离线全绿、生产系统性错标。
**evidence**: spec G9(:66)称匹配键=「normalize_stock_code 的 HK 5 位补零形态(HK01810)」;但 data_provider/base.py:147-151 HK 补零分支仅在输入带 HK 前缀时触发,运行时实测 normalize_stock_code('00700')=='00700'(非 'HK00700'),而 'hk00700'/'0700.HK' → 'HK00700';真端点成份表代码列为裸 5 位字符串(.venv/lib/python3.10/site-packages/akshare/stock_feature/stock_hsgt_em.py stock_hk_ggt_components_em,eastmoney f12 代码列如 '00700')。§7 item 2(spec:183-184)写「成份表含 '00700'/'01810' 等形态」——『等形态』允许 fixture 写成已归一形态,此时按 G9 实现三写法全命中、测试通过,而真端点数据下 'HK00700' ∉ {'00700',…} 恒 False。
**fix**: ①G9 改为显式定义匹配键 helper:normalize 后若 _market_tag==hk 且无 HK 前缀则补 'HK'+zfill(5)(双侧同用);②§7 item 2 钉死 fixture 表形态=裸 5 位(并注明该形态派生自已安装 akshare 1.18.64 源码的列契约,禁用已归一形态造 fixture),显式断言裸 '00700' 成份行能被 'hk00700'/'0700.HK' 查询命中;③docs 记录 akshare 版本假设。

## [Important] F2 (domain)
**claim**: spec 未指定 get_ggt_holding 传给 stock_hsgt_individual_em 的 symbol 形态,而该端点对 HK 码要求裸 5 位补零(内部拼 filter `SECUCODE="{symbol}.HK"`);若实现者传 canonical "HK00700"(len 7 恰好也进 HK 分支),filter 变成 "HK00700.HK" → 空结果/异常,被 adapter 的宽 try/except 吞掉 → 个股持股子块在生产环境永久 None,且与"端点不可达"不可区分(spec §6 已把整段缺席声明为设计行为),离线测试全绿、可能永远无人诊断
**evidence**: akshare/stock_feature/stock_hsgt_em.py 现场源码:stock_hsgt_individual_em `if len(symbol)==6` 派 A股北向分支(已停更),否则派 __stock_hsgt_individual_zh_hk_em,其 filter 为 f'(SECUCODE="{symbol}.HK")(MUTUAL_TYPE="002")';spec §4.1 只写"get_ggt_holding(stock_code, deadline)"未写码形态;§7.11 真网 probe 若直调端点(用默认/裸码)而非经 adapter 走 canonical 码,也逮不到此错
**fix**: spec §4.1 明写:adapter 内从 canonical "HKxxxxx" 剥前缀得裸 5 位补零码再传端点(与 F1 的键归一 helper 共用);§7.11 的 network probe 至少一条必须走 adapter 公开方法+canonical 入参端到端,不许直调 akshare 函数

## [Important] F3 (domain)
**claim**: spec §3.2 的持股字段与真实返回列存在三处偏差,若 fixtures 按 spec 臆造列名而非真实列名,测试通过但真网解析零命中(永久 fail-closed)或取错列:①占比列真名为「持股数量占A股百分比」(HOLD_SHARES_RATIO 重命名,HK 语境下列名误导),FREE_SHARES_RATIO/TOTAL_SHARES_RATIO 被 akshare 丢弃——spec 所述"占流通/总股本比"两口径在返回表里根本拿不到;②change_5d 真实对应列是「持股市值变化-5日」,是市值变化(元)不是持股量/占比变动,语义须钉死否则报告文案"近5日变动"误导;③返回按持股日期升序排序且全历史多页拉取(pageSize=500,数年日频≈4-6 个 HTTP 请求),最新行是 iloc[-1] 不是 iloc[0],且单次调用的多请求开销须计入 deadline 预算
**evidence**: akshare/stock_feature/stock_hsgt_em.py __stock_hsgt_individual_zh_hk_em 现场源码:rename 字典中 FREE_SHARES_RATIO/TOTAL_SHARES_RATIO→"-"后被列筛选丢弃、HOLD_MARKETCAP_CHG5→「持股市值变化-5日」、末尾 sort_values("持股日期") 升序、分页循环 range(1,total_page+1);对照 spec §3.2 "holding_ratio_pct(占流通/总股本比,端点口径披露)/change_5d(近5日变动,端点有则填)" 与 §7.1 "DataFrame fixtures(仿真端点返回形态)"
**fix**: spec §3.2 按真实列名钉字段映射表(持股数量/持股市值/持股数量占A股百分比/持股市值变化-5日/持股日期),change_5d 改名 value_change_5d 或描述改"近5日持股市值变化";§7.1 要求 fixture 列名与 akshare 1.18.64 源码 rename 后列名逐字一致;§4.1 注明取最新行=按持股日期取 max 而非首行

## [Important] F4 (domain)
**claim**: 南向净流的行列形态与 NaN 语义未钉死,存在"假零"陷阱:fund_flow_summary 真实返回每(类型×板块)一行(南向=「港股通(沪)」「港股通(深)」两行),且并存两个候选数值列(成交净买额 vs 资金净流入,语义不同:净买额=买卖成交差,净流入=含额度使用口径,北向侧 2024 后部分字段已停更为空);所有数值列经 to_numeric(errors="coerce") 产 NaN——若实现者按 §3.3 "两腿求和"直接 Series.sum()(pandas 默认 skipna=True),休市日/字段停更时两腿全 NaN 求和得 0.0,报告呈现"南向净流 0 亿"是编造数据,直接违反 G4 "全缺→None"
**evidence**: akshare/stock_feature/stock_hsgt_em.py stock_hsgt_fund_flow_summary_em 现场源码:列含「类型/板块/资金方向/交易状态/成交净买额/资金净流入」,三个金额列均 pd.to_numeric(errors="coerce") 后 /10000;spec §3.3 只写"southbound_net_flow(亿元,EOD)+沪深两条腿求和,单腿缺→partial"未指定选哪列、未定义 NaN 处理;§7.1 fixture 若不含 NaN 腿用例则测试逮不到
**fix**: spec §3.3 钉死:①行过滤条件=类型=="南向"(两行);②选用列(建议成交净买额,并在 docs 注明与资金净流入的口径差);③NaN 判定先行——腿值 isna 视为"该腿缺",两腿皆 NaN→None 禁 sum,单腿 NaN→partial 且只报可得腿(或也 None,二选一写死);§7.1 增加"两腿 NaN fixture→None 非 0.0"的 RED 用例

## [Important] GGT-CONTRACT-3 (contract)
**claim**: §4.3『prompt 注入段落仅 market=="hk" 且 section 非 None』与 G8『完整镜像 margin 四层』自相矛盾且时序不可行:margin 蓝图是 LLM 后填充、明文『不喂 prompt、不改决策』(analyzer.py:949),pipeline 的 fill 调用发生在 LLM 之后——『section 非 None 才注入 prompt』在镜像架构里根本没有执行点。仓库内真实的 prompt 注入前例是 _dragon_tiger_prompt_line:LLM 前直接读 fundamental_context 块状态、不经 section。若不修,实现者要么照 margin 镜像导致 prompt 注入静默落空(需求未满足无人察觉),要么自行发明无锚点的注入位置(可能把注入错挂到 LLM 后路径)。
**evidence**: spec §4.3 analyzer 条目 vs G8;src/analyzer.py:944-951(fill_margin_if_needed docstring『LLM 后、对决策只读(不喂 prompt…)』);src/core/pipeline.py:627-631(fill 在 LLM 产出 result 之后);src/analyzer.py:970(_dragon_tiger_prompt_line,LLM 前直读 fundamental_context 门控 status∈{ok,partial})与 :3313(prompt += 消费点)。
**fix**: 二选一并写死进 spec:(a) 严格镜像 margin=纯展示不喂 prompt,删掉 prompt 注入句;(b) 要 prompt 注入则改锚 dragon_tiger 机制——新增 _ggt_prompt_line(fundamental_context) 在 analyzer:3313 旁并列注入,门控改为『ggt 块 status∈{ok,partial}』而非『section 非 None』,并在 §7 补 zh/en prompt 行测试。

## [Important] GGT-CONTRACT-4 (contract)
**claim**: G6『跑过一次 HK 报告后看板有值』是进程本地事实,spec 未披露跨进程不可见性:_GGT_LIST_CACHE 是模块级进程内缓存,而本部署形态下日常分析(systemd dsa-daily 跑 main.py)与 API 服务(uvicorn server:app,/signals/board 所在进程)可以是两个进程——报告链在 daemon 进程里填的缓存,看板进程永远读不到,ggt_eligible 在该部署下恒 None(不是冷启动暂时 None,是结构性永远 None)。§6 诚实边界只写了 TTL 滞后+冷启动,漏了这条最可能命中生产的边界;docs/ggt-southbound.md 若照 §6 写会给出错误的运维预期。
**evidence**: spec G6/§4.1(『进程内模块级缓存』『看板只读缓存不发网』)+§6(仅列 TTL 滞后与冷启动);src/services/signal_board_service.py(看板编排在 API 服务进程,经 /signals/board 端点消费);CLAUDE.md 常用命令段列 `python main.py` 与 `uvicorn server:app` 为两个独立入口;记忆档案:systemd dsa-daily 独立于 Web 服务全自主日运行。
**fix**: 最小修:§6 与 docs 明写『看板注解仅当 HK 分析在 API 服务同进程内跑过(如 Web 触发分析或 --serve 合体模式)才会出现;独立 daemon 部署下恒 None』;或升级方案(需用户拍板):get_ggt_eligible_cached 冷缓存时允许一次带短超时的惰性抓取(有界、失败不重试),或将 eligible 快照随报告落库供看板读。

## [Important] R1 (robust)
**claim**: 「失败不缓存」(§3.5/G5)+东财长期不可达+每 HK 股 3 个串行子抓取,会在多线程 pipeline 下耗尽全进程共享的 fundamental 超时 worker 槽位池(硬编码 8),连带击穿非 HK 标的的 fundamental 抓取——违反 spec §5『非 HK 标的全链零行为变化』与『不拖垮』承诺;且无负缓存时每次报告(每 120s context 缓存窗)都对死端点重打满 timeout
**evidence**: spec §3.5(:102-103 '抓取失败不缓存失败')+G5(:62)+§5(:162-164 '零行为变化/不拖垮')。代码:data_provider/base.py:753-754 `_fundamental_timeout_worker_limit = 8`(硬编码、全 manager 共享);base.py:2597-2598 槽位非阻塞 acquire 失败→其他任务立即返回 'timeout worker pool exhausted';base.py:2611-2622 超时后 daemon worker 线程泄漏继续挂在网络调用上、finally 才释放槽位——akshare 不暴露 timeout 参数,东财黑洞路由(TCP SYN 挂起)下单个泄漏线程可占槽 ~2 分钟;src/core/pipeline.py:2392 默认 3 并发股票 × 每股 3 个 ggt 子抓取 = 峰值 9 个挂起 worker > 8 槽;FUNDAMENTAL_RETRY_MAX(src/config.py:1866, env 可调)>1 时每 leg 泄漏线程数翻倍。若不修:混合 watchlist 日批中,几只 HK 股冷缓存撞死端点即让同批 A股的 valuation/margin/capital_flow 全部瞬时失败,报告质量跨市场劣化,且现象随缓存窗口周期性复发,极难归因
**fix**: spec §4.1/G5 增补:①成份名单+南向净流两个共享缓存增加失败负缓存(短 TTL,如 300s,可复用字面量不必新配置项),个股 holding 保持不缓存失败;②两个共享抓取加 single-flight(in-flight 去重),并发 pipeline 线程共享同一次抓取结果而非各自开 worker;③文档写明与 `_fundamental_timeout_slots` 槽位池的交互及东财挂起模式下的退化行为

## [Important] R2 (robust)
**claim**: get_ggt_context『仿 get_margin_context 全形态』(§4.2)蓝本失配:margin 是单端点一次 _run_with_retry,ggt 是三个串行端点。spec 未定义预算切分与部分结果保留——若三 leg 包成一个 task:超时 kill 丢弃已完成 leg 的结果(eligibility 已抓到也作废),且 FUNDAMENTAL_RETRY_MAX≥2 时整包重跑放大未失败 leg 的抓取;若单预算 min(fetch=3s, remaining):三个串行东财调用(含冷缓存全量成份表)挤 3 秒,健康慢网下 ggt 块慢性 partial/failed,12h 名单缓存永远暖不起来
**evidence**: spec §4.2(:117-118 '仿 get_margin_context :3494 全形态')+§4.1(:108-112 三方法)。代码:base.py:3517-3521 margin 单 task 单 _run_with_retry;base.py:3305 `margin_budget = min(fetch_timeout, remaining_seconds)` 蓝本预算=3s(src/config.py:989 fetch=3.0);base.py:2645-2655 _run_with_retry 整 task 重跑无部分结果通道;stage 总预算 8s(src/config.py:101)中 valuation+bundle 先吃掉最多 6s(base.py:2907, 2941),留给 ggt 常态只剩 ~2s。若不修:实现者按蓝本二选一都踩坑,产出要么丢部分数据(fail 语义比必要更差,§3.4 partial 判定失真),要么慢性饥饿(健康网络下报告长期无 ggt 段、看板长期 None),且两种坑离线 mock 测试都测不出来
**fix**: spec §4.2 明确:三 leg 各自独立 _run_with_retry、共享一个 monotonic deadline(margin 式 deadline 参数下传 adapter),结果按 leg 独立装配(某 leg 超时不丢已完成 leg);同时明确 ok/partial 分界(三 leg 全有值→ok,部分→partial);注明 retry 重入安全性依赖名单/净流缓存,holding leg 重抓有界可接受

## [Important] R3 (robust)
**claim**: 看板冷缓存承诺与文档部署拓扑矛盾:G6『跑过一次 HK 报告后看板有值』只在报告与 API server 同进程时成立。文档真源部署是 systemd `main.py --schedule` 独立进程跑日报,模块级进程内缓存永远暖不到 server 进程——该部署下 BoardEntry.ggt_eligible 恒 None,看板半边功能静默失效,与 §6『最长滞后 TTL(12h)+冷启动 None』的表述(暗示最终会有值)不符
**evidence**: spec G6(:63 '跑过一次 HK 报告后看板有值')+§6(:176)+§4.1(:113-114 模块级缓存纯内存读)。代码:docs/DEPLOY.md:170 `ExecStart=... main.py --schedule`(调度进程≠server 进程);缓存是 fundamental_adapter 模块级 dict,无跨进程共享;仅 web 触发的 task_service 进程内分析(src/services/task_service.py:59-62)才能暖 server 缓存。另外 signal_board_service 每 entry 还有 SIGNALS_BOARD_CACHE_TTL_S=300s 缓存(signal_board_service.py:275-277),暖后再叠最多 5 分钟 None 滞后。若不修:生产(文档推荐部署)用户永远看不到 ggt_eligible 有值,按文档排障找不到原因,功能验收即翻车
**fix**: spec G6/§6/docs 把承诺按进程作用域收窄:显式写明『缓存进程内,systemd --schedule 部署下 server 看板恒 None,仅 web 触发分析或 --serve 单进程部署可暖』;或增补看板侧惰性后台暖缓存(非阻塞、每 TTL 至多一次、失败负缓存)并写清预算边界——二选一,但不能维持现在的无条件表述

## [Important] R4 (robust)
**claim**: eligible=False 的强断言(『内地账户不可买』渲染进报告+看板+12h 缓存)缺静默截断防护:成份表端点返回被截断/分页语义漂移/字段错位但不抛异常时,产出小而错的 set,大量真·港股通标的被判 False——恰好违反 G4 fail-closed『不编造』核心承诺,且 12h TTL 让错误持续一整天。spec G3 只给持股端点定义了『字段解析失败整体降级』,成份表无任何合理性校验
**evidence**: spec §3.1(:92-94 'False 只能来自表可得且不在表内',但『表可得』无定义)+G3(:60 解析失败降级仅限 stock_hsgt_individual_em)+G5(:62 12h 缓存)+§6(:169-171 禁把 None 渲染成不可买——但没防『把该 True 的渲染成 False』)。端点活性未核验(§0.2)+东财 API 漂移是本仓已有先例(margin spec、capital-flow 历史),截断不抛异常的失败模式现实存在。若不修:比 None 更糟的是错误的 False——报告明确告诉用户『买不了』一只实际可买的股票,注解性字段变成反向误导,且离线 fixtures 测不出(fixtures 都是完整表)
**fix**: spec §3.1/§7 增补:成份表最小行数合理性门槛(如 <50 行视为『表不可得』→ None,港股通成份常年 500+ 只)+归一化失败比例守卫(normalize 后无效键占比过高→整表作废);§7 加截断表 fixture 测试(截断→eligible=None 而非 False)

## [Important] R5 (robust)
**claim**: 模块级缓存锁纪律未定义,且 G5(防打爆端点=要去重)与 G6(看板读者纯内存零延迟)两个诉求在朴素实现下互相冲突:最自然的防踩踏写法是 fetch 持锁(double-checked locking),而 _run_with_timeout 超时后泄漏的 daemon 线程会带锁挂在网络调用上分钟级——此时 get_ggt_eligible_cached 在板服务 8 线程池里全部阻塞在锁上,『延迟敏感、纯内存查询』的看板请求卡死数分钟
**evidence**: spec §4.1(:110-111 仅写 'dict+lock+ts',无锁作用域约定;:113-114 '纯读…不发网' 未要求非阻塞)+G5(:62 '名单全量表按股查询会打爆端点'——暗示需要去重)。代码:base.py:2611-2622 超时线程泄漏继续执行 task(若 task 持锁则锁被带走);signal_board_service.py:283 板服务 8 并发 worker 全走同一读函数;同进程场景现实存在(web 触发分析 via task_service 与看板轮询并发)。蓝本 _MARGIN_MEMO_LOCK(fundamental_adapter.py:562-566)只锁 dict 写,但 spec 没把这条纪律写成约束。若不修:实现者为满足 G5 的去重诉求持锁 fetch 的概率很高,离线测试全绿,上线后看板偶发分钟级卡死且随东财网络状态漂移,难复现难归因
**fix**: spec §4.1 增补两句硬约束:①锁只保护 dict 读写,网络抓取一律在锁外;②踩踏控制用 single-flight 标志(in-flight 时其他写路径直接用旧值/None,不等待),get_ggt_eligible_cached 永不阻塞等待抓取(有旧值返旧值,无值返 None)

## [Important] F2 (scope)
**claim**: 策略文档 Inc2 验收原文与 §7 存在未响应的映射缺口:『facade 兜底』(HK MVP 语境=akshare→yfinance 门面兜底)对南向数据不可能同形实现(yfinance 无南向数据),『1m fail-closed』对 EOD 数据是措辞残留;spec 只引用了验收原文中『真网 deferred』的半句,对另外两项零响应。且 G3 写「个股持股=stock_hsgt_individual_em(主)」——『(主)』暗示存在备源却从未定义(§0.2 枚举了 stock_hsgt_stock_statistics_em 但 G3 不用),§7 无任何兜底测试项。若不修:验收对照时该行不可满足,要么实现者临场发明一个 spec 外的 fallback(契约漂移),要么 review 期被验收文本卡住。
**evidence**: docs/strategy-actionable-signal-system.md:117(『带 fallback,东财不可达时 fail-closed』)与 :119(『验收:离线单测(端点 schema/成份过滤/1m fail-closed)+ facade 兜底』)vs spec G3(:60)单端点+『(主)』悬空、§0.2(:18-19)枚举 stock_hsgt_stock_statistics_em 未用、§7(:179-199)十一项无 fallback 测试、spec:24 仅引验收原文为真网 deferred 背书。
**fix**: spec 增一节显式映射验收原文:声明南向数据无第二源故『facade 兜底』不适用、fail-closed 即降级语义、『1m fail-closed』系 HK MVP 措辞残留;按 AGENTS.md 文档漂移规则在本增量顺手订正策略文档 :119 验收行。二选一处理『(主)』:要么删掉,要么指定 stock_hsgt_stock_statistics_em 为 holding 备源并在 §7 加兜底切换测试项。

## [Important] F3 (scope)
**claim**: §4.3『prompt 注入段落』与 §6『ggt 纯展示维度、不改任何信号/回测统计』及 G8『完整镜像 margin 四层』三者互相矛盾:margin 蓝本实际是 LLM 后填充、明文『不喂 prompt、不碰 decision_stability』;ggt 信息一旦进 prompt 会改变 LLM 方向判断→position_recommendation→回测样本组成(方向性调用门控),『纯展示』不成立。§7 对此零覆盖:既无 prompt 注入门控测试,也无 margin 蓝本核心属性测试(decision_stability 不动性)的 ggt 对应物。若不修:实现者只能二选一猜,无论怎么选都违反 spec 的另一半,且哪种偏差测试都逮不住。
**evidence**: spec:131(『prompt 注入段落仅 market=="hk" 且 section 非 None』)vs spec:177(『本增量不改任何信号/回测统计——ggt 纯展示维度』)vs G8(:65『完整镜像 margin 四层』);蓝本事实:src/analyzer.py fill_margin_if_needed docstring『LLM 后、对决策只读(不喂 prompt、不碰 decision_stability)』;蓝本测试 tests/test_margin_surface.py:97 test_fill_margin_does_not_touch_decision_stability 在 §7(:179-199)无对应项。
**fix**: 拍板一种并改齐:推荐删除 :131 的 prompt 注入、完整镜像 margin=LLM 后 fill_ggt_if_needed(保住『纯展示』与 G8);若坚持喂 prompt,则改写 §6 承认其影响 LLM 输出与回测样本,并在 §7 新增两项:hk+section 非 None→prompt 含 ggt 段/其余组合不含;镜像 test_fill_margin_does_not_touch_decision_stability 或明示放弃该不变式。

## [Important] F4 (scope)
**claim**: §7 item 5『既有 offshore 字段零变化(邻域回归)』无判别锚点,漏掉两类关键反例:①offshore 总 status 聚合是否纳入 ggt 未定义——最顺手的对称实现会把 ggt 塞进 active_statuses,fixtures 控制下 CI 全绿,生产东财宕机(常态风险)时 HK 基本面整体从 ok 降为 partial,违反 §5『不拖垮』;②margin 蓝本明确覆盖、spec 完全没提的两个枚举工厂(build_failed_fundamental_context/_build_market_not_supported)——不增键则 ggt 键在 failed/pipeline-disabled 路径缺席,破坏『upstream callers see the same shape regardless of market』的既有形状契约,且此漂移 presence-only 消费端静默容忍,永远测不出。
**evidence**: data_provider/base.py:3010-3016 总 status 只由 active_statuses={valuation,growth,earnings} 决定(spec §4.2 :117-123 对是否纳入 ggt 只字未提);base.py:3029-3058 build_failed_fundamental_context 的 block_names 枚举、base.py:2790-2849 _build_market_not_supported 的 blocks 枚举均为 margin 增键时的必改位,蓝本测试 tests/test_margin_context.py:45(test_enumeration_not_supported_factory_includes_margin)/:53(test_enumeration_failed_factory_includes_margin)存在而 §7 无 ggt 对应;既有邻域断言 tests/test_fundamental_context.py:143(offshore status=='ok')是会被①打破的现成锚点。
**fix**: §4.2 明确:ggt 不参与总 status 聚合(与 capital_flow/dragon_tiger 同类排除)+两枚举工厂增 ggt 键;§7 item 5 替换为三条可判别断言:hk 标的 yfinance ok+ggt failed→ctx['status']=='ok' 且 coverage['ggt']=='failed';两工厂产物含 ggt 键且 coverage 记录(镜像 margin 两测)。

## [Important] F5 (scope)
**claim**: §7 item 8 用『mock 缓存』测 G6 是 tautology:mock get_ggt_eligible_cached 只证明看板调用了某个同名函数,证明不了 G6 的两条真契约——(a)看板路径纯内存零网络;(b)报告链写入的缓存与看板读取的是同一个模块级对象(跨模块 seam)。若不修:未来有人把冷缓存 None『修好』成顺手触发抓取(最自然的 bugfix 冲动),mock 测试依然全绿,看板从此被东财 timeout 拖慢——G6 的全部动机(看板延迟敏感)被静默推翻。
**evidence**: spec G6(:63)『只读缓存…纯内存查询不发网』、§4.1(:113-114)reader 与 _GGT_LIST_CACHE 同居 fundamental_adapter;§7 item 8(:194-195)只写『HK 标的经 mock 缓存 True/False/冷缓存 None』,未规定断言机制。可测性成立的前提(缓存为模块级 dict 可直接 seed)spec 自己已给出。
**fix**: item 8 改写为真缓存测试:直接 seed 真 _GGT_LIST_CACHE(新鲜 ts→True/False、过期 ts→None、空→None 四态),同时 monkeypatch akshare 抓取入口为 raise AssertionError,断言看板整条取数路径零抓取;再加一条集成向断言:报告链填充缓存后(调 get_ggt_eligibility_set 一次),看板读到值——钉死同对象 seam。

## [Important] F6 (scope)
**claim**: §7 item 8 缺 BoardEntry 键集断言站点:只点名『_degraded_entry 含键』,漏了两处静默失败面——①ok 路径 _entry_from_board_signals 漏加键时,BoardEntry(**entry) 经 Pydantic 默认值静默补 None(HK 行全部 ggt_eligible=None,功能整体 no-op 而测试全绿);②BoardEntry 未设 extra='forbid',entry dict 多键/schema 漏字段会被静默丢弃,API 载荷根本没有该字段。三态断言若打在 reader 函数层面同样是 tautology。
**evidence**: src/services/signal_board_service.py:214-230(_entry_from_board_signals,ok 路径组装点,spec §4.4 :140-142 未点名其键测试)与 :233-246(_degraded_entry);api/v1/schemas/stocks.py:185-223 BoardEntry 无 model_config extra 约束(Pydantic v2 默认 ignore);现成断言样板:tests/test_signal_board_service.py:295(test_degraded_entry_contains_corrected_keys)与 tests/test_signals_board_endpoint.py:42(BoardEntry(**entry) splat 路径端到端)。
**fix**: item 8 明确三个断言站点:①_entry_from_board_signals 产物含 ggt_eligible 键(镜像 :295 样式);②_degraded_entry 含键(已有);③endpoint 级 BoardEntry(**entry) 后 model_dump 含该字段且值透传(镜像 test_signals_board_endpoint.py:42 样式)——三态值断言必须打在 ①/③ 上,不许打在 reader 上。

## [Minor] F5 (domain)
**claim**: 成份端点事实形态与 spec 三处措辞不吻合(不修会造成实现期错误推断):①它是实时行情榜单,单请求两腿合并(fs 同时带 DLMK0146+DLMK0144),无腿标识、全有或全无——"单腿缺→partial"对成份名单不可能发生,若实现者把 §3.3 的 partial 语义误推广到 eligibility 会造出不存在的状态;②榜单无日期列,§2 数据流塞进 _build_fundamental_block 的 trade_date 对 eligible 无来源(只有 holding/flow 有各自日期);③港股通调整机制存在「仅可卖出」证券(移出买入名单但持有者可卖),该榜单不披露此区分,BoardEntry 描述"true=港股通标的(内地账户可买)"在调整期对 sell-only 股票过度承诺可买性
**evidence**: akshare stock_hk_ggt_components_em 现场源码:params["fs"]="b:DLMK0146,b:DLMK0144" 一次请求、返回列仅行情字段(序号/代码/名称/价量)无日期无腿标识;spec §2 数据流 "{eligible, holding, southbound_flow, trade_date}"、§4.4 BoardEntry description "true=港股通标的(内地账户可买)"、§3.3 partial 措辞
**fix**: §3.1 注明"名单两腿合并单次抓取,eligibility 无 partial 态(可得/不可得二分,叠 in/not-in 得三态)";§2/§4.3 明确 trade_date 仅来自 holding(GgtContext 已分设 trade_date/flow_date,保持);§4.4 与 report_language 三态文案把 "可买" 弱化为"港股通成份(不区分仅可卖状态,以券商名单为准)",docs 诚实边界补一条

## [Minor] F6 (domain)
**claim**: §3.3 把南向净流单位写死为"亿元"但未声明币种口径:港股通(南向)成交净买额按港交所口径以港元计价(而额度余额为人民币),akshare 仅做 /10000 缩放不做币种换算;报告若渲染"X 亿元"会被读作人民币,存在 HKD/CNY 混标,且本沙箱不可发网核验,属于必须写进诚实边界的未决口径
**evidence**: akshare stock_hsgt_fund_flow_summary_em 现场源码:成交净买额/资金净流入/当日资金余额三列同做 /10000,无任何币种字段或换算;spec §3.3 "southbound_net_flow(亿元,EOD)"、§6 诚实边界仅对持股占比写了"口径以数据源披露为准"未覆盖币种
**fix**: §3.3/§6/docs 把单位写成"亿(南向净买额币种以数据源口径为准,通常为港元;真网核验后钉死)",report_language 文案与 notification 渲染避免出现"元/人民币"字样,列入真网 deferred 核验清单

## [Minor] GGT-CONTRACT-5 (contract)
**claim**: us 侧 ggt 块形态三处表述互相冲突:§2 图『us 键存在但 not_supported』、§4.2『us 保持空 not_supported 块』、§7 测试5『us 的 ggt 键存在但空』——空 dict {} 与 margin 前例的显式 not_supported 块(含 status/coverage/source_chain/errors/data 五键)是可观测不同的两种形态(coverage 记录、_has_meaningful_payload、测试断言均有差)。且 §4.2 只说『coverage 记录 ggt 状态』,未提 base.py:3006-3008 的 errors/source_chain 聚合硬编码元组也需同步加 'ggt',漏加会导致 ggt 的 errors 不进 context 顶层 errors(与其余块行为不一致)。
**evidence**: spec §2 vs §4.2 vs §7.5 措辞互斥;data_provider/base.py:2985-2991(margin 前例:offshore 非支持块=显式 not_supported 块非空 dict)、:2995-3005(coverage=block_statuses 硬编码)、:3006-3008(errors/source_chain 聚合硬编码块元组)。
**fix**: 统一为镜像 margin 前例:us 的 ggt=显式 not_supported 块,coverage['ggt']='not_supported',并明示 base.py:3006-3008 聚合元组加 'ggt';§7 测试5 改为断言 us 的 ggt 块 status=='not_supported'(而非『空』)。

## [Minor] GGT-CONTRACT-6 (contract)
**claim**: G5『个股持股走既有 _fundamental_cache(整 context 120s TTL)外层』未提 _should_cache_fundamental_context 的硬编码 8 块元组门:context 非 ok 时逐块查 meaningful data,'ggt' 不在元组内 → 『其余 8 块全空但 ggt 有数据』的 HK context(yfinance 全挂+实时报价挂+东财通)不会被缓存,120s 内每次调用重打 stock_hsgt_individual_em,G5 承诺的外层缓存对该场景静默失效。
**evidence**: spec G5;data_provider/base.py:2766-2787(_should_cache_fundamental_context 块元组 valuation…boards 共 8 项无 ggt)、:3019(offshore 写缓存经此门)。
**fix**: spec §4.2 明示把 'ggt' 加入 _should_cache_fundamental_context 的块元组(与『失败不缓存』语义兼容:ggt 全 None 时该块无 meaningful data 不触发缓存),或显式声明接受该场景不缓存并写进 docs 边界。

## [Minor] R6 (robust)
**claim**: partial 渲染语义内部矛盾:§4.3 notification 同一句里既要求『eligible 三态文案』(None→ggt_unknown_label『成份状态未知』)又要求『None 字段跳过』——按后者读,eligible=None 被静默跳过,ggt_unknown_label 成死代码,与 §7 测试 7『eligible True/False/None 三态文案』直接冲突;另 §3.4 只给出『任一有值→ok/partial』,ok 与 partial 的分界未定义,『持股有值但 eligible=None』这类 partial 组合的呈现规则实现者只能猜
**evidence**: spec §4.3(:136-137 'eligible 三态文案+…None 字段跳过')vs §4.2 report_language(:132-134 定义 ggt_unknown_label)vs §7 测试7(:192 'True/False/None 三态文案');§3.4(:100-101 ok/partial 无分界)。蓝本对照:notification.py:1283-1295 margin 对 None 字段渲染 'N/A' 而非跳过——三种既有先例(跳过/N/A/专属文案)spec 未指定取哪种。若不修:RED 测试按三态文案写、实现按跳过写(或反之),review 期打回返工;ok/partial 判定各写各的,status 语义与 §3.4 漂移
**fix**: spec §4.3 改为无歧义表述:eligible=None→渲染 ggt_unknown_label(与 §7 测试7、§6 诚实边界对齐);『None 跳过』限定为 holding_*/southbound_* 数值字段;§3.4 补一句 ok=eligible 与两子块三者全有值,否则 partial

## [Minor] F7 (scope)
**claim**: §3.3『沪深两条腿求和,单腿缺→partial 并披露』是有语义无测试的死规格:§7 item 1 只覆盖端点抛异常/None/空表,没有单腿缺失 fixture;且净流取哪一列(端点同时有『资金净流入』与『成交净买额』)与『亿元』单位均未锚定——离线 fixture 判别不了单位,错一到四个数量级用户面直接可见,item 11 真网 probe 只断 schema 形态也逮不住。
**evidence**: spec:98-99(§3.3)与 §7 item 1(:181-183)、item 11(:198-199『断言 schema 形态』);.venv/.../akshare/stock_feature/stock_hsgt_em.py stock_hsgt_fund_flow_summary_em 列含『资金方向/板块/资金净流入/成交净买额』,南向为 港股通(沪)/(深) 两行,取列与单位换算 spec 未定。
**fix**: ①item 1 增单腿缺失 fixture→partial+errors 披露断言;②G3 钉死取列(建议『成交净买额』并写选择依据)与单位换算;③item 11 probe 增量级 sanity band(如 |南向净流| < 5000 亿元);或走 YAGNI 路线:把市场级净流整块降为 deferred(见 F8 同理),此条自动消失——策略文档 :117 的『南向资金』由个股持股维度已可满足。

## [Minor] F8 (scope)
**claim**: YAGNI/语义悬空:change_5d『近 5 日变动,端点有则填』——真端点(已安装 akshare 源码可证)只有『持股市值变化-5日』(含价格效应:大涨日会呈现『南向大幅加仓』假象),没有现成的持股数量 5 日变动列;『端点有则填』会诱导实现者把市值变化误映射为持仓增减,报告语义误导且离线测试无从判别。同文件还证实 HK 分支占比列名是『持股数量占A股百分比』(akshare 命名疣),fixture 必须用该真实列名。
**evidence**: spec:96-97(§3.2 change_5d)vs .venv/.../akshare/stock_feature/stock_hsgt_em.py __stock_hsgt_individual_zh_hk_em 输出列:持股日期/当日收盘价/当日涨跌幅/持股数量/持股市值/持股数量占A股百分比/持股市值变化-1日/-5日/-10日——无股数变动列,但有持股数量完整时序(可差分)。
**fix**: 二选一:(a)从 Inc2 砍掉 change_5d(最小面,首个真网核验后再补);(b)钉死计算=用持股数量时序按日期差分得 holding_shares_change_5d,fixture 用两行时序判别,并禁止映射『持股市值变化-5日』。同时 §7 item 1 的 fixture 列名以 akshare 源码列契约为准(含『持股数量占A股百分比』疣),docs 注明口径。

## [Minor] F9 (scope)
**claim**: item 8『非 HK 恒 None』缺关键反例:看板市场判定(_infer_market)与报告链(_market_tag)不同源——裸 5 位码 '00700' 在看板被判成 CN(c[:6].isdigit() 分支),同一标的报告链有港股通段、看板注解却恒 None;§7 若只用 'hk00700' 造测试则全绿,该跨层不一致永远无人发现,用户看到报告/看板自相矛盾。
**evidence**: src/services/signal_board_service.py:59-67(_infer_market:'00700'→'CN',仅 HK 前缀/.HK 后缀→'HK')vs 运行时实测 _market_tag('00700')=='hk';spec §4.4(:140)gate 用 market=='HK'、G9(:66)声称三写法同键(裸 5 位是三写法之一)。
**fix**: item 8 增裸 '00700' 反例并拍板:要么接受 pre-existing 判定并把该形态限制写进 docs/ggt-southbound.md 诚实边界(与看板 market 显示为 CN 的既有疣一并记录),要么看板 gate 改用 _market_tag(bs.code) 与报告链同源(需评估对既有 market 字段零影响)。

## [Minor] F10 (scope)
**claim**: item 10 后半(旧 JSON 无键加载零破坏)近乎 Pydantic Optional 默认值的同义反复,而真正的 roundtrip 风险没盖住:analyzer 构建的 section dict 与 GgtContext 字段名若漂移(如 southbound_net_flow vs southbound_flow),Pydantic 默认忽略多余键→序列化出全 None 段,item 6(测 builder dict)与 item 10(测 schema)各自全绿但从未咬合。类似地 item 7 的三态文案若不显式断言 None 文案≠False 文案,`if eligible:` 真值化 bug(None/False 塌缩为『不可买』)测不出——这正是 §6 明令禁止的渲染。
**evidence**: spec §7 item 6(:190-191)/item 7(:192-193)/item 10(:197)三项断言站点互相独立;§4.3(:126-137)builder 与 schema 分层定义未规定键名奇偶测试;§6(:170-171)『禁把 None 渲染成不可买』无对应测试措辞;margin 蓝本同为分离测试但其键名映射在设计里有 D8 显式冻结(src/analyzer.py _build_margin_from_context docstring)。
**fix**: ①item 10 改为用 _build_ggt_from_context 的真实产物直接构造 GgtContext(**built) 钉死键名奇偶(替代手写 dict);②item 7 明确三态断言为三个互异字符串各自出现,并加显式反例:eligible=None 时输出禁止包含 False 文案(『非港股通标的』);③spec §4.3 仿 margin D8 写一行键名冻结映射。
