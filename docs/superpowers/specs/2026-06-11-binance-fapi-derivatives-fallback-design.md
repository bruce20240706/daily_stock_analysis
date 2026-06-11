# 设计：Binance fapi 衍生品备援（整源降级，OKX 主源不变）

- 日期：2026-06-11
- 状态：设计已批准（方案 B：独立 Binance 模块 + 整源降级编排），待写实施计划
- 子项目：perp 系列韧性补强——衍生品抓取从 OKX 单源升级为 OKX 主 / Binance fapi 兜底
- 关联前序：perp 指标（A/B/多空比）、perp 回测资金费（E）均依赖 `data_provider/crypto_derivatives.py` 的 OKX 单源抓取

## 0. 背景与动机

衍生品数据现状全部 OKX 单源：`fetch_perp_metrics`（5 路实时：资金费/标记价/OI/全市场多空比/大户多空比，供单股注入、复盘 per-coin 与聚合）与 `fetch_funding_rate_history`（回测资金费）。OKX 不可达时：实时指标 presence-only 整组缺省（分析 prompt 失去合约视角）；回测资金费**静默记 0**（隐性数据质量损失）。

Binance fapi 对两个抓取面都有免 key 对等端点。本环境实测 Binance 451（地区限制）、OKX 200——**备援在本机永远不触发**，价值在 OKX 受限/故障的其他部署环境；本地只能离线 mock 验证（见 §5/§8）。

## 1. 范围与零改动面

**只改**：新建 `data_provider/binance_derivatives.py`；修改 `data_provider/crypto_derivatives.py`（两个公开函数尾部加整源降级分支）；`.env.example`（+1 增强型变量）；测试与文档。

**零改动**：下游全部契约——`CryptoDerivativesService`/`CryptoDerivativesReviewService`/backtest service 调用的函数签名与输出形状不变；`source` 保持单值（`'okx'` 或 `'binance'`）；门控仍由 service 层 `crypto_derivatives_enabled` 承担；DB schema、API schema、Web/Desktop、聚合层（`fetch_perp_market_snapshot`/`_perp_row_for_symbol` 经模块内全局引用自动随降级受益）。

**明确不做（YAGNI）**：
- 不做优先级配置（固定 OKX→Binance；两源场景配置价值低）。
- 不做逐路混源补缺（OKX 部分成功即用 OKX——presence-only 本就接受部分字段；混源行有 OI 张数/多空比统计口径噪音，且 `source` 契约会漂移）。
- 不引入多源 fetcher 注册框架（两源 YAGNI）。
- 不改 Coinbase 等第三源（无衍生品公共对等物）。

## 2. 新模块 `data_provider/binance_derivatives.py`

分层纪律同 `crypto_derivatives.py`：不 import `src.*`、不碰 DB；helpers 顶层复用 `from data_provider.crypto_derivatives import _http_get_json, _to_float`（不平行实现；无循环——见 §3 延迟 import 方向）。

### 2.1 基址与 symbol

```python
def _fapi_base() -> str:
    """Binance fapi 基址；BINANCE_FAPI_BASE_URL 可覆盖（受限地区换镜像），默认官方域名。"""
    return (os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")
```

symbol：`BASE+QUOTE` 直接拼接（`BTC`,`USDT` → `BTCUSDT`）；guard 与 OKX 同：`quote ∉ {USDT, USDC}` 或 base 为空 → 空结果、零网络调用（`_LINEAR_QUOTES` 从 crypto_derivatives 导入复用）。

### 2.2 端点与解析（响应形状以 §8 验证项兜底）

| 路 | 端点 | 输出字段 | 解析要点 |
|----|------|----------|----------|
| premiumIndex | `GET /fapi/v1/premiumIndex?symbol=` | `funding_rate`（`lastFundingRate`）、`mark_price`（`markPrice`） | 单 dict，一路给两字段 |
| openInterest | `GET /fapi/v1/openInterest?symbol=` | 供换算（`openInterest`=基础币数量） | **不直接输出** |
| 全市场多空比 | `GET /futures/data/globalLongShortAccountRatio?symbol=&period=5m&limit=1` | `long_short_ratio`（`longShortRatio`） | 数组**升序**（最新在末），取**末元素** |
| 大户多空比 | `GET /futures/data/topLongShortAccountRatio?symbol=&period=5m&limit=1` | `long_short_ratio_top` | 同上 |

