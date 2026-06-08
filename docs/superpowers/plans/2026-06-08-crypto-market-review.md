# crypto 大盘复盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `MARKET_REVIEW_REGION` 支持 `crypto`，复用现有 region 大盘复盘管线产出"主流币篮子行情 + 市场叙事 + 新币/上新资讯"复盘，crypto 全程 opt-in。

**Architecture:** crypto 作为第 4 个 region 接入现有 `MarketAnalyzer` 管线（overview → news → LLM → `market_review_payload` → 持久化 → Web）。无新结构化数据源：篮子用 crypto 现货实时行情（复用 Binance→OKX→Coinbase fallback），breadth/sectors/market_light 对 crypto 一律 omit，新币资讯走 search_service + LLM 叙事。**纪律：全量接线，不留 `crypto`→`cn` 半门控。**

**Tech Stack:** Python 3（pytest），FastAPI，React + TypeScript（vitest），现有 `data_provider`/`src/core`/`src/market_analyzer.py`。

**对应设计：** `docs/superpowers/specs/2026-06-08-crypto-market-review-design.md`

**全局验证（每个后端 commit 前）：** `PYTHONPATH="$PWD" .venv/bin/python -m pytest <该任务测试文件> -q`；任务收尾跑 `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`。注意：本仓库 venv 只有 `.venv/bin/python`（无 `python`），且工作区路径含空格，前端完整 build 走无空格副本或交 CI。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/config.py` | crypto 篮子配置 + region 校验放行 crypto | Modify |
| `src/core/market_review.py` | region tuple/标题/`both` 解耦 | Modify |
| `src/core/market_strategy.py` | `CRYPTO_BLUEPRINT` + 路由 | Modify |
| `src/market_analyzer.py` | __init__ 放行 + region 文案分支 + 跳过 snapshot + 新币 prompt | Modify |
| `src/core/trading_calendar.py` | `compute_effective_region` 含 crypto | Modify |
| `data_provider/base.py` | `get_main_indices` crypto 篮子分支 | Modify |
| `apps/dsa-web/.../MarketReviewReportView.tsx` | crypto 隐藏 breadth 卡 + 计价币单位 | Modify |
| `.env.example` / `docs/crypto-guide.md` / `docs/CHANGELOG.md` | 配置与文档 | Modify |
| `tests/test_crypto_market_review*.py` | 单测 + 端到端 | Create |

---

## Task 1: 配置层 — 放行 crypto region + 篮子符号配置

**Files:**
- Modify: `src/config.py:2272-2282`（`_parse_market_review_region`），`:1757-1758` 附近（`from_env`），`:926` 附近（字段定义）
- Test: `tests/test_crypto_market_review_config.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_market_review_config.py
import importlib

from src.config import Config


def test_market_review_region_accepts_crypto():
    assert Config._parse_market_review_region("crypto") == "crypto"
    assert Config._parse_market_review_region("CRYPTO") == "crypto"


def test_market_review_region_invalid_still_falls_back_cn():
    assert Config._parse_market_review_region("foobar") == "cn"


def test_crypto_basket_symbols_default_and_env_override(monkeypatch):
    monkeypatch.delenv("CRYPTO_MARKET_REVIEW_SYMBOLS", raising=False)
    cfg = Config.from_env()
    assert "BTC/USDT" in cfg.crypto_market_review_symbols
    assert "ETH/USDT" in cfg.crypto_market_review_symbols

    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "HYPE/USDT,SUI/USDT")
    cfg2 = Config.from_env()
    assert cfg2.crypto_market_review_symbols == "HYPE/USDT,SUI/USDT"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_review_config.py -q`
Expected: FAIL（`crypto` 被回退为 `cn`；`Config` 无 `crypto_market_review_symbols` 属性）

- [ ] **Step 3: 放行 crypto region**

`src/config.py` 的 `_parse_market_review_region`（2277、2280 行）：

```python
        if v in ('cn', 'us', 'hk', 'crypto', 'both'):
            return v
        logging.getLogger(__name__).warning(
            f"MARKET_REVIEW_REGION 配置值 '{value}' 无效，已回退为默认值 'cn'（合法值：cn / hk / us / crypto / both）"
        )
```

- [ ] **Step 4: 新增篮子配置字段 + 默认常量 + env 读取**

在 `src/config.py` 现有 crypto 默认值附近（如 `crypto_data_priority` 字段 `:926` 旁）加字段：

```python
    crypto_market_review_symbols: str = "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,TRX/USDT,TON/USDT,DOT/USDT"
