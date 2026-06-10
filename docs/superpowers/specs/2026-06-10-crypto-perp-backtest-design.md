# crypto 永续合约回测（资金费 + 做空，1x）设计（子项目 E）

> 阶段二「合约/永续维度」子项目 **E**（B/C/D/E 的最后一项）。让 perp 标的（`BASE/QUOTE:PERP`，子项目 C+D 已可路由/取日线/可分析）的**回测**反映两项永续机制：**做空盈亏**（看空建议不再记 0%，跌则盈）与**资金费成本**（持仓的真实 carry）。固定 **1x**，全程 additive，**零 DB schema 改动**，现货/股票回测逐字不变。**不做杠杆、不做强平、不做做空 TP/SL**（理由见 §2.3 / §8）。

**Goal:** 回测引擎对 perp 标的产出 perp-correct 的 `simulated_return_pct`：看空→`short`（`(entry−exit)/entry×100 + 资金费`），看多→`long`（现有多头盈亏 `−资金费`），观望/默认→`cash`（0%）；资金费由 OKX `funding-rate-history` 按回测窗口求和得到，门控复用 `crypto_derivatives_enabled`、失败优雅降级为 0。现货/股票路径（`is_perp=False`）行为与当前完全一致。

**范围决策（已确认）：**
- 口径：**资金费成本 + 做空盈亏**，固定 **1x**（无杠杆、无强平）。
- 现货/股票：`evaluate_single` 新增参数 `is_perp=False, funding_cost_pct=0.0`，**默认值 = 现状逐字不变**。
- 做空：用现有 `position_recommendation` 列存 `"short"`（`String(8)` 容纳），盈亏与资金费折进现有 `simulated_return_pct` 列。**不新增 `short_count`/`funding` 列 → 零 schema 改动、零迁移。**
- perp 无持仓态**复用 `"cash"`**（不引入 `"flat"`），使 `long_count`/`cash_count` 对 perp 仍有意义。

---

## 1. 背景与查证（已读码核实，2026-06-10）

- 引擎（`src/core/backtest_engine.py`，**纯逻辑、DB 无关**）：
  - `infer_position_recommendation`（:134）→ `"long"|"cash"`：bearish/wait→cash，bullish/hold→long，默认 cash。
  - `infer_direction_expected`（:112）→ `up/down/not_down/flat`：bearish→**down**，bullish→up，hold→not_down，wait→flat。**bearish 已映射 down**，做空胜负判定无需新增方向逻辑。
  - `evaluate_single`（:157，全 kwargs）：`start_price<=0`→error；`len(forward_bars)<eval_days`→insufficient_data；否则取窗口 `window_bars=forward_bars[:eval_days]`，`end_close=window_bars[-1].close`，`stock_return_pct=(end_close−start)/start×100`；`position=infer_position_recommendation`（:213）；调 `_evaluate_targets`；`simulated_return_pct`：`position!="long"`→`0.0`，`exit is None`→`None`，否则 `(exit−start)/start×100`（:238-244）；`simulated_entry_price = start if position=="long" else None`（:237）。
  - `_evaluate_targets`（:551）：`position!="long"`→`(None,None,"not_applicable",None,None,None,"cash")`（:568-577）；多头逐 bar：`stop_hit = low<=stop_loss`、`tp_hit = high>=take_profit`，同 bar 双触→`first_hit="ambiguous"` 且按止损价退出。
  - `_classify_outcome`（:511）：方向感知；`down` 时 `r<=−band`→win、`r>=band`→loss。**做空盈亏方向已正确**。
  - `compute_summary`（:275）：`long_count`/`cash_count` 按 `position_recommendation` 计数（:291-292）；`win/loss/neutral`、`direction_accuracy`、`avg_simulated_return_pct`、`avg_stock_return_pct` 均 **position-agnostic**（按 `outcome`/`*_return_pct` 聚合，:294-309）；`stop_loss_trigger_rate`/`take_profit_trigger_rate`/`ambiguous_rate`/`avg_days_to_first_hit` 的 applicable 过滤**键于 `position=="long"`**（:311-358）。
