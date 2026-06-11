# 设计：Binance fapi 衍生品备援（整源降级，OKX 主源不变）

- 日期：2026-06-11
- 状态：设计已批准（方案 B：独立 Binance 模块 + 整源降级编排）；已按对抗审查（27 发现 → 13 确认）收敛，待写实施计划
- 子项目：perp 系列韧性补强——衍生品抓取从 OKX 单源升级为 OKX 主 / Binance fapi 兜底
- 关联前序：perp 指标（A/B/多空比）、perp 回测资金费（E）均依赖 `data_provider/crypto_derivatives.py` 的 OKX 单源抓取

## 0. 背景与动机

衍生品数据现状全部 OKX 单源：`fetch_perp_metrics`（5 路实时：资金费/标记价/OI/全市场多空比/大户多空比，供单股注入、复盘 per-coin 与聚合）与 `fetch_funding_rate_history`（回测资金费）。OKX 不可达时：实时指标 presence-only 整组缺省（分析 prompt 失去合约视角）；回测资金费**静默记 0**（隐性数据质量损失）。

Binance fapi 对两个抓取面都有免 key 对等端点。本环境实测 Binance 451（地区限制）、OKX 200——**备援在本机永远不触发**，价值在 OKX 受限/故障的其他部署环境；本地只能离线 mock 验证（见 §5/§8）。

## 1. 范围与改动面

**改动**：
- 新建 `data_provider/binance_derivatives.py`；
- `data_provider/crypto_derivatives.py` 两个公开函数加整源降级分支；
- **`src/analyzer.py` 一处必改（对抗审查 blocker）**：合约指标块标题现为硬编码 `### 合约市场指标（永续，来源 OKX）`（~line 3031）——降级后 `source='binance'` 时 prompt 会对 LLM 说谎。改为动态：标题前取 `src_label = (contracts.get("source") or "okx").upper()`，标题改 f-string `（永续，来源 {src_label}）`。Web 的 `ReportCryptoMetrics` 已动态渲染 `source.toUpperCase()`，无需改；复盘聚合输出无 source 字段，无需改。
- `src/config.py` + `src/core/config_registry.py`：新 env 按既有 `binance_base_url` 同模式注册（见 §4）；`.env.example` 同步。
- 测试与文档（§5/§6）。

**零改动**：下游 services 契约——`CryptoDerivativesService`/`CryptoDerivativesReviewService`/backtest service 调用的函数签名与输出形状不变（三者均不检视 source，已核）；`source` 保持单值（`'okx'` 或 `'binance'`）；门控仍由 service 层 `crypto_derivatives_enabled` 承担；DB schema、API schema、Web/Desktop、聚合层（`fetch_perp_market_snapshot`/`_perp_row_for_symbol` 经模块内全局引用自动随降级受益）。

**明确不做（YAGNI）**：
- 不做优先级配置（固定 OKX→Binance；两源场景配置价值低）。
- 不做逐路混源补缺（OKX 部分成功即用 OKX——presence-only 本就接受部分字段；混源行有 OI 张数/多空比统计口径噪音，且 `source` 契约会漂移）。
- 不引入多源 fetcher 注册框架（两源 YAGNI）；不改 Coinbase（无衍生品公共对等物）。

## 2. 新模块 `data_provider/binance_derivatives.py`

分层纪律同 `crypto_derivatives.py`：不 import `src.*`、不碰 DB。helpers 顶层复用 `from data_provider.crypto_derivatives import _http_get_json, _to_float, _LINEAR_QUOTES`——**显式设计选择**：复用下划线私有名以避免平行实现（AGENTS.md 优先复用）；在 `crypto_derivatives.py` 对应定义处加一行注释「供 binance_derivatives 复用」标注边界（仓库内无先例，以注释立约定；不为此抽公共模块——YAGNI）。无循环 import（方向见 §3）。

### 2.1 基址与 symbol

```python
def _fapi_base() -> str:
    """Binance fapi 基址；BINANCE_FAPI_BASE_URL 可覆盖（受限地区换镜像），默认官方域名。"""
    return (os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")
```

symbol：`BASE+QUOTE` 直接拼接（`BTC`,`USDT` → `BTCUSDT`）；guard 与 OKX 同：base 为空或 `quote ∉ _LINEAR_QUOTES` → 空结果、**零网络调用**。USDC 本位（如 `BTCUSDC`）按 §8 假设可用；不可用时该 quote 单次请求失败 fail-soft 空结果（无重试放大）。