```

在 `from_env` 的 crypto 段（`:1757-1758` 旁）加 env 读取：

```python
            crypto_market_review_symbols=os.getenv(
                'CRYPTO_MARKET_REVIEW_SYMBOLS',
                'BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,TRX/USDT,TON/USDT,DOT/USDT',
            ),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_review_config.py -q`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/test_crypto_market_review_config.py
git commit -m "feat: crypto 大盘复盘配置 — region 放行 crypto + 篮子符号 env"
```

---

## Task 2: market_review.py — crypto region 行 + 标题 + `both` 解耦

**Files:**
- Modify: `src/core/market_review.py:31-37`（tuple/order/valid），`:48-66`（标题），`:69-84`（resolve）
- Test: `tests/test_crypto_market_review_resolve.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_market_review_resolve.py
from src.core.market_review import (
    _resolve_market_review_regions,
    _VALID_MARKET_REVIEW_REGIONS,
    _get_market_review_text,
)


def test_crypto_is_valid_region():
    assert "crypto" in _VALID_MARKET_REVIEW_REGIONS


def test_resolve_crypto_single():
    assert _resolve_market_review_regions("crypto") == ["crypto"]


def test_resolve_both_excludes_crypto():
    # D3: both 必须保持 cn+hk+us，绝不包含 crypto
    assert _resolve_market_review_regions("both") == ["cn", "hk", "us"]


def test_resolve_comma_list_with_crypto_keeps_order():
    assert _resolve_market_review_regions("cn,crypto") == ["cn", "crypto"]


def test_crypto_title_present_zh_and_en():
    assert _get_market_review_text("zh")["crypto_title"].strip() != ""
    assert _get_market_review_text("en")["crypto_title"].strip() != ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_review_resolve.py -q`
Expected: FAIL（crypto 不在 valid set；`both` 不含 crypto 这条会"意外"通过，但 crypto_title KeyError）

- [ ] **Step 3: tuple 加 crypto 行 + 显式解耦 `both`**

`src/core/market_review.py:31-37` 改为：

```python
_MARKET_REVIEW_MARKETS = (
    ('cn', 'cn_title', 'A 股'),
    ('hk', 'hk_title', '港股'),
    ('us', 'us_title', '美股'),
    ('crypto', 'crypto_title', '加密货币'),
)
_MARKET_REVIEW_REGION_ORDER = tuple(market for market, _, _ in _MARKET_REVIEW_MARKETS)
_VALID_MARKET_REVIEW_REGIONS = frozenset(_MARKET_REVIEW_REGION_ORDER)
# MARKET_REVIEW_REGION=both 的固定语义：仅传统三市，crypto 须显式 opt-in（不随 tuple 扩张）
_BOTH_REVIEW_REGIONS = ('cn', 'hk', 'us')
```

`_resolve_market_review_regions`（69-84）的 `both` 分支改为：

```python
    if region == 'both':
        return list(_BOTH_REVIEW_REGIONS)
```

（comma-list 分支不变：`[market for market in _MARKET_REVIEW_REGION_ORDER if market in requested]` 会自然产出 `["cn","crypto"]`。）

- [ ] **Step 4: 标题字典加 crypto_title**

`_get_market_review_text`（48-66）en 分支加 `"crypto_title": "# Crypto Market Recap",`，zh 分支加 `"crypto_title": "# 加密货币大盘复盘",`。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_review_resolve.py -q`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add src/core/market_review.py tests/test_crypto_market_review_resolve.py
git commit -m "feat: crypto 大盘复盘 region 接入 — tuple/标题 + both 解耦保持 cn+hk+us"
```

---

## Task 3: market_analyzer.py — __init__ 放行 + region 文案分支

**Files:**
- Modify: `src/market_analyzer.py:138`（whitelist），`:152-160`（scope），`:162-178`（turnover），`:188-202`（title/hint），`:1340`（template market_labels）
- Test: `tests/test_crypto_market_analyzer_text.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_market_analyzer_text.py
from src.market_analyzer import MarketAnalyzer


def _crypto_analyzer():
    return MarketAnalyzer(region="crypto")


def test_crypto_region_not_downgraded_to_cn():
    a = _crypto_analyzer()
    assert a.region == "crypto"
    assert a.profile.region == "crypto"


def test_crypto_scope_name_zh():
    a = _crypto_analyzer()
    assert a._get_market_scope_name("zh") == "加密货币市场"


def test_crypto_turnover_unit_is_quote_currency():
    a = _crypto_analyzer()
    label = a._get_turnover_unit_label()
    assert "亿" not in label and "CNY" not in label


def test_crypto_review_title_zh():
    a = _crypto_analyzer()
    assert "加密货币" in a._get_review_title("2026-06-08")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_analyzer_text.py -q`
