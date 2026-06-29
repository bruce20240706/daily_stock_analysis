# 链路B 窗口正确性设计（_eval 右端截尾 + 非 crypto 分钟历史深度）

- 主题：`chainb-window-correctness`
- 日期：2026-06-26
- 状态：设计定稿（经对抗式审查收敛 v2，见 §9），待落实现计划
- 范围：链路B（信号可信度三重门回测）；链路A 仅核验+文档收尾，不改代码
- 关联：本特性是「盘中/分钟级回测」epic 的窗口正确性收尾，承接信号引擎走查向量化
  （`2026-06-25/26-signal-eval-vectorization*`）解锁的分钟链路B 可用性。

---

## 0. 背景与缘起

「盘中/分钟级回测」epic 推进过程中，多次登记了两个 deferred 待办：

- `end_date 右边界截断`
- `_load_bars 下传 start_date 为非 crypto 源真正加深历史`

本次对两条路径逐行核验后，结论与原措辞有偏差，需先厘清：

### 0.1 运行态拓扑（两条消费路径）

| 链路 | 入口 | 锚定 | 窗口右边界 | 现状 |
| --- | --- | --- | --- | --- |
| 链路A（操作建议/PnL 前向验证） | `backtest_service.py::run_backtest` | per-candidate `analysis_date` | `analysis_date + eval_window_days` + 长假缓冲，`start_date`/`end_date` 精确下传 | **窗口正确性已具备** |
| 链路B（信号可信度三重门） | `signal_backtest_service.py::SignalBacktestService.run` → `signal_backtest._eval` | 无 analysis_date，逐股全历史扫描 | df 自然长度，无截断 | **两处窗口缺陷（本特性修复）** |

链路B 产出的 `signal_stats` 经 `resolve_marker_hit_fields` 被图表/看板消费为命中率注解；
图表 marker 几何由 `compute_volume_price_signals` 决定（与链路B `_eval` 解耦），本特性不触及。

### 0.2 缺陷一：`_eval` 右端截尾偏差（探索新发现）

`src/services/signal_backtest.py:120`：

```python
for t in range(min_history, n - 1):                                    # 至少留 1 根前瞻
    ...
    fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
```

`df.iloc[t+1 : t+1+horizon]` 在 `t` 接近右端时被 pandas 自动截短为不足 `horizon` 根。
后果（右端截尾 / right-censoring）：

`classify_triple_barrier`（signal_backtest.py:72-81）是逐根首触即返、对 win/loss **对称**的判定，
走完所有可用前瞻仍未触任何门才记 `expired`。故截短前瞻窗的失真是**对称**的：

- 所有在可用窗口内尚未解析的**晚解析结果（慢赢 AND 慢输皆然）**→ 统一记 `expired` → 排除出胜率
  分母（`sample = win + loss`）。
- 所有**早解析结果（快赢 AND 快输）**→ 全部保留。

净效果是最近端信号被「早解析者」过度代表；`win_rate`/Wilson CI 的偏置**方向先验不可定**（取决于被
截尾的晚解析队列相对被保留的早解析队列的赢/输构成），但无论方向，这都是不该有的统计偏置。注释
「至少留 1 根前瞻」表明这是当初欠规约的边界，而非有意决策。链路A 通过 `min_age_days` 入选门 + 引擎
`forward_bars[:eval_days]` 早已规避同类问题；链路B 无等价守卫。**C1（`n-1`→`n-horizon`）消除全部
右端截尾，与偏置方向无关。**

### 0.3 缺陷二：`_load_bars` 非 crypto 历史深度不受控

`src/services/signal_backtest_service.py:174-176`：

```python
df, _src = DataFetcherManager().get_intraday_data(
    code, interval, days=_minute_fetch_days(market_interval=interval))
```

分钟分支只传 `days`，而 `days` 仅 crypto 真正遵从：