### 2.2 端点、响应包络与解析

**响应包络（与 OKX 关键差异）**：OKX 全部包 `{"code":..,"data":[...]}`；Binance fapi **无包络**——`premiumIndex`/`openInterest` 直接返回**裸 dict**，`futures/data/*` 与 `fundingRate` 直接返回**裸数组**。解析必须按此区分，结构异常（类型不符/缺字段/空）一律该路缺省。

| 路 | 端点 | 响应形状 | 输出字段 | 解析要点 |
|----|------|----------|----------|----------|
| premiumIndex | `GET /fapi/v1/premiumIndex?symbol=` | 裸 dict | `funding_rate`（`lastFundingRate`）、`mark_price`（`markPrice`） | 一路给两字段 |
| openInterest | `GET /fapi/v1/openInterest?symbol=` | 裸 dict | 供换算（`openInterest`=基础币数量） | **不直接输出** |
| 全市场多空比 | `GET /futures/data/globalLongShortAccountRatio?symbol=&period=5m&limit=1` | 裸数组（升序，最新在末） | `long_short_ratio`（`longShortRatio`） | 取**末元素**（对 limit=1 返回最新/最旧两种解释都稳健） |
| 大户多空比 | `GET /futures/data/topLongShortAccountRatio?symbol=&period=5m&limit=1` | 同上 | `long_short_ratio_top` | 同上 |

### 2.3 `fetch_perp_metrics(base, quote) -> dict`

4 路并发（`ThreadPoolExecutor(max_workers=4)`，镜像 OKX 一次性发起），每路独立 fail-soft（异常 → 该路缺省，`logger.warning` 带 `[合约指标:binance]` 类标签）。

**OI 口径裁定**：Binance 只给基础币数量，与 OKX"张数"语义不同不可混 → **只填 `open_interest_usd = openInterest × markPrice`**（两值都解析成功才填），**永不填 `open_interest`**（张数字段 OKX 专属）。下游 presence-only 自动只渲染 USD。

presence-only：任一字段存在 → `out["source"] = "binance"`；全空 → `{}`。

### 2.4 `fetch_funding_rate_history(base, quote, start_ms, end_ms) -> list`

`GET /fapi/v1/fundingRate?symbol=&startTime={start_ms}&endTime={end_ms}&limit=1000`：
- 服务端窗口过滤；`limit=1000`（§8 验证项）≥ 窗口最大需求（`eval_window_days ≤ 120` → 3N ≤ 360 条），**不实现分页**：若返回行数 == limit（截断迹象）仍只用已得数据——与 OKX 路超页上限"返回已采集部分（偏低估，fail-soft）"语义一致。
- 返回升序裸数组 `[{fundingTime, fundingRate, ...}]`；客户端再按半开 `start_ms <= fundingTime < end_ms` 过滤一道保险（`endTime` 服务端包含/排除语义无论哪种，最终结果一致——这正是客户端过滤存在的目的）。
- 任何异常/结构异常 → `[]`（fail-soft）。

## 3. 编排改动（`data_provider/crypto_derivatives.py`）

**延迟 import 方向**：binance 模块**顶层** import 本模块 helpers；本模块仅在降级分支**函数体内** `from data_provider import binance_derivatives as bd`——加载期无环，首调时 binance 模块完成初始化，无状态遗漏。

- `fetch_perp_metrics`：**插入点精确定义（对抗审查修正）**——降级分支放在 presence-only 字段组装完成之后、`if out:` 判断**之前**（与 `if out:` 同缩进层级；若按初稿"插在 `if out:` 与 `return` 之间"且误读缩进会成为不可达代码）。最终结构：

```python
    # ...（5 路收集与 presence-only 组装，原样）...
    if not out:  # OKX 整组全空（含全部失败）→ 整源降级 Binance（fapi）
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环
        return bd.fetch_perp_metrics(base, quote)            # 空时返回 {}，presence-only 契约保持
    out["source"] = "okx"
    return out
```

（原 `if out: out["source"] = "okx"` 收紧为无条件赋值——执行到此处 out 必非空，语义等价且消除歧义。）

- `fetch_funding_rate_history`：分页循环结束后、返回前：