Expected: FAIL（region 被降级为 cn）

- [ ] **Step 3: __init__ 放行 crypto**

`src/market_analyzer.py:138`：

```python
        self.region = region if region in ("cn", "us", "hk", "crypto") else "cn"
```

- [ ] **Step 4: scope name 加 crypto 分支**

`_get_market_scope_name`（152-160）在 `if self.region == "hk":` 之后加：

```python
        if self.region == "crypto":
            return "crypto market" if review_language == "en" else "加密货币市场"
```

- [ ] **Step 5: turnover 单位/格式加 crypto 分支**

`_get_turnover_unit_label`（162-168）在 hk 分支后加：

```python
        if self.region == "crypto":
            return "quote ccy" if self._get_review_language() == "en" else "计价币"
```

`_format_turnover_value`（170-178）把 `if self.region in ("us", "hk"):` 改为包含 crypto：

```python
        if self.region in ("us", "hk", "crypto"):
            return f"{amount_raw / 1e9:.2f}"
```

- [ ] **Step 6: review title / index hint / template label 加 crypto**

`_get_review_title`（188-193）en 的 `market_names` 加 `"crypto": "Crypto Market Recap"`；zh 分支返回保持 `## {date} 大盘复盘`（通用），但为体现币种语境改为：

```python
    def _get_review_title(self, date: str) -> str:
        if self._get_review_language() == "en":
            market_names = {"us": "US Market Recap", "hk": "HK Market Recap", "crypto": "Crypto Market Recap"}
            market_name = market_names.get(self.region, "A-share Market Recap")
            return f"## {date} {market_name}"
        if self.region == "crypto":
            return f"## {date} 加密货币大盘复盘"
        return f"## {date} 大盘复盘"
```

`_get_index_hint`（195-202）en 分支加 crypto（zh 走 `self.profile.prompt_index_hint`，CRYPTO_PROFILE 已有合适提示）：

```python
            if self.region == "crypto":
                return "Assess overall crypto risk appetite from BTC/ETH and other majors in the basket."
```

模板报告 `market_labels`（1340）加 crypto：

```python
        market_labels = {"cn": "A股", "us": "美股", "hk": "港股", "crypto": "加密货币"}
```

- [ ] **Step 7: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_analyzer_text.py -q`
Expected: PASS（4 passed）

- [ ] **Step 8: Commit**

```bash
git add src/market_analyzer.py tests/test_crypto_market_analyzer_text.py
git commit -m "feat: crypto 大盘复盘文案 — region 放行 + 计价币单位/标题/范围分支"
```

---

## Task 4: market_strategy.py — CRYPTO_BLUEPRINT + 路由

**Files:**
- Modify: `src/core/market_strategy.py:132-172`（HK 之后新增 CRYPTO_BLUEPRINT + 路由）
- Test: `tests/test_crypto_market_strategy.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_market_strategy.py
from src.core.market_strategy import get_market_strategy_blueprint


def test_crypto_blueprint_routed():
    bp = get_market_strategy_blueprint("crypto")
    assert bp.region == "crypto"


