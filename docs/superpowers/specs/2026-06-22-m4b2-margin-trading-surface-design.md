# M4-B-2 融资融券呈现 设计

- 日期：2026-06-22
- 状态：设计已评审通过，待写实现计划（writing-plans）
- 基线分支：`feat/m4b-capital-flow`（**堆叠在 M4-B-1 之上**，head `3ea63b28`；M4-B-1 资金面/龙虎榜呈现已在本分支落地）
- 关联：M4 数据面拆分的第三片。M4-B-1（capital_flow/龙虎榜呈现）为前置；本片为 M4-B-2，**仅做融资融券**。
- 接地证据：设计前用并行工作流对照本 worktree 真实代码逐点坐实（adapter / base.py 枚举 / analyzer+pipeline / notification 两渲染路径 / schema+config+tests），并经三路对抗性审查（契约漂移 / fail-open+门控 / 测试+文档）查漏后收敛。下文锁定项已吸收全部 blocker/major 修正。

---

## 1. 背景与目标

M4-B-1 已把**主力资金流 + 龙虎榜**确定性补进报告（`CapitalFlow` section、两条 notification 渲染路径、双语标签），并保持「主力资金流喂 LLM prompt + 驱动 post-LLM 降级」的既有决策语义。M4-B-2 在同一 worktree 上**追加融资融券（margin / RZRQ）呈现**，与 M4-B-1 同构但有一处关键分歧（见 D4）。

可行性结论（live 探测得出，写入背景以免返工）：

- **北向 / HSGT 个股资金已被交易所于 2024 年停更**：2026 年 `stock_hsgt_hist_em("北向资金")` 近段全 NaN、个股级接口报错，无任何免费/付费源可复活。**故 M4-B-2 放弃北向**，只做融资融券。
- **融资融券经免费 akshare 完整可得**：`stock_margin_detail_sse(date=YYYYMMDD)`（沪，约 1980 行，代码列「标的证券代码」）、`stock_margin_detail_szse(date=YYYYMMDD)`（深，约 2073 行，代码列「证券代码」）按日返回**全市场明细**；未发布/非交易日返回**空 DataFrame**（快、无异常）；单次抓取约 1.4s，落在默认 3s 超时内。

M4-B-2 目标（最小、additive、A股-gated、presence-only）：

1. 新增 `MarginTrading` 报告 section，把个股**融资余额 / 融资买入额 / 融券余量 + 交易日 + 交易所**确定性补进报告，用户在报告里看得到。
2. 与 M4-B-1 同构：确定性后处理填充、presence-only、fail-open、两条 pipeline 路径 + 两条 notification 渲染路径 + 双语标签全覆盖。
3. **D4 关键分歧**：融资融券**仅呈现、不喂 LLM prompt、不进 `decision_stability`、不产生任何 bias**——对决策完全只读（capital_flow 是喂 prompt 且驱动降级的，这是 reviewer 必查的契约差异）。

## 2. 非目标

- **北向 / HSGT**：数据源 2024 停更，明确放弃（写入 `docs/margin-trading.md` 局限 + 同步订正 `docs/capital-flow.md:103`）。
- **融资融券喂 LLM / 进决策 / 产 bias / 写 decision_stability**：明确不做（D4）。**不新增 `_margin_prompt_line`、不接 `_format_prompt`、不接 `stabilize_decision_with_structure`、不加 `_margin_availability_status` 之类决策访问器**——接地时一度提出的 prompt 行属内部矛盾，本片整体删除。
- **融资融券趋势 / 多日序列 / 融资融券余额占比衍生指标**：仅最新交易日快照，presence-only，不算趋势、不设阈值。
- **新数据源 / 新 token / 新配置开关**：不引入（复用 akshare `fundamental_adapter`，tokenless；复用 `fundamental_fetch_timeout_seconds` + 既有 A股/非 ETF 门控）。
- **融资融券专用 Web React 组件**：不新增；同 capital_flow/chip/volume，经 notification markdown + 报告 payload 呈现。
- **精确交易日历**：不引入；用有界回退 ≤3（见 D5、§7），并把实际命中日 `trade_date` 显式呈现以暴露陈旧度。

