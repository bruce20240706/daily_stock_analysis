# 美股盘中/分钟级回测 设计 Spec

> 日期：2026-06-24
> 阶段：盘中/分钟级回测 epic 第三市场(crypto MVP → A股 → **美股**)
> 前置：crypto MVP(merge 66cf4166)、A股(merge 41f6e450)已落地于 main。

## 1. 目标与范围

把已落地的分钟回测扩展到**美股个股**,复用同一引擎/服务/API/Web/CLI,仅做市场化适配。

- **In scope**:美股个股(`is_us_stock_code`,1–5 大写字母 + 可选 `.X` 后缀如 `BRK.B`)。
- **Out of scope**:美股指数(SPX/DJI 等,无 operation_advice、非回测候选);港股;盘前盘后时段;扩展 interval 词表(不加 30m)。
- **硬约束**:crypto / A股 / 日线路径**行为字节级不变**;`interval` 默认 `1d` 即等于现状;**本阶段零新增配置**(复用现有 intraday 配置与免 key 的 yfinance);`interval` 词表统一 `{1d,1m,5m,15m,1h}`,**API/CLI/Web allow-list 零改动**。

## 2. 决策记录(brainstorm 定稿)

- **D1 数据源**:**yfinance 单一免费源(MVP)**。yfinance 免 key、已在仓、唯一可批量回测的免费分钟源。AlphaVantage(需 key、免费 ~25 请求/天)对批量回测不可行,**不纳入本阶段**(留待后续可选深度兜底)。
- **D2 超窗处理**:yfinance 免费分钟仅近期可得(见 §5);超出窗口的老 `analysis_date` 取数失败 → **优雅降级为 `insufficient_data`**(复用现有路径,不加新逻辑)+ 文档说明各 interval 可用 band。**美股 1m 例外**:因 7d 历史 + 8d 单请求上限与回测窗口恒冲突,**fail-closed**(`NotImplementedError`,不发请求,见 §4.3.1),而非每次发起必失败的网络调用。
- **D3 方案**:**市场参数化复用**(中心查表 + `market_of` 加 `us` 分支 + 新增 `YfinanceFetcher.get_intraday_data`),不造平行实现。

## 3. 架构

沿用 crypto/A股 既有装配:`interval` 贯穿 `BacktestService.run_backtest`;行隔离走 `engine_version` TAG(`v1`/`v1-5m`/…,美股 leverage 恒 1 → 无 `-xN`);落库 `bar_interval` + `first_hit_bar_index`;入场价 = 日线收盘,窗口从次日起按 `window_bar_cnt` 前向切片(分钟流即交易日历)。美股仅在四处接入市场判定与一个新数据源入口。

## 4. 详细设计

### 4.1 helper 市场化(`src/core/intraday_backtest.py`)

`MARKET_TRADING_MINUTES` 增 `"us": 390`(常规时段 09:30–16:00 ET = 6.5h = 390 分钟,排除盘前盘后,与 A股 排除午休同理)。

**`bars_per_day` 由 floor 改为 ceil**(`-(-minutes // interval_min)`),计入会话末尾的半根 bar——这是数据源实际发出的 bar 数(yfinance 美股 1h 返回 09:30…15:30 共 7 根,末根 15:30–16:00 为半根)。**仅影响有余数的 (市场,粒度)**:当前唯一是 us 1h(floor 6 → ceil 7);crypto(1440)/A股(240)所有粒度均整除,ceil==floor 值不变,**既有 helper 测试不受影响**。

| interval | us bars_per_day | 说明 |
|----------|-----------------|------|
| `1m`  | 390 | **美股 1m 实质不支持,见 §4.3/§5** |
| `5m`  | 78  | 390/5 整除 |
| `15m` | 26  | 390/15 整除 |
| `1h`  | 7   | ceil(390/60)=7,匹配 yfinance 实际 7 根/日(末根半根) |

`derive_window_bar_count(N, interval, market)` 不变(已支持 market 形参)。