- **crypto**：`get_intraday_data` 用 `start_ms = now - days` 锚定回看根数，深度受控。
- **A股**：tushare/akshare 忽略 `days`；不传 `start_date` 时 akshare 用 `1970-01-01`～`2099-01-01`
  宽边界（凑巧取到源最大窗），tushare 取源默认（可能偏浅）。
- **美股**：yfinance 忽略 `days`；不传 `start`/`end` 时 `yf.download` 对分钟仅返回很短的默认窗。

净效果：美股（及部分 A股）分钟链路B 的样本量远小于 crypto，可信度统计不可用甚至为空。

### 0.4 链路A `end_date 右边界` 已闭合（核验，不改码）

`backtest_service.py:185-204` 分钟路径已精确算出窗口并下传 `start_date`+`end_date`：

```python
_minute_window_start = start_daily.date + timedelta(days=1)
if market == "crypto":
    _end_offset = int(eval_window_days)
else:
    _end_offset = max(int(eval_window_days) * 2, int(eval_window_days) * 3 // 2 + 14)
_window_end_date = _minute_window_start + timedelta(days=_end_offset)
fwd_df, _src = DataFetcherManager().get_intraday_data(
    analysis.code, interval=interval,
    start_date=_minute_window_start.isoformat(),
    end_date=_window_end_date.isoformat(),
    days=int(eval_window_days))
```

右边界受控的证据链：

- A股 tushare/akshare、美股 yfinance：`end_date` 直接传给源 API → 源层截断。
- crypto：Binance `_page_klines` 忽略 `end_date`，但 `limit ≈ days × bars_per_day`（`days = eval_window_days`，
  crypto `_end_offset = eval_window_days`）正向翻页，右边界等价由 `limit` 封顶；再经引擎
  `evaluate_single` 的 `forward_bars[:eval_days]` 二次截断。

结论：链路A 的 `end_date 右边界截断` 在 A股/美股扩展后已等价闭合，本特性仅在实现计划中**重跑一次
核验**并在文档登记关闭，不改代码。

---

## 1. 设计决策

三项决策对应 brainstorming 阶段的用户选择：

- **D1 范围**：两半都修（链路B `_eval` 截尾 + `_load_bars` 非 crypto 深度）。二者是「链路B 窗口
  正确性」的两半，共用同一回归套件，一次收敛。
- **D2 截尾作用面**：统一修（日线 + 分钟）。`signal_backtest.py` 首行声明该模块「纯函数，
  bar-interval 无关」；截尾修复（循环上界 `n-1`→`n-horizon`）天然 interval-agnostic。若「仅分钟」
  则须把 interval/flag 穿进纯函数、破坏其契约，且日线同类偏差保留不修。取统一修：更正确、代码更干净，
  代价是日线 `signal_stats` 一次小幅、可文档化的变化。
- **D3 band 夹取放置**：链路B 本地 band 表。源历史上限知识落在 `signal_backtest_service` 的常量
  （同既有 `_YF_INTERVAL` / `MARKET_TRADING_MINUTES` 风格），不给 fetcher 加能力声明（避免「链路B
  不知道会路由到哪个 fetcher」+ 额外机械代码，对 3 市场×4 周期属过度设计）。

  关键约束：band 夹取**不能下沉到 fetcher**。链路A 对越界的旧 analysis_date 期望 `insufficient_data`
  （fail-closed），若 fetcher 内部夹取会静默返回近端不匹配数据污染链路A。故夹取只发生在链路B 的
  `_load_bars` 调用侧，fetcher 与链路A 不变。

---

## 2. 改动 C1：`_eval` 右端截尾（`src/services/signal_backtest.py`）

### 2.1 代码改动

```python
-    for t in range(min_history, n - 1):                                    # 至少留 1 根前瞻
+    for t in range(min_history, n - horizon):                              # 保证完整 horizon 根前瞻（右端欠龄不计入）
```