## 3. 关键决策（已锁定）

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | 范围 | 仅融资融券；北向数据源停更，放弃 |
| D2 | 代码基线 | 堆叠在 M4-B-1（`feat/m4b-capital-flow`）之上，可随 M4-B 整体合入 main |
| D3 | 报告填充 | **确定性后处理填充**（镜像 `fill_capital_flow_if_needed`），非 LLM 生成（融资数字不可被幻觉） |
| **D4** | **决策影响** | **零**——仅呈现，不喂 prompt、不进 decision_stability、不产 bias、对决策只读（**与 capital_flow 的关键分歧**） |
| D5 | 最新交易日 | smart-start（最近工作日）+ **有界回退 ≤3 个交易日候选**取首个非空；非精确日历；命中日 `trade_date` 显式呈现 |
| D6 | 配置 | 零新增配置/数据源/token（复用 `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS` + A股/非 ETF/非北交所门控） |
| D7 | 字段词表 | **全链冻结单一词表**：`financing_balance / financing_buy / short_volume / trade_date / exchange`（贯穿 model / builder / 两渲染路径 / 标签 / 测试） |
| D8 | 键名映射 | `fundamental_context` 块键 = `margin`；dashboard/schema 字段 = `margin_trading`（**故意区分**，builder 内显式映射并文档化，禁止静默统一） |

## 4. 架构与模块边界

全部 additive、A股-gated、presence-only、fail-open。

### 4.1 抓取层（`data_provider/fundamental_adapter.py`）

新增 `get_margin_detail(stock_code, deadline=None) -> Dict[str, Any]`，**复用既有工具**（不造平行实现）：

- `_normalize_code` 取纯 6 位码 →**交易所路由**：`6xx`（600/601/603/605/688）→ SSE / `stock_margin_detail_sse`；`0xx/3xx` → SZSE / `stock_margin_detail_szse`。
- **显式门控（关键修正）**：`is_bse_code(code)`（北交所 8xx/4xx/92xxxx）或 `_is_etf_code(code)` → 直接返回 `status="not_supported"`，**不落到 SSE/SZSE 端点**（不依赖 SZSE 对北交所码静默返空——`base.py` 的 `get_*_context` 门控只排除非 cn + ETF，**不排除北交所**，故必须在 adapter 内补这道闸）。
- 调用经 `_call_df_candidates([(fn_name, {"date": YYYYMMDD})])`（沿用 Series→frame、错误收集、fail-open 三元返回）。
- **最新交易日 + 有界回退（D5）**：smart-start = 最近工作日（周末回退到周五）；最多 ≤3 个候选日（按工作日回退、跳周末，**非精确交易日历**，节假日不建模），逐个调用取**首个非空**。**回退受 deadline 约束**：每次 akshare 调用前检查 `time.monotonic()` 是否超过 deadline，超则停止（返回已得或 not_supported），避免 3 次串行全市场下载吃爆预算。
- **模块级 per-date memo（接地确认本文件原无此模式，新增）**：`_margin_detail_memo`（`OrderedDict[(exchange, "YYYYMMDD"), pd.DataFrame]`），模块级 `threading.Lock` 守护**读+写+淘汰**（不在持锁期间做网络抓取——冷缓存极少量重复在途抓取可接受并文档化）；**仅缓存非空 DataFrame**（不把早盘空/预览快照钉死一整天）；**LRU 上限 ≤4**（满则 `popitem(last=False)` 淘汰最旧；显式淘汰，不靠"约保留 2 个"的口头约定——本进程是长驻 systemd `dsa-daily`，全市场 DataFrame 无界增长是真实内存泄漏）。
- 取行：`_extract_latest_row(df, stock_code)`（沿用代码列归一过滤）。取字段：`_pick_by_keywords` + `_safe_float/_safe_str` 取 `融资余额→financing_balance`、`融资买入额→financing_buy`、`融券余量→short_volume`。
- **`trade_date` 必填（关键修正）**：成功时把**实际命中**的 `YYYYMMDD` 写入 `trade_date`（即已传入的 date 参数，无额外调用）；`exchange` 写 `SSE/SZSE`。
- 返回 dict：`{status, financing_balance, financing_buy, short_volume, trade_date, exchange, source_chain, errors}`；`status="not_supported"` 默认，命中且 `trade_date == smart-start 当日` → `ok`，命中但来自更旧回退日 → **`partial`**（陈旧标志），空/异常 → `not_supported`/`failed`。

