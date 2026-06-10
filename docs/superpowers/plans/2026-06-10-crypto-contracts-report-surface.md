# crypto 永续合约指标全链透出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase 1 已注入 `context["crypto_contracts"]` 的永续指标（资金费率/标记价/未平仓量）presence-only、additive 地透出到分析报告 API `ReportDetails` 与 Web 卡片。

**Architecture:** 数据已随 `context_snapshot`（嵌套于 `enhanced_context`）持久化。新增一个 `src/utils/data_processing.py` 抽取器，镜像 `extract_fundamental_detail_fields`/`extract_board_detail_fields`；在三个 `ReportDetails(...)` 构造点接线（覆盖 sync/async/历史重载全部入口）；Web 端靠既有 `toCamelCase(deep)` 自动映射，新增 typed 接口 + 独立卡片组件挂载到 `ReportSummary`。零 `data_provider/` 改动、零 DB schema 改动、无新配置项。

**Tech Stack:** Python（FastAPI / Pydantic v2 / pytest+subtests）、TypeScript（React / Vite / Vitest / @testing-library/react / camelcase-keys）。

**关键事实（已读码核实，2026-06-10）：**
- 快照嵌套：`crypto_contracts` 在 `context_snapshot["enhanced_context"]["crypto_contracts"]`（`pipeline.py:1860` `_build_context_snapshot`）。
- 数据存活：`_without_runtime_prompt_context`（`pipeline.py:2074`）只 pop `market_phase_context`/`portfolio_context`/`analysis_context_pack`/`analysis_context_pack_summary`，不动 `crypto_contracts`。
- 三入口读同一份持久化快照（经 `parse_json_field`）：`api/v1/endpoints/analysis.py:1184`、`:965`、`api/v1/endpoints/history.py:445`。
- 助手 `parse_json_field`（`data_processing.py:25`，对 dict 幂等）、`_non_empty_dict`（`:37`，空 dict/非 dict→None）。
- Web 深度 camel：`apps/dsa-web/src/api/analysis.ts` 对 report 调 `toCamelCase`(=`camelcaseKeys(data,{deep:true})`)，`crypto_contracts.funding_rate`→`cryptoContracts.fundingRate` 自动。
- Web 组合根：`apps/dsa-web/src/components/report/ReportSummary.tsx`（60/69/72/88 行渲染 ReportOverview/ReportStrategy/ReportNews/ReportDetails，`details` 在作用域）。

**测试运行约定：** Python 用仓库 venv：`PYTHONPATH="$PWD" .venv/bin/python -m pytest ...`。Web 因工作区路径含空格，`npm ci`/`lint`/`build` 须在 `/tmp` 无空格副本跑（见仓库记忆 [[workspace-path-space-npm]]）；`vitest` 单测可本地跑，但**最终须真跑 `npm run lint` + `npm run build`**，vitest 全绿 ≠ web-gate。

---

### Task 1: Python 抽取器 `extract_crypto_contracts_detail_fields`

**Files:**
- Test: `tests/test_crypto_contracts_extract.py`（新建）
- Modify: `src/utils/data_processing.py`（在 `extract_board_detail_fields` 之后新增函数，约 :260）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_crypto_contracts_extract.py`：

```python
# -*- coding: utf-8 -*-
"""crypto_contracts 抽取器：嵌套 enhanced_context + presence-only + JSON 字符串入参。"""
import json

from src.utils.data_processing import extract_crypto_contracts_detail_fields

_CONTRACTS = {
    "funding_rate": 0.0000059888,
    "mark_price": 62669.5,
    "open_interest": 2861888.58,
    "open_interest_usd": 1793545573.08,
    "source": "okx",
}


def test_nested_enhanced_context_returns_contracts():
    snapshot = {"enhanced_context": {"crypto_contracts": _CONTRACTS}}
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS


def test_json_string_input_is_parsed():
    snapshot = json.dumps({"enhanced_context": {"crypto_contracts": _CONTRACTS}})
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS


def test_missing_enhanced_context_returns_none():
    assert extract_crypto_contracts_detail_fields({"foo": 1})["crypto_contracts"] is None


def test_empty_contracts_dict_returns_none():
    snapshot = {"enhanced_context": {"crypto_contracts": {}}}
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] is None


def test_non_dict_snapshot_returns_none():
    for bad in (None, "not-json", 123, ["x"]):
        assert extract_crypto_contracts_detail_fields(bad)["crypto_contracts"] is None


