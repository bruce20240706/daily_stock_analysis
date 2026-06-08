# crypto 大盘复盘设计

- 状态：已评审，待实施（writing-plans）
- 日期：2026-06-08
- 范围类型：feat（高风险区：报告结构 / 大盘复盘 / 数据源 / 配置语义 / 调度）
- 关联：crypto 市场支持（per-stock 分析、持仓、报告文案已合入 main）

## 0. 目标 / 非目标

### 目标
让 `MARKET_REVIEW_REGION` 支持 `crypto`，产出一份"主流币篮子行情 + 市场叙事 + 新币/上新资讯"的大盘复盘，**完全复用**现有 region 管线：`MarketAnalyzer` → 结构化 `market_review_payload` → 持久化（DB history + 可选文件）→ Web 渲染 → 通知。crypto 复盘全程 **opt-in**。

具体能力：
1. crypto 作为第 4 个 region 接入现有复盘管线（cn/hk/us → +crypto）。
2. 复盘正文含**主流币篮子行情表**（默认 ~12 主流币，可配置覆盖/追加任意币）。
3. 复盘正文含**新币与上新动态**叙事段（IEO/launchpad/新上线资讯，纯 LLM 叙事，零新数据源）。
4. 市场叙事/策略由 LLM 基于篮子行情 + 新闻产出。

### 非目标（本期明确不做）
- 不引入任何新的结构化数据源：BTC 占比（dominance）、总市值、恐贪指数、market-wide breadth（涨跌家数）、板块/赛道排名、资金费率、未平仓、清算、链上数据 —— 一律不做。
- 不做"合成 breadth"（用固定 watchlist 自造涨跌家数喂指标）—— 违反仓库反 fabricated-metric / 反静默降级原则。
- crypto **不生成** `MarketLightSnapshot`（0-100 市场灯/评分）。
- `MARKET_REVIEW_REGION=both` 语义**不变**（仍 = cn+hk+us），crypto 不进入 `both`。
- **不做**新币的"自动发现 + 结构化上新行情表"（见 §11 后续子项目2）。

## 1. 架构原则：全量接线，不留半门控

crypto 作为第 4 个 region 接入现有 `MarketAnalyzer` 管线，而非另起平行实现（遵循 AGENTS.md "优先复用现有模块，不新增平行实现"）。

**核心纪律**：当前代码有多处 `cn|hk|us` 白名单在遇到未知 region 时**静默回退到 `cn`**：

- `src/config.py:2277` `_parse_market_review_region()` 白名单 `('cn','us','hk','both')`，否则 warn + fallback `cn`
- `src/market_analyzer.py:138` `self.region = region if region in ("cn","us","hk") else "cn"`
- `src/core/market_strategy.py:166-172` `get_market_strategy_blueprint()` 未知 region 回退 CN 蓝图
- `src/core/market_review.py:69-84` `_resolve_market_review_regions()` 非法 → `['cn']`

风险：若只接一部分，会出现 `get_profile('crypto')` 可用、但 `_parse_market_review_region('crypto')` 把 crypto 悄悄变 `cn` 的死分支（用户配了 crypto，跑出来是 A 股复盘）。

**结论：要么全量接线，要么不接。** 本设计要求 §3 清单中所有 touch-point 同批改完，并有测试覆盖"crypto 不被回退成 cn"。

## 2. 数据层：主流币篮子

### 2.1 篮子来源与配置
- 默认篮子（市场概览代表，~12 主流币，QUOTE=USDT）：
  `BTC/ETH/BNB/SOL/XRP/DOGE/ADA/AVAX/LINK/TRX/TON/DOT`
- 可经新增可选环境变量 **`CRYPTO_MARKET_REVIEW_SYMBOLS`** 完全覆盖或追加任意币（如 `HYPE/USDT,FIL/USDT,SUI/USDT`）。遵循"不配置也可运行，配置后增强"。
- 默认值作为常量放在 `src/core/market_profile.py`（贴近 `CRYPTO_PROFILE`）；env 解析复用现有 crypto 代码校验（`is_crypto_code`，QUOTE ∈ SUPPORTED_QUOTES）。非法项跳过并告警，不静默吞。

### 2.2 行情获取
- 给 `get_main_indices(region)` 增加 **crypto 分支**（今天没有该分支，crypto 会返回 `[]`）：
  - 对篮子每个 symbol 取实时行情（price + change_pct），复用 `data_provider/crypto_base.py` + manager 已有 fallback（Binance→OKX→Coinbase，含本次刚加的超时/重试配置）。
  - 单 symbol 取不到时回退日线最后两根算涨跌幅；仍失败则该行标注不可用（不静默丢成 0）。