### 4.2 上下文块（`data_provider/base.py`，新增 `get_margin_context`）

镜像 `get_capital_flow_context`（§5 模板）：cn-gate + 非 ETF + `timeout<=0` 闸 + `_run_with_retry(lambda: self._fundamental_adapter.get_margin_detail(stock_code), timeout, "margin")` + payload 非 dict→`failed` 块 + `_build_fundamental_block(status, data, source_chain, errors)`。

- **adapter 方法名对齐（修正）**：lambda 调用 `get_margin_detail`（非 `get_margin`），否则 `AttributeError` 被 fail-open 吞成永久 `failed` 空块。
- **独立预算切片（修正）**：在 `get_fundamental_context` 内于 `institution` 之后、`capital_flow` 之前给 margin 自己的 `margin_budget = min(fetch_timeout, remaining)` 并 `_consume_budget`，把该 budget 作为 deadline 传给 `get_margin_detail`，**不蚕食** capital_flow/dragon_tiger 预算。

**枚举完整性（blocker 级，all-or-nothing）**——`margin` 必须接入 `base.py` **全部**硬编码块枚举点，漏任一处会在 `result_ctx['<block>']` **下标**访问处抛 `KeyError`，**而该 KeyError 发生在 per-block fail-open 守卫之外、`get_fundamental_context` 内部，会 abort 整个个股 fundamental 构建**，直接违反「单一辅助源失败不拖垮主流程」。接地坐实的站点：

1. `_should_cache_fundamental_context` 块元组（约 2580-2588）
2. `_build_market_not_supported` blocks dict（约 2595-2638）—— HK/US 路径工厂，漏则离岸路径无 `margin` 键
3. `_build_offshore_fundamental_context`：`result_ctx` 初始化（约 2687）、not_supported 循环（约 2783）、`block_statuses`（约 2793-2800）、错误链循环（约 2803）
4. `build_failed_fundamental_context` `block_names`（约 2828-2836）
5. `get_fundamental_context`（CN）：`result_ctx` 初始化（约 2902）、ETF 分支 + 普通分支赋值（约 3073-3113，**ETF 分支手赋三块最易漏**）、`block_statuses`（约 3115-3123）、错误链循环（约 3125-3133）

**防御性加固（修正）**：margin **自身**的引用在 `block_statuses` / 错误链循环里用 `result_ctx.get("margin", {})`（不改动其它块的既有下标语义，最小面），使"部分实现"也不致 KeyError-abort 整个上下文。

### 4.3 确定性后处理填充（`src/analyzer.py`，镜像 `fill_capital_flow_if_needed`）

- 新增 `_build_margin_from_context(fundamental_context, language="zh") -> Optional[Dict[str, Any]]`：presence-only——读 `fundamental_context["margin"]["data"]`（与 capital_flow 同样 `['<block>']['data']` 嵌套），块 `status` ∈ {ok, partial} 才构建，否则返回 `None`（section 不出现）。输出 dict 键 = **冻结词表**（D7）：`financing_balance / financing_buy / short_volume / trade_date / exchange`。**返回纯 dict，绝不返回 tuple/bias**。
- 新增 `fill_margin_if_needed(result, fundamental_context) -> None`：try/except 包裹、fail-open 静默跳过；`_build_margin_from_context` 为 None → return；否则**写 `dashboard["data_perspective"]["margin_trading"]`（注意 D8：写 `margin_trading`，不是 `margin`）**。日志标 `[margin]`。**不触碰 `decision_stability`、不调用任何 prompt 构建、不传入 `stabilize_decision_with_structure`**。