def test_crypto_blueprint_not_cn_content():
    bp = get_market_strategy_blueprint("crypto")
    joined = bp.title + " " + " ".join(bp.principles)
    assert "涨停" not in joined
    assert "国企指数" not in joined
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_strategy.py -q`
Expected: FAIL（crypto 回退 CN_BLUEPRINT，region=="cn"，含"涨停"）

- [ ] **Step 3: 新增 CRYPTO_BLUEPRINT（HK_BLUEPRINT 之后，167 行 `get_market_strategy_blueprint` 之前）**

```python
CRYPTO_BLUEPRINT = MarketStrategyBlueprint(
    region="crypto",
    title="加密货币市场三段式复盘策略",
    positioning="以 BTC/ETH 等主流币走势衡量加密市场整体风险偏好，7×24 连续交易、无涨跌停、价格以交易对计价币（如 USDT）计。",
    principles=[
        "先看 BTC/ETH 主导方向，再看主流币篮子分化，最后看资金与情绪轮动。",
        "结论必须映射到仓位、节奏与风险控制动作；高波动市场尤重风控。",
        "判断使用当日行情与近期新闻，不臆测未验证信息；价格用计价币表述，不写'元'。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断加密市场处于上升、震荡还是防守阶段。",
            checkpoints=["BTC/ETH 是否同向", "主流币篮子是否普涨/普跌", "关键价位是否被突破（计价币）"],
        ),
        StrategyDimension(
            name="资金情绪",
            objective="识别短线风险偏好与情绪温度。",
            checkpoints=["主流币与山寨币的强弱分化", "成交活跃度变化", "市场叙事是否过热或恐慌"],
        ),
        StrategyDimension(
            name="主线与上新",
            objective="提炼可交易主线与新上项目机会及风险。",
            checkpoints=["近期新上线/IEO/launchpad 项目动态", "板块叙事（L1/L2/AI/MEME 等）轮动", "新币上线初期高波动与流动性风险"],
        ),
    ],
    action_framework=[
        "进攻：BTC/ETH 共振上行 + 篮子普涨 + 主线/新叙事强化。",
        "均衡：主流币分化或缩量震荡，控制仓位并等待确认。",
        "防守：BTC/ETH 转弱 + 普跌扩散，优先风控与降杠杆。",
    ],
)
```

`get_market_strategy_blueprint`（166-172）在 hk 之后加：

```python
    if region == "crypto":
        return CRYPTO_BLUEPRINT
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_strategy.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/core/market_strategy.py tests/test_crypto_market_strategy.py
git commit -m "feat: crypto 大盘复盘策略蓝图 CRYPTO_BLUEPRINT（替代误用的 CN 蓝图）"
```

---

## Task 5: trading_calendar.py — compute_effective_region 含 crypto

**Files:**
- Modify: `src/core/trading_calendar.py:559-569`
- Test: `tests/test_crypto_effective_region.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_effective_region.py
from src.core.trading_calendar import compute_effective_region


def test_crypto_always_eligible_when_selected():
    # crypto 24/7：只要被选中且在 open set 中即返回 crypto
    assert compute_effective_region("crypto", {"crypto"}) == "crypto"
    assert compute_effective_region("crypto", {"cn", "crypto"}) == "crypto"


def test_crypto_not_open_returns_empty():
    assert compute_effective_region("crypto", {"cn"}) == ""


def test_both_still_excludes_crypto():
    assert compute_effective_region("both", {"cn", "hk", "us", "crypto"}) == "cn,hk,us"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_effective_region.py -q`
Expected: FAIL（crypto 不在白名单 → 被改写为 cn）

- [ ] **Step 3: compute_effective_region 放行 crypto（559-562）**

```python
    if config_region not in ("cn", "hk", "us", "crypto", "both"):
        config_region = "cn"
    if config_region in ("cn", "hk", "us", "crypto"):
        return config_region if config_region in open_markets else ""
```

（`both` 分支 564 行保持 `("cn","hk","us")` 不变 → D3 满足。）

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_effective_region.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/core/trading_calendar.py tests/test_crypto_effective_region.py
git commit -m "feat: crypto 大盘复盘调度 — compute_effective_region 放行 24/7 crypto"
```

---

## Task 6: market_analyzer.py — crypto 跳过 MarketLightSnapshot

**Files:**
- Modify: `src/market_analyzer.py:521-583`（`build_market_review_payload`），`:1385`（`_run_daily_review_parts`）
- Test: `tests/test_crypto_payload_omits.py`（Create）

> 说明：review 路径直接调 `analyzer.build_market_light_snapshot`，不经 `market_light_service`，且 `MarketRegion=Literal["cn","hk","us"]` 会让 `MarketLightSnapshot(region="crypto")` 校验失败。因此必须在分析器层对 crypto 跳过 snapshot，**不改** `market_light_service`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_payload_omits.py
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_crypto_payload_omits_market_light_and_breadth():
    a = MarketAnalyzer(region="crypto")
    overview = MarketOverview(date="2026-06-08")
    payload = a.build_market_review_payload(overview, news=[], report="# 加密货币大盘复盘\n\n## 一、概览\n内容")
    assert payload["region"] == "crypto"
    assert "market_light" not in payload   # crypto 不出市场灯
    assert "breadth" not in payload        # has_market_stats=False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_payload_omits.py -q`
Expected: FAIL（`build_market_light_snapshot` 对 crypto 构造 MarketLightSnapshot → pydantic ValidationError）

- [ ] **Step 3: payload 对 crypto 跳过 market_light**