```python
    if not rates:  # OKX 窗口内无数据/抓取失败 → 整源降级 Binance
        from data_provider import binance_derivatives as bd
        return bd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
    return rates
```

**触发语义边界**：
- 非线性计价 guard 在两函数最前 `return {}`/`[]`，**不**触发 Binance（两所同样只支持线性，零网络调用不变）。
- OKX **部分成功不降级**（presence-only 接受部分字段）。
- 资金费历史的 OKX"真无数据"与"抓取失败"现状都归 `[]`（既有 fail-soft），两者都送 Binance 试一次，Binance 也无则仍 `[]`——下游语义不变。
- 4xx（含 451）沿 `_http_get_json`"4xx 不重试"语义 → 快速降级。

**正常路径零扰动**：OKX 可达时降级分支不触发，行为与现状一致（§5 以"OKX 非空 → 零 Binance 调用"锁测）。

## 4. 配置

对称既有 `binance_base_url` 的实际注册面（复核修正：它仅注册于 `src/config.py:940,1780`，**不在** config_registry——初稿引用的审查结论有误，新变量不单方面进 registry 以免不对称）：

- `src/config.py`：Config 增 `binance_fapi_base_url` 字段（默认 `https://fapi.binance.com`，读 env `BINANCE_FAPI_BASE_URL`，与 `binance_base_url` 两处同型）——注：抓取模块本身仍直读 env（分层纪律：data_provider 不 import src.*，与现货 `binance_fetcher.py` 直读 `BINANCE_BASE_URL` 同模式）；config 注册为配置一致性。
- `.env.example` 追加（注释写清**作用范围**，避免与现货变量混淆）：

```bash
# Binance fapi（USDT/USDC 本位合约）基址：仅用于衍生品备援路径（OKX 整组不可得时降级）；
# 与现货行情的 BINANCE_BASE_URL 互不影响。受限地区可换镜像域名，默认官方，不配置也可运行。
# BINANCE_FAPI_BASE_URL=https://fapi.binance.com
```

不新增优先级/开关；衍生品总门控沿用 `CRYPTO_DERIVATIVES_ENABLED`。

## 5. 测试

**monkeypatch 落点注意**：binance 模块经 `from ... import` 绑定 helpers，`cd._http_get_json` 与 `bd._http_get_json` 是**两个独立名字**——OKX 路 patch `cd.`，Binance 路 patch `bd.`，降级编排测试两个都 patch。`bd._http_get_json` 是 binance 模块唯一网络出口（spy 它足以证明零 Binance 网络活动）。

**既有用例改动面（对抗审查修正——初稿"零改动"声明不成立）**：恰有 **2 个**既有用例会命中新降级分支并经未打补丁的 `bd._http_get_json` 发起**真实网络请求**（违反离线纪律；且在 Binance 可达环境断言会翻车）：
- `tests/test_crypto_derivatives_fetch.py::test_fetch_perp_metrics_all_fail_returns_empty`
- `tests/test_crypto_funding_history.py::test_exception_fails_soft_to_empty`

两者各加一行 `monkeypatch.setattr(bd, "_http_get_json", boom)`（语义升级为"双源全挂"用例，断言不变）；**其余既有用例零改动**（snapshot/backtest-service 测试 mock 整函数不触分支；分页/截断用例返回非空不触发；非线性 guard 在分支前）。

- 新 `tests/test_binance_derivatives.py`（全离线）：
  - symbol 拼接（BTC/USDT→BTCUSDT）；非线性 quote/空 base → 空结果且零网络调用（spy 计数）。
  - premiumIndex 一路给 funding_rate+mark_price；裸 dict 包络解析。
  - OI USD 换算：`openInterest × markPrice`；缺任一 → 无 `open_interest_usd`；**任何情况不出现 `open_interest` 键**。
  - 多空比裸数组升序取末元素（fake 给两条升序数据断言取新值）；空数组/单元素/limit 失效多元素/缺 `longShortRatio` 字段 → 该路缺省。
  - 资金费历史：升序解析、客户端半开过滤（边界：`fundingTime == end_ms` 排除）、`len==limit` 截断仍返回已得、异常/结构异常 → `[]`。
  - 单路失败其余保留；全挂 → `{}`；`source='binance'` 仅非空时存在；`_fapi_base()` env 覆盖生效。
