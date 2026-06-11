# 设计：crypto 永续多空比指标（全市场账户 + 大户，presence-only）

- 日期：2026-06-11
- 状态：设计已批准，待写实施计划
- 子项目：perpetuals 增量指标（多空比）
- 关联前序：A（单股 perp 透出）/B（复盘聚合）/E（perp 回测）已落地于 `feat/crypto-perp-backtest`

## 0. 背景与范围

现有 perp 指标链路三层：

- **抓取层** `data_provider/crypto_derivatives.py`：`fetch_perp_metrics`（单标的，instId=`BASE-QUOTE-SWAP`，并发拉 funding/mark/OI 三路，presence-only，`source=okx`）；`fetch_perp_market_snapshot`（一篮子并发取各币指标 → OI 加权资金费率 + 总 OI + top5 明细）。
- **编排层** `crypto_derivatives_service.py`（单股，写 `context['crypto_contracts']`）；`crypto_derivatives_review_service.py`（复盘，读篮子 `crypto_market_review_symbols`）。
- **透出层** `analyzer.py`（单股 prompt 合约表格）；`market_analyzer.py`（复盘情绪事实块，中英双语）。

本子项目新增**两个 presence-only 指标字段**，沿同一条链路接入单股透出与复盘聚合两处：

- `long_short_ratio`：全市场散户账户多空比（>1 偏多）。
- `long_short_ratio_top`：大户账户多空比（与散户对比看分歧）。

**明确不做（YAGNI / 稳定性护栏）**：
- 不改回测引擎、不进 `evaluate_single`、不碰做空/资金费语义（指标纯透出）。
- 零 DB schema 改动、零迁移。
- 不新增配置项（复用 `crypto_derivatives_enabled` 门控）→ **不动 `.env.example`**。
- 不引入持仓口径（position ratio）；大户固定用账户口径，与全市场账户口径对称。
- 不改 `src/agent/tools/analysis_tools.py:364`：经核对该处"多空平衡"是 K 线十字星（Doji）形态描述，与永续多空比无关，不联动。

## 1. 指标定义与端点

| 字段 | OKX 端点（公共，免 key） | 参数 | 语义 |
|------|------|------|------|
| `long_short_ratio` | `/api/v5/rubik/stat/contracts/long-short-account-ratio` | `ccy=BASE`, `period=5m` | 全市场散户账户净多/净空比，>1 偏多 |
| `long_short_ratio_top` | `/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader` | `instId=BASE-QUOTE-SWAP`, `period=5m` | 大户账户多空比 |

两端点返回结构均为 `{"code":"0","data":[[ts, ratio], ...]}`，按时间**新→旧**排序（取 `data[0]` 为最新；**排序是关键假设，列入 §9 验证项**）。presence-only 取 `data[0]` 的比值元素（索引 1）。`period=5m` 取最新颗粒值（最鲜）。非线性计价（quote∉{USDT,USDC}）/参数非法/空/异常 → 字段缺省（fail-soft，不抛）。

注意：全市场端点 url 子串 `long-short-account-ratio` 是大户端点 url（`...-contract-top-trader`）的前缀，**测试 fake 的 url 匹配必须先判 `top-trader` 再判通用串**，否则两路会命中同一分支。

## 2. 抓取层改动（`data_provider/crypto_derivatives.py`）

### 2.1 新增常量

```python
OKX_LS_ACCOUNT_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
OKX_LS_TOP_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
```

### 2.2 新增解析助手（rubik 返回 `data[0]` 是数组，不可复用 `_okx_first`）

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

### 2.3 `fetch_perp_metrics`：并发槽 3→5，新增两路（保持 presence-only / `source=okx`）

在 `inst = f"{base}-{quote}-SWAP"` 后，把 `ThreadPoolExecutor(max_workers=3)` 改为 `max_workers=5`，并新增两个 submit 与结果解析：

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

`source=okx` 仍由既有 `if out:` 设置——任一字段（含多空比）存在即标源，语义不变。

### 2.4 `_perp_row_for_symbol`：把两字段带进 per-coin row

在 `open_interest_usd` 之后追加（presence-only）：

```python
        if metrics.get("long_short_ratio") is not None:
            row["long_short_ratio"] = metrics["long_short_ratio"]
        if metrics.get("long_short_ratio_top") is not None:
            row["long_short_ratio_top"] = metrics["long_short_ratio_top"]
```

