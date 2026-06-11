# crypto 永续多空比指标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 crypto 永续新增两个 presence-only 多空比指标（全市场账户 `long_short_ratio` + 大户 `long_short_ratio_top`），端到端透出到单股/复盘 prompt、结构化 API payload、Web 卡片。

**Architecture:** 沿现有 perp 指标三层链路追加字段：抓取层 `data_provider/crypto_derivatives.py`（OKX rubik 两端点，新 `_okx_ratio` 助手）→ 编排/聚合（`fetch_perp_metrics` 3→5 路、`fetch_perp_market_snapshot` OI 加权）→ 透出（analyzer/market_analyzer prompt + 结构化 payload 透传 + Web `ReportCryptoMetrics`/`MarketReviewReportView`）。纯增量、presence-only、fail-soft；零 DB schema、零回测/做空语义改动；无新配置（复用 `crypto_derivatives_enabled`）。

**Tech Stack:** Python 3（pytest、ThreadPoolExecutor、requests）；React + TypeScript（vitest、eslint、vite build）。

**对应 spec：** `docs/superpowers/specs/2026-06-11-crypto-perp-long-short-ratio-design.md`（已按深审收敛）。注意：spec §5 "零改动面：API/Schema、Web/Desktop" 因结构化 payload 为整 dict 透传而**不准确**——本计划已纳入 API 字段增量（透传，无 schema 改动）与 Web 渲染，Task 9 同步更正 spec。

---

## 关键约定（所有任务通用）

**锁套件（不可改，必须保持字节级不变 + 全绿，作为 `is_perp=False`/无 ls 数据回归证据）：**
`tests/test_backtest_engine.py`、`tests/test_crypto_backtest.py`、`tests/test_backtest_summary.py`。本计划不触碰它们。

**后端验证（cwd=仓库根）：** 用 venv 解释器（仓库 PATH 无 `python`）：
```bash
.venv/bin/python -m pytest <test_path> -q
```
全量门禁：`PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`

**Web 验证脚手架（路径含空格，真路径 `npm ci` 会残缺安装；vitest 全绿≠web-gate）：**
```bash
# 1) 同步到 /tmp 无空格副本（首次保留 node_modules 缓存，后续增量同步源码）
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/
cd /tmp/dsa-web-verify
# 2) 首次安装（仅第一次需要；后续任务可跳过，除非 package.json 变）
npm ci
# 3) 单组件快测（快速反馈）
npx vitest run src/components/report/__tests__/<File>.test.tsx
# 4) web-gate（最终必跑，vitest 绿不代表 lint/build 绿）
npm run lint && npm run build
```
> 编辑始终在真实仓库 `apps/dsa-web/` 内进行；每次验证前重跑步骤 1 的 rsync 把改动带到 /tmp。

**commit 规范：** 英文类型前缀 + 中文描述，无 `Co-Authored-By`，无工具前缀。本地分支 `feat/crypto-perp-backtest`，不 push。

---

## Task 1: 抓取助手 `_okx_ratio` + 两端点常量

**Files:**
- Modify: `data_provider/crypto_derivatives.py`（常量区 + 新增 `_okx_ratio`，置于 `_okx_first` 之后）
- Test: `tests/test_crypto_derivatives_fetch.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_crypto_derivatives_fetch.py` 末尾追加：

```python
def test_okx_ratio_parses_latest_row(monkeypatch):
    monkeypatch.setattr(
        cd, "_http_get_json",
        lambda url, params=None, headers=None: {"code": "0", "data": [["1700000300000", "1.23"], ["1700000000000", "1.10"]]},
    )
    assert abs(cd._okx_ratio(cd.OKX_LS_ACCOUNT_URL, {"ccy": "BTC", "period": "5m"}) - 1.23) < 1e-12


def test_okx_ratio_structural_anomalies_return_none(monkeypatch):
    for payload in [{}, {"data": None}, {"data": []}, {"data": [["onlyts"]]}, {"data": "x"}, {"data": [123]}]:
        monkeypatch.setattr(cd, "_http_get_json", lambda url, params=None, headers=None, p=payload: p)
        assert cd._okx_ratio(cd.OKX_LS_TOP_URL, {}) is None


def test_okx_ratio_http_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd._okx_ratio(cd.OKX_LS_ACCOUNT_URL, {}) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py::test_okx_ratio_parses_latest_row -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'OKX_LS_ACCOUNT_URL'` 或 `_okx_ratio`）

- [ ] **Step 3: 实现常量 + 助手**

