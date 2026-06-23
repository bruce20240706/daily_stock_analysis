# A股 盘中/分钟级回测 设计(扩展 crypto MVP)

- 日期:2026-06-23
- 状态:设计已获批,待写实现计划(writing-plans)
- 前序:`docs/superpowers/specs/2026-06-23-intraday-backtest-design.md`(crypto MVP,已合入 main)
- 范围:把分钟回测能力从 crypto 扩到 **A股(沪深为主,北交所 best-effort)**,复用同一引擎/服务/API/Web/CLI 链路,仅做"市场化"适配。对 crypto 与日线既有行为**只追加、不修改**。

## 0. 目标与非目标

### 目标
让日线 AI 操作建议的止盈/止损/方向结论可在 **A股分钟价格路径**上前向验证,复用 crypto MVP 的 bar-agnostic 引擎、日线候选、engine_version 行隔离、API/Web/CLI 呈现,仅把写死 crypto 的口径(`bars_per_day` 1440)与市场门控做参数化扩展。

### 非目标(YAGNI / out-of-scope)
- 美股分钟(后续阶段)。
- 港股分钟(无可纳入分钟源)。
- A股 30m/60min 独立档(统一词表 `1h` 已覆盖 60min)。
- A股不对称交易成本精确建模(印花税卖出 0.05%、佣金、过户费)→ 留待迭代;本阶段沿用对称 `fee_bps`/`slippage_bps`,默认 0(理想化)。
- 北交所强保证(分钟源不确定 → 无源时优雅 `insufficient_data`)。
- 交易日历日期算术做窗口(Q2 决定用"分钟流即日历"避免 exchange-calendars 耦合)。

## 1. 锁定决策(brainstorm 收敛)

| # | 决策 | 取值 |
| --- | --- | --- |
| Q1 | 数据源 | Tushare 主源(HTTP `stk_mins`,需 token+积分)+ akshare 免费兜底(`stock_zh_a_hist_min_em`,免 token);均实现 `intraday_data` capability,门面 failover |
| Q2 | 窗口解析 | 复用 crypto"分钟流即交易日历":取 analysis_date 之后 session bar,`window_bar_cnt = eval交易日 × bars_per_day(cn,interval)` 前向切片;入场=日线收盘;**无 exchange-calendars 窗口日期算术** |
| Q3 | interval 词表 | 统一 `{1d,1m,5m,15m,1h}` 不变;provider 内部映射(Tushare `1m→1min/5m→5min/15m→15min/1h→60min`;akshare period `1/5/15/60`);API/CLI/Web 零改动 |
| Q4 | 门控/检测 | 新增 `is_a_share_code`(SH 600/601/603/605/688 + SZ 000/001/002/003/300/301 + `is_bse_code`);分钟门控放行 `crypto|perp|a_share`;HK/US 仍 raise;crypto 门控不变 |
| 方案 | 市场抽象 | 市场参数化 helper + 中心查表(`MARKET_TRADING_MINUTES = {"crypto":1440,"cn":240}`) |

## 2. 关键取证结论(支撑设计的代码事实)