def test_flat_top_level_fallback_returns_contracts():
    snapshot = {"crypto_contracts": _CONTRACTS}  # 防御性 dual-shape
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_contracts_extract.py -q`
Expected: FAIL（`ImportError: cannot import name 'extract_crypto_contracts_detail_fields'`）

- [ ] **Step 3: 实现抽取器**

在 `src/utils/data_processing.py` 中 `extract_board_detail_fields` 函数结束后（约 :260）追加：

```python
def extract_crypto_contracts_detail_fields(context_snapshot: Any) -> Dict[str, Any]:
    """从 context_snapshot 抽取 crypto 永续合约指标（presence-only）。

    crypto_contracts 由 pipeline 注入 enhanced_context，随快照持久化为
    {"enhanced_context": {"crypto_contracts": {...}}}。返回 {"crypto_contracts": dict|None}。
    """
    snapshot_obj = parse_json_field(context_snapshot)
    contracts = None
    if isinstance(snapshot_obj, dict):
        enhanced = snapshot_obj.get("enhanced_context")
        if isinstance(enhanced, dict):
            contracts = _non_empty_dict(enhanced.get("crypto_contracts"))
        if contracts is None:  # 防御性 dual-shape，对齐 extract_fundamental_context
            contracts = _non_empty_dict(snapshot_obj.get("crypto_contracts"))
    return {"crypto_contracts": contracts}
```

（`parse_json_field`、`_non_empty_dict`、`Any`/`Dict` 均已在本文件定义/导入，无需新增 import。）

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_contracts_extract.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add tests/test_crypto_contracts_extract.py src/utils/data_processing.py
git commit -m "feat: crypto_contracts 报告抽取器（嵌套 enhanced_context，presence-only）"
```

---

### Task 2: API schema 字段 + 接线 `_build_analysis_report`（site 1184）

**Files:**
- Modify: `api/v1/schemas/history.py:244`（`ReportDetails` 新增字段）
- Modify: `api/v1/endpoints/analysis.py:79-83`（import）、`:1183-1193`（接线 + 守卫）
- Test: `tests/test_analysis_api_contract.py`（新增一个 test method，模仿既有 `test_*_belong_boards` 风格）

- [ ] **Step 1: 写失败测试**

在 `tests/test_analysis_api_contract.py` 中（与 `_build_analysis_report` 既有用例同一个 TestCase 内，例如紧随 boards 用例之后）新增：

```python
    def test_build_analysis_report_surfaces_crypto_contracts(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={"meta": {}, "summary": {}, "strategy": {}, "details": {}},
            query_id="q1",
            stock_code="BTC/USDT",
            stock_name="BTC/USDT",
            context_snapshot={
                "enhanced_context": {
                    "crypto_contracts": {
                        "funding_rate": 0.0000059888,
                        "mark_price": 62669.5,
                        "open_interest": 2861888.58,
                        "open_interest_usd": 1793545573.08,
                        "source": "okx",
                    }
                }
            },
            fallback_fundamental_payload=None,
        )

        self.assertIsNotNone(report.details)
        self.assertEqual(report.details.crypto_contracts["funding_rate"], 0.0000059888)
        self.assertEqual(report.details.crypto_contracts["mark_price"], 62669.5)
        self.assertEqual(report.details.crypto_contracts["source"], "okx")

    def test_build_analysis_report_omits_crypto_contracts_when_absent(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={"meta": {}, "summary": {}, "strategy": {}, "details": {}},
            query_id="q1",
            stock_code="600519",
            stock_name="贵州茅台",
            context_snapshot={"enhanced_context": {"code": "600519"}},
            fallback_fundamental_payload=None,
        )

        # details 仍因 context_snapshot 非空而构建，但 crypto_contracts 为 None（presence-only）
        self.assertIsNone(report.details.crypto_contracts)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_analysis_api_contract.py -q -k crypto_contracts`
Expected: FAIL（`AttributeError: 'ReportDetails' object has no attribute 'crypto_contracts'` 或 Pydantic 未知字段）

- [ ] **Step 3a: ReportDetails 新增字段**

`api/v1/schemas/history.py`，在 `ReportDetails`（:244）末尾字段（`sector_rankings` 之后）追加：

```python
    crypto_contracts: Optional[Any] = Field(None, description="加密永续合约指标（presence-only：资金费率/标记价/未平仓量/来源）")
```

（`Optional`/`Any`/`Field` 已在该文件导入。）