`build_market_review_payload`（532）：

```python
        if self.region == "crypto":
            light = None
        else:
            light = market_light_snapshot or self.build_market_light_snapshot(overview)
```

字典字面量（561）移除 `"market_light": light,` 这一行，并在 `return payload` 之前、`if has_breadth_data:` 之后追加：

```python
        if light is not None:
            payload["market_light"] = light
```

- [ ] **Step 4: 主流程对 crypto 不构造 snapshot**

`_run_daily_review_parts`（1385）：

```python
        snapshot = None if self.region == "crypto" else self.build_market_light_snapshot(overview)
```

（`MarketLightReviewResult.market_light_snapshot` 接受 None；下游消费方已按 presence 处理。）

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_payload_omits.py -q`
Expected: PASS（1 passed）

- [ ] **Step 6: 回归既有 payload 测试（确保 cn/us/hk 不受影响）**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_market_analyzer_generate_text.py -q`
Expected: PASS（既有用例全绿）

- [ ] **Step 7: Commit**

```bash
git add src/market_analyzer.py tests/test_crypto_payload_omits.py
git commit -m "feat: crypto 大盘复盘 — 跳过 MarketLightSnapshot，payload omit market_light"
```

---

## Task 7: data_provider/base.py — get_main_indices crypto 篮子分支

**Files:**
- Modify: `data_provider/base.py:2163-2185`（`get_main_indices` 加 crypto 分发 + 新增 `_get_crypto_basket_indices`）
- Test: `tests/test_crypto_basket_indices.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_basket_indices.py
from data_provider.base import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


def _fake_quote(code):
    return UnifiedRealtimeQuote(
        code=code, name=code, source=RealtimeSource.FALLBACK,
        price=100.0, change_pct=2.5, volume=10.0, amount=1000.0, high=105.0, low=95.0,
    )


def test_crypto_basket_uses_config_symbols(monkeypatch):
    # 实现就地读 os.getenv（与 data_provider 既有风格一致），env 立即生效
    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "BTC/USDT,ETH/USDT")
    mgr = DataFetcherManager()
    monkeypatch.setattr(mgr, "get_realtime_quote", lambda code: _fake_quote(code))

    rows = mgr.get_main_indices(region="crypto")
    assert len(rows) == 2
    codes = {r["code"] for r in rows}
    assert codes == {"BTC/USDT", "ETH/USDT"}
    row = rows[0]
    # MarketIndex 构造所需键齐全
    for key in ("code", "name", "current", "change", "change_pct", "open",
                "high", "low", "prev_close", "volume", "amount", "amplitude"):
        assert key in row
    assert row["current"] == 100.0
    assert row["change_pct"] == 2.5


def test_crypto_basket_skips_invalid_and_failed(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "BTC/USDT,NOTACOIN,ETH/USDT")
    mgr = DataFetcherManager()

    def quote(code):
        return None if code == "ETH/USDT" else _fake_quote(code)

    monkeypatch.setattr(mgr, "get_realtime_quote", quote)
    rows = mgr.get_main_indices(region="crypto")
    # NOTACOIN 非法跳过；ETH 取价失败跳过；仅剩 BTC
    assert [r["code"] for r in rows] == ["BTC/USDT"]
```

> 注意：若 `Config` 为单例缓存，`from_env` 不随 env 变化。`_get_crypto_basket_indices` 改为**直接读 `os.getenv`**（默认值与 config 字段一致），即可让上面 env 测试确定生效，并与 data_provider 既有"就地 getenv"风格一致。下方实现采用该方式。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_basket_indices.py -q`
Expected: FAIL（`get_main_indices(region="crypto")` 返回 `[]`）

- [ ] **Step 3: get_main_indices 加 crypto 分发**

`data_provider/base.py` `get_main_indices`（2163）方法体最前面加：

```python
        if region == "crypto":
            return self._get_crypto_basket_indices()
