# 结构化新币上新发现 + 上新行情表 设计

- 状态：已评审，待实施（writing-plans）
- 日期：2026-06-08
- 范围类型：feat（高风险区：新数据源 / 报告结构 / 持久化 / 数据源 fallback）
- 依赖：建立在 `feat/crypto-market-review`（crypto 大盘复盘，未合入）之上——本特性扩展该复盘的 payload 与 Web 视图。实施分支从 `feat/crypto-market-review` 切出。
- 关联：crypto 大盘复盘设计 `docs/superpowers/specs/2026-06-08-crypto-market-review-design.md`（§11 已把本特性拆为后续子项目）

## 0. 目标 / 非目标

### 目标
为 crypto 大盘复盘新增**结构化新币发现**：自动识别近期新上线的现货币种，附实时行情，产出"上新行情表"，作为复盘 payload 的结构化 `new_listings` 字段（presence-only、additive）。

### 非目标
- 不引入付费数据源：CoinGecko `/coins/list/new`、CMC `/cryptocurrency/listings/new` 均为**付费**，按"无 API key"原则排除。
- 不替换现有"新币与上新动态"**新闻叙事段**（Tier-2，`src/market_analyzer.py:456/467/478`）。二者互补：叙事=背景/解读，结构化表=可核事实。
- 不依赖任何 API key；不假设 `api.binance.com` 可达（本环境 451）。
- 不做价格/上新的任何编造；缺数据一律 omit。

## 1. 可行性结论（2026-06-08 实测）

| 源 | 上市时间字段 | key? | 状态需求 | 结论 |
|---|---|---|---|---|
| **OKX** `GET https://www.okx.com/api/v5/public/instruments?instType=SPOT` | **`listTime`**(ms，原生) | 否 | 无状态 | 最干净，`state=live`/`instId/baseCcy/quoteCcy` 齐全（HIGH） |
| **Coinbase** `GET https://api.coinbase.com/api/v3/brokerage/market/products` | **`new_at`**(ISO8601，原生) | 否(UA 头) | 无状态 | 原生时间戳；按 `base_currency_id` 去重；list 端点 CDN 缓存 ~4h（HIGH） |
| **Binance** `GET https://data-api.binance.vision/api/v3/exchangeInfo` | **无**(spot 无 onboardDate) | 否 | **需快照差分** | 仅能"本次 symbol 集 vs 上一份快照"推新；`api.binance.com` 451，必须用 `.vision` 镜像（HIGH） |
| ~~CoinGecko/CMC new-listings~~ | activated_at/date_added | **付费** | — | 排除 |
| ~~Binance bapi 公告~~ | releaseDate | 否(非官方) | — | 不可移植/易 403，**不采用** |

## 2. 架构

```
crypto 复盘(MARKET_REVIEW_REGION=crypto)
  └─ 发现聚合器 (data_provider/crypto_new_listings.py)
       ├─ OKX instruments(listTime, 无状态)        ┐
       ├─ Coinbase products(new_at, 无状态)         ├─ 各源 try/except 隔离
       └─ Binance exchangeInfo 快照差分(读+写 DB)   ┘
       → 归一化记录 → 按 base asset 去重 → 窗口/max 截断 → 倒序
  └─ 行情富化 (复用 DataFetcherManager.get_realtime_quote, presence-only)
  └─ build_market_review_payload 追加 additive new_listings[]
  └─ 持久化(DB history 不变) + Web 上新行情表渲染
```

5 个职责清晰的单元：发现源解析（每源一个纯函数/类）、聚合器（合并/去重/窗口）、快照持久化（Binance 差分用）、行情富化、payload/渲染集成。

## 3. 发现源（新模块 `data_provider/crypto_new_listings.py`）

- **不 import `src.config`**（与 `crypto_base.py` 隔离纪律一致，验证：crypto_base 仅 import `.base`/`.realtime_types`）。配置经参数从 manager 传入。
- 复用 `CryptoExchangeBase._http_get` 风格的超时/重试（env `CRYPTO_FETCH_TIMEOUT_SECONDS`/`CRYPTO_FETCH_MAX_RETRIES`，`data_provider/crypto_base.py:80-96`）。
- 归一化记录（dataclass）：
  ```
  NewListing(base: str, quote: str, symbol: str, exchange: str,
             listed_at: Optional[int_ms], source: str)
  ```
- **OKX**：解析 `data[].{instId,baseCcy,quoteCcy,state,listTime}`；过滤 `state == "live"` 且 `listTime` 在窗口内。
- **Coinbase**：UA 头；解析 `products[].{product_id,base_currency_id,quote_currency_id,new_at,status}`；`status` 正常、`new_at` 在窗口内；按 `base_currency_id` 去重。
- **Binance**：`data-api.binance.vision/api/v3/exchangeInfo` → 取 `symbols[] where status=="TRADING" and isSpotTradingAllowed` 的 `(baseAsset,quoteAsset)` 集合；与持久化的上一份集合差分 → 新增 symbol（`listed_at=None`，因 spot 无上市时间）；随后写回新快照。
- 每源独立 try/except，失败记 `log` 并返回空列表，不抛断主流程。

## 4. 持久化（复用现有 DB）

- 系统使用 SQLAlchemy `DatabaseManager` + `src/repositories/*`。存 Binance spot symbol 集 + `captured_at`。
- **优先用现有轻量 kv/settings 载体**（避免新建表 / schema 迁移）；plan 阶段核定是否存在可复用的 kv/settings 表；若确无，加**最小专用表**并提供迁移与回滚说明（高风险区，需显式记录）。
- **优雅降级**：无上一份快照（首跑 / 临时 CI 无持久卷）→ 播种基线、本次不报 Binance 新上；OKX/Coinbase 无状态照常产出。Binance 覆盖仅在有持久卷处（本地 / Docker / 自托管）可靠——文档需写明。

