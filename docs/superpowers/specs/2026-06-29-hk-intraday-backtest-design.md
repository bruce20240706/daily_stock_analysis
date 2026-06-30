# 港股(HK)分钟级回测(MVP,链路 A+B)设计

- 主题：`hk-intraday-backtest`
- 日期：2026-06-29
- 状态：设计定稿(经对抗式审查 4 视角读真实代码+离线探签名核验:4-change 设计 0 Blocker;§6/§7 测试完整性补齐 6 处必改测试、§0.4/§4/§5.1 精度订正,见 §11)，待落实现计划
- 范围：把盘中/分钟级回测扩展到**港股个股**(链路 A 操作建议/PnL + 链路 B 信号可信度);双源 akshare 东财(主)+ yfinance(兜底);**MVP 仅路由+数据**,成本走现有跨市场 fee/slip(默认 0),HK 双边印花税另立 follow-up。
- 关联：承接「盘中/分钟级回测」epic(2026-06-23 crypto / 06-24 A股+美股+链路B / 06-29 印花税+cash 成本门控);**完全镜像 cn/us 接入方式**,仅市场化适配。

---

## 0. 背景与现状(已读真实代码核验)

### 0.1 现有市场骨架

- `MARKET_TRADING_MINUTES = {"crypto":1440,"cn":240,"us":390}`(`src/core/intraday_backtest.py:18`),无 hk → `bars_per_day(interval,"hk")` 会 KeyError。
- `bars_per_day` 用 ceil(`-(-minutes//bar)`,`:42`):未知 market 抛 KeyError;ceil 仅影响有余数档(注释当前只列 us 1h=7)。
- `market_of(code)`(`data_provider/base.py:313-321`):仅 crypto/cn/us,其余 `raise ValueError`;**用 `is_crypto_code/is_perp_code/is_a_share_code/is_us_stock_code` 直接判定**(同模块已有私有 `_is_hk_market`,`:202-219`,匹配 5 位数字/HK 前缀/.HK 后缀)。

### 0.2 HK 当前被系统性排除(4 处硬拦截 + 2 处缺表)

1. `market_of("HK…")` → ValueError(`base.py:321`)。
2. 门面 `DataFetcherManager.get_intraday_data` guard(`base.py:1576-1578`):`if not (crypto/perp/a_share/us_stock): raise DataFetchError("…仅 crypto / A股 / 美股")`。
3. `_intraday_fetchers_for`(`base.py:1524-1549`):`else: return []`(hk 落空);且 `if market != "us": 排除 YfinanceFetcher`(`:1544`)。
4. 链路 A gate(`src/services/backtest_service.py:115-120`):or-链仅 crypto/perp/a_share/us_stock,HK 计入 `skipped_unsupported`。
5. `MARKET_TRADING_MINUTES` 无 hk(§0.1)。
6. 链路 B `_INTRADAY_MAX_DAYS = {"us":…,"cn":…}`(`signal_backtest_service.py:47-50`)无 hk;`run()` docstring 标「HK/指数无分钟取数,单股计入 errors」(`:117`)。

### 0.3 已就位的基础(无需改)

- **港股交易日历**:`trading_calendar.py` 已注册 `XHKG`/`Asia/Hong_Kong`/午休(`session_has_break`)/收盘竞价窗口(`_CLOSING_AUCTION_WINDOW_MINUTES["hk"]=10`);`get_market_for_stock` 已返回 "hk"。
- **港股日线**全源支持(`_DAILY_MARKET_FETCHER_SUPPORT`:akshare/tushare/yfinance/longbridge 均含 hk;`base.py:703-710`)。
- `is_hk_stock_code`(公开 API,`akshare_fetcher.py:196-208`,委托 `_is_hk_code`;经 `data_provider/__init__.py` 暴露)。
- 成本枢纽 `apply_round_trip_cost(...,sell_side_bps)`、cash 成本门控(`simulated_entry_price is not None`)、方向 long-only、perp/leverage、`end_date` 缓冲(else=`max(N*2,N*3//2+14)`)、`VPSConfig.for_market`(非 crypto 走默认)——HK 全部自然兼容。

### 0.4 数据源可行性(已核验)