- `CRYPTO_PROFILE.has_market_stats=False` / `has_sector_rankings=False` 不变 → 自然不取 breadth/sectors。

### 2.3 边界
- 概览表只表达"市场盘面"。具体币（含新币）的深度分析走**现有 per-coin 流**（`main.py --stocks SUI/USDT`），不混入概览，避免大盘复盘膨胀成个股报告。

## 2b. 新币与上新动态（资讯叙事段）

- crypto 复盘报告新增一节 `## 新币与上新动态`，**纯 markdown 叙事**，**无结构化字段、无 payload schema 变更**（保持 presence-only 兼容约定）。
- 数据来自 `src/search_service.py` 追加的 crypto 新币查询集（与 `CRYPTO_PROFILE.news_queries` 区分用途），覆盖 IEO / launchpad / 新上线 / 交易所上新等，例如：
  `加密货币 新币 上线` / `IEO launchpad new listing` / `Binance OKX 上新` / `crypto new token launch`
- LLM 在 crypto prompt（见 §3 `CRYPTO_BLUEPRINT`）中被指示：基于上述新闻产出"新币与上新动态"小节，给出近期值得关注的新项目/上新与风险提示。
- **优雅降级**：取不到新币新闻时，该节简要说明"近期无显著上新资讯"，不报错、不阻断主流程。

## 3. 区域门控接线清单（touch-points）

| # | 层 | 锚点 | 改动 |
|---|---|---|---|
| 1 | 配置 enum + 报错文案 | `src/config.py:2277,2280` | 白名单加 `crypto`，报错文案同步 |
| 2 | 配置文档 | `.env.example`（crypto / market-review 段） | 记录 `MARKET_REVIEW_REGION=crypto` 可选值 + 新增 `CRYPTO_MARKET_REVIEW_SYMBOLS` |
| 3 | region tuple → valid set | `src/core/market_review.py:31-37` | 按现有 tuple 约定 `(market_code, config_key, display_label)` 加 crypto 行（market_code=`crypto`、display=`加密货币`、config_key 沿用现有命名风格），`_VALID_MARKET_REVIEW_REGIONS` 自动含 crypto |
| 4 | i18n 标题字典 | `src/core/market_review.py:48-66` | 加 crypto 标题（zh/en） |
| 5 | MarketAnalyzer 白名单 | `src/market_analyzer.py:138` | 放行 `crypto`（不再回退 cn） |
| 6 | get_profile | `src/core/market_profile.py:84-92` | 已返回 `CRYPTO_PROFILE`（无需改）；补 dataclass docstring `"cn"|"us"` 漂移 |
| 7 | 策略蓝图 | `src/core/market_strategy.py:166-172` | 新增 `CRYPTO_BLUEPRINT`（现回退 CN 蓝图，含"涨停/国企指数"，**内容错误**）；含 §2b 新币段指令、24/7、无涨跌停、计价币、高波动 regime |
| 8 | 市场范围/标题/单位/hint | `src/market_analyzer.py:152-202` | crypto 分支：计价币单位（USDT，非 CNY）、crypto 标题、en 路径 index hint |
| 9 | turnover 数值格式 | `src/market_analyzer.py:170-178` | crypto 量级处理（不套 A 股"亿元"口径） |
| 10 | market-light service region set | `src/core/market_light_service.py:20-28` | 放行 crypto（否则 `ValueError`）；但 crypto **不出 snapshot**（见 §4） |
| 11 | 调度有效 region | `src/core/trading_calendar.py:559-564` | `compute_effective_region` 含 crypto（24/7，始终 eligible） |
| 12 | 数据路由 | `data_provider` `get_main_indices`（`base.py:2163` / `yfinance_fetcher.py:320-331`） | 新增 crypto 篮子分支（见 §2.2） |
| 13 | 新币查询集 | `src/search_service.py` | 追加 crypto 新币/上新查询（见 §2b） |

说明：`get_open_markets_today()`（`trading_calendar.py:522-541`）已含 crypto，`is_market_open('crypto')=True`（`:150-151`），`MARKET_TIMEZONE['crypto']='UTC'`（`:46`）已就绪；仅 `compute_effective_region` 漏接（#11）。

## 4. MarketLightSnapshot：crypto 跳过

- crypto **不生成** `MarketLightSnapshot`（现权重 breadth 45% / index 35% / limit 20%，breadth+limit 对 crypto 结构性缺失）。
- payload 直接**不携带** `market_light` 字段（与现有 us/hk omit breadth 的 presence-only 约定一致）。不用缺失维度凑误导性 0-100 合成分。
- `market_light_service` 需放行 crypto 以免 `ValueError`，但在 crypto 分支显式跳过/返回 `available=False`，不进入合成评分。