- **`bars_per_day` 写死 crypto 1440**:`src/core/intraday_backtest.py` `_MINUTES_PER_DAY = 1440`,`bars_per_day(interval) = 1440 // INTRADAY_INTERVAL_MINUTES[interval]`;`derive_window_bar_count(eval_window_days, interval) = eval_window_days × bars_per_day(interval)`。A股 240 交易分钟/日 → 必须市场化。
- **分钟门控两处**:`DataFetcherManager.get_intraday_data`(`data_provider/base.py`)与 `BacktestService.run_backtest`(`src/services/backtest_service.py`)均门控 `is_crypto_code | is_perp_code`,非此 → 前者 `DataFetchError`、后者 `skipped_unsupported`。
- **Tushare**:`data_provider/tushare_fetcher.py` 用自建 `_TushareHttpClient`(直连 HTTP POST `api.tushare.pro`,`api_name`+`token`+`params`),**非 ts SDK** → 分钟走 HTTP `api_name='stk_mins'`(SDK `pro_bar(freq=)` 的底层)。token=`config.tushare_token`(env `TUSHARE_TOKEN`);限频 `_check_rate_limit`(80/min,500/天免费);配额/权限不足抛 `RateLimitError`。symbol 映射 `_convert_stock_code('600519') → '600519.SH'` 已存在(SH 600/601/603/605/688;SZ 000/001/002/003/300/301;BSE 8/4/92→.BJ)。
- **akshare**:仓库已有 `AkshareFetcher`(cn/hk 日线);A股分钟用其 `stock_zh_a_hist_min_em(symbol, period, start_date, end_date, adjust)`(period∈{1,5,15,30,60},免 token,中文列)。
- **市场路由 + capability**:`_DAILY_MARKET_FETCHER_SUPPORT` 含 `{"AkshareFetcher":{"cn","hk"}, "TushareFetcher":{"cn","hk"}, ...}`;`_filter_daily_fetchers_for_market(fetchers, "cn")` + `_filter_fetchers_by_capability(fetchers, "intraday_data")` 是 crypto 分钟已用的路由机制 → A股复用同机制(market="cn")。
- **共享 normalize**:crypto 分钟标准化在 `CryptoExchangeBase._normalize_intraday`(datetime 全精度 + OHLCV + 排序 + pct_chg);A股 fetcher 继承 `BaseFetcher`(非 CryptoExchangeBase)→ 需把该标准化提取为共享 helper 复用,避免平行实现。
- **engine_version tag**:`build_engine_version_tag(base, interval, leverage)`;A股 leverage 恒 1(`perp_only` 仅 perp 触发 L>1)→ A股分钟 tag = `v1-5m` 等(无 `-xN`),与 crypto 现货同形。crypto 与 cn 代码 disjoint(`BTC/USDT` vs `600519`),无需 market 进 tag/唯一键。
- **trading_calendar**:`src/core/trading_calendar.py` 有 XSHG 日历、`is_market_open`、lunch-break/session phase、`MARKET_TIMEZONE['cn']='Asia/Shanghai'`(本设计 Q2 不依赖其做窗口算术,但 A股分钟数据天然只含 session bar)。
- **持久化语义**:crypto 阶段已确立 `BacktestResult.eval_window_days` 存日历/交易日数(非 window_bar_cnt),`first_hit_bar_index` 承载分钟首次命中、`bar_interval` 显式判别 — A股复用,A股的 eval_window_days 即**交易日数**(与日线回测 `get_forward_bars` 限交易日一致)。

## 3. 复用 vs 新增

### 复用(不改语义)
`evaluate_single`、`run_backtest` 主流程、engine_version tag、`bar_interval`/`first_hit_bar_index` 列与迁移、API `interval`、Web interval 选择器、CLI `--backtest-interval`、成本后处理(`apply_round_trip_cost`)、opt-in 调度钩子、`_df_to_bars`、持久化消歧、门面缓存。

### 新增/改造
1. `bars_per_day` / `derive_window_bar_count` 市场化(加 `market` 形参 + `MARKET_TRADING_MINUTES`)。
2. `is_a_share_code(code)` + `market_of(code)`(`data_provider/base.py`)。
3. `TushareFetcher.get_intraday_data`(`stk_mins`)+ `AkshareFetcher.get_intraday_data`(`stock_zh_a_hist_min_em`);均声明 `intraday_data` capability。
4. 门面 `get_intraday_data` 门控放行 `is_a_share_code` + market="cn" 路由。
5. service 分钟门控放行 a_share + 传 `market` 给 `derive_window_bar_count`。
6. **抽取共享 `normalize_intraday_df`**(由 `CryptoExchangeBase._normalize_intraday` 提取),crypto + A股 fetcher 共用。

## 4. 各层设计