- **akshare `stock_hk_hist_min_em`**(东财,免 key;**真实签名** `(symbol, period, adjust, start_date, end_date)`,C3 全程关键字传参不受顺序影响):period 取值 `{1,5,15,30,60}`;5m/15m/30m/60m 走 kline(`beg=0`/`end=20500000` 服务端**全量**、`start_date`/`end_date` 仅 client 端 DatetimeIndex 切片)、1m 走 trends2(`ndays=5` 硬编码限定返回上限,start/end 仅本地过滤)。symbol=5 位数字。**kline 路径返回 11 列**(时间/开盘/收盘/最高/最低/成交量/成交额 + 振幅/涨跌幅/涨跌额/换手率,核心 7 列与 A股 `stock_zh_a_hist_min_em` 同名);rename 仅覆盖 7 核心列,额外列由 `normalize_intraday_df` 的 `_KEEP` 选列静默丢弃 → C3 复用正确。(1m trends2 第 8 列为「最新价」而非 A股「均价」,但 1m fail-closed 不可达。)**`AkshareFetcher.get_intraday_data` 目前仅 A股分支**(`akshare_fetcher.py:415-450`,`ak.stock_zh_a_hist_min_em`,`_AK_PERIOD={"5m":"5","15m":"15","1h":"60"}` 无 1m)。
- **yfinance**(`0700.HK`):`_convert_stock_code` 已 HK00700→0700.HK、`_YF_INTERVAL={5m,15m,1h}`(无 1m)、`is_us_stock_code(HK)=False`(不触发 `.`→`-`)、`_DAILY_MARKET_FETCHER_SUPPORT[YfinanceFetcher]` 含 hk。**fetcher 层零改动**,仅被 `_intraday_fetchers_for` 的 `market!="us"` 过滤挡住。

---

## 1. 设计决策

- **D1 数据源**:**双源 akshare 东财(主)+ yfinance(兜底)**,镜像 cn 的双源鲁棒性。akshare 取历史(5m/15m/1h kline),yfinance 为安全网。
- **D2 成本范围**:MVP 只做路由+数据;HK 成本走**现有跨市场 `fee/slip`**(默认 0;fee/slip 本就 ×2 对称,可近似 HK 双边)。**HK 专属双边印花税 knob 另立 follow-up spec**(与 A股「先分钟、后印花税」一致)。
- **D3 1m fail-closed**:两源均无法锚定 HK 1m 历史(东财 trends2 ndays=5;yfinance 无 1m)→ HK 1m 一律 fail-closed(akshare 经 `_AK_PERIOD` 无 1m 抛 NotImplementedError、yfinance 无 1m),同 A股/美股 1m。
- **D4 代码边界 best-effort**:`is_hk_stock_code` 匹配任意 5 位 HK 码(含 ETF/窝轮/牛熊证;指数 `^HSI` 已天然排除)。**不加「仅个股」过滤**,同 A股北交所 best-effort,文档标注。
- **D5 路由白名单**:hk 分钟仅路由 `[AkshareFetcher, YfinanceFetcher]`,**显式排除 TushareFetcher**(其 `stk_mins` 仅 A股,但日线表含 hk,不收窄会漏入)。

---

## 2. 改动 C1：市场骨架(`src/core/intraday_backtest.py`)

- `MARKET_TRADING_MINUTES` 加 `"hk": 330`(09:30–12:00=150 + 13:00–16:00=180;午休/竞价不计)。
- `bars_per_day` 函数体**不改**;新值自动:1m=330、5m=66、15m=22、**1h=ceil(330/60)=6**。
- 更新 `bars_per_day` docstring 的 ceil 注释:有余数档由「唯一 us 1h=7」改为「us 1h=7、hk 1h=6」。

---

## 3. 改动 C2：市场归类与路由(`data_provider/base.py`,3 处 + 白名单)

### 3.1 `market_of`(`:313-321`)
`raise ValueError` 前插入:
```python
    if _is_hk_market(code):
        return "hk"
```
(同模块私有 `_is_hk_market` 即可,无需 import;docstring 同步去掉「港股…抛 ValueError」。)

### 3.2 门面 guard(`:1576-1578`)
or-链加 `or _is_hk_market(stock_code)`;错误文案改为「仅 crypto / A股 / 港股 / 美股」。

