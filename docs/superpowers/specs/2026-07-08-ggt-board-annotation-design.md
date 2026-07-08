# Inc 2b:信号看板港股通(GGT)可买性注解 — 设计

**日期**:2026-07-08
**类型**:feat(actionable-signal 战略 Inc 2 的看板 follow-up)
**前置**:Inc 2 港股通南向维度(merge c1c10c64;报告 section 三数据面已落地),净流+超时修复(b56f2ca8)。

## 1. Context

Inc 2 在 **HK 标的分析报告** 中新增了港股通(GGT)南向 section(成份可买性 / 个股南向持股 / 市场级南向净流)。当时**看板可买性注解 defer 到 Inc 2b**,理由记录为:「港股通缓存是进程内模块级(`threading.Lock` dict),看板刷新(systemd `--schedule`)与报告生成(uvicorn)可能异进程 → 跨进程模块缓存不可见,看板侧 `eligible` 结构性恒 None」。

**2026-07-08 探索推翻了这个前提**:信号看板 **完全在 uvicorn FastAPI 请求处理器内按需计算**(`GET /board` → `build_board`),**没有** systemd/scheduler 预计算路径(全仓 grep `build_board` 只有定义 `signal_board_service.py:273` + 端点调用 `signals.py:52` 两处)。看板本就在请求内做 live `DataFetcherManager` 调用(每股日线历史 + 750 日 resonance 历史)。因此看板可以在**同一进程内自抓**一次全市场 eligibility set,不需要跨进程共享缓存、不需要落库/外部缓存基础设施迁移。

`get_ggt_eligibility_set()` 是**全市场、单次、进程级 12h 缓存**的抓取(`fundamental_adapter.py:660-690`;`ak.stock_hk_ggt_components_em()` 无股票入参、cache key 固定 `"k"`):一次抓取覆盖看板上所有 HK 标的,随后每行只是 `_ggt_key(code) in set` 的成员判定。

## 2. Goal / Non-Goals

**Goal**:在信号看板每行(`BoardEntry`)呈现港股通**可买性(eligibility)三态徽章**,presence-only、fail-closed、不影响任何决策/信号/排序。

**Non-Goals**:
- **不**在看板呈现个股南向持股、市场级南向净流(留在报告 section;净流是市场级单值,逐行重复无意义;持股占比属报告材料)。
- **不**触碰 `SignalMarker`(GGT 是 stock-level 属性,非 marker-derived,不走 `signal_stats` resolver)。
- **不**改看板排序/分组/计数/`action_group` 语义;GGT 纯展示。
- **不**新增配置开关(复用既有 `GGT_FETCH_TIMEOUT_SECONDS` + `GGT_LIST_CACHE_TTL_SECONDS`)。
- **不**在单股 `/signals` 端点重复 GGT(单股已有报告 section)。

## 3. 架构与数据流

GGT 看板注解是 **stock-level 属性**(镜像 `market` / `resonance` 这条线),不是 marker-derived。全链:

```
GET /board → build_board(codes, days, refresh, interval)          # signals.py:52 → signal_board_service.py:273
  → [现有] ThreadPoolExecutor 并行算 entries(缓存命中 + 新算)     # _compute_entry:249
  → [新增] 后置注解 pass:
       hk_entries = [e for e in entries if e["market"]=="HK" and not _is_etf_code(e["code"])]
       if hk_entries:
           elig_set = DataFetcherManager().get_ggt_eligibility_set()   # 一次,有界,进程级 12h 缓存,fail-closed None
           for e in hk_entries:
               e["ggt_eligible"] = _ggt_eligible_state(e["code"], elig_set)
  → 返回 {as_of, entries, counts, degraded_codes}                  # SignalsBoardResponse(**...) 端点零改
```

`ggt_eligible: Optional[bool]`(True/False/None)随 `SignalsBoardResponse(**build_board(...))` spread 构造自动透出(`signals.py:52`,端点零改)。

## 4. 门控与三态语义

**门控**(镜像报告 section scope):
- 仅 board entry `market == "HK"`(**大写**,来自 `_infer_market` @ `signal_board_service.py:59-67`)**且非 ETF**(`_is_etf_code(code)`)的行计算注解。
- 报告 section 对 HK ETF 返 `not_supported`,故看板对 HK ETF 也 → None(无徽章)。
- CN / US / crypto 行 `ggt_eligible` 恒 None(无徽章)。