## 5. 聚合 + 行情富化

- **去重**：按规范 base asset 跨所 union；保留 `exchanges`/`pairs` 列表与最早 `listed_at`。
- **窗口/截断**：`CRYPTO_NEW_LISTING_WINDOW_DAYS`（默认 7）过滤。排序键 = `listed_at`（原生时间戳）；Binance 差分项无 `listed_at`，统一用本次 run 的 `captured_at`（视为"本次发现"）作排序键，与原生时间戳一并倒序。`CRYPTO_NEW_LISTING_MAX`（默认 20）截断，`log` 丢弃数（不静默）。Binance 差分项的窗口过滤同样以 `captured_at` 计（首次发现即视为窗口内）。
- **行情富化**：对每个 base 复用 `DataFetcherManager.get_realtime_quote`（`data_provider/base.py:1553`，crypto 路由 `:1638`），计价对优先 `USDT` 否则发现到的 quote；附 `price/change_pct/volume`，**presence-only**——缺失/illiquid 则 omit 字段，绝不补 0（沿用 `UnifiedRealtimeQuote.to_dict` None 过滤）。

## 6. payload / 兼容性

- `build_market_review_payload`（`src/market_analyzer.py:559-626`）追加 **additive** `new_listings: List[dict]`，仅 crypto region 且非空时携带；`version` 保持 1。
- 每条结构示意：`{base, exchanges:[...], pairs:[...], listed_at?, quote_pair?, price?, change_pct?, volume?}`（`?` 字段 presence-only）。
- 现有 `markdown_report` 与"新币与上新动态"叙事段保留。cn/us/hk payload 零影响。

## 7. Web 渲染（`apps/dsa-web` `MarketReviewReportView.tsx`）

- crypto 且 `new_listings` 非空 → 渲染"上新行情表"卡：列 = 币种 / 交易所 / 上市时间 / 价格·涨跌幅（有则显示，缺则 `-`）。
- 缺失或非 crypto → 整卡 omit（不渲染占位）。复用现有 indices 表样式与 region-aware 渲染（Tier-2 已加 `region` 透传）。

## 8. 配置（manager 读 env；同步 `.env.example`/docs/CHANGELOG）

| env | 默认 | 含义 |
|---|---|---|
| `CRYPTO_NEW_LISTING_ENABLED` | `true` | 关闭则完全不发现（additive、空则自动 omit，开着也低风险） |
| `CRYPTO_NEW_LISTING_WINDOW_DAYS` | `7` | "新"窗口天数 |
| `CRYPTO_NEW_LISTING_SOURCES` | `okx,coinbase,binance` | 启用源与顺序 |
| `CRYPTO_NEW_LISTING_MAX` | `20` | 表最大行数 |

env 一律在 manager（或 service 层）读取，**不在 data_provider 内**（隔离纪律）。

## 9. 错误处理 / 稳定性护栏（AGENTS.md §7）

- 单源失败隔离；全部源失败 → `new_listings` omit，不报错、不拖垮 crypto 复盘。
- Binance 必须走 `data-api.binance.vision`（`api.binance.com` 451）；首跑无基线优雅播种。
- 无 API key；data_provider 不 import src.config；presence-only、无造数据；丢弃/降级均 `log`，不静默。
- 调度低频（日级），单次每源一调用，远低于各所限流（OKX 20req/2s、Coinbase ~10req/s）；Coinbase list CDN 缓存致 `new_at` 可能滞后数小时——文档写明，日级可接受。

## 10. 测试

后端（`tests/`）：
- 各源解析器：mock HTTP 返回样例 JSON（OKX 含 `listTime`、Coinbase 含 `new_at`、Binance exchangeInfo symbol 列表），断言归一化记录正确、窗口过滤生效、非法/缺字段跳过不崩。
- Binance 差分：有上一份快照→仅报新增 symbol；首跑无基线→播种、不报新上；快照写回。
- 去重：同币多所/多对 → 合并为一条 base，`exchanges`/`pairs` 聚合。
- 窗口 + max 截断（含丢弃计数日志）。
- 行情富化 presence-only：缺价 → omit 字段不补 0。
- payload：crypto 非空 → 携带 `new_listings`；空或非 crypto → omit；version 不变。
- 单源失败/全失败：隔离、整段 omit、不抛。
- Web：`new_listings` 非空渲染表；空/非 crypto omit；cn/us/hk 不受影响。
- 离线 e2e：mock 三源 + 行情，跑通 crypto 复盘，断言 payload 含 `new_listings` 且 presence-only。

验证矩阵：`./scripts/ci_gate.sh` + `pytest -m "not network"`；Web 在无空格副本跑 vitest/lint 或交 CI `web-gate`。在线源验证（OKX/Coinbase/Binance.vision 真实可达性）走 `network-smoke`，离线测试一律 mock。

## 11. 回滚

- 特性 opt-in：`CRYPTO_NEW_LISTING_ENABLED=false` 可关；且仅 crypto 复盘触发。
- 回滚 = revert 本特性 commit；默认配置、cn/hk/us、现有 crypto 复盘与叙事段零影响。
- 若新增了专用持久化表，回滚需说明迁移回退（优先选 kv/settings 载体以避免该情况）。
