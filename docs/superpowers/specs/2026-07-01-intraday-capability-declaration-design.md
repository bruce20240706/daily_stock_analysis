# intraday_data capability 显式声明 —— 设计 spec

> 日期:2026-07-01　类型:refactor(纯重构,路由字节级不变)
> 影响面:`data_provider/`(分钟数据源路由)。无 config / schema / API / Web / 报告 / 通知变化。

## 1. 背景与动机

分钟(intraday)数据源路由 `DataFetcherManager._intraday_fetchers_for`(`data_provider/base.py:1515`)当前**从日线支持表派生**分钟路由,再叠启发式补丁,而非直接声明「某源的 `get_intraday_data` 服务哪些市场」。具体三层:

1. **日线派生**:先用 `_filter_daily_fetchers_for_market(fetchers, market)`(读 `_DAILY_MARKET_FETCHER_SUPPORT`)取该市场的**日线**源集合。
2. **stub 探测**:`type(f).get_intraday_data is not BaseFetcher.get_intraday_data` —— 剔除未覆写分钟方法的纯日线源(Efinance/Pytdx/Baostock/Longbridge/Finnhub/AlphaVantage)。
3. **两处 ad-hoc 补丁**:
   - `if market not in ("us","hk"): 排除 YfinanceFetcher` —— 因 yfinance **日线**支持 cn,但其**分钟**不服务 A股,否则漏入 A股分钟路径(补丁①)。
   - `if market == "hk": 白名单仅 {Akshare, Yfinance}` —— 因 Tushare **覆写了** `get_intraday_data`(服务 A股)**且日线支持 hk**,stub 探测无法把它从 hk 剔除,只能硬编码白名单(补丁②)。

两次线上咬人(①yfinance 漏入 cn、②Tushare 漏入 hk)同根:**分钟市场支持是从日线支持「推导」出来的,而非「声明」。** 每个分钟源其实精确知道自己 `get_intraday_data` 服务哪些市场。把这条隐含知识变成**显式声明**,即可把三层启发式收敛成一次查表,并从根上消除「日线派生」这类漂移。

## 2. 目标与非目标

**目标:**
- 每个 fetcher **显式声明**其分钟能力服务的市场集合(`intraday_markets: frozenset`)。
- 门面分钟路由改为**声明式**:`market in f.intraday_markets`,替换掉「日线派生 + stub 探测 + 两处 ad-hoc 补丁」。
- **纯重构,路由字节级不变**:对每个市场,声明式路由产出的 fetcher 列表**与顺序**与当前完全一致,由 golden 等价测试锁定。

**非目标(明确不做):**
- 不改**生产路由的可观测行为**:对全部真实 fetcher(均 BaseFetcher 子类),不改路由结果、不改排序、不改 runtime 可用性语义、不改取数逻辑。(唯一例外是经 `DataFetcherManager(fetchers=...)` 注入的 duck-typed 源的入路由契约,见 §9 契约细化 —— 生产零影响。)
- 不动 `_DAILY_MARKET_FETCHER_SUPPORT` 日线支持表(两表相互独立、互不派生)。
- 不动其它 capability 路径(`realtime_quote` / `stock_name` / `stock_list` / boards / `daily_data`)—— 本次只碰 `intraday_data`。
- 不删 `_filter_daily_fetchers_for_market`(日线路径仍在用),仅让分钟路径**停止调用**它。
- 不改 `BaseFetcher.get_intraday_data` 默认抛 `NotImplementedError`(保留为安全网)。
- 不新增 config / env / schema / Web / 文档面向用户的能力;这是内部路由重构。

## 3. 现状:当前有效路由(重构须逐字复现)

`get_intraday_data` 覆写者(经 grep 确认):`CryptoExchangeBase`(Binance/Okx/Coinbase/OkxPerpetual 的父类)、`TushareFetcher`、`AkshareFetcher`、`YfinanceFetcher`。类层级:

```
BaseFetcher
└─ CryptoExchangeBase                 (覆写 get_intraday_data)
   ├─ BinanceFetcher
   ├─ CoinbaseFetcher
   └─ OkxFetcher
      └─ OkxPerpetualFetcher          (经 MRO 继承 CryptoExchangeBase.get_intraday_data)
```