同步订正 `_eval` docstring 中的边界描述（「至少留 1 根前瞻」→「保证每个被评估 bar 都有完整
horizon 根前瞻，消除右端截尾偏差」）。`evaluate_signal_outcomes`/`evaluate_baseline_outcomes` 的
`horizon` 参数语义由「前瞻 bar 数上限」收紧为「完整前瞻 bar 数」，docstring 顺带订正。

### 2.2 语义与边界

- baseline（`all_bars=True`）与信号（`all_bars=False`）共用同一循环，`excess = ci_low - baseline_rate`
  口径保持一致。
- `range(min_history, n - horizon)`：当 `n - horizon <= min_history` 时为空区间 → 无 outcome（视为
  数据不足，可接受）。`horizon` 默认 10、恒 ≥1。
- `if not fwd: continue` 守卫在新边界下成为防御性冗余（fwd 恒为完整 horizon 根），保留无害。
- `_MIN_BARS = 50` 与 `min_history = 40`、`horizon = 10` 的关系：需 `n > min_history + horizon`（即
  `n > 50`）才产出 outcome；恰好 50 根的股票现产出 0 条（此前产 ~9 条截尾样本）。属更正确，**不调整
  `_MIN_BARS`**（YAGNI，稳定性优先）。

### 2.3 消费者范围

`evaluate_signal_outcomes` / `evaluate_baseline_outcomes` 仅被链路B（`signal_backtest_service`）消费；
图表/看板走 `compute_volume_price_signals`，不经 `_eval`。故 C1 仅影响链路B `signal_stats`。

---

## 3. 改动 C2：非 crypto 分钟历史深度（`src/services/signal_backtest_service.py`）

### 3.1 band 常量与天数

```python
# 各源分钟历史「单次安全回看上限」(日历天):据 start_date 加深历史时按 市场×interval 夹取,
# 避免向源请求其单次调用无法稳定返回的过深窗口。
#   us(yfinance):5m/15m≈60d、1h≈730d 为文档硬上限;1m=7d(且 1m 已在 fetcher fail-closed)。
#   cn(tushare):stk_mins 单次有行数上限,下列为保守值——
#     ⚠ 实现期必须在线核验 tushare 对 today-N 的 1m/5m/15m/1h 真实单次返回(优雅近端子集 / 报错 /
#       返回错窗),据实校准本表(可放宽);核验前以保守上限避免 cn 高频从「浅窗可用」恶化为单股 errors。
#       关键:tushare get_intraday_data 体内不读 days(no-op),但真正消费 start_date,故旧「days 偏大
#       不取错数」的安全性不可迁移到 start_date——这正是本表对 cn 也必须夹取的原因。
# crypto 不在表中:按 days 锚定、不下传 start_date(见 _minute_fetch_start_date),行为字节级不变。
_INTRADAY_MAX_DAYS = {
    "us": {"1m": 7, "5m": 60, "15m": 60, "1h": 730},
    "cn": {"1m": 30, "5m": 90, "15m": 365, "1h": 730},
}


def _minute_fetch_days(*, market: str, interval: str) -> int:
    """分钟取数回看天数（传给 get_intraday_data 的 days，并据此推 start_date）。

    1h 历史更深取 730、其余取 365 为基线;再按 _INTRADAY_MAX_DAYS[market][interval] 夹取
    (us/cn 各源单次安全上限)。crypto 不在表中→返回基线(与旧 _minute_fetch_days 逐 interval 相等)。
    """
    base = 730 if interval == "1h" else 365
    band = _INTRADAY_MAX_DAYS.get(market, {})
    return min(base, band.get(interval, base))
```

签名由 `_minute_fetch_days(*, market_interval)` 改为 `_minute_fetch_days(*, market, interval)`。
（无现存测试引用旧签名，安全。）crypto 走 `band.get` 回退基线 → days 与旧实现逐 interval 相等（§3.4）。

### 3.2 历史起点