- 服务（`src/services/backtest_service.py`）：
  - `run_backtest`（:33）逐 analysis 取 `start_daily`（:95）、`forward_bars`（:117）→ `BacktestEngine.evaluate_single(operation_advice=…, start_price=float(start_daily.close), forward_bars=…, stop_loss=analysis.stop_loss, take_profit=analysis.take_profit, config=…)`（:131-139）。
  - `stop_loss`/`take_profit` **直接来自 analysis 记录**（:136-137）——即 LLM 产出的价位，引擎按**多头框架**消费（SL 在下、TP 在上）。
  - `get_config()` 已在 `run_backtest` 内可用（:42）；现已有惰性导入 `from data_provider.base import DataFetcherManager`（`_try_fill_daily_data`:519）规避导入开销/环路的先例。
  - `_build_summary_model`（:578）按现有 summary 键构造 `BacktestSummary`，含 `long_count`/`cash_count`（:588-589）。
- 存储（`src/storage.py`）：`BacktestResult.position_recommendation = Column(String(8))`（可容纳 `"short"`/`"cash"`/`"long"`）；`BacktestSummary` 的 `long_count`/`cash_count` 为固定 `Integer` 列——**新增 `short_count` 需新增列（迁移）**，本期**不做**。
- 仓储（`src/repositories/backtest_repo.py`）：`upsert_summary`（:278）更新列含 `"long_count"`/`"cash_count"`，不含 `short_count`。
- perp 标的判定/拆分（子项目 C+D 已落地，`data_provider/base.py`）：`is_perp_code(code)`、`parse_perp_code(code)->(base,quote)`；perp 已通过 `trading_calendar`/路由当作 crypto（24/7），`forward_bars` 由 OKX SWAP 日线落库后可取——E **不触碰**取数链路。
- 既有永续抓取范式（`data_provider/crypto_derivatives.py`）：`_http_get_json`（复用 `CRYPTO_FETCH_TIMEOUT_SECONDS`/`CRYPTO_FETCH_MAX_RETRIES`，4xx 不重试）、`_to_float`、`_okx_first`（fail-soft→`{}`）、`_LINEAR_QUOTES={"USDT","USDC"}`。本期新增 `fetch_funding_rate_history` 沿用同一纪律。

## 2. 架构与组件（全 additive；`is_perp=False` 即现货/股票现状逐字不变）

分层：`data_provider/` 纯抓取（不 import src.*、不碰 DB）；编排/门控在 `src/services/backtest_service.py`；纯盈亏逻辑在 `src/core/backtest_engine.py`。

### 2.1 `data_provider/crypto_derivatives.py`（扩展，纯抓取）

新增常量与函数：