```

- [ ] **Step 4: 新增 _get_crypto_basket_indices**

在 `get_main_indices` 之后新增方法（复用已有 `is_crypto_code` 与 `self.get_realtime_quote` 的 crypto fallback）：

```python
    def _get_crypto_basket_indices(self) -> List[Dict[str, Any]]:
        """crypto 大盘复盘"篮子"：对配置的主流币逐个取实时行情，映射为指数行情结构。

        复用 get_realtime_quote 的 Binance→OKX→Coinbase fallback。非法代码与取价失败均跳过，
        不静默塞 0，避免误导。
        """
        import os
        # is_crypto_code 是本模块（base.py）顶部定义的函数，直接调用即可（勿再 import）

        default_symbols = (
            "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,"
            "ADA/USDT,AVAX/USDT,LINK/USDT,TRX/USDT,TON/USDT,DOT/USDT"
        )
        raw = os.getenv("CRYPTO_MARKET_REVIEW_SYMBOLS", default_symbols)
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

        rows: List[Dict[str, Any]] = []
        for code in symbols:
            if not is_crypto_code(code):
                logger.warning("[crypto篮子] 跳过非法代码: %s", code)
                continue
            try:
                quote = self.get_realtime_quote(code)
            except Exception as e:  # 单币失败不影响其余
                logger.warning("[crypto篮子] %s 取价异常: %s", code, e)
                quote = None
            if quote is None or quote.price is None:
                logger.info("[crypto篮子] %s 无可用行情，跳过", code)
                continue
            price = float(quote.price)
            pct = float(quote.change_pct) if quote.change_pct is not None else 0.0
            prev_close = price / (1 + pct / 100) if pct != -100 else 0.0
            change = price - prev_close
            high = float(quote.high) if quote.high is not None else 0.0
            low = float(quote.low) if quote.low is not None else 0.0
            amplitude = ((high - low) / prev_close * 100) if (prev_close and high and low) else 0.0
            rows.append({
                "code": code,
                "name": code,
                "current": price,
                "change": change,
                "change_pct": pct,
                "open": 0.0,
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "volume": float(quote.volume) if quote.volume is not None else 0.0,
                "amount": float(quote.amount) if quote.amount is not None else 0.0,
                "amplitude": amplitude,
            })
        if not rows:
            logger.warning("[crypto篮子] 未取到任何篮子行情，将依赖新闻进行定性分析")
        return rows
```

> `is_crypto_code` 已在 `base.py:45` 定义（同模块），直接调用，无需 import，避免循环引用。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_basket_indices.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add data_provider/base.py tests/test_crypto_basket_indices.py
git commit -m "feat: crypto 大盘复盘 — get_main_indices 主流币篮子分支（复用实时行情 fallback）"
```

---

## Task 8: 新币与上新动态 — crypto 新闻查询 + prompt 叙事段

**Files:**
- Modify: `src/market_analyzer.py:446-489`（`search_market_news` 追加 crypto 新币查询），`:1052-1181`（`_build_review_prompt` 插入 crypto 叙事段指令）
- Test: `tests/test_crypto_new_coin_section.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_new_coin_section.py
from src.market_analyzer import MarketAnalyzer, MarketOverview


def test_crypto_addendum_present_for_crypto():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_addendum_prompt("zh")
    assert "新币" in block and "上新" in block


def test_crypto_addendum_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_addendum_prompt("zh") == ""


def test_crypto_extra_news_queries_nonempty():
    a = MarketAnalyzer(region="crypto")
    qs = a._get_crypto_new_coin_queries()
    assert any("IEO" in q or "上新" in q or "new" in q.lower() for q in qs)


def test_non_crypto_extra_news_queries_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_new_coin_queries() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_coin_section.py -q`
Expected: FAIL（方法不存在）

- [ ] **Step 3: 新增两个 helper（放在 `search_market_news` 之前）**

```python
    def _get_crypto_new_coin_queries(self) -> List[str]:
        """crypto 专用新币/上新检索词；非 crypto 返回空。"""
        if self.region != "crypto":
            return []
        return [
            "加密货币 新币 上线",
            "IEO launchpad new listing",
            "Binance OKX 上新 公告",
            "crypto new token launch",
        ]

    def _get_crypto_addendum_prompt(self, review_language: str | None = None) -> str:
        """crypto 专用 prompt 附加段：要求 LLM 基于新闻产出"新币与上新动态"小节；非 crypto 返回空。"""
        if self.region != "crypto":
            return ""
        if (review_language or self._get_review_language()) == "en":
            return (
                "\n[Crypto-specific] Add a section '## New Listings & IEO' summarizing notable "
                "recent token launches / IEO / launchpad / exchange listings strictly from the "
                "provided news. If none, say so briefly. Do not invent listings or prices."
            )
        return (
            "\n[加密货币专属] 增加一节'## 新币与上新动态'，严格依据所给新闻总结近期值得关注的"
            "新上线/IEO/launchpad/交易所上新项目与风险；若无显著资讯则简要说明。不得编造上新或价格。"
        )
```