```python
def _minute_fetch_start_date(*, market: str, interval: str, today: Optional[date] = None) -> Optional[str]:
    """非 crypto 分钟取数的历史起点（ISO date 字符串）。

    crypto 返回 None → 维持 get_intraday_data 的 days-only 近窗行为（字节级不变）；
    非 crypto 返回 today - _minute_fetch_days，使 tushare/yfinance 真正加深历史。
    today 默认 date.today()；helper 单测可显式注入 today 保证确定性，_load_bars 集成测试（§5.4）
    则经 monkeypatch 模块级 date 锁定（_load_bars 不透传 today）。
    """
    if market == "crypto":
        return None
    ref = today or date.today()
    return (ref - timedelta(days=_minute_fetch_days(market=market, interval=interval))).isoformat()
```

模块顶部新增 `from datetime import date, timedelta`。

### 3.3 `_load_bars` 接 market 并下传 start_date

```python
def _load_bars(self, svc, code, interval, market):
    """按 interval 取 bar：日线走 StockService（不变），分钟走链路A get_intraday_data。

    分钟路径按 market 计算回看深度与历史起点：crypto 维持 days-only（字节级不变），
    非 crypto 下传 start_date 真正加深历史（美股夹 yfinance band）。把 'datetime' 列
    重命名为 'date' 复用既有 'date' 列契约。取不到数据返回 None（交由 run 计入 skipped）。
    """
    if not is_intraday_interval(interval):       # '1d'
        hist = svc.get_history_data(stock_code=code, period="daily", days=_FETCH_DAYS)
        rows = (hist or {}).get("data") or []
        return pd.DataFrame(rows) if rows else None
    from data_provider.base import DataFetcherManager
    days = _minute_fetch_days(market=market, interval=interval)
    start_date = _minute_fetch_start_date(market=market, interval=interval)
    df, _src = DataFetcherManager().get_intraday_data(
        code, interval, start_date=start_date, days=days)
    if df is None or df.empty:
        return None
    return df.rename(columns={"datetime": "date"})
```

`run` 内调用点（line 114）改为 `df = self._load_bars(svc, code, interval, market)`；`market` 已在
line 108 由 `get_market_for_stock(code)` 算出（返回 `'cn'|'hk'|'us'|'crypto'|None`，crypto 含 perp）。

### 3.4 crypto 字节级不变证明

`market == 'crypto'`（spot+perp） → `_minute_fetch_start_date` 返回 `None`、`_minute_fetch_days`
不走 us 夹取 → `days = 730/365`。

- 旧调用：`get_intraday_data(code, interval, days=730/365)`，`start_date` 取默认 `None`。
- 新调用：`get_intraday_data(code, interval, start_date=None, days=730/365)`。

实参等价 → 缓存键 `(code, interval, days, 'None', 'None')` 不变 → 取数与缓存行为字节级一致。

### 3.5 非 crypto 缓存键影响

非 crypto 下 `start_date` 由 `None` 变为真实 ISO date → 缓存键随之变化（属正确：是新的、更深的窗口）。
同一进程内 `date.today()` 稳定，键稳定；跨自然日变化 → 键变 → 取数刷新到「现在」，符合预期。无串桶风险。

### 3.6 各市场净行为

| market / interval | days(夹取后) | start_date | 净效果 |
| --- | --- | --- | --- |
| crypto / perp（全 interval） | 730(1h)/365 | None | 字节级不变 |
| cn 1h | 730 | today-730 | tushare 经 start_date 加深（≈2000 根，大概率不越界） |
| cn 15m | 365 | today-365 | tushare 加深（≈3900 根，大概率不越界） |
| cn 5m | 90（保守夹取） | today-90 | 夹到保守上限避免越界；实现期据 tushare 真实上限校准 |
| cn 1m | 30（保守夹取，tushare 独占） | today-30 | akshare 1m fail-closed；tushare 深窗夹保守上限 |
| us 5m / 15m | 60 | today-60 | 夹到 yfinance band 避免越界返空 |
| us 1h | 730 | today-730 | yfinance band 内 |
| us 1m | 7 | today-7 | yfinance 1m 已 fail-closed（band 自洽，start 不达 download） |
| hk / None | base | today-base | 路由层无分钟 fetcher → fail-closed，与现状一致 |