```python
OKX_FUNDING_HISTORY_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
# 首页即自窗口右界(after=end_ms)向后翻，故 12 页约束的是“窗口跨度”(~400 天)而非“现在→窗口”距离；
# 实际 eval 窗口远小于此，正常不会截断；极端超界返回已采集部分（偏低估，fail-soft）。
_FUNDING_HISTORY_MAX_PAGES = 12  # 100 结算/页 ≈ 33 天/页


def fetch_funding_rate_history(base: str, quote: str, start_ms: int, end_ms: int) -> list:
    """OKX 永续 BASE-QUOTE-SWAP 在半开窗口 [start_ms, end_ms) 内的资金费率列表（fundingRate 小数）。
    自 after=end_ms 起向后分页（after=更早），按窗口过滤；非线性计价/参数非法/无数据 → []。fail-soft。"""
    base = (base or "").upper()
    quote = (quote or "").upper()
    if not base or quote not in _LINEAR_QUOTES:
        return []
    inst = f"{base}-{quote}-SWAP"
    rates: list = []
    cursor = int(end_ms)  # OKX after: 返回 fundingTime 早于该值的记录；自窗口右界起翻，预算用在窗口内
    for _ in range(_FUNDING_HISTORY_MAX_PAGES):
        params = {"instId": inst, "limit": "100", "after": str(cursor)}
        try:
            data = _http_get_json(OKX_FUNDING_HISTORY_URL, params)
        except Exception as e:
            logger.warning("[资金费历史] %s 抓取失败: %s", inst, e)
            break
        arr = data.get("data") if isinstance(data, dict) else None
        if not isinstance(arr, list) or not arr:
            break
        page_min_ts = None
        for item in arr:
            ts = _to_float(item.get("fundingTime"))
            fr = _to_float(item.get("fundingRate"))
            if ts is None:
                continue
            page_min_ts = ts if page_min_ts is None else min(page_min_ts, ts)
            if fr is not None and start_ms <= ts < end_ms:   # 半开 [start, end)：含 start、排除 end 边界结算
                rates.append(fr)
        if page_min_ts is None or page_min_ts <= start_ms or len(arr) < 100:
            break
        cursor = int(page_min_ts)
    return rates
```

- presence-only/fail-soft：任何异常或非线性计价 → `[]`（资金费成本归 0，做空/做多方向盈亏仍计算）。
- 半开窗口 `[start_ms, end_ms)`：含起界、排除终界结算，避免与上一/下一窗口在 00:00 边界双计；与 §2.4 真实持有区间对齐。
- 分页自 `after=end_ms` 起向后翻，`_FUNDING_HISTORY_MAX_PAGES` 约束的是窗口跨度（~400 天）而非“现在→窗口”距离；正常 eval 窗口远小于此，超限返回已采集部分（偏低估，fail-soft）。

### 2.2 `src/core/backtest_engine.py`：`infer_perp_position`（新 classmethod）

镜像 `infer_position_recommendation`，唯一差异：**bearish → `"short"`**（而非 `"cash"`）；其余一致（wait→cash、bullish/hold→long、默认 cash）。

```python
@classmethod
def infer_perp_position(cls, operation_advice: Optional[str]) -> str:
    """Infer perp position: long/short/cash. bearish -> short; otherwise mirrors long-only inference."""
    text = cls._normalize_text(operation_advice)
    if cls._matches_intent(text, cls._BEARISH_KEYWORDS):
        return "short"
    wait_pos = cls._first_intent_position(text, cls._WAIT_KEYWORDS)
    if wait_pos is not None:
        bullish_pos = cls._first_intent_position(text, cls._BULLISH_KEYWORDS)
        hold_pos = cls._first_intent_position(text, cls._HOLD_KEYWORDS)
        if (bullish_pos is None or wait_pos < bullish_pos) and (
            hold_pos is None or wait_pos < hold_pos
        ):
            return "cash"
    if cls._matches_intent(text, cls._BULLISH_KEYWORDS) or cls._matches_intent(text, cls._HOLD_KEYWORDS):
        return "long"
    if cls._matches_intent(text, cls._WAIT_KEYWORDS):
        return "cash"
    return "cash"
```

### 2.3 `src/core/backtest_engine.py`：`evaluate_single` 新增 perp 参数（additive）

签名追加 `is_perp: bool = False, funding_cost_pct: float = 0.0`（置于 `config` 之后，全 kwargs，默认 = 现状）。

**(a) position 推断——三处出口统一（error :180、insufficient_data :193、completed :213）：**

`evaluate_single` 在 `start_price<=0`（:180）、`insufficient_data`（:193）、`completed`（:213）三处**各自独立**计算 `position_recommendation`，当前均硬调 long-only 的 `cls.infer_position_recommendation`。若只改 :213，看空 perp 落到 error/insufficient 时会被记成 `"cash"` 而非 `"short"`（虽不影响仅统计 `completed` 的 `long_count`/`cash_count`，但持久化字段语义错误、与 completed 行不一致）。故在函数体顶部定义一次选择器，三处出口统一使用：