### 2.3 `fetch_perp_metrics(base, quote) -> dict`

4 路并发（`ThreadPoolExecutor(max_workers=4)`，镜像 OKX 的一次性发起模式），每路独立 fail-soft（异常 → 该路缺省，`logger.warning` 带 `[合约指标:binance]` 类标签）。

**OI 口径裁定**：Binance 只给基础币数量，与 OKX 的"张数"语义不同不可混 → **只填 `open_interest_usd = openInterest × markPrice`**（两值都解析成功才填），**永不填 `open_interest`**（张数字段 OKX 专属）。下游 presence-only 自动只渲染 USD。

presence-only：任一字段存在 → `out["source"] = "binance"`；全空 → `{}`。

### 2.4 `fetch_funding_rate_history(base, quote, start_ms, end_ms) -> list`

`GET /fapi/v1/fundingRate?symbol=&startTime={start_ms}&endTime={end_ms}&limit=1000`：
- 服务端窗口过滤；单页 `limit=1000` ≥ 窗口最大需求（`eval_window_days ≤ 120` → 3N ≤ 360 条），**无需分页**。
- 返回升序数组 `[{fundingTime, fundingRate, ...}]`；客户端再按半开 `start_ms <= fundingTime < end_ms` 过滤一道保险（`endTime` 服务端包含语义见 §8 验证项；客户端过滤兜底使语义与 OKX 路完全一致）。
- 任何异常/结构异常 → `[]`（fail-soft）。

## 3. 编排改动（`data_provider/crypto_derivatives.py`）

两个公开函数尾部各加一个整源降级分支；**延迟 import** 避免循环依赖（binance 模块顶层 import 本模块 helpers，本模块仅在降级路径函数体内 import binance 模块）：

- `fetch_perp_metrics`：现有 5 路收集与 presence-only 组装**原样**；在 `if out: out["source"]="okx"` 与 `return out` 之间插入：

```python
    if not out:  # OKX 整组全空（含全部失败）→ 整源降级 Binance（fapi）
        from data_provider import binance_derivatives as bd  # 延迟 import 防循环
        return bd.fetch_perp_metrics(base, quote)
```

- `fetch_funding_rate_history`：循环结束后、`return rates` 之前插入：

```python
    if not rates:  # OKX 窗口内无数据/抓取失败 → 整源降级 Binance
        from data_provider import binance_derivatives as bd
        return bd.fetch_funding_rate_history(base, quote, start_ms, end_ms)
    return rates
```

**触发语义边界**：
- 非线性计价 guard 在两函数最前 `return {}`/`[]`，**不**触发 Binance（两所同样只支持线性，保持零网络调用不变——既有零调用测试零改动）。
- OKX **部分成功不降级**（如仅 funding 一路成功，整行用 OKX，presence-only 接受部分字段）。
- 资金费历史的 OKX "真无数据" 与 "抓取失败" 现状都归 `[]`（既有 fail-soft），两者都送 Binance 试一次，Binance 也无则仍 `[]`——下游语义不变。
- 4xx（含 451）沿 `_http_get_json` 既有"4xx 不重试"语义 → 快速降级，不放大延迟。

**正常路径零扰动**：OKX 可达时降级分支不触发，行为与现状字节级一致（强回归不变量，§5 锁测）。

## 4. 配置

`.env.example` 追加（增强型：不配置也可运行）：

```bash
# Binance fapi（衍生品备援）基址：OKX 不可用且 Binance 受限的地区可换镜像域名，默认官方
# BINANCE_FAPI_BASE_URL=https://fapi.binance.com
```

不新增优先级/开关；衍生品总门控沿用 `CRYPTO_DERIVATIVES_ENABLED`。

## 5. 测试

**monkeypatch 落点注意**：binance 模块经 `from ... import` 绑定 helpers，`cd._http_get_json` 与 `bd._http_get_json` 是**两个独立名字**——OKX 路 patch `cd._http_get_json`，Binance 路 patch `bd._http_get_json`，降级编排测试两个都 patch（这是测试隔离的天然边界，必须在用例里写对）。