---

## 4. 行为变更与兼容性

### 4.1 链路B `signal_stats` 变化（C1，日线 + 分钟）

- 日线与分钟 `signal_stats` 均丢弃最近 `horizon-1` 根欠龄信号 → `win`/`loss`/`sample`/`win_rate`/
  `ci_low`/`ci_high`/`baseline_win_rate`/`excess` 小幅变化。
- 图表 marker 与几何**不变**（由 `compute_volume_price_signals` 决定）；仅命中率注解数值随重跑
  `--signal-backtest` 落库后微调，且可能跨 `min_sample` 阈值出现/消失。
- 与向量化 §5.2 已记录的同类行为变更同源，需在 `docs/signal-credibility.md` §5.2 续记 + CHANGELOG。

### 4.2 非 crypto 分钟历史深度（C2）

美股分钟链路B 由「源默认浅窗」变为「夹 yfinance band 的受控深窗」（样本量上升、可信度统计可用）。
A股经 tushare `start_date` 加深，但**深度受各 interval 的源单次上限约束**：1h/15m 大概率可深取，
5m/1m 夹到保守上限（实现期据 tushare 真实单次返回校准，见 §3.1 ⚠）。crypto 不变。无 API/schema
破坏；但这是用户可见能力增强（美股/A股分钟可信度可用性），需同步 `docs/intraday-backtest.md` 与
`docs/signal-credibility.md`（§6）。

### 4.3 不涉及

- 无 DB schema 迁移、无 `.env`/配置项新增（band 表为源能力常量）。
- 图表/看板路径、链路A、各 fetcher、`compute_volume_price_signals` 全传递树：零改动。
- 读路径（`resolve_marker_hit_fields`/`/signals/board`）：不变，只是被注解的统计数值刷新。

---

## 5. 测试设计

### 5.1 C1 截尾边界（确定性，纯函数）

> ⚠ 关键：截尾修复是「绝对边界」off-by-one（`n-1`→`n-horizon`）。**差分型断言**（两 horizon 计数差、
> 去尾 1 根计数差）对上界的恒定偏移 c 完全免疫——实测上界 `n-horizon+c`（c∈{-1,0,+1}）下差分恒定不变，
> 却在 c=+1 时重新引入截尾。故必须用**绝对计数**断言锁边界，不可只用差分。

- **T1 绝对计数锁边界**：构造「全有效价位」fixture（平滑趋势 + OHLC 留振幅使 ATR>0，价位从 t=19 起
  非 None，见本节末注），断言
  `len(evaluate_baseline_outcomes(df_n, horizon=h)) == max(0, (n - h) - min_history)`（min_history=40）
  对 (n,h) ∈ {(50,10)→0, (51,10)→1, (120,10)→70, (120,5)→75}。绝对计数对上界 off-by-one 敏感：
  上界误写 `n-h±1` 会被 (50,10)/(51,10) 边界例与 (120,*) 例同时检出。
- **T2 末根满 horizon（直接）**：T1 的 (51,10) 例即锁此性质——唯一被评估 bar t=40 的前瞻为
  `df.iloc[41:51]` 恰 10 根；(50,10) 例产 0 条则证明「不足完整 horizon 的 bar 一律不评估」。
- **T3 因果不漏（既有 `test_evaluate_signal_outcomes_causal_no_future_leak` 复核）**：截断 df 的可评估
  bar 集合 ⊆ 完整 df，`full_counts[key] >= truncated` 仍成立（新边界对两者一致收缩）。复跑确认通过。