```python
infer_position = cls.infer_perp_position if is_perp else cls.infer_position_recommendation
# 三处原 `cls.infer_position_recommendation(operation_advice)`（:180/:193/:213）均改为 `infer_position(operation_advice)`
```

`_evaluate_targets(position=position, ...)` **不变**：`"long"` 走现有多头 TP/SL；`"short"`/`"cash"` 命中 `position!="long"`→`not_applicable`（`hit_sl/hit_tp=None`、`first_hit="not_applicable"`）。**做空不评估 TP/SL**（理由见下）。

**(b) entry / exit / simulated_return 块（替换 :237-244）：**

```python
funding = float(funding_cost_pct or 0.0) if is_perp else 0.0

if position == "long":
    simulated_entry_price = start_price
    if simulated_exit_price is None:
        simulated_return_pct = None
    else:
        simulated_return_pct = (simulated_exit_price - start_price) / start_price * 100
        if is_perp:
            simulated_return_pct -= funding            # 多头付资金费(正费率)
elif position == "short":                              # 仅 is_perp 可达（现货推断永不返回 short）
    simulated_entry_price = start_price
    simulated_exit_price = end_close                   # 做空持有至窗口末
    simulated_exit_reason = "window_end_short"
    if end_close is None:
        simulated_return_pct = None
    else:
        simulated_return_pct = (start_price - end_close) / start_price * 100 + funding  # 跌则盈 + 收资金费(正费率)
else:  # cash
    simulated_entry_price = None
    simulated_return_pct = 0.0
```

返回 dict 不增删键（`simulated_entry_price`/`simulated_exit_price`/`simulated_exit_reason`/`simulated_return_pct` 已存在；short 复写 exit 价/原因，`first_hit`/`hit_*` 维持 `_evaluate_targets` 的 `not_applicable`/`None`）。

**资金费口径与方向：** OKX `fundingRate` 为正＝多头付空头。`funding_cost_pct = Σ(持有窗口内 fundingRate) × 100`（百分比）。多头 `−funding`，空头 `+funding`。持有窗口对齐**真实持仓区间**（入场=起始 bar 收盘＝`start_date+1` 00:00 UTC，出场=末 bar 收盘＝`start_date+N+1` 00:00 UTC，半开 `[入场, 出场)` ≈ `eval_window_days×3` 个 8h 结算，见 §2.4）。残余近似仅一处：**多头**提前触发 TP/SL 时仍按整持有窗口计资金费（略高估持有成本，对多头 `−funding` 偏保守，可接受）；**做空恒持有至窗口末、无提前出场**，对齐窗口后无系统性偏置（消除了初版“整窗口计提对空头 `+funding` 反偏乐观”的反保守问题）。1x、日内 carry 量级很小。

**为何做空不评估 TP/SL（相对初版"反向 TP/SL"的收敛）：** `stop_loss`/`take_profit` 来自 analysis（LLM 多头框架，SL 在下、TP 在上）。对做空套用反向判定会把"低于入场的 SL"立即判成触发，产出垃圾。除非 analysis 明确按做空框架产出价位（当前无此保证），否则反向 TP/SL 是**虚构精度**。故做空仅记窗口末方向盈亏 + 资金费，TP/SL 留空（`not_applicable`），对称性让位于正确性。

### 2.4 `src/services/backtest_service.py`：perp 检测 + 资金费成本 + 调用（编排）

`run_backtest` 循环内，`evaluate_single` 调用（:131）前插入 perp 检测与资金费计算；`evaluate_single` 增传 `is_perp`/`funding_cost_pct`：

