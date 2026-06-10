# crypto 永续合约指标 全链透出 设计（Phase 2：surface to report/API/Web）

> 阶段二「合约/永续维度」的**第二个子项目**（A 全链透出）。Phase 1（`feat/crypto-derivatives`）已抓取并把 `crypto_contracts`（资金费率/标记价/未平仓量）注入 LLM 分析 prompt，但该数据**只到达 prompt，之后被丢弃**——不进 API `ReportDetails`、Web 看不到。本子项目把已抓取的 `crypto_contracts` 沿 schema→API→Web **presence-only、additive** 地透出，让用户在报告里看到永续指标。补完 Phase 1 数据的"最后一公里"。

**Goal:** 单标的 crypto 分析报告中，把 Phase 1 已注入 `context["crypto_contracts"]` 的永续指标（funding_rate / mark_price / open_interest / open_interest_usd / source）透出到 API `ReportDetails` 与 Web 报告卡片，全程 presence-only、stock/hk/us 零影响、无新配置项。

**范围决策（已确认）：**
- 仅单标的**分析报告**透出（market-review 聚合永续情绪是子项目 B，不在本期）。
- **持久化随报告载荷**：不新增 DB 列、不动表结构、不做 `funding_rate > X` 查询；数据已随 `context_snapshot` 持久化于现有快照。
- **方案①**：从 `context_snapshot` 抽取，镜像 `extract_fundamental_detail_fields` / `extract_board_detail_fields` 既有范式；**不**新增 `AnalysisResult` 字段，**不**改 `UnifiedRealtimeQuote`。
- Web 用**独立** `ReportCryptoMetrics.tsx` 卡片（不塞进 `ReportOverview.tsx`）。
- 无新开关：上游由 `crypto_derivatives_enabled`（Phase 1）门控；关闭则快照无 `crypto_contracts` 键，自动不透出（不叠加互斥开关）。

---

## 1. 背景与查证（直接读码确认，2026-06-10）

- `crypto_contracts` 由 `src/core/pipeline.py` 在 analyze 前经 `attach_crypto_contracts(enhanced_context, self.config)` 注入到 `enhanced_context["crypto_contracts"]`。
- **快照形状（关键，已读码核实）**：`_build_context_snapshot`（`pipeline.py:1860`）建的快照是**嵌套**结构 `{"enhanced_context": self._without_runtime_prompt_context(enhanced_context), "realtime_quote_raw": ..., ...}`。故 `crypto_contracts` 实际位于 **`context_snapshot["enhanced_context"]["crypto_contracts"]`，不在顶层**。
- **数据存活（已读码核实）**：`_without_runtime_prompt_context`（`pipeline.py:2074`）只 `pop` 掉 `market_phase_context` / `portfolio_context` / `analysis_context_pack` / `analysis_context_pack_summary` —— **不动 `crypto_contracts`**，故其完整随快照持久化。
- **读取链（已读码核实）**：三个 `ReportDetails(...)` 构造点拿到的 `context_snapshot` 全部来自**已持久化记录**（非内存 `enhanced_context`），且经 `parse_json_field` 反序列化（DB 存的是 JSON 字符串）：
  - `api/v1/endpoints/analysis.py:1184`（`_build_analysis_report`）：sync/async 调用方（470/800）经 `_load_sync_fundamental_sources` → `db.get_analysis_history(...).context_snapshot` → `parse_json_field` 读出。
  - `api/v1/endpoints/analysis.py:965`（task status 完成态）：`context_snapshot` 取自存储记录。
  - `api/v1/endpoints/history.py:445`（历史详情重载）：`result.get("context_snapshot")`。
  - ⇒ 三处读的是**同一份嵌套快照**，故只需一个抽取器 + 一处 schema 字段覆盖全部入口。
  - **依赖前提**：上述读取依赖快照已被持久化，即 `save_context_snapshot=True`（`config.py:878` 默认 True）。`SAVE_CONTEXT_SNAPSHOT=false` 时记录无快照 → 连 sync 实时响应也不透出。此为 fundamentals/boards 同款既有依赖（它们从同一份快照抽取），非本期新增回归。
- `ReportDetails`（`api/v1/schemas/history.py:244`）现有结构化块字段约定为 `Optional[Any]`（`financial_report` / `dividend_metrics` / `belong_boards` / `sector_rankings`）。
- 抽取器集中在 `src/utils/data_processing.py`：`extract_fundamental_context:143`（范本：`parse_json_field` → `snapshot["enhanced_context"][...]` + 顶层 fallback）/ `extract_realtime_detail_fields:171` / `extract_fundamental_detail_fields:209` / `extract_board_detail_fields:236`；`_non_empty_dict`(:228 邻近) 可复用。
- Web：`apps/dsa-web/src/api/analysis.ts` 对报告调 `toCamelCase<AnalysisReport>(...)`；`toCamelCase` = `camelcaseKeys(data, { deep: true })`（`apps/dsa-web/src/api/utils.ts`），**深度递归**——`crypto_contracts.funding_rate` → `cryptoContracts.fundingRate` 等内层键自动转换，无需手工映射。
- Web `ReportDetails` 接口在 `apps/dsa-web/src/types/analysis.ts:268`，camelCase；既有 typed 块（`financialReport`/`belongBoards`/`sectorRankings`）由上述深转换得到。

