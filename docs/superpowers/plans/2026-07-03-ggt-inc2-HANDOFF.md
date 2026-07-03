# Inc 2 港股通南向维度 — 续作交接(2026-07-03 关机存档)

> 本文件是下次续作的**主入口**。读完本文即可无缝接续。非仓库正式文档,续作完成后可删。

## 0. 一句话状态

Inc 2(港股通南向维度)卡在 **brainstorming 的对抗审查折进阶段**:spec 草案已写 + 已过一轮
4 视角对抗审查(28 findings),但**反驳复核阶段因额度耗尽全部失败**,findings 未经证伪筛选;
且我已独立确认其中的 Blocker 属实(不是误报)。**下一步 = 把 findings 折进 spec → 自审 →
用户审阅门 → writing-plans → SDD**。尚未写任何实现代码。

## 1. 三项请求的整体进度(用户指令"按顺序执行 1,2,3")

- **项目 3(小件)✅ 全完成**:fork 备份已推 `git push origin main:backup/local-main`
  (`7d450f99..9ef8b2d4`,49 commits,纯 FF 无 force);滚动 walk-forward 维持顺延(样本现实)。
- **项目 2(合规红线 Inc 0-a)✅ 已决**:2026-07-03 暂定 **「自用内部」**(用户"执行合规决策"
  但 AskUserQuestion 批答超时→取最保守档=定档现状,不对外分发信号、不新增对外能力;
  可升级档,用户明确改选即升级)。**待办**:repo 内 `docs/strategy-actionable-signal-system.md`
  §3 的 Inc 0-a 段尚未同步此暂定档——下次 docs commit 顺手同步。
- **项目 1(Inc 2 港股通)🔄 进行中**:见下,是续作的主体。

## 2. Inc 2 已产出物(均在磁盘,重启不丢)

- **spec 草案 v2**:`docs/superpowers/specs/2026-07-03-ggt-southbound-context-design.md`
  (未跟踪未 commit;已含我关机前折进的领域订正——见 §4)。
- **对抗审查 28 findings 全文**:`docs/superpowers/specs/2026-07-03-ggt-review-findings.md`
  (紧凑清单)+ workflow journal `.../subagents/workflows/wf_0aa0923f-774/journal.jsonl`
  + 完整 JSON `tool-results/bq71sgmeh.txt`(后两者在 session dir,可能随 session 清理,
  故已提取到前者 repo 内)。
- 未建 worktree、未起分支、零实现代码。

## 3. 续作第一步(精确指令)

1. 读 spec v2 + `2026-07-03-ggt-review-findings.md`。
2. **findings 未经反驳复核**(verify 阶段全 fail)——续作时可选:(a)重跑该 workflow 的
   verify 阶段(`Workflow({scriptPath: '.../ggt-spec-adversarial-review-wf_0aa0923f-774.js',
   resumeFromRunId: 'wf_0aa0923f-774'})`,Find 阶段缓存命中、只补跑 verify);或(b)因 Blocker
   已我方独立确认属实,直接人工裁定折进(更省)。
3. 把确认的 findings 折进 spec(§4 列出已折进的、§5 列出**待折进**的)。
4. spec 自审(占位/矛盾/歧义/范围)→ **停在用户审阅门**(决策表 G1-G10 逐条可否决,
   尤其 G1「注解 vs 硬过滤」)。
5. 用户批 spec 后 → writing-plans → SDD(worktree `/root/chainb-...` 无空格路径,base=main=9ef8b2d4)。

## 4. 我关机前已折进 spec 的领域订正(本地 inspect akshare 1.18.64 源码所得,已改入 v2)

- **端点更正(承重)**:个股南向持股原写 `stock_hsgt_individual_em` **是错的**——inspect 证实
  它是**北向**个股接口(默认参 002008=A股码)。已改为
  `stock_hsgt_stock_statistics_em(symbol="南向持股", start_date=末端日, end_date=同)`,
  返回全市场南向持股日表,本股按归一码查行。`stock_hsgt_hold_stock_em` 亦仅北向/沪/深无南向,已排除。
- 缓存升级为三份(名单/持股日表/净流),持股日表一次抓服务当日全部 HK 标的 + 末端日回退≤5 日。
- 砍掉"持股近 N 日变动"(无稳定端点,YAGNI)。
- G3/G5/G2/§3.2/§4.1 已同步。

## 5. **待折进 spec 的 findings(下次必做,按严重度)**

### Blocker(4,其中 F1/GGT-CONTRACT-1 是同一 bug 的两次独立发现)
- **F1 = GGT-CONTRACT-1 [已我方确认属实]**:`normalize_stock_code` 对**裸 5 位码**('00700')
  原样返回**不加 HK 前缀**(base.py:188),而东财成份表返回的正是裸 5 位码 → 按 spec 字面
  "成份表归一后建 set" 会导致 set 键='00700' vs 查询侧 canonical 'HK00700' **永不同键** →
  所有 HK 标的 eligible=False 假阴性 → 报告编造"非港股通标的"违反 G4 fail-closed。
  **修法**:定义共享键函数 `_ggt_key(code)`(normalize 后若仍纯数字则补 `HK`+zfill(5)),
  ingest 侧与查询侧(含 get_ggt_eligible_cached)**同一函数**;§7.2 测试 set 侧 fixture 必须
  用裸 '00700'/'01810' 钉住陷阱。**前科**:yfinance 裸5位误映射曾被终审逮出(c356d6b0)。
