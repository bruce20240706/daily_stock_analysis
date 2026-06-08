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

## 2. 架构（分层：data_provider 纯抓取 / src 编排+持久化）

**分层纪律（评审结论 C2/H4）**：`data_provider/` 当前完全不 import `src.storage`/`src.repositories`/`DatabaseManager`（已验证）。本特性必须维持该隔离——**发现源 fetchers 留在 data_provider 且保持纯抓取（无 DB、无 config 读取）；Binance 差分、快照持久化、聚合去重、行情富化的编排放到 `src/` 服务层**，由 `market_analyzer` 调用。

```
crypto 复盘(MARKET_REVIEW_REGION=crypto)
  └─ src/services/crypto_new_listing_service.py  ← 编排层（读 env / 调度 / diff / 去重 / 富化）
       ├─ data_provider/crypto_new_listings.py（纯抓取，无 DB/无 config）
       │    ├─ fetch_okx_instruments()   → 原生 listTime（无状态）
       │    ├─ fetch_coinbase_products() → 原生 new_at（无状态）
       │    └─ fetch_binance_spot_symbols() → 当前 spot symbol 集（无状态，仅返回集合）
       ├─ Binance base-asset 差分 vs 上一份快照（src repo 读 DB）→ 写回新快照
       ├─ 按 base asset 去重（跨所 union）→ 窗口/max 截断 → 倒序
       └─ 行情富化（复用 DataFetcherManager.get_realtime_quote, presence-only）
  └─ market_analyzer.build_market_review_payload 追加 additive new_listings[]
  └─ 持久化(DB history 不变) + Web 上新行情表渲染
```

职责单元：(1) data_provider 三个**纯抓取函数**；(2) src 服务层编排（配置、Binance 差分、去重、窗口、富化）；(3) src 仓储层快照持久化（Binance 差分用）；(4) payload 集成（market_analyzer）；(5) Web 渲染。

## 3. 发现源（纯抓取，`data_provider/crypto_new_listings.py`）

- **不 import `src.config`、不 import `src.storage`/repositories、不写 DB**（与 `crypto_base.py` 隔离纪律一致：仅 import `.base`/`.realtime_types`）。窗口等参数由 src 服务层传入；这些函数只负责"抓 + 解析 + 归一化"。
- 复用 `CryptoExchangeBase._http_get` 风格的超时/重试（env `CRYPTO_FETCH_TIMEOUT_SECONDS`/`CRYPTO_FETCH_MAX_RETRIES`，`data_provider/crypto_base.py:80-96`）。
- 归一化记录（dataclass）：
  ```
  NewListing(base: str, quote: str, symbol: str, exchange: str,
             listed_at: Optional[int_ms], source: str)
  ```
- **OKX** `fetch_okx_instruments(window)`：解析 `data[].{instId,baseCcy,quoteCcy,state,listTime}`；过滤 `state == "live"` 且 `listTime` 落在 `(now-window, now]`（**排除未来 listTime**，preopen/preMkt 不计为"已上新"）。
- **Coinbase** `fetch_coinbase_products(window)`：UA 头；解析 `products[].{product_id,base_currency_id,quote_currency_id,new_at,status}`；`status` 正常、`new_at` 在窗口内；按 `base_currency_id` 去重。
- **Binance** `fetch_binance_spot_symbols()`：`data-api.binance.vision/api/v3/exchangeInfo` → 返回当前 `symbols[] where status=="TRADING" and isSpotTradingAllowed` 的 `(baseAsset,quoteAsset)` 集合（**纯返回集合，不做差分、不碰 DB**——差分在 src 服务层）。
- 每函数独立 try/except，失败记 `log` 并返回空（list / set），不抛断主流程。

## 4. 持久化（src 服务/仓储层，Binance 差分专用）