> ⚠️ **市场大小写不一致(关键实现细节)**:看板 entry 的 `market` 由 `_infer_market` 产出 **大写** `"HK"`;而 GGT/adapter/引擎路径用 `get_market_for_stock` 产出**小写** `"hk"`。看板注解门控必须 key off entry 自带的 **大写 `"HK"`**,不要复用小写判定。

**三态**(共用纯函数 `_ggt_eligible_state(code, elig_set)`):
- `True` = `_ggt_key(code) in elig_set`
- `False` = `elig_set` 是真 `set`(适配器保证:抓取成功且 ≥50 有效成员,`fundamental_adapter.py:679,684`)但本股 key 不在其中
- `None` = `elig_set` 非 set(为 None:抓取失败 / <50 行 / 超时)或 非 HK / HK ETF → **无徽章**

**降级/失败行**(`_degraded_entry` @ `signal_board_service.py:233`):`ggt_eligible=None`(无法判定,不猜)。

**防漂移**:`_ggt_eligible_state` 抽为纯函数,让 `base.py:3646`(`get_ggt_context` 的 eligibility 推断)与看板注解 **共用同一实现**。`get_ggt_context` 改调该 helper,行为字节级不变。防「读写两处三态判定漂移」。

## 5. 抓取成本与错误处理(fail-closed 护栏)

- **无新配置开关**:复用 `GGT_FETCH_TIMEOUT_SECONDS`(有界超时)+ `GGT_LIST_CACHE_TTL_SECONDS`(12h 正缓存 / 300s 负缓存)。默认 always-on。符合「不叠加开关」护栏。
- **有界(关键)**:manager 新增 `get_ggt_eligibility_set()` 必须把适配器抓取包进 `self._run_with_retry(lambda: adapter.get_ggt_eligibility_set(), config.ggt_fetch_timeout_seconds, "ggt_eligibility")` —— 因为**适配器方法本身不经 G8 有界超时**(那是 `get_ggt_context._fetch_leg` 提供的,`base.py:3636-3643`)。这样看板路径拿到同款 G8 护栏:东财挂起(网络库无超时)不会拖死看板 API 请求。任一异常/超时 → 返回 None。
- **门控省成本**:纯 CN/US/crypto 看板(无 HK 非 ETF 行)**完全不触发** GGT 抓取(guard 短路)。
- **fail-closed**:超时/异常/None → 所有 HK 行 `ggt_eligible=None`(无徽章),看板照常渲染,绝不阻塞、不编造。
- **冷成本**:每 12h 窗口首个含 HK 股的看板一次性 ~19s(有界 20s),进程级缓存与报告生成共享 —— 近期跑过任何 HK 报告则已暖。看板本就每 directional 股抓 750 日历史,此成本按比例很小。
- **看板 300s 结果缓存交互**:注解 pass 每次 `build_board` 都跑(即使全缓存命中);暖缓存下只是「一次集合查 + 每 HK 行成员判定」(无网络)。故缓存 entry 始终带 ≤12h 新鲜的注解,与 `_BOARD_CACHE`(`signal_board_service.py:168`)300s 结果缓存解耦。

## 6. 全栈改动清单(7 处,镜像 `market`/`resonance` stock-level 线)

