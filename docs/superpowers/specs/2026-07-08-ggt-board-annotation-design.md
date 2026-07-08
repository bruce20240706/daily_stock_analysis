# Inc 2b:信号看板港股通(GGT)可买性注解 — 设计

**日期**:2026-07-08
**类型**:feat(actionable-signal 战略 Inc 2 的看板 follow-up)
**前置**:Inc 2 港股通南向维度(merge c1c10c64;报告 section 三数据面已落地),净流+超时修复(b56f2ca8)。
**修订**:2026-07-08 经 4 视角对抗审查(13 confirmed)修订——HK ETF 门控、degraded 门控、TS 测试工厂、manager 单例、tuple 解包、缓存浅拷贝、docs 范围。

## 1. Context

Inc 2 在 **HK 标的分析报告** 中新增了港股通(GGT)南向 section(成份可买性 / 个股南向持股 / 市场级南向净流)。当时**看板可买性注解 defer 到 Inc 2b**,理由记录为:「港股通缓存是进程内模块级(`threading.Lock` dict),看板刷新(systemd `--schedule`)与报告生成(uvicorn)可能异进程 → 跨进程模块缓存不可见,看板侧 `eligible` 结构性恒 None」。

**2026-07-08 探索推翻了这个前提**:信号看板 **完全在 uvicorn FastAPI 请求处理器内按需计算**(`GET /board` → `build_board`),**没有** systemd/scheduler 预计算路径(全仓 grep `build_board` 只有定义 `signal_board_service.py:273` + 端点调用 `signals.py:52` 两处)。看板本就在请求内做 live `DataFetcherManager` 调用(每股日线历史 + 750 日 resonance 历史)。关键点是 **每个请求在自己的进程内自抓** eligibility set —— 因此**与 worker 数无关**:即便未来 uvicorn `--workers N` 多进程部署,每个 worker 只是各自把 set 抓进自己的模块级 12h 缓存(冷成本 ×N,不破坏正确性)。不需要跨进程共享缓存、不需要落库/外部缓存基础设施迁移。

`get_ggt_eligibility_set()` 是**全市场、单次、进程级 12h 缓存**的抓取(`fundamental_adapter.py:660-690`;`ak.stock_hk_ggt_components_em()` 无股票入参、cache key 固定 `"k"`):一次抓取覆盖看板上所有 HK 标的,随后每行只是 `_ggt_key(code) in set` 的成员判定。

## 2. Goal / Non-Goals

**Goal**:在信号看板每行(`BoardEntry`)呈现港股通**可买性(eligibility)三态徽章**,presence-only、fail-closed、不影响任何决策/信号/排序/计数。

**Non-Goals**:
- **不**在看板呈现个股南向持股、市场级南向净流(留在报告 section;净流是市场级单值,逐行重复无意义;持股占比属报告材料)。
- **不**触碰 `SignalMarker`(GGT 是 stock-level 属性,非 marker-derived,不走 `signal_stats` resolver)。
- **不**改看板排序/分组/计数/`action_group` 语义;GGT 纯展示。
- **不**新增配置开关(复用既有 `GGT_FETCH_TIMEOUT_SECONDS` + `GGT_LIST_CACHE_TTL_SECONDS`)。
- **不**在单股 `/signals` 端点重复 GGT(单股已有报告 section);故注解只在 `build_board` 后置,不入 `build_signals_for_code`(被单股端点复用)。
- **不**给 HK ETF 做特殊 not_supported 处理(见 §4:报告 section 本身也不特殊化 HK ETF,看板镜像之以保 报告↔看板 parity)。

## 3. 架构与数据流

GGT 看板注解是 **stock-level 属性**(镜像 `market` / `resonance` 这条线),不是 marker-derived。全链在 `build_board` 内、并行算完 entries 之后做一次**后置注解 pass**:

```
GET /board → build_board(codes, days, refresh, interval)          # signals.py:52 → signal_board_service.py:273
  → [现有] ThreadPoolExecutor 并行算 entries(缓存命中 + 新算)     # _compute_entry:249
  → [新增] _annotate_ggt(entries):
       hk_ok = [e for e in entries if e["market"]=="HK" and e["status"]=="ok"]
       if not hk_ok:                                              # 门控短路:纯 CN/US/crypto 或全 degraded → 零成本
           return entries
       elig_set = _get_ggt_manager().get_ggt_eligibility_set()    # 一次,有界,进程级 12h 缓存,fail-closed None
       return [
           {**e, "ggt_eligible": _ggt_eligible_state(e["code"], elig_set)}   # 浅拷贝,不原地改缓存 dict
           if (e["market"]=="HK" and e["status"]=="ok") else e
           for e in entries
       ]
  → 返回 {as_of, entries, counts, degraded_codes}                  # SignalsBoardResponse(**...) 端点零改
```