### 4.1 helper 市场化(`src/core/intraday_backtest.py`)
```
MARKET_TRADING_MINUTES = {"crypto": 1440, "cn": 240}   # 新增;US 后续加 {"us":390}
def bars_per_day(interval, market="crypto") -> int:
    mins = MARKET_TRADING_MINUTES[market]              # 未知 market 抛 KeyError/ValueError
    return mins // INTRADAY_INTERVAL_MINUTES[interval]  # 未知 interval 仍抛 ValueError
def derive_window_bar_count(eval_window_days, interval, market="crypto") -> int:
    return int(eval_window_days) * bars_per_day(interval, market)
```
- crypto 现有调用走默认 `market="crypto"` → 1440,字节级不变。
- cn:`bars_per_day("5m","cn") = 240//5 = 48`;`derive_window_bar_count(10,"5m","cn") = 480`。
- `validate_interval`/`build_engine_version_tag`/`apply_round_trip_cost` 不变(市场无关)。

### 4.2 市场检测(`data_provider/base.py`)
- `is_a_share_code(code) -> bool`:`normalize_stock_code` 后,SH 前缀(600/601/603/605/688)∪ SZ 前缀(000/001/002/003/300/301)∪ `is_bse_code(code)`;排除 HK 5 位(`_is_hk_market`)、crypto、US。
- `market_of(code) -> str`:`is_crypto_code|is_perp_code → "crypto"`;`is_a_share_code → "cn"`;否则抛/返回 None(分钟路径不该到达)。供 service 传 `derive_window_bar_count`。

### 4.3 数据层
- **共享标准化**:提取 `normalize_intraday_df(df, code)`(datetime 全精度 + 标准 OHLCV 列 + 升序 + pct_chg,**不算技术指标**)到共享位置(如 `data_provider/intraday_normalize.py` 或 base 模块函数);`CryptoExchangeBase._normalize_intraday` 改为调用它(crypto 行为不变),A股 fetcher 复用。
- **TushareFetcher.get_intraday_data(code, interval, start_date=None, end_date=None, days=30)**:freq 映射(`1m→1min/5m→5min/15m→15min/1h→60min`);`ts_code=_convert_stock_code(code)`;`_TushareHttpClient` 调 `api_name='stk_mins'`,params `{ts_code, freq, start_date, end_date}`(datetime 串);复用 `_check_rate_limit`;无 token/积分/配额 → 抛(`RateLimitError`/`DataFetchError`)由门面 failover。声明 capability `intraday_data`。
- **AkshareFetcher.get_intraday_data(...)**:`stock_zh_a_hist_min_em(symbol=6位, period=映射('1'/'5'/'15'/'60'), start_date, end_date, adjust='qfq')`;中文列 → `normalize_intraday_df`;免 token。声明 capability `intraday_data`。
- **门面 `DataFetcherManager.get_intraday_data`**(`data_provider/base.py`):门控改为 `is_crypto_code|is_perp_code|is_a_share_code` 放行(否则 `DataFetchError`);`market = "crypto_perp" if perp else "crypto" if crypto else "cn"`;`_filter_daily_fetchers_for_market(fetchers, market)` + `_filter_fetchers_by_capability(fetchers, "intraday_data")`。**cn 顺序须显式 Tushare 在前、akshare 兜底**——注意默认日线优先级里 akshare 在 Tushare 之前(`Efinance→Akshare→Tushare→...`),故 intraday cn 路由需对过滤后的 fetcher 做显式排序(Tushare 优先),以兑现 Q1"Tushare 主源"。无 token 账号:`TushareFetcher.is_available()`(token 未配 → False)经 `_is_fetcher_available` 直接被跳过 → 干净落到 akshare,无慢失败;有 token 无积分账号:Tushare 在前但 `stk_mins` 失败 → failover akshare(罕见慢路径)。复用 `crypto_intraday_minute_cache_ttl_s` 进程内缓存(键 `(code,interval,days)`,**命名债**:实为通用分钟缓存,跨市场共用)。