- [ ] **Step 4: search_market_news 追加 crypto 新币查询**

`search_market_news`（约 460 行 `search_queries = self.profile.news_queries`）改为：

```python
        search_queries = list(self.profile.news_queries) + self._get_crypto_new_coin_queries()
```

`market_names` dict 加 crypto 上下文：

```python
            "crypto": "加密货币市场" if review_language == "zh" else "crypto market",
```

- [ ] **Step 5: prompt 模板插入叙事段指令（zh 与 en 各一处）**

在 `_build_review_prompt` 的 en 模板中 `{self._get_strategy_prompt_block()}`（1161）之后插入：

```python
{self._get_crypto_addendum_prompt(review_language)}
```

在 zh 模板对应的 `{self._get_strategy_prompt_block()}` 之后同样插入 `{self._get_crypto_addendum_prompt(review_language)}`（zh 模板位于 en 分支之外的 `return f"""..."""`，定位其策略块插值处）。

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_new_coin_section.py -q`
Expected: PASS（4 passed）

- [ ] **Step 7: Commit**

```bash
git add src/market_analyzer.py tests/test_crypto_new_coin_section.py
git commit -m "feat: crypto 大盘复盘 — 新币与上新动态叙事段（新闻查询 + prompt 指令）"
```

---

## Task 9: Web — MarketReviewReportView crypto-aware

**Files:**
- Modify: `apps/dsa-web/src/components/.../MarketReviewReportView.tsx`（breadth 卡守卫 :461、:80-98 icon、:480 单位）
- Test: 对应 `MarketReviewReportView.test.tsx`（按既有测试文件追加用例）

> 目标：crypto payload 无 `breadth` 时，**不渲染**"暂无数据"占位（对 crypto 语义错误），而是整段省略；篮子行情表照常渲染。

- [ ] **Step 1: 定位 region 来源与测试文件**

Run:
```bash
ls apps/dsa-web/src/components/**/MarketReviewReportView*.{tsx,test.tsx} 2>/dev/null
grep -n "region\|marketData.id\|marketData.breadth" apps/dsa-web/src/components/**/MarketReviewReportView.tsx | head
```
Expected: 找到组件与测试文件，确认 `marketData` 是否带 `region` 字段（payload 含 `region`）。

- [ ] **Step 2: 写失败测试（vitest，断言 crypto 不出"暂无数据"占位）**

在既有 `MarketReviewReportView.test.tsx` 追加：

```tsx
it('crypto 市场不渲染 breadth 暂无数据占位', () => {
  const payload = {
    version: 1, kind: 'market_review', region: 'crypto', language: 'zh',
    title: '加密货币大盘复盘', date: '2026-06-08',
    indices: [{ code: 'BTC/USDT', name: 'BTC/USDT', current: 64210, changePct: 2.1, high: 65000, low: 63000 }],
    sections: [{ key: 'full_review', title: 'Review', markdown: '## 一、概览\n内容' }],
    markdown_report: '# 加密货币大盘复盘',
  };
  render(<MarketReviewReportView payload={payload as any} />);
  expect(screen.queryByText(/暂无.*宽度|No breadth/i)).toBeNull();
  expect(screen.getByText('BTC/USDT')).toBeInTheDocument();
});
```
（具体 props 名以组件实际签名为准；若组件按 `structuredMarketData` 聚合，构造对应结构。）

- [ ] **Step 3: 跑测试确认失败**

Run（无空格路径或 CI）：`cd /tmp/dsa-web-build && npx vitest run src/components/**/MarketReviewReportView.test.tsx`
Expected: FAIL（crypto 命中 `: ( <p>{noBreadthData}</p> )` 占位分支）

- [ ] **Step 4: 实现 — crypto 省略 breadth 占位 + 计价币单位**

`MarketReviewReportView.tsx:461` 的三元：当 `marketData.breadth` 为假且该市场为 crypto 时，渲染 `null` 而非占位：

```tsx
                {marketData.breadth ? (
                  /* 既有 breadth 网格不变 */
                ) : (marketData.region === 'crypto' ? null : (
                  <p className="text-sm text-secondary-text">{marketReviewText.noBreadthData}</p>
                ))}