### 4.4 pipeline 接线（`src/core/pipeline.py`，两条路径）

- **导入（修正）**：把 `fill_margin_if_needed` 加进既有 `from src.analyzer import (...)` 块（约 30-38 行，与 `fill_capital_flow_if_needed` 并列），否则两路径 `NameError`。
- 传统路径（约 626，Step 7.6b 之后新增 Step 7.6c）+ agent 路径（`_analyze_with_agent`，约 1145，`fill_capital_flow_if_needed` 之后）各加：
  ```python
  if result:
      fill_margin_if_needed(result, fundamental_context)
  ```
- 序列：chip → capital_flow → **margin** → price_position → stabilize（margin **不**传入 `stabilize_decision_with_structure`）。两路径保持对称（agent 路径注释标「与传统 Step 7.6c 同序」）。

### 4.5 呈现（两条渲染路径 + 标签，全锁步）

**两条 disjoint 渲染路径都要加 margin 块**（接地确认 capital_flow 的 j2 路径当前无测试覆盖，是真实盲区）：

- **Python 传统路径**（`src/notification.py` `generate_dashboard_report` **实例方法**，约 1250-1275 capital_flow 块之后）：`margin_data = data_persp.get("margin_trading", {})`；`if margin_data:` 守卫；**用内联三元** `'EN' if report_language == 'en' else '中文'`（该方法**不**消费 `report_language.py` 标签 dict）渲染：
  - zh：`融资融券：融资余额 {financing_balance|N/A} | 融资买入 {financing_buy|N/A} | 融券余量 {short_volume|N/A}（{沪/深} {trade_date}）`
  - en：`Margin Trading: Financing Balance {…} | Financing Buy {…} | Short Volume {…} ({SSE/SZSE} {trade_date})`
- **Jinja2 路径**（`templates/report_markdown.j2`，约 110-116 capital_flow 块之后）：`{% set margin_data = data_persp.get('margin_trading', {}) %}` + `{% if margin_data %}` + 用 `labels.*` + `{{ 'N/A' if margin_data.get('financing_balance') is none else … }}`（Jinja2 用 `is none`）。
- **标签**（`src/report_language.py` `_REPORT_LABELS`，zh ~257-260 / en ~369-372）：**仅供 j2 路径**。新增 zh：`margin_label=融资融券, financing_balance_label=融资余额, financing_buy_label=融资买入, short_volume_label=融券余量`；en：`margin_label=Margin Trading, financing_balance_label=Financing Balance, financing_buy_label=Financing Buy, short_volume_label=Short Volume`。
- **锁步纪律（修正）**：Python 路径靠内联三元、j2 靠 labels——两路径独立，**测试必须各覆盖一条**，防止改一条漏另一条；`trade_date`/`exchange` 在两路径都贴在余额旁显示（暴露陈旧度）。

### 4.6 报告 schema（`src/schemas/report_schema.py`）

```python
class MarginTrading(BaseModel):
    """融资融券（A股；最新交易日快照，presence-only）。"""
    financing_balance: Optional[Union[int, float, str]] = None  # 融资余额（元）
    financing_buy: Optional[Union[int, float, str]] = None      # 融资买入额（元）
    short_volume: Optional[Union[int, float, str]] = None       # 融券余量（股）
    trade_date: Optional[str] = None                            # YYYYMMDD（实际命中日）
    exchange: Optional[str] = None                              # SSE / SZSE
```

`DataPerspective` 追加：`margin_trading: Optional[MarginTrading] = None`（可选、默认 None，旧 payload 无该键仍解析）。**字段名严格等于 D7 冻结词表 + builder 输出 + 两渲染路径 getter + 测试断言**。

### 4.7 关键复用 / 不动