在 `data_provider/crypto_derivatives.py` 常量区（`OKX_FUNDING_HISTORY_URL` 行附近）追加：

```python
OKX_LS_ACCOUNT_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
OKX_LS_TOP_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
```

在 `_okx_first` 函数之后新增：

```python
def _okx_ratio(url: str, params: dict) -> Optional[float]:
    """GET OKX rubik 多空比端点，取最新一行 [ts, ratio] 的比值；失败/空/结构异常 → None（fail-soft）。"""
    try:
        data = _http_get_json(url, params)
    except Exception as e:
        logger.warning("[多空比] %s 抓取失败: %s", url, e)
        return None
    arr = data.get("data") if isinstance(data, dict) else None
    if isinstance(arr, list) and arr and isinstance(arr[0], (list, tuple)) and len(arr[0]) >= 2:
        return _to_float(arr[0][1])
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py -q`
Expected: PASS（含原有用例）

- [ ] **Step 5: 提交**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_derivatives_fetch.py
git commit -m "feat: 新增 OKX rubik 多空比抓取助手 _okx_ratio（取最新行/结构异常 fail-soft）"
```

---

## Task 2: `fetch_perp_metrics` 接入两路多空比

**Files:**
- Modify: `data_provider/crypto_derivatives.py:fetch_perp_metrics`
- Test: `tests/test_crypto_derivatives_fetch.py`

- [ ] **Step 1: 写失败测试 + 扩 `_fake_okx`**

把 `tests/test_crypto_derivatives_fetch.py` 顶部的 `_fake_okx` 改为（**top-trader 分支必须在通用 `long-short-account-ratio` 分支之前**，因前者 url 含后者子串；两端点返回**不同**比值以验证未误路由）：

```python
def _fake_okx(url, params=None, headers=None):
    if "long-short-account-ratio-contract-top-trader" in url:
        return {"code": "0", "data": [["1700000300000", "0.80"]]}
    if "long-short-account-ratio" in url:
        return {"code": "0", "data": [["1700000300000", "1.20"]]}
    if "funding-rate" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0000059888"}]}
    if "mark-price" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "markPx": "62669.5"}]}
    if "open-interest" in url:
        return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "2861888.58", "oiCcy": "28618.88", "oiUsd": "1793545573.08"}]}
    return {}
```

强化既有 `test_fetch_perp_metrics_parses_all`，在 `assert out["source"] == "okx"` 之前追加：

```python
    assert out["long_short_ratio"] == 1.20       # 全市场（通用端点）
    assert out["long_short_ratio_top"] == 0.80   # 大户（top-trader 端点）；两值不同 → 锁 url 前缀匹配顺序，防误路由
```

并新增单路失败 fail-soft 用例：

```python
def test_fetch_perp_metrics_single_ls_failure_keeps_others(monkeypatch):
    def partial(url, params=None, headers=None):
        if "long-short-account-ratio-contract-top-trader" in url:
            raise RuntimeError("okx top ls down")
        if "long-short-account-ratio" in url:
            return {"data": [["1700000300000", "1.20"]]}
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        if "mark-price" in url:
            return {"data": [{"markPx": "62669.5"}]}
        if "open-interest" in url:
            return {"data": [{"oi": "100.0", "oiUsd": "6000000.0"}]}
        return {}
    monkeypatch.setattr(cd, "_http_get_json", partial)
    out = cd.fetch_perp_metrics("BTC", "USDT")
    assert out["long_short_ratio"] == 1.20
    assert "long_short_ratio_top" not in out      # 大户路失败 → 缺省
    assert out["funding_rate"] == 0.0001
    assert out["source"] == "okx"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py::test_fetch_perp_metrics_parses_all -q`
Expected: FAIL（`KeyError: 'long_short_ratio'`）

- [ ] **Step 3: 实现 `fetch_perp_metrics` 接两路**

在 `data_provider/crypto_derivatives.py` 把 `fetch_perp_metrics` 的并发块改为 `max_workers=5` 并加两路：

```python
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_fr = ex.submit(_okx_first, OKX_FUNDING_URL, {"instId": inst})
        f_mp = ex.submit(_okx_first, OKX_MARK_URL, {"instType": "SWAP", "instId": inst})
        f_oi = ex.submit(_okx_first, OKX_OI_URL, {"instId": inst})
        f_ls = ex.submit(_okx_ratio, OKX_LS_ACCOUNT_URL, {"ccy": base, "period": "5m"})
        f_lst = ex.submit(_okx_ratio, OKX_LS_TOP_URL, {"instId": inst, "period": "5m"})
        fr, mp, oi = f_fr.result(), f_mp.result(), f_oi.result()
        ls, lst = f_ls.result(), f_lst.result()