`ggt_eligible: Optional[bool]`(True/False/None)随 `SignalsBoardResponse(**build_board(...))` spread 构造自动透出(`signals.py:52`,端点零改)。

## 4. 门控与三态语义

**门控**:仅 board entry `market == "HK"`(**大写**,来自 `_infer_market` @ `signal_board_service.py:59-67`)**且 `status == "ok"`** 的行计算注解。

- **`status == "ok"` 排除 degraded 行**(`_degraded_entry` 置 `status="degraded"` @ `signal_board_service.py:243`):degraded 行「无法完成信号计算」,不注解 GGT(保持 `ggt_eligible=None`,与 §6 `_degraded_entry` 的 None 默认一致)。`status=="ok"` 也恰是被 `_BOARD_CACHE` 缓存的集合(@267),故注解 pass 只碰这批 —— 见下「浅拷贝」。
- **不再对 HK ETF 做门控**(经审查:`_is_etf_code` @ `base.py:222` 只识别 A股 6 位数字 ETF 码,对任何 HK 码恒返 False;`get_ggt_context` 的门控 `_market_tag != "hk" or _is_etf_code(code)` @ `base.py:3618` 对 HK ETF 也是 `False or False` → **不返 not_supported**,而是照常算 True/False)。故看板**镜像报告**:HK ETF 若不在 `stock_hk_ggt_components_em` 成份表 → False(「非港股通」),与报告行为一致,不引入 报告↔看板 漂移。原设计「HK ETF→None」基于错误前提,已删除。

> ⚠️ **市场大小写不一致(关键实现细节)**:看板 entry 的 `market` 由 `_infer_market` 产出 **大写** `"HK"`;而 GGT/adapter/引擎路径用 `get_market_for_stock` 产出**小写** `"hk"`。看板注解门控必须 key off entry 自带的 **大写 `"HK"`**,不要复用小写判定。

**三态**(共用纯函数 `_ggt_eligible_state(code, elig_set)`,与 `get_ggt_context` 同源):
- `True` = `_ggt_key(code) in elig_set`
- `False` = `elig_set` 是真 `set`(适配器保证:抓取成功且 ≥50 有效成员,`fundamental_adapter.py:679,684`)但本股 key 不在其中
- `None` = `elig_set` 非 set(为 None:抓取失败 / <50 行 / 超时)→ **无徽章**;非 HK 行 / degraded 行也 None(不进 pass)

**防漂移**:`_ggt_eligible_state` 抽为纯函数放 `data_provider/fundamental_adapter.py`(与 `_ggt_key` 同处),让 `base.py:3646`(`get_ggt_context` 的 eligibility 推断)与看板注解 **共用同一实现**。`get_ggt_context` 的 `eligible = (_ggt_key(code) in elig_set) if isinstance(elig_set, set) else None` 改调 `eligible = _ggt_eligible_state(code, elig_set)`,行为字节级不变(既有 12 个 test_ggt_context 锁定)。

## 5. 抓取成本、并发与错误处理(fail-closed 护栏)

- **无新配置开关**:复用 `GGT_FETCH_TIMEOUT_SECONDS`(有界超时)+ `GGT_LIST_CACHE_TTL_SECONDS`(12h 正缓存 / 300s 负缓存)。默认 always-on。符合「不叠加开关」护栏。
- **有界(关键)**:manager 新增 `get_ggt_eligibility_set()` 把适配器抓取包进 `_run_with_retry` 并**按其三元组返回约定解包**(`_run_with_retry` 返回 `(payload, err, ms)`、超时不抛而是返 `(None, err, ms)`,`base.py:2627-2657`):
  ```python
  def get_ggt_eligibility_set(self):
      from src.config import get_config
      cap = max(0.0, float(get_config().ggt_fetch_timeout_seconds))
      if cap <= 0:
          return None
      try:
          payload, _err, _ms = self._run_with_retry(
              lambda: self._fundamental_adapter.get_ggt_eligibility_set(), cap, "ggt_eligibility")
          return payload if isinstance(payload, set) else None   # 三元组解包,非直接 return 整个 tuple
      except Exception:
          return None
  ```
  适配器方法本身**不经** G8 有界超时(那是 `get_ggt_context._fetch_leg` 提供的);此包裹给看板路径同款 G8 护栏:东财挂起(网络库无超时)不会拖死看板 API 请求。
