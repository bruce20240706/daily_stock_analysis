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
- **D2 超窗处理**:yfinance 免费分钟仅近期可得(见 §5);超出窗口的老 `analysis_date` 取数失败 → **优雅降级为 `insufficient_data`**(复用现有路径,不加新逻辑)+ 文档说明各 interval 可用 band。
- **D3 方案**:**市场参数化复用**(中心查表 + `market_of` 加 `us` 分支 + 新增 `YfinanceFetcher.get_intraday_data`),不造平行实现。

## 3. 架构

沿用 crypto/A股 既有装配:`interval` 贯穿 `BacktestService.run_backtest`;行隔离走 `engine_version` TAG(`v1`/`v1-5m`/…,美股 leverage 恒 1 → 无 `-xN`);落库 `bar_interval` + `first_hit_bar_index`;入场价 = 日线收盘,窗口从次日起按 `window_bar_cnt` 前向切片(分钟流即交易日历)。美股仅在四处接入市场判定与一个新数据源入口。

## 4. 详细设计

### 4.1 helper 市场化(`src/core/intraday_backtest.py`)

`MARKET_TRADING_MINUTES` 增 `"us": 390`(常规时段 09:30–16:00 ET = 6.5h = 390 分钟,排除盘前盘后,与 A股 排除午休同理)。

`bars_per_day(interval, market)` 现有 floor 公式 `MARKET_TRADING_MINUTES[market] // INTRADAY_INTERVAL_MINUTES[interval]`:

| interval | us bars_per_day |
|----------|-----------------|
| `1m`  | 390 |
| `5m`  | 78  |
| `15m` | 26  |
| `1h`  | 6（注：390%60=30，yfinance 美股 1h 实际约 7 根/日，末根 15:30–16:00 为半根；采用 floor=6 满整点定义，1h 窗口偏短约 1 根/日，文档化，不特例化） |

`derive_window_bar_count(N, interval, market)` 不变(已支持 market 形参)。

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

新增分钟入口(免 key):

```python
_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m"}

def get_intraday_data(self, stock_code, interval, start_date=None, end_date=None, days=30):
    yf_interval = _YF_INTERVAL.get(interval)
    if yf_interval is None:
        raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}")
    import yfinance as yf
    yf_code = self._convert_stock_code(stock_code)
    df = yf.download(tickers=yf_code, start=start_date, end=end_date,
                     interval=yf_interval, auto_adjust=True, progress=False)
    # MultiIndex 列拍平 + DatetimeIndex → datetime 列(实现期对齐实际返回结构)
    if df is None or df.empty:
        raise DataFetchError(f"[{self.name}] {stock_code} 无分钟数据(interval={interval})")
    df = <拍平列、reset_index 产出 datetime 列、列名小写 open/high/low/close/volume>
    from .intraday_normalize import normalize_intraday_df
    return normalize_intraday_df(df, stock_code)
```

- 复用 `_convert_stock_code` 与共享 `normalize_intraday_df`(约定入参已含 `datetime` 列 → 先从 yfinance 的 DatetimeIndex 构建)。
- 声明 `intraday_data` capability:与 crypto/A股 一致——覆写 `get_intraday_data` 即被门面 override 过滤保留;`is_available` 沿用现有(yfinance 恒可用)。
- 门面 `DataFetcherManager._intraday_fetchers_for`:`market="us"` → `_filter_daily_fetchers_for_market(us)`(保留 Yfinance/Longbridge/Finnhub/AlphaVantage)→ capability + override 过滤后**仅 yfinance**(单源,无需排序;Longbridge/Finnhub/AlphaVantage 未覆写 → BaseFetcher 默认 NotImplementedError → 被 override 过滤剔除)。

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

- 单测:`market_of`/`is_us_stock_code` 美股归类(AAPL/BRK.B→us;SPX/600519/HK00700/BTC-USDT 排除);`YfinanceFetcher.get_intraday_data`(mock `yf.download` 返回 MultiIndex/DatetimeIndex,验证 interval 映射 `1h→60m`、列拍平、共享标准化、空结果抛 `DataFetchError`、不支持 interval 抛 `NotImplementedError`);门面 us 路由(仅 yfinance、非美股拒绝、override 过滤剔除其它美股日线源);service us 放行 + 市场化窗口(5m,N=10 → window_bar_cnt=780)+ end_date 缓冲。
- crypto/A股/日线零回归(全量 `pytest -m "not network"`)。
- `-m network` 观测(非阻断):`YfinanceFetcher().get_intraday_data("AAPL","5m",start=近5天)` 真拉,断言 schema/升序唯一/5m 间距为主;连接异常带重试后 skip(沿用 A股 网络测试的 `_fetch_or_skip` 重试手法)。

## 5. 可用 band 与限制(文档)

- 回测需 `analysis_date` **既够老**(forward 窗口已走完,受 `min_age_days` 门控)**又够新**(在 yfinance 窗口内):
  - `5m`/`15m`:≈ `[now-60d, now-窗口]`
  - `1h`:≈ `[now-730d, now-窗口]`(美股最宽可用 band)
  - `1m`:≈仅极近期 + 极小窗口(7d 基本放不下多日 forward 窗口);文档明示美股 1m 仅适合 1–2 日小窗,常规窗口将落 `insufficient_data`(非错数据,可正常使用,只是无样本)。
- 成本:沿用 crypto 对称 `fee/slippage`(默认 0);美股无印花税(仅极小 SEC/TAF 费),不单独建模。
- 复权:yfinance `auto_adjust=True`;与库内日线收盘入场价的复权基准可能不同步(同 A股 §10.5 caveat),窗口短/无公司行动时可忽略。

## 6. 验证矩阵

- 后端:`./scripts/ci_gate.sh` + `python -m pytest -m "not network"`(venv `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`)。
- 影响面:`data_provider/`(yfinance/base)、`src/core/intraday_backtest.py`、`src/services/backtest_service.py`、`docs/`、`tests/`。无 API/Schema/Web/Desktop 改动(allow-list 与字段不变)。

## 7. 自审清单(实现期对齐)

- yfinance 分钟返回的真实列结构(MultiIndex 层级、DatetimeIndex 名、时区)以实际 yfinance 版本为准,实现时核对 `_normalize_data` 现有拍平逻辑并复用其手法。
- `bars_per_day(1h,"us")=6` 的半根偏差以文档化接受,不特例化。
- end_date 缓冲对 us 复用 cn 公式(上界足够),如后续发现美股节假日导致偏差再收紧。