- **建表机制已核实**：系统用 `Base.metadata.create_all`（`src/storage.py:846`，**非 Alembic**）。**不存在**可复用的 kv/settings/state 表（仅 `DatabaseManager` 本体）。因此就该**新增一张最小专用表 + model + repo**（如 `crypto_symbol_snapshot`：exchange、symbols(JSON/text)、captured_at）；因走 `create_all`，**新表对存量 SQLite 自动创建、低风险**（无需手写迁移脚本；注意 create_all 不改既有表，但新表无此问题）。回滚=revert（删表无数据损失，纯缓存性质）。
- 服务层流程：读上一份 Binance base-asset 集 → 与本次 `fetch_binance_spot_symbols()` 做 **base-asset 差分**（见 §5 H2）→ 新增 base 入 new_listings → 写回新快照。
- **优雅降级**：无上一份快照（首跑 / 每日 GH Actions 临时环境无持久卷）→ 播种基线、本次不报 Binance 新上；OKX/Coinbase 无状态照常产出。
- **CI 现实（评审 C1）**：`.github/workflows/00-daily-analysis.yml` 不持久化 `./data/stock_analysis.db`（仅 pip cache + artifact），故**每日自动化复盘里 Binance 差分恒无输出**；Binance 仅在持久卷部署（本地 / Docker volume / 自托管）生效。据此 Binance **默认关闭**（§8），文档须写明（§9/M7）。

## 5. 聚合 + 行情富化（src 服务层）

- **Binance 差分按 base asset（评审 H2）**：以 baseAsset 集合比对（"上一份 base 集"vs"本次 base 集"），只报**全新 base**，避免把存量币新增计价对（如已上的 BTC 新加 `BTC/FDUSD`）误判为"新币"。边界：`BREAK→TRADING` 状态翻转 / re-listing 会被当作新 base——在 §9 与文档注明为已知边界。
- **去重**：按规范 base asset 跨所 union；保留 `exchanges`/`pairs` 列表。
  - **同名碰撞风险（评审 H3）**：不同交易所同 ticker 可能是不同项目，纯按 base 合并会把两币并成一条。**缓解**：仅当多源 base 相同**且** native `listed_at` 相近（同窗口内、差值阈值可配）时才合并；否则保留 per-exchange 行并各自标注。spec 写明此为已知局限（无跨所 canonical-id 的免费源）。
- **合并 `listed_at` 口径（评审 M1）**：一条 base 同时来自 OKX/Coinbase（真实原生时间戳）与 Binance 差分（无 listed_at）时，**优先真实原生时间戳**；仅当只有 Binance 来源时用本次 `captured_at` 作代理。
- **窗口/截断**：`CRYPTO_NEW_LISTING_WINDOW_DAYS`（默认 7）过滤。排序键 = `listed_at`（原生时间戳）；纯 Binance 差分项无 `listed_at`，用本次 run 的 `captured_at`（视为"本次发现"）作排序键，与原生时间戳一并倒序。`CRYPTO_NEW_LISTING_MAX`（默认 20）截断，`log` 丢弃数（不静默）。纯 Binance 差分项的窗口过滤以 `captured_at` 计（首次发现即视为窗口内）。
- **行情富化**：对每个 base 复用 `DataFetcherManager.get_realtime_quote`（`data_provider/base.py:1553`，crypto 路由 `:1638`），计价对优先 `USDT` 否则发现到的 quote；附 `price/change_pct/volume`，**presence-only**——缺失/illiquid 则 omit 字段，绝不补 0（沿用 `UnifiedRealtimeQuote.to_dict` None 过滤）。
  - **延迟预算（评审 M3）**：最多 `MAX`(默认 20) 个 base 各串行 `get_realtime_quote`（每个含三源 fallback），日级复盘可接受；`MAX` 即为天花板。新币常不在配置交易所→quote 失败→omit 价格字段（不阻断）。

## 6. payload / 兼容性

- `build_market_review_payload`（`src/market_analyzer.py:559-626`）追加 **additive** `new_listings: List[dict]`，仅 crypto region 且非空时携带；`version` 保持 1。
- 每条结构示意：`{base, exchanges:[...], pairs:[...], listed_at?, quote_pair?, price?, change_pct?, volume?}`（`?` 字段 presence-only）。
- 现有 `markdown_report` 与"新币与上新动态"叙事段保留。cn/us/hk payload 零影响。

## 7. Web 渲染（`apps/dsa-web` `MarketReviewReportView.tsx`）

- crypto 且 `new_listings` 非空 → 渲染"上新行情表"卡：列 = 币种 / 交易所 / 上市时间 / 价格·涨跌幅（有则显示，缺则 `-`）。
- **上市时间为空（评审 M6）**：纯 Binance 差分项 `listed_at=None` → 该列显示 `—`（不显示 captured_at 代理值，避免误导为真实上市时间）。
- 缺失或非 crypto → 整卡 omit（不渲染占位）。复用现有 indices 表样式与 region-aware 渲染（Tier-2 已加 `region` 透传）。

## 8. 配置（src 服务层读 env；同步 `.env.example`/docs/CHANGELOG）