### 3.3 `_intraday_fetchers_for`(`:1524-1549`)
- market 判定加分支:`elif _is_hk_market(code): market = "hk"`(置于 us 分支后、`else: return []` 前)。
- yfinance 过滤(`:1544`)`if market != "us":` 改为 `if market not in ("us", "hk"):`(让 yfinance 对 hk 存活)。
- **白名单收窄**(置于排序前):
```python
    if market == "hk":
        # HK 分钟仅 akshare(东财) + yfinance;Tushare stk_mins 仅 A股(日线表含 hk 会漏入)
        _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
        fetchers = [f for f in fetchers if f.name in _hk_order]
        fetchers.sort(key=lambda f: _hk_order[f.name])
```
- docstring(`:1516`/`:1521`)同步:market 列出 hk;说明 hk 走 akshare 主 + yfinance 兜底。

---

## 4. 改动 C3：akshare HK 分钟分支(`data_provider/akshare_fetcher.py`)

`get_intraday_data` 在 A股逻辑前加 HK 分支:
```python
    period = _AK_PERIOD.get(interval)        # 1m 不在表 → None → 下方 NotImplementedError(HK/A股 1m 同 fail-closed)
    if period is None:
        raise NotImplementedError(f"[{self.name}] 不支持 interval={interval}")
    import akshare as ak
    if is_hk_stock_code(stock_code):     # is_hk_stock_code 同模块已定义(:196),本地可用
        if interval == "1m":             # 独立守卫:HK 1m fail-closed,不依赖 _AK_PERIOD 共享表(防 A股 1m 将来入表静默激活 HK 1m/trends2 ndays=5)
            raise NotImplementedError(f"[{self.name}] 港股 1m 不支持(东财 trends2 仅 ndays=5,无法锚定历史窗口)")
        hk_symbol = stock_code.lower().replace("hk", "").zfill(5)   # 归一同日线 _fetch_hk_data:876(normalize 后为大写 HK00700 或裸 00700,均→"00700")
        sd = f"{start_date} 09:00:00" if start_date else "1970-01-01 09:00:00"
        ed = f"{end_date} 16:00:00" if end_date else "2099-01-01 16:00:00"
        raw = ak.stock_hk_hist_min_em(symbol=hk_symbol, period=period, start_date=sd, end_date=ed, adjust="qfq")
        if raw is None or raw.empty:
            raise DataFetchError(f"[{self.name}] {stock_code} 无港股分钟数据（period={period}）")
        from .intraday_normalize import normalize_intraday_df
        raw = raw.rename(columns={"时间":"datetime","开盘":"open","收盘":"close","最高":"high","最低":"low","成交量":"volume","成交额":"amount"})
        return normalize_intraday_df(raw, stock_code)
    # …既有 A股分支不变(ak.stock_zh_a_hist_min_em)…
```
- **symbol 归一**:`stock_code.lower().replace("hk","").zfill(5)`,与日线 `_fetch_hk_data`(`akshare_fetcher.py:876`)**同款**。门面入口已 `normalize_stock_code`(`base.py:1574`):`HK00700`/`0700.HK`→**大写 `HK00700`**、裸 `00700`→保持 `00700`(**不加前缀**;对抗审查纠正原「hk00700 小写」措辞)。`.lower().replace("hk","")` 对 `HK00700`→`00700`、`00700`→`00700` 均正确(测试覆盖两形)。
- `stock_hk_hist_min_em` 列名与 A股一致 → rename/`normalize_intraday_df` 直接复用。
- **1m**:`_AK_PERIOD` 无 1m → period=None → NotImplementedError(fail-closed,先于 HK 分支,A股/HK 共用)。

> yfinance **零改动**(§0.4),仅靠 C2 解开过滤。

---

## 5. 改动 C4：服务层(链路 A + 链路 B)

### 5.1 链路 A(`src/services/backtest_service.py`)
- gate(`:115-120`)or-链加 `or is_hk_stock_code(analysis.code)`;**import(对抗审查纠正:定论)**:`is_hk_stock_code` 定义在 `akshare_fetcher.py`、经 `data_provider/__init__.py` 暴露,**不在 `data_provider.base`** → `from data_provider.base import is_hk_stock_code` 会 ImportError。须**另起一行** `from data_provider import is_hk_stock_code`(不要并入 `:108` 的 `from data_provider.base import …`)。
- 注释(`:121`)补「港股个股(best-effort)」。
- `market = market_of(analysis.code)` 现返回 "hk" → `window_bar_cnt = bars_per_day(interval,"hk")`;`end_date` 缓冲走 else(覆盖港股长假),**无需 hk 专属分支**。
- **成本**:HK market!="cn" → 不计 A股印花税(正确);走现有 fee/slip(默认 0);cash 成本门控自动适用。**本特性不加 HK 成本分支**。