复用 `fundamental_adapter`（`_call_df_candidates/_normalize_code/_extract_latest_row/_pick_by_keywords/_safe_*`）、`is_bse_code/_is_etf_code/_market_tag`、`_build_fundamental_block`、`_run_with_retry`、`fundamental_fetch_timeout_seconds`、capital_flow 填充/渲染模式。**不动**：capital_flow/龙虎榜（M4-B-1）、主力资金流 prompt 量级表与 post-LLM 降级、`_capital_flow_bias_with_status`、`decision_stability` 结构、换手率/量比/筹码 既有 section。

## 5. 数据流

pipeline 取 `fundamental_context`（`get_fundamental_context` 内新增 margin 块抓取，A股 gate + 独立预算切片）→ **margin 不进 LLM prompt** → LLM 分析 → 后处理：`fill_margin_if_needed` 确定性填 `DataPerspective.margin_trading`（presence-only）→ 报告序列化 → 两条 notification 渲染路径各渲染融资融券块（含 trade_date/exchange）/ API 载荷携带 section。`decision_stability` / prompt / bias **全程不参与**（D4）。

模板方法对照（`get_capital_flow_context`）：
```python
def get_margin_context(self, stock_code, budget_seconds=None) -> Dict[str, Any]:
    """融资融券块（fail-open）。"""
    # cn-gate + 非ETF + timeout<=0 闸（镜像 get_capital_flow_context）
    payload, err, cost_ms = self._run_with_retry(
        lambda: self._fundamental_adapter.get_margin_detail(stock_code),  # 名字对齐
        timeout, "margin")
    if not isinstance(payload, dict):
        return self._build_fundamental_block("failed", {}, [...], [err or "margin failed"])
    # status: 有 financing_balance/short_volume 任一 → ok/partial（按 adapter status）；否则 not_supported
    return self._build_fundamental_block(status, {…冻结词表字段…},
        self._normalize_source_chain(payload.get("source_chain", []), "margin", status, cost_ms),
        list(payload.get("errors", [])) + ([err] if err else []))
```

## 6. 字段契约

- `MarginTrading`：见 §4.6，全 Optional/presence-only/None-tolerant。
- **键名映射（D8）**：`fundamental_context["margin"]`（块键）↔ `dashboard.data_perspective.margin_trading`（schema/渲染键）——builder 内显式映射，禁静默统一。
- `exchange` 取值 `SSE/SZSE`；渲染层 zh 映射 `沪/深`、en 保留 `SSE/SZSE`。
- `trade_date`：`YYYYMMDD`，**成功必填**=实际命中日；命中更旧回退日 → 块 `status=partial`。
- 非 A股/港股/美股/crypto/ETF/北交所：margin = `not_supported` → `DataPerspective.margin_trading` = None、两渲染路径不渲染融资融券块。
- `decision_stability` / LLM prompt：**不新增任何 margin 痕迹**。

## 7. 错误处理与边界

- **fail-open（护栏）**：`get_margin_detail` 不抛异常（`_call_df_candidates` 收错误链）；`get_margin_context` payload 非 dict → `failed` 块；`fill_margin_if_needed` try/except 静默跳过。margin 失败**不阻断主流程**。
- **陈旧度（修正）**：有界回退 ≤3 取首个非空，**非精确日历**，长假/晚发布可能命中数日前快照；故 `trade_date` 必填 + 更旧日 → `partial` + 两渲染路径显示日期，**陈旧不被静默当作当日**（capital_flow 不需此因其龙虎榜是 20 日窗内二值标志；margin 是绝对余额，陈旧值会误读为现值）。
- **空 DataFrame**：非交易/未发布日 akshare 返空（非异常），`_call_df_candidates` 的 `not df.empty` 使空结果**不记错误**→ status `not_supported`、section 不出现（presence-only 可接受，但文档注明此掩盖与真实不可用同形）。
- **预算（修正）**：≤3 串行全市场下载受 deadline 约束；margin 独立预算切片，不蚕食 capital_flow/dragon_tiger，避免 `_run_with_timeout` worker 池被慢调用拖累后续 fundamental 抓取。
- **memo（修正）**：`threading.Lock` 守护读/写/淘汰；仅缓存非空；LRU ≤4 显式淘汰；按 `(exchange, date)` 键 + 全市场 df + 逐股 `_extract_latest_row` → **无跨股泄漏**；冷缓存极少量重复在途抓取可接受并文档化。
- 不新增 fail-fast、不静默掩盖契约（status 如实反映 section 是否出现 + 字段是否 None）。