```

（若 `marketData` 无 `region` 字段，则在聚合 `structuredMarketData` 处把 payload.region 透传到每个条目；在 Step 1 已确认来源。）turnover 单位（:480）已用 `marketData.breadth.turnoverUnit`，crypto 无 breadth 卡，无需额外改；保留即可。

- [ ] **Step 5: 跑测试确认通过 + lint**

Run：`cd /tmp/dsa-web-build && npx vitest run src/components/**/MarketReviewReportView.test.tsx && npm run lint`
Expected: PASS + lint 0 error

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/src/components
git commit -m "feat: crypto 大盘复盘 Web — 省略 breadth 占位，保留篮子行情表"
```

---

## Task 10: 配置文档 + CHANGELOG

**Files:**
- Modify: `.env.example`（crypto 段），`docs/crypto-guide.md`，`docs/CHANGELOG.md`
- 无测试（docs/chore）

- [ ] **Step 1: .env.example 增 crypto 复盘项**

在 crypto 段（CRYPTO_FETCH_* 之后）追加：

```
# MARKET_REVIEW_REGION=crypto                      # 大盘复盘市场：cn/hk/us/crypto/both（both=cn+hk+us，crypto 须显式选）
# CRYPTO_MARKET_REVIEW_SYMBOLS=BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,TRX/USDT,TON/USDT,DOT/USDT  # crypto 大盘复盘"篮子"成分，可增删
```

- [ ] **Step 2: docs/crypto-guide.md 增"大盘复盘"小节**

补充：如何启用 crypto 大盘复盘（`MARKET_REVIEW_REGION=crypto`）、篮子配置、复盘含主流币篮子+市场叙事+新币资讯、不含 breadth/板块/市场灯的原因（无对应数据源）、`both` 不含 crypto、新币结构化发现为后续子项目。

- [ ] **Step 3: CHANGELOG [Unreleased] 追加（扁平格式）**

```
- [新功能] 大盘复盘支持数字货币（crypto）：`MARKET_REVIEW_REGION=crypto` 产出主流币篮子行情 + 市场叙事 + 新币/上新资讯；篮子经 `CRYPTO_MARKET_REVIEW_SYMBOLS` 可配置；crypto 须显式 opt-in（`both` 仍为 cn+hk+us），无 breadth/板块/市场灯（无对应数据源）。
```

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: crypto 大盘复盘配置与指南（MARKET_REVIEW_REGION=crypto + 篮子）"
```

---

## Task 11: 端到端集成测试 + 全量回归

**Files:**
- Test: `tests/test_crypto_market_review_e2e.py`（Create）

- [ ] **Step 1: 写端到端测试（离线，mock 篮子与无 LLM 走模板）**

```python
# tests/test_crypto_market_review_e2e.py
from src.market_analyzer import MarketAnalyzer, MarketIndex


def test_crypto_review_end_to_end_offline(monkeypatch):
    a = MarketAnalyzer(region="crypto")  # analyzer=None → 模板报告；search_service=None → 无新闻

    basket = [
        MarketIndex(code="BTC/USDT", name="BTC/USDT", current=64210.0, change_pct=2.1, high=65000.0, low=63000.0),
        MarketIndex(code="ETH/USDT", name="ETH/USDT", current=3180.0, change_pct=1.4, high=3250.0, low=3100.0),
    ]
    monkeypatch.setattr(a, "_get_main_indices", lambda: basket)

    result = a.run_daily_review_with_snapshot()
    payload = result.structured_payload

    assert payload["region"] == "crypto"
    assert result.market_light_snapshot is None
    assert "market_light" not in payload
    assert "breadth" not in payload
    assert any(idx["code"] == "BTC/USDT" for idx in payload["indices"])
    # 报告不含 A 股专属字样（防回退）
    assert "A股大盘复盘" not in payload["markdown_report"]
```

- [ ] **Step 2: 跑测试确认失败/通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_review_e2e.py -q`
Expected: 在 Task 1-8 完成后应 PASS；若 FAIL 按报错定位未接线点。

- [ ] **Step 3: 全量离线回归**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest -m "not network" -q`
Expected: 全绿，无既有用例回归。

- [ ] **Step 4: Commit**

```bash
git add tests/test_crypto_market_review_e2e.py
git commit -m "test: crypto 大盘复盘端到端（payload omit breadth/market_light + 不回退 cn）"
```

---

## 实施后交付说明（执行者填写）

- 改了什么 / 为什么 / 验证情况（贴 `pytest -m "not network"` 末行）/ 未验证项（前端完整 build 是否在无空格路径或 CI）/ 风险点（crypto 取价依赖外网，离线测试已 mock）/ 回滚（`git revert` 各 feat commit；`both` 未变，存量 cn/hk/us 零影响）。