`_DAILY_MARKET_FETCHER_SUPPORT`(`base.py:705`,**不动**,仅供对照):Binance/Okx/Coinbase={crypto}、OkxPerpetual={crypto_perp}、Tushare/Akshare={cn,hk}、Yfinance={cn,hk,us}、Longbridge={hk,us}、Efinance/Pytdx/Baostock={cn}、Finnhub/AlphaVantage={us}。

**当前有效分钟路由(golden 基线)**:

| market | 代码判定入口 | 当前分钟源(顺序) | 顺序来源 |
| --- | --- | --- | --- |
| crypto | `is_crypto_code` | Binance, Okx, Coinbase | 快照(priority)顺序,无显式重排 |
| crypto_perp | `is_perp_code` | OkxPerpetual | 单源 |
| cn | `is_a_share_code` | Tushare › Akshare | cn 显式排序表 `{Tushare:0, Akshare:1}` |
| us | `is_us_stock_code` | Yfinance | 单源 |
| hk | `_is_hk_market` | Akshare › Yfinance | hk 显式排序表 `{Akshare:0, Yfinance:1}` |
| 其它 | —— | `[]` | 调用方拒绝 |

> 注:上表是**声明式路由须逐字复现的 oracle**。crypto/us 顺序 = 当前 `_get_fetchers_snapshot()` 的 priority 顺序;测试中以实际当前顺序 pin 定(见 §7)。

runtime 可用性(**保留**):`_filter_fetchers_by_capability(fetchers, capability="intraday_data")` 用 `is_available_for_request/is_available/_is_available` 探测剔除**当下不可用**的源(如**无 token 的 Tushare** → cn 只剩 Akshare)。此为运行时能力,正交于静态市场声明,重构后照旧。

## 4. 设计:`intraday_markets` 声明

在 `BaseFetcher` 加类属性,默认空集;各分钟源覆写:

```python
class BaseFetcher(ABC):
    # 该源 get_intraday_data 服务的市场集合(空=不提供分钟数据)。
    # 与 _DAILY_MARKET_FETCHER_SUPPORT(日线)相互独立、互不派生。
    intraday_markets: frozenset = frozenset()
```

| 类 | `intraday_markets` | 说明 |
| --- | --- | --- |
| `BaseFetcher` | `frozenset()` | 默认空 —— 纯日线源(Efinance/Pytdx/Baostock/Longbridge/Finnhub/AlphaVantage)继承空集 → 自动排除(取代 stub 探测) |
| `CryptoExchangeBase` | `frozenset({"crypto"})` | Binance/Okx/Coinbase 经继承得 `{crypto}` |
| `OkxPerpetualFetcher` | `frozenset({"crypto_perp"})` | **必须显式覆写**:它经 MRO 会从 `CryptoExchangeBase` 继承到 `{crypto}`,须覆写为 `{crypto_perp}` 才与当前路由一致(当前 perp 仅 OkxPerpetual、crypto 不含 OkxPerpetual) |
| `TushareFetcher` | `frozenset({"cn"})` | **不含 hk**(即使日线支持 hk)—— 这正是补丁②的知识,现显式化 |
| `AkshareFetcher` | `frozenset({"cn", "hk"})` | 东财分钟服务 A股 + 港股 |
| `YfinanceFetcher` | `frozenset({"us", "hk"})` | **不含 cn**(即使日线支持 cn)—— 这正是补丁①的知识,现显式化 |

**继承语义要点(spec 强调,防实现踩坑)**:`OkxPerpetualFetcher(OkxFetcher(CryptoExchangeBase))`,若只在 `CryptoExchangeBase` 声明 `{crypto}` 而漏掉 `OkxPerpetualFetcher` 的覆写,perp 会被误并入 crypto、且 crypto_perp 变空 —— 路由破坏。故 `OkxPerpetualFetcher` 覆写是**必需项**,§7 有专项测试锁定。

## 5. 门面路由改写(collapse 成一句)

`_intraday_fetchers_for` 的**市场判定段不变**、**cn/hk 排序表不变**,仅中段三层启发式收敛为一次声明式过滤:

**Before(现状,base.py:1539-1558):**
```python
fetchers = self._get_fetchers_snapshot()
fetchers = self._filter_daily_fetchers_for_market(fetchers, market)          # 日线派生
fetchers = self._filter_fetchers_by_capability(fetchers, capability="intraday_data")
fetchers = [f for f in fetchers
            if type(f).get_intraday_data is not BaseFetcher.get_intraday_data]  # stub 探测
if market not in ("us", "hk"):
    fetchers = [f for f in fetchers if f.name != "YfinanceFetcher"]           # 补丁①
if market == "hk":
    _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
    fetchers = [f for f in fetchers if f.name in _hk_order]                   # 补丁②
    fetchers.sort(key=lambda f: _hk_order[f.name])
if market == "cn":
    _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
    fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
return fetchers
```

**After(声明式):**
```python
fetchers = self._get_fetchers_snapshot()
# 声明式过滤,替换 日线派生+stub+补丁①②。用 getattr 容错(与既有 getattr 探针 base.py:791 一致):
# 无 intraday_markets 属性的对象(如测试注入的 duck-typed 源)回退空集 → 优雅排除,不抛 AttributeError。
fetchers = [f for f in fetchers if market in getattr(f, "intraday_markets", frozenset())]
fetchers = self._filter_fetchers_by_capability(fetchers, capability="intraday_data")  # runtime 可用性,保留
if market == "hk":
    _hk_order = {"AkshareFetcher": 0, "YfinanceFetcher": 1}
    fetchers.sort(key=lambda f: _hk_order.get(f.name, 2))                     # 排序保留(白名单已由声明保证)
if market == "cn":
    _cn_order = {"TushareFetcher": 0, "AkshareFetcher": 1}
    fetchers.sort(key=lambda f: _cn_order.get(f.name, 2))
return fetchers
```

**等价性论证(byte-identical)**:
- **日线派生 → 声明**:当前 `market in _DAILY... ∩ (覆写 get_intraday_data) ∩ 补丁` 的净结果,恰等于「声明了该 market 的源」。逐市场核对:
  - crypto:{Binance,Okx,Coinbase}(声明 {crypto});OkxPerpetual 声明 {crypto_perp} 被排除 ✓(当前亦不含 perp)。
  - crypto_perp:{OkxPerpetual} ✓。
  - cn:{Tushare,Akshare}(yfinance 声明 {us,hk} 天然排除 = 补丁①;Efinance/Pytdx/Baostock 空集排除 = stub)✓。
  - us:{Yfinance} ✓。
  - hk:{Akshare,Yfinance}(Tushare 声明 {cn} 天然排除 = 补丁②;Longbridge 空集排除 = stub)✓。
- **hk 排序**:原用 `.sort(key=_hk_order[f.name])` 且**先白名单过滤**保证 key 必命中;改后白名单由声明保证(hk 集合只可能是 Akshare/Yfinance),`.get(name, 2)` 与原 `[name]` 在该集合上等价,且更防御(理论上多出的源落末尾而非 KeyError)。
- **runtime 可用性**:`_filter_fetchers_by_capability` 位置由「stub 之前」移到「声明过滤之后」;因声明集合 ⊆ 原日线集合、且该过滤按源逐一判定与顺序无关,结果不变(无 token Tushare 仍从 cn 剔除)。
- **快照顺序**:`_get_fetchers_snapshot()` priority 顺序 → 声明过滤保序 → crypto/us 维持 priority 顺序、cn/hk 由排序表重排,与现状一致。
- **duck-typed 注入源(契约细化,非严格 byte-identical 的唯一偏差)**:Before 路径全程 getattr 容错(stub 探测读 `type(f).get_intraday_data`、可用性探针 `getattr(f, probe, None)`),故经 `DataFetcherManager(fetchers=[...])` 注入的 duck-typed 对象(非 BaseFetcher 子类、无 `intraday_markets`)只要覆写了 `get_intraday_data` 就能进分钟路由。声明式模型下,这类对象**须显式声明 `intraday_markets`**才被路由;未声明则经 `getattr(..., frozenset())` **优雅排除**(不再崩溃、也不再隐式纳入)。**实践影响仅限测试脚手架**:所有生产 fetcher 都是 BaseFetcher 子类,经基类默认 + 覆写必有该属性;唯一现存消费者是注入 duck-typed stub 的单测(如 `tests/test_manager_intraday_cn.py:86 _Stub`),须在其上补 `intraday_markets` 声明(stub 本就模拟对应市场源,补声明是忠实适配,见 §7.7)。此为对「注入自定义源」这一入口的契约细化——由隐式(覆写方法即入)改为显式(声明市场才入),方向与本重构一致。