- 新 `tests/test_binance_derivatives.py`（Binance 模块单测，全离线）：
  - symbol 拼接（BTC/USDT→BTCUSDT）；非线性 quote/空 base → 空结果且零网络调用（spy 计数）。
  - premiumIndex 一路给 funding_rate+mark_price 两字段。
  - OI USD 换算：`openInterest × markPrice`；缺 markPrice 或缺 openInterest → 无 `open_interest_usd`；**任何情况不出现 `open_interest` 键**。
  - 多空比升序数组取末元素（fake 给两条升序数据，断言取到新值）。
  - 资金费历史：升序数组解析、客户端半开窗口过滤（含边界：`fundingTime == end_ms` 被排除）、异常/结构异常 → `[]`。
  - 单路失败其余保留；全挂 → `{}`；`source='binance'` 仅在非空时存在。
  - `_fapi_base()`：env 覆盖生效（monkeypatch env）。
- 扩展 `tests/test_crypto_derivatives_fetch.py`（**既有用例零改动**——锁"正常路径零扰动"）：
  - OKX 全空（cd 路全挂）+ Binance 成功 → 输出 Binance 字段、`source='binance'`。
  - OKX 部分成功（仅 funding）→ 用 OKX、`source='okx'`、**零** Binance 调用（spy `bd._http_get_json` 计数为 0）。
  - 双源全挂 → `{}`（metrics）/`[]`（history）。
  - 451 模拟（`requests.HTTPError` 4xx 经 `_http_get_json` 抛出）→ OKX 各路 fail-soft 全空 → Binance 接管。
  - 资金费历史：OKX 空列表 → Binance 同窗口接管；OKX 非空 → 零 Binance 调用。
  - 非线性 quote → 两源都零调用（既有用例之外补 binance spy 断言）。

验证命令：`.venv/bin/python -m pytest tests/test_binance_derivatives.py tests/test_crypto_derivatives_fetch.py tests/test_crypto_perp_snapshot.py tests/test_crypto_funding_history.py -q` 后全量 `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`。

## 6. 文档

- `docs/crypto-guide.md`：「加密永续合约指标」补数据源说明（OKX 主源，整组不可得时自动降级 Binance fapi，`source` 字段标实际来源；Binance 路 OI 仅 USD 口径）；「永续回测（资金费 + 做空，1x）」的资金费条目补一句兜底语义；配置小节/表（若有）补 `BINANCE_FAPI_BASE_URL`。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平一行：`- [新功能] crypto 衍生品抓取新增 Binance fapi 整源兜底（OKX 全空才降级，source 标实际来源；回测资金费历史同享；新增 BINANCE_FAPI_BASE_URL 可换镜像，默认官方域名）`。

## 7. 风险与回滚

- **正常路径零风险**：OKX 可达时降级分支不触发（既有用例零改动作回归证据）。
- **最坏延迟**：仅 OKX 全挂时新增一次 4 路并发（资金费历史为 1 路），且 4xx 不重试快速失败。
- **数据口径**：Binance 行无张数 OI、两所多空比统计口径不同——`source` 字段如实标注，单行内口径纯净（整源降级的设计目的）。
- **回滚**：按提交 revert；删降级分支 + 新模块即回到 OKX 单源，无契约破坏。

## 8. 未验证假设（实施/真实环境确认）

本环境 Binance 451，**在线验证不可能**，以下依据公开 API 文档与离线 mock，留待 OKX 受限环境/镜像域名实测：

- `futures/data/globalLongShortAccountRatio`/`topLongShortAccountRatio` 返回**升序**且 `limit=1` 给**最新**一条（实现取末元素对两种 limit 行为都稳健）。
- `/fapi/v1/fundingRate` 的 `endTime` 为**包含**语义（客户端半开过滤兜底，包含/排除都不影响最终结果一致性）。
- `premiumIndex.lastFundingRate` 为当期资金费率小数（与 OKX `fundingRate` 同量纲）。
- USDC 本位线性永续（如 `BTCUSDC`）在 fapi 同端点可用；不可用时该 quote 自然 fail-soft 空结果。