> **本节末注（fixture 可构造性，已核验）**：价位仅依赖 rolling MA20 / 20 根 swing-low / ATR14
> （`derive_price_levels_series`，**不依赖 swing pivot**），平滑趋势即可让 t≥19 全部有效价位；但 high
> 须 > low（留 OHLC 振幅）否则 ATR=0 → stop/target=None。T1 fixture 必须给出 OHLC 振幅，避免退化。

### 5.2 C1 oracle 镜像（向量化等价套件）

承重 golden 套件中有**两处**逐窗前瞻 oracle 内联循环须随 C1 同步改（否则该套件落红）：

- `tests/test_signal_eval_vectorization.py:327` `_oracle_signal_outcomes`（信号 oracle）：
  `for t in range(min_history, n - 1)` → `range(min_history, n - horizon)`。
- `tests/test_signal_eval_vectorization.py:362` `test_golden_baseline_unchanged` 的**内联 baseline
  参照 oracle**：`for t in range(40, n - 1)` → `range(40, n - horizon)`（该测试硬编码 horizon=10，
  即 `range(40, n - 10)`）。⚠ **此处极易误判**：它紧邻 line 392 的 compute_signals 断言，但 line 362
  实为 `evaluate_baseline_outcomes` 的逐窗参照（line 370 `assert _counter(base) == Counter(ref)`），
  与 C1 强相关。实测不改则 n=160 时 **119 vs 110**（尾部 9 条截尾样本）→ 该承重 golden 必红。
- **仅 line 392**（`assert any(k < n - 1 for k in sig.keys())`，关于 `compute_signals_for_all_bars`
  的 sig.keys）与 horizon 截尾无关，**保持不动**。
- 两处 oracle 的 fwd 截短逻辑（`df.iloc[t+1:t+1+horizon]`）已与 `_eval` 逐字一致，故只改循环上界即足够
  （无需改 fwd）。
- 复跑多种子 Counter 等价测试，确认 `_eval` 与两处 oracle 在新边界下仍逐项等价。

### 5.3 C2 helper 单测

- `_minute_fetch_days`：`('us','5m')==60`、`('us','15m')==60`、`('us','1h')==730`、`('cn','5m')==365`、
  `('cn','1h')==730`、`('crypto','5m')==365`、`('crypto','1h')==730`。
- `_minute_fetch_start_date`：`market='crypto'` → `None`；
  `('us','5m', today=date(2026,6,26))==(date(2026,6,26)-timedelta(days=60)).isoformat()`；
  `('us','1h', today=...)== today-730`；`('cn','5m', today=...)== today-365`（断言用 timedelta 自洽，不手算）。

### 5.4 C2 `_load_bars` mock 抓参

monkeypatch `DataFetcherManager.get_intraday_data` 捕获调用 kwargs（返回桩 df 使 rename 通过）；
因 `_load_bars` 不透传 today，用例须 monkeypatch 模块级 `signal_backtest_service.date`（固定 `.today()`）
或以同帧 `date.today()` 计算期望，锁定确定性（避免跨午夜抖动）：

- crypto：`start_date is None`、`days==365`（5m）。
- us 5m：`start_date == today-60`、`days==60`。
- cn 5m：`start_date == today-90`、`days==90`（按 §3.1 cn 保守 band）。
- cn 1m：`days==30`、`start_date == today-30`（tushare 独占，验证夹取生效）。

### 5.5 既有测试复核（不绕过真实风险）

- `tests/test_signal_backtest.py::test_evaluate_signal_outcomes_exact_dedup_count`（`vb_count==1`）：
  fixture `_make_history_with_signals` 为 `n=80`、**唯一信号 bar=60**（量能尖峰在 bar60，已核实），
  新边界 `range(40,70) ∋ 60` → `vb_count==1` **确定性**成立。该测试 docstring 仍提 `m.timestamp ==
  last_bar_ts` 去重（向量化 `_eval` 已无此过滤，属既有陈旧 docstring）——与 C1 同改文件，顺手订正。