## 6. 保留不变项(清单)

- 市场判定链 `is_perp_code / is_crypto_code / is_a_share_code / is_us_stock_code / _is_hk_market`,未知 → `[]`。
- `_filter_fetchers_by_capability(capability="intraday_data")` runtime 可用性探测(无 token Tushare 剔除等)。
- cn / hk 排序表(顺序语义)。
- `BaseFetcher.get_intraday_data` 默认抛 `NotImplementedError`(安全网:即便误路由也不会静默取错数)。
- `_DAILY_MARKET_FETCHER_SUPPORT` 与 `_filter_daily_fetchers_for_market`(日线路径继续使用)。
- 门面 `get_intraday_data` 的降级/异常聚合逻辑(NotImplementedError 捕获后继续下一个源)。

## 7. 测试(纯重构,以等价 + 防泄漏 + 防变异锁定)

置于既有分钟路由测试文件(`tests/test_manager_intraday_*.py` / `tests/test_get_intraday_data*.py`,实现时按现有归属就近追加;若无合适文件则新增 `tests/test_intraday_capability_routing.py`)。

1. **golden 等价 —— 须区分「默认管理器」与「两源可用管理器」**(Important:cn 全表在默认离线环境不可达):
   - **默认管理器**(ci_gate 离线无 token,`DataFetcherManager()`):断言精确有序列表 —— crypto=`BTC/USDT`→`[Binance,Okx,Coinbase]`、perp=perp 代码→`[OkxPerpetual]`、us=`AAPL`→`[Yfinance]`、hk=`HK00700`→`[Akshare,Yfinance]`、**cn=`600519`→`[Akshare]`**(无 token,Tushare 不实例化(`base.py:1213-1217`)/即便在也被 `is_available→False` 剔除(`tushare_fetcher.py:210-217`))、未知/畸形→`[]`。
   - **两源可用管理器**(镜像 `tests/test_manager_intraday_cn.py:84-102`:`DataFetcherManager(fetchers=[ak, ts])` + stub `is_available→True` + stub 声明 `intraday_markets`):**cn→`[Tushare,Akshare]`**,专锁 `_cn_order`「Tushare 主源优先」排序 oracle。
   - crypto/us 顺序**不以「跑当前代码取值」定**(那会让测试跟随实现、失去 oracle 意义 = tautology);改为**按各源 `.priority` 显式推定**:实现时读 Binance/Okx/Coinbase 的 `priority` 值,据此在断言里写出确定顺序并注释来源;若相等则记录并按快照稳定序断言。