```

在 OI 解析之后、`if out:` 之前追加：

```python
    if ls is not None:
        out["long_short_ratio"] = ls
    if lst is not None:
        out["long_short_ratio_top"] = lst
```

- [ ] **Step 4: 运行确认通过（含非线性零调用、全失败用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_fetch.py -q`
Expected: PASS（包括 `test_fetch_perp_metrics_non_linear_quote_returns_empty` 的 `called["n"]==0`——guard 在 submit 之前；`test_fetch_perp_metrics_all_fail_returns_empty`）

- [ ] **Step 5: 提交**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_derivatives_fetch.py
git commit -m "feat: fetch_perp_metrics 接入全市场/大户多空比（并发 3→5，presence-only）"
```

---

## Task 3: per-coin row + 复盘快照 OI 加权聚合

**Files:**
- Modify: `data_provider/crypto_derivatives.py:_perp_row_for_symbol`、`fetch_perp_market_snapshot`
- Test: `tests/test_crypto_perp_snapshot.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_crypto_perp_snapshot.py` 末尾追加：

```python
def test_aggregates_oi_weighted_long_short_ratio(monkeypatch):
    table = {
        "BTC/USDT": {"open_interest_usd": 1000.0, "long_short_ratio": 1.0, "long_short_ratio_top": 0.8, "source": "okx"},
        "ETH/USDT": {"open_interest_usd": 3000.0, "long_short_ratio": 2.0, "long_short_ratio_top": 1.2, "source": "okx"},
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    # 全市场 (1*1000 + 2*3000)/4000 = 1.75；大户 (0.8*1000 + 1.2*3000)/4000 = 1.1
    assert abs(out["avg_long_short_ratio"] - 1.75) < 1e-12
    assert abs(out["avg_long_short_ratio_top"] - 1.1) < 1e-12
    by_sym = {c["symbol"]: c for c in out["coins"]}
    assert by_sym["ETH/USDT"]["long_short_ratio"] == 2.0
    assert by_sym["BTC/USDT"]["long_short_ratio_top"] == 0.8


def test_long_short_ratio_excludes_coin_without_oi(monkeypatch):
    table = {
        "BTC/USDT": {"open_interest_usd": 1000.0, "long_short_ratio": 1.0},
        "ETH/USDT": {"long_short_ratio": 5.0},  # 无 OI：进篮子但不参与权重
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert abs(out["avg_long_short_ratio"] - 1.0) < 1e-12  # 仅 BTC 计入
    assert len(out["coins"]) == 2                          # ETH 仅有 ls 也进篮子


def test_no_long_short_ratio_when_absent(monkeypatch):
    table = {"BTC/USDT": {"funding_rate": 0.0001, "open_interest_usd": 1000.0}}
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT"])
    assert "avg_long_short_ratio" not in out
    assert "avg_long_short_ratio_top" not in out
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_perp_snapshot.py::test_aggregates_oi_weighted_long_short_ratio -q`
Expected: FAIL（`KeyError: 'avg_long_short_ratio'`）

- [ ] **Step 3: 实现 per-coin row + 聚合**

在 `_perp_row_for_symbol` 的 `open_interest_usd` 追加块之后加：

```python
        if metrics.get("long_short_ratio") is not None:
            row["long_short_ratio"] = metrics["long_short_ratio"]
        if metrics.get("long_short_ratio_top") is not None:
            row["long_short_ratio_top"] = metrics["long_short_ratio_top"]
```

把 `fetch_perp_market_snapshot` 的聚合段（`weighted_num` 起至 `total_open_interest_usd` 输出）替换为：

```python
    weighted_num = 0.0
    weighted_den = 0.0
    total_oi = 0.0
    has_oi = False
    ls_num = ls_den = 0.0       # 全市场多空比 OI 加权
    lst_num = lst_den = 0.0     # 大户多空比 OI 加权
    for r in rows:
        fr = r.get("funding_rate")
        oi = r.get("open_interest_usd")
        if oi is not None:
            total_oi += oi
            has_oi = True
            if fr is not None and oi > 0:
                weighted_num += fr * oi
                weighted_den += oi
            if oi > 0:
                lsr = r.get("long_short_ratio")
                if lsr is not None:
                    ls_num += lsr * oi
                    ls_den += oi
                lsrt = r.get("long_short_ratio_top")
                if lsrt is not None:
                    lst_num += lsrt * oi
                    lst_den += oi
    if weighted_den > 0:
        out["avg_funding_rate"] = weighted_num / weighted_den
    if has_oi:
        out["total_open_interest_usd"] = total_oi
    if ls_den > 0:
        out["avg_long_short_ratio"] = ls_num / ls_den
    if lst_den > 0:
        out["avg_long_short_ratio_top"] = lst_num / lst_den
```

（`coins` 构造不变：整行 `r` 已携带 ls 字段。）

- [ ] **Step 4: 运行确认通过（含既有聚合用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_crypto_perp_snapshot.py -q`
Expected: PASS（含原 `test_aggregates_weighted_funding_and_total_oi` 等）

- [ ] **Step 5: 提交**

```bash
git add data_provider/crypto_derivatives.py tests/test_crypto_perp_snapshot.py
git commit -m "feat: 复盘快照按 OI 加权聚合多空比，per-coin 携带各币比值"
```

---

## Task 4: 单股 prompt 透出（analyzer.py）

**Files:**
- Modify: `src/analyzer.py`（`crypto_contracts` 表格块，资金费率行之后）
- Test: `tests/test_crypto_derivatives_prompt.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_crypto_derivatives_prompt.py` 末尾追加：

```python
def test_prompt_includes_long_short_ratio_rows():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "long_short_ratio": 1.23, "long_short_ratio_top": 0.85, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "多空比(全市场)" in prompt
    assert "1.23" in prompt
    assert "多空比(大户)" in prompt
    assert "0.85" in prompt


def test_prompt_omits_long_short_ratio_when_absent():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "多空比" not in prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py::test_prompt_includes_long_short_ratio_rows -q`
Expected: FAIL（断言 `多空比(全市场)` 不在 prompt）

- [ ] **Step 3: 实现两行**

在 `src/analyzer.py` 的合约指标块，资金费率 `if fr is not None: rows.append(...)` 之后、`mp = contracts.get("mark_price")` 之前插入：

```python
            lsr = contracts.get("long_short_ratio")
            if lsr is not None:
                rows.append(f"| 多空比(全市场) | {lsr:.2f} | >1 散户偏多 / <1 偏空 |")
            lsr_top = contracts.get("long_short_ratio_top")
            if lsr_top is not None:
                rows.append(f"| 多空比(大户) | {lsr_top:.2f} | 大户账户净多/净空，与散户对比看分歧 |")
```

- [ ] **Step 4: 运行确认通过（含既有合约块用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_crypto_derivatives_prompt.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/analyzer.py tests/test_crypto_derivatives_prompt.py
git commit -m "feat: 单股合约指标 prompt 透出多空比(全市场/大户)"
```

---

## Task 5: 复盘 prompt 透出（market_analyzer.py，zh+en）

**Files:**
- Modify: `src/market_analyzer.py:_get_crypto_perp_sentiment_prompt_block`
- Test: `tests/test_crypto_perp_review_prompt.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_crypto_perp_review_prompt.py` 末尾追加：

```python
def test_perp_prompt_block_zh_long_short_ratio():
    a = MarketAnalyzer(region="crypto")
    perp = {
        "avg_funding_rate": 0.00025,
        "avg_long_short_ratio": 1.75,
        "avg_long_short_ratio_top": 1.10,
        "coins": [{"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000.0, "long_short_ratio": 2.0}],
    }
    block = a._get_crypto_perp_sentiment_prompt_block(perp, "zh")
    assert "OI 加权多空比(全市场)：1.75" in block
    assert "OI 加权多空比(大户)：1.10" in block
    assert "多空比 2.00" in block  # per-coin 全市场比值


def test_perp_prompt_block_en_long_short_ratio():
    a = MarketAnalyzer(region="crypto")
    perp = {
        "avg_long_short_ratio": 1.75,
        "avg_long_short_ratio_top": 1.10,
        "coins": [{"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000.0, "long_short_ratio": 2.0}],
    }
    block = a._get_crypto_perp_sentiment_prompt_block(perp, "en")
    assert "Market long/short ratio (OI-weighted): 1.75" in block
    assert "Top-trader long/short ratio (OI-weighted): 1.10" in block
    assert "L/S 2.00" in block


def test_perp_prompt_block_omits_long_short_ratio_when_absent():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(PERP, "zh")  # 模块常量 PERP 无 ls 字段
    assert "多空比" not in block
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_crypto_perp_review_prompt.py::test_perp_prompt_block_zh_long_short_ratio -q`
Expected: FAIL

- [ ] **Step 3: 实现 zh+en 聚合行 + per-coin**

在 `src/market_analyzer.py:_get_crypto_perp_sentiment_prompt_block` 内：

**en 分支**——在 `if toi is not None: parts.append(f"- Total open interest: ${toi:,.0f}")` 之后、`for c in coins:` 之前插入：

```python
            lsr = perp.get("avg_long_short_ratio")
            if lsr is not None:
                parts.append(f"- Market long/short ratio (OI-weighted): {lsr:.2f}")
            lsr_top = perp.get("avg_long_short_ratio_top")
            if lsr_top is not None:
                parts.append(f"- Top-trader long/short ratio (OI-weighted): {lsr_top:.2f}")
```

en 分支的 per-coin 追加行 `parts.append(f"  - {sym}: {fr_txt}{oi_txt}")` 替换为：

```python
                lsr_c = c.get("long_short_ratio")
                ls_txt = f", L/S {lsr_c:.2f}" if lsr_c is not None else ""
                parts.append(f"  - {sym}: {fr_txt}{oi_txt}{ls_txt}")
```

**zh 分支**——在 `if toi is not None: parts.append(f"- 总未平仓量：${toi:,.0f}")` 之后、`for c in coins:` 之前插入：

```python
        lsr = perp.get("avg_long_short_ratio")
        if lsr is not None:
            parts.append(f"- OI 加权多空比(全市场)：{lsr:.2f}")
        lsr_top = perp.get("avg_long_short_ratio_top")
        if lsr_top is not None:
            parts.append(f"- OI 加权多空比(大户)：{lsr_top:.2f}")
```

zh 分支的 per-coin 追加行 `parts.append(f"  - {sym}：{fr_txt}{oi_txt}")` 替换为：

```python
            lsr_c = c.get("long_short_ratio")
            ls_txt = f"，多空比 {lsr_c:.2f}" if lsr_c is not None else ""
            parts.append(f"  - {sym}：{fr_txt}{oi_txt}{ls_txt}")
```

> 注意：en 行用半角 `: `/`  - {sym}: `，zh 行用全角 `：`/`  - {sym}：`，据此区分两分支的同形代码。`不得编造数据`/`do not invent data` 提示语保持不变。

- [ ] **Step 4: 运行确认通过（含既有 zh/en/零值/N\\A 用例不回归）**

Run: `.venv/bin/python -m pytest tests/test_crypto_perp_review_prompt.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/market_analyzer.py tests/test_crypto_perp_review_prompt.py
git commit -m "feat: 复盘永续情绪 prompt 透出 OI 加权多空比(全市场/大户)+per-coin"
```

---

## Task 6: 结构化 payload 透传锁测（API 增量契约）

**Files:**
- Test: `tests/test_crypto_contracts_extract.py`、`tests/test_crypto_perp_review_payload.py`

> 透传层 `extract_crypto_contracts_detail_fields`（整 dict）与 `payload["perp_sentiment"]=perp_sentiment`（整体赋值）无需改代码——本任务仅加回归测试锁定新字段增量流过 API。

- [ ] **Step 1: 写测试**

在 `tests/test_crypto_contracts_extract.py` 末尾追加：

```python
def test_long_short_ratio_fields_pass_through():
    contracts = {**_CONTRACTS, "long_short_ratio": 1.23, "long_short_ratio_top": 0.85}
    snapshot = {"enhanced_context": {"crypto_contracts": contracts}}
    out = extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"]
    assert out["long_short_ratio"] == 1.23
    assert out["long_short_ratio_top"] == 0.85
```

在 `tests/test_crypto_perp_review_payload.py` 末尾追加：

```python
def test_payload_perp_sentiment_passes_long_short_ratio():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    perp = {**PERP, "avg_long_short_ratio": 1.75, "avg_long_short_ratio_top": 1.10}
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment=perp)
    assert payload["perp_sentiment"]["avg_long_short_ratio"] == 1.75
    assert payload["perp_sentiment"]["avg_long_short_ratio_top"] == 1.10
```

- [ ] **Step 2: 运行确认通过（透传层已支持，应直接 PASS）**

Run: `.venv/bin/python -m pytest tests/test_crypto_contracts_extract.py tests/test_crypto_perp_review_payload.py -q`
Expected: PASS（证明新字段经透传层进入 API payload）

- [ ] **Step 3: 后端全量门禁**

Run: `PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh`
Expected: 全绿（含锁套件字节级不变）

- [ ] **Step 4: 提交**

```bash
git add tests/test_crypto_contracts_extract.py tests/test_crypto_perp_review_payload.py
git commit -m "test: 锁定多空比字段经结构化 payload 透传进 API（增量契约）"
```

---

## Task 7: Web 单股卡片渲染（ReportCryptoMetrics）

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts:CryptoContracts`
- Modify: `apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx`
- Test: `apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx`

> 字段经通用 `toCamelCase` 自动转换：`long_short_ratio`→`longShortRatio`、`long_short_ratio_top`→`longShortRatioTop`。

- [ ] **Step 1: 写失败测试**

在 `apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx` 末尾（最后一个 `});` 之前的顶层）追加（沿用该文件既有 `render`/`screen` import 风格）：

```tsx
  it('renders long/short ratio rows when present', () => {
    render(<ReportCryptoMetrics contracts={{ fundingRate: 0.0001, longShortRatio: 1.23, longShortRatioTop: 0.85 }} language="zh" />);
    expect(screen.getByText('多空比(全市场)')).toBeInTheDocument();
    expect(screen.getByText('1.23')).toBeInTheDocument();
    expect(screen.getByText('多空比(大户)')).toBeInTheDocument();
    expect(screen.getByText('0.85')).toBeInTheDocument();
  });

  it('omits long/short ratio rows when absent', () => {
    render(<ReportCryptoMetrics contracts={{ fundingRate: 0.0001 }} language="zh" />);
    expect(screen.queryByText('多空比(全市场)')).not.toBeInTheDocument();
  });
```

> 若该测试文件用 `describe(...)` 包裹，请把两个 `it` 放进同一 describe 块内；import 与现有用例保持一致。

- [ ] **Step 2: 运行确认失败**

```bash
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify && npm ci
npx vitest run src/components/report/__tests__/ReportCryptoMetrics.test.tsx
```
Expected: FAIL（找不到 `多空比(全市场)` 文本）

- [ ] **Step 3: 实现类型 + 渲染**

`apps/dsa-web/src/types/analysis.ts` 的 `CryptoContracts` 接口追加两字段：

```typescript
  longShortRatio?: number;      // 全市场账户多空比（>1 偏多）
  longShortRatioTop?: number;   // 大户账户多空比
```

`apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx`：
- `TEXT.zh` 追加：

```typescript
    longShortRatio: '多空比(全市场)',
    longShortHint: '>1 散户偏多 / <1 偏空',
    longShortRatioTop: '多空比(大户)',
```
- `TEXT.en` 追加：

```typescript
    longShortRatio: 'L/S Ratio (Market)',
    longShortHint: '>1 longs lean / <1 shorts lean',
    longShortRatioTop: 'L/S Ratio (Top Traders)',
```
- 在 `fundingRate` 行 `rows.push(...)` 之后、`markPrice` 行之前插入：

```tsx
  if (typeof contracts.longShortRatio === 'number') {
    rows.push({ label: t.longShortRatio, value: contracts.longShortRatio.toFixed(2), hint: t.longShortHint });
  }
  if (typeof contracts.longShortRatioTop === 'number') {
    rows.push({ label: t.longShortRatioTop, value: contracts.longShortRatioTop.toFixed(2) });
  }
```

- [ ] **Step 4: 运行确认通过**

```bash
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify
npx vitest run src/components/report/__tests__/ReportCryptoMetrics.test.tsx
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/types/analysis.ts apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx
git commit -m "feat(web): 单股合约指标卡片渲染多空比(全市场/大户)"
```

---

## Task 8: Web 复盘卡片渲染（MarketReviewReportView）

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts:PerpSentiment`、`PerpSentimentCoin`
- Modify: `apps/dsa-web/src/components/report/MarketReviewReportView.tsx`
- Test: `apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx`

- [ ] **Step 1: 写失败测试**

在 `MarketReviewReportView.test.tsx` 现有「渲染加密永续情绪」用例（约 line 298 的 `perpSentiment` fixture）的同组，新增一个用例（沿用该文件既有 render 工具与 payload 构造方式）：

```tsx
  it('renders long/short ratio in perp sentiment card', () => {
    renderMarketReview({
      region: 'crypto',
      perpSentiment: {
        avgFundingRate: 0.00025,
        totalOpenInterestUsd: 4000000000,
        avgLongShortRatio: 1.75,
        avgLongShortRatioTop: 1.1,
        coins: [{ symbol: 'ETH/USDT', fundingRate: 0.0003, openInterestUsd: 3000000000, longShortRatio: 2.0 }],
      },
    });
    expect(screen.getByText('OI 加权多空比(全市场)')).toBeInTheDocument();
    expect(screen.getByText('1.75')).toBeInTheDocument();
    expect(screen.getByText('OI 加权多空比(大户)')).toBeInTheDocument();
    expect(screen.getByText('1.10')).toBeInTheDocument();
  });
```

> `renderMarketReview` 用该测试文件已有的渲染辅助/payload 包装方式（对照 line 298 那个用例怎么构造 payload 就怎么写）；若该文件直接用 `render(<MarketReviewReportView .../>)`，照搬其入参结构，仅替换 `perpSentiment` 内容。

- [ ] **Step 2: 运行确认失败**

```bash
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify
npx vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx
```
Expected: FAIL（找不到 `OI 加权多空比(全市场)`）

- [ ] **Step 3: 实现类型 + 双语 text + 渲染**

`apps/dsa-web/src/types/analysis.ts`：
- `PerpSentimentCoin` 追加：`  longShortRatio?: number;`
- `PerpSentiment` 追加：

```typescript
  avgLongShortRatio?: number;
  avgLongShortRatioTop?: number;
```

`apps/dsa-web/src/components/report/MarketReviewReportView.tsx`：
- text 类型（`...totalOpenInterest: string;` 所在接口）追加：`  avgLongShortRatio: string;` `  avgLongShortRatioTop: string;`
- `zh` 文案（`totalOpenInterest: '总未平仓量',` 之后）追加：

```typescript
    avgLongShortRatio: 'OI 加权多空比(全市场)',
    avgLongShortRatioTop: 'OI 加权多空比(大户)',
```
- `en` 文案（`totalOpenInterest: 'Total Open Interest',` 之后）追加：

```typescript
    avgLongShortRatio: 'OI-weighted L/S (Market)',
    avgLongShortRatioTop: 'OI-weighted L/S (Top)',
```
- 聚合网格——在 `totalOpenInterestUsd` 的 `<div>` 卡片（line ~601-606）之后、`</div>`（grid 结束，line 607）之前追加两块：

```tsx
                      {marketData.perpSentiment.avgLongShortRatio !== undefined ? (
                        <div className="rounded-lg border border-subtle p-3">
                          <p className="label-uppercase">{marketReviewText.avgLongShortRatio}</p>
                          <p className="mt-1 font-semibold text-foreground">{marketData.perpSentiment.avgLongShortRatio.toFixed(2)}</p>
                        </div>
                      ) : null}
                      {marketData.perpSentiment.avgLongShortRatioTop !== undefined ? (
                        <div className="rounded-lg border border-subtle p-3">
                          <p className="label-uppercase">{marketReviewText.avgLongShortRatioTop}</p>
                          <p className="mt-1 font-semibold text-foreground">{marketData.perpSentiment.avgLongShortRatioTop.toFixed(2)}</p>
                        </div>
                      ) : null}
```
- per-coin `<li>`——在 `{c.openInterestUsd !== undefined ? ...}` 行（line ~614）之后追加：

```tsx
                            {c.longShortRatio !== undefined ? ` · L/S ${c.longShortRatio.toFixed(2)}` : ''}
```

> 渲染/presence 门（`hasPerpSentimentData` 与 line 588-591 的渲染条件）**无需改**：`avgLongShortRatio` 由聚合保证只在某币 `oi>0` 时存在，此时 `totalOpenInterestUsd` 必同时存在，已触发门；per-coin 比值随 `coins.length>0` 已覆盖。

- [ ] **Step 4: 运行确认通过（含既有复盘渲染用例不回归）**

```bash
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify
npx vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx
```
Expected: PASS

- [ ] **Step 5: web-gate（lint + build，vitest 绿不代表 gate 绿）**

```bash
rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify
npm run lint && npm run build
```
Expected: lint 0 error、build 成功

- [ ] **Step 6: 提交**

```bash
git add apps/dsa-web/src/types/analysis.ts apps/dsa-web/src/components/report/MarketReviewReportView.tsx apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx
git commit -m "feat(web): 复盘永续情绪卡片渲染 OI 加权多空比(全市场/大户)+per-coin"
```

---

## Task 9: 文档同步 + spec 更正

**Files:**
- Modify: `docs/crypto-guide.md`（单股「合约市场指标」字段表 + 「永续情绪聚合」+ 结构化字段清单行 243 附近）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加一行）
- Modify: `docs/superpowers/specs/2026-06-11-crypto-perp-long-short-ratio-design.md`（更正 §5 "零改动面" 表述）

> Docs only, tests not run。需核对命令/字段名与实际仓库一致。

- [ ] **Step 1: crypto-guide 字段表**

在 `docs/crypto-guide.md` 单股「合约市场指标」表（约 line 229-231，资金费率/标记价/未平仓量行）后追加两行：

```markdown
| 多空比(全市场) | OKX `/rubik/stat/contracts/long-short-account-ratio`（`ccy`） | 全市场散户账户净多/净空，>1 偏多 |
| 多空比(大户) | OKX `.../long-short-account-ratio-contract-top-trader`（`instId`） | 大户账户多空比，与散户对比看分歧 |
```

在「8.6 永续情绪聚合」（约 line 200-203）补充：聚合新增 **OI 加权全市场/大户多空比**；并在结构化透出说明（约 line 241-243，列 `crypto_contracts` 字段 `funding_rate/mark_price/open_interest/open_interest_usd/source` 处）把字段清单补上 `long_short_ratio` / `long_short_ratio_top`（presence-only，经透传随 API 返回；Web 卡片已渲染）。

- [ ] **Step 2: CHANGELOG 扁平一行**

在 `docs/CHANGELOG.md` 的 `## [Unreleased]` 段**末尾**追加（单行，无分类标题）：

```markdown
- [新功能] crypto 永续新增多空比指标（全市场账户 + 大户，单股/复盘 prompt 与 OI 加权聚合，经透传随 API 返回并在 Web 卡片渲染，presence-only，默认开）
```

- [ ] **Step 3: 更正 spec §5**

在 `docs/superpowers/specs/2026-06-11-crypto-perp-long-short-ratio-design.md` 的 §5「无改动面」项，把 `API/Schema、Web/Desktop` 从"零改动面"中移除，改注明：结构化 payload 为整 dict 透传，新字段经 `toCamelCase` 增量进入 API 响应（无 schema 改动）与 Web 卡片（已渲染）；DB schema、`evaluate_single`、回测 service、`.env.example` 仍零改动。

- [ ] **Step 4: 提交**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md docs/superpowers/specs/2026-06-11-crypto-perp-long-short-ratio-design.md
git commit -m "docs: 多空比指标补 crypto-guide 字段/聚合/结构化清单与 CHANGELOG，更正 spec 改动面表述"
```

---

## 最终验证（全部任务完成后）

- [ ] 后端全量门禁：`PATH="$PWD/.venv/bin:$PATH" ./scripts/ci_gate.sh` → 全绿
- [ ] 锁套件字节级不变：`git diff --stat main -- tests/test_backtest_engine.py tests/test_crypto_backtest.py tests/test_backtest_summary.py` → 无输出
- [ ] Web gate：`rsync -a --delete --exclude node_modules "apps/dsa-web/" /tmp/dsa-web-verify/ && cd /tmp/dsa-web-verify && npm run lint && npm run build` → 通过
- [ ] 交付说明：改了什么 / 为什么 / 验证情况 / 未验证项（OKX rubik 端点在线响应：离线 monkeypatch 已覆盖解析，真实排序/列布局见 spec §9 未验证假设）/ 风险点 / 回滚方式

---

## Self-Review（plan 对照 spec）

- **Spec §1 端点/语义** → Task 1（常量+助手）、Task 2（接两路）。✓
- **Spec §2 抓取层** → Task 1、Task 2。✓
- **Spec §3 聚合层** → Task 3。✓
- **Spec §4.1 单股 prompt** → Task 4。✓
- **Spec §4.2 复盘 prompt（zh+en+per-coin）** → Task 5。✓
- **Spec §5 契约（透传增量进 API）** → Task 6（锁测）、Task 9（更正 §5 表述）。✓
- **Spec §6 测试计划** → Task 1-6 测试。✓
- **Spec §7 文档** → Task 9。✓
- **用户选 B：Web 渲染** → Task 7（单股卡片）、Task 8（复盘卡片）。✓
- **类型一致性**：后端字段 `long_short_ratio`/`long_short_ratio_top`/`avg_long_short_ratio`/`avg_long_short_ratio_top`；Web camel `longShortRatio`/`longShortRatioTop`/`avgLongShortRatio`/`avgLongShortRatioTop` 全程一致。✓
- **占位扫描**：每步含完整代码/命令/期望输出，无 TBD。✓