## 8. 兼容性

- 报告 schema：`DataPerspective.margin_trading` 追加可选（默认 None）→ 旧消费方忽略缺失字段，不破坏 payload 契约。
- **capital_flow（M4-B-1）/ 主力资金流 prompt / post-LLM 降级 / `decision_stability` / 换手率/量比/筹码 零改动** → 现有决策与呈现行为不变（回归锁定见 §9）。
- 前端无代码改动；报告 payload 新增可选字段，前端忽略未知字段。
- 零新配置/数据源/token；`.env.example` **不改**（复用 `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS`，与 M4-B-1 一致，交付说明显式声明以预答 AGENTS.md「新增配置须同步 .env.example」检查）。

## 9. 测试矩阵（pytest，离线确定性，mock fundamental_context / spy adapter）

新增 `tests/test_margin_surface.py`（分析+渲染层）+ adapter memo 用例（adapter 层）：

1. **冻结词表（D7）**：`_build_margin_from_context` 返回键 = `{financing_balance, financing_buy, short_volume, trade_date, exchange}` 用**子集断言** `required <= set(mt)`（**不**用脆的 `set(mt)=={...}`，接地确认 `test_capital_flow_surface.py:131` 该模式会因 additive 字段而破）。
2. **两渲染路径**：(a) 传统 `report_renderer_enabled=False` → 输出含「融资融券」+ 融资余额值；(b) Jinja2 `report_renderer_enabled=True`（或直接 `report_renderer.render('markdown', ...)`）→ 输出含融资融券块——**这是唯一真正触达 `labels.margin_label` 等四个新标签的用例**，漏则标签零覆盖、生产 `UndefinedError`。
3. **双语渲染级**：j2 路径 `report_language='en'` → 含 `Margin Trading/Financing Balance/Short Volume`；zh → 含 `融资融券/融资余额/融券余量`（builder 级语言断言不够，标签盲区在渲染级）。
4. **D4 决策只读（负契约，双测）**：(a) 填入饱和 margin 后 `fill_margin_if_needed` → `stabilize_decision_with_structure`，断言 `decision_type`/`operation_advice` 字节不变、`dashboard['decision_stability']` 仍为 None、无 `margin*` 键；(b) 构造饱和 margin 块跑 prompt 构建路径，断言生成的 LLM prompt **不含**任何融资融券标题/数值（镜像 `test_dragon_tiger_prompt_line_*` 但断言**缺席**）。
5. **not_supported 门控**：HK/US/crypto/ETF/北交所(8x/4x) → `get_margin_context`（或 builder）返回 not_supported/None、fill 后 `margin_trading` 缺席（镜像 `test_fill_not_supported_leaves_capital_flow_absent`）。
6. **fail-open（三独立测，非合一）**：(a) margin 块 status=not_supported → builder None；(b) **空 DataFrame** → status not_supported/无 margin、不崩；(c) **adapter 抛异常**（akshare import 失败/API error）→ `fill_margin_if_needed` 吞掉、不抛、无决策影响、`margin_trading` 缺席。
7. **base.py 枚举完整性**：CN 个股 / A股 ETF / 离岸(HK/US) 跑 `get_fundamental_context` + `_build_market_not_supported` + `build_failed_fundamental_context` 后，断言 `'margin' in result_ctx` 且 `coverage['margin']` 存在、ETF/离岸为 `not_supported`（证明门控、非空言）。
8. **memo（adapter 层）**：跨 >2 个日期 × 2 交易所调用后断言 `len(_margin_detail_memo) <= 4`；同 `(exchange, date)` 两次调用只触发一次底层 `_call_df_candidates`（spy）；空结果不入缓存。
9. **陈旧度**：命中更旧回退日 → 块 status=`partial` 且 `trade_date` 为该旧日；渲染输出含该日期。

## 10. 验证门禁