`crypto_contracts` 字段集（Phase 1 `fetch_perp_metrics` 产出，均 presence-only）：`funding_rate`(float, 比率非百分比) / `mark_price`(float) / `open_interest`(float, 张) / `open_interest_usd`(float) / `source`(str="okx")。

## 2. 架构与组件（全 additive）

分层：API 抽取/拼装在 `api/` + `src/utils/`；展示在 `apps/dsa-web/`。无 `data_provider/` 改动（数据已存在于快照）。

### 2.1 `src/utils/data_processing.py`（新增抽取器）

```python
def extract_crypto_contracts_detail_fields(context_snapshot: Any) -> Dict[str, Any]:
    """从 context_snapshot 抽取 crypto 永续合约指标（presence-only）。
    返回 {"crypto_contracts": dict|None}：仅当快照含非空 dict 才有值。"""
    snapshot_obj = parse_json_field(context_snapshot)
    contracts = None
    if isinstance(snapshot_obj, dict):
        enhanced = snapshot_obj.get("enhanced_context")
        if isinstance(enhanced, dict):
            contracts = _non_empty_dict(enhanced.get("crypto_contracts"))
        if contracts is None:                      # 防御性 dual-shape，对齐 extract_fundamental_context
            contracts = _non_empty_dict(snapshot_obj.get("crypto_contracts"))
    return {"crypto_contracts": contracts}
```

- **必须 `parse_json_field`**（DB 存的是 JSON 字符串；对已是 dict 的入参幂等）。
- **必须走嵌套** `snapshot["enhanced_context"]["crypto_contracts"]`（见 §1 快照形状）；直接读顶层会**永远 None**。
- 复用同文件 `_non_empty_dict`（空 dict / 非 dict → None），不自造判空。
- 顶层 fallback 为防御性，对齐 `extract_fundamental_context` 既有写法（兼容未来可能的扁平快照）。
- 不做字段改写/补零；原样透传 Phase 1 的 presence-only dict。

### 2.2 `api/v1/schemas/history.py::ReportDetails`（新增字段）

```python
crypto_contracts: Optional[Any] = Field(None, description="加密永续合约指标（presence-only：资金费率/标记价/未平仓量/来源）")
```

与邻居 `financial_report` 等 `Optional[Any]` 约定一致；缺省 `None`，对存量 stock 报告零影响。

### 2.3 三个 `ReportDetails(...)` 构造点接线（一致性契约）

在 1184 / 965 / 445 三处，各自：

```python
extracted_contracts = extract_crypto_contracts_detail_fields(context_snapshot)  # 各处变量名用对应的 context_snapshot 源
details = ReportDetails(
    ...,                                    # 既有字段不变
    crypto_contracts=extracted_contracts.get("crypto_contracts"),
)
```

- `analysis.py:1184` 与 `:965` 处的 `details = None` 守卫条件（1183 / 964）追加 `or extracted_contracts.get("crypto_contracts") is not None`，保证即使（理论上）仅有合约数据也能构建 details。实务上 `context_snapshot is not None` 已使守卫通过，此追加表达意图、防御未来重排。
- `history.py:445` 处无 `details=None` 守卫（无条件构建），仅加字段。
- **三处必须同步**：单标的分析有实时（同步/异步 task）与历史重载两类入口；漏接历史路径会导致"刚分析能看到、重载历史看不到"的契约漂移（违反 AGENTS.md §8.1）。

### 2.4 Web 类型（`apps/dsa-web/src/types/analysis.ts`）

```typescript
export interface CryptoContracts {
  fundingRate?: number;       // 比率（非百分比）；展示时 *100
  markPrice?: number;
  openInterest?: number;      // 张
  openInterestUsd?: number;
  source?: string;            // 'okx'
}
// ReportDetails 接口追加：
//   cryptoContracts?: CryptoContracts;
```

### 2.5 Web 渲染（新增 `apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx`）

- 入参 `details?.cryptoContracts`；**presence-only**：逐字段存在才渲染对应行；全缺 → 返回 `null`（整卡不出现）。
- 字段镜像 prompt 块语义：
  - 资金费率：`(fundingRate * 100).toFixed(4) + '%'`，标注"正=多头付费 / 负=空头付费（约 8h 结算）"。
  - 标记价：原值展示，保留有效精度（小币不截断）。
  - 未平仓量：`openInterest 张 / $openInterestUsd`（`toLocaleString` 千分位）。
  - 来源：`source`（如 OKX）。