| env | 默认 | 含义 |
|---|---|---|
| `CRYPTO_NEW_LISTING_ENABLED` | `true` | 关闭则完全不发现（additive、空则自动 omit，开着也低风险） |
| `CRYPTO_NEW_LISTING_WINDOW_DAYS` | `7` | "新"窗口天数 |
| `CRYPTO_NEW_LISTING_SOURCES` | `okx,coinbase` | 启用源与顺序。**默认不含 binance**（评审 C1：Binance 差分需持久卷，每日 CI 临时环境无输出）；持久部署可设 `okx,coinbase,binance` 显式启用 |
| `CRYPTO_NEW_LISTING_MAX` | `20` | 表最大行数 |

env 一律在 **src 服务层** 读取，**不在 data_provider 内**（隔离纪律 C2）。

## 9. 错误处理 / 稳定性护栏（AGENTS.md §7）

- 单源失败隔离；全部源失败 → `new_listings` omit，不报错、不拖垮 crypto 复盘。
- Binance 必须走 `data-api.binance.vision`（`api.binance.com` 451）；首跑无基线优雅播种；**默认关闭**（C1）。
- 无 API key；data_provider 不 import src.config / 不碰 DB（C2）；presence-only、无造数据；丢弃/降级均 `log`，不静默。
- 调度低频（日级），单次每源一调用，远低于各所限流（OKX 20req/2s、Coinbase ~10req/s）。
- **已知边界 / 文档须写明（M7）**：① Coinbase list 端点 CDN 缓存致 `new_at` 可能滞后数小时（日级可接受）；② Binance 差分仅持久卷部署生效，每日 CI 无输出；③ Binance `BREAK→TRADING`/re-listing 可能被当作新 base；④ 跨所同名 ticker 可能是不同项目（§5 H3 缓解后仍属已知局限）。

## 10. 测试

后端（`tests/`）：
- 各源**纯抓取函数**：mock HTTP 返回样例 JSON（OKX 含 `listTime`、Coinbase 含 `new_at`、Binance exchangeInfo symbol 列表），断言归一化记录正确、窗口 `(now-window, now]` 过滤生效（**含排除未来 listTime**）、非法/缺字段跳过不崩。
- Binance **base-asset 差分**（服务层）：有上一份快照→仅报全新 base；存量币新增计价对（BTC/FDUSD）→**不**误报；首跑无基线→播种、不报新上；快照写回。
- **持久化 repo/model（评审 M5）**：snapshot 表 model 经 `create_all` 创建；repo 读写 round-trip；无表/无行的首跑路径。
- **临时环境现实（M5）**：无持久快照 → Binance 段为空、其余源照常（模拟每日 CI）。
- 去重：同币多所/多对 → 合并为一条 base，`exchanges`/`pairs` 聚合；**同名碰撞**：base 同但 `listed_at` 相差超阈值 → 不合并（保留多行）。
- 合并 `listed_at`：OKX(真实)+Binance(None) → 取真实原生时间戳；仅 Binance → captured_at 代理。
- 窗口 + max 截断（含丢弃计数日志）。
- 行情富化 presence-only：缺价 → omit 字段不补 0。
- payload：crypto 非空 → 携带 `new_listings`；空或非 crypto → omit；version 不变。
- 单源失败/全失败：隔离、整段 omit、不抛。
- Web：`new_listings` 非空渲染表；`listed_at=None` 显示 `—`；空/非 crypto omit；cn/us/hk 不受影响。
- 离线 e2e：mock 源 + 行情，跑通 crypto 复盘，断言 payload 含 `new_listings` 且 presence-only。

验证矩阵：`./scripts/ci_gate.sh` + `pytest -m "not network"`；Web 在无空格副本跑 vitest/lint 或交 CI `web-gate`。在线源验证（OKX/Coinbase/Binance.vision 真实可达性）走 `network-smoke`，离线测试一律 mock。

## 11. 回滚

- 特性 opt-in：`CRYPTO_NEW_LISTING_ENABLED=false` 可关；Binance 默认即关（默认源仅 okx,coinbase）；且仅 crypto 复盘触发。
- 回滚 = revert 本特性 commit；默认配置、cn/hk/us、现有 crypto 复盘与叙事段零影响。
- 新增的快照表（`create_all` 自动建，纯缓存性质、无业务数据）revert 后遗留空表无害；如需彻底清理可手动 `DROP TABLE`。无数据迁移、无回填。