注意：原先 row 仅由 funding/oi 触发非空；某币若**只有多空比**也会令 row 非空 → 带 symbol 进篮子。该币无 OI 故不参与 OI 加权（见 §3），funding 缺省时在 top5 排序按 key=-1.0 落后，符合既有 fail-soft 形态。

## 3. 聚合层改动（`fetch_perp_market_snapshot`）

复用既有按 `rows` 的单次遍历，在累加 funding/OI 的同时累加两路多空比的 **OI 加权**分子分母（OI 缺省或 ≤0 的币不参与权重，与 `avg_funding_rate` 同法）：

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

`coins`（top5 明细）继续整行返回 `r`，已自动携带 `long_short_ratio`/`long_short_ratio_top`，无需改 coins 构造。

## 4. 透出层改动

### 4.1 单股（`src/analyzer.py`，合约市场指标表格）

在资金费率行 `rows.append(...)` 之后、标记价之前，插入两行（presence-only；比值用 `:.2f`）：

```python
            lsr = contracts.get("long_short_ratio")
            if lsr is not None:
                rows.append(f"| 多空比(全市场) | {lsr:.2f} | >1 散户偏多 / <1 偏空 |")
            lsr_top = contracts.get("long_short_ratio_top")
            if lsr_top is not None:
                rows.append(f"| 多空比(大户) | {lsr_top:.2f} | 大户账户净多/净空，与散户对比看分歧 |")
```

表头说明文案（"结合资金费率与持仓判断…"）不变。

### 4.2 复盘（`src/market_analyzer.py`，`_get_crypto_perp_sentiment_prompt_block`，zh + en 双分支）

**聚合行**：在 `total_open_interest_usd` 行之后、coins 循环之前，两分支各加（presence-only）：

- en：
  ```python
            lsr = perp.get("avg_long_short_ratio")
            if lsr is not None:
                parts.append(f"- Market long/short ratio (OI-weighted): {lsr:.2f}")
            lsr_top = perp.get("avg_long_short_ratio_top")
            if lsr_top is not None:
                parts.append(f"- Top-trader long/short ratio (OI-weighted): {lsr_top:.2f}")
  ```
- zh：
  ```python
        lsr = perp.get("avg_long_short_ratio")
        if lsr is not None:
            parts.append(f"- OI 加权多空比(全市场)：{lsr:.2f}")
        lsr_top = perp.get("avg_long_short_ratio_top")
        if lsr_top is not None:
            parts.append(f"- OI 加权多空比(大户)：{lsr_top:.2f}")
  ```

**per-coin 行**：在两分支 coins 循环里，于现有 `fr_txt`/`oi_txt` 后追加全市场多空比（大户仅在聚合行体现，per-coin 保持精简）：

- en：`ls_txt = f", L/S {lsr_c:.2f}" if lsr_c is not None else ""`，coin 行改为 `f"  - {sym}: {fr_txt}{oi_txt}{ls_txt}"`，其中 `lsr_c = c.get("long_short_ratio")`。
- zh：`ls_txt = f"，多空比 {lsr_c:.2f}" if lsr_c is not None else ""`，coin 行改为 `f"  - {sym}：{fr_txt}{oi_txt}{ls_txt}"`。

`不得编造数据` / `do not invent data` 提示语不变。空数据/非 crypto 仍返回 `""`（逻辑不变）。

## 5. 门控 / 契约 / 无改动面

- **门控**：单股经 `CryptoDerivativesService.collect`（已判 `crypto_derivatives_enabled`）；复盘经 `CryptoDerivativesReviewService.collect`（已判 enabled + region）。多空比随 `fetch_perp_metrics`/`fetch_perp_market_snapshot` 流入，**无需新增门控**。
- **契约**：纯追加 dict 字段；下游 `crypto_contracts` / 复盘 payload 的消费方读取靠 `.get(...)`，旧字段全保留，向后兼容。
- **零改动面**：DB schema、`evaluate_single`、回测 service、`.env.example`、API/Schema、Web/Desktop、`analysis_tools.py`。

## 6. 测试计划

新增用例为主；既有用例因不检查"排他性"且不含多空比数据，缺省字段不渲染，应保持全绿（强回归证据）。