- **manager 复用单例(避免每请求 churn)**:board 用**模块级懒加载单例** manager(镜像 `src/services/history_loader.py:_get_fetcher_manager` 双检锁 @50-57),而非每请求 `DataFetcherManager()`。后者 `__init__` 会建 9-13 个 fetcher + 2 个基本面适配器 + 锁/信号量并打一行 INFO 日志(`base.py:724-754`),而 eligibility 只用 `_fundamental_adapter` —— 每次轮询新建整个 manager 是纯浪费/日志噪音。单例只在进程内建一次。
- **门控省成本**:`hk_ok` 为空(纯 CN/US/crypto 看板 或 全 degraded)→ **完全不触发** manager 获取/GGT 抓取(guard 短路 return)。
- **fail-closed**:超时/异常/None → 所有 HK 行 `ggt_eligible=None`(无徽章),看板照常渲染全部 entry,绝不阻塞、不编造。
- **冷成本(已知、有界、可摊薄)**:每 12h 窗口首个含 HK 股的看板一次性 ~19s(有界 20s),进程级缓存与报告生成共享 —— 近期跑过任何 HK 报告则已暖。看板本就每 directional 股同步抓 750 日历史(端点本就是重同步 live-fetch 路径),此冷成本按比例很小,且只阻塞单个请求方(FastAPI 同步路径跑在 anyio threadpool,不阻塞其他请求)。若后续嫌慢,可加 opt-in「仅暖缓存才注解 + 后台预热」旁路(非本增量)。
- **缓存浅拷贝(不原地改缓存 dict)**:`_compute_entry` 缓存命中返回的是 `_BOARD_CACHE` 里的**同一个 dict 对象**(@256),且被缓存的正是 `status=="ok"` 的行 —— 即注解 pass 要碰的行。故 pass **对每个 HK-ok entry 浅拷贝**(`{**e, "ggt_eligible": ...}`)再写,**不原地 mutate** 缓存对象,避免并发 `/board` 请求在锁外竞写共享 dict。注解每请求重算(读进程级 12h 缓存的 set + 成员判定,暖态无网络),故徽章新鲜度跟随 12h eligibility 缓存而非 300s 看板缓存。

## 6. 全栈改动清单(镜像 `market`/`resonance` stock-level 线)

| # | 层 | 文件 | 改动 |
|---|---|---|---|
| 1 | Pydantic | `api/v1/schemas/stocks.py` | `BoardEntry`(class @185)加 `ggt_eligible: Optional[bool] = Field(None, description="港股通成份可买性三态:True=港股通标的/False=非成份/None=名单不可达或非HK")`(近 line 213);**不碰** `SignalMarker`;无 `model_config` 改动(extra 已 ignore、legacy 安全) |
| 2 | 适配器 helper | `data_provider/fundamental_adapter.py` | 新增纯函数 `_ggt_eligible_state(code, elig_set)`(与 `_ggt_key` 同处;`isinstance(set)` 守卫 → True/False,否则 None) |
| 3 | Manager | `data_provider/base.py` | 新增公开方法 `get_ggt_eligibility_set()`(有界包裹 + 三元组解包,见 §5);`get_ggt_context` 的 `eligible = ...`(@3646)改调 `_ggt_eligible_state`(byte-identical);import `_ggt_eligible_state` |
| 4 | Board service | `src/services/signal_board_service.py` | 新增 `_annotate_ggt(entries)` 后置 pass(§3)+ 模块级单例 `_get_ggt_manager()`(镜像 history_loader);`build_board`(@273 return 前)调 `entries = _annotate_ggt(entries)`;`_degraded_entry`(@233)加 `"ggt_eligible": None`(**冗余但符合该 builder 全字段枚举惯例**,post-pass 不碰 degraded 行,靠 Pydantic 默认亦为 None);import `_ggt_eligible_state` + `DataFetcherManager` |
| 5 | 端点 | `api/v1/endpoints/signals.py` | **零改**(spread 构造 @52 自动携带) |
| 6 | TS 类型 | `apps/dsa-web/src/types/kline.ts` | `BoardEntry`(@75)加 `ggtEligible: boolean \| null`(近 line 93,required 字段) |
| 7 | mapper | `apps/dsa-web/src/api/stocks.ts` | `RawBoardEntry`(@84)加 `ggt_eligible?: boolean \| null`;`mapBoardEntry`(@105)加 `ggtEligible: r.ggt_eligible ?? null`(legacy 容错) |
| 8 | 渲染 + helper | `apps/dsa-web/src/components/board/SignalBoardGroup.tsx` + `apps/dsa-web/src/utils/credibility.ts` | 命中率 `<td>` 内 resonance 徽章旁(@70-76)加 GGT 徽章;`credibility.ts` 加 `ggtLabel(ggtEligible)` 中文硬编码(镜像 `verifiedLabel` @35),None 返 null |
| 9 | TS 测试工厂 | `apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx` + `apps/dsa-web/src/pages/__tests__/SignalBoardPage.test.tsx` | 两处构造完整 `BoardEntry` 字面量的工厂(`mk` @16 / `entry` @27)加 `ggtEligible: null` —— 否则 required 新字段缺失致 `tsc` 报错、web-gate `npm run build` RED(Inc 1c 加 familySize/ciLowCorrected 时同样改过这两个工厂) |