- [ ] **Step 3b: analysis.py import 新抽取器**

`api/v1/endpoints/analysis.py:79-83` 的 `from src.utils.data_processing import (` 块内追加一行：

```python
    extract_crypto_contracts_detail_fields,
```

- [ ] **Step 3c: 接线 site 1184 + 守卫**

`api/v1/endpoints/analysis.py`，在 `_build_analysis_report` 内 `extracted_boards = extract_board_detail_fields(...)`（:1175）之后、`details = None`（:1181）之前插入：

```python
    extracted_contracts = extract_crypto_contracts_detail_fields(context_snapshot)
```

把守卫条件（:1183）：

```python
    if details_data or any(extracted_fundamental.values()) or has_board_details or context_snapshot is not None or analysis_context_pack_overview is not None:
```

改为追加合约判定：

```python
    if details_data or any(extracted_fundamental.values()) or has_board_details or extracted_contracts.get("crypto_contracts") is not None or context_snapshot is not None or analysis_context_pack_overview is not None:
```

在 `ReportDetails(...)`（:1184-1193）参数末尾（`sector_rankings=...` 之后）追加：

```python
            crypto_contracts=extracted_contracts.get("crypto_contracts"),
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_analysis_api_contract.py -q -k crypto_contracts`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add api/v1/schemas/history.py api/v1/endpoints/analysis.py tests/test_analysis_api_contract.py
git commit -m "feat: ReportDetails.crypto_contracts 字段 + _build_analysis_report 接线（presence-only）"
```

---

### Task 3: 接线剩余两入口（analysis.py:965 + history.py:445）+ 全站点契约守卫

**Files:**
- Modify: `api/v1/endpoints/analysis.py:962-974`（site 965 接线 + 守卫）
- Modify: `api/v1/endpoints/history.py:45-48`（import）、`:436-454`（site 445 接线）
- Test: `tests/test_crypto_contracts_wiring.py`（新建：结构化守卫，确保每个 `ReportDetails(` 构造点都带 `crypto_contracts=`）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_crypto_contracts_wiring.py`：

```python
# -*- coding: utf-8 -*-
"""全站点契约守卫：每个 ReportDetails(...) 构造点都必须接 crypto_contracts=（防漏接历史/任务态入口）。"""
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FILES = [
    _ROOT / "api" / "v1" / "endpoints" / "analysis.py",
    _ROOT / "api" / "v1" / "endpoints" / "history.py",
]


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_report_details_site_wires_crypto_contracts(path):
    text = path.read_text(encoding="utf-8")
    construct_sites = text.count("ReportDetails(")          # 仅构造调用（class 定义在 schemas，不在本文件）
    wired = text.count("crypto_contracts=")
    assert construct_sites > 0, f"{path.name}: 预期存在 ReportDetails 构造点"
    assert wired >= construct_sites, (
        f"{path.name}: {construct_sites} 个 ReportDetails 构造点，但只有 {wired} 处接 crypto_contracts="
        "（漏接会导致该入口的报告永远不透出永续指标）"
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_contracts_wiring.py -q`
Expected: FAIL（`history.py` 0 处、`analysis.py` 仅 1 处 `crypto_contracts=`，少于构造点数）

- [ ] **Step 3a: analysis.py site 965 接线 + 守卫**

`api/v1/endpoints/analysis.py`，在 `extracted_boards = extract_board_detail_fields(...)`（:958-961）之后、`has_board_details = ...`（:962）之前插入：

```python
            extracted_contracts = extract_crypto_contracts_detail_fields(context_snapshot)
```

把守卫（:964）：

```python
            if any(extracted_fundamental.values()) or has_board_details or context_snapshot is not None or analysis_context_pack_overview is not None:
```

改为：

```python
            if any(extracted_fundamental.values()) or has_board_details or extracted_contracts.get("crypto_contracts") is not None or context_snapshot is not None or analysis_context_pack_overview is not None:
```

在该处 `ReportDetails(...)`（:965-974）参数末尾（`sector_rankings=...` 之后）追加：

```python
                    crypto_contracts=extracted_contracts.get("crypto_contracts"),
```

- [ ] **Step 3b: history.py import**

`api/v1/endpoints/history.py:45-48` 的 `from src.utils.data_processing import (` 块内追加：

```python
    extract_crypto_contracts_detail_fields,
```

- [ ] **Step 3c: history.py site 445 接线**

`api/v1/endpoints/history.py`，在 `extracted_boards = extract_board_detail_fields(...)`（:440-443）之后、`details = ReportDetails(`（:445）之前插入：

```python
        extracted_contracts = extract_crypto_contracts_detail_fields(result.get("context_snapshot"))
```

在该 `ReportDetails(...)`（:445-454）参数末尾（`sector_rankings=...` 之后）追加：

```python
            crypto_contracts=extracted_contracts.get("crypto_contracts"),
```

（注：此处用 `result.get("context_snapshot")` 作为抽取器入参，与同处 `extract_fundamental_detail_fields(context_snapshot=result.get("context_snapshot"))` 一致。）

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_contracts_wiring.py tests/test_analysis_api_contract.py -q`
Expected: PASS（wiring 2 passed + 既有契约用例全过）

并确认编译：
Run: `PYTHONPATH="$PWD" .venv/bin/python -m py_compile api/v1/endpoints/analysis.py api/v1/endpoints/history.py`
Expected: 无输出（成功）

- [ ] **Step 5: 提交**

```bash
git add api/v1/endpoints/analysis.py api/v1/endpoints/history.py tests/test_crypto_contracts_wiring.py
git commit -m "feat: crypto_contracts 接线 task-status 与历史重载入口 + 全站点契约守卫"
```

---

### Task 4: Web 类型 `CryptoContracts` + `ReportDetails.cryptoContracts`

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts`（`MarketIndicators` 接口附近新增 `CryptoContracts`；`ReportDetails` 接口 :268 追加字段）

- [ ] **Step 1: 新增 CryptoContracts 接口**

`apps/dsa-web/src/types/analysis.ts`，在 `MarketIndicators` 接口（:175-182）之后追加：

```typescript
export interface CryptoContracts {
  fundingRate?: number;       // 比率（非百分比），展示时 *100
  markPrice?: number;
  openInterest?: number;      // 张
  openInterestUsd?: number;
  source?: string;            // 'okx'
}
```

- [ ] **Step 2: ReportDetails 接口追加字段**

`apps/dsa-web/src/types/analysis.ts` 的 `ReportDetails` 接口（:268-277），在 `sectorRankings?: SectorRankings;` 之后追加：

```typescript
  cryptoContracts?: CryptoContracts;
```

- [ ] **Step 3: 类型校验**

Run（在 `/tmp` 无空格副本，见运行约定）：`npm run build`
Expected: tsc 编译通过（无类型错误）。若仅本地快速校验，可 `npx tsc --noEmit`。

- [ ] **Step 4: 提交**

```bash
git add apps/dsa-web/src/types/analysis.ts
git commit -m "feat: Web CryptoContracts 类型 + ReportDetails.cryptoContracts"
```

---

### Task 5: Web 卡片 `ReportCryptoMetrics` + 挂载 + 导出（TDD）

**Files:**
- Test: `apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx`（新建）
- Create: `apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx`
- Modify: `apps/dsa-web/src/components/report/ReportSummary.tsx`（挂载）
- Modify: `apps/dsa-web/src/components/report/index.ts`（导出）

- [ ] **Step 1: 写失败测试**

新建 `apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx`：

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { CryptoContracts } from '../../../types/analysis';
import { ReportCryptoMetrics } from '../ReportCryptoMetrics';

const FULL: CryptoContracts = {
  fundingRate: 0.0000059888,
  markPrice: 62669.5,
  openInterest: 2861888.58,
  openInterestUsd: 1793545573.08,
  source: 'okx',
};

describe('ReportCryptoMetrics', () => {
  it('renders funding rate / mark price / OI when present', () => {
    render(<ReportCryptoMetrics contracts={FULL} />);
    expect(screen.getByText(/0\.0006%/)).toBeInTheDocument();      // 0.0000059888*100 toFixed(4)
    expect(screen.getByText(/62669\.5/)).toBeInTheDocument();
    expect(screen.getByText(/OKX/i)).toBeInTheDocument();
  });

  it('omits a missing field (presence-only)', () => {
    render(<ReportCryptoMetrics contracts={{ markPrice: 100 }} />);
    expect(screen.getByText(/100/)).toBeInTheDocument();
    expect(screen.queryByText(/资金费率/)).not.toBeInTheDocument();
  });

  it('renders nothing when contracts is empty/undefined', () => {
    const { container } = render(<ReportCryptoMetrics contracts={undefined} />);
    expect(container).toBeEmptyDOMElement();
    const { container: c2 } = render(<ReportCryptoMetrics contracts={{}} />);
    expect(c2).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run（`apps/dsa-web` 下）：`npx vitest run src/components/report/__tests__/ReportCryptoMetrics.test.tsx`
Expected: FAIL（`Failed to resolve import '../ReportCryptoMetrics'`）

- [ ] **Step 3a: 实现组件**

新建 `apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx`：

```tsx
import type React from 'react';
import type { CryptoContracts, ReportLanguage } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportCryptoMetricsProps {
  contracts?: CryptoContracts;
  language?: ReportLanguage;
}

const TEXT = {
  zh: {
    eyebrow: '永续合约',
    title: '合约市场指标（来源 OKX）',
    fundingRate: '资金费率',
    fundingHint: '正=多头付费 / 负=空头付费（约 8h 结算）',
    markPrice: '标记价',
    openInterest: '未平仓量(OI)',
    source: '来源',
  },
  en: {
    eyebrow: 'Perpetual',
    title: 'Contract Market Metrics (via OKX)',
    fundingRate: 'Funding Rate',
    fundingHint: 'positive = longs pay / negative = shorts pay (~8h settlement)',
    markPrice: 'Mark Price',
    openInterest: 'Open Interest',
    source: 'Source',
  },
} as const;

/** crypto 永续合约指标卡片 - presence-only；无任何字段则不渲染。 */
export const ReportCryptoMetrics: React.FC<ReportCryptoMetricsProps> = ({ contracts, language = 'zh' }) => {
  const t = TEXT[normalizeReportLanguage(language)];
  if (!contracts) return null;

  const rows: Array<{ label: string; value: string; hint?: string }> = [];
  if (typeof contracts.fundingRate === 'number') {
    rows.push({ label: t.fundingRate, value: `${(contracts.fundingRate * 100).toFixed(4)}%`, hint: t.fundingHint });
  }
  if (typeof contracts.markPrice === 'number') {
    rows.push({ label: t.markPrice, value: String(contracts.markPrice) });
  }
  if (typeof contracts.openInterest === 'number') {
    const usd = typeof contracts.openInterestUsd === 'number'
      ? ` / $${contracts.openInterestUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
      : '';
    rows.push({ label: t.openInterest, value: `${contracts.openInterest.toLocaleString('en-US')} 张${usd}` });
  }
  if (contracts.source) {
    rows.push({ label: t.source, value: contracts.source.toUpperCase() });
  }
  if (rows.length === 0) return null;

  return (
    <Card variant="bordered" padding="md" className="home-panel-card text-left">
      <DashboardPanelHeader eyebrow={t.eyebrow} title={t.title} className="mb-3" />
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-muted-text">{row.label}</span>
            <span className="text-right font-mono text-foreground">
              {row.value}
              {row.hint ? <span className="ml-2 block text-xs text-muted-text sm:inline">{row.hint}</span> : null}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
};
```

> 实现者注：`Card` / `DashboardPanelHeader` / `normalizeReportLanguage` 的导入路径与 `ReportDetails.tsx`（同目录）一致；若实际导出名/路径不同，以同目录既有组件为准对齐，勿新造样式系统。

- [ ] **Step 3b: 挂载到 ReportSummary**

`apps/dsa-web/src/components/report/ReportSummary.tsx`：顶部 import 追加：

```tsx
import { ReportCryptoMetrics } from './ReportCryptoMetrics';
```

在 `<ReportStrategy ... />`（:69）之后、`<ReportNews ... />`（:72）之前插入：

```tsx
      <ReportCryptoMetrics contracts={details?.cryptoContracts} language={reportLanguage} />
```

> 实现者注：确认 `ReportSummary` 内 `details` 与 `reportLanguage` 变量名（见 :88 `<ReportDetails details={details} ... language={reportLanguage} />`）。若命名不同，按实际作用域变量对齐。

- [ ] **Step 3c: 导出**

`apps/dsa-web/src/components/report/index.ts` 追加：

```typescript
export * from './ReportCryptoMetrics';
```

- [ ] **Step 4: 运行确认通过**

Run（`apps/dsa-web` 下）：`npx vitest run src/components/report/__tests__/ReportCryptoMetrics.test.tsx`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/components/report/ReportCryptoMetrics.tsx \
        apps/dsa-web/src/components/report/__tests__/ReportCryptoMetrics.test.tsx \
        apps/dsa-web/src/components/report/ReportSummary.tsx \
        apps/dsa-web/src/components/report/index.ts
git commit -m "feat: ReportCryptoMetrics 永续指标卡片 + 挂载 ReportSummary（presence-only）"
```

---

### Task 6: 文档

**Files:**
- Modify: `docs/crypto-guide.md`（永续节补"报告透出"）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

- [ ] **Step 1: crypto-guide 永续节追加**

在 `docs/crypto-guide.md` 的「加密永续合约指标」节末尾追加小节：

```markdown
### 报告透出

永续指标除注入分析 prompt 外，也会 presence-only 透出到结构化报告：
- API：`AnalysisReport.details.crypto_contracts`（字段 `funding_rate` / `mark_price` / `open_interest` / `open_interest_usd` / `source`，缺省省略）。
- Web：分析报告页「合约市场指标（来源 OKX）」卡片（`ReportCryptoMetrics`），无数据则整卡不渲染。
- 依赖 `SAVE_CONTEXT_SNAPSHOT=true`（默认）；关闭快照持久化时不透出。与 `CRYPTO_DERIVATIVES_ENABLED` 同步——上游关闭则无数据可透出。
```

- [ ] **Step 2: CHANGELOG 追加（扁平格式，禁止新增类目标题）**

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段追加一行：

```markdown
- [新功能] crypto 永续合约指标透出到分析报告 API（`ReportDetails.crypto_contracts`）与 Web 卡片（presence-only，additive）
```

- [ ] **Step 3: 核对引用**

Run: `grep -n "crypto_contracts\|ReportCryptoMetrics\|合约市场指标" docs/crypto-guide.md docs/CHANGELOG.md`
Expected: 命中新增内容；确认字段名/组件名与代码一致。

- [ ] **Step 4: 提交**

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: crypto 永续指标报告透出说明 + CHANGELOG"
```

---

### Task 7: 全量回归

**Files:** 无（仅验证）

- [ ] **Step 1: 后端 ci_gate**

Run: `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh all`
Expected: `backend-gate: all checks passed`，0 失败（新增用例计入总数）。

- [ ] **Step 2: 前端 lint + build（/tmp 无空格副本，web-gate 等价）**

按仓库记忆 [[workspace-path-space-npm]]：rsync 到 `/tmp` 无空格副本后：
Run: `npm ci && npm run lint && npm run build`
Expected: eslint 0 error、tsc/vite build 成功。（仅 vitest 不等价于 web-gate，须真跑 `npm run lint`。）

- [ ] **Step 3: 交付自检**

确认：三入口都接线（wiring 守卫通过）；presence-only（缺省不渲染/字段 None）；stock/hk/us 报告零变化；无新配置项；中英双语卡片文案；文档与代码字段名一致。

- [ ] **Step 4: 收尾**

调用 `superpowers:finishing-a-development-branch` 呈现 keep-local/merge/PR/discard 选项（默认沿用本地分支栈，不推送）。

---

## Self-Review

**1. Spec coverage（逐节核对）：**
- §2.1 抽取器 → Task 1 ✅
- §2.2 ReportDetails 字段 → Task 2 Step 3a ✅
- §2.3 三接点接线（1184/965/445）→ Task 2（1184）+ Task 3（965/445）+ 守卫测试 ✅
- §2.4 Web 类型 → Task 4 ✅
- §2.5 卡片 + 挂载 ReportSummary + 导出 → Task 5 ✅
- §4 兼容/`save_context_snapshot` 边界 → Task 6 文档写明 + Task 2 omit 用例 ✅
- §5 测试（嵌套真形 + JSON 字符串 + 三层）→ Task 1/2/5 ✅
- §6 文档 → Task 6 ✅
- §7 分支回滚 → Task 7 Step 4 ✅

**2. Placeholder scan：** 无 TBD/TODO；每个代码步均给出完整代码与确切命令/预期。组件中 `Card`/`DashboardPanelHeader`/`normalizeReportLanguage` 的导入对齐既有 `ReportDetails.tsx`（已标注实现者注）。

**3. Type consistency：** Python 字段 snake_case（`funding_rate`/`mark_price`/`open_interest`/`open_interest_usd`/`source`）贯穿 extractor↔schema↔test；Web camelCase（`fundingRate`/`markPrice`/`openInterest`/`openInterestUsd`/`source`）经 `toCamelCase(deep)` 一一对应；`extract_crypto_contracts_detail_fields` 返回 `{"crypto_contracts": ...}`，三接点统一 `.get("crypto_contracts")`；组件 prop 名 `contracts`（test 与挂载一致）。