- **抓取层** `tests/test_crypto_derivatives_fetch.py`（扩展）：
  - 扩 `_fake_okx`，新增两路 ls 端点（**先判 `top-trader` 再判通用 `long-short-account-ratio`**），两端点返回**不同**比值（如全市场 1.20、大户 0.80），结构 `{"data": [[ts, ratio], ...]}`。
  - 强化 `test_fetch_perp_metrics_parses_all`：分别断言 `out["long_short_ratio"]` 取到全市场值、`out["long_short_ratio_top"]` 取到大户值，**两值不同**——这是 §1 url 前缀匹配顺序（top-trader 误路由到通用分支）的**唯一回归保障**；若两端点 fake 返回同值，该陷阱将零覆盖。
  - 新增：单路 ls 失败其余保留（fail-soft）；非线性计价仍 `{}` 且零网络调用（断言 `called["n"]==0` 路径不被新 submit 破坏——guard 在 submit 之前）。
  - 新增 `_okx_ratio` 结构异常（data 非 list / 行长 <2 / 空）→ None。
- **聚合层** `tests/test_crypto_perp_snapshot.py`（扩展）：`_fake_metrics` 表加 `long_short_ratio`/`long_short_ratio_top`，断言 `avg_long_short_ratio`/`avg_long_short_ratio_top` 为 OI 加权值；某币缺 OI 不参与权重；coins 携带各币比值。
- **单股透出** `tests/test_crypto_derivatives_prompt.py`（扩展）：`crypto_contracts` 含两比值 → prompt 出现"多空比(全市场)""多空比(大户)"两行与格式化数值；缺省时不渲染。
- **复盘透出** `tests/test_crypto_perp_review_prompt.py`（扩展）：PERP 含 `avg_long_short_ratio*` 与 per-coin `long_short_ratio` → zh/en 聚合行与 per-coin 行渲染；缺省不渲染。

验证命令：`PATH=".venv/bin:$PATH" python -m pytest tests/test_crypto_derivatives_fetch.py tests/test_crypto_perp_snapshot.py tests/test_crypto_derivatives_prompt.py tests/test_crypto_perp_review_prompt.py -q` 后跑 `./scripts/ci_gate.sh` 全量。

## 7. 文档

- `docs/crypto-guide.md`：永续指标小节补两个多空比字段含义与口径（全市场账户 vs 大户账户、OI 加权聚合、presence-only）。
- `docs/CHANGELOG.md` `[Unreleased]`：**扁平**追加一行 `- [新功能] crypto 永续新增多空比指标（全市场账户 + 大户，单股透出与复盘 OI 加权聚合，presence-only）`。

## 8. 风险与回滚

- **风险**：rubik 端点偶发限流/不可达 → 字段缺省（presence-only fail-soft），不影响主流程。新增两路并发使单标的抓取从 3 路增至 5 路，仍在同一 `ThreadPoolExecutor` 一次性发起，延迟不叠加。
- **并发量**：复盘篮子路径为外层 `ThreadPoolExecutor(max_workers=8)` × 内层 5 路 ≈ 最多 40 个并发连接（原 24）。但两个新端点是**独立端点**，每端点并发仍为篮子规模 N（不变），故**单端点限流压力不变**，仅总连接数上升、可接受；触限亦 fail-soft 缺省。
- **回滚**：纯追加改动，按提交逐个 revert 即可；无数据迁移、无契约破坏，回滚后旧行为字节级恢复。

## 9. 未验证假设（实施/验证阶段确认）

- OKX rubik 端点在本环境可达（funding/mark/OI 已通，预计同样可达；不可达则 presence-only 自动缺省，离线测试用 monkeypatch 不依赖网络）。
- 大户端点 `long-short-account-ratio-contract-top-trader` 返回结构与全市场端点同为 `data:[[ts, ratio], ...]`（2 元行）。若实际为多元行，`_okx_ratio` 取索引 1 仍为比值；如不符，验证阶段据真实样例修正索引。
- **返回按时间新→旧排序**（故取 `data[0]` 为最新）。若实际为旧→新升序，`data[0]` 将是最旧值（默认 `limit=100`、`period=5m` ≈ 8 小时前），语义失真但仍 fail-soft 不崩。验证阶段确认排序；若为升序，改取 `data[-1]` 或传 `limit=1`（需先确认 ccy 端点 `long-short-account-ratio` 是否支持 `limit` 参数）。