**徽章语义**:True→「港股通」(success 色)、False→「非港股通」(muted 色)、None→不渲染(presence-only,与报告 section「None 禁含'非港股通标的'防误读」一致)。中文硬编码,镜像看板现有 verified/resonance/excess 徽章(均不读 report_language)。

## 7. 测试

**后端**:
- board service `_annotate_ggt`:HK-ok 行三态(mock `_get_ggt_manager().get_ggt_eligibility_set` 返 set/None)、非 HK→None(不注解)、**degraded HK 行→None**(status 门控,不被覆盖)、**HK ETF→镜像报告 True/False**(不特殊化)、**set 每 build_board 只抓一次**(多 HK 行 → spy call_count==1)、**无 HK-ok 行不抓**(纯 CN 看板 / 全 degraded → spy call_count==0 门控短路)、超时/None→全 HK 行 None 且看板仍返回全部 entry(fail-closed)、**注解不 mutate `_BOARD_CACHE` 缓存 dict**(缓存命中对象 `ggt_eligible` 不被原地改;断言浅拷贝)。
- manager `get_ggt_eligibility_set`:有界(挂起适配器 sleep → 超时内返 None,镜像 `test_get_ggt_context_hung_leg_is_bounded_not_blocking`)+ 委托适配器 + **三元组解包**(适配器返 set → 方法返 set,非返 tuple)+ set/None 透传。
- 共用 `_ggt_eligible_state`:True/False/None 三态;`get_ggt_context` 重构后既有 12 个 test_ggt_context 测试仍全绿(行为字节级不变)。
- Pydantic `BoardEntry`:收 True/False/None + legacy payload 无字段 → None。

**前端**:
- `mapBoardEntry`:`ggt_eligible` → `ggtEligible`,`?? null` legacy 容错(无字段 → null)。
- `SignalBoardGroup` vitest:True→「港股通」徽章在;False→「非港股通」徽章在;None→无徽章。
- `ggtLabel` 单测:True/False/None → 标签/标签/null。
- 两个 BoardEntry 测试工厂加 `ggtEligible: null`(见 §6 #9),保 `tsc` 绿。

**门禁**:`./scripts/ci_gate.sh`(后端,含新增测试)+ web-gate(`cd apps/dsa-web && npm ci && npm run lint && npm run build`,动了前端必跑)。

## 8. 验证

- 后端:`PATH=.venv/bin:$PATH ./scripts/ci_gate.sh` 全绿(基线 4003 → +新增测试)。
- 前端:`npm run lint`(eslint . 零错)+ `npm run build`(tsc+vite,须绿,验 §6 #9 工厂已补)。
- 真网可选(关沙箱前台):`GET /board` 含 HK 标的(如 hk00700)→ 确认 entry 带 `ggt_eligible=true`、非成份 HK 股 → false、东财不可达 → None 全行无徽章且看板正常返回。

## 9. 风险与回滚

**风险**:
- 冷缓存下含 HK 股的首个看板请求一次性 ~19s(有界 20s、每 12h 一次、与报告缓存共享、仅阻塞单个请求方)。缓解:门控 + 有界 + fail-closed + 单例 manager 减 churn;若嫌慢可后续加 opt-in「仅暖缓存才注解 + 后台预热」旁路(非本增量)。
- 市场大小写(`"HK"` vs `"hk"`)踩坑:门控 key off entry 大写 `market`,已在 §4 标红,测试覆盖非 HK→None。
- HK ETF 呈现为「非港股通」(False):这是**镜像报告的既有行为**(报告对 HK ETF 也算 False),非本增量新引入的误差;修 ETF 可买性需改数据源口径,超出本增量。
- `get_ggt_context` 重构(改调 helper):byte-identical,既有 12 测试锁定。

**回滚**:全 additive;`git revert` 单 commit 即可。前端字段 legacy 容错(`?? null`),旧后端载荷不含 `ggt_eligible` → 前端 null → 无徽章,零破坏。

## 10. 交付结构提醒

改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式;CHANGELOG `[Unreleased]` 扁平一行 `[新功能]`。

**文档 `docs/ggt-southbound.md` 需三处订正**(不止改标题):
1. 「看板注解 defer 到 Inc 2b」整段(含「看板侧的 eligible 读取会结构性地永远命中 None」的**跨进程缓存论据**)—— 该前提已被 §1 推翻,改述为「看板 uvicorn 请求内自抓 whole-market eligibility set 注解,不需跨进程共享缓存」并标记已落地。
2. `## v1 已知局限` 段的独立 bullet「看板可买性注解 deferred 至 Inc 2b(跨进程缓存基础设施缺口)」—— 删除或改为已落地。
3. 若有其他引用「看板 defer」的措辞一并核对。