- `test_evaluate_baseline_outcomes_count_ge_signals`（`len(base) >= len(sig)`）：两者同收缩，关系不变。
- **服务层真引擎集成测试**（`tests/test_signal_backtest_service.py::test_run_interval_5m_real_eval_offline`
  与 `test_run_interval_1d_uses_daily_path`）：未 mock `evaluate_*`、跑真 `_eval`。其 `_minute_df` 为
  完全平价 fixture（high/low/close 恒定）→ 所有 baseline outcome 恒为 `expired`、stats 为空，故 C1 的
  尾部收缩对其断言（仅 `processed`/`interval`）**不可见**、不会变红；但也因此**不提供 C1 计数回归守卫**
  （守卫由 §5.1 T1 绝对计数 + §5.2 oracle 等价承担）。本特性不改这两个测试，仅在交付说明登记其盲性。

### 5.6 链路A 核验（不改码）

实现阶段重跑核验 §0.4：确认 `backtest_service.py:185-204` 下传 start/end，且 crypto limit-bounded +
引擎 `[:eval_days]` 截断成立。仅在 `docs/intraday-backtest.md` 登记「end_date 右边界已闭合」。

### 5.7 cn band 在线核验（I3 前置，决定最终 band 值）

实现阶段必须对 tushare `stk_mins` 在线核验 `today-N` 的真实单次返回，N 取本表 cn 各值（1m=30/5m=90/
15m=365/1h=730），观测：返回行数、是否优雅截到近端可用子集、是否报错、是否返回错窗（如最旧 N 根）。
据结果校准 `_INTRADAY_MAX_DAYS["cn"]`（可放宽至源真实单次上限，或收紧避免 errors），并把结论写回 §3.1
注与 §3.6 表。**离线门禁（§5.8）只验夹取逻辑（mock 抓参），不验源活体行为**；活体核验走 `-m network`
观测测试（不可达时 skip，不阻断），结论以交付说明登记。沙箱无外网时该项标「未验证」，须有网环境补跑。

### 5.8 门禁

`./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`，含承重 golden）全绿；记录 passed 数相对
3766 基线的增量。承重 golden（`test_golden_signal_equiv_oracle_multimarket` / `test_golden_baseline_unchanged`）
两处 oracle 已随 §5.2 同步 → 必须复跑确认其转绿（这是 B1 的直接回归证据）。

---

## 6. 文件清单

- `src/services/signal_backtest.py`：C1 循环上界 + docstring。
- `src/services/signal_backtest_service.py`：C2 band 常量、`_minute_fetch_days` 签名、
  `_minute_fetch_start_date`、`_load_bars` 签名与调用、`run` 传 market、`datetime` 导入。
- `tests/test_signal_eval_vectorization.py`：**两处** oracle 镜像 `n-horizon`（line 327 信号 oracle +
  line 362 baseline 内联参照；line 392 不动）。
- `tests/test_signal_backtest.py`：C1 截尾回归（§5.1 T1 绝对计数 / T2 末根满 horizon / T3 因果）+
  既有断言复核 + 顺手订正 `test_evaluate_signal_outcomes_exact_dedup_count` 陈旧 docstring。
- `tests/test_signal_backtest_service.py`：C2 helper 单测（`_minute_fetch_days`/`_minute_fetch_start_date`，
  含 us/cn 各 interval band）+ `_load_bars` mock 抓参（monkeypatch 模块级 `date`）。
- `docs/signal-credibility.md`：§5.2 续记 C1 行为变更 + 非 crypto 深度说明。
- `docs/intraday-backtest.md`：登记链路A `end_date 右边界`已闭合（核验结论）**+ C2 非 crypto 分钟
  start_date 深度锚定行为变更**（美股/A股分钟数据深度小节；cn band 待 tushare 在线核验校准）。
- `docs/CHANGELOG.md`：`[Unreleased]` 扁平条目（修复/改进/文档/测试）。

---

## 7. 风险与回滚