2. **防泄漏(锁补丁①②的语义)**:`"YfinanceFetcher" not in names(cn)`(补丁①不回归);`"TushareFetcher" not in names(hk)`(补丁②不回归)。
3. **默认空集**:`BaseFetcher.intraday_markets == frozenset()`;且断言纯日线源实例(如 `EfinanceFetcher`/`LongbridgeFetcher`)`.intraday_markets == frozenset()` → 任何市场均不入分钟路由。
4. **per-fetcher 声明单测**:逐类断言 `intraday_markets` 精确等于 §4 表(`Binance/Okx/Coinbase=={"crypto"}`、`OkxPerpetual=={"crypto_perp"}`、`Tushare=={"cn"}`、`Akshare=={"cn","hk"}`、`Yfinance=={"us","hk"}`)。
5. **perp 覆写专项(防 MRO 陷阱)**:断言 `OkxPerpetualFetcher().intraday_markets == frozenset({"crypto_perp"})` 且 `"crypto" not in OkxPerpetualFetcher().intraday_markets` —— 若实现漏了覆写(继承成 `{crypto}`),此测试 RED。
6. **runtime 可用性仍生效**:在两源可用管理器上把 Tushare 的 `is_available`→False,断言 cn 路由剔除 Tushare 只剩 Akshare —— 证声明过滤未吞掉可用性语义。
7. **既有分钟测试不回归 —— 显式含 duck-typed stub 测试**(Blocker:这些正是易漏的 RED 源):`test_non_crypto_intraday_raises` / `test_non_crypto_non_cn_raises` / `test_unsupported_market_raises`(「不支持市场→raises」),**以及** `tests/test_manager_intraday_cn.py:84-102` 注入 duck-typed `_Stub` 的两源 cn 排序测试。实现者须 **grep 全仓所有注入 duck-typed 分钟 stub 的测试**(搜 `_intraday_fetchers_for` / `DataFetcherManager(fetchers=`),对每个**模拟真实市场源**的 stub 补 `intraday_markets` 声明(忠实适配,见 §5 契约细化条),确保全部 GREEN。
8. **duck-typed 容错专项(锁 Blocker 修复)**:注入一个**无 `intraday_markets` 属性**的 duck-typed 对象,断言 `_intraday_fetchers_for` **不抛 AttributeError** 且该对象被优雅排除(`getattr` 回退空集);再给它补 `intraday_markets=frozenset({"cn"})` 后断言被纳入 cn 路由 —— 双向证「无声明不崩、有声明才入」的契约细化。
9. **声明↔门面双真值防漂移(推荐)**:对每个市场,断言门面路由结果 == 「快照中 `market in getattr(f,'intraday_markets',frozenset())` 且可用」的独立重算 —— 防「改了 frozenset 忘改门面 / 反之」的单侧漂移。

> 变异抵抗:测试 1+4+5+8 pin 精确集合/有序列表/容错 —— 任一 frozenset 写错、漏 perp 覆写、直接属性访问(AttributeError)、或路由顺序变动均 RED。

## 8. 验证矩阵

- 后端 Python 改动:`data_provider/`。执行 `./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`)。
- 最低:`python -m py_compile data_provider/base.py <改动的 fetcher 文件>`。
- 无 config / API / Schema / Web 面 → 免 web-gate;无网络新依赖 → 离线全覆盖。
- 交付说明须写明:golden 等价测试(默认 + 两源可用两类管理器)锁定生产路由字节级不变;既有「不支持市场 raises」+ duck-typed stub 排序测试纳入回归;duck-typed 注入源契约细化(见 §9)。

## 9. 风险与回滚

- **风险:低**。生产路由(全 BaseFetcher 子类)纯重构、无可观测行为变化,golden 等价(默认 + 两源可用)+ 防泄漏 + 防变异 + duck-typed 容错 + 既有回归多重锁定。主要实现风险是 frozenset 写错 / 漏 perp 覆写 / 用直接属性访问(应 getattr)—— 已由 §7.1/§7.4/§7.5/§7.8 专测覆盖。
- **契约细化(唯一非严格 byte-identical 偏差,已知且受控)**:经 `DataFetcherManager(fetchers=[...])` 注入的 **duck-typed 源**(无 `intraday_markets`)由「覆写 `get_intraday_data` 即隐式入分钟路由」改为「须显式声明 `intraday_markets` 才入,否则经 getattr 优雅排除」。生产环境零影响(所有 fetcher 均 BaseFetcher 子类、必有该属性);现存唯一消费者是注入 stub 的单测,已在 §5/§7.7 要求补声明适配。此偏差是有意的入口收敛(隐式→显式),与本重构目标一致。
- **回滚**:单分支 `git revert`。新增的 `intraday_markets` 属性即使残留也无害(仅当门面按其路由时才生效,revert 门面即失效)。

## 10. 交付说明(模板)

改了什么 / 为什么(消除「日线派生」路由漂移根因)/ 验证情况(ci_gate + golden 等价 + 既有回归)/ 未验证项 / 风险点(低,四重锁定)/ 回滚方式(单分支 revert)。