- 扩展 `tests/test_crypto_derivatives_fetch.py`：
  - OKX 全空 + Binance 成功 → Binance 字段、`source='binance'`。
  - OKX 部分成功（仅 funding）→ `source='okx'`、**零** Binance 调用（spy `bd._http_get_json`==0）。
  - 双源全挂 → `{}`；451 模拟（`requests.HTTPError` 4xx）→ OKX 全空 → Binance 接管（同时锁"4xx 单次失败不重试"：cd 路 spy 调用次数 == 路数）。
  - 历史：OKX 空 → Binance 同窗口接管；OKX 非空 → 零 Binance 调用。
- `src/analyzer.py` 动态来源标题：扩展 `tests/test_crypto_derivatives_prompt.py`——`source:'binance'` → 标题含「来源 BINANCE」；无 source → 默认「来源 OKX」；既有「合约市场指标」断言不破（标题前缀不变）。

验证命令：`.venv/bin/python -m pytest tests/test_binance_derivatives.py tests/test_crypto_derivatives_fetch.py tests/test_crypto_funding_history.py tests/test_crypto_perp_snapshot.py tests/test_crypto_derivatives_prompt.py -q` 后全量 `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`。

## 6. 文档

- `docs/crypto-guide.md`：
  - 「加密永续合约指标」：现行文案「来源 OKX」相关表述改为「OKX 主源，整组不可得时自动降级 Binance fapi，`source` 字段标实际来源；**Binance 路 OI 仅 USD 口径（无张数）**」。
  - 「永续回测（资金费 + 做空，1x）」资金费条目补一句兜底语义（OKX 拉不到时自动改用 Binance fapi 同窗口）。
  - 配置说明处补 `BINANCE_FAPI_BASE_URL`（作用范围同 §4 注释）。
- `src/core/config_registry.py:~3887`：`crypto_derivatives_enabled` 条目描述现写 "(sourced from OKX public APIs)"——降级落地后失真，改为 "(OKX primary, Binance fapi whole-source fallback)" 类表述（防契约漂移的顺手修正，与本期直接相关）。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平一行：`- [新功能] crypto 衍生品抓取新增 Binance fapi 整源兜底（OKX 全空才降级，source 标实际来源并动态进 prompt；回测资金费历史同享；新增 BINANCE_FAPI_BASE_URL 可换镜像，默认官方域名）`。

## 7. 风险与回滚

- **正常路径零风险**：OKX 可达时降级分支不触发（锁测 + 既有用例回归）。
- **最坏延迟**：仅 OKX 全挂时新增一次 4 路并发（历史 1 路），4xx 不重试快速失败。
- **Binance 权重（降级模式下）**：`futures/data/*` 两个多空比端点权重显著高于普通行情端点；复盘篮子 N 币全降级时一轮 ≈ N×(2 高权重 + 2 低权重) 请求。默认篮子规模（个位数～十余币）远低于 IP 限额，可接受；若用户配置超大篮子且长期处于降级态，限频会使部分路 fail-soft 缺省（不崩溃）。记录于此，不为此加节流（YAGNI）。
- **数据口径**：Binance 行无张数 OI、两所多空比统计口径不同——`source` 如实标注（含 prompt 动态标题），单行内口径纯净。
- **回滚**：按提交 revert；删降级分支 + 新模块 + analyzer 动态标题即回 OKX 单源，无契约破坏。

## 8. 未验证假设（实施/真实环境确认）

本环境 Binance 451，**在线验证不可能**，以下依据公开 API 文档与离线 mock，留待 OKX 受限环境/镜像域名实测：

- `futures/data/globalLongShortAccountRatio`/`topLongShortAccountRatio`：返回**裸数组升序**且 `limit=1` 给**最新**一条（实现取末元素，对 limit 行为两种解释稳健）；注意该族端点仅保留约 30 天历史——取最新值用法不受影响。
- `/fapi/v1/fundingRate`：`limit` 上限为 1000 且单页可容窗口需求（≤360 条）；`endTime` 包含/排除语义（客户端半开过滤兜底，两种语义最终结果一致）。
- `premiumIndex.lastFundingRate` 为当期资金费率小数（与 OKX `fundingRate` 同量纲）。
- `premiumIndex`/`openInterest` 返回**裸 dict**（无 `data` 包络）。
- USDC 本位线性永续（如 `BTCUSDC`）在 fapi 同端点可用；不可用时该 quote 单次失败 fail-soft 空结果。
