# crypto 专属大盘指标 设计（BTC 主导率 / 总市值 / 恐贪指数）

> 阶段二后续项。把 crypto 大盘复盘从「主流币篮子 + 叙事」补齐到含顶层宏观指标。
> 镜像已落地的 `new_listings` 范式：data_provider 纯抓取 → src 服务编排 → presence-only payload 字段 → Web 渲染 + LLM 叙事引用。

**Goal:** 在 crypto 大盘复盘中新增三项免费、无 key 的宏观指标——BTC 主导率、加密总市值（含 24h 变化）、恐贪指数——以 presence-only 字段并入复盘 payload，并注入复盘 prompt 供 LLM 叙事引用。

**范围决策（已确认）：** 全部三项指标（CoinGecko `/global` 顺手免费带 ETH 主导率）；结构化字段 + 喂入 LLM prompt；默认开（免费无 key、additive、失败降级为空、无持久化依赖）。

---

## 1. 背景与现状

`crypto 大盘复盘`（`feat/crypto-market-review`，已在本地栈上）当前产出：
- `indices`：12 主流币篮子（`data_provider/base.py::_get_crypto_basket_indices`，逐币实时价）
- `news` + LLM 叙事报告；`new_listings`（结构化上新，presence-only）
- 跳过 `breadth` / `market_light`（crypto `has_market_stats=False`）

缺口（`crypto-guide.md §9 已知限制`）：**crypto 专属大盘指标（BTC 主导率 / 总市值 / 恐贪指数，需引入新数据源）**。本设计实现该项。

数据源已实测（2026-06-09，免费、无 key、可达）：
- **CoinGecko `/api/v3/global`**（HTTP 200）：`data.market_cap_percentage.{btc,eth}`、`data.total_market_cap.usd`、`data.market_cap_change_percentage_24h_usd`、`data.total_volume.usd`。
- **alternative.me `/fng/`**（HTTP 200）：`data[0].{value, value_classification, timestamp}`。

> 注：CoinGecko 的「新上线」端点是付费的（已在 new-listings 阶段确认并排除），但 `/global` 在免费层。两者不同端点，互不影响。

## 2. 架构与组件（方案 A：镜像 new_listings）

分层纪律：`data_provider/` 纯抓取（不 import `src.*`、不碰 DB、不做编排）；合并/presence-only/配置门控在 `src` 服务层。

### 2.1 `data_provider/crypto_market_indicators.py`（新，纯抓取）

```python
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
FNG_URL = "https://api.alternative.me/fng/"

def fetch_global_market() -> dict:
    """CoinGecko /global → 宏观聚合；presence-only（缺字段不塞）；失败/非 dict → {}。"""
    # 返回键（均 presence-only）：
    #   btc_dominance, eth_dominance (float, %)
    #   total_market_cap_usd (float), market_cap_change_24h_pct (float), total_volume_usd (float)

def fetch_fear_greed() -> dict:
    """alternative.me /fng → 情绪；失败/空 → {}。"""
    # 返回：{value:int, classification:str, timestamp:int}（任一缺失则整体视为不可用 → {}）
    # 注意：API 的 value / timestamp 为字符串，需 int() 解析（解析失败 → {}）；classification 原样取字符串。
```

- 本地 `_http_get_json` + `_fetch_timeout()` / `_fetch_max_retries()`，复用 `CRYPTO_FETCH_TIMEOUT_SECONDS` / `CRYPTO_FETCH_MAX_RETRIES`，4xx 不重试。遵循本仓库「零跨层依赖、就地复制而非跨模块 import 私有」约定（与 `crypto_base.py` / `crypto_new_listings.py` 一致）。
- 解析守卫：`isinstance(data, dict)`；数值用 `float()` 包裹并 `try/except` 跳过非法值；presence-only（只放成功解析出的键）。

### 2.2 `src/services/crypto_market_indicator_service.py`（新，编排）

```python
class CryptoMarketIndicatorService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def collect(self) -> dict:
        # 1. disabled（crypto_market_indicators_enabled=False）→ {}
        # 2. 调 cmi.fetch_global_market() / cmi.fetch_fear_greed()（经模块引用，便于 mock）
        # 3. 合并为单 dict：global 各键平铺 + fear_greed 子 dict（presence-only）
        # 4. 任一源失败保留另一源；两源全空 → {}
```

- 通过模块引用调用 data_provider 函数（`import data_provider.crypto_market_indicators as cmi`），便于测试 monkeypatch。

### 2.3 `src/market_analyzer.py`

- `_get_crypto_market_indicators() -> dict`：`region != "crypto"` → `{}`；`try/except → {}`（镜像 `_get_crypto_new_listings`）。
- `build_market_review_payload(..., market_indicators: Optional[dict] = None)`：`if market_indicators: payload["market_indicators"] = market_indicators`（presence-only，`version` 不变）。
- **prompt 注入**：
  - `_run_daily_review_parts` 在 `generate_market_review` **之前** 取 `indicators`，传入报告生成。
  - `generate_market_review(overview, news, indicators=None)` → `_build_review_prompt(overview, news, indicators=None)`。
  - 新增 `_get_crypto_indicators_prompt_block(indicators, review_language) -> str`：`region != "crypto"` 或 `not indicators` → `""`；否则渲染**事实块**（中英双语），插入 crypto prompt 中（「主要指数 / Major Indices」附近）。
  - `_get_crypto_addendum_prompt` 追加一句指令：「结合上述宏观指标点评市场情绪与结构，不得编造数据」/ EN 对应句。