### 4.4 service 集成(`src/services/backtest_service.py`)
- 分钟门控:`if intraday and not (is_crypto_code|is_perp_code|is_a_share_code): skipped_unsupported += 1; continue`。
- `market = market_of(analysis.code)`;`window_bar_cnt = derive_window_bar_count(eval_window_days, interval, market)`。
- 取数:`DataFetcherManager().get_intraday_data(code, interval, start_date=(analysis_date+1日历).isoformat(), end_date=(analysis_date+1+ceil(eval_window_days×1.6)+buffer 日历日).isoformat(), days=eval_window_days)`(end 宽余覆盖 N 交易日;实际窗口由 `forward_bars[:window_bar_cnt]` 切片决定)。
- 入场价 `start_price=float(start_daily.close)`、`_df_to_bars`、`EvaluationConfig(eval_window_days=window_bar_cnt)`、落库(eval_window_days 存交易日数、bar_interval=interval、first_hit_bar_index)— 全部不变。
- A股 leverage 恒 1(`perp_only` 门控已限);engine_version tag = `build_engine_version_tag(base, interval, 1)`。

### 4.5 配置
- **本阶段不新增配置**。`TUSHARE_TOKEN` 已存在;akshare 免配;复用现有 intraday 缓存(`CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S`)、成本(`CRYPTO_INTRADAY_BACKTEST_FEE_BPS`/`_SLIPPAGE_BPS`)、调度(`INTRADAY_BACKTEST_ENABLED`/`_SCHEDULE_MINUTES`、`CRYPTO_INTRADAY_BACKTEST_INTERVAL`)。
- **命名债**(记录,不在本阶段改):部分 `CRYPTO_*` 前缀配置实为通用分钟配置;A股不对称印花税成本未建模(默认 0 理想化)。

### 4.6 测试策略(离线确定性优先)
- helper 市场化:`bars_per_day("5m","cn")==48`、`derive_window_bar_count(10,"5m","cn")==480`、crypto 默认 `bars_per_day("5m")==288` 不变;未知 market 抛错。
- `is_a_share_code`/`market_of` 边界:沪(600519/688xxx)、深(000001/300xxx)、北交(8/4/92)、HK 5 位(排除)、crypto(排除)、US(排除)。
- Tushare/akshare `get_intraday_data`(mock HTTP/库调用):freq/period 映射、`normalize_intraday_df` 列与精度、失败抛错→门面 failover(Tushare 失败→akshare 成功)。
- 门面路由:cn 码命中 intraday capability fetcher;非 cn/crypto 抛错。
- service A股分钟:放行、window_bar_cnt 市场化、落库 tag/bar_interval/first_hit_bar_index、eval_window_days 存交易日数。
- 共享 normalize 提取后 crypto 路径回归不变。
- `-m network` 观测(非阻断):akshare 免费真拉沪深 5m(数据/精度/递增);Tushare 仅在 `TUSHARE_TOKEN` 配置且积分足时真拉,否则 skip。
- 全量门禁:`./scripts/ci_gate.sh` + `pytest -m "not network"`。

### 4.7 错误处理与降级
- Tushare 无 token/积分/配额 → 抛 → 门面 failover akshare。
- akshare 失败/无源(如北交无分钟)→ 门面 `DataFetchError` → service 记 `insufficient_data`,不崩批次。
- 非 cn/crypto 传分钟 interval → `skipped_unsupported`。
- 默认 interval=1d / 成本 0 / 调度关 → 不配置即等于现状;A股分钟不可用不影响日线与 crypto。

## 5. 风险与回滚
- **风险**:Tushare 分钟积分门槛(多数免费账号无 → 实际走 akshare);akshare 限频/接口偶变(失败即降级);A股不对称成本(印花税)未建模(默认 0,已标注);北交分钟源不确定(best-effort)。`end` 宽余日历范围估算需保证覆盖 N 交易日(buffer 充足)。
- **回滚**:纯追加(helper 形参带默认、新 fetcher 方法、门控放行、interval 默认 1d)→ 不传分钟即现状;crypto 路径与日线既有行为字节级不变;A股分钟不可用时优雅 `insufficient_data`。整体回滚:revert 本特性提交,不影响已合入的 crypto MVP。

---

附:本 spec 为 writing-plans 的输入。实现以实际代码为准;若取证事实与代码漂移(如 `stk_mins` 实际字段、akshare 函数签名、cn fetcher 优先级),优先信任代码并顺手订正本 spec。