## 5. Payload / 兼容性

- crypto 在嵌套 `markets` dict 中作为一个 region key，**omit** `breadth` / `sectors` / `market_light`（与 us/hk omit breadth 同款行为，已被 `tests/test_market_analyzer_generate_text.py:1344-1422` 验证）。
- `market_review_payload` `version=1` **不变**，不改动既有 region key 形状；新币叙事段在 markdown 正文内，不新增结构化字段。
- API payload 仍是 `Any`（`api/v1/schemas/analysis.py:270-277`），Web/Desktop 按 `region` 推断渲染。

## 6. Web 渲染（`apps/dsa-web` `MarketReviewReportView.tsx`）

- breadth 卡对 crypto **omit**（`:461-485`），而非渲染"暂无数据"——后者对 crypto 语义错误（无涨跌停、无涨跌家数）。
- turnover 单位 CNY → 计价币（`:480`）；section icon 关键词 `涨停|跌停`（`:80-98`）对 crypto 不渲染。
- 复用大盘复盘的 Markdown/GFM 渲染主体与个股模块隐藏逻辑，新币叙事段随正文自然渲染。

## 7. 调度（24/7）

- crypto 已 `is_market_open=True`、UTC、`get_open_markets_today` 含 crypto；仅 `compute_effective_region` 需放行。
- crypto opt-in，默认 `both`（=cn+hk+us）不触发 crypto；一旦显式选中（`MARKET_REVIEW_REGION=crypto` 或 `cn,crypto`），每个调度 tick 都 eligible（符合 24/7）。
- 单通知渠道失败不阻断主流程（沿用现有）。

## 8. 错误处理 / 稳定性护栏

- 篮子全失败：区分"未实现"（之前 crypto 分支缺失返回 `[]`）vs"今日取不到"，给诊断日志（AGENTS.md 数据源护栏）。
- **单 region 失败不拖垮其他 region**（沿用现有 per-region 隔离）；crypto 取数失败不影响同批 cn/hk/us。
- 新币叙事段无数据时优雅留空，不报错。
- 严禁用 broad fallback / 静默 `return []` 掩盖"crypto 分支未实现"。

## 9. 测试

后端（`tests/`）：
- crypto region 端到端复盘生成（篮子行情表存在、市场叙事段存在）。
- **crypto 不被静默回退成 cn**：`_parse_market_review_region('crypto')`、`MarketAnalyzer(region='crypto').region`、`get_market_strategy_blueprint('crypto')` 均落到 crypto，且蓝图内容非 CN（不含"涨停/国企指数"）。
- crypto payload omit `breadth`/`sectors`/`market_light`。
- `both` 仍 = cn+hk+us（不含 crypto）。
- `CRYPTO_MARKET_REVIEW_SYMBOLS` 覆盖/追加生效；非法币跳过 + 告警。
- `compute_effective_region` 含 crypto；`market_light_service` 放行 crypto 不 `ValueError` 且不出 snapshot。
- 新币叙事段：有新闻 → 出段；无新闻 → 优雅留空不报错。

Web（`apps/dsa-web`）：
- crypto payload（breadth undefined）渲染、breadth 卡 omit（非"暂无数据"）。
- 单位为计价币、无涨停/跌停 icon。

验证矩阵（按 AGENTS.md §6）：`./scripts/ci_gate.sh` + `pytest -m "not network"`；Web `npm run lint && npm run build`（注意工作区路径含空格，完整 build 走无空格路径或交 CI `web-gate`）。

## 10. 回滚

crypto 全程 opt-in 且 `both` 未变 → 回滚 = `git revert` region 接线提交，存量 cn/hk/us 复盘行为零影响（前提：`both` 未被悄悄扩成含 crypto）。

## 11. 后续子项目（拆出，单独设计）

**子项目2：新币自动发现 + 结构化上新行情表**
- 目标：从交易所免费上新/公告接口（Binance 上新公告、OKX/Coinbase listing endpoints、`exchangeInfo` onboardDate 等）**自动发现**近期新上币种，产出结构化"新上币种 + 行情"表并入复盘。
- 为何拆出：现有 crypto fetcher 仅 per-symbol candles/ticker，无任何上新/公告发现能力；多交易所发现可靠性、去重、schema、缓存、渲染、稳定性都是新设计面，属新数据能力，工作量与风险显著高于本期。
- 触发：本期上线后另起 brainstorm → spec → plan。