| # | 层 | 文件 | 改动 |
|---|---|---|---|
| 1 | Pydantic | `api/v1/schemas/stocks.py` | `BoardEntry`(class @185)加 `ggt_eligible: Optional[bool] = Field(None, description="港股通成份可买性三态:True=港股通标的/False=非标的/None=名单不可达或非HK")`(近 line 213);**不碰** `SignalMarker`;无 `model_config` 改动(extra 已 ignore) |
| 2 | Manager | `data_provider/base.py` | 新增公开方法 `get_ggt_eligibility_set()`(有界包裹,见 §5)+ 抽 `_ggt_eligible_state(code, elig_set)` 纯函数;`get_ggt_context` 的 `eligible = ...`(@3646)改调 helper(行为不变) |
| 3 | Board service | `src/services/signal_board_service.py` | §3 后置 pass(build_board @273);`_entry_from_board_signals`(@214)entry dict 默认 `ggt_eligible: None`;`_degraded_entry`(@233)加 `"ggt_eligible": None`;import `_is_etf_code` |
| 4 | 端点 | `api/v1/endpoints/signals.py` | **零改**(spread 构造 @52 自动携带) |
| 5 | TS 类型 | `apps/dsa-web/src/types/kline.ts` | `BoardEntry`(@75)加 `ggtEligible: boolean \| null`(近 line 93) |
| 6 | mapper | `apps/dsa-web/src/api/stocks.ts` | `RawBoardEntry`(@84)加 `ggt_eligible?: boolean \| null`;`mapBoardEntry`(@105)加 `ggtEligible: r.ggt_eligible ?? null`(legacy 容错) |
| 7 | 渲染 + helper | `apps/dsa-web/src/components/board/SignalBoardGroup.tsx` + `apps/dsa-web/src/utils/credibility.ts` | 命中率 `<td>` 内 resonance 徽章旁(@70-76)加 GGT 徽章;`credibility.ts` 加 `ggtLabel(ggtEligible)` 中文硬编码(镜像 `verifiedLabel` @35),None 返 null |

**徽章语义**:True→「港股通」(success 色)、False→「非港股通」(muted 色)、None→不渲染(presence-only,与报告 section「None 禁含'非港股通标的'防误读」一致)。中文硬编码,镜像看板现有 verified/resonance/excess 徽章(均不读 report_language)。

## 7. 测试

**后端**:
- board service:HK 行三态(mock `get_ggt_eligibility_set` 返 set/None)、非 HK→None、HK ETF→None、degraded→None、**set 每 build_board 只抓一次**(多 HK 行 → spy call_count==1)、**无 HK 行不抓**(纯 CN 看板 → spy call_count==0 门控)、超时/None→全 HK 行 None 且看板仍返回全部 entry(fail-closed)。
- manager `get_ggt_eligibility_set`:有界(挂起适配器 sleep → 超时内返 None,镜像 `test_get_ggt_context_hung_leg_is_bounded_not_blocking`)+ 委托适配器 + set/None 透传。
- 共用 `_ggt_eligible_state`:True/False/None 三态;`get_ggt_context` 重构后既有 12 个 test_ggt_context 测试仍全绿(行为字节级不变)。
- Pydantic `BoardEntry`:收 True/False/None + legacy payload 无字段 → None。

**前端**:
- `mapBoardEntry`:`ggt_eligible` → `ggtEligible`,`?? null` legacy 容错(无字段 → null)。
- `SignalBoardGroup` vitest:True→「港股通」徽章在;False→「非港股通」徽章在;None→无徽章。
- `ggtLabel` 单测:True/False/None → 标签/标签/null。

**门禁**:`./scripts/ci_gate.sh`(后端,含新增测试)+ web-gate(`cd apps/dsa-web && npm ci && npm run lint && npm run build`,动了前端必跑)。

## 8. 验证

- 后端:`PATH=.venv/bin:$PATH ./scripts/ci_gate.sh` 全绿(基线 4003 → +新增测试)。
- 前端:`npm run lint`(eslint . 零错)+ `npm run build` 绿。
- 真网可选(关沙箱前台):`GET /board` 含 HK 标的(如 hk00700)→ 确认 entry 带 `ggt_eligible=true`、非港股通 HK 股 → false、东财不可达 → None 全行无徽章且看板正常返回。

## 9. 风险与回滚

**风险**:
- 冷缓存下含 HK 股的首个看板请求一次性 ~19s(有界 20s、每 12h 一次、与报告缓存共享)。缓解:门控 + 有界 + fail-closed;若嫌慢可后续加 opt-in「仅暖缓存才注解」旁路(非本增量)。
- 市场大小写(`"HK"` vs `"hk"`)踩坑:门控 key off entry 大写 `market`,已在 §4 标红,测试覆盖非 HK→None。
- `get_ggt_context` 重构(改调 helper):byte-identical,既有 12 测试锁定。

**回滚**:全 additive;`git revert` 单 commit 即可。前端字段 legacy 容错(`?? null`),旧后端载荷不含 `ggt_eligible` → 前端 null → 无徽章,零破坏。

## 10. 交付结构提醒

改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式;CHANGELOG `[Unreleased]` 扁平一行 `[新功能]`;文档 `docs/ggt-southbound.md`「看板注解 defer 到 Inc 2b」段订正为「已落地」。