```python
# 顶部 import 调整：from datetime import date, datetime, timedelta, timezone
# run_backtest 循环前一次性惰性导入（沿用 _try_fill_daily_data 的惰性约定）：
from data_provider.base import is_perp_code

# ...evaluate_single 前：
is_perp = is_perp_code(analysis.code)
funding_cost_pct = 0.0
# 仅在 forward_bars 足量（不会落 insufficient_data）时才发起 OKX 资金费抓取，避免对将被丢弃的行做无谓网络 I/O
if is_perp and len(forward_bars) >= int(eval_window_days) and getattr(config, "crypto_derivatives_enabled", True):
    funding_cost_pct = self._compute_perp_funding_cost_pct(
        code=analysis.code,
        start_date=start_daily.date,
        eval_window_days=int(eval_window_days),
    )

evaluation = BacktestEngine.evaluate_single(
    operation_advice=analysis.operation_advice,
    analysis_date=start_daily.date,
    start_price=float(start_daily.close),
    forward_bars=forward_bars,
    stop_loss=analysis.stop_loss,
    take_profit=analysis.take_profit,
    config=eval_config,
    is_perp=is_perp,
    funding_cost_pct=funding_cost_pct,
)
```

新增私有方法（fail-soft，资金费抓取异常不拖垮回测主流程）：

```python
def _compute_perp_funding_cost_pct(self, *, code: str, start_date: date, eval_window_days: int) -> float:
    """perp 标的真实持有窗口 [入场=start_date 收盘, 出场=末 bar 收盘) 的资金费成本(百分比, 多头视角)。
    窗口半开、≈ eval_window_days×3 个 8h 结算；非 perp/禁用/异常 → 0.0。
    注：窗口按日历日推算，假定 OKX 24/7 日线无内部缺口（缺口期口径退化为近似）。"""
    try:
        from data_provider.base import parse_perp_code
        import data_provider.crypto_derivatives as cd
        base, quote = parse_perp_code(code)
        # 持有区间对齐真实持仓：OKX 1D candle 收盘对齐次日 00:00 UTC。
        midnight = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        start_dt = midnight + timedelta(days=1)                    # 入场 = start_date 收盘
        end_dt = midnight + timedelta(days=eval_window_days + 1)   # 出场 = 末 bar 收盘
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        rates = cd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
        return sum(rates) * 100.0
    except Exception as exc:
        logger.warning(f"perp 资金费抓取失败({code}): {exc}")
        return 0.0
```

**门控分离（设计要点）：** 做空方向盈亏是**标的本质**（perp 代码即衍生品，纯 bar 计算、无网络），对 `is_perp_code` 标的**始终生效**；资金费是**网络抓取的成本调整**，复用 `crypto_derivatives_enabled` 作为 OKX 调用 kill-switch（默认开）。禁用或抓取失败 → 资金费 0、方向盈亏照常。

### 2.5 `src/core/backtest_engine.py`：`compute_summary` 的 short 处理（**零 schema**）

- `win/loss/neutral`、`direction_accuracy_pct`、`win_rate_pct`、`avg_simulated_return_pct`、`avg_stock_return_pct` 均 position-agnostic → **做空自动正确纳入**（无改动）。
- `long_count`/`cash_count` 维持现状：perp 多头计入 `long_count`、perp 无持仓计入 `cash_count`；**做空（`"short"`）不计入这两项**，但计入 `completed_count`，并以 `position_recommendation="short"` 结果行可查。
- `stop_loss_trigger_rate`/`take_profit_trigger_rate`/`ambiguous_rate`/`avg_days_to_first_hit` 的 applicable 过滤键于 `position=="long"`——做空 `hit_*=None` 天然被排除，**无需改动**。
- **不新增 `short_count`/funding summary 列**，故 `compute_summary`/`_build_summary_model`/`upsert_summary`/`storage` 均**零改动**。注：凡 `completed` 集合含做空行的 summary——含 `scope="overall"` 的现货+perp **混合行**以及 perp 个股行——都会 `long_count + cash_count < completed_count`（差额=做空数），属预期；仅 `completed` 全为现货/股票的 summary 才严格 `long+cash==completed`。无消费方依赖该等式（已核：无 Python 断言依赖，Web fixture 本身亦不满足该等式）。