- **crypto 字节级不变**（§3.4）：分钟链路A/链路B crypto 路径与日线无回归面。
- **日线链路B 变更小且文档化**：丢最近 `horizon-1` 根欠龄信号，方向为消除偏差。
- **美股 band 夹取**：依赖 yfinance 文档上限常量；若 yfinance 上限调整，仅需改 `_US_INTRADAY_MAX_DAYS`。
- **回滚**：纯代码改动，无 schema/数据迁移，revert 对应 commits 即恢复；signal_stats 重跑
  `--signal-backtest` 即回旧值。

---

## 8. 不做（YAGNI / 范围外）

- 链路A 代码改动（已闭合，仅核验+文档）。
- fetcher 能力声明 `max_intraday_history_days`（D3 已否决）。
- `_load_bars` 对 crypto 传 start_date（保字节级不变，刻意不做）。
- 其他 deferred 项：`intraday_data` capability 显式声明、A股不对称印花税、qfq 复权基准漂移、
  VPS 阈值分钟标定、`_anchored_vwap_signals_rows` O(B·n)、yfinance 类股日线符号缺陷——均独立 spec。

---

## 9. 对抗式审查可追溯（2026-06-26，spec v1→v2）

4 视角并行读真实代码取证 + 对每条 Blocker/Important 独立对抗复核（含参数化复现）。结论与处置：

- **B1（Blocker，确认+复现）** §5.2/§6 曾把 `test_signal_eval_vectorization.py:362` 误判为
  「compute_signals oracle，不动」，实为 `test_golden_baseline_unchanged` 的内联 baseline 参照
  oracle（`range(40,n-1)`）。C1 后不改则该承重 golden 必红（n=160：119 vs 110）。→ §5.2/§6 已订正：
  line 327 与 line 362 两处同步改 `n-horizon`，仅 line 392 不动。
- **I1（Important，确认+复现）** 原 T1/T2 差分计数对上界恒定偏移 c（off-by-one）免疫（c=+1 仍 PASS 却
  重引截尾）。→ §5.1 改为**绝对计数**锁边界（含 (50,10)/(51,10) 边界例），并加 fixture 末注。
- **I2（Important，确认）** §0.2「丢慢赢家、留快输家」是 cherry-pick：`classify_triple_barrier` 对称，
  截短窗对称丢弃所有慢解析者（慢赢+慢输），偏置方向先验不可定。→ §0.2 已改述。
- **I3（Important，partially_confirmed→已决策）** cn 深窗只对 us 设 band，但 tushare 真正消费
  start_date（days 是 no-op，旧安全性不迁移），cn 5m/1m 深窗对 tushare 单次上限的越界行为未验证，
  可能恶化为 errors。→ 决策「推广 band 表 + cn 保守夹取」：§3.1 `_INTRADAY_MAX_DAYS` 含 cn 保守值，
  §5.7 加 tushare 在线核验前置以校准。
- **M1（Minor）** today 注入只到 helper，`_load_bars` 不透传。→ §3.2/§5.4 改 monkeypatch 模块级 `date`
  并收窄措辞。
- **M2（Nit）** us 1m fail-closed 依赖 yfinance 跨模块不变量。→ band 表加 us `1m:7` 自洽。
- **M3（Minor）** docs/intraday-backtest.md 应同记 C2 非 crypto 深度行为变更。→ §6 已加。
- **已确认 spec 假设成立**：价位不依赖 swing pivot（T1 可构造，需 OHLC 振幅）；
  `derive_price_levels_series` 因果（T2 前提成立）；`_make_history_with_signals` 唯一信号 bar=60（vb_count
  确定性成立）；顺带发现该测试陈旧 docstring（§5.5/§6 已纳入顺手订正）。
- **驳回（1，已对抗排除）** 「`_load_bars` 签名变更无 call-site 守卫」：漏传 market→TypeError→被吞→
  processed=0→既有测试失败能兜住，且 §5.4 已在计划内未 defer（发现自相矛盾）。