- **GGT-CONTRACT-2 [已我方确认属实]**:§4 改动清单**遗漏 margin 蓝图的真实接线层
  `src/core/pipeline.py`**。margin section 进报告靠 pipeline 在 LLM 后调
  `fill_margin_if_needed(result, fundamental_context)`(pipeline.py:627-631 与 :1150-1154 两处)。
  spec 只列 `_build_ggt_from_context` 构建器,无 `fill_ggt_if_needed` 包装器、无 pipeline.py 项 →
  照 spec 落地会**四层全写完、spec 内测试全绿,但 data_perspective 永无 ggt_context 键 →
  报告端到端不出现**且无测试变红(核心交付静默落空)。**修法**:§4.3 补 `fill_ggt_if_needed`
  (仿 analyzer.py:944)+ §4 新增 pipeline.py 两处调用点(镜像 :631/:1154);§7 补 fill 级测试
  (ok→data_perspective['ggt_context'] 出现;failed→不出现)。

### Important(15,择要——全文见 findings.md)
- **GGT-CONTRACT-3**:§4.3"section 非 None 才注入 prompt"与 G8"镜像 margin"矛盾——margin 是
  **LLM 后**填充明文"不喂 prompt"(analyzer.py:949),fill 发生在 LLM 后,"注入 prompt"无执行点。
  真实 prompt 注入前例是 `_dragon_tiger_prompt_line`(LLM 前直读 fundamental_context)。
  **修法**:二选一钉死——ggt 若要进 prompt 走 dragon_tiger 式(LLM 前门控 status),
  若只进报告展示走 margin 式(LLM 后 fill、不进 prompt);推荐后者(与"纯展示维度"一致)。
- **F2**:`get_ggt_holding` 传端点的 symbol 形态未指定;若传 canonical 'HK00700'(len7)会拼成
  'HK00700.HK' 空结果被吞→个股持股生产永久 None 且与"端点不可达"不可辨。修法同 F1 剥前缀取裸补零码;
  §7.11 真网 probe 至少一条走 adapter 公开方法+canonical 入参端到端,不许直调 akshare 函数。
  (注:此项建立在已被 §4 更正掉的旧端点上,续作时按新端点 stock_hsgt_stock_statistics_em
  的实际码形态重核——**该端点按日返回全市场表、非按股查**,F2 的具体机制需重新核实,但"码形态
  必须钉死+probe 走 adapter"的结论仍成立。)
- **F3**:持股字段真实列名与 spec 臆造有偏差(占比列真名「持股数量占A股百分比」/change_5d 实为
  「持股市值变化-5日」是市值元非持股量/返回按持股日期升序多页拉取取 iloc[-1])——fixture 必须
  用 akshare 1.18.64 源码 rename 后**逐字真实列名**,否则测试自说自话真网零命中。
  (同样建立在旧端点上,新端点 stock_hsgt_stock_statistics_em 的真实列名需 inspect 重核。)
- **F4**:南向净流"假零"陷阱——fund_flow_summary 数值列 to_numeric(errors="coerce")产 NaN,
  按 §3.3 直接 Series.sum()(skipna=True)在两腿全 NaN 时得 **0.0** 编造"净流 0 亿"违反 G4。
  修法:§3.3 钉死 NaN 判定先行(两腿皆 NaN→None 禁 sum,单腿 NaN→partial);选列(成交净买额
  vs 资金净流入口径差)钉死;§7.1 增"两腿 NaN fixture→None 非 0.0"RED 用例。
- 其余 Important(contract 面 4-6、robust、scope 视角):BoardEntry/`_degraded_entry` 精确键集
  断言站点必改、offshore context 邻域回归锚点、`_market_tag` 对 HK 返回 "hk" 小写(spec §4.4 写
  market=="HK" 大写需核对)、东财长期不可达下"失败不缓存"=每次报告重试三端点打满 timeout
  (是否要负缓存)、facade 兜底覆盖映射——**逐条见 findings.md,续作时全部过一遍**。

### Minor(9)
- F5(成份端点无 partial 态/无日期列/sell-only 股票"可买"过度承诺)、F6(南向净流币种 HKD/CNY
  未声明,akshare 只 /10000 不换算)等——见 findings.md,多为诚实边界补一句。

## 6. 关键环境/规约备忘(续作照旧)

- 主仓路径含空格:python 命令前置 `export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(引号)。
- worktree 用无空格持久路径 `/root/<名>`,勿用 /tmp(重启清)。
- 子代理"后台等死"教训:验证前台有界跑,派发词写显式同回合轮询协议,勿挂 Monitor 等后台 shell。
- commit=英文类型前缀+中文正文,无 Co-Authored-By;未经确认不 merge/push/tag。
- ci_gate ~650-850s 超单次 Bash 上限→后台+退出码文件+同回合轮询;web-gate 仅在改 registry/locale/
  前端时触发(本增量 G7 决定不进 registry/locale→**免 web-gate**)。
- **本沙箱东财不可达**→GGT 真网端到端 deferred;离线 fixtures 全覆盖为主。
- akshare 已装 1.18.64;续作核端点用**本地 inspect 源码**(`inspect.getsource(ak.xxx)`)不发网。
- main head=**9ef8b2d4**(本地=fork backup 同步);Inc1 主干(1a/1b/1c/1d/1e)全完成。

## 7. Task 台账(TaskList 中)

- #346 GGT-1 探索 完成;#347 GGT-2/3/4(澄清取默认+方案+spec+审查+用户门)进行中——
  卡在"对抗审查折进"这一步。续作把 #347 推进到用户审阅门。