- 后端：`./scripts/ci_gate.sh`（flake8 critical E9/F63/F7/F82 + `pytest -m "not network"`）。
- 纯后端改动（adapter/base/analyzer/pipeline/schema/notification/template/report_language）→ 不触发 web-gate；报告 payload 新增可选字段属 API/Schema 联动，交付说明写明前端忽略未知字段、无破坏。
- 文档：CHANGELOG `[Unreleased]` 扁平追加；新增 `docs/margin-trading.md`；订正 `docs/capital-flow.md:103`。

## 11. 风险与回滚

- 风险：融资融券对决策只读（D4），不接 prompt/decision_stability/bias，故不扰动任何既有决策路径（§9.4 负契约锁定为护栏）。陈旧度由 `trade_date` + `partial` 暴露（§7）。memo 内存由 LRU≤4 + 仅缓存非空封顶（§7）。北交所/ETF 由 adapter 显式闸 + base 门控双重拦截（§4.1/§4.2）。
- 回滚：改动集中在新增 `get_margin_detail` + `get_margin_context` + base.py margin 枚举接入 + `MarginTrading` 模型 + `_build_margin_from_context`/`fill_margin_if_needed` + 两渲染路径 margin 块 + 标签；回滚=撤这些 additive 改动；capital_flow/主力资金流/降级/decision_stability/换手率/筹码 主链未动，回滚面小。可随 M4-B 整体弃用。

## 12. 已知局限（v1）

- 仅 A股（沪深融资融券明细 A股专属）；港股/美股/crypto/ETF/北交所无融资融券 section。
- 仅最新交易日快照（融资余额/融资买入额/融券余量 + 日期/交易所），无趋势/多日序列/占比衍生；presence-only。
- 最新交易日用有界回退 ≤3（非精确交易日历，节假日不建模）；长假/晚发布可能命中数日前快照，以 `trade_date` + `partial` 暴露。
- 北向/HSGT 个股数据 2024 起被交易所停更，无源可纳入（永久局限，非"留待迭代"）。
- 融资融券**仅呈现**，不喂 LLM、不进决策（与 capital_flow 的关键分歧，reviewer 必查）。
- 无专用 Web 组件（同 capital_flow/chip/volume，经 notification markdown + 报告 payload 呈现）。

---

## 实现切片（供 writing-plans 参考）

1. `fundamental_adapter.py`：`get_margin_detail`（交易所路由 + 北交所/ETF 显式闸 + smart-start/有界回退≤3+deadline + Lock/LRU≤4/仅非空 memo + 冻结词表字段 + trade_date 必填）+ adapter 单测（memo 上限/命中/空不缓存）。
2. `base.py`：`get_margin_context`（镜像 capital_flow，adapter 名对齐）+ **margin 接入全部枚举站点（all-or-nothing）** + 独立预算切片 + margin 引用用 `.get` 防御 + 枚举完整性回归（CN/ETF/离岸）。
3. `report_schema.py`：`MarginTrading` 模型 + `DataPerspective.margin_trading` + schema 向后兼容测试。
4. `analyzer.py` + `src/core/pipeline.py`：`_build_margin_from_context`/`fill_margin_if_needed`（presence-only/fail-open/纯 dict/写 `margin_trading`）+ **import 加 `fill_margin_if_needed`** + 两路径钉死调用点（chip→cf→margin→price→stabilize）+ §9.1/9.4/9.6 单测。
5. `notification.py` + `templates/report_markdown.j2` + `report_language.py`：两渲染路径 margin 块（Python 内联三元 / j2 用 labels）+ 四个双语标签 + §9.2/9.3/9.9 单测（两路径各覆盖）。
6. 文档：CHANGELOG 扁平行 + 新增 `docs/margin-trading.md`（镜像 capital-flow.md，**显式写 D4 分歧**：仅呈现、不喂 LLM/decision，无 _EN 兄弟文件）+ 订正 `docs/capital-flow.md:103`（融资融券已纳入 M4-B-2，仅北向因停更留作永久局限）+ 全量后端门禁。