### 2.6 配置 / schema

- **无新配置项**：复用 `crypto_derivatives_enabled`（默认开）。无 `.env.example`/registry/locale 改动。
- **无 DB schema 改动、无迁移**：`position_recommendation`（`String(8)`，容纳 `"short"`）复用，资金费/做空盈亏折进 `simulated_return_pct`。
- **同步更新两处枚举注释**（纯注释、无迁移）：`src/storage.py:307` `# long/cash` → `# long/cash/short`；`src/storage.py:333` `# stop_loss/take_profit/window_end/cash/ambiguous_stop_loss` → 追加 `window_end_short`（`"window_end_short"` 16 字符 ≤ `String(24)`）。保持列契约注释与实际取值集一致。

## 3. 数据流

```
run_backtest(perp 标的) 逐 analysis
  → start_daily / forward_bars（C+D 已让 perp 走 crypto 取数）
  → is_perp_code(code)? 且 crypto_derivatives_enabled
       → _compute_perp_funding_cost_pct → cd.fetch_funding_rate_history(base,quote,start_ms,end_ms) → Σrate×100
  → BacktestEngine.evaluate_single(..., is_perp=True, funding_cost_pct)
       → position = infer_perp_position（bearish→short）
       → simulated_return_pct：long=(exit−start)/start×100−funding；short=(start−end)/start×100+funding；cash=0
  → BacktestResult（position_recommendation 含 "short"；simulated_return_pct 含 perp 调整）
  → compute_summary（win/loss/avg 含 short；long/cash 计数不含 short）
```

现货/股票（`is_perp_code=False`）：`is_perp=False`、`funding_cost_pct=0.0` → 走原路径，**逐字不变**。

## 4. 错误处理 / 兼容

- 资金费抓取 fail-soft：`fetch_funding_rate_history` 任何异常/非线性计价/无数据 → `[]` → 成本 0；`_compute_perp_funding_cost_pct` 再包一层 try/except → 0.0。回测主流程不中断。
- `is_perp=False` 默认参数 → 现货/股票回测、现有结果与 summary **完全向后兼容**；现有 DB 行无需迁移、无需回填。
- `position_recommendation` 新增取值 `"short"` 仅出现在 perp 行；现货消费方（Web 历史、Agent memory 归一化 `_normalize_learning_summary`）按 `outcome`/比率消费，不枚举 position 值，无破坏。
- 复用 `CRYPTO_FETCH_*` 超时/重试；分页有上限，避免历史过久标的拖慢回测。

## 5. 测试（离线，无网络、无 LLM）