> **改动面提示**:`bars_per_day` 是 A股 阶段落地的共享 helper;本阶段把 `//` 改 ceil 属对该 helper 的修改,需同步既有 `tests/test_intraday_backtest_helpers.py`(crypto/cn 断言值不变,新增 us 含 1h=7 的断言),并确认 crypto 分钟回测窗口逐字不变(所有 crypto 粒度整除)。

### 4.2 市场检测(`data_provider/base.py`)

`market_of` 增 `us` 分支(顺序:crypto/perp → `crypto`;a_share → `cn`;**us_stock → `us`**;其余抛 `ValueError`):

```python
def market_of(code: str) -> str:
    if is_crypto_code(code) or is_perp_code(code):
        return "crypto"
    if is_a_share_code(code):
        return "cn"
    if is_us_stock_code(code):
        return "us"
    raise ValueError(f"无分钟市场归类: {code!r}")
```

复用既有 `is_us_stock_code`(`data_provider/us_index_mapping.py`:1–5 大写字母 + 可选 `.X`,排除美股指数与 crypto)。数字码的 A股/HK、含 `/` 的 crypto 天然不与美股字母码冲突。

### 4.3 数据源:`YfinanceFetcher.get_intraday_data`(`data_provider/yfinance_fetcher.py`)

新增分钟入口(免 key)。**`1m` 故意不纳入**(fail-closed,见下 §4.3.1):

```python
# 1m 不纳入:yfinance 1m 历史仅 7 天且单请求 ≤8 天,与回测的 min_age + 窗口缓冲恒冲突(§4.3.1)
_YF_INTERVAL = {"5m": "5m", "15m": "15m", "1h": "60m"}

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception_type((ConnectionError, TimeoutError)),
       before_sleep=before_sleep_log(logger, logging.WARNING))
def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
    yf_interval = _YF_INTERVAL.get(interval)
    if yf_interval is None:
        raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}（美股 1m 不支持）")
    import yfinance as yf
    # 美股类股(BRK.B/BF.B)：_convert_stock_code 原样返回带点符号,但 Yahoo 用连字符 → '.'→'-'
    yf_code = self._convert_stock_code(stock_code)
    if is_us_stock_code(stock_code):
        yf_code = yf_code.replace(".", "-")           # BRK.B → BRK-B
    df = yf.download(tickers=yf_code, start=start_date, end=end_date,
                     interval=yf_interval, auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise DataFetchError(f"[{self.name}] {stock_code} 无分钟数据(interval={interval})")
    # 显式标准化(不走日线 _normalize_data——后者产 'date' 列且注入 amount=volume×close 估算):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)   # ('Close','AAPL') → 'Close'
    df = df.reset_index()                              # 索引(Datetime)落为列
    df = df.rename(columns={"Datetime": "datetime", "Date": "datetime",
                            "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Volume": "volume"})
    # 时区:yfinance 分钟为 tz-aware(美东)→ 去时区保留美东墙钟,与 crypto/A股 的 naive 对齐
    # (用 .dt.tz 判定,避免 pandas≥2.1 已弃用的 is_datetime64tz_dtype)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if getattr(df["datetime"].dt, "tz", None) is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    from .intraday_normalize import normalize_intraday_df
    return normalize_intraday_df(df, stock_code)        # amount 缺省 → 填 None
```