### 5.2 链路 B(`src/services/signal_backtest_service.py`)
- `_INTRADAY_MAX_DAYS` 加 `"hk": {"5m": 60, "15m": 60, "1h": 730}`(**无 1m 键** → 1m 走基线 365 但 fetch 层 NotImplementedError fail-closed,365 不被消费无害)。
  - **band 语义非对称(对抗审查补)**:该上限对 **yfinance 是服务端真限**(请求过深直接截至 60d);对 **akshare 东财 kline 是 client 端裁剪**(`beg=0` 总拉全量历史后本地切片,band 仅限信号引擎可见窗口/内存,**不减网络传输**)。东财更浅时优雅返回子集(同 cn 已验);若东财 HK 历史极深可能触 akshare 15s 超时 → DataFetchError → yfinance 兜底(仍受 60d 服务端保护)接管。
- `_load_bars` 经 `get_market_for_stock→"hk"` 自动路由(C2 已解锁),无需改。
- `run()` docstring(`:117`)去掉「HK 无分钟取数」,改为「分钟覆盖 crypto/A股沪深/美股个股/港股个股(best-effort)」。

---

## 6. 行为变更与兼容性

- **新增能力**:港股个股可走分钟回测(CLI `--backtest-interval`、API `interval`、Web BacktestPage、链路 B `--signal-backtest-interval`),复用既有入口,**仅市场放行 + 数据接入**。
- **crypto/cn/us/日线零回归**:hk 是纯新增分支;`interval` 默认 1d 即现状;`MARKET_TRADING_MINUTES`/`_INTRADAY_MAX_DAYS` 仅追加 hk 键不动既有;白名单只对 market=="hk" 生效。
- **无 schema 迁移、无新配置**(成本复用 fee/slip)。`bar_interval`/`engine_version`(`v1-5m` 等)沿用。
- **测试契约变更(对抗审查纠正:不止 1 处,共 6 处必改,否则 ci_gate 红)**:本特性使 HK 由「不支持」变「支持」,以下既有**离线**测试的负向断言会失败,必须同步改:
  1. `tests/test_market_detection_us.py:27`:`market_of("HK00700")` raise → 改 `assert == "hk"`。
  2. `tests/test_market_detection_ashare.py:55-59`(`test_market_of_unsupported_raises`,参数 `["00700","HK00700"]`)断言 raise → 改 `assert == "hk"` + 改 `:55` 注释。(同文件 `:33-39 test_is_a_share_false_others` **不受影响**——HK 仍非 a_share。)
  3. `tests/test_backtest_service_intraday.py:234-269`(`test_intraday_non_crypto_skipped`,code=`HK00700`,断言 `processed==0`/未取数)→ **换不支持码**(见下)。
  4. `tests/test_backtest_service_intraday.py:318-349`(`test_intraday_non_crypto_skip_counter`,code=`HK00700`,断言 `skipped_unsupported==1`)→ **换不支持码** + 改 `:321` 注释。
  5. `tests/test_intraday_backtest_helpers.py:78-80`(`test_bars_per_day_unknown_market_raises`,`bars_per_day("5m","hk")` 断言 raise)→ 改用**真未登记市场** `"jp"`(`hk` 现已登记)。
  6. `tests/test_signal_backtest_service.py:269-270`(断言 `_minute_fetch_days("hk","5m")==365`、`("hk","1h")==730`)→ 改 `5m==60`、`1h==730`,去「hk 不在 band 表」注释。
  - **#3/#4 替换码**:用改后仍不支持的"看似有效"码 **`510050`**(已核验 `is_crypto/perp/a_share/us/hk` 全 False → 仍计 `skipped_unsupported`;`SPX`/`^HSI` 亦可),保留 skip-counter 路径覆盖。
- 文档:`intraday-backtest.md`(新增港股章 + §3 市场表加 hk + 改 §9.2/§10.5 港股表述)、`signal-credibility.md`(HK 注)、`CHANGELOG`。

---

## 7. 测试设计