### 2.4 配置

- `src/config.py`：`crypto_market_indicators_enabled: bool = True`（env `CRYPTO_MARKET_INDICATORS_ENABLED`，沿用现有 bool 解析）。
- `src/core/config_registry.py`：新增条目，`help_key = "settings.data_source.crypto_market_indicators"`。
- `apps/dsa-web/src/locales/settingsHelp.ts`：新增 `settings.data_source.crypto_market_indicators`（zh + en）——**必须同步**，否则 `test_registry_help_keys_exist_in_locales` 回归（new-listings 阶段曾被此咬到）。
- 复用 `CRYPTO_FETCH_*`（不新增超时/重试旋钮）。
- `.env.example`：新增 `CRYPTO_MARKET_INDICATORS_ENABLED=true` + 注释。

### 2.5 Web（`apps/dsa-web`）

- `src/types/analysis.ts`：新增 `MarketIndicators` 接口（camelCase：`btcDominance` / `ethDominance` / `totalMarketCapUsd` / `marketCapChange24hPct` / `totalVolumeUsd` / `fearGreed: {value, classification, timestamp}`）+ `marketIndicators?` 挂到 `MarketReviewPayload`。
- `src/components/.../MarketReviewReportView.tsx`：crypto 复盘顶部新增「市场指标」条/卡片；守卫 `region === 'crypto' && marketIndicators`；逐字段 presence-only（缺省显示 `—`）；恐贪值附 `classification` 文案。
- i18n：标签键（主导率 / 总市值 / 24h 变化 / 恐贪指数）zh + en。
- payload snake_case 由 `camelcase-keys {deep:true}` 自动转 camelCase（`market_cap_change_24h_pct → marketCapChange24hPct`、`fear_greed → fearGreed`）。

### 2.6 文档

- `docs/crypto-guide.md`：新增「加密市场宏观指标」节（字段契约、来源、presence-only、默认开）；§9 已知限制中**移除**「crypto 专属大盘指标」一项。
- `docs/CHANGELOG.md`：`[Unreleased]` 扁平格式新增 `- [新功能] ...` 一行。

## 3. 数据流

```
overview = get_market_overview()
news     = search_market_news()
indicators = _get_crypto_market_indicators()        # 新增；在报告生成之前
report   = generate_market_review(overview, news, indicators)   # prompt 含事实块
snapshot = None（crypto）
new_listings = _get_crypto_new_listings()
payload  = build_market_review_payload(overview, news, report, snapshot,
                                       new_listings=..., market_indicators=indicators)
→ payload["market_indicators"]（presence-only）→ Web 渲染 / LLM 叙事已引用
```

## 4. 错误处理（全程降级，不拖垮复盘）

- 任一源失败/超时/4xx → 该子字段缺省（不塞 0、不编造）。
- 两源全失败 或 `disabled` → payload 不含 `market_indicators`；prompt 无事实块；复盘照常进行。
- 4xx 不重试；超时/重试沿用 `CRYPTO_FETCH_*`。
- `_get_crypto_market_indicators` 顶层 `try/except → {}`，任何异常不外溢到复盘主流程。

## 5. payload 契约（presence-only、additive、`version` 不变）

```json
"market_indicators": {
  "btc_dominance": 56.07,
  "eth_dominance": 8.98,
  "total_market_cap_usd": 2241017397766,
  "market_cap_change_24h_pct": -0.64,
  "total_volume_usd": 91620725291,
  "fear_greed": {"value": 10, "classification": "Extreme Fear", "timestamp": 1780963200}
}
```

- 缺失字段一律省略（不出现 `null`/`0` 占位）。
- 仅 `region == "crypto"` 复盘可能出现该字段；cn/hk/us payload 不受影响。

## 6. 测试

| 层 | 用例 |
|---|---|
| data_provider | `fetch_global_market` 解析（mock 真实形 JSON）、非 dict→{}、缺字段 presence-only、`fetch_fear_greed` 解析 / 失败→{} |
| service | `collect()` 合并 presence-only、`disabled→{}`、单源失败保留另一源、两源全空→{} |
| payload | `build_market_review_payload` 含 `market_indicators`（有值）/ 省略（空）、`version` 不变 |
| prompt | crypto + 有 indicators → 含事实块；无 indicators → 不含；非 crypto → 不含 |
| e2e | crypto 复盘 payload 含 `market_indicators`（presence-only，mock 两源） |
| Web vitest | 渲染守卫：crypto + present → 渲染；非 crypto / 缺省 → 不渲染；逐字段缺省显示 `—` |

离线测试一律 mock 网络；真实可达性走 `network-smoke`（已手测两源 200）。

## 7. 分支与回滚

- 分支：`feat/crypto-market-indicators`，基于当前 HEAD（`feat/crypto-new-listings`）；栈：`main ← crypto-market-review ← crypto-new-listings ← crypto-market-indicators`。
- 回滚：改动以新增文件 + 1 个 presence-only payload 字段 + 1 个 prompt 事实块 + 1 个 Web 卡片为主；`git revert` 或丢弃分支即恢复;`CRYPTO_MARKET_INDICATORS_ENABLED=false` 可运行时关闭。

## 8. 范围边界（YAGNI）

不做：altcoin season 指数、per-coin 主导率历史、恐贪历史曲线、独立 sources 开关（单一 enable 旗标）、合约/链上指标、把指标注入 markdown 表格（Web 从 payload 渲染即可，不动现有表格注入机制）。仅 crypto region 触发。