- **守卫即 presence-only**：`cryptoContracts` 缺省即不渲染；stock/hk/us 报告永不携带该字段，故无需额外 region 判定（presence 守卫已等价于 crypto 守卫）。
- **挂载点（已核实组合根）**：`apps/dsa-web/src/components/report/ReportSummary.tsx` —— 它在 60/69/72/88 行依次渲染 `ReportOverview` / `ReportStrategy` / `ReportNews` / `ReportDetails`，且 `details` 已在作用域内。新卡片作为独立同级组件插入（建议置于 `ReportStrategy` 之后、`ReportNews` 之前），`details={details}`。`index.ts` 导出。
  - 注：`ReportDetails.tsx` 是 JSON 透明度/追溯区（raw + snapshot 折叠），**非**结构化卡片区；结构化块（boards/sector）在 `ReportOverview.tsx`。本卡片走独立组件、挂在 `ReportSummary`，不混入这两者。
- 文案：中英双语沿用现有报告组件的 locale 约定（如组件内常量或既有 i18n 入口）。

## 3. 数据流

```
Phase 1: pipeline 注入 enhanced_context["crypto_contracts"] → 快照为 context_snapshot（已持久化）
本期:
  API _build_analysis_report / task-status / history-detail
    → extract_crypto_contracts_detail_fields(context_snapshot)
    → ReportDetails.crypto_contracts（presence-only）
  → AnalysisReport JSON（snake_case）
  → Web toCamelCase(deep) → details.cryptoContracts.{fundingRate,...}
  → ReportCryptoMetrics 卡片（presence-only 渲染）
```

## 4. 错误处理 / 兼容

- presence-only 全程：无 `crypto_contracts` → 字段 `None`/`undefined` → 卡片不渲染；stock/hk/us 与无永续数据的 crypto 报告零变化。
- 存量历史（Phase 1 之前的分析）快照无 `crypto_contracts` → 自动不透出，无需迁移。
- `Optional[Any]` 字段对既有 API 消费者纯追加，不破坏现有客户端（桌面端读同一 schema：新增可选字段，向后兼容）。
- 无新配置项；`CRYPTO_DERIVATIVES_ENABLED=false` 时上游不注入 → 链路自然空。
- **依赖 `save_context_snapshot=True`（默认开）**：三入口均从持久化快照读取，故 `SAVE_CONTEXT_SNAPSHOT=false` 时不透出（含 sync 实时响应）。与 fundamentals/boards 同款既有依赖，非新增回归；不为本特性单独改写持久化策略。

## 5. 测试（离线，无网络、无 LLM）

> 测试桩必须用**真实嵌套形状** `{"enhanced_context": {"crypto_contracts": {...}}}`（并覆盖 JSON 字符串入参），否则会与"读顶层"的错误实现一起自洽地错。

| 层 | 用例 |
|---|---|
| utils 抽取 | `extract_crypto_contracts_detail_fields`：嵌套 `{"enhanced_context":{"crypto_contracts":{...}}}`→返回该 dict；JSON 字符串入参→正确解析；无 `enhanced_context`/空 dict/非 dict→None；（防御）顶层扁平 `{"crypto_contracts":{...}}`→返回 |
| API 透出 | 给嵌套形状 `context_snapshot`，经 `_build_analysis_report`（及/或 history 重载路径）→ `ReportDetails.crypto_contracts` 命中；缺失→None。覆盖至少 `_build_analysis_report` + 一个重载路径 |
| Web 渲染 | vitest：`cryptoContracts` 齐全→渲染资金费率(%)/标记价/OI；逐字段缺省省略；全缺→`null` 不渲染；stock 报告（无字段）不渲染 |

## 6. 文档

- `docs/crypto-guide.md` 永续节补"报告透出（API `ReportDetails.crypto_contracts` + Web 卡片）"小节。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平追加：`- [新功能] crypto 永续合约指标透出到分析报告 API/Web（presence-only，additive）`。
- 评估中英双语文档同步（`crypto-guide` 若有 EN 版需同步或在交付说明注明原因）。

## 7. 分支与回滚

- 分支：`feat/crypto-contracts-report`，叠在 `feat/crypto-derivatives` 之上；栈：`main ← market-review ← new-listings ← market-indicators ← backtest ← derivatives ← contracts-report`。
- 回滚：纯新增（1 抽取器 + 1 schema 字段 + 3 接点 + Web 类型/组件/测试）；`git revert` 或丢弃分支即恢复；运行时 `CRYPTO_DERIVATIVES_ENABLED=false` 即停止透出。

## 8. 范围边界（YAGNI / 留后续子项目）

不做：market-review 聚合永续情绪（子项目 B）、独立 perp 符号（C）、perp klines（D）、perp 回测（E）、Binance fapi fallback（F，本环境 451）、DB 索引列与按资金费率查询、`UnifiedRealtimeQuote` 字段提升、资金费率历史/趋势图。本期仅把已抓取的 `crypto_contracts` presence-only 透出到单标的报告 API/Web。