### 7.1 单元(离线确定性)
- **helpers**(`tests/test_intraday_backtest_helpers.py`):新增 `MARKET_TRADING_MINUTES["hk"]==330`、`bars_per_day` hk 四档=`{1m:330,5m:66,15m:22,1h:6}`(纯算术,1h=ceil(330/60)=6)、`derive_window_bar_count(10,"5m","hk")==660`;**crypto/cn/us 既有断言不回归**(ceil 改注释不改值);既有 `:78-80` "hk raises" 负向测试改用 `"jp"`(见 §6 test 5)。
- **market_of**(正向新增):`market_of("HK00700")=="hk"`、补 `00700`/`0700.HK` 变体、`market_of_order_no_collision` 加 hk 不串其它市场;**既有负向 raise 断言改法见 §6(test 1/2)**。
- **akshare HK 分支**(新 `tests/test_akshare_hk_intraday.py`):monkeypatch `ak.stock_hk_hist_min_em` 返回构造 11 列 df → 断言 rename/`normalize_intraday_df` 正确(额外列被丢)、symbol 归一对 `HK00700`/`00700` 一致;**1m → NotImplementedError**(独立守卫 fail-closed,§4 C3)。
- **路由**(`tests/test_*intraday_fetchers*` 或相邻):`_intraday_fetchers_for("HK00700")` 含 AkshareFetcher+YfinanceFetcher、**不含 TushareFetcher**、且 akshare 排在 yfinance 前;`market!="us"→not in("us","hk")` 后 yfinance 对 hk 存活。

### 7.2 集成(链路 A,离线 mock)
- 新 `tests/test_backtest_service_hk_intraday.py`(**镜像 `test_backtest_service_ashare_intraday.py`**):monkeypatch `get_candidates`(HK 码)/`_resolve_analysis_date`/`get_start_daily`/`DataFetcherManager.get_intraday_data`/`evaluate_single`/`save_results_batch` → 断言 HK 被处理(`processed=1`、`skipped_unsupported=0`)、引擎切片=`bars_per_day(interval,"hk")×N`、落库 `bar_interval`/`engine_version`/`first_hit_bar_index` 语义正确。

### 7.3 链路 B
- `tests/test_signal_backtest_service.py`:`_minute_fetch_days(market="hk",interval=...)` 按 hk band 夹取(5m/15m→60、1h→730、1m→365 无害);**既有 `:269-270` 基线断言须改,见 §6(test 6)**。

### 7.4 门禁
- 后端 `./scripts/ci_gate.sh`(flake8 + `pytest -m "not network"`)全绿,记录 passed 增量。
- **无前端代码改动**(BacktestPage interval 选择器已存在,hk 经后端放行即生效)→ **不需 web-gate**(除非新增前端文案,本特性不新增)。
- (可选 `-m network`)真实 HK 分钟取数观测测试(默认 deselect)。

---

## 8. 文件清单

- `src/core/intraday_backtest.py`:`MARKET_TRADING_MINUTES["hk"]=330` + ceil 注释(C1)。
- `data_provider/base.py`:`market_of` hk 分支、门面 guard、`_intraday_fetchers_for` hk 路由+白名单+yfinance 过滤放行 + docstring(C2)。
- `data_provider/akshare_fetcher.py`:`get_intraday_data` HK 分支(C3)。
- `src/services/backtest_service.py`:gate 放行 hk + import(C4.1)。
- `src/services/signal_backtest_service.py`:`_INTRADAY_MAX_DAYS["hk"]` + docstring(C4.2)。
- **测试**(§6 列 6 处必改 + §7 新增):改 `tests/test_market_detection_us.py`、`tests/test_market_detection_ashare.py`、`tests/test_backtest_service_intraday.py`(2 处,换码 510050)、`tests/test_intraday_backtest_helpers.py`(hk 正向 + 负向改 jp)、`tests/test_signal_backtest_service.py`;新增 `tests/test_akshare_hk_intraday.py`、`tests/test_backtest_service_hk_intraday.py`、路由单测。
- `docs/intraday-backtest.md`:新增港股章 + §3 市场表加 hk(330)行;**逐行订正**:`:3` 头部去「港股不在计划内」、`:217` 去「港股不在计划内/skipped」、`:279`「港股仍不支持」改为支持、`:292`「yfinance 非 us 排除」改为「非 (us,hk) 排除」。`docs/signal-credibility.md`(HK 注)、`docs/CHANGELOG.md`(`[新功能]` 扁平)。

---

## 9. 风险与回滚