- 复用 `_convert_stock_code` 与共享 `normalize_intraday_df`(后者约定入参已含 `datetime` 列)。
- **时区(#3)**:yfinance 分钟索引带 `America/New_York` 时区;在 fetcher 边界 `tz_localize(None)` 去时区(保留美东墙钟 09:30 等),与 crypto(naive UTC)/A股(naive 本地)对齐,避免下游混入 tz-aware Timestamp。
- **重试(#5)**:沿用日线入口的 `@retry`(ConnectionError/TimeoutError 指数退避),缓解 Yahoo 抓取抖动/限频;批量可靠性 caveat 见 §5。
- 声明 `intraday_data` capability:覆写 `get_intraday_data` 即被门面 override 过滤保留;`is_available` 沿用现有(yfinance 恒可用)。
- 门面 `DataFetcherManager._intraday_fetchers_for`:`market="us"` → `_filter_daily_fetchers_for_market(us)`(保留 Yfinance/Longbridge/Finnhub/AlphaVantage)→ capability + override 过滤后**仅 yfinance**(单源,无需排序;Longbridge/Finnhub/AlphaVantage 未覆写 `get_intraday_data` → BaseFetcher 默认抛 NotImplementedError → 被 override 过滤剔除。已对照 main 核实仅 crypto_base/tushare/akshare/base 覆写)。

> **`_convert_stock_code` 复用边界**:它对非美股返回带点后缀(如 `600519.SS`),故 `'.'→'-'` 只在 `is_us_stock_code(stock_code)` 为真时施加(分钟入口实际只被 us 触达,此处亦防御)。日线路径对类股的同一符号问题为既有潜在缺陷,**不在本阶段修**(避免改动日线行为),仅在本入口规避。

### 4.3.1 美股 1m 为何 fail-closed(#1)

回测候选须 `analysis_date` 够老(forward 窗口走完,受 `min_age_days` 默认 14 门控),而 yfinance `1m` **历史仅近 7 天**且**单请求区间 ≤8 天**;§4.4 的 `end_date` 缓冲下限又是 15 天(`max(N*2, N*3//2+14)`,N=1→15)。两条硬限与回测窗口恒冲突 → 美股 1m **永远取不到**。因此与 A股 akshare 1m 同手法 **fail-closed**:`1m` 不在 `_YF_INTERVAL` → 抛 `NotImplementedError` → 门面无可用源 → service 落 `insufficient_data`,**不发起无谓网络请求**。`1m` 仍在全局词表 `{1d,1m,5m,15m,1h}`(供 crypto/A股),仅美股不支持。

### 4.4 service 分钟分支(`src/services/backtest_service.py`)

- gate 增 `or is_us_stock_code(analysis.code)`(crypto/perp/a_share/us_stock 之外仍 `skipped_unsupported`)。
- `market = market_of(analysis.code)` 现可返回 `us`;`window_bar_cnt = derive_window_bar_count(N, interval, market)`。
- `end_date` 缓冲推广:`if market == "crypto": offset = N else: offset = max(N*2, N*3//2 + 14)`(cn/us 同走日历缓冲覆盖周末/节假日;美股无超长假,该上界足够)。
- 其余不变:入场价=日线收盘、窗口 `analysis_date+1` 起、落库 `engine_version=v1-<interval>`(无 `-xN`)、`bar_interval`、`first_hit_bar_index`、`eval_window_days`=交易日数。

### 4.5 零配置

不新增任何配置项;`interval` 默认 `1d`、成本默认 0、调度默认 false → 不配置即等于现状。yfinance 免 key,不需任何凭据。

### 4.6 降级与超窗(D2)

超出 yfinance 可得窗口的老 `analysis_date` → `yf.download` 返回空 → `DataFetchError` → 门面聚合抛出 → service 捕获落 `insufficient_data`(与 A股 取数失败同路径)。**不新增预判/跳过逻辑**;文档写清各 interval 可用 band 与推荐用法。

### 4.7 测试

- 单测:
  - `market_of`/`is_us_stock_code` 美股归类(AAPL/BRK.B→us;SPX/600519/HK00700/`BTC/USDT` 排除)。
  - `bars_per_day`:us 5m=78/15m=26/**1h=7(ceil)**;回归断言 crypto/cn 值不变(ceil==floor)。
  - `YfinanceFetcher.get_intraday_data`(mock `yf.download` 返回 MultiIndex + tz-aware DatetimeIndex):验证 interval 映射 `1h→60m`;MultiIndex 拍平 + `Datetime`→`datetime` 重命名;**输出 datetime 为 tz-naive**(美东墙钟);**`BRK.B`→`yf.download` 收到 `BRK-B`**(捕获 tickers 参数);空结果抛 `DataFetchError`;**`1m` 抛 `NotImplementedError`(fail-closed)**;不支持 interval 抛 `NotImplementedError`。
  - 门面 us 路由(仅 yfinance、非美股拒绝、override 过滤剔除 Longbridge/Finnhub/AlphaVantage)。
  - service us 放行 + 市场化窗口(5m,N=10 → window_bar_cnt=780;1h,N=10 → 70)+ end_date 缓冲(us 走 `max(N*2,N*3//2+14)`)。
- crypto/A股/日线零回归(全量 `pytest -m "not network"`)。
- `-m network` 观测(非阻断):`YfinanceFetcher().get_intraday_data("AAPL","5m",start=近5天)` 真拉,断言 schema/升序唯一/5m 间距为主;连接异常带重试后 skip(沿用 A股 网络测试的 `_fetch_or_skip` 重试手法)。

## 5. 可用 band 与限制(文档)

- 回测需 `analysis_date` **既够老**(forward 窗口已走完,受 `min_age_days` 门控)**又够新**(在 yfinance 窗口内):
  - `5m`/`15m`:≈ `[now-60d, now-窗口]`
  - `1h`:≈ `[now-730d, now-窗口]`(美股最宽可用 band,推荐粒度)
  - `1m`:**美股不支持**(yfinance 7d 历史 + 8d 单请求上限与回测 min_age/窗口恒冲突,fail-closed,见 §4.3.1)→ 抛 `NotImplementedError` → `insufficient_data`,不发请求。
- 批量可靠性:yfinance 抓取 Yahoo,大批量(数百候选)可能限频/瞬断;入口已包 `@retry` 指数退避,仍可能偶发失败 → 该条 `insufficient_data`/error 计数(与 A股 akshare 限频 caveat 同)。美股分钟回测建议小批量/手动触发。
- 成本:沿用 crypto 对称 `fee/slippage`(默认 0);美股无印花税(仅极小 SEC/TAF 费),不单独建模。
- 复权:yfinance `auto_adjust=True`;与库内日线收盘入场价的复权基准可能不同步(同 A股 §10.5 caveat),窗口短/无公司行动时可忽略。
- 类股(BRK.B 等):分钟入口已做 `'.'→'-'` 符号映射(§4.3);日线路径同名问题为既有缺陷,不在本阶段修。

## 6. 验证矩阵

- 后端:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`(venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`)。
- 影响面:`data_provider/`(yfinance/base)、`src/core/intraday_backtest.py`、`src/services/backtest_service.py`、`docs/`、`tests/`。无 API/Schema/Web/Desktop 改动(allow-list 与字段不变)。

## 7. 自审清单(实现期对齐)

- yfinance 分钟返回的真实列结构(MultiIndex 层级、`Datetime` 索引名、tz)以实际 yfinance 版本为准,实现时核对 §4.3 拍平/重命名/去时区步骤;若版本差异(如索引名非 `Datetime`)按实际调整 rename。
- `bars_per_day` 改 ceil 后,务必跑 `tests/test_intraday_backtest_helpers.py` 确认 crypto/cn 既有断言值不变(均整除),仅 us 1h=7 为新值;确认 crypto 分钟回测窗口逐字不变。
- `'.'→'-'` 类股映射仅施于 `is_us_stock_code` 为真者(避免误伤 `600519.SS` 等带点后缀);日线路径同名缺陷不在本阶段修。
- end_date 缓冲对 us 复用 cn 公式(上界足够,美股无超长假),如后续发现偏差再收紧。
- 美股 1m fail-closed(§4.3.1):实现需有测试锁定 `1m` 抛 `NotImplementedError`,防止后续误把 1m 加回 `_YF_INTERVAL`。