| 层 | 用例 |
|---|---|
| data_provider | `fetch_funding_rate_history`：monkeypatch `_http_get_json`：半开窗口 `[start,end)` 内求和正确（`ts==start` 含、`ts==end` 排除的边界用例）、窗口外过滤、自 `after=end_ms` 起向后分页拼接、非线性计价→[]、异常→[]、超页上限截断。fake 须按 `"funding-rate-history" in url` **先于** `"funding-rate"` 分支匹配（避免误入现货 funding-rate fixture） |
| 引擎 position | `infer_perp_position`：bearish→short、bullish/hold→long、wait/默认→cash（对照 `infer_position_recommendation` 仅 bearish 分叉） |
| 引擎 long（perp） | `evaluate_single(is_perp=True, funding_cost_pct=f)`：多头 `(exit−start)/start×100 − f`；TP/SL 命中路径与现货一致；funding=0 时数值等同 spot long |
| 引擎 short | 看空建议：position="short"、`(start−end)/start×100 + f`、`simulated_exit_reason="window_end_short"`、`hit_*`/`first_hit` 为 None/not_applicable；跌则 win（direction down） |
| 引擎 error/insufficient（perp）| 看空 perp 落 `start_price<=0` 或 `insufficient_data`：`position_recommendation=="short"`（验证三处出口选择器统一，非 long-only `"cash"`） |
| 引擎现货回归 | 现有 lock/characterization 套件 **`tests/test_backtest_engine.py` + `tests/test_backtest_summary.py` + `tests/test_crypto_backtest.py` 必须原样全绿、不得改 fixture/断言**（`is_perp=False` 逐字不变的首要回归证据）；另补一例显式断言默认参数下 long/cash 的 `simulated_return_pct`/entry/exit 与改动前一致 |
| summary | 含 short 行：`win/loss/avg_simulated_return` 纳入 short；`long_count`/`cash_count` 不含 short；`completed_count` 含 short；现货集仍 `long+cash==completed` |
| funding 窗口 helper | `_compute_perp_funding_cost_pct`：spy 捕获传入 `fetch_funding_rate_history` 的 `(start_ms, end_ms)`，断言＝入场(`start_date+1` 00:00 UTC)/出场(`start_date+N+1` 00:00 UTC)，且 `pct==sum(rates)*100`；`forward_bars` 不足时**不发起**抓取（gate 生效） |
| service | `run_backtest` perp 标的：monkeypatch `cd.fetch_funding_rate_history` + seeded bars → 落 `position="short"`/资金费调整后的 `simulated_return_pct`；`crypto_derivatives_enabled=False` → funding 0、方向盈亏仍计算；非 perp 标的不调资金费 |

真实在线可达性（OKX funding-rate-history）走 `network-smoke`/手测。

## 6. 文档

- `docs/crypto-guide.md` 回测节补「永续回测（资金费 + 做空，1x）」小节：做空盈亏口径、资金费成本口径与持有窗口对齐、1x、不做杠杆/强平/做空 TP/SL、门控复用 `crypto_derivatives_enabled`。
- **同步修正同文件中与本期矛盾的旧表述**（避免文档自相矛盾，对齐 AGENTS.md 反漂移）：`docs/crypto-guide.md:33` 删/改 “暂不含 perp 回测”；`:229` 从“后续子项目（未做）”移除 “perp klines/回测”（klines 属 C+D、回测属 E，均已落地）；`:244`「合约 / 永续 / 杠杆（当前仅现货）」精修为“perp 标的分析与回测已支持，杠杆仍不做”。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平：`- [新功能] crypto 永续合约回测纳入资金费成本与做空盈亏（1x，additive，零 schema，复用 crypto_derivatives_enabled；现货/股票回测不变）`。

## 7. 分支与回滚

- 分支：`feat/crypto-perp-backtest`，叠在 `feat/crypto-perp-instrument` 上。
- 回滚：纯新增/参数默认值（1 抓取函数 + 引擎 `infer_perp_position` + `evaluate_single` position 选择器/entry-exit 分支 + service perp 检测/资金费 helper）；`is_perp` 默认 False → 现货/股票路径不变；`CRYPTO_DERIVATIVES_ENABLED=false` 即停资金费抓取；`git revert`/丢弃分支即恢复。无 DB 迁移需回滚。

## 8. 范围边界（YAGNI / 不做）

不做：**杠杆**（analysis 不含杠杆信息）、**强平**（日线无法忠实模拟盘内强平→虚构精度）、**做空 TP/SL**（analysis 价位为多头框架，反向套用＝虚构精度，§2.3）、**`short_count`/funding 的 summary 列**（避免高风险 DB 迁移；做空经 `position="short"` 行与 win/loss/avg 体现）、取数链路改动（C+D 已完成 perp 路由/日线）。本期仅把资金费成本与做空盈亏 additive 折进现有回测引擎与结果列。