- **band 在线深度未核验**(东财 HK 分钟保留天数、yfinance HK 上限):保守 band 由构造安全(请求超上限时东财返子集/yfinance 截至 60d,均不报错,同 cn 已验);**在线核验 deferred**(需有网环境)。
- **午休 gap**:东财/yfinance HK 分钟午休(12:00–13:00)自然不返 bar;`bars_per_day=330/bar` 已按连续交易分钟计(5m/15m 整除无半根),信号 datetime 连续性与 A股午休同构(A股已验)。
- **HK 1h 实际 bar 数 online 待核验**:`bars_per_day("1h","hk")==6` 是纯算术(ceil(330/60),单测断言正确);但数据源每日实际吐 6 根(早市末半根 11:30–12:00 + 午后 3 根)还是 5 根,需在线核验——若 5 根则窗口偏宽(偏安全不偏窄),不影响单测、仅影响窗口精度,deferred 同 cn/us。
- **yfinance 兜底单点**:与 us 单源风险同级;akshare 主源在,双源已优于 us。
- **跨市场泄漏**:白名单(D5)确保 tushare 不漏入 hk;`is_us_stock_code(HK)=False` 确保 yfinance 不误判。
- **回滚**:纯新增市场分支 + 数据接入,无 schema/迁移;`git revert` 即恢复;不传 hk 标的或 interval=1d 即不触发。

---

## 10. 不做(YAGNI / 范围外)

- **HK 双边印花税 knob**(另立 follow-up;MVP 走 fee/slip 默认 0)。
- HK 做空/short(现货 long-only,同 A股)。
- HK「仅个股」过滤(best-effort,同北交所)。
- 30m interval(不在现 `SUPPORTED_INTERVALS`/`_AK_PERIOD`/`_YF_INTERVAL` 词表)。
- Longbridge 分钟源(未实现 `get_intraday_data`,且需 key)。
- qfq 复权基准漂移(既有 A股/美股同问题,已文档化,统一另议)。
- 前端新文案/新组件(interval 选择器已存在)。

---

## 11. 对抗式审查可追溯(2026-06-29)

4 视角并行读真实代码 + 离线探 akshare/yfinance 签名核验。**4-change 设计 0 Blocker**:routing/`market_of`/akshare 签名(`(symbol,period,adjust,start_date,end_date)`)/yfinance 路径/市场数学(330→66/22/6,derive 660)/band 夹取/crypto-cn-us 零回归 全 confirmed;**D5 白名单 confirmed 必要**(TushareFetcher 日线表含 hk 且实现 get_intraday_data,有 token 时不收窄会漏入 HK)。处置:

- **Blocker(spec 测试完整性)**:§6/§7 原仅列 1 处必改测试,实际 **6 处离线测试**会红(`test_market_detection_us:27`/`test_market_detection_ashare:55-59`/`test_backtest_service_intraday:234-269`+`:318-349`/`test_intraday_backtest_helpers:78-80`/`test_signal_backtest_service:269-270`)→ §6 全列 + 替换码 `510050` 核验 + jp 改法。
- **Important**:`from data_provider.base import is_hk_stock_code` 实测 ImportError(符号在 akshare_fetcher,经 `data_provider/__init__` 暴露)→ §5.1 定论 `from data_provider import …`;§0.4 kline 返 11 列非 7(额外列 normalize `_KEEP` 丢弃,C3 仍对)→ §0.4 订正;HK 1m fail-closed 原仅靠共享 `_AK_PERIOD` 无 1m → §4 C3 加独立守卫(防将来 A股 1m 入表静默激活)。
- **Minor/Nit 已收**:`normalize_stock_code` 返大写 `HK00700`/裸 `00700` 不加前缀(非小写 hk00700)→ §0.4/§4 订正;akshare kline `beg=0` 全量拉取、band 对 akshare 仅 client 裁剪(15s 超时风险,yfinance 兜底)→ §5.2/§9;1m trends2 `ndays=5 硬编码`(非「忽略 start/end」)→ §0.4;HK 1h 实际 bar 数(6 vs 5)online 待核验 → §9;docs `:3/:217/:279/:292` 逐行 → §8。
- **确认无缺陷**:`_is_hk_market` 对 cn(6 位)/us(字母)/crypto(带斜杠)不误判,5 位纯数字唯一落 hk;`market_of` 返回 hk 被 backtest_service 下游(`:191` else 非 crypto 缓冲、`:311` 非 cn 不计印花税)正确消费;Web BacktestPage interval 为自由透传无市场枚举(不需 web-gate)。
