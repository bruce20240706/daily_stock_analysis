# K 线可视化 + 量价信号引擎 + 图上买卖标注 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 A股/港股通/加密永续补齐可交互 K 线(klinecharts)、确定性量价信号引擎、图上规则+LLM 双轨买卖标注、数值化买卖价位线、以及复用回测的信号命中率实证。

**Architecture:** 前端 KLineDrawer(lazy klinecharts) 并行消费 GET /history(OHLCV) 与 NEW GET /signals(markers+price_lines+consistency)；后端新增 volume_price_signals 引擎(复用 alert_indicators.normalize_ohlcv 与单一 swing pivot 基元)、/signals 端点、价位反算器(写 price_position 挂现有 stabilize_decision_with_structure 护栏)、命中率回填(复用 BacktestResult)；不造平行决策链/第二套量能口径。两条数据同源同复权——同源由构造保证（`/signals` 复用与 `/history` 同一 `get_history_data` 取数路径）；复权一致性检测非本期范围，detection 留后续。

**Tech Stack:** Python/FastAPI/Pydantic/pandas(后端)；React/TypeScript/Vite/klinecharts@^9.8.12(前端)；pytest + vitest/RTL。

**关联 spec:** docs/superpowers/specs/2026-06-15-kline-signals-design.md

**里程碑顺序与依赖:** M0(前端渲染地基) → M1(量价引擎) → M2a(信号契约+端点) → M2b(价位反算器挂护栏) → M2c(命中率回填) → M2d(前端双轨标注)。

---

## M0 · K 线渲染地基

本里程碑只做"出图"：前端引入 `klinecharts@^9.8.12`，在一个 lazy 重面板里渲染日线蜡烛 + 成交量副图（红涨绿跌可切）+ 十字光标/缩放；新增 `KLine` 类型、`getKlineHistory` client 与 `mapKLineDataToKLine` 映射 util；新建 `KLineDrawer`（Drawer 壳 + lazy + ErrorBoundary，仿 `ReportMarkdownDrawer`），从 `HomePage` 的 `StockBar` 单只个股接一个入口；后端 `/history` 端点 `days` 默认 30→120（仅该端点 Query，不动任何 365 常量）。**本里程碑不引入任何 marker / signal**——`SignalMarker` 类型、`getSignals`、双轨标注全部留给 M2a/M2d。

完成后系统行为变化：个股栏每只股票多一个"K线"按钮，点击侧滑出 K 线抽屉；`/history` 默认回看天数变长。`/signals` 尚不存在，抽屉只画图无标注，符合 spec 6 的"有图无标注"降级前提。

### File Structure（本段涉及）

```
api/v1/endpoints/stocks.py                         (Modify: /history days Query 默认 30→120)
tests/test_stock_history_days.py                   (Create: /history days 默认值/上限回归)

apps/dsa-web/package.json                          (Modify: 加 klinecharts@^9.8.12)
apps/dsa-web/vite.config.ts                        (Modify: vendorChunkByPackage 加 'klinecharts')
apps/dsa-web/src/types/kline.ts                    (Create: KLine 类型)
apps/dsa-web/src/api/stocks.ts                     (Modify: getKlineHistory + mapKLineDataToKLine)
apps/dsa-web/src/api/__tests__/stocks.test.ts      (Create: mapKLineDataToKLine 三市场日期映射)
apps/dsa-web/src/components/kline/KLineChartPanel.tsx   (Create: lazy 重面板, klinecharts 渲染)
apps/dsa-web/src/components/kline/KLineDrawer.tsx       (Create: Drawer 壳 + lazy + ErrorBoundary)
apps/dsa-web/src/components/kline/index.ts              (Create: barrel)
apps/dsa-web/src/components/kline/__tests__/KLineDrawer.test.tsx  (Create: 渲染/降级/lazy 测试)
apps/dsa-web/src/components/history/StockBarItem.tsx    (Modify: onViewKline 入口按钮)
apps/dsa-web/src/components/history/__tests__/StockBarItem.test.tsx (Modify: 入口按钮测试)
apps/dsa-web/src/pages/HomePage.tsx                (Modify: 渲染 KLineDrawer + 状态接线)

.env.example                                       (Modify: 注释 /history 默认 days)
docs/CHANGELOG.md                                  (Modify: [Unreleased] 追加)
docs/kline-visualization.md                        (Create: M0 行为/入口/降级说明)
```

约定（贯穿本段，跨里程碑统一接口契约）：
- 前端类型放 `apps/dsa-web/src/types/kline.ts`，**不堆进 `types/analysis.ts`**。
- client / util 放 `apps/dsa-web/src/api/stocks.ts`，**不复用 `api/history.ts`**。
- 组件目录 `apps/dsa-web/src/components/kline/`，**不撞名 `StockHistoryTrendDrawer`**。
- 前端测试命令统一在工作区路径有空格的环境下用 `npm --prefix` 形式直接跑（vitest 不受路径空格影响）；`npm run lint && npm run build` 的全套验证按 MEMORY 约定可在 `/tmp` 无空格副本跑，本段每个前端 task 的 ④ 给出 `vitest run <file>` 精确命令。
- 后端测试用 `.venv/bin/python`（venv 在 `.venv/bin/python`）。

---

### Task 0.1 · 后端 `/history` 端点 days 默认 30→120（仅该端点 Query）

**Files**
- Modify: `api/v1/endpoints/stocks.py:491`（`days: int = Query(30, ge=1, le=365, ...)` → `Query(120, ...)`）
- Test: `tests/test_stock_history_days.py`（Create）

**绝对禁止**：不得改 `src/services/alert_indicators.py:24` 的 `MAX_REQUESTED_DAYS=365`、`data_tools._DAILY_HISTORY_MAX_DAYS`、config_registry、前端 AlertRuleForm 的任何 365；仅改该端点 Query。`tests/test_alert_worker.py:60` 的 `"at most 365 days"` 断言必须仍通过。

**步骤**

① 写失败测试 —— 新建 `tests/test_stock_history_days.py`：

```python
"""/history 端点 days Query 契约回归。

M0 只放宽该端点 Query 的默认值（30→120），上限保持 365、下限保持 1；
mock StockService，断言 handler 真正收到的 days 值（默认/显式/越界拒绝），
不依赖真实网络或 DB。同时锁定：放宽仅作用于本端点，不触碰 alert 取数上限。
"""
import pytest
from fastapi.testclient import TestClient

from api.app import app
import api.v1.endpoints.stocks as stocks_ep

client = TestClient(app)


class _CapturingStockService:
    """记录 handler 透传给 service 的 days，返回最小合法结构。"""

    last_days = None

    def get_history_data(self, stock_code, period="daily", days=30):
        _CapturingStockService.last_days = days
        return {"stock_code": stock_code, "stock_name": None, "data": []}


@pytest.fixture
def mock_stock_service(monkeypatch):
    _CapturingStockService.last_days = None
    monkeypatch.setattr(stocks_ep, "StockService", lambda *a, **k: _CapturingStockService())


def test_history_days_default_is_120(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 120


def test_history_days_explicit_is_passed_through(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=45")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 45


def test_history_days_upper_bound_still_365(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=365")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 365


def test_history_days_above_365_rejected(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=366")
    assert resp.status_code == 422


def test_history_days_below_one_rejected(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=0")
    assert resp.status_code == 422
```

② 跑命令验证失败：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_stock_history_days.py -q
```

预期输出：`test_history_days_default_is_120` FAIL（`assert 30 == 120`），其余 4 条 PASS。结尾类似 `1 failed, 4 passed`。

③ 写最小实现 —— 编辑 `api/v1/endpoints/stocks.py:491`，只改默认值：

```python
def get_stock_history(
    stock_code: str,
    period: str = Query("daily", description="K 线周期", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(
        120,
        ge=1,
        le=365,
        description="获取天数（日历回看天数；K 线抽屉默认 120，上限保守保持 365）",
    )
) -> StockHistoryResponse:
```

④ 跑命令验证通过：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_stock_history_days.py tests/test_alert_worker.py -q
```

预期输出：全部 PASS（含 `test_alert_worker.py` 的 `"at most 365 days"` 断言），结尾 `N passed`，0 failed。再跑 `.venv/bin/python -m py_compile api/v1/endpoints/stocks.py` 无输出（成功）。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git checkout -b feat/m0-kline-rendering 2>/dev/null || git checkout feat/m0-kline-rendering && \
git add api/v1/endpoints/stocks.py tests/test_stock_history_days.py && \
git commit -m "feat: /history 端点 days 默认放宽至 120（仅该端点 Query）"
```

---

### Task 0.2 · `KLine` 类型与 `mapKLineDataToKLine` 三市场日期映射

**Files**
- Create: `apps/dsa-web/src/types/kline.ts`（`KLine` 类型）
- Modify: `apps/dsa-web/src/api/stocks.ts`（加 `KLineDataRaw` 类型 + `mapKLineDataToKLine` util）
- Test: `apps/dsa-web/src/api/__tests__/stocks.test.ts`（Create）

**契约**：`KLine{timestamp:number, open, high, low, close, volume, turnover:number}`；`mapKLineDataToKLine(raw)` 把后端 `KLineData`（`date 'YYYY-MM-DD'`、`amount`、`volume?` 可空、`change_percent`）映射为 `KLine`：`date → epoch ms 按 Asia/Shanghai`、`amount → turnover`、`volume ?? 0`、`amount ?? 0`。`SignalMarker` 类型本里程碑不引入（留 M2a）。

**Asia/Shanghai 锚定实现说明**：`'YYYY-MM-DD'` 无时区，UTC+8 当日 00:00 即 `Date.parse('${date}T00:00:00+08:00')`。直接拼固定偏移字符串而非依赖运行环境 TZ，保证三市场（A股/港股/美股 date 字符串）统一锚 Shanghai，与 `format.ts` 约定一致。

**步骤**

① 写失败测试 —— 新建 `apps/dsa-web/src/types/kline.ts`（先写类型，否则测试无法 import；类型本身即"实现"，但映射函数尚不存在 → 测试在 `mapKLineDataToKLine` 处失败）。先建类型文件：

```typescript
// apps/dsa-web/src/types/kline.ts
/**
 * 前端 K 线渲染域类型。独立于分析记录域（types/analysis.ts），
 * 跨里程碑统一接口契约：KLine 供 klinecharts 适配，SignalMarker 在 M2a 引入。
 */

/** 单根 K 线（klinecharts applyNewData 入参形状的超集）。 */
export interface KLine {
  /** epoch ms，唯一权威时间锚（按 Asia/Shanghai 解析后端 date）。 */
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手/张），后端缺失时归零。 */
  volume: number;
  /** 成交额（元），来自后端 amount，缺失时归零。 */
  turnover: number;
}
```

新建测试 `apps/dsa-web/src/api/__tests__/stocks.test.ts`：

```typescript
import { describe, expect, it } from 'vitest';
import { mapKLineDataToKLine } from '../stocks';
import type { KLine } from '../../types/kline';

/** UTC+8 当日 00:00 的 epoch ms。 */
const shanghaiMidnightMs = (date: string): number =>
  Date.parse(`${date}T00:00:00+08:00`);

describe('mapKLineDataToKLine', () => {
  it('maps A-share daily bar with amount->turnover and Asia/Shanghai timestamp', () => {
    const result: KLine = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: 12345,
      amount: 678900,
      change_percent: 1.2,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-01-02'));
    expect(result.open).toBe(10);
    expect(result.high).toBe(11);
    expect(result.low).toBe(9.5);
    expect(result.close).toBe(10.5);
    expect(result.volume).toBe(12345);
    expect(result.turnover).toBe(678900);
  });

  it('maps HK bar identically (date string anchored to Asia/Shanghai, not local TZ)', () => {
    const result = mapKLineDataToKLine({
      date: '2026-03-16',
      open: 300,
      high: 305,
      low: 298,
      close: 302,
      volume: 1000,
      amount: 302000,
      change_percent: -0.5,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-03-16'));
    expect(result.timestamp).toBe(Date.UTC(2026, 2, 15, 16, 0, 0)); // 2026-03-16 00:00 +08:00
  });

  it('maps US bar date string to the same Asia/Shanghai anchor as A/HK', () => {
    const result = mapKLineDataToKLine({
      date: '2026-06-15',
      open: 150,
      high: 152,
      low: 149,
      close: 151,
      volume: 5000,
      amount: 755000,
      change_percent: 0.7,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-06-15'));
  });

  it('defaults missing volume and amount to zero', () => {
    const result = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: null,
      amount: null,
      change_percent: null,
    });

    expect(result.volume).toBe(0);
    expect(result.turnover).toBe(0);
  });
});
```

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/api/__tests__/stocks.test.ts
```

预期输出：导入/类型错误 —— `mapKLineDataToKLine` 未从 `../stocks` 导出（vitest 报 `No "mapKLineDataToKLine" export is defined` 或 transform error），4 条全 FAIL。

③ 写最小实现 —— 在 `apps/dsa-web/src/api/stocks.ts` 顶部 import 与文件内追加（接现有 `import apiClient from './index';` 之后加 `import type`，并在 `stocksApi` 定义之外导出 util）：

```typescript
import type { KLine } from '../types/kline';
```

在文件末尾（`stocksApi` 之后）追加：

```typescript
/** 后端 /history 返回的单根 K 线原始形状（snake_case，date 为 'YYYY-MM-DD'）。 */
export type KLineDataRaw = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
  change_percent?: number | null;
};

/**
 * 把后端 KLineData 映射为 klinecharts 所需的 KLine。
 *
 * - date 'YYYY-MM-DD'（无时区）统一锚 Asia/Shanghai 当日 00:00 → epoch ms，
 *   三市场（A股/港股/美股）一致，与 utils/format.ts 的 Asia/Shanghai 约定对齐；
 *   拼固定 +08:00 偏移而非依赖运行环境 TZ。
 * - amount → turnover；volume/amount 缺失归零（klinecharts 量副图需数值）。
 */
export const mapKLineDataToKLine = (raw: KLineDataRaw): KLine => ({
  timestamp: Date.parse(`${raw.date}T00:00:00+08:00`),
  open: raw.open,
  high: raw.high,
  low: raw.low,
  close: raw.close,
  volume: raw.volume ?? 0,
  turnover: raw.amount ?? 0,
});
```

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/api/__tests__/stocks.test.ts
```

预期输出：`4 passed`，0 failed（`Test Files 1 passed`）。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/stocks.test.ts && \
git commit -m "feat: 新增 KLine 类型与 mapKLineDataToKLine 三市场日期映射"
```

---

### Task 0.3 · `getKlineHistory` client

**Files**
- Modify: `apps/dsa-web/src/api/stocks.ts`（`stocksApi` 加 `getKlineHistory`）
- Test: `apps/dsa-web/src/api/__tests__/stocks.test.ts`（追加 describe）

**契约**：`getKlineHistory(code: string, days?: number): Promise<KLine[]>`，打 `GET /api/v1/stocks/{code}/history`（与 spec 4 同源，**不复用 history.ts**）；`code` 经 `encodeURIComponent`（兼容 `BTC/USDT` 这类带 `/` 的 crypto code，对齐后端 `:path` 路由 `tests/test_crypto_api_routes.py` 约定）；按 `timestamp` 升序排序（klinecharts 要求升序，后端 normalize 已升序，这里做防御性排序）；逐根经 `mapKLineDataToKLine`。

**步骤**

① 写失败测试 —— 在 `apps/dsa-web/src/api/__tests__/stocks.test.ts` 顶部把现有静态 import 调整为 mock 形态，并追加 `getKlineHistory` 测试。**完整替换该测试文件**为（含 Task 0.2 的 4 条映射测试 + 新 client 测试，统一在文件头 mock `../index`）：

```typescript
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { KLine } from '../../types/kline';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { get, post },
}));

// 在 mock 之后再导入被测模块（其内部 import '../index' 命中上面的 mock）。
const { mapKLineDataToKLine, stocksApi } = await import('../stocks');

/** UTC+8 当日 00:00 的 epoch ms。 */
const shanghaiMidnightMs = (date: string): number =>
  Date.parse(`${date}T00:00:00+08:00`);

describe('mapKLineDataToKLine', () => {
  it('maps A-share daily bar with amount->turnover and Asia/Shanghai timestamp', () => {
    const result: KLine = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: 12345,
      amount: 678900,
      change_percent: 1.2,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-01-02'));
    expect(result.open).toBe(10);
    expect(result.high).toBe(11);
    expect(result.low).toBe(9.5);
    expect(result.close).toBe(10.5);
    expect(result.volume).toBe(12345);
    expect(result.turnover).toBe(678900);
  });

  it('maps HK bar identically (date string anchored to Asia/Shanghai, not local TZ)', () => {
    const result = mapKLineDataToKLine({
      date: '2026-03-16',
      open: 300,
      high: 305,
      low: 298,
      close: 302,
      volume: 1000,
      amount: 302000,
      change_percent: -0.5,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-03-16'));
    expect(result.timestamp).toBe(Date.UTC(2026, 2, 15, 16, 0, 0));
  });

  it('maps US bar date string to the same Asia/Shanghai anchor as A/HK', () => {
    const result = mapKLineDataToKLine({
      date: '2026-06-15',
      open: 150,
      high: 152,
      low: 149,
      close: 151,
      volume: 5000,
      amount: 755000,
      change_percent: 0.7,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-06-15'));
  });

  it('defaults missing volume and amount to zero', () => {
    const result = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: null,
      amount: null,
      change_percent: null,
    });

    expect(result.volume).toBe(0);
    expect(result.turnover).toBe(0);
  });
});

describe('stocksApi.getKlineHistory', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('requests /history with days and maps rows to KLine[]', async () => {
    get.mockResolvedValueOnce({
      data: {
        stock_code: '600519',
        stock_name: '贵州茅台',
        period: 'daily',
        data: [
          { date: '2026-01-02', open: 10, high: 11, low: 9.5, close: 10.5, volume: 100, amount: 1000, change_percent: 1.2 },
          { date: '2026-01-03', open: 10.5, high: 12, low: 10.4, close: 11.8, volume: 200, amount: 2300, change_percent: 12.3 },
        ],
      },
    });

    const result = await stocksApi.getKlineHistory('600519', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/600519/history', { params: { days: 120 } });
    expect(result).toHaveLength(2);
    expect(result[0].timestamp).toBe(shanghaiMidnightMs('2026-01-02'));
    expect(result[0].turnover).toBe(1000);
    expect(result[1].close).toBe(11.8);
  });

  it('defaults days to 120 and encodes crypto codes containing slash', async () => {
    get.mockResolvedValueOnce({ data: { data: [] } });

    await stocksApi.getKlineHistory('BTC/USDT');

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/history', { params: { days: 120 } });
  });

  it('sorts returned bars ascending by timestamp', async () => {
    get.mockResolvedValueOnce({
      data: {
        data: [
          { date: '2026-01-05', open: 1, high: 1, low: 1, close: 1, volume: 1, amount: 1, change_percent: 0 },
          { date: '2026-01-02', open: 1, high: 1, low: 1, close: 1, volume: 1, amount: 1, change_percent: 0 },
        ],
      },
    });

    const result = await stocksApi.getKlineHistory('600519');

    expect(result.map((bar) => bar.timestamp)).toEqual([
      shanghaiMidnightMs('2026-01-02'),
      shanghaiMidnightMs('2026-01-05'),
    ]);
  });

  it('returns an empty array when backend payload has no data', async () => {
    get.mockResolvedValueOnce({ data: {} });

    const result = await stocksApi.getKlineHistory('600519');

    expect(result).toEqual([]);
  });
});
```

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/api/__tests__/stocks.test.ts
```

预期输出：`mapKLineDataToKLine` 4 条 PASS；`stocksApi.getKlineHistory` 4 条 FAIL（`stocksApi.getKlineHistory is not a function`）。结尾类似 `4 failed, 4 passed`。

③ 写最小实现 —— 在 `apps/dsa-web/src/api/stocks.ts` 的 `stocksApi` 对象内（`parseImport` 之后）追加方法，常量定义在 `stocksApi` 之前：

```typescript
/** K 线抽屉默认回看天数，与后端 /history 端点 Query 默认对齐。 */
export const KLINE_DEFAULT_DAYS = 120;
```

在 `stocksApi` 对象内追加：

```typescript
  /**
   * 拉取日线 K 线，映射为 klinecharts 所需的 KLine[]。
   * 与 /signals 同源同 days（M0 暂只用 /history）；不复用 api/history.ts（分析记录域）。
   * code 经 encodeURIComponent 以兼容带 '/' 的 crypto 代码（后端 {code:path} 路由）。
   */
  async getKlineHistory(code: string, days: number = KLINE_DEFAULT_DAYS): Promise<KLine[]> {
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { days } },
    );
    const data = response.data as { data?: KLineDataRaw[] };
    const rows = data.data ?? [];
    return rows
      .map(mapKLineDataToKLine)
      .sort((a, b) => a.timestamp - b.timestamp);
  },
```

注意：`stocksApi` 当前各方法用 `apiClient.post`，`apiClient` 已 import。`getKlineHistory` 引用同文件内的 `mapKLineDataToKLine` 与 `KLineDataRaw`（Task 0.2 已定义于本文件），无循环依赖。

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/api/__tests__/stocks.test.ts
```

预期输出：`8 passed`，0 failed。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/stocks.test.ts && \
git commit -m "feat: stocks.ts 新增 getKlineHistory 拉取并映射日线 K 线"
```

---

### Task 0.4 · 引入 klinecharts 依赖与 vite 分包

> **非 TDD：依赖/构建配置 task，以命令探针为红绿**——无单元测试，红=安装前 `npm ls klinecharts` 报缺失/非 0 退出，绿=安装后 `npm ls klinecharts` 显示 `klinecharts@9.8.x` 且 `npm run build` 通过。

**Files**
- Modify: `apps/dsa-web/package.json`（`dependencies` 加 `"klinecharts": "^9.8.12"`）
- Modify: `apps/dsa-web/vite.config.ts`（`vendorChunkByPackage` 加 `'klinecharts': 'vendor-klinecharts'`）

**约束**：精确 caret `^9.8.12`，**禁 `@latest`/`10.x`**（当前 latest=`10.0.0-beta3` 预发布）；与 recharts 的 `vendor-charts` 区分，单独 `vendor-klinecharts`；**仅供 lazy 重面板内 import**，本 task 不写任何 import（同步路径零引用，保证 M0 不影响首屏）。

**步骤**

① 写失败测试 —— 本 task 为依赖/构建配置，验证点是"装得上 + 分包名生效 + 同步 bundle 不含 klinecharts"。先写一个分包配置断言（纯函数 `getVendorChunkName` 在 vite.config 内未导出，故改为对 `vite.config.ts` 文本断言 + 安装后 lock 校验）。新建临时校验脚本无收益，改用既有验证矩阵：本 task 的"失败测试"= 安装前 `npm ls klinecharts` 报缺失。运行：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" ls klinecharts
```

预期输出（失败态）：`(empty)` 或 `npm error code ELSPROBLEMS` / `missing: klinecharts`，退出码非 0。

② 写实现 —— 编辑 `apps/dsa-web/package.json`，在 `dependencies` 内 `"clsx"` 之后插入（保持字母序大致一致即可）：

```json
    "clsx": "^2.1.1",
    "klinecharts": "^9.8.12",
    "lucide-react": "^0.555.0",
```

编辑 `apps/dsa-web/vite.config.ts`，在 `vendorChunkByPackage` 对象内 `recharts: 'vendor-charts',` 之后插入：

```typescript
  recharts: 'vendor-charts',
  klinecharts: 'vendor-klinecharts',
```

安装依赖（工作区路径含空格，`npm --prefix` 安装通常可行；若遇 MEMORY 记录的"路径空格致 npm ci 残缺"问题，则按 ④ 在 `/tmp` 无空格副本验证）：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" install klinecharts@^9.8.12 --save-exact=false
```

③ 验证安装版本在 9.x：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" ls klinecharts
```

预期输出：`klinecharts@9.8.x`（9.8.12 或其 patch），**绝不出现 10.x / beta**。

④ 验证分包 + 首屏不受影响 —— 由于工作区路径含空格会导致 `npm ci`/`build` 不稳定，按 MEMORY 约定在 `/tmp` 无空格副本跑全套构建，确认产物有独立 `vendor-klinecharts` chunk 且入口/首屏 chunk 不含 klinecharts（本 task 尚无 import，故 build 后 `vendor-klinecharts` 可能不生成——这是预期：只有 Task 0.6 lazy import 后才会产出该 chunk）。本 task 只断言 build 仍通过：

```bash
rsync -a --delete "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-build/ --exclude node_modules --exclude dist && \
cp "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/package-lock.json" /tmp/dsa-web-build/ 2>/dev/null; \
npm --prefix /tmp/dsa-web-build ci && \
npm --prefix /tmp/dsa-web-build run build 2>&1 | tail -20
```

预期输出：`tsc -b` 无类型错误；`vite build` 成功输出 `✓ built in ...`，无 `vendor-klinecharts`（尚无 import，符合预期），首屏 chunk 体积无显著变化。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/package.json apps/dsa-web/package-lock.json apps/dsa-web/vite.config.ts && \
git commit -m "feat: 引入 klinecharts@^9.8.12 并配置 vendor-klinecharts 分包"
```

---

### Task 0.5 · `KLineChartPanel` 重面板（klinecharts 渲染蜡烛 + 量副图 + 红涨绿跌可切）

**Files**
- Create: `apps/dsa-web/src/components/kline/KLineChartPanel.tsx`
- Test: `apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.test.tsx`（Create）

**职责**：被 `KLineDrawer` lazy import 的重面板（唯一 `import 'klinecharts'` 处）。挂载时 `getKlineHistory(stockCode, days)` 拉数据 → `init` 图表 → `applyNewData(klines)`；展示蜡烛 + 成交量副图；十字光标/缩放为 klinecharts 9.8 默认行为；红涨绿跌通过 `setStyles({ candle: { bar: { upColor, downColor, ... } } })` 配置，提供"红涨绿跌 / 绿涨红跌"切换按钮（默认中式红涨绿跌）。加载/空/错误三态。卸载 `dispose`。**props 与 `getSignals` 无关**（本里程碑无标注）。

**props**：`{ stockCode: string; market?: string; days?: number }`（`stockName` 由 Drawer 标题层用，不传入面板）。

**测试策略**：klinecharts 直接操作真实 canvas，在 jsdom 下不可靠，故测试 **mock `klinecharts`**（断言 `init`/`applyNewData`/`dispose`/`setStyles` 被以正确数据调用），并 mock `stocksApi.getKlineHistory`（断言加载态→数据态、颜色切换触发 `setStyles`、错误态文案）。这与 spec 7"抽屉渲染组件测试"一致，且不把真实风险层 mock 掉关键契约（数据映射已在 Task 0.2/0.3 真测）。

**步骤**

① 写失败测试 —— 新建 `apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.test.tsx`：

```typescript
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { KLine } from '../../../types/kline';

const init = vi.hoisted(() => vi.fn());
const dispose = vi.hoisted(() => vi.fn());
const applyNewData = vi.hoisted(() => vi.fn());
const setStyles = vi.hoisted(() => vi.fn());
const createIndicator = vi.hoisted(() => vi.fn());
const getKlineHistory = vi.hoisted(() => vi.fn());

vi.mock('klinecharts', () => ({
  init: (...args: unknown[]) => {
    init(...args);
    return { applyNewData, setStyles, createIndicator, dispose };
  },
  dispose: (...args: unknown[]) => dispose(...args),
}));

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    getKlineHistory: (...args: unknown[]) => getKlineHistory(...args),
  },
}));

const sampleKlines: KLine[] = [
  { timestamp: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 100, turnover: 1000 },
  { timestamp: 2, open: 10.5, high: 12, low: 10, close: 11.8, volume: 200, turnover: 2300 },
];

const renderPanel = async (props?: { market?: string; days?: number }) => {
  const { KLineChartPanel } = await import('../KLineChartPanel');
  render(<KLineChartPanel stockCode="600519" {...props} />);
};

describe('KLineChartPanel', () => {
  beforeEach(() => {
    init.mockReset();
    dispose.mockReset();
    applyNewData.mockReset();
    setStyles.mockReset();
    createIndicator.mockReset();
    getKlineHistory.mockReset();
  });

  afterEach(() => {
    vi.resetModules();
  });

  it('fetches with default days, inits the chart and applies fetched klines', async () => {
    getKlineHistory.mockResolvedValueOnce(sampleKlines);

    await renderPanel();

    await waitFor(() => expect(applyNewData).toHaveBeenCalledWith(sampleKlines));
    expect(getKlineHistory).toHaveBeenCalledWith('600519', 120);
    expect(init).toHaveBeenCalledTimes(1);
    expect(createIndicator).toHaveBeenCalledWith('VOL', false, expect.any(Object));
  });

  it('shows empty state when backend returns no bars', async () => {
    getKlineHistory.mockResolvedValueOnce([]);

    await renderPanel();

    expect(await screen.findByText('暂无 K 线数据')).toBeInTheDocument();
    expect(applyNewData).not.toHaveBeenCalled();
  });

  it('shows error state when the fetch fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    getKlineHistory.mockRejectedValueOnce(new Error('network down'));

    await renderPanel();

    expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('toggles up/down colors via setStyles when the color switch is clicked', async () => {
    getKlineHistory.mockResolvedValueOnce(sampleKlines);

    await renderPanel();

    await waitFor(() => expect(applyNewData).toHaveBeenCalled());
    setStyles.mockClear();

    fireEvent.click(screen.getByRole('button', { name: '切换涨跌颜色' }));

    expect(setStyles).toHaveBeenCalledTimes(1);
    const styleArg = setStyles.mock.calls[0][0] as { candle: { bar: { upColor: string } } };
    // 切到绿涨红跌后，up 应为绿色族（非默认红）。
    expect(styleArg.candle.bar.upColor).not.toBe('#ef4444');
  });
});
```

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/kline/__tests__/KLineChartPanel.test.tsx
```

预期输出：`Failed to resolve import "../KLineChartPanel"`，4 条全 FAIL。

③ 写最小实现 —— 新建 `apps/dsa-web/src/components/kline/KLineChartPanel.tsx`：

```typescript
import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { dispose, init } from 'klinecharts';
import type { Chart } from 'klinecharts';
import { stocksApi, KLINE_DEFAULT_DAYS } from '../../api/stocks';
import type { KLine } from '../../types/kline';

interface KLineChartPanelProps {
  stockCode: string;
  market?: string;
  days?: number;
}

type LoadState = 'loading' | 'ready' | 'empty' | 'error';

/** 中式红涨绿跌（默认）与绿涨红跌两套配色。 */
const UP_RED = '#ef4444';
const DOWN_GREEN = '#22c55e';

const buildCandleStyles = (upRedDownGreen: boolean) => {
  const upColor = upRedDownGreen ? UP_RED : DOWN_GREEN;
  const downColor = upRedDownGreen ? DOWN_GREEN : UP_RED;
  return {
    candle: {
      bar: {
        upColor,
        downColor,
        noChangeColor: '#888888',
        upBorderColor: upColor,
        downBorderColor: downColor,
        noChangeBorderColor: '#888888',
        upWickColor: upColor,
        downWickColor: downColor,
        noChangeWickColor: '#888888',
      },
    },
  };
};

/**
 * klinecharts 重面板：唯一 import 'klinecharts' 处（仅经 KLineDrawer lazy 加载，
 * 不进同步路径，保证首屏不受影响）。渲染蜡烛 + 成交量副图，
 * 十字光标/缩放为 klinecharts 默认能力；涨跌颜色默认中式红涨绿跌，可切换。
 */
export const KLineChartPanel: React.FC<KLineChartPanelProps> = ({
  stockCode,
  days = KLINE_DEFAULT_DAYS,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [upRedDownGreen, setUpRedDownGreen] = useState(true);

  useEffect(() => {
    let disposed = false;
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container);
    chartRef.current = chart ?? null;
    if (chart) {
      chart.setStyles(buildCandleStyles(true));
      chart.createIndicator('VOL', false, { id: 'vol_pane' });
    }

    (async () => {
      try {
        const klines = await stocksApi.getKlineHistory(stockCode, days);
        if (disposed) return;
        if (klines.length === 0) {
          setState('empty');
          return;
        }
        chartRef.current?.applyNewData(klines as KLine[]);
        setState('ready');
      } catch (error) {
        if (disposed) return;
        console.error('KLine history load failed:', error);
        setState('error');
      }
    })();

    return () => {
      disposed = true;
      if (container) {
        dispose(container);
      }
      chartRef.current = null;
    };
  }, [stockCode, days]);

  const toggleColors = () => {
    setUpRedDownGreen((prev) => {
      const next = !prev;
      chartRef.current?.setStyles(buildCandleStyles(next));
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-end">
        <button
          type="button"
          onClick={toggleColors}
          aria-label="切换涨跌颜色"
          className="home-surface-button rounded-lg px-3 py-1.5 text-xs text-secondary-text"
        >
          {upRedDownGreen ? '红涨绿跌' : '绿涨红跌'}
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="h-full w-full" data-testid="kline-chart-container" />
        {state === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
          </div>
        )}
        {state === 'empty' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-secondary-text">暂无 K 线数据</p>
          </div>
        )}
        {state === 'error' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-danger">K 线加载失败</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default KLineChartPanel;
```

实现说明：测试 mock 的 `init` 返回对象含 `setStyles/createIndicator/applyNewData/dispose`；真实 klinecharts 9.8 的 `init(el)` 返回 `Chart | null`，`createIndicator('VOL', false, { id })` 把成交量挂在新副图窗格，`setStyles({candle:{bar:{...}}})` 切配色，模块级 `dispose(el)` 释放——均为 9.8 公开 API。`Chart` 类型从 `klinecharts` 导出。

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/kline/__tests__/KLineChartPanel.test.tsx
```

预期输出：`4 passed`，0 failed。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/components/kline/KLineChartPanel.tsx apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.test.tsx && \
git commit -m "feat: 新增 KLineChartPanel 渲染日线蜡烛与成交量副图"
```

---

### Task 0.6 · `KLineDrawer` 壳（Drawer + lazy + ErrorBoundary）+ barrel

**Files**
- Create: `apps/dsa-web/src/components/kline/KLineDrawer.tsx`
- Create: `apps/dsa-web/src/components/kline/index.ts`
- Test: `apps/dsa-web/src/components/kline/__tests__/KLineDrawer.test.tsx`（Create）

**契约**：`KLineDrawer` props `{ stockCode: string; stockName?: string; market?: string; isOpen: boolean; onClose: () => void }`（与跨里程碑契约一致）。复用 `common/Drawer`，仿 `ReportMarkdownDrawer` 范式：`lazy(() => import('./KLineChartPanel'))` + Suspense fallback + ErrorBoundary 兜 chunk 失败/面板渲染错误（对应 spec 6"`/signals` 失败不影响出图"的同类降级护栏，本期是面板加载失败兜底）。**这是 klinecharts 进入 bundle 的唯一 lazy 边界**，保证 Task 0.4 的"仅 lazy import"成立。

**步骤**

① 写失败测试 —— 新建 `apps/dsa-web/src/components/kline/__tests__/KLineDrawer.test.tsx`：

```typescript
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const renderDrawer = async (overrides?: { onClose?: () => void; isOpen?: boolean }) => {
  const { KLineDrawer } = await import('../KLineDrawer');
  const onClose = overrides?.onClose ?? vi.fn();
  render(
    <KLineDrawer
      stockCode="600519"
      stockName="贵州茅台"
      isOpen={overrides?.isOpen ?? true}
      onClose={onClose}
    />,
  );
  return onClose;
};

describe('KLineDrawer', () => {
  afterEach(() => {
    vi.doUnmock('../KLineChartPanel');
    vi.resetModules();
  });

  it('renders nothing when closed', async () => {
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => <div data-testid="kline-panel" />,
    }));

    await renderDrawer({ isOpen: false });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('loads the lazy chart panel inside the drawer when open', async () => {
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => <div data-testid="kline-panel">chart here</div>,
    }));

    await renderDrawer();

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByTestId('kline-panel')).toBeInTheDocument();
  });

  it('keeps a rejected lazy import inside the drawer (chunk failure fallback)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => Promise.reject(new Error('chunk load failed')));

    try {
      await renderDrawer();

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('keeps panel render errors inside the drawer and closes via the handler', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const onClose = vi.fn();
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => {
        throw new Error('panel render failed');
      },
    }));

    try {
      await renderDrawer({ onClose });

      expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '关闭' }));

      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    } finally {
      consoleError.mockRestore();
    }
  });
});
```

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/kline/__tests__/KLineDrawer.test.tsx
```

预期输出：`Failed to resolve import "../KLineDrawer"`，4 条全 FAIL。

③ 写最小实现 —— 新建 `apps/dsa-web/src/components/kline/KLineDrawer.tsx`：

```typescript
import type React from 'react';
import { Component, lazy, Suspense, useCallback, useMemo } from 'react';
import { Drawer } from '../common/Drawer';

interface KLineDrawerProps {
  stockCode: string;
  stockName?: string;
  market?: string;
  isOpen: boolean;
  onClose: () => void;
}

interface KLineDrawerErrorBoundaryProps {
  resetKey: string;
  fallback: React.ReactNode;
  children: React.ReactNode;
}

interface KLineDrawerErrorBoundaryState {
  hasError: boolean;
}

class KLineDrawerErrorBoundary extends Component<
  KLineDrawerErrorBoundaryProps,
  KLineDrawerErrorBoundaryState
> {
  state: KLineDrawerErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): KLineDrawerErrorBoundaryState {
    return { hasError: true };
  }

  componentDidUpdate(prevProps: KLineDrawerErrorBoundaryProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  componentDidCatch(error: unknown) {
    console.error('KLine drawer failed:', error);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

const KLineLoadingState: React.FC = () => (
  <div className="flex h-64 flex-col items-center justify-center">
    <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
    <p className="mt-4 text-sm text-secondary-text">K 线加载中…</p>
  </div>
);

const KLineErrorState: React.FC<{ onRequestClose: () => void }> = ({ onRequestClose }) => (
  <div className="flex h-64 flex-col items-center justify-center">
    <p className="text-sm text-danger">K 线加载失败</p>
    <button
      type="button"
      onClick={onRequestClose}
      className="home-surface-button mt-4 rounded-lg px-4 py-2 text-sm text-secondary-text"
    >
      关闭
    </button>
  </div>
);

/**
 * K 线抽屉壳：Drawer + lazy 重面板 + ErrorBoundary，仿 ReportMarkdownDrawer。
 * klinecharts 仅在 lazy 的 KLineChartPanel 内 import，保证首屏不受影响；
 * 面板加载/渲染失败被错误边界兜底，不外溢、不影响其余页面（spec 6 降级护栏）。
 */
export const KLineDrawer: React.FC<KLineDrawerProps> = ({
  stockCode,
  stockName,
  market,
  isOpen,
  onClose,
}) => {
  const LazyKLineChartPanel = useMemo(
    () => lazy(() => import('./KLineChartPanel')),
    [],
  );

  const handleClose = useCallback(() => {
    onClose();
  }, [onClose]);

  if (!isOpen) return null;

  const title = stockName ? `${stockName} ${stockCode}` : stockCode;

  return (
    <Drawer
      isOpen={isOpen}
      onClose={handleClose}
      title={title}
      width="max-w-4xl"
      zIndex={100}
      backdropClassName="bg-background/56 backdrop-blur-[2px]"
    >
      <div className="flex h-[70vh] flex-col">
        <KLineDrawerErrorBoundary
          resetKey={stockCode}
          fallback={<KLineErrorState onRequestClose={handleClose} />}
        >
          <Suspense fallback={<KLineLoadingState />}>
            <LazyKLineChartPanel stockCode={stockCode} market={market} />
          </Suspense>
        </KLineDrawerErrorBoundary>
      </div>
    </Drawer>
  );
};

export default KLineDrawer;
```

实现说明：`lazy(() => import('./KLineChartPanel'))` 直接消费 `KLineChartPanel` 的 `export default`（Task 0.5 已提供）。测试用 `vi.doMock('../KLineChartPanel', () => ({ default: ... }))` 与之匹配。`title` 复用 Drawer 的标题区。`LazyKLineChartPanel` 接收 `stockCode/market`，与面板 props 对齐（`stockName` 只用于 Drawer 标题）。

新建 barrel `apps/dsa-web/src/components/kline/index.ts`：

```typescript
export { KLineDrawer } from './KLineDrawer';
```

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/kline/__tests__/KLineDrawer.test.tsx
```

预期输出：`4 passed`，0 failed。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/components/kline/KLineDrawer.tsx apps/dsa-web/src/components/kline/index.ts apps/dsa-web/src/components/kline/__tests__/KLineDrawer.test.tsx && \
git commit -m "feat: 新增 KLineDrawer 壳（Drawer + lazy 面板 + 错误边界）"
```

---

### Task 0.7 · StockBar 入口按钮（StockBarItem 加"K线"按钮）

**Files**
- Modify: `apps/dsa-web/src/components/history/StockBarItem.tsx`（`StockBarItemProps` 加 `onViewKline?`，渲染"K线"按钮）
- Test: `apps/dsa-web/src/components/history/__tests__/StockBarItem.test.tsx`（追加用例）

**契约**：在 `StockBarItemComponent` 的 actions 区（删除按钮旁）加一个"K线"按钮，点击 `e.stopPropagation()` 后调用 `onViewKline(item.stockCode, item.stockName)`；`onViewKline` 可选——未传则不渲染该按钮（向后兼容现有调用方）。**只接 StockBar 这一个入口**（spec 5.4 M0：先接 HomePage StockBar 一个最小入口）。market 项（`MARKET` 大盘复盘）不展示 K 线按钮。

**步骤**

① 写失败测试 —— 在 `apps/dsa-web/src/components/history/__tests__/StockBarItem.test.tsx` 末尾（`describe` 内）追加：

```typescript
  it('renders a K线 entry button and invokes onViewKline with code and name', () => {
    const onViewKline = vi.fn();
    render(
      <StockBarItemComponent
        item={issue1600Item}
        isViewing={false}
        onClick={vi.fn()}
        onViewKline={onViewKline}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    const klineButton = within(actions).getByRole('button', { name: /查看 .* K 线/ });
    fireEvent.click(klineButton);

    expect(onViewKline).toHaveBeenCalledWith('600519', '贵州茅台股票股份有限公司');
  });

  it('does not render the K线 button when onViewKline is absent', () => {
    render(
      <StockBarItemComponent
        item={issue1600Item}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    expect(
      within(screen.getByTestId('history-card-actions')).queryByRole('button', { name: /K 线/ }),
    ).not.toBeInTheDocument();
  });

  it('does not invoke the row onClick when the K线 button is clicked', () => {
    const onClick = vi.fn();
    const onViewKline = vi.fn();
    render(
      <StockBarItemComponent
        item={issue1600Item}
        isViewing={false}
        onClick={onClick}
        onViewKline={onViewKline}
      />,
    );

    fireEvent.click(
      within(screen.getByTestId('history-card-actions')).getByRole('button', { name: /查看 .* K 线/ }),
    );

    expect(onClick).not.toHaveBeenCalled();
    expect(onViewKline).toHaveBeenCalledTimes(1);
  });
```

测试文件头需有 `fireEvent`（现有 import 为 `render, screen, within`），在 ③ 一并补 import。

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/history/__tests__/StockBarItem.test.tsx
```

预期输出：新增 3 条因 `fireEvent` 未导入或"K线"按钮不存在而 FAIL；原有用例 PASS。

③ 写最小实现 —— 编辑 `apps/dsa-web/src/components/history/__tests__/StockBarItem.test.tsx` 顶部 import：

```typescript
import { fireEvent, render, screen, within } from '@testing-library/react';
```

编辑 `apps/dsa-web/src/components/history/StockBarItem.tsx`，`StockBarItemProps` 加字段：

```typescript
interface StockBarItemProps {
  item: StockBarItemType;
  isViewing: boolean;
  onClick: (recordId: number) => void;
  onDelete?: (stockCode: string) => void;
  onViewKline?: (stockCode: string, stockName?: string) => void;
  isDeleting?: boolean;
  isMarketReview?: boolean;
}
```

组件解构加 `onViewKline`：

```typescript
export const StockBarItemComponent: React.FC<StockBarItemProps> = ({
  item,
  isViewing,
  onClick,
  onDelete,
  onViewKline,
  isDeleting = false,
  isMarketReview = false,
}) => {
```

在 actions 区，删除按钮（`{onDelete && (`）**之前**插入 K 线按钮（market 项不展示）：

```tsx
              {onViewKline && !isMarketReview && (
                <Button
                  variant="ghost"
                  size="xsm"
                  onClick={(e) => {
                    e.stopPropagation();
                    onViewKline(item.stockCode, item.stockName);
                  }}
                  className="opacity-0 group-hover/item:opacity-100 transition-opacity h-6 w-6 p-0 flex items-center justify-center"
                  aria-label={`查看 ${item.stockName || item.stockCode} K 线`}
                >
                  <svg className="h-3.5 w-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 3v18h18M7 14l3-4 3 3 4-6" />
                  </svg>
                </Button>
              )}
```

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/history/__tests__/StockBarItem.test.tsx
```

预期输出：全部 PASS（原有 + 新增 3 条），`Test Files 1 passed`。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/components/history/StockBarItem.tsx apps/dsa-web/src/components/history/__tests__/StockBarItem.test.tsx && \
git commit -m "feat: 个股栏卡片新增 K 线查看入口按钮"
```

---

### Task 0.8 · 入口接线（StockBar 透传 + HomePage 渲染 KLineDrawer）

**Files**
- Modify: `apps/dsa-web/src/components/history/StockBar.tsx`（`StockBarProps` 加 `onViewKline?`，透传给 `StockBarItemComponent`）
- Modify: `apps/dsa-web/src/pages/HomePage.tsx`（state + handler + 渲染 `KLineDrawer`，把 `onViewKline` 传给 `StockBar`）
- Test: `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx`（Create：透传断言）

**说明**：HomePage 仅做最小接线——新增 `klineTarget` state、`handleViewKline`，把 `<KLineDrawer ... />` 渲染在页面树（与现有 `StockHistoryTrendDrawer`/`ReportMarkdownDrawer` 并列，按需挂载）。HomePage 自身无独立单测覆盖此接线（其 `HomePage.test.tsx` 体量大、接线为组合），故 HomePage 改动以 `npm run build` 类型校验 + StockBar 透传单测 + 手动联调为验证；StockBar 透传单测保证 prop 流不漏。

**步骤**

① 写失败测试 —— 新建 `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx`：

```typescript
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StockBar } from '../StockBar';
import type { StockBarItem } from '../../../types/analysis';

const items: StockBarItem[] = [
  {
    id: 1,
    stockCode: '600519',
    stockName: '贵州茅台',
    sentimentScore: 60,
    operationAdvice: '观望',
    analysisCount: 1,
    lastAnalysisTime: '2026-05-31T04:52:00Z',
  },
];

describe('StockBar onViewKline wiring', () => {
  it('forwards onViewKline from the item button to the parent handler', () => {
    const onViewKline = vi.fn();
    render(
      <StockBar
        items={items}
        isLoading={false}
        onItemClick={vi.fn()}
        onViewKline={onViewKline}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    fireEvent.click(within(actions).getByRole('button', { name: /查看 .* K 线/ }));

    expect(onViewKline).toHaveBeenCalledWith('600519', '贵州茅台');
  });

  it('omits the K线 button when no onViewKline is provided', () => {
    render(
      <StockBar
        items={items}
        isLoading={false}
        onItemClick={vi.fn()}
      />,
    );

    expect(
      within(screen.getByTestId('history-card-actions')).queryByRole('button', { name: /K 线/ }),
    ).not.toBeInTheDocument();
  });
});
```

② 跑命令验证失败：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/history/__tests__/StockBar.test.tsx
```

预期输出：第一条 FAIL —— `StockBar` 不接受 `onViewKline`（TS 编译期或运行期按钮不存在），第二条可能 PASS。结尾 `1 failed, 1 passed` 或两条均 FAIL。

③ 写最小实现 —— 编辑 `apps/dsa-web/src/components/history/StockBar.tsx`：`StockBarProps` 加字段：

```typescript
interface StockBarProps {
  items: StockBarItemType[];
  isLoading: boolean;
  selectedStockCode?: string;
  selectedRecordId?: number;
  onItemClick: (recordId: number) => void;
  onDeleteStock?: (stockCode: string) => Promise<void> | void;
  onViewKline?: (stockCode: string, stockName?: string) => void;
  isDeleting?: boolean;
  className?: string;
}
```

组件解构加 `onViewKline`（在 `onDeleteStock,` 之后）：

```typescript
  onDeleteStock,
  onViewKline,
  isDeleting = false,
```

把它透传给 `StockBarItemComponent`（在现有 `onDelete={onDeleteStock}` 旁）：

```tsx
                  <StockBarItemComponent
                    item={item}
                    isViewing={isSelected}
                    onClick={onItemClick}
                    onDelete={onDeleteStock}
                    onViewKline={onViewKline}
                    isDeleting={isDeleting}
                    isMarketReview={isMarket}
                  />
```

编辑 `apps/dsa-web/src/pages/HomePage.tsx`：
- 在 `import { StockHistoryTrendDrawer, StockBar } from '../components/history';`（:13）下方加：

```typescript
import { KLineDrawer } from '../components/kline';
```

- 在 HomePage 组件内（与其它 `useState` 同处）加状态与 handler：

```typescript
  const [klineTarget, setKlineTarget] = useState<{ stockCode: string; stockName?: string } | null>(null);

  const handleViewKline = useCallback((stockCode: string, stockName?: string) => {
    setKlineTarget({ stockCode, stockName });
  }, []);

  const handleCloseKline = useCallback(() => {
    setKlineTarget(null);
  }, []);
```

- 把 `onViewKline={handleViewKline}` 加到 `<StockBar ... />`（:578）的 props，并把 `handleViewKline` 加入该 `useMemo` 的依赖数组。
- 在页面渲染树（与 `StockHistoryTrendDrawer` 同层，组件 return 内靠近其它 drawer 渲染处）加：

```tsx
      {klineTarget && (
        <KLineDrawer
          stockCode={klineTarget.stockCode}
          stockName={klineTarget.stockName}
          isOpen={true}
          onClose={handleCloseKline}
        />
      )}
```

（`useState`/`useCallback` HomePage 顶部已 import；若 `useCallback` 未在 import 列表中，补入 React import。）

④ 跑命令验证通过：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run src/components/history/__tests__/StockBar.test.tsx
```

预期输出：`2 passed`，0 failed。

再跑全量前端测试 + 类型构建（构建用 `/tmp` 无空格副本，规避工作区路径空格坑）：

```bash
npm --prefix "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web" run test -- --run && \
rsync -a --delete "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-build/ --exclude node_modules --exclude dist && \
npm --prefix /tmp/dsa-web-build ci && \
npm --prefix /tmp/dsa-web-build run lint && \
npm --prefix /tmp/dsa-web-build run build 2>&1 | tail -25
```

预期输出：vitest 全绿（含本段全部新测试）；`eslint .` 无 error；`tsc -b` 无类型错误；`vite build` 成功，且产物含独立 `vendor-klinecharts-*.js` chunk（此时 lazy import 已落地），首屏 entry chunk 不含 klinecharts。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add apps/dsa-web/src/components/history/StockBar.tsx apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx apps/dsa-web/src/pages/HomePage.tsx && \
git commit -m "feat: HomePage 接入 K 线抽屉并由个股栏触发"
```

---

### Task 0.9 · 文档与 CHANGELOG（用户可见入口/端点变化）

> **非 TDD：文档/CHANGELOG task，以命令探针为红绿**——红=`test -f docs/kline-visualization.md` 失败 / CHANGELOG 无新行，绿=文件存在且 `grep` 命中 CHANGELOG 新增条目；无单元测试。

**Files**
- Modify: `.env.example`（在 `/history` 相关或 API 段落注明端点默认 days）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式追加）
- Create: `docs/kline-visualization.md`（M0 行为/入口/降级/后续里程碑指引）

**说明**：本段引入用户可见能力（K 线抽屉入口）与 API 行为变化（`/history` 默认 days），按 AGENTS.md 必须同步 CHANGELOG 与文档。无新增 `.env` 配置项（颜色切换为前端运行时状态，days 默认走端点 Query），故 `.env.example` 仅以注释说明端点默认值，不新增变量；若评审认为无需改 `.env.example` 可省略该文件改动并在交付说明注明。

**步骤**

① 改 `docs/CHANGELOG.md` —— 在 `## [Unreleased]` 段顶部按扁平格式追加（每条独立一行，禁止新增 `### 类目标题`）：

```markdown
- [新功能] Web 个股栏新增 K 线抽屉入口：渲染日线蜡烛 + 成交量副图，支持十字光标、缩放与红涨绿跌/绿涨红跌切换（klinecharts 懒加载，仅在打开抽屉时引入，不影响首屏）。
- [改进] `GET /api/v1/stocks/{code}/history` 端点 `days` 默认由 30 放宽至 120（上限保持 365），为 K 线抽屉提供更长回看窗口；alert 取数与 data_tools 的 365 上限常量未改动。
```

② 新建 `docs/kline-visualization.md`（精简，仅 M0 范围 + 入口 + 降级 + 后续指引）：

```markdown
# K 线可视化（M0 渲染地基）

## 范围
- Web 个股栏（HomePage `StockBar`）每只个股新增「K 线」按钮，点击侧滑出 K 线抽屉。
- 抽屉渲染日线蜡烛 + 成交量副图，支持十字光标、缩放；涨跌颜色默认中式红涨绿跌，可切换为绿涨红跌。
- 数据来自 `GET /api/v1/stocks/{code}/history`（默认 `days=120`），经 `mapKLineDataToKLine` 映射为前端 `KLine`（日期按 Asia/Shanghai 锚定为 epoch ms，`amount` → `turnover`）。

## 关键文件
- 类型：`apps/dsa-web/src/types/kline.ts`（`KLine`）
- client/映射：`apps/dsa-web/src/api/stocks.ts`（`getKlineHistory`、`mapKLineDataToKLine`）
- 组件：`apps/dsa-web/src/components/kline/KLineDrawer.tsx`（壳）、`KLineChartPanel.tsx`（lazy 重面板，唯一 import klinecharts 处）
- 入口：`apps/dsa-web/src/components/history/StockBarItem.tsx` → `StockBar.tsx` → `HomePage.tsx`

## 依赖与分包
- `klinecharts@^9.8.12`（Apache-2.0；禁用 `@latest`/`10.x`）。
- `vite.config.ts` 将 klinecharts 单独打入 `vendor-klinecharts` chunk，且仅经 `KLineDrawer` 的 lazy import 加载，首屏不引入。

## 降级
- 面板加载/渲染失败由 `KLineDrawer` 错误边界兜底，显示「K 线加载失败」，不影响页面其余部分。
- 后端无数据时显示「暂无 K 线数据」。

## 后续里程碑（不在 M0）
- M1：量价信号引擎；M2a：`/signals` 端点与 `SignalMarker`；M2d：图上双轨买卖标注、价位线、命中率展示。本期抽屉只出图、无标注。
```

③ 改 `.env.example` —— 若文件内已有 stocks/history 相关注释段则就近补一行说明；否则可跳过（无新增变量）。验证文件存在并定位段落：

```bash
grep -n -i "history\|stocks\|days" "/root/AI/WorkSpace/cursor/AI _Trading_System/.env.example" | head
```

若有合适锚点，追加注释行（示例，按实际段落调整）：

```bash
# K 线抽屉/历史行情：GET /api/v1/stocks/{code}/history 端点 days 默认 120、上限 365（端点级 Query，非全局常量）
```

④ 验证（docs 任务，不跑代码测试，仅核对命令/文件名/端点名一致）：

```bash
test -f "/root/AI/WorkSpace/cursor/AI _Trading_System/docs/kline-visualization.md" && echo OK && \
grep -q "vendor-klinecharts" "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/vite.config.ts" && echo "chunk-name-consistent" && \
grep -q "days 默认由 30 放宽至 120" "/root/AI/WorkSpace/cursor/AI _Trading_System/docs/CHANGELOG.md" && echo "changelog-ok"
```

预期输出：`OK` / `chunk-name-consistent` / `changelog-ok` 三行。再确认 CHANGELOG `[Unreleased]` 段内未引入 `###` 标题（扁平格式）：

```bash
sed -n '/## \[Unreleased\]/,/^## /p' "/root/AI/WorkSpace/cursor/AI _Trading_System/docs/CHANGELOG.md" | grep -c '^### ' 
```

预期输出：`0`。

⑤ Commit：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add docs/CHANGELOG.md docs/kline-visualization.md .env.example && \
git commit -m "docs: 记录 K 线抽屉入口与 /history days 放宽"
```

---

### M0 收尾验证（合入前一次性回归）

后端：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
./scripts/ci_gate.sh && \
.venv/bin/python -m pytest tests/test_stock_history_days.py tests/test_alert_worker.py tests/test_crypto_api_routes.py -q
```

预期：`ci_gate.sh` 通过；上述三测试文件全绿（特别是 `test_alert_worker.py` 的 `"at most 365 days"` 与 `test_crypto_api_routes.py` 的 history 路由命中均仍通过，证明未触碰 365 常量与 `:path` 路由契约）。

前端（在 `/tmp` 无空格副本跑全套）：

```bash
rsync -a --delete "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-build/ --exclude node_modules --exclude dist && \
npm --prefix /tmp/dsa-web-build ci && \
npm --prefix /tmp/dsa-web-build run lint && \
npm --prefix /tmp/dsa-web-build run test -- --run && \
npm --prefix /tmp/dsa-web-build run build 2>&1 | tail -25
```

预期：`eslint .` 无 error；vitest 全绿；`vite build` 成功且产物含 `vendor-klinecharts-*.js`，首屏 entry/`index-*.js` 不含 klinecharts（可 `grep -rl klinecharts /tmp/dsa-web-build/dist/assets/index-*.js` 应为空）。

**未验证项 / 风险点**：
- klinecharts 9.8 真实 canvas 渲染在 jsdom 下不可断言，组件测试以 mock klinecharts 验证调用契约 + 真测数据映射；真实出图、十字光标/缩放交互需在浏览器（`npm run dev` + 浏览器或 playwright smoke）人工确认，列为 M0 手动验收项。
- `HomePage.tsx` 接线无独立单测，依赖 `tsc -b` 类型校验 + StockBar 透传单测 + 手动联调。
- crypto 带 `/` 代码（如 `BTC/USDT`）的 K 线入口端到端依赖后端 `:path` 路由（已有 `test_crypto_api_routes.py` 守护）+ 前端 `encodeURIComponent`（Task 0.3 单测守护）。
- 回滚：本段全部为新增文件 + 端点 Query 默认值 + 三处可选 prop；逐 commit revert 即可恢复，端点改回 `Query(30, ...)`，删除 `components/kline/` 与 `types/kline.ts` 不影响主流程。

---

以下为我在起草时核验的关键事实（供执行 agent 参照，避免漂移）：
- `/history` 端点在 `api/v1/endpoints/stocks.py`，`days: int = Query(30, ge=1, le=365, ...)` 在 `get_stock_history` 签名内（spec 标注 :491）；`StockService.get_history_data(stock_code, period, days)` 签名已确认。
- 365 常量另在 `src/services/alert_indicators.py:24` (`MAX_REQUESTED_DAYS`)，受 `tests/test_alert_worker.py:60` 的 `"at most 365 days"` 断言守护——M0 绝不触碰。
- `tests/test_crypto_api_routes.py` 已示范 `TestClient(app)` + monkeypatch `stocks_ep.StockService` 的后端端点测试范式（Task 0.1 复用）。
- 前端 vitest 配置：`vitest.config.ts`（jsdom + globals + `src/setupTests.ts`），API 测试用 `vi.hoisted` + `vi.mock('../index')` mock axios（`alphasift.test.ts`/`systemConfig.test.ts` 范式），Drawer 测试用 `vi.doMock` + lazy 范式（`ReportMarkdownDrawer.test.tsx`）。
- `Drawer`（`components/common/Drawer.tsx`）渲染 `role="dialog"`，关闭按钮 `aria-label="关闭抽屉"`（注意：错误态自定义关闭按钮文案为「关闭」，与 Drawer 自带关闭按钮区分）。`ReportMarkdownDrawer` 是 lazy+ErrorBoundary 范本。
- `StockBarItemComponent`（`components/history/StockBarItem.tsx`）actions 区有 `data-testid="history-card-actions"`，删除按钮 `aria-label="删除 ... 历史记录"`；`StockBarItem` 类型（`types/analysis.ts:518`）无 `market` 字段，故入口仅传 `stockCode/stockName`，`market` 在 `KLineDrawer` 保持可选 undefined。
- `vite.config.ts` 的 `vendorChunkByPackage` 含 `recharts: 'vendor-charts'`，新增 `klinecharts: 'vendor-klinecharts'` 与之区分。
- `package.json` 当前无 klinecharts，recharts 为 `^3.3.0`；前端 `format.ts` 用 `Asia/Shanghai`（`getRecentStartDate`/`getTodayInShanghai`），映射 util 的 Shanghai 锚定与之一致。
- 工作区路径含空格（MEMORY 记录会导致 `npm ci` 残缺），故全套 lint/build 在 `/tmp/dsa-web-build` 无空格副本跑；单文件 `vitest run` 用 `npm --prefix` 即可。

---

## M1 · 量价信号引擎

实现 `src/services/volume_price_signals.py`（纯函数模块，无 I/O、无 DB）：swing pivot 基元、ATR、量价八法穷尽互斥查表、OBV+顶底背离、放量突破/缩量回调、Anchored VWAP、B 类 VSA/Upthrust/Spring 降权契约、主入口 `compute_volume_price_signals`。全部 marker 时间锚用 `epoch ms (Asia/Shanghai)`，所有滚动量基元强制 `shift(1)` 防未来函数，swing pivot 左右各 `k` 确认天然滞后 `k`。本里程碑仅后端 + pytest，不碰 schema/端点/前端（M2a 起）。

复用契约（精确命名，禁止漂移）：
- 归一化复用 `normalize_ohlcv(df, required_columns=('open','high','low','close','volume'))`（`src/services/alert_indicators.py:178`，`required_columns` keyword-only 必填）。
- 配置读取复用 `parse_env_float(value, default, *, field_name, minimum=None, maximum=None)`（`src/config.py:200`）。
- 主入口 `compute_volume_price_signals(df, *, config: VPSConfig | None = None) -> VPSResult`；`VPSResult{markers: list[VPSignal], status: 'ok'|'degraded', degraded_reason: str|None}`。
- swing pivot `find_swing_pivots(series, k: int) -> list[Pivot]`；ATR `atr(df, period: int = 14) -> pd.Series`（**全仓唯一定义**，M2b 复用）。

### File Structure（M1 落地后）

```
src/services/
  volume_price_signals.py      # NEW：引擎，纯函数（M1 全部实现落点）
tests/
  test_volume_price_signals.py # NEW：真值表 + 边界 + 未来函数反例 + 鲁棒性 + B 类降权
.env.example                   # MODIFY：追加量价阈值/eps/swing k/突破窗口可配项
docs/CHANGELOG.md              # MODIFY：[Unreleased] 追加一行
docs/volume-price-signals.md   # NEW：引擎字段契约/阈值/降权语义说明
```

引擎内固定的数据结构（后续所有 Task 只允许引用以下命名，不得新增同义类型）：

```python
# src/services/volume_price_signals.py 顶部统一定义
@dataclass(frozen=True)
class VPSConfig:
    eps: float = 0.004                  # 价档 flat 判定半带宽（pct_chg 绝对值 <= eps）
    vol_low: float = 0.7                # 量档 low 上界（< vol_low）
    vol_shrink: float = 0.8             # shrink 上界
    vol_up: float = 1.2                 # normal 上界
    vol_high: float = 1.5               # up 上界 / high 下界（>= vol_high）
    swing_k: int = 3                    # swing pivot 左右确认根数
    vol_ma_window: int = 20             # 量基准窗口（交易 bar 数）
    breakout_window: int = 20           # 放量突破 high.rolling 窗口 N
    breakout_rel_vol: float = 2.0       # 放量突破 rel_vol 阈值
    pullback_rel_vol: float = 0.9       # 缩量回调段内 rel_vol 上界
    pullback_atr_mult: float = 3.0      # 缩量回调最大回撤 = ATR * 倍数
    atr_period: int = 14
    b_class_top_k: int = 2              # B 类每结果集限流 top-k
    b_class_confidence: str = "low"     # B 类置信硬上限

@dataclass(frozen=True)
class Pivot:
    index: int          # df 行号（已确认，滞后 k）
    timestamp: int      # epoch ms (Asia/Shanghai)
    price: float
    kind: str           # 'high' | 'low'

@dataclass(frozen=True)
class VPSignal:
    timestamp: int               # epoch ms (Asia/Shanghai)
    price: float
    anchor: str                  # 'low' | 'high' | 'close'
    direction: str               # 'bullish' | 'bearish' | 'neutral'
    signal_type: str
    confidence: str              # 'high' | 'medium' | 'low'
    is_daily_approx: bool
    is_anomalous: bool
    reason: str
    threshold: float | None
    observed_value: float | None

@dataclass(frozen=True)
class VPSResult:
    markers: list[VPSignal]
    status: str                  # 'ok' | 'degraded'
    degraded_reason: str | None
```

`signal_type` 取值集合（固定，跨 Task 引用）：`vfx_*`（八法，见 Task 3 真值表）、`obv_top_divergence`/`obv_bottom_divergence`、`volume_breakout`、`shrink_pullback`、`anchored_vwap_reclaim`/`anchored_vwap_loss`、`vsa_no_demand`/`vsa_no_supply`/`vsa_stopping`/`vsa_effort_vs_result`、`upthrust`、`spring`。

---

### Task 1 · 共用基元：normalize + 量化派生 + 鲁棒性骨架

**Files**
- Test: `tests/test_volume_price_signals.py`（Create）
- Create: `src/services/volume_price_signals.py`

本 Task 落地：`VPSConfig`/`Pivot`/`VPSignal`/`VPSResult` 数据结构；内部 `_to_epoch_ms_shanghai(date_value) -> int`；`_compute_primitives(norm_df, config) -> pd.DataFrame`（追加列 `spread/body/range_pos/vol_ma/rel_vol/pct_chg/ma5/ma20/is_limit_bar`，全部 `shift(1)` 口径）；`_normalize(df, config) -> tuple[pd.DataFrame, str | None]`（包裹 `normalize_ohlcv`，捕获 `ValueError`、空/窗口不足返回 degraded reason）。

#### Step 1.1 — 写失败测试

```python
# tests/test_volume_price_signals.py
# -*- coding: utf-8 -*-
"""Unit tests for the volume-price signal engine (M1)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import math
import numpy as np
import pandas as pd
import pytest

from src.services.volume_price_signals import (
    VPSConfig,
    VPSResult,
    VPSignal,
    Pivot,
    compute_volume_price_signals,
    find_swing_pivots,
    atr,
    _compute_primitives,
    _normalize,
    _to_epoch_ms_shanghai,
)

SH = ZoneInfo("Asia/Shanghai")


def _make_df(rows: list[dict], start: str = "2024-01-01") -> pd.DataFrame:
    """Build an OHLCV df with consecutive calendar dates (string date column)."""
    base = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i, r in enumerate(rows):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, **r})
    return pd.DataFrame(out)


def _bar(open_, high, low, close, volume) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _flat_series(n: int, price: float = 100.0, volume: float = 1000.0) -> pd.DataFrame:
    return _make_df([_bar(price, price + 1, price - 1, price, volume) for _ in range(n)])


def test_to_epoch_ms_uses_shanghai_midnight():
    ms = _to_epoch_ms_shanghai("2024-01-01")
    expected = int(datetime(2024, 1, 1, tzinfo=SH).timestamp() * 1000)
    assert ms == expected


def test_primitives_shift_one_no_lookahead():
    # vol_ma at row t must NOT include volume[t]
    df = _make_df([_bar(10, 11, 9, 10, v) for v in (100, 200, 300, 400, 500)])
    norm, reason = _normalize(df, VPSConfig())
    assert reason is None
    prim = _compute_primitives(norm, VPSConfig(vol_ma_window=2))
    # vol_ma[2] = mean(volume[0],volume[1]) = 150, NOT mean including volume[2]
    assert prim["vol_ma"].iloc[2] == pytest.approx(150.0)
    assert prim["rel_vol"].iloc[2] == pytest.approx(300.0 / 150.0)


def test_primitives_limit_bar_flagged_and_range_pos_none():
    df = _make_df([_bar(10, 10, 10, 10, 100)] * 3)  # one-字板 high==low
    norm, _ = _normalize(df, VPSConfig())
    prim = _compute_primitives(norm, VPSConfig())
    assert bool(prim["is_limit_bar"].iloc[-1]) is True
    assert pd.isna(prim["range_pos"].iloc[-1])


def test_primitives_rel_vol_none_when_vol_ma_zero():
    df = _make_df([_bar(10, 11, 9, 10, 0)] * 5)  # zero volume -> vol_ma 0
    norm, _ = _normalize(df, VPSConfig(vol_ma_window=2))
    prim = _compute_primitives(norm, VPSConfig(vol_ma_window=2))
    assert pd.isna(prim["rel_vol"].iloc[-1])


def test_normalize_missing_volume_returns_degraded_reason():
    df = pd.DataFrame({"date": ["2024-01-01"], "open": [1], "high": [1], "low": [1], "close": [1]})
    norm, reason = _normalize(df, VPSConfig())
    assert norm.empty
    assert reason is not None and "volume" in reason


def test_normalize_insufficient_window_returns_degraded_reason():
    df = _flat_series(5)  # fewer than vol_ma_window default 20
    norm, reason = _normalize(df, VPSConfig())
    assert reason is not None and "insufficient" in reason.lower()
```

#### Step 1.2 — 验证失败

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：collection error / `ModuleNotFoundError: No module named 'src.services.volume_price_signals'`（红）。

#### Step 1.3 — 写最小实现

```python
# src/services/volume_price_signals.py
# -*- coding: utf-8 -*-
"""量价信号引擎（M1）：纯函数，输入 OHLCV DataFrame，输出 VPSResult。

设计要点：
- 所有滚动量基元统一 shift(1)，防未来函数。
- swing pivot 左右各 k 根确认，天然滞后 k，OBV 背离 / VSA 高低点全部复用。
- 八法为穷尽且互斥的二维查表，每格必有归类（信号或 neutral 兜底）。
- B 类（VSA/Upthrust/Spring）强制降权：不进 consistency 投票、不驱动 price_lines、置信 <= low、top-k 限流。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.config import parse_env_float
from src.services.alert_indicators import normalize_ohlcv

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class VPSConfig:
    eps: float = 0.004
    vol_low: float = 0.7
    vol_shrink: float = 0.8
    vol_up: float = 1.2
    vol_high: float = 1.5
    swing_k: int = 3
    vol_ma_window: int = 20
    breakout_window: int = 20
    breakout_rel_vol: float = 2.0
    pullback_rel_vol: float = 0.9
    pullback_atr_mult: float = 3.0
    atr_period: int = 14
    b_class_top_k: int = 2
    b_class_confidence: str = "low"

    @classmethod
    def from_env(cls) -> "VPSConfig":
        return cls(
            eps=parse_env_float(os.getenv("VPS_PRICE_EPS"), 0.004, field_name="VPS_PRICE_EPS", minimum=0.0),
            vol_low=parse_env_float(os.getenv("VPS_VOL_LOW"), 0.7, field_name="VPS_VOL_LOW", minimum=0.0),
            vol_shrink=parse_env_float(os.getenv("VPS_VOL_SHRINK"), 0.8, field_name="VPS_VOL_SHRINK", minimum=0.0),
            vol_up=parse_env_float(os.getenv("VPS_VOL_UP"), 1.2, field_name="VPS_VOL_UP", minimum=0.0),
            vol_high=parse_env_float(os.getenv("VPS_VOL_HIGH"), 1.5, field_name="VPS_VOL_HIGH", minimum=0.0),
            swing_k=int(parse_env_float(os.getenv("VPS_SWING_K"), 3.0, field_name="VPS_SWING_K", minimum=1.0)),
            breakout_window=int(parse_env_float(os.getenv("VPS_BREAKOUT_WINDOW"), 20.0, field_name="VPS_BREAKOUT_WINDOW", minimum=2.0)),
            breakout_rel_vol=parse_env_float(os.getenv("VPS_BREAKOUT_REL_VOL"), 2.0, field_name="VPS_BREAKOUT_REL_VOL", minimum=1.0),
        )


@dataclass(frozen=True)
class Pivot:
    index: int
    timestamp: int
    price: float
    kind: str


@dataclass(frozen=True)
class VPSignal:
    timestamp: int
    price: float
    anchor: str
    direction: str
    signal_type: str
    confidence: str
    is_daily_approx: bool
    is_anomalous: bool
    reason: str
    threshold: float | None
    observed_value: float | None


@dataclass(frozen=True)
class VPSResult:
    markers: list[VPSignal]
    status: str
    degraded_reason: str | None


def _to_epoch_ms_shanghai(date_value) -> int:
    if isinstance(date_value, str):
        dt = datetime.strptime(date_value[:10], "%Y-%m-%d")
    elif isinstance(date_value, pd.Timestamp):
        dt = date_value.to_pydatetime()
    elif isinstance(date_value, datetime):
        dt = date_value
    else:
        dt = pd.Timestamp(date_value).to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_SHANGHAI)
    return int(dt.timestamp() * 1000)


def _normalize(df, config: VPSConfig) -> tuple[pd.DataFrame, str | None]:
    try:
        norm = normalize_ohlcv(df, required_columns=_REQUIRED_COLUMNS)
    except ValueError as exc:
        return pd.DataFrame(), str(exc)
    if norm.empty:
        return norm, "no closed daily data available"
    min_bars = max(config.vol_ma_window, config.atr_period, config.breakout_window) + 1
    if len(norm) < min_bars:
        return norm, f"insufficient window: need {min_bars} bars, got {len(norm)}"
    return norm, None


def _compute_primitives(norm_df: pd.DataFrame, config: VPSConfig) -> pd.DataFrame:
    prim = norm_df.copy()
    high = prim["high"].astype(float)
    low = prim["low"].astype(float)
    close = prim["close"].astype(float)
    open_ = prim["open"].astype(float)
    volume = prim["volume"].astype(float)

    spread = high - low
    prim["spread"] = spread
    prim["body"] = close - open_
    prim["is_limit_bar"] = (spread <= 0)
    range_pos = (close - low) / spread.where(spread > 0)
    prim["range_pos"] = range_pos  # NaN where spread<=0 (一字板) -> neutral, 不兜 eps

    vol_ma = volume.rolling(config.vol_ma_window).mean().shift(1)
    prim["vol_ma"] = vol_ma
    rel_vol = volume / vol_ma.where(vol_ma > 0)
    prim["rel_vol"] = rel_vol  # NaN where vol_ma<=0|NaN -> degraded marker

    prim["pct_chg"] = close.pct_change()
    prim["ma5"] = close.rolling(5).mean()
    prim["ma20"] = close.rolling(20).mean()
    return prim
```

> 注：`compute_volume_price_signals`/`find_swing_pivots`/`atr` 在后续 Task 引入；本 Task 测试只 import 已实现的符号。Step 1.1 顶部的 `compute_volume_price_signals, find_swing_pivots, atr` import 会在本 Task 失败——因此本 Task 实现需同时落地这三者的**最小骨架**（签名 + 占位返回），以使 import 成功；其真实逻辑在 Task 2/4 替换。最小骨架：

```python
def find_swing_pivots(series, k: int) -> list["Pivot"]:
    raise NotImplementedError("implemented in Task 2")


def atr(df, period: int = 14) -> pd.Series:
    raise NotImplementedError("implemented in Task 2")


def compute_volume_price_signals(df, *, config: VPSConfig | None = None) -> VPSResult:
    cfg = config or VPSConfig()
    norm, reason = _normalize(df, cfg)
    if reason is not None:
        return VPSResult(markers=[], status="degraded", degraded_reason=reason)
    return VPSResult(markers=[], status="ok", degraded_reason=None)
```

#### Step 1.4 — 验证通过

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：`6 passed`（本 Task 6 个用例全绿；`find_swing_pivots`/`atr` 的 `NotImplementedError` 不被这 6 个用例触发）。

#### Step 1.5 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git checkout -b feat/m1-volume-price-signals && \
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py && \
git commit -m "feat: 量价引擎落地共用基元与鲁棒性骨架"
```

---

### Task 2 · swing pivot 基元 + ATR

**Files**
- Test: `tests/test_volume_price_signals.py`（Modify，追加 swing/atr 用例）
- Modify: `src/services/volume_price_signals.py`（替换 `find_swing_pivots`/`atr` 占位）

#### Step 2.1 — 写失败测试

```python
# tests/test_volume_price_signals.py 追加

def test_find_swing_pivots_lags_by_k_no_lookahead():
    # V 形：低点在 index 3，k=2 需 index 5 才确认 -> pivot.index==3 但只有右侧 2 根确认后才产出
    closes = [10, 9, 8, 5, 8, 9, 10]
    s = pd.Series(closes)
    pivots = find_swing_pivots(s, k=2)
    lows = [p for p in pivots if p.kind == "low"]
    assert any(p.index == 3 and p.price == 5 for p in lows)
    # 最后 k 根不可能成为已确认 pivot（右侧确认不足）
    assert all(p.index <= len(closes) - 1 - 2 for p in pivots)


def test_find_swing_pivots_micro_new_high_is_not_pivot():
    # 每日微创新高（单调上升）-> 无 swing high pivot（永远没有右侧更低确认）
    s = pd.Series([float(i) for i in range(20)])
    pivots = find_swing_pivots(s, k=3)
    assert [p for p in pivots if p.kind == "high"] == []


def test_find_swing_pivots_detects_high_and_low():
    closes = [1, 2, 3, 2, 1, 2, 3, 4, 3, 2]
    pivots = find_swing_pivots(pd.Series(closes), k=2)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    assert any(p.index == 2 for p in highs)
    assert any(p.index == 4 for p in lows)


def test_atr_matches_wilder_manual():
    df = pd.DataFrame({
        "high": [10, 12, 13, 14],
        "low": [8, 9, 11, 12],
        "close": [9, 11, 12, 13],
    })
    out = atr(df, period=2)
    # TR1 NaN(no prev close); TR2=max(12-9,|12-9|,|9-9|)=3; TR3=max(13-11,|13-11|,|11-11|)=2
    assert out.iloc[1] == pytest.approx(3.0)  # first available (rolling/ewm seed)
    assert out.notna().iloc[-1]
    assert (out.dropna() > 0).all()
```

#### Step 2.2 — 验证失败

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q -k "swing or atr"
```
预期：4 个新用例 `NotImplementedError`（红）。

#### Step 2.3 — 写最小实现

```python
# src/services/volume_price_signals.py：替换两个 NotImplementedError 占位

def find_swing_pivots(series, k: int) -> list[Pivot]:
    """左右各 k 根严格确认的摆动高低点；天然滞后 k，不含未来函数。"""
    values = pd.Series(series).astype(float).reset_index(drop=True)
    n = len(values)
    pivots: list[Pivot] = []
    if n < 2 * k + 1 or k < 1:
        return pivots
    for i in range(k, n - k):
        window = values.iloc[i - k:i + k + 1]
        center = values.iloc[i]
        left = window.iloc[:k]
        right = window.iloc[k + 1:]
        if center > left.max() and center > right.max():
            pivots.append(Pivot(index=i, timestamp=0, price=float(center), kind="high"))
        elif center < left.min() and center < right.min():
            pivots.append(Pivot(index=i, timestamp=0, price=float(center), kind="low"))
    return pivots


def _attach_pivot_timestamps(pivots: list[Pivot], norm_df: pd.DataFrame) -> list[Pivot]:
    return [
        Pivot(
            index=p.index,
            timestamp=_to_epoch_ms_shanghai(norm_df["date"].iloc[p.index]),
            price=p.price,
            kind=p.kind,
        )
        for p in pivots
    ]


def atr(df, period: int = 14) -> pd.Series:
    """Wilder ATR（全仓唯一定义，M2b 复用）。"""
    frame = pd.DataFrame(df)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
```

> `find_swing_pivots` 入参为价格序列、不含 date，故 `timestamp=0`；引擎内部用 `_attach_pivot_timestamps` 在拿到 `norm_df` 后回填真实时间锚（后续 Task 调用）。`test_atr_matches_wilder_manual` 断言 `iloc[1]==3.0`：`min_periods=1` 下 ewm 第二点即 TR2=3（TR1 因无前收为 NaN，被 ewm seed 跳过）。

#### Step 2.4 — 验证通过

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：`10 passed`（Task 1 的 6 + 本 Task 的 4）。

#### Step 2.5 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py && \
git commit -m "feat: 新增 swing pivot 基元与 Wilder ATR"
```

---

### Task 3 · 量价八法穷尽互斥查表

**Files**
- Test: `tests/test_volume_price_signals.py`（Modify，追加完整真值表 + 边界点）
- Modify: `src/services/volume_price_signals.py`（新增 `_volume_bucket`/`_price_bucket`/`_classify_vfx`）

量档（左闭右开，对称无缝）：`low: rel_vol<0.7` / `shrink: [0.7,0.8)` / `normal: [0.8,1.2)` / `up: [1.2,1.5)` / `high: >=1.5`。价档：`down: pct_chg<-eps` / `flat: |pct_chg|<=eps` / `up: pct_chg>eps`（eps=0.4%）。5×3=15 格全覆盖，每格给定 `signal_type`（`vfx_*`）+ direction，量增价升须叠加 `body>0 或 range_pos>0.5` 才确认看多否则降级 neutral；`rel_vol is None` 或一字板 → `vfx_undefined` neutral + `is_anomalous`。

真值表（15 格 + 确认/降级规则）：

| 量档＼价档 | down | flat | up |
| --- | --- | --- | --- |
| low(<0.7) | `vfx_shrink_down` bearish→实为缩量下跌(中性偏空) | `vfx_dry_flat` neutral | `vfx_shrink_up` bullish(背量上涨,谨慎) |
| shrink([0.7,0.8)) | `vfx_shrink_down` neutral | `vfx_dry_flat` neutral | `vfx_shrink_up` neutral |
| normal([0.8,1.2)) | `vfx_normal_down` neutral | `vfx_normal_flat` neutral | `vfx_normal_up` neutral |
| up([1.2,1.5)) | `vfx_expand_down` bearish | `vfx_expand_flat` neutral | `vfx_expand_up` bullish*（须 body>0 或 range_pos>0.5，否则降 neutral=派发） |
| high(>=1.5) | `vfx_climax_down` bearish | `vfx_climax_flat` neutral | `vfx_climax_up` bullish*（同上确认） |

#### Step 3.1 — 写失败测试

```python
# tests/test_volume_price_signals.py 追加
from src.services.volume_price_signals import _volume_bucket, _price_bucket, _classify_vfx


@pytest.mark.parametrize("rel_vol,expected", [
    (0.69, "low"), (0.70, "shrink"), (0.79, "shrink"),
    (0.80, "normal"), (1.19, "normal"),
    (1.20, "up"), (1.49, "up"),
    (1.50, "high"), (3.0, "high"),
])
def test_volume_bucket_boundaries(rel_vol, expected):
    assert _volume_bucket(rel_vol, VPSConfig()) == expected


@pytest.mark.parametrize("pct,expected", [
    (-0.005, "down"), (-0.0041, "down"),
    (-0.004, "flat"), (0.0, "flat"), (0.004, "flat"),
    (0.0041, "up"), (0.02, "up"),
])
def test_price_bucket_boundaries(pct, expected):
    assert _price_bucket(pct, VPSConfig()) == expected


def test_vfx_truth_table_complete_no_dead_zone():
    cfg = VPSConfig()
    seen = set()
    for rel in (0.5, 0.75, 1.0, 1.3, 2.0):
        for pct in (-0.02, 0.0, 0.02):
            sig = _classify_vfx(rel_vol=rel, pct_chg=pct, body=1.0, range_pos=0.8, config=cfg)
            assert sig is not None
            assert sig.signal_type.startswith("vfx_")
            assert sig.direction in {"bullish", "bearish", "neutral"}
            seen.add((rel, pct))
    assert len(seen) == 15  # 5 量档 × 3 价档 全覆盖


def test_vfx_expand_up_without_body_confirmation_degrades_to_neutral():
    cfg = VPSConfig()
    # 量增价升但收阴(body<0)、收在下半区 -> 派发，不得判 bullish
    sig = _classify_vfx(rel_vol=1.3, pct_chg=0.02, body=-1.0, range_pos=0.2, config=cfg)
    assert sig.signal_type == "vfx_expand_up"
    assert sig.direction == "neutral"


def test_vfx_expand_up_with_body_confirmation_is_bullish():
    cfg = VPSConfig()
    sig = _classify_vfx(rel_vol=1.3, pct_chg=0.02, body=1.0, range_pos=0.8, config=cfg)
    assert sig.direction == "bullish"


def test_vfx_none_rel_vol_is_anomalous_neutral():
    cfg = VPSConfig()
    sig = _classify_vfx(rel_vol=None, pct_chg=0.02, body=1.0, range_pos=0.8, config=cfg)
    assert sig.signal_type == "vfx_undefined"
    assert sig.direction == "neutral"
    assert sig.is_anomalous is True
```

#### Step 3.2 — 验证失败

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q -k "bucket or vfx"
```
预期：`ImportError: cannot import name '_volume_bucket'`（红）。

#### Step 3.3 — 写最小实现

```python
# src/services/volume_price_signals.py 追加

_VFX_TABLE = {
    ("low", "down"): ("vfx_shrink_down", "bearish"),
    ("low", "flat"): ("vfx_dry_flat", "neutral"),
    ("low", "up"): ("vfx_shrink_up", "bullish"),
    ("shrink", "down"): ("vfx_shrink_down", "neutral"),
    ("shrink", "flat"): ("vfx_dry_flat", "neutral"),
    ("shrink", "up"): ("vfx_shrink_up", "neutral"),
    ("normal", "down"): ("vfx_normal_down", "neutral"),
    ("normal", "flat"): ("vfx_normal_flat", "neutral"),
    ("normal", "up"): ("vfx_normal_up", "neutral"),
    ("up", "down"): ("vfx_expand_down", "bearish"),
    ("up", "flat"): ("vfx_expand_flat", "neutral"),
    ("up", "up"): ("vfx_expand_up", "bullish"),
    ("high", "down"): ("vfx_climax_down", "bearish"),
    ("high", "flat"): ("vfx_climax_flat", "neutral"),
    ("high", "up"): ("vfx_climax_up", "bullish"),
}
# 量增价升须确认 body>0 或 range_pos>0.5，否则降级 neutral（派发陷阱）
_VFX_NEEDS_CONFIRM = {("up", "up"), ("high", "up")}


def _volume_bucket(rel_vol, config: VPSConfig) -> str | None:
    if rel_vol is None or (isinstance(rel_vol, float) and np.isnan(rel_vol)):
        return None
    if rel_vol < config.vol_low:
        return "low"
    if rel_vol < config.vol_shrink:
        return "shrink"
    if rel_vol < config.vol_up:
        return "normal"
    if rel_vol < config.vol_high:
        return "up"
    return "high"


def _price_bucket(pct_chg, config: VPSConfig) -> str | None:
    if pct_chg is None or (isinstance(pct_chg, float) and np.isnan(pct_chg)):
        return None
    if pct_chg < -config.eps:
        return "down"
    if pct_chg > config.eps:
        return "up"
    return "flat"


def _classify_vfx(*, rel_vol, pct_chg, body, range_pos, config: VPSConfig) -> VPSignal:
    vbucket = _volume_bucket(rel_vol, config)
    pbucket = _price_bucket(pct_chg, config)
    if vbucket is None or pbucket is None:
        return VPSignal(
            timestamp=0, price=0.0, anchor="close", direction="neutral",
            signal_type="vfx_undefined", confidence="low",
            is_daily_approx=True, is_anomalous=True,
            reason="量比或涨跌幅不可用（vol_ma<=0/NaN 或一字板）",
            threshold=None, observed_value=rel_vol if rel_vol is not None else None,
        )
    signal_type, direction = _VFX_TABLE[(vbucket, pbucket)]
    if (vbucket, pbucket) in _VFX_NEEDS_CONFIRM:
        body_ok = body is not None and body > 0
        pos_ok = range_pos is not None and not (isinstance(range_pos, float) and np.isnan(range_pos)) and range_pos > 0.5
        if not (body_ok or pos_ok):
            direction = "neutral"  # 量增价升但收阴/弱势 -> 派发，不得看多
    return VPSignal(
        timestamp=0, price=0.0, anchor="close", direction=direction,
        signal_type=signal_type, confidence="medium",
        is_daily_approx=True, is_anomalous=False,
        reason=f"量价八法：量档={vbucket}/价档={pbucket}",
        threshold=None, observed_value=float(rel_vol),
    )
```

#### Step 3.4 — 验证通过

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：全绿、0 failed/error（含 Task 1+2 的 10 个基础用例 + 本 Task 新增的八法量档/价档边界用例；参数化展开后总数随实参数变化，以实际收集数为准，关键是无 fail/error）。

#### Step 3.5 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py && \
git commit -m "feat: 量价八法穷尽互斥查表与边界归类"
```

---

### Task 4 · A 类信号：OBV 背离 + 放量突破 + 缩量回调 + Anchored VWAP，接入主入口

**Files**
- Test: `tests/test_volume_price_signals.py`（Modify，追加 OBV/突破/AVWAP 未来函数反例 + 鲁棒性）
- Modify: `src/services/volume_price_signals.py`（新增 `_obv`/`_detect_obv_divergence`/`_detect_breakouts`/`_detect_shrink_pullback`/`_anchored_vwap_signals`；`compute_volume_price_signals` 串联 A 类）

#### Step 4.1 — 写失败测试

```python
# tests/test_volume_price_signals.py 追加

def _trend_up_df(n: int = 40, step: float = 1.0, base_vol: float = 1000.0) -> pd.DataFrame:
    rows = []
    price = 100.0
    for _ in range(n):
        o = price
        c = price + step
        rows.append(_bar(o, c + 0.5, o - 0.5, c, base_vol))
        price = c
    return _make_df(rows)


def test_micro_new_high_does_not_trigger_obv_top_divergence():
    # 价格每日微创新高 + 量同步 -> OBV 同步创高，不应误报顶背离
    df = _trend_up_df(60)
    res = compute_volume_price_signals(df)
    assert all(m.signal_type != "obv_top_divergence" for m in res.markers)


def test_obv_top_divergence_when_price_high_obv_not():
    # 构造两个已确认 swing high：第二个价更高但量持续萎缩 -> OBV 未跟 -> 顶背离
    rows = []
    # 第一峰
    seq = [100, 103, 106, 103, 100, 103, 108, 104, 100]
    vols = [2000, 2200, 2400, 1500, 1400, 1600, 900, 800, 700]  # 第二峰量明显小
    for p, v in zip(seq, vols):
        rows.append(_bar(p, p + 0.5, p - 0.5, p, v))
    # 垫满窗口
    pad = [_bar(100, 100.5, 99.5, 100, 1000) for _ in range(30)]
    df = _make_df(pad + rows)
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    assert any(m.signal_type == "obv_top_divergence" for m in res.markers)


def test_breakout_excludes_current_day():
    # 当日收盘恰等于此前窗口最高；shift(1) 不含当日 -> 视为突破（>= 历史 max）
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    breakout = _bar(100, 105.0, 100.0, 105.0, 5000)  # close 105 >= 此前 high max 100, rel_vol 高
    df = _make_df(pad + [breakout])
    res = compute_volume_price_signals(df, config=VPSConfig(breakout_window=20, breakout_rel_vol=2.0))
    bks = [m for m in res.markers if m.signal_type == "volume_breakout"]
    assert len(bks) == 1
    assert bks[0].timestamp == _to_epoch_ms_shanghai(df["date"].iloc[-1])


def test_breakout_requires_rel_vol_threshold():
    pad = [_bar(100, 100.0, 99.0, 100, 1000) for _ in range(30)]
    breakout_lowvol = _bar(100, 105.0, 100.0, 105.0, 1000)  # 价突破但量不足
    df = _make_df(pad + [breakout_lowvol])
    res = compute_volume_price_signals(df, config=VPSConfig(breakout_rel_vol=2.0))
    assert all(m.signal_type != "volume_breakout" for m in res.markers)


def test_anchored_vwap_only_anchors_confirmed_breakout_not_future_bottom():
    # 一个非突破的局部低点不得被 AVWAP 提前锚定；仅突破日产 AVWAP 相关 marker
    df = _trend_up_df(40)  # 平滑上行，无放量突破事件
    res = compute_volume_price_signals(df)
    assert all(not m.signal_type.startswith("anchored_vwap") for m in res.markers)


def test_compute_degraded_on_short_window():
    df = _flat_series(5)
    res = compute_volume_price_signals(df)
    assert res.status == "degraded"
    assert res.markers == []
    assert res.degraded_reason is not None


def test_compute_ok_status_on_sufficient_window():
    df = _trend_up_df(40)
    res = compute_volume_price_signals(df)
    assert res.status == "ok"
    assert all(isinstance(m, VPSignal) for m in res.markers)
```

#### Step 4.2 — 验证失败

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q -k "obv or breakout or vwap or degraded or ok_status"
```
预期：多个用例 fail（`compute_volume_price_signals` 当前恒返回空 markers；`obv_top_divergence` 断言失败、`breakout` 断言失败）。

#### Step 4.3 — 写最小实现

```python
# src/services/volume_price_signals.py 追加

def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def _detect_obv_divergence(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    close = prim["close"].astype(float).reset_index(drop=True)
    obv = _obv(close, prim["volume"].astype(float).reset_index(drop=True))
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
    out: list[VPSignal] = []
    for kind, sig_type, cmp_price, cmp_obv, direction in (
        ("high", "obv_top_divergence", lambda a, b: a > b, lambda a, b: a <= b, "bearish"),
        ("low", "obv_bottom_divergence", lambda a, b: a < b, lambda a, b: a >= b, "bullish"),
    ):
        same = [p for p in pivots if p.kind == kind]
        for prev, curr in zip(same, same[1:]):
            price_extreme = cmp_price(curr.price, prev.price)
            obv_lagging = cmp_obv(float(obv.iloc[curr.index]), float(obv.iloc[prev.index]))
            if price_extreme and obv_lagging:
                out.append(VPSignal(
                    timestamp=curr.timestamp, price=curr.price,
                    anchor=kind, direction=direction, signal_type=sig_type,
                    confidence="medium", is_daily_approx=True, is_anomalous=False,
                    reason="价格创新极值但 OBV 未同步（形态背离）",
                    threshold=float(obv.iloc[prev.index]),
                    observed_value=float(obv.iloc[curr.index]),
                ))
    return out


def _detect_breakouts(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    high = prim["high"].astype(float)
    close = prim["close"].astype(float)
    prior_max = high.rolling(config.breakout_window).max().shift(1)  # 明确不含当日
    rel_vol = prim["rel_vol"]
    out: list[VPSignal] = []
    for i in range(len(prim)):
        pm = prior_max.iloc[i]
        rv = rel_vol.iloc[i]
        if pd.isna(pm) or pd.isna(rv):
            continue
        if close.iloc[i] >= pm and rv >= config.breakout_rel_vol:
            out.append(VPSignal(
                timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
                price=float(close.iloc[i]), anchor="close", direction="bullish",
                signal_type="volume_breakout", confidence="high",
                is_daily_approx=True, is_anomalous=False,
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）",
                threshold=float(pm), observed_value=float(rv),
            ))
    return out


def _detect_shrink_pullback(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    close = prim["close"].astype(float).reset_index(drop=True)
    ma5 = prim["ma5"]
    ma20 = prim["ma20"]
    rel_vol = prim["rel_vol"]
    atr_series = atr(prim, config.atr_period).reset_index(drop=True)
    highs = [p for p in _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim) if p.kind == "high"]
    out: list[VPSignal] = []
    if not highs:
        return out
    last_high = highs[-1]
    i = len(prim) - 1
    if pd.isna(ma5.iloc[i]) or pd.isna(ma20.iloc[i]) or ma5.iloc[i] <= ma20.iloc[i]:
        return out  # 仅上升趋势
    drawdown = last_high.price - float(close.iloc[i])
    seg_rel = rel_vol.iloc[last_high.index + 1:i + 1].dropna()
    atr_now = float(atr_series.iloc[i])
    if drawdown > 0 and not seg_rel.empty and (seg_rel < config.pullback_rel_vol).all() \
            and drawdown < config.pullback_atr_mult * atr_now:
        out.append(VPSignal(
            timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
            price=float(close.iloc[i]), anchor="close", direction="bullish",
            signal_type="shrink_pullback", confidence="medium",
            is_daily_approx=True, is_anomalous=False,
            reason=f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）",
            threshold=config.pullback_atr_mult * atr_now, observed_value=drawdown,
        ))
    return out


def _anchored_vwap_signals(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    """仅锚已确认放量突破日；重夺/失守按 edge-cross 当根触发。"""
    breakouts = _detect_breakouts(prim, config)
    if not breakouts:
        return []
    anchor_ms = {b.timestamp for b in breakouts}
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    volume = prim["volume"].astype(float).reset_index(drop=True)
    typical = (high + low + close) / 3.0
    ts = [_to_epoch_ms_shanghai(prim["date"].iloc[i]) for i in range(len(prim))]
    out: list[VPSignal] = []
    for b in breakouts:
        start = ts.index(b.timestamp)
        cum_pv = 0.0
        cum_v = 0.0
        vwap = []
        for j in range(start, len(prim)):
            cum_pv += typical.iloc[j] * volume.iloc[j]
            cum_v += volume.iloc[j]
            vwap.append(cum_pv / cum_v if cum_v > 0 else np.nan)
        for off in range(1, len(vwap)):
            j = start + off
            prev_delta = close.iloc[j - 1] - vwap[off - 1]
            curr_delta = close.iloc[j] - vwap[off]
            if prev_delta < 0 <= curr_delta:
                out.append(_avwap_signal(prim, j, "anchored_vwap_reclaim", "bullish", vwap[off]))
            elif prev_delta >= 0 > curr_delta:
                out.append(_avwap_signal(prim, j, "anchored_vwap_loss", "bearish", vwap[off]))
    return out


def _avwap_signal(prim: pd.DataFrame, i: int, sig_type: str, direction: str, level: float) -> VPSignal:
    return VPSignal(
        timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[i]),
        price=float(prim["close"].astype(float).iloc[i]), anchor="close",
        direction=direction, signal_type=sig_type, confidence="medium",
        is_daily_approx=True, is_anomalous=False,
        reason="锚定突破日 AVWAP 的重夺/失守", threshold=float(level),
        observed_value=float(prim["close"].astype(float).iloc[i]),
    )
```

替换 `compute_volume_price_signals` 串联 A 类：

```python
def compute_volume_price_signals(df, *, config: VPSConfig | None = None) -> VPSResult:
    cfg = config or VPSConfig()
    norm, reason = _normalize(df, cfg)
    if reason is not None:
        return VPSResult(markers=[], status="degraded", degraded_reason=reason)
    prim = _compute_primitives(norm, cfg)

    degraded_reason = None
    if prim["rel_vol"].isna().all():
        degraded_reason = "rel_vol unavailable for all bars (vol_ma<=0/NaN)"

    markers: list[VPSignal] = []
    markers.extend(_detect_obv_divergence(prim, cfg))
    markers.extend(_detect_breakouts(prim, cfg))
    markers.extend(_detect_shrink_pullback(prim, cfg))
    markers.extend(_anchored_vwap_signals(prim, cfg))

    status = "degraded" if degraded_reason else "ok"
    return VPSResult(markers=markers, status=status, degraded_reason=degraded_reason)
```

#### Step 4.4 — 验证通过

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：全部绿（无 fail/error），新增 7 个 A 类用例通过，先前用例不回归。

#### Step 4.5 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py && \
git commit -m "feat: A 类量价信号 OBV 背离/放量突破/缩量回调/锚定 VWAP"
```

---

### Task 5 · B 类 VSA/Upthrust/Spring 降权契约 + top-k 限流

**Files**
- Test: `tests/test_volume_price_signals.py`（Modify，追加 B 类降权/不超频断言）
- Modify: `src/services/volume_price_signals.py`（新增 `_detect_vsa_bars`/`_detect_upthrust_spring`/`_limit_b_class`；`compute_volume_price_signals` 接入 B 类并限流）

B 类硬约束：置信恒 `low`、`is_daily_approx=True`、每结果集按强度（`observed_value` 绝对值）取 `top_k`；不进入 consistency 投票、不驱动 price_lines（本里程碑不产 price_lines，consistency 在 M2a；此处仅断言 B 类 marker 不改变 A 类 markers 的方向集合、数量受限）。

#### Step 5.1 — 写失败测试

```python
# tests/test_volume_price_signals.py 追加

def _b_class(markers):
    return [m for m in markers if m.signal_type.startswith("vsa_") or m.signal_type in {"upthrust", "spring"}]


def test_b_class_confidence_always_low():
    df = _trend_up_df(50)
    res = compute_volume_price_signals(df)
    for m in _b_class(res.markers):
        assert m.confidence == "low"


def test_b_class_respects_top_k_limit():
    # 制造大量 VSA 候选，断言不超过 top_k
    rows = []
    for i in range(60):
        v = 3000 if i % 2 == 0 else 200  # 高低量交替，制造大量 No Demand/No Supply
        rows.append(_bar(100, 100.2, 99.8, 100, v))
    df = _make_df(rows)
    cfg = VPSConfig(b_class_top_k=2)
    res = compute_volume_price_signals(df, config=cfg)
    assert len(_b_class(res.markers)) <= cfg.b_class_top_k


def test_b_class_does_not_change_a_class_direction_set():
    df = _trend_up_df(50)
    with_b = compute_volume_price_signals(df)
    a_only = [m for m in with_b.markers if m not in _b_class(with_b.markers)]
    # B 类移除后 A 类方向集合不变（B 不污染 A）
    a_directions = {(m.signal_type, m.direction) for m in a_only}
    recomputed = compute_volume_price_signals(df)
    a_recomputed = {
        (m.signal_type, m.direction)
        for m in recomputed.markers
        if not (m.signal_type.startswith("vsa_") or m.signal_type in {"upthrust", "spring"})
    }
    assert a_directions == a_recomputed


def test_upthrust_and_spring_reuse_swing_pivots():
    # 假突破顶（upthrust）：冲高后收回到前高之下
    pad = [_bar(100, 101, 99, 100, 1000) for _ in range(30)]
    pivot_high = [_bar(100, 108, 100, 107, 1500), _bar(107, 107.5, 105, 106, 1200),
                  _bar(106, 106.5, 104, 105, 1100)]
    upthrust = [_bar(105, 110, 104, 104, 4000)]  # 冲破 108 后收回 104 < 前高
    df = _make_df(pad + pivot_high + upthrust)
    res = compute_volume_price_signals(df, config=VPSConfig(swing_k=2))
    assert any(m.signal_type == "upthrust" and m.direction == "bearish" for m in res.markers)
```

#### Step 5.2 — 验证失败

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q -k "b_class or upthrust or spring"
```
预期：fail（当前 markers 无任何 B 类 signal_type，`test_upthrust_and_spring_reuse_swing_pivots` 断言失败、`test_b_class_confidence_always_low` 因无 B 类而 vacuous 通过但 top_k/upthrust 用例红）。

#### Step 5.3 — 写最小实现

```python
# src/services/volume_price_signals.py 追加

def _detect_vsa_bars(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    out: list[VPSignal] = []
    for i in range(len(prim)):
        rv = prim["rel_vol"].iloc[i]
        rp = prim["range_pos"].iloc[i]
        body = prim["body"].iloc[i]
        if pd.isna(rv) or bool(prim["is_limit_bar"].iloc[i]):
            continue  # 一字板/量比缺失 -> 排除
        ts = _to_epoch_ms_shanghai(prim["date"].iloc[i])
        price = float(prim["close"].astype(float).iloc[i])
        # No Demand：缩量上涨、收在上半区无力 -> 看空意味
        if rv < config.vol_shrink and body > 0 and not pd.isna(rp) and rp < 0.5:
            out.append(_vsa_signal(ts, price, "vsa_no_demand", "bearish", rv))
        # No Supply：缩量下跌、收在下半区无量承接 -> 看多意味
        elif rv < config.vol_shrink and body < 0 and not pd.isna(rp) and rp > 0.5:
            out.append(_vsa_signal(ts, price, "vsa_no_supply", "bullish", rv))
        # Stopping/Climactic：高量大幅波动后收回中部
        elif rv >= config.vol_high and not pd.isna(rp) and 0.3 <= rp <= 0.7:
            out.append(_vsa_signal(ts, price, "vsa_stopping", "neutral", rv))
        # Effort vs Result：高量但实体极小（努力无果）
        elif rv >= config.vol_high and abs(body) < (prim["spread"].iloc[i] * 0.2):
            out.append(_vsa_signal(ts, price, "vsa_effort_vs_result", "neutral", rv))
    return out


def _vsa_signal(ts: int, price: float, sig_type: str, direction: str, rv: float) -> VPSignal:
    return VPSignal(
        timestamp=ts, price=price, anchor="close", direction=direction,
        signal_type=sig_type, confidence="low", is_daily_approx=True,
        is_anomalous=False, reason="VSA 单 bar 形态（日线近似，低置信）",
        threshold=None, observed_value=float(rv),
    )


def _detect_upthrust_spring(prim: pd.DataFrame, config: VPSConfig) -> list[VPSignal]:
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    highs = [p for p in _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim) if p.kind == "high"]
    lows = [p for p in _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim) if p.kind == "low"]
    out: list[VPSignal] = []
    for i in range(len(prim)):
        ts = _to_epoch_ms_shanghai(prim["date"].iloc[i])
        prior_highs = [p for p in highs if p.index < i]
        prior_lows = [p for p in lows if p.index < i]
        if prior_highs and high.iloc[i] > prior_highs[-1].price and close.iloc[i] < prior_highs[-1].price:
            out.append(VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="high", direction="bearish",
                signal_type="upthrust", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假突破顶（Upthrust，日线近似）", threshold=float(prior_highs[-1].price),
                observed_value=float(high.iloc[i]),
            ))
        if prior_lows and low.iloc[i] < prior_lows[-1].price and close.iloc[i] > prior_lows[-1].price:
            out.append(VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="low", direction="bullish",
                signal_type="spring", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假跌破底（Spring，日线近似）", threshold=float(prior_lows[-1].price),
                observed_value=float(low.iloc[i]),
            ))
    return out


def _limit_b_class(b_markers: list[VPSignal], config: VPSConfig) -> list[VPSignal]:
    ranked = sorted(
        b_markers,
        key=lambda m: abs(m.observed_value) if m.observed_value is not None else 0.0,
        reverse=True,
    )
    return ranked[:config.b_class_top_k]
```

`compute_volume_price_signals` 接入 B 类（A 类组装后追加，限流不污染 A）：

```python
    # ... A 类 markers 组装完毕后：
    b_markers = _detect_vsa_bars(prim, cfg) + _detect_upthrust_spring(prim, cfg)
    markers.extend(_limit_b_class(b_markers, cfg))

    status = "degraded" if degraded_reason else "ok"
    return VPSResult(markers=markers, status=status, degraded_reason=degraded_reason)
```

#### Step 5.4 — 验证通过

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest tests/test_volume_price_signals.py -q
```
预期：全部绿。

#### Step 5.5 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/volume_price_signals.py tests/test_volume_price_signals.py && \
git commit -m "feat: B 类 VSA/Upthrust/Spring 降权契约与 top-k 限流"
```

---

### Task 6 · 暴露配置项 + 文档同步 + 全量门禁

**Files**
- Modify: `.env.example`（追加 VPS_* 可配项，紧邻 `volume_breakout` 策略说明段之后）
- Create: `docs/volume-price-signals.md`（引擎字段契约 / 阈值语义 / B 类降权说明）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 追加一行，扁平格式）

本 Task 无代码逻辑变更，仅配置/文档 + 全量后端门禁验证。

#### Step 6.1 — 写最小实现（配置 + 文档）

在 `.env.example` 追加（说明量价引擎可配阈值，注明与既有 `VolumeStatus` 5 日基准差异）：

```bash
# ============ 量价信号引擎（M1，volume_price_signals.py）============
# 量价八法/突破阈值，全部基于 20 日量基准（与 _analyze_volume 的 5 日 VolumeStatus 口径不同，刻意分离）。
# 价档 flat 判定半带宽（pct_chg 绝对值 <= eps 视为持平）
# VPS_PRICE_EPS=0.004
# 量档边界（rel_vol = volume / vol_ma(20).shift(1)）：low<VPS_VOL_LOW，[LOW,SHRINK) shrink，[SHRINK,UP) normal，[UP,HIGH) up，>=HIGH high
# VPS_VOL_LOW=0.7
# VPS_VOL_SHRINK=0.8
# VPS_VOL_UP=1.2
# VPS_VOL_HIGH=1.5
# swing pivot 左右确认根数（越大越稳、滞后越多）
# VPS_SWING_K=3
# 放量突破窗口 N（close >= high.rolling(N).max().shift(1)，不含当日）
# VPS_BREAKOUT_WINDOW=20
# 放量突破成交量阈值（rel_vol >= 该值）
# VPS_BREAKOUT_REL_VOL=2.0
```

`docs/volume-price-signals.md` 新建，包含：`VPSResult/VPSignal/Pivot` 字段表、`signal_type` 全集与方向语义、量价八法 5×3 真值表、未来函数防护（shift(1)/swing 滞后 k/突破不含当日/AVWAP 仅锚突破日）、鲁棒性（vol_ma<=0→rel_vol None+degraded、一字板排除、窗口不足 degraded）、B 类降权契约（置信≤low、不进 consistency、不驱动 price_lines、top-k 限流）、`VPS_*` 可配项与默认值。

`docs/CHANGELOG.md` 的 `[Unreleased]` 追加一行：

```
- [新功能] 新增量价信号引擎 volume_price_signals.py（量价八法/OBV 背离/放量突破/缩量回调/锚定 VWAP/VSA 降权）
```

#### Step 6.2 — 验证（全量门禁 + 字面一致性）

命令：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
python -m py_compile src/services/volume_price_signals.py && \
python -m pytest tests/test_volume_price_signals.py -q && \
flake8 src/services/volume_price_signals.py --select=E9,F63,F7,F82 && \
grep -q "VPS_PRICE_EPS" .env.example && echo ENV_OK && \
grep -q "量价信号引擎" docs/CHANGELOG.md && echo CHANGELOG_OK
```
预期：`pytest` 全绿、flake8 无输出（0 错误）、`ENV_OK`、`CHANGELOG_OK`。

补充离线全量回归（确认未触发其它测试回归）：
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && python -m pytest -m "not network" -q
```
预期：通过（含新文件，无新增 fail/error）。

#### Step 6.3 — Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add .env.example docs/volume-price-signals.md docs/CHANGELOG.md && \
git commit -m "docs: 量价引擎可配项与字段契约说明"
```

---

M1 完成判据：`src/services/volume_price_signals.py` 提供 `compute_volume_price_signals(df, *, config) -> VPSResult`、`find_swing_pivots`、`atr`（全仓唯一定义）；八法 15 格穷尽互斥 + 边界点全覆盖；未来函数反例（微创新高不误触 OBV 背离 / 平滑上行无 AVWAP 提前锚定 / 突破 shift(1) 不含当日）全绿；鲁棒性（vol_ma<=0→rel_vol None+degraded、一字板排除、窗口不足 degraded）全绿；B 类不污染 A 类方向集合、置信≤low、top-k 限流。本里程碑不引入 schema/端点/前端，M2a 起接入。

未验证项与风险：本计划为静态草拟，未实际执行测试（`compute_volume_price_signals` 中 `ts.index(b.timestamp)` 依赖时间锚唯一性，若同源数据存在重复 date 需在 M2a 接入端点时复核；OBV 背离用 `close` 序列找 pivot 而非 high/low 极值，与 spec "same-type swing pivot" 一致但需在实现期确认对 gap 行情的稳定性）。回滚方式：删除 `src/services/volume_price_signals.py`、`tests/test_volume_price_signals.py`、`docs/volume-price-signals.md`，还原 `.env.example`/`docs/CHANGELOG.md` 两处追加即可，无任何主流程依赖。

相关文件绝对路径：
- `/root/AI/WorkSpace/cursor/AI _Trading_System/src/services/volume_price_signals.py`（Create）
- `/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_volume_price_signals.py`（Create）
- `/root/AI/WorkSpace/cursor/AI _Trading_System/.env.example`（Modify）
- `/root/AI/WorkSpace/cursor/AI _Trading_System/docs/volume-price-signals.md`（Create）
- `/root/AI/WorkSpace/cursor/AI _Trading_System/docs/CHANGELOG.md`（Modify）

---

## M2a · 信号契约 + /signals 端点

> 本段为整体实现计划中的一个里程碑段（衔接 M1 量价引擎、M2b 价位反算器）。M2a 只产出后端信号契约 schema + `/signals` 端点（rule markers + consistency 单一定义 + LLM 最新 1 点 + status/degraded 契约 + latest-by-code 查询）。`price_lines` 三个子字段本段全部返回 `null`，由 M2b 填值。

**依赖前置（M1 已交付，本段直接消费，不在本段重新定义）：**
- `src/services/volume_price_signals.py` 提供：`compute_volume_price_signals(df, *, config: VPSConfig | None = None) -> VPSResult`；`VPSResult` 含属性 `markers: list[VPSignal]`、`status: str`（`'ok'|'degraded'`）、`degraded_reason: str | None`。
- 每个 `VPSignal`（M1 dataclass）含属性：`timestamp: int`（epoch ms, Asia/Shanghai；时间锚直接透传，**无 `.date`**）、`price: float`、`anchor: str`（`'low'|'high'|'close'`）、`direction: str`（`'bullish'|'bearish'|'neutral'`）、`signal_type: str`、`confidence: str`（`'high'|'medium'|'low'`）、`is_daily_approx: bool`、`is_anomalous: bool`、`reason: str`、`threshold: float | None`、`observed_value: float | None`。M2a marker 组装直接用 `int(sig.timestamp)`，不调 `date_str_to_epoch_ms`（后者仅供 LLM 点的 `latest_bar_date: 'YYYY-MM-DD'`）。
- M2a 不读取 `VPSignal` 上未列出的任何属性；`hit_rate/hit_sample/verified` 由 M2c 回填，本段在 marker 上统一置 `hit_rate=None, hit_sample=None, verified=False`。

### File Structure（本段）

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `api/v1/schemas/stocks.py` | API Pydantic 契约。追加 `SignalMarker`、`PriceLines`、`SignalsResponse`。不动 `KLineData/StockQuote/StockHistoryResponse`；`SniperPoints` 不在本文件、完全不碰。 | Modify |
| `src/storage.py` | `DatabaseManager` 追加 `get_latest_analysis_by_code(code) -> AnalysisHistory | None`，复用 `ix_analysis_code_time` 索引（与现有 `get_latest_analysis_by_query_id` 同范式）。 | Modify `:1749` 之后追加方法 |
| `src/services/signals_service.py` | 新建。纯服务函数：把"同源 bar 序列 + 引擎结果 + 规则代表方向 + LLM 最新 1 点"组装成 `SignalsResponse` 所需 dict。封装 consistency 单一定义、BuySignal→direction 映射、stale 判定。端点只做 HTTP 包装。 | Create |
| `api/v1/endpoints/stocks.py` | 追加 `GET /{stock_code:path}/signals` 端点；复用 `StockService.get_history_data` 取同源 bar；调用 `signals_service`。`/history` 不在本段改（M0 已处理）。 | Modify `:556` 之后追加端点 |
| `tests/test_signals_endpoint.py` | 新建。端点契约测试：ok/degraded 形状、consistency 三态 + stale/unknown、向后兼容、SignalMarker timestamp 锚定。 | Create |
| `tests/test_signals_latest_by_code.py` | 新建。`get_latest_analysis_by_code` 单测（返回最近一条、无记录返回 None、按 code 隔离）。 | Create |

**测试范式说明**：参照 `tests/test_stock_watchlist_api.py`——直接 import 端点函数、用 fake/monkeypatch 注入依赖、同步调用（端点是 `def` 不是 `async def`）。不起 `TestClient`，避免拉起整库 app fixture 与 auth。`signals_service` 的核心逻辑用纯函数单测覆盖，端点测试只验证组装与 HTTP 契约。

---

### Task 1: `get_latest_analysis_by_code` latest-by-code 查询

**Files:**
- Modify: `src/storage.py`（在 `get_latest_analysis_by_query_id` 之后，约 `:1750`）
- Test: `tests/test_signals_latest_by_code.py`

- [ ] **Step 1: Write the failing test**

```python
# -*- coding: utf-8 -*-
"""Regression tests for DatabaseManager.get_latest_analysis_by_code (M2a)."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from src.config import Config
from src.storage import DatabaseManager, AnalysisHistory


class LatestAnalysisByCodeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # 与 tests/test_analysis_history.py:setUp 同范式：经 DATABASE_PATH + reset_instance
        # 拿独立临时库。DatabaseManager.get_instance() 不接 db_url 形参，
        # 库路径只能经 Config（DATABASE_PATH）注入。
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "latest_by_code.db")
        os.environ["DATABASE_PATH"] = db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._tmp.cleanup()

    def _insert(self, code: str, advice: str, created_at: datetime) -> None:
        with self.db.session_scope() as session:
            session.add(
                AnalysisHistory(
                    code=code,
                    name=code,
                    report_type="single",
                    operation_advice=advice,
                    created_at=created_at,
                )
            )

    def test_returns_most_recent_record_for_code(self) -> None:
        base = datetime(2026, 6, 1, 9, 0, 0)
        self._insert("600519", "观望", base)
        self._insert("600519", "买入", base + timedelta(days=2))

        latest = self.db.get_latest_analysis_by_code("600519")

        self.assertIsNotNone(latest)
        self.assertEqual(latest.operation_advice, "买入")

    def test_returns_none_when_no_record(self) -> None:
        self.assertIsNone(self.db.get_latest_analysis_by_code("AAPL"))

    def test_isolated_by_code(self) -> None:
        base = datetime(2026, 6, 1, 9, 0, 0)
        self._insert("600519", "买入", base)
        self._insert("hk00700", "卖出", base + timedelta(days=1))

        latest = self.db.get_latest_analysis_by_code("600519")

        self.assertIsNotNone(latest)
        self.assertEqual(latest.operation_advice, "买入")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signals_latest_by_code.py -v`
Expected: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'get_latest_analysis_by_code'`（三个用例均 error）。

- [ ] **Step 3: Write minimal implementation**

在 `src/storage.py` 的 `get_latest_analysis_by_query_id` 方法之后追加（紧邻 `:1749` 的 `return result` 后、`get_data_range` 之前）：

```python
    def get_latest_analysis_by_code(self, code: str) -> Optional[AnalysisHistory]:
        """
        根据股票代码查询最新一条分析历史记录

        与 get_latest_analysis_by_query_id 不同，本方法按 code 检索，
        用于 /signals 端点取 LLM 最新结论（无 query_id 上下文）。
        复用 ix_analysis_code_time 复合索引（code, created_at）。

        Args:
            code: 股票代码

        Returns:
            AnalysisHistory 对象，不存在返回 None
        """
        if not code:
            return None
        with self.get_session() as session:
            result = session.execute(
                select(AnalysisHistory)
                .where(AnalysisHistory.code == code)
                .order_by(desc(AnalysisHistory.created_at))
                .limit(1)
            ).scalars().first()
            return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_signals_latest_by_code.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_signals_latest_by_code.py
git commit -m "feat: 新增 get_latest_analysis_by_code latest-by-code 查询"
```

---

### Task 2: `SignalMarker` / `PriceLines` / `SignalsResponse` 契约 schema

**Files:**
- Modify: `api/v1/schemas/stocks.py`（在 `StockHistoryResponse` 之后追加，文件末尾 `:108`）
- Test: `tests/test_signals_endpoint.py`（本 Task 仅写 schema 默认值/枚举的契约测试，端点测试在 Task 4）

- [ ] **Step 1: Write the failing test**

新建 `tests/test_signals_endpoint.py`，先只放 schema 契约用例：

```python
# -*- coding: utf-8 -*-
"""Contract tests for /signals schemas and endpoint (M2a)."""

from api.v1.schemas.stocks import PriceLines, SignalMarker, SignalsResponse


def test_signal_marker_defaults_for_m2a_unfilled_fields():
    marker = SignalMarker(
        timestamp=1717200000000,
        price=1800.0,
        anchor="close",
        direction="bullish",
        signal_type="volume_breakout",
        source="rule",
        confidence="high",
        is_daily_approx=False,
        is_anomalous=False,
        reason="放量突破 20 日新高",
    )
    # M2c 回填前默认空
    assert marker.threshold is None
    assert marker.observed_value is None
    assert marker.hit_rate is None
    assert marker.hit_sample is None
    assert marker.verified is False
    # as_of 仅 source=llm 时有值
    assert marker.as_of is None


def test_price_lines_all_null_is_valid_for_m2a():
    lines = PriceLines()
    assert lines.entry is None
    assert lines.stop is None
    assert lines.target is None


def test_signals_response_degraded_shape():
    resp = SignalsResponse(
        status="degraded",
        markers=[],
        price_lines=PriceLines(),
        consistency="unknown",
        degraded_reason="数据不足，窗口未满",
    )
    assert resp.status == "degraded"
    assert resp.markers == []
    assert resp.consistency == "unknown"
    assert resp.degraded_reason == "数据不足，窗口未满"
    # 序列化保留 price_lines 子字段（不整体省略）
    dumped = resp.model_dump()
    assert dumped["price_lines"] == {"entry": None, "stop": None, "target": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signals_endpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'SignalMarker' from 'api.v1.schemas.stocks'`（collection error）。

- [ ] **Step 3: Write minimal implementation**

在 `api/v1/schemas/stocks.py` 顶部确保 `Literal` 已导入，并在 `StockHistoryResponse` 类之后（文件末尾）追加。先把 import 行从：

```python
from typing import Optional, List
```

改为：

```python
from typing import Literal, Optional, List
```

然后在文件末尾追加：

```python
class SignalMarker(BaseModel):
    """图上买卖信号标注点（追加契约，不影响旧 SniperPoints）"""

    timestamp: int = Field(..., description="epoch ms，唯一权威时间锚（前端按 timestamp 匹配蜡烛）")
    price: float = Field(..., description="标注价位")
    anchor: Literal["low", "high", "close"] = Field(..., description="price 锚定语义")
    direction: Literal["bullish", "bearish", "neutral"] = Field(..., description="信号方向")
    signal_type: str = Field(..., description="信号类型，如 volume_breakout / obv_top_divergence / llm_advice")
    source: Literal["rule", "llm"] = Field(..., description="信号来源")
    confidence: Literal["high", "medium", "low"] = Field(..., description="置信度（可排序，B 类<=low）")
    is_daily_approx: bool = Field(..., description="是否日线近似（VSA 等 B 类为 True）")
    is_anomalous: bool = Field(..., description="是否处于一字板/涨跌停等异常 bar")
    reason: str = Field(..., description="触发依据文案（用于钻取）")
    threshold: Optional[float] = Field(None, description="触发阈值，无则 null")
    observed_value: Optional[float] = Field(None, description="实际观测值，无则 null")
    hit_rate: Optional[float] = Field(None, description="历史方向命中率（M2c 回填），无样本则 null")
    hit_sample: Optional[int] = Field(None, description="命中率样本数（M2c 回填），无则 null")
    verified: bool = Field(False, description="hit_sample 达阈值则 True（M2c 回填）")
    as_of: Optional[int] = Field(None, description="仅 source=llm：该 LLM 结论生成时间 epoch ms")


class PriceLines(BaseModel):
    """买卖价位线（M2a 全 null，M2b 由价位反算器填值）"""

    entry: Optional[float] = Field(None, description="入场价位线，反算不出为 null")
    stop: Optional[float] = Field(None, description="止损价位线，反算不出为 null")
    target: Optional[float] = Field(None, description="目标价位线，反算不出为 null")


class SignalsResponse(BaseModel):
    """/signals 端点响应契约"""

    status: Literal["ok", "degraded"] = Field(..., description="整体状态；degraded 仍返回 200 + 部分结果")
    markers: List[SignalMarker] = Field(default_factory=list, description="信号标注点（rule 逐 bar + LLM 最新 1 点）")
    price_lines: PriceLines = Field(default_factory=PriceLines, description="买卖价位线（子字段允许 null）")
    consistency: Literal["consistent", "divergent", "conflict", "unknown", "stale"] = Field(
        ..., description="量价/规则与 LLM 一致性，仅在 LLM 点邻域计算"
    )
    degraded_reason: Optional[str] = Field(None, description="status=degraded 时的原因说明")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "ok",
            "markers": [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "consistent",
            "degraded_reason": None,
        }
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_signals_endpoint.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add api/v1/schemas/stocks.py tests/test_signals_endpoint.py
git commit -m "feat: 新增 SignalMarker/PriceLines/SignalsResponse 信号契约 schema"
```

---

### Task 3: `signals_service` 组装逻辑（consistency 单一定义 + BuySignal 方向映射 + stale 判定）

**Files:**
- Create: `src/services/signals_service.py`
- Test: `tests/test_signals_service.py`（新建）

本 Task 把端点不该承担的业务逻辑收敛到纯函数，便于不依赖 HTTP/DB 直测三态 consistency 与 stale。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_signals_service.py`：

```python
# -*- coding: utf-8 -*-
"""Unit tests for signals_service consistency/stale logic (M2a)."""

from datetime import datetime, timedelta

from src.stock_analyzer import BuySignal
from src.services.signals_service import (
    buy_signal_to_direction,
    compute_consistency,
    STALE_TRADING_DAYS_DEFAULT,
)


def test_buy_signal_to_direction_mapping():
    assert buy_signal_to_direction(BuySignal.STRONG_BUY) == "bullish"
    assert buy_signal_to_direction(BuySignal.BUY) == "bullish"
    assert buy_signal_to_direction(BuySignal.HOLD) == "neutral"
    assert buy_signal_to_direction(BuySignal.WAIT) == "neutral"
    assert buy_signal_to_direction(BuySignal.SELL) == "bearish"
    assert buy_signal_to_direction(BuySignal.STRONG_SELL) == "bearish"


def test_consistency_consistent_when_same_direction():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice="买入",
        llm_created_at=datetime(2026, 6, 10),
        latest_bar_date="2026-06-12",
        trading_days_elapsed=1,
    )
    assert state == "consistent"


def test_consistency_conflict_when_opposite_directions():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice="卖出",
        llm_created_at=datetime(2026, 6, 10),
        latest_bar_date="2026-06-12",
        trading_days_elapsed=1,
    )
    assert state == "conflict"


def test_consistency_divergent_when_one_neutral_one_directional():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice="持有",
        llm_created_at=datetime(2026, 6, 10),
        latest_bar_date="2026-06-12",
        trading_days_elapsed=1,
    )
    assert state == "divergent"


def test_consistency_unknown_when_no_llm_record():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice=None,
        llm_created_at=None,
        latest_bar_date="2026-06-12",
        trading_days_elapsed=None,
    )
    assert state == "unknown"


def test_consistency_unknown_when_llm_advice_unrecognized():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice="无法识别的散文",
        llm_created_at=datetime(2026, 6, 10),
        latest_bar_date="2026-06-12",
        trading_days_elapsed=1,
    )
    assert state == "unknown"


def test_consistency_stale_when_llm_too_old():
    state = compute_consistency(
        rule_direction="bullish",
        llm_advice="买入",
        llm_created_at=datetime(2026, 5, 1),
        latest_bar_date="2026-06-12",
        trading_days_elapsed=STALE_TRADING_DAYS_DEFAULT + 1,
    )
    assert state == "stale"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signals_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.signals_service'`（collection error）。

- [ ] **Step 3: Write minimal implementation**

新建 `src/services/signals_service.py`：

```python
# -*- coding: utf-8 -*-
"""
===================================
信号端点组装服务（M2a）
===================================

职责：
1. 把 M1 量价引擎 markers 映射为 API SignalMarker。
2. 用收敛后的单个 BuySignal 作"规则代表方向"，与 LLM 最新结论计算 consistency。
3. 处理 LLM 陈旧度（stale）与未识别（unknown）。

本服务为纯函数集合，不持有数据库/网络句柄，便于直测。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from src.stock_analyzer import BuySignal
from src.report_language import infer_decision_type_from_advice

logger = logging.getLogger(__name__)

# LLM 结论超过该交易日数则视为陈旧；可由调用方覆盖
STALE_TRADING_DAYS_DEFAULT = 5

# 把无时区的本地日期统一按 Asia/Shanghai 解释（与前端 format.ts 约定一致）
_SHANGHAI_TZ = timezone(timedelta(hours=8))

# BuySignal(6 态) → 三向
_BUY_SIGNAL_DIRECTION = {
    BuySignal.STRONG_BUY: "bullish",
    BuySignal.BUY: "bullish",
    BuySignal.HOLD: "neutral",
    BuySignal.WAIT: "neutral",
    BuySignal.SELL: "bearish",
    BuySignal.STRONG_SELL: "bearish",
}

# infer_decision_type_from_advice 输出 → 三向
_ADVICE_DIRECTION = {
    "buy": "bullish",
    "hold": "neutral",
    "sell": "bearish",
}


def buy_signal_to_direction(signal: BuySignal) -> str:
    """把收敛后的 BuySignal 映射为 bullish/bearish/neutral。"""
    return _BUY_SIGNAL_DIRECTION.get(signal, "neutral")


def date_str_to_epoch_ms(date_str: str) -> int:
    """'YYYY-MM-DD' → epoch ms，统一按 Asia/Shanghai（三市场一致）。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_SHANGHAI_TZ)
    return int(dt.timestamp() * 1000)


def datetime_to_epoch_ms(dt: datetime) -> int:
    """LLM created_at（无时区默认本地库时间）→ epoch ms，按 Asia/Shanghai 解释。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_SHANGHAI_TZ)
    return int(dt.timestamp() * 1000)


def _advice_to_direction(advice: Any) -> Optional[str]:
    """复用现有 infer_decision_type_from_advice；未识别返回 None。"""
    if advice is None:
        return None
    text = str(advice).strip()
    if not text:
        return None
    # default 用一个哨兵以区分"识别为 hold" vs "无法识别"
    decision = infer_decision_type_from_advice(text, default="__unrecognized__")
    if decision == "__unrecognized__":
        return None
    return _ADVICE_DIRECTION.get(decision)


def compute_consistency(
    *,
    rule_direction: str,
    llm_advice: Any,
    llm_created_at: Optional[datetime],
    latest_bar_date: str,  # noqa: ARG001  保留以备 M2b/邻域扩展，本期不参与判定
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
) -> str:
    """
    consistency 单一定义：
    - 无 LLM 记录 / LLM 方向未识别 → unknown
    - LLM 超过 stale_threshold 个交易日 → stale
    - 两向相同（非 neutral）→ consistent
    - 一向 neutral、一向有方向 → divergent
    - 两向相反（bullish vs bearish）→ conflict
    - 其余（双 neutral）→ consistent
    """
    if llm_advice is None or llm_created_at is None:
        return "unknown"

    llm_direction = _advice_to_direction(llm_advice)
    if llm_direction is None:
        return "unknown"

    if trading_days_elapsed is not None and trading_days_elapsed > stale_threshold:
        return "stale"

    if rule_direction == llm_direction:
        return "consistent"

    pair = {rule_direction, llm_direction}
    if pair == {"bullish", "bearish"}:
        return "conflict"
    # 含 neutral 的一致/不一致
    return "divergent"
```

> 说明：`compute_consistency` 形参列表已包含 `latest_bar_date` 以保持端点调用签名稳定（M2b 引入价位邻域时复用），本期不参与判定，已用 `noqa: ARG001` 标注，非占位逻辑。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_signals_service.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/services/signals_service.py tests/test_signals_service.py
git commit -m "feat: 新增 signals_service consistency 单一定义与方向映射"
```

---

### Task 4: marker 组装函数 `build_signals_payload`（含 SignalMarker 映射 + LLM 最新 1 点 + status/degraded）

**Files:**
- Modify: `src/services/signals_service.py`（追加 `build_signals_payload`）
- Test: `tests/test_signals_service.py`（追加用例）

本 Task 把"引擎结果 + 规则代表方向 + LLM 记录"组装成端点直接 `**` 进 `SignalsResponse` 的 dict。端点本身（Task 5）只做取数 + 调用 + 异常包装。

- [ ] **Step 1: Write the failing test**

在 `tests/test_signals_service.py` 末尾追加：

```python
from types import SimpleNamespace

from src.services.signals_service import build_signals_payload


def _vpsignal(timestamp, direction="bullish", signal_type="volume_breakout"):
    # M1 VPSignal 的时间锚是 timestamp:int（epoch ms, Asia/Shanghai），无 .date。
    return SimpleNamespace(
        timestamp=timestamp,
        price=1800.0,
        anchor="close",
        direction=direction,
        signal_type=signal_type,
        confidence="high",
        is_daily_approx=False,
        is_anomalous=False,
        reason="放量突破",
        threshold=2.0,
        observed_value=2.5,
    )


def _engine_result(markers, status="ok", degraded_reason=None):
    return SimpleNamespace(markers=markers, status=status, degraded_reason=degraded_reason)


def test_build_payload_ok_maps_rule_markers_and_llm_point():
    engine = _engine_result([
        _vpsignal(date_str_to_epoch_ms("2026-06-11")),
        _vpsignal(date_str_to_epoch_ms("2026-06-12")),
    ])
    llm_record = SimpleNamespace(
        operation_advice="买入",
        created_at=datetime(2026, 6, 12),
    )
    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,
        latest_bar_date="2026-06-12",
        llm_record=llm_record,
        trading_days_elapsed=0,
    )

    assert payload["status"] == "ok"
    assert payload["consistency"] == "consistent"
    assert payload["degraded_reason"] is None
    # M2a price_lines 全 null
    assert payload["price_lines"] == {"entry": None, "stop": None, "target": None}

    rule_markers = [m for m in payload["markers"] if m["source"] == "rule"]
    llm_markers = [m for m in payload["markers"] if m["source"] == "llm"]
    assert len(rule_markers) == 2
    # LLM 本期仅 1 点
    assert len(llm_markers) == 1

    first_rule = rule_markers[0]
    # timestamp 锚定到 bar 日期（Asia/Shanghai 00:00）
    assert first_rule["timestamp"] == date_str_to_epoch_ms("2026-06-11")
    assert first_rule["source"] == "rule"
    assert first_rule["as_of"] is None
    # M2c 未回填默认
    assert first_rule["hit_rate"] is None
    assert first_rule["hit_sample"] is None
    assert first_rule["verified"] is False

    llm_marker = llm_markers[0]
    assert llm_marker["source"] == "llm"
    assert llm_marker["signal_type"] == "llm_advice"
    assert llm_marker["direction"] == "bullish"
    # LLM 点锚定最新 bar、as_of=结论生成时间
    assert llm_marker["timestamp"] == date_str_to_epoch_ms("2026-06-12")
    assert llm_marker["as_of"] == datetime_to_epoch_ms(datetime(2026, 6, 12))


def test_build_payload_degraded_keeps_200_shape_and_unknown_consistency():
    engine = _engine_result([], status="degraded", degraded_reason="窗口不足")
    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=None,
        latest_bar_date="2026-06-12",
        llm_record=None,
        trading_days_elapsed=None,
    )
    assert payload["status"] == "degraded"
    assert payload["markers"] == []
    assert payload["consistency"] == "unknown"
    assert payload["degraded_reason"] == "窗口不足"


def test_build_payload_no_llm_record_yields_unknown_but_keeps_rule_markers():
    engine = _engine_result([_vpsignal(date_str_to_epoch_ms("2026-06-12"))])
    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,
        latest_bar_date="2026-06-12",
        llm_record=None,
        trading_days_elapsed=None,
    )
    assert payload["status"] == "ok"
    assert payload["consistency"] == "unknown"
    assert len([m for m in payload["markers"] if m["source"] == "rule"]) == 1
    assert len([m for m in payload["markers"] if m["source"] == "llm"]) == 0


def test_consistency_rule_direction_from_buysignal_not_b_class_markers():
    """同一 bar 同时有 A 类与 B 类 marker 时，consistency 的 rule_direction
    只来自收敛后的单个 BuySignal；A/B 类 marker 方向都不参与 consistency 投票
    （B 类尤其不得驱动方向）。此处 A 类与 B 类 marker 均为 bearish，但收敛
    BuySignal=BUY(bullish) 且 LLM=买入(bullish) → consistency 必须 consistent。"""
    ts = date_str_to_epoch_ms("2026-06-12")
    a_marker = _vpsignal(ts, direction="bearish", signal_type="volume_breakout")
    b_marker = _vpsignal(ts, direction="bearish", signal_type="upthrust")  # B 类
    engine = _engine_result([a_marker, b_marker])
    llm_record = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))

    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,  # 收敛代表方向 = bullish
        latest_bar_date="2026-06-12",
        llm_record=llm_record,
        trading_days_elapsed=0,
    )

    # rule_direction 取自 BuySignal.BUY → bullish，与 LLM bullish 一致
    assert payload["consistency"] == "consistent"
    # 两条 rule marker（A+B）都进 markers，但都不改变 consistency
    rule_markers = [m for m in payload["markers"] if m["source"] == "rule"]
    assert len(rule_markers) == 2
    assert {m["direction"] for m in rule_markers} == {"bearish"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signals_service.py -k build_payload -v`
Expected: FAIL — `ImportError: cannot import name 'build_signals_payload'`（3 个新用例 error）。

- [ ] **Step 3: Write minimal implementation**

在 `src/services/signals_service.py` 末尾追加：

```python
def _marker_from_vpsignal(sig: Any) -> dict:
    """把 M1 VPSignal 映射为 SignalMarker dict（source=rule）。

    时间锚直接透传 M1 `VPSignal.timestamp`（epoch ms，Asia/Shanghai），
    不经 date_str_to_epoch_ms（VPSignal 无 .date 字段；date_str_to_epoch_ms
    仅供 LLM 点的 'YYYY-MM-DD' latest_bar_date 用）。
    """
    return {
        "timestamp": int(sig.timestamp),
        "price": float(sig.price),
        "anchor": sig.anchor,
        "direction": sig.direction,
        "signal_type": sig.signal_type,
        "source": "rule",
        "confidence": sig.confidence,
        "is_daily_approx": bool(sig.is_daily_approx),
        "is_anomalous": bool(sig.is_anomalous),
        "reason": sig.reason,
        "threshold": sig.threshold,
        "observed_value": sig.observed_value,
        # 以下 M2c 回填
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "as_of": None,
    }


def _llm_marker(
    *,
    llm_record: Any,
    latest_bar_date: str,
    latest_close: Optional[float],
) -> Optional[dict]:
    """LLM 最新 1 点：锚定最新 bar（时间锚 + 价位锚到该 bar 收盘）、source=llm、as_of=结论生成时间。"""
    advice = getattr(llm_record, "operation_advice", None)
    direction = _advice_to_direction(advice)
    if direction is None:
        # 方向未识别则不画 LLM 点（consistency 另行标 unknown）
        return None
    created_at = getattr(llm_record, "created_at", None)
    as_of = datetime_to_epoch_ms(created_at) if created_at is not None else None
    return {
        "timestamp": date_str_to_epoch_ms(latest_bar_date),
        # 价位锚到最新 bar 的收盘，而非 0.0 占位（latest_close 缺失才回落 0.0）
        "price": float(latest_close) if latest_close is not None else 0.0,
        "anchor": "close",
        "direction": direction,
        "signal_type": "llm_advice",
        "source": "llm",
        "confidence": "medium",
        "is_daily_approx": False,
        "is_anomalous": False,
        "reason": f"LLM 最新结论：{advice}",
        "threshold": None,
        "observed_value": None,
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "as_of": as_of,
    }


def build_signals_payload(
    *,
    engine_result: Any,
    rule_signal: Optional[BuySignal],
    latest_bar_date: str,
    latest_close: Optional[float] = None,
    llm_record: Any,
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
) -> dict:
    """
    组装 /signals 响应 dict（端点据此构造 SignalsResponse）。

    - markers：引擎逐 bar rule markers + LLM 最新 1 点（若方向可识别）。
    - price_lines：M2a 全 null，M2b 填值。
    - consistency：用收敛后的单个 BuySignal 与 LLM 最新结论计算。
    - status/degraded_reason：透传引擎结果。
    """
    markers: List[dict] = [_marker_from_vpsignal(s) for s in (engine_result.markers or [])]

    llm_advice = getattr(llm_record, "operation_advice", None) if llm_record is not None else None
    llm_created_at = getattr(llm_record, "created_at", None) if llm_record is not None else None

    if llm_record is not None:
        llm_point = _llm_marker(
            llm_record=llm_record,
            latest_bar_date=latest_bar_date,
            latest_close=latest_close,
        )
        if llm_point is not None:
            markers.append(llm_point)

    rule_direction = buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral"
    consistency = compute_consistency(
        rule_direction=rule_direction,
        llm_advice=llm_advice,
        llm_created_at=llm_created_at,
        latest_bar_date=latest_bar_date,
        trading_days_elapsed=trading_days_elapsed,
        stale_threshold=stale_threshold,
    )

    return {
        "status": engine_result.status,
        "markers": markers,
        "price_lines": {"entry": None, "stop": None, "target": None},
        "consistency": consistency,
        "degraded_reason": engine_result.degraded_reason,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_signals_service.py -v`
Expected: PASS（11 passed：Task 3 的 7 + 本 Task 的 4，含 B 类不参与 consistency 用例）。

- [ ] **Step 5: Commit**

```bash
git add src/services/signals_service.py tests/test_signals_service.py
git commit -m "feat: 新增 build_signals_payload 组装 rule markers 与 LLM 最新点"
```

---

### Task 5: `GET /{stock_code:path}/signals` 端点

**Files:**
- Modify: `api/v1/endpoints/stocks.py`（import 段 + 文件末尾 `:556` 之后追加端点）
- Test: `tests/test_signals_endpoint.py`（追加端点用例）

端点职责：取同源 bar（复用 `StockService.get_history_data`，与 `/history` 同 `days`、同丢未收盘规则）→ 转 DataFrame → 调 M1 引擎 + 调 `StockTrendAnalyzer.analyze` 出收敛 `BuySignal` → 取 latest-by-code LLM 记录 → 调 `build_signals_payload` → 构造 `SignalsResponse`。`degraded` 仍 200；硬错误用 `ErrorResponse` + `responses={}`。`degraded_reason` 为自由文本 string（本期取值如「无可用历史数据」/引擎窗口不足原因），**本期不产出特定的 `non_adjusted_series` 复权降级原因**——复权一致性检测非本期范围，`/signals` 与 `/history` 同源由构造保证（同一 `get_history_data` 路径），detection 留后续。

- [ ] **Step 1: Write the failing test**

在 `tests/test_signals_endpoint.py` 末尾追加（端点直接调用 + monkeypatch，仿 `test_stock_watchlist_api.py`）：

```python
from types import SimpleNamespace
from datetime import datetime

import pandas as pd
import pytest

import api.v1.endpoints.stocks as stocks_ep
from src.stock_analyzer import BuySignal
from src.services.signals_service import date_str_to_epoch_ms


def _fake_history_result(rows):
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "period": "daily",
        "data": rows,
    }


def _bar(date, close):
    return {
        "date": date, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 1000.0,
        "amount": 1000.0 * close, "change_percent": 0.5,
    }


def _vpsignal(timestamp):
    # M1 VPSignal 的时间锚是 timestamp:int（epoch ms, Asia/Shanghai），无 .date。
    return SimpleNamespace(
        timestamp=timestamp, price=1800.0, anchor="close", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False,
        reason="放量突破", threshold=2.0, observed_value=2.5,
    )


def _patch_common(monkeypatch, *, engine_result, rule_signal, llm_record):
    rows = [_bar("2026-06-11", 1790.0), _bar("2026-06-12", 1800.0)]
    monkeypatch.setattr(
        stocks_ep.StockService, "get_history_data",
        lambda self, stock_code, period="daily", days=120: _fake_history_result(rows),
    )
    monkeypatch.setattr(
        stocks_ep, "compute_volume_price_signals",
        lambda df, config=None: engine_result,
    )

    class _FakeAnalyzer:
        def __init__(self, *a, **k):
            pass

        def analyze(self, df, code):
            return SimpleNamespace(buy_signal=rule_signal)

    monkeypatch.setattr(stocks_ep, "StockTrendAnalyzer", _FakeAnalyzer)

    class _FakeDB:
        def get_latest_analysis_by_code(self, code):
            return llm_record

    monkeypatch.setattr(stocks_ep.DatabaseManager, "get_instance", classmethod(lambda cls: _FakeDB()))


def test_signals_endpoint_ok_shape(monkeypatch):
    engine = SimpleNamespace(
        markers=[
            _vpsignal(date_str_to_epoch_ms("2026-06-11")),
            _vpsignal(date_str_to_epoch_ms("2026-06-12")),
        ],
        status="ok", degraded_reason=None,
    )
    llm = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=llm)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert resp.status == "ok"
    assert resp.consistency == "consistent"
    assert resp.price_lines.entry is None
    assert any(m.source == "llm" for m in resp.markers)
    assert any(m.source == "rule" for m in resp.markers)


def test_signals_endpoint_degraded_returns_200_payload(monkeypatch):
    engine = SimpleNamespace(markers=[], status="degraded", degraded_reason="窗口不足")
    _patch_common(monkeypatch, engine_result=engine, rule_signal=None, llm_record=None)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    assert resp.status == "degraded"
    assert resp.markers == []
    assert resp.consistency == "unknown"
    assert resp.degraded_reason == "窗口不足"


def test_signals_endpoint_empty_history_is_degraded(monkeypatch):
    monkeypatch.setattr(
        stocks_ep.StockService, "get_history_data",
        lambda self, stock_code, period="daily", days=120: _fake_history_result([]),
    )
    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)
    assert resp.status == "degraded"
    assert resp.consistency == "unknown"
    assert resp.markers == []


def test_signals_endpoint_timestamp_anchored_to_bar(monkeypatch):
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-11"))],
        status="ok", degraded_reason=None,
    )
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=None)

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)
    rule_markers = [m for m in resp.markers if m.source == "rule"]
    assert rule_markers[0].timestamp == date_str_to_epoch_ms("2026-06-11")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_signals_endpoint.py -k endpoint -v`
Expected: FAIL — `AttributeError: module 'api.v1.endpoints.stocks' has no attribute 'get_stock_signals'`（4 个端点用例 error）。

- [ ] **Step 3: Write minimal implementation**

在 `api/v1/endpoints/stocks.py` 的 import 段补充（紧接现有 `from api.v1.schemas.stocks import (...)` 块，把 `SignalsResponse` 加入，并新增引擎/分析器/存储/服务 import）。

把：

```python
from api.v1.schemas.stocks import (
    ExtractFromImageResponse,
    ExtractItem,
    KLineData,
    StockHistoryResponse,
    StockQuote,
)
```

改为：

```python
from api.v1.schemas.stocks import (
    ExtractFromImageResponse,
    ExtractItem,
    KLineData,
    SignalsResponse,
    StockHistoryResponse,
    StockQuote,
)
```

并在 import 段末尾（`from data_provider.base import normalize_stock_code` 之后）追加（`os` 当前 stocks.py 未 import，需补；`parse_env_int`/`parse_env_float` 用于把 stale/价位 env 真正读进调用处）：

```python
import os

import pandas as pd

from src.config import parse_env_int, parse_env_float
from src.services.volume_price_signals import compute_volume_price_signals
from src.services.signals_service import build_signals_payload, STALE_TRADING_DAYS_DEFAULT
from src.stock_analyzer import StockTrendAnalyzer
from src.storage import DatabaseManager
```

然后在文件末尾（`get_stock_history` 之后）追加端点：

```python
@router.get(
    "/{stock_code:path}/signals",
    response_model=SignalsResponse,
    responses={
        200: {"description": "信号契约（含 ok/degraded）"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取股票量价/规则买卖信号",
    description=(
        "返回与 /history 同源的日线收盘级买卖信号标注：规则信号逐 bar、"
        "LLM 结论最新 1 点、量价/规则一致性与价位线（价位线由后续里程碑填值）。"
        "degraded 状态仍返回 200 + 部分结果。"
    ),
)
def get_stock_signals(
    stock_code: str,
    days: int = Query(120, ge=1, le=365, description="日历回看天数（与 /history 同源）"),
) -> SignalsResponse:
    """
    获取量价/规则买卖信号契约。

    与 /history 同源：复用 StockService.get_history_data 取同一 bar 序列，
    再叠加 M1 量价引擎 markers、收敛后的单个 BuySignal 代表方向、LLM 最新结论点。

    Args:
        stock_code: 股票代码
        days: 日历回看天数（与 /history 同源；语义同 get_daily_data）

    Returns:
        SignalsResponse：status/markers/price_lines/consistency/degraded_reason
    """
    try:
        service = StockService()
        history = service.get_history_data(stock_code=stock_code, period="daily", days=days)
        rows = history.get("data", []) or []

        # 数据不足/取数为空：degraded 200，不报错（与 /history 失败不拖垮抽屉一致）
        if not rows:
            payload = {
                "status": "degraded",
                "markers": [],
                "price_lines": {"entry": None, "stop": None, "target": None},
                "consistency": "unknown",
                "degraded_reason": "无可用历史数据",
            }
            return SignalsResponse(**payload)

        df = pd.DataFrame(rows)
        latest_bar_date = str(rows[-1].get("date"))
        # LLM 点价位锚到最新 bar 收盘（缺失则后续回落 0.0）
        _latest_close_raw = rows[-1].get("close")
        latest_close = float(_latest_close_raw) if _latest_close_raw is not None else None

        # M1 量价引擎（逐 bar markers + status/degraded）
        engine_result = compute_volume_price_signals(df)

        # 收敛后的单个 BuySignal 作"规则代表方向"
        rule_signal = None
        try:
            analyzer = StockTrendAnalyzer()
            trend_result = analyzer.analyze(df, stock_code)
            rule_signal = getattr(trend_result, "buy_signal", None)
        except Exception as exc:  # 规则信号失败不拖垮 markers
            logger.warning("规则代表方向计算失败 code=%s err=%s", stock_code, exc)

        # LLM 最新 1 条（latest-by-code）
        llm_record = None
        try:
            llm_record = DatabaseManager.get_instance().get_latest_analysis_by_code(stock_code)
        except Exception as exc:  # LLM 取数失败不拖垮 markers
            logger.warning("LLM 最新结论读取失败 code=%s err=%s", stock_code, exc)

        trading_days_elapsed = _elapsed_trading_days(llm_record, rows)

        # stale 阈值真正从 env 读取（与仓库 parse_env_int 入口一致），
        # 不再让 SIGNALS_STALE_TRADING_DAYS 停留在"写进 .env.example 却不生效"的中间态。
        stale_threshold = parse_env_int(
            os.getenv("SIGNALS_STALE_TRADING_DAYS"),
            STALE_TRADING_DAYS_DEFAULT,
            field_name="SIGNALS_STALE_TRADING_DAYS",
            minimum=1,
        )

        payload = build_signals_payload(
            engine_result=engine_result,
            rule_signal=rule_signal,
            latest_bar_date=latest_bar_date,
            latest_close=latest_close,
            llm_record=llm_record,
            trading_days_elapsed=trading_days_elapsed,
            stale_threshold=stale_threshold,
        )
        return SignalsResponse(**payload)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取信号失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取信号失败: {str(e)}",
            },
        )


def _elapsed_trading_days(llm_record, rows: list) -> Optional[int]:
    """统计 LLM 结论生成日之后、bar 序列中出现的交易日数（用于 stale 判定）。

    用同源 bar 的日期序列计数（按交易 bar 数，而非自然日），
    与价格基准契约"窗口按交易 bar 数"一致。
    """
    if llm_record is None:
        return None
    created_at = getattr(llm_record, "created_at", None)
    if created_at is None:
        return None
    cutoff = created_at.strftime("%Y-%m-%d")
    return sum(1 for r in rows if str(r.get("date")) > cutoff)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_signals_endpoint.py -v`
Expected: PASS（Task 2 的 3 + 本 Task 的 4 = 7 passed）。

- [ ] **Step 5: Commit**

```bash
git add api/v1/endpoints/stocks.py tests/test_signals_endpoint.py
git commit -m "feat: 新增 GET /stocks/{code}/signals 端点（rule+LLM 双轨与一致性契约）"
```

---

### Task 6: 向后兼容回归 + 路由注册校验 + 全段验证

**Files:**
- Test: `tests/test_signals_endpoint.py`（追加向后兼容 + 路由用例）

确认本段是纯追加：旧 `/history`、`KLineData`、`StockHistoryResponse` 行为不变，且 `/signals` 已注册到 stocks 路由。

- [ ] **Step 1: Write the failing test**

在 `tests/test_signals_endpoint.py` 末尾追加：

```python
def test_history_schema_backward_compatible():
    # 旧 schema 字段不变（追加 SignalMarker 不影响 KLineData/StockHistoryResponse）
    from api.v1.schemas.stocks import KLineData, StockHistoryResponse

    kline = KLineData(date="2026-06-12", open=1.0, high=2.0, low=0.5, close=1.5)
    assert kline.volume is None
    resp = StockHistoryResponse(stock_code="600519", period="daily")
    assert resp.data == []


def test_signals_route_registered_on_stocks_router():
    from api.v1.endpoints.stocks import router

    paths = {route.path for route in router.routes}
    assert "/{stock_code:path}/signals" in paths
    assert "/{stock_code:path}/history" in paths  # 旧路由仍在
```

- [ ] **Step 2: Run test to verify it fails (or confirm green)**

Run: `.venv/bin/python -m pytest tests/test_signals_endpoint.py -k "backward or route" -v`
Expected: PASS（这两条是断言"未破坏"的护栏；若 FAIL 说明本段误改了旧契约/未注册路由，须回到对应 Task 修正后再继续）。

- [ ] **Step 3: 全段联测**

Run:
```bash
.venv/bin/python -m pytest tests/test_signals_endpoint.py tests/test_signals_service.py tests/test_signals_latest_by_code.py -v
```
Expected: PASS（全部用例通过：endpoint 9 + service 10 + latest_by_code 3）。

- [ ] **Step 4: 编译 + 不联网回归 + CI gate**

Run:
```bash
.venv/bin/python -m py_compile api/v1/schemas/stocks.py api/v1/endpoints/stocks.py src/services/signals_service.py src/storage.py
.venv/bin/python -m pytest -m "not network" -q
./scripts/ci_gate.sh
```
Expected: `py_compile` 无输出（成功）；`pytest -m "not network"` 全绿；`ci_gate.sh` 退出码 0。

- [ ] **Step 5: 同步文档 + Commit**

更新 `docs/CHANGELOG.md` 的 `[Unreleased]` 段（扁平格式，追加一行）：

```markdown
- [新功能] 新增 GET /api/v1/stocks/{code}/signals 端点：返回与 /history 同源的规则量价信号（逐 bar）+ LLM 最新结论点 + 量价一致性（consistent/divergent/conflict/unknown/stale）+ 价位线占位（后续里程碑填值）；degraded 仍返回 200。
```

在 `.env.example` 追加信号陈旧度可配项（紧邻现有信号/分析相关配置块，若无则置于文件末尾追加注释块）：

```bash
# /signals 端点：LLM 结论超过该交易日数则一致性标记为 stale
SIGNALS_STALE_TRADING_DAYS=5
```

> 注：`/signals` 端点已在调用处用 `parse_env_int(os.getenv("SIGNALS_STALE_TRADING_DAYS"), STALE_TRADING_DAYS_DEFAULT, ...)` 真正读取该 env 并作为 `stale_threshold` 传入 `build_signals_payload`（不配置时回落模块默认 5）；`STALE_TRADING_DAYS_DEFAULT` 只作 fallback，env 配置即生效，无"写进 .env.example 却不生效"的中间态。

Commit：
```bash
git add docs/CHANGELOG.md .env.example tests/test_signals_endpoint.py
git commit -m "docs: 记录 /signals 端点契约与信号陈旧度配置"
```

---

### 本段交付说明（执行者填写模板）

- **改了什么**：新增 `SignalMarker/PriceLines/SignalsResponse` 契约、`get_latest_analysis_by_code` 查询、`signals_service` 组装逻辑、`GET /stocks/{code}/signals` 端点；`price_lines` 全 null（M2b 填）。
- **为什么这么改**：补齐 spec 5.2 信号契约 + 端点，consistency 单一定义（收敛 `BuySignal` vs LLM 最新点），复用 `infer_decision_type_from_advice` 与 `ix_analysis_code_time`，纯追加不破坏 `/history`。
- **验证情况**：`tests/test_signals_*.py` 全绿 + `pytest -m "not network"` + `ci_gate.sh`（执行后填实际结果）。
- **未验证项**：M1 引擎真实实现的端到端联调（本段对 `compute_volume_price_signals` 用 monkeypatch；M1 合入后需补一条真实引擎冒烟）。`SIGNALS_STALE_TRADING_DAYS` 已在端点调用处接线（`parse_env_int` 读取并传 `stale_threshold`），非未验证项。
- **风险点**：LLM 点价位已锚到最新 bar 收盘（`latest_close = rows[-1]["close"]` 传入 `build_signals_payload`，缺失才回落 0.0），非 0.0 占位；`_elapsed_trading_days` 用 bar 日期字符串比较计数（依赖同源 bar 升序且日期格式 `YYYY-MM-DD`）。
- **回滚方式**：删除新增端点/schema/service/查询方法与三个测试文件即恢复；均为纯追加，不影响主流程与 `/history`。

---

**自审核对（已对照 spec 5.2 / §7 测试策略 / 统一接口契约）：**
- 契约命名全部对齐：`SignalsResponse{status,markers,price_lines{entry,stop,target},consistency,degraded_reason}`、`SignalMarker` 全 18 字段、`compute_volume_price_signals(df, *, config=None)`、`build_signals_payload`、`buy_signal_to_direction`、`compute_consistency`、`get_latest_analysis_by_code`、`infer_decision_type_from_advice`——无漂移。
- 覆盖 spec §7 后端契约项：ok/degraded 形状 ✓、consistency 三态 + stale/unknown ✓、向后兼容 ✓、timestamp 锚定 ✓。
- `price_lines` 本段全 null（M2b 填）、不动 `SniperPoints`、`/history` 不改（M0 已处理）——均符合本段范围边界。
- 未引用任何未在本段或 M1 契约定义的类型/函数；无 TBD/TODO/"类似 Task N"/无代码测试占位。

相关文件绝对路径：
- spec：`/root/AI/WorkSpace/cursor/AI _Trading_System/docs/superpowers/specs/2026-06-15-kline-signals-design.md`
- 待改：`/root/AI/WorkSpace/cursor/AI _Trading_System/api/v1/schemas/stocks.py`、`/root/AI/WorkSpace/cursor/AI _Trading_System/api/v1/endpoints/stocks.py`、`/root/AI/WorkSpace/cursor/AI _Trading_System/src/storage.py`
- 待建：`/root/AI/WorkSpace/cursor/AI _Trading_System/src/services/signals_service.py`、`/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_signals_endpoint.py`、`/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_signals_service.py`、`/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_signals_latest_by_code.py`

---

## M2b · 价位数值化反算器(挂护栏)

本段在 M1 已落地的 `src/services/volume_price_signals.py`（含 `atr(df, period=14)`、`find_swing_pivots`、`normalize_ohlcv` 调用约定）基础上，新增价位反算器 `derive_price_levels(df) -> PriceLevels`，把派生的 support/resistance/current_price 写入 `result.dashboard["data_perspective"]["price_position"]`，再调用**现有** `stabilize_decision_with_structure`（`src/analyzer.py:901`）走文案护栏，并把 `entry/stop/target` 填入 `/signals` 的 `price_lines`（各可 null）。

**重要事实校正（实现以现状为准，写测试时必须对齐）：** spec 5.3 散文写"写入 `result.dashboard.price_position`"，但 `stabilize_decision_with_structure` 实际只从 `result.dashboard["data_perspective"]["price_position"]` 读取 `support_level`/`resistance_level`/`current_price`（见 `src/analyzer.py:923-939`、`fill_price_position_if_needed` 同路径 `:862-864`）。因此反算器必须写到**嵌套 `data_perspective.price_position`**，否则护栏读不到。本里程碑所有"写 price_position"均指该嵌套键。

**护栏命中的验证锚（断言走护栏非绕过）：** 护栏运行后会写 `result.dashboard["decision_stability"]`（`applied`/`capital_flow_bias`/`support`/`resistance`，见 `src/analyzer.py:1242-1250` 与 `:1294-1304`），且会按 support/resistance 改写 `operation_advice`/`decision_type`/`signal_type`。测试通过断言这些**护栏副作用**证明 entry/stop/target 不是直接旁路输出，而是 support/resistance 经 `data_perspective.price_position` → `stabilize_decision_with_structure`。

**接口契约（跨里程碑统一，禁止漂移）：**
- 反算器：`derive_price_levels(df) -> PriceLevels`；`PriceLevels` 为 `@dataclass`，字段 `entry: float | None, stop: float | None, target: float | None, risk_reward: float | None`，置于 `src/services/volume_price_signals.py`（与 `atr` 同文件）。
- 价位合理性校验：**新增** `is_invalid_price_level(*, entry, stop, target, current_price) -> bool`（**禁止复用** `src/analyzer.py:234/301` 的内嵌闭包 `_is_invalid_stop_loss`）；判无效 → 回退 ATR 派生值；ATR 也不可用 → 该子线为 `None`（隐藏该线）。
- 价位线单一权威 = 反算器数值，`source=rule`；LLM `SniperPoints` 本期不画线。
- 写护栏：`apply_price_levels_to_guard(result, df, *, fundamental_context=None) -> PriceLevels`（编排函数，置于 `src/services/volume_price_signals.py`），内部先 `derive_price_levels` → 写 `data_perspective.price_position` 的 `support_level`/`resistance_level`/`current_price` → 调 `stabilize_decision_with_structure`，返回经校验的 `PriceLevels` 供 `/signals` 组装 `price_lines`。

### File Structure

```
src/services/volume_price_signals.py   Modify  新增 PriceLevels / derive_price_levels / is_invalid_price_level / apply_price_levels_to_guard（atr 已在 M1）
api/v1/endpoints/stocks.py             Modify  /signals 端点组装 price_lines（M2a 已建端点骨架；此处填三子字段）
api/v1/schemas/stocks.py               (M2a)   PriceLines{entry,stop,target} 已定义，本段复用，不改
tests/test_volume_price_levels.py      Create  反算器数值 / 喂护栏非绕过 / 回退路径 / price_lines null
docs/CHANGELOG.md                      Modify  [Unreleased] 追加一行
.env.example                           Modify  反算器 ATR 倍数等可配项
```

---

### Task M2b-1 · 反算器数值：`derive_price_levels(df) -> PriceLevels`

基于 MA5/MA20、近 20 日高低、ATR 算 entry/stop/target/risk_reward，与图上规则信号同源（同一 `normalize_ohlcv` 后 DataFrame）。规则：`entry = 近20日低 与 MA20 取较低支撑参考`（用 MA20 与 swing low 中更贴近现价的支撑作 entry）；`stop = entry - atr_mult * ATR`（默认 `atr_mult=1.5`）；`target = entry + risk_reward_target * (entry - stop)`（默认 RR 目标 2.0）；`risk_reward = (target - entry) / (entry - stop)`。窗口不足 / ATR 不可用的子项为 `None`。

**Files**
- Modify: `src/services/volume_price_signals.py`（在 `atr` 定义之后追加 `PriceLevels`、`_DEFAULT_ATR_MULT`、`_DEFAULT_RR_TARGET`、`derive_price_levels`）
- Test: `tests/test_volume_price_levels.py`（Create）

**Step 1 — 写失败测试（真实代码）**

创建 `tests/test_volume_price_levels.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for M2b price-level back-calculator and guard wiring."""

import numpy as np
import pandas as pd
import pytest

from src.services.volume_price_signals import (
    PriceLevels,
    derive_price_levels,
)


def _uptrend_df(n: int = 60) -> pd.DataFrame:
    """Deterministic gently-rising series with stable ATR (~1.0)."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    base = np.linspace(100.0, 130.0, n)
    high = base + 1.0
    low = base - 1.0
    close = base + 0.2
    open_ = base - 0.2
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_derive_price_levels_returns_ordered_levels_with_positive_rr() -> None:
    levels = derive_price_levels(_uptrend_df())

    assert isinstance(levels, PriceLevels)
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target is not None
    # stop below entry below target for a long setup
    assert levels.stop < levels.entry < levels.target
    # risk_reward = (target-entry)/(entry-stop) must be finite and positive
    assert levels.risk_reward is not None
    assert levels.risk_reward > 0
    expected_rr = (levels.target - levels.entry) / (levels.entry - levels.stop)
    assert levels.risk_reward == pytest.approx(expected_rr, rel=1e-6)


def test_derive_price_levels_stop_is_one_atr_band_below_entry() -> None:
    df = _uptrend_df()
    levels = derive_price_levels(df)
    # ATR of the constant-amplitude series is ~2.0 (high-low band); stop sits
    # atr_mult * ATR below entry. Assert the gap is a positive multiple of ATR,
    # not an arbitrary fixed percentage.
    from src.services.volume_price_signals import atr

    last_atr = float(atr(df).iloc[-1])
    assert last_atr > 0
    gap = levels.entry - levels.stop
    assert gap == pytest.approx(1.5 * last_atr, rel=1e-6)


def test_derive_price_levels_degraded_when_window_too_short() -> None:
    short_df = _uptrend_df(n=5)
    levels = derive_price_levels(short_df)
    # Insufficient window -> ATR unavailable -> stop/target hidden, entry may
    # still resolve from available closes but stop/target must be None.
    assert levels.stop is None
    assert levels.target is None
    assert levels.risk_reward is None
```

**Step 2 — 跑命令验证失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出（导入即失败，因 `PriceLevels`/`derive_price_levels` 尚不存在）：

```
ImportError: cannot import name 'PriceLevels' from 'src.services.volume_price_signals'
...
ERROR tests/test_volume_price_levels.py
1 error in ...s
```

**Step 3 — 写最小实现（真实代码）**

在 `src/services/volume_price_signals.py` 中 `atr` 函数之后追加：

```python
from dataclasses import dataclass

# M2b price-level back-calculator tunables (see .env.example).
_DEFAULT_ATR_MULT = 1.5
_DEFAULT_RR_TARGET = 2.0
_PRICE_LEVEL_WINDOW = 20


@dataclass
class PriceLevels:
    """Back-calculated long-setup price levels (single authority, source=rule)."""

    entry: float | None
    stop: float | None
    target: float | None
    risk_reward: float | None


def _last_finite(series) -> float | None:
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN guard
        return None
    return value


def derive_price_levels(
    df,
    *,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> PriceLevels:
    """Derive entry/stop/target/risk_reward from MA5/MA20/20D-low/ATR.

    Input is the same normalize_ohlcv DataFrame that drives on-chart rule
    signals, so MA / high-low口径 stay single-source. Any sub-level that cannot
    be computed (short window, NaN ATR) is returned as None instead of guessed.
    """
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return PriceLevels(entry=None, stop=None, target=None, risk_reward=None)

    close = df["close"].astype(float)
    current_price = _last_finite(close)

    ma20 = _last_finite(close.rolling(_PRICE_LEVEL_WINDOW).mean())
    swing_low = None
    if "low" in df.columns and len(df) >= _PRICE_LEVEL_WINDOW:
        swing_low = _last_finite(
            df["low"].astype(float).rolling(_PRICE_LEVEL_WINDOW).min()
        )

    # Entry = the support reference closest to (but not above) current price.
    entry_candidates = [c for c in (ma20, swing_low) if c is not None]
    if current_price is not None:
        below = [c for c in entry_candidates if c <= current_price]
        entry = max(below) if below else (min(entry_candidates) if entry_candidates else None)
    else:
        entry = max(entry_candidates) if entry_candidates else None

    last_atr = _last_finite(atr(df))
    if entry is None or last_atr is None or last_atr <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)

    stop = entry - atr_mult * last_atr
    risk = entry - stop
    if risk <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)

    target = entry + rr_target * risk
    risk_reward = (target - entry) / risk
    return PriceLevels(entry=entry, stop=stop, target=target, risk_reward=risk_reward)
```

> 注意：若 M1 已用 `from __future__ import annotations` 或已 `import pandas as pd`，去掉重复 import；`float | None` 注解在 3.10+ 直接可用，本仓库 CI 为 3.11，无需额外导入。`dataclass` import 若文件顶部已有则合并到顶部。

**Step 4 — 跑命令验证通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出：

```
...                                                                      [100%]
3 passed in ...s
```

**Step 5 — Commit**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && git add src/services/volume_price_signals.py tests/test_volume_price_levels.py && git commit -m "feat: 新增价位反算器 derive_price_levels 基于 MA20/近20低/ATR 派生入损标"
```

---

### Task M2b-2 · 价位合理性校验 + 回退（新增函数，非复用 `_is_invalid_stop_loss`）

新增 `is_invalid_price_level`，对 entry/stop/target 做方向与有限性校验（多头 setup 要求 `stop < entry < target` 且各为有限正数）。无效 → 回退到纯 ATR 派生值（`entry=current_price`，`stop/target` 由 ATR 重算）；ATR 也不可用 → 该线 `None`（隐藏）。**明确不 import** `src/analyzer.py` 的内嵌闭包 `_is_invalid_stop_loss`。

**Files**
- Modify: `src/services/volume_price_signals.py`（追加 `is_invalid_price_level`、`_fallback_atr_levels`，并在 `derive_price_levels` 末尾接入校验回退）
- Test: `tests/test_volume_price_levels.py`（追加用例）

**Step 1 — 写失败测试（真实代码）**

在 `tests/test_volume_price_levels.py` 追加：

```python
def test_is_invalid_price_level_flags_disordered_levels() -> None:
    from src.services.volume_price_signals import is_invalid_price_level

    # stop above entry is invalid for a long setup
    assert is_invalid_price_level(entry=100.0, stop=105.0, target=120.0, current_price=101.0) is True
    # target below entry is invalid
    assert is_invalid_price_level(entry=100.0, stop=95.0, target=99.0, current_price=101.0) is True
    # non-finite is invalid
    assert is_invalid_price_level(entry=100.0, stop=float("nan"), target=120.0, current_price=101.0) is True
    # well-ordered long levels are valid
    assert is_invalid_price_level(entry=100.0, stop=95.0, target=110.0, current_price=101.0) is False


def test_derive_price_levels_falls_back_to_atr_when_entry_above_current() -> None:
    """When swing/MA entry sits above current price (invalid long entry),
    the back-calculator must fall back to ATR-anchored levels off current price,
    never emit a stop above entry."""
    import numpy as np
    import pandas as pd

    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    # Sharp final drop so MA20 / 20D-low sit ABOVE the last close.
    base = np.concatenate([np.linspace(130.0, 132.0, n - 1), [110.0]])
    df = pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base,
            "volume": np.full(n, 1_000_000.0),
        }
    )

    levels = derive_price_levels(df)
    assert levels.entry is not None
    assert levels.stop is not None
    assert levels.target is not None
    # fallback must restore long ordering
    assert levels.stop < levels.entry < levels.target
    # entry anchored on current price in fallback
    assert levels.entry == pytest.approx(float(df["close"].iloc[-1]), rel=1e-6)
```

**Step 2 — 跑命令验证失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出（`is_invalid_price_level` 导入失败 + 回退用例失败）：

```
ImportError: cannot import name 'is_invalid_price_level' from 'src.services.volume_price_signals'
...
1 error in ...s
```

**Step 3 — 写最小实现（真实代码）**

在 `src/services/volume_price_signals.py` 追加，并改造 `derive_price_levels` 末尾接入回退：

```python
import math


def is_invalid_price_level(
    *,
    entry: float | None,
    stop: float | None,
    target: float | None,
    current_price: float | None,
) -> bool:
    """Risk-sanity check for back-calculated long levels (NEW, not the
    analyzer's internal _is_invalid_stop_loss closure)."""
    for value in (entry, stop, target):
        if value is None or not math.isfinite(value) or value <= 0:
            return True
    # long-setup ordering: stop < entry < target
    if not (stop < entry < target):
        return True
    return False


def _fallback_atr_levels(
    current_price: float | None,
    last_atr: float | None,
    *,
    atr_mult: float,
    rr_target: float,
) -> PriceLevels:
    if current_price is None or last_atr is None or last_atr <= 0:
        return PriceLevels(entry=current_price, stop=None, target=None, risk_reward=None)
    entry = current_price
    stop = entry - atr_mult * last_atr
    risk = entry - stop
    if risk <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    target = entry + rr_target * risk
    return PriceLevels(entry=entry, stop=stop, target=target, risk_reward=(target - entry) / risk)
```

把 `derive_price_levels` 的 `return PriceLevels(...)`（成功分支，即 `target`/`risk_reward` 计算完成那一行）替换为带校验回退的返回：

```python
    candidate = PriceLevels(entry=entry, stop=stop, target=target, risk_reward=risk_reward)
    if is_invalid_price_level(
        entry=candidate.entry,
        stop=candidate.stop,
        target=candidate.target,
        current_price=current_price,
    ):
        return _fallback_atr_levels(
            current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target
        )
    return candidate
```

> `math` import 若文件顶部已有则合并；不要新增对 `src.analyzer` 的任何 import。

**Step 4 — 跑命令验证通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出：

```
6 passed in ...s
```

**Step 5 — Commit**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && git add src/services/volume_price_signals.py tests/test_volume_price_levels.py && git commit -m "feat: 新增价位合理性校验与 ATR 回退 隐藏不合理价位线"
```

---

### Task M2b-3 · 喂护栏（断言走 `data_perspective.price_position` 经护栏，非绕过）

新增编排函数 `apply_price_levels_to_guard`：把派生的 support/resistance/current_price 写入 `result.dashboard["data_perspective"]["price_position"]`（`support_level`/`resistance_level`/`current_price`，与 `src/analyzer.py:862-864`/`:923-939` 真实读取路径一致），再调用**现有** `stabilize_decision_with_structure`。测试断言护栏副作用（`decision_stability.applied`、按 support/resistance 改写的 `decision_type`/`operation_advice`），证明走护栏而非旁路。

**Files**
- Modify: `src/services/volume_price_signals.py`（追加 `apply_price_levels_to_guard`）
- Test: `tests/test_volume_price_levels.py`（追加用例）

**Step 1 — 写失败测试（真实代码）**

在 `tests/test_volume_price_levels.py` 追加（沿用 `tests/test_decision_stability.py` 的 `AnalysisResult` + `data_perspective.price_position` + `capital_flow` 形态）：

```python
def _fund_flow(main: float, five_day: float = 0.0) -> dict:
    return {
        "capital_flow": {
            "status": "ok",
            "data": {"stock_flow": {"main_net_inflow": main, "inflow_5d": five_day}},
        }
    }


def test_apply_price_levels_feeds_guard_via_price_position_not_bypass() -> None:
    """Buy near resistance without inflow must be downgraded by the EXISTING
    guard. We assert guard side-effects (decision_stability.applied, decision_type
    flip, support/resistance echoed) to prove levels travel through
    data_perspective.price_position -> stabilize_decision_with_structure."""
    import numpy as np
    import pandas as pd

    from src.analyzer import AnalysisResult
    from src.services.volume_price_signals import apply_price_levels_to_guard

    # current price hugging resistance -> guard should downgrade a buy
    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    base = np.linspace(100.0, 130.0, n)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": base - 0.2,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.2,
            "volume": np.full(n, 1_000_000.0),
        }
    )

    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=66,
        trend_prediction="看多",
        operation_advice="买入",
        decision_type="buy",
        report_language="zh",
        current_price=float(df["close"].iloc[-1]),
        change_pct=1.2,
        dashboard={
            "core_conclusion": {"one_sentence": "原始结论"},
            "data_perspective": {
                "price_position": {
                    "current_price": float(df["close"].iloc[-1]),
                    # resistance just above current price; support far below
                    "support_level": float(df["close"].iloc[-1]) * 0.85,
                    "resistance_level": float(df["close"].iloc[-1]) * 1.005,
                }
            },
        },
    )

    levels = apply_price_levels_to_guard(
        result, df, fundamental_context=_fund_flow(main=-1_000_000, five_day=-2_000_000)
    )

    # guard ran (not bypassed): side-effects written
    stability = result.dashboard["decision_stability"]
    assert stability["applied"] is True
    # guard read support/resistance from data_perspective.price_position
    pp = result.dashboard["data_perspective"]["price_position"]
    assert stability["resistance"] == pytest.approx(pp["resistance_level"], rel=1e-9)
    # buy near resistance without inflow -> downgraded to hold
    assert result.decision_type == "hold"
    assert result.operation_advice != "买入"
    # returned levels are the single price-line authority (source=rule)
    assert levels.entry is not None


def test_apply_price_levels_writes_levels_into_price_position() -> None:
    import numpy as np
    import pandas as pd

    from src.analyzer import AnalysisResult
    from src.services.volume_price_signals import apply_price_levels_to_guard

    df = _uptrend_df()
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=55,
        trend_prediction="震荡",
        operation_advice="持有",
        decision_type="hold",
        report_language="zh",
        current_price=float(df["close"].iloc[-1]),
        change_pct=0.0,
        dashboard={"core_conclusion": {"one_sentence": "x"}, "data_perspective": {}},
    )

    apply_price_levels_to_guard(result, df, fundamental_context=None)

    pp = result.dashboard["data_perspective"]["price_position"]
    # support/resistance populated by the back-calculator for the guard to read
    assert "support_level" in pp
    assert "resistance_level" in pp
    assert "current_price" in pp
```

**Step 2 — 跑命令验证失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出（`apply_price_levels_to_guard` 导入失败）：

```
ImportError: cannot import name 'apply_price_levels_to_guard' from 'src.services.volume_price_signals'
...
1 error in ...s
```

**Step 3 — 写最小实现（真实代码）**

在 `src/services/volume_price_signals.py` 追加（**延迟 import** `stabilize_decision_with_structure`，避免 service↔analyzer 顶层循环导入）：

```python
def apply_price_levels_to_guard(
    result,
    df,
    *,
    fundamental_context=None,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> PriceLevels:
    """Derive price levels, write support/resistance/current_price into
    result.dashboard['data_perspective']['price_position'], then call the
    EXISTING stabilize_decision_with_structure guard. Returns the validated
    PriceLevels (single price-line authority, source=rule)."""
    levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)

    if result is not None and df is not None and "close" in getattr(df, "columns", []):
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        result.dashboard = dashboard
        dp = dashboard.get("data_perspective")
        if not isinstance(dp, dict):
            dp = {}
            dashboard["data_perspective"] = dp
        pp = dp.get("price_position")
        if not isinstance(pp, dict):
            pp = {}
            dp["price_position"] = pp

        current_price = _last_finite(df["close"].astype(float))
        if current_price is not None:
            pp.setdefault("current_price", current_price)
        # stop is the structural support the文案护栏 reads; target the resistance.
        if levels.stop is not None:
            pp.setdefault("support_level", levels.stop)
        if levels.target is not None:
            pp.setdefault("resistance_level", levels.target)

    # Delayed import to avoid analyzer<->service top-level circular import.
    from src.analyzer import stabilize_decision_with_structure

    stabilize_decision_with_structure(
        result, trend_result=None, fundamental_context=fundamental_context
    )
    return levels
```

> 设计说明：`setdefault` 保证**已有真实 support/resistance（如 test1 预置的 resistance）优先**，反算器仅在缺失时补；这样 test1 的"buy near resistance"语义由预置 resistance 主导护栏，test2 的空 price_position 由反算器填充。两条用例都验证了"经 price_position 进护栏"。

**Step 4 — 跑命令验证通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q
```

预期输出：

```
8 passed in ...s
```

**Step 5 — Commit**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && git add src/services/volume_price_signals.py tests/test_volume_price_levels.py && git commit -m "feat: 价位反算器经 data_perspective.price_position 接入现有文案护栏"
```

---

### Task M2b-4 · `/signals` 填 `price_lines`（各子字段可 null）

在 M2a 已建的 `GET /api/v1/stocks/{stock_code:path}/signals` 端点里，用 `apply_price_levels_to_guard` 的返回 `PriceLevels` 组装 `PriceLines{entry, stop, target}`（各允许 null），价位线单一权威 = 反算器数值（`source=rule`），LLM SniperPoints 不画线。

**Files**
- Modify: `api/v1/endpoints/stocks.py`（`/signals` handler 内组装 `price_lines`）
- Test: `tests/test_volume_price_levels.py`（追加纯映射单测，避免端点全链网络依赖）

**Step 1 — 写失败测试（真实代码）**

先确认实现位置：M2a 端点已构造 `SignalsResponse`，本段引入一个纯映射 helper `build_price_lines(levels) -> PriceLines` 以便单测（端点调用它）。在 `tests/test_volume_price_levels.py` 追加：

```python
def test_build_price_lines_maps_levels_with_nullable_fields() -> None:
    from api.v1.endpoints.stocks import build_price_lines
    from src.services.volume_price_signals import PriceLevels

    full = build_price_lines(PriceLevels(entry=100.0, stop=95.0, target=110.0, risk_reward=2.0))
    assert full.entry == 100.0
    assert full.stop == 95.0
    assert full.target == 110.0

    partial = build_price_lines(PriceLevels(entry=100.0, stop=None, target=None, risk_reward=None))
    assert partial.entry == 100.0
    assert partial.stop is None
    assert partial.target is None

    empty = build_price_lines(None)
    assert empty.entry is None
    assert empty.stop is None
    assert empty.target is None
```

**Step 2 — 跑命令验证失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py::test_build_price_lines_maps_levels_with_nullable_fields -q
```

预期输出：

```
ImportError: cannot import name 'build_price_lines' from 'api.v1.endpoints.stocks'
...
1 error in ...s
```

**Step 3 — 写最小实现（真实代码）**

在 `api/v1/endpoints/stocks.py` 顶部已 import 的 schema 区追加 `PriceLines`（若 M2a 已 import 则跳过），并新增 helper：

```python
from api.v1.schemas.stocks import PriceLines  # M2a 已定义；若已 import 则不重复
from src.services.volume_price_signals import PriceLevels


def build_price_lines(levels: "PriceLevels | None") -> PriceLines:
    """Map back-calculated PriceLevels to the API PriceLines (each nullable).
    Single price-line authority is the rule back-calculator; LLM SniperPoints
    are not drawn as lines this milestone."""
    if levels is None:
        return PriceLines(entry=None, stop=None, target=None)
    return PriceLines(entry=levels.entry, stop=levels.stop, target=levels.target)
```

在 `/signals` handler 里，M2a 已有 `df = pd.DataFrame(rows)`（同源 bar）。**`/signals` 端点只做纯读**：调 `derive_price_levels(df)` 拿数值填 `price_lines`，不写 `price_position`、不调护栏——护栏属于分析主流程的副作用（见下方说明），不在只读端点里发生。

M2a 端点构造 `payload` 时 `price_lines` 是 `{"entry": None, "stop": None, "target": None}` 占位（见 M2a Task 5 `build_signals_payload`）。本段在 M2a 端点 `build_signals_payload(...)` 返回 dict 后、`SignalsResponse(**payload)` 之前，覆写 `price_lines`：

```python
        # /signals 纯读：直接用反算器数值填 price_lines（无副作用、不写 price_position、不调护栏）。
        # 护栏写入（price_position + stabilize_decision_with_structure）属分析主流程，不在此端点。
        # 价位反算可配项真正从 env 读取（与 parse_env_float 入口一致），不停留在
        # "写进 .env.example 却不生效"的中间态；不配置时回落函数默认 1.5 / 2.0。
        atr_mult = parse_env_float(
            os.getenv("KLINE_PRICE_LEVEL_ATR_MULT"), _DEFAULT_ATR_MULT,
            field_name="KLINE_PRICE_LEVEL_ATR_MULT", minimum=0.1,
        )
        rr_target = parse_env_float(
            os.getenv("KLINE_PRICE_LEVEL_RR_TARGET"), _DEFAULT_RR_TARGET,
            field_name="KLINE_PRICE_LEVEL_RR_TARGET", minimum=0.1,
        )
        price_levels = derive_price_levels(df, atr_mult=atr_mult, rr_target=rr_target)  # df 不足时各字段 None
        payload["price_lines"] = build_price_lines(price_levels).model_dump()
        return SignalsResponse(**payload)
```

并把 import 段补上反算器纯函数与默认常量（M2a 已 import `os`/`parse_env_float`；本段把端点改用 `derive_price_levels`，并取 `_DEFAULT_ATR_MULT`/`_DEFAULT_RR_TARGET` 作 env 缺省回落）：

```python
from src.services.volume_price_signals import (
    derive_price_levels,
    _DEFAULT_ATR_MULT,
    _DEFAULT_RR_TARGET,
)
```

> 说明：M2a `/signals` handler 内**没有** `normalized_df` / `analysis_result` / `fundamental_context` 这些变量——它只有 `df`（`pd.DataFrame(rows)`）、`engine_result`、`rule_signal`、`llm_record`。`/signals` 是只读端点，故 `price_lines` 只取 `derive_price_levels(df)` 的纯数值；`build_price_lines(None)`/字段全 None 的降级 `PriceLevels` 自然产出三字段全 null，不影响 markers。`apply_price_levels_to_guard`（写 `price_position` + 调 `stabilize_decision_with_structure`）的护栏副作用由分析主流程承担（M2b-2/M2b-3 已定义并测试该编排函数），与 `/signals` 端点解耦。

**Step 4 — 跑命令验证通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q && .venv/bin/python -m py_compile api/v1/endpoints/stocks.py
```

预期输出：

```
9 passed in ...s
```

（`py_compile` 无输出即通过。）

**Step 5 — Commit**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && git add api/v1/endpoints/stocks.py tests/test_volume_price_levels.py && git commit -m "feat: /signals 端点用反算器数值填充 price_lines 各子字段可空"
```

---

### Task M2b-5 · 文档与配置同步 + 全量回归

把反算器可配项写进 `.env.example`，CHANGELOG `[Unreleased]` 追加扁平条目，并跑后端门禁回归。

**Files**
- Modify: `.env.example`
- Modify: `docs/CHANGELOG.md`
- Test: 全量 `pytest -m "not network"` + `./scripts/ci_gate.sh`

**Step 1 — 写失败测试（守卫 .env.example 含新键）**

在 `tests/test_volume_price_levels.py` 追加（避免新配置项漂移、忘同步文档）：

```python
def test_env_example_documents_price_level_tunables() -> None:
    from pathlib import Path

    text = Path("/root/AI/WorkSpace/cursor/AI _Trading_System/.env.example").read_text(encoding="utf-8")
    assert "KLINE_PRICE_LEVEL_ATR_MULT" in text
    assert "KLINE_PRICE_LEVEL_RR_TARGET" in text
```

**Step 2 — 跑命令验证失败**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py::test_env_example_documents_price_level_tunables -q
```

预期输出：

```
assert 'KLINE_PRICE_LEVEL_ATR_MULT' in text
AssertionError
...
1 failed in ...s
```

**Step 3 — 写最小实现（真实文档/配置）**

在 `.env.example` 末尾（或量价信号相关区块）追加：

```bash
# ===== K线量价信号 · 价位反算器（M2b）=====
# 止损距离 = ATR 倍数（多头 setup，entry 下方 N 倍 ATR 为止损）
KLINE_PRICE_LEVEL_ATR_MULT=1.5
# 目标位风险回报比（target = entry + RR * (entry - stop)）
KLINE_PRICE_LEVEL_RR_TARGET=2.0
```

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段追加一行（扁平格式，禁加 `### 类目标题`）：

```markdown
- [新功能] 新增 K 线价位反算器（derive_price_levels），基于 MA20/近20日低/ATR 派生入场/止损/目标价，经 data_perspective.price_position 接入现有文案护栏，并填充 /signals 的 price_lines（各字段可空）
```

> 注：`derive_price_levels` 函数默认参数为 `atr_mult=_DEFAULT_ATR_MULT(1.5)`/`rr_target=_DEFAULT_RR_TARGET(2.0)`；`/signals` 端点（M2b-4）已在调用处用 `parse_env_float(os.getenv("KLINE_PRICE_LEVEL_ATR_MULT"), _DEFAULT_ATR_MULT, ...)` / `KLINE_PRICE_LEVEL_RR_TARGET` 读取并传参，故 `.env` 配置即生效（不配置回落默认），无"写进 .env.example 却不生效"的中间态。复用仓库现有 `parse_env_float` 入口，不新建平行配置加载器。

**Step 4 — 跑命令验证通过**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_volume_price_levels.py -q && .venv/bin/python -m pytest -m "not network" -q && ./scripts/ci_gate.sh
```

预期输出：`tests/test_volume_price_levels.py` 全绿（10 passed），`-m "not network"` 套件无新增 failure，`ci_gate.sh` 以 `0` 退出（结尾打印门禁通过摘要）。

**Step 5 — Commit**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && git add .env.example docs/CHANGELOG.md tests/test_volume_price_levels.py && git commit -m "docs: 同步价位反算器配置项与 CHANGELOG"
```

---

**M2b 依赖与边界说明（交付时随计划带出）**

- 依赖 M1 已在 `src/services/volume_price_signals.py` 落地 `atr(df, period=14) -> pd.Series`（全仓唯一定义）。若 M1 未合入，Task M2b-1 的 `from ... import atr` 会失败 —— 本段不重复定义 ATR。
- 依赖 M2a 已建 `GET /api/v1/stocks/{stock_code:path}/signals` 端点骨架、`api/v1/schemas/stocks.py` 的 `PriceLines`/`SignalsResponse`、以及 handler 内的同源 `df = pd.DataFrame(rows)`。Task M2b-4 仅在其上用 `derive_price_levels(df)` 纯读填 `price_lines`，不新建端点、不新建第二套取数。M2a `/signals` handler **无** `normalized_df`/`analysis_result`/`fundamental_context` 变量；这些只存在于分析主流程。
- 护栏副作用解耦：`/signals` 端点只读，**不**写 `price_position`、**不**调 `stabilize_decision_with_structure`。写 `data_perspective.price_position` 再调护栏的副作用属分析主流程，由 `apply_price_levels_to_guard`（M2b-2/M2b-3）承担并在其单测里以 `dashboard["decision_stability"]["applied"]`/`decision_type` 翻转断言"经护栏非绕过"——该验证锚不在 `/signals` 端点上。
- 单一权威：价位线只用反算器数值（`source=rule`）；LLM `SniperPoints` 本期不画线（spec 5.3），仅钻取面板文本展示，本段不涉及。
- 护栏读取路径以实际代码为准（`dashboard["data_perspective"]["price_position"]`），与 spec 5.3 散文的"`dashboard.price_position`"差异已在本段开头标注，按 AGENTS.md 第 6 条信任代码现状。

---

返回值附注（给编排脚本）：本段为 M2b 的 writing-plans markdown，已按 5 步 TDD 落到可执行命令与真实代码。关键文件绝对路径：
- 设计 spec：`/root/AI/WorkSpace/cursor/AI _Trading_System/docs/superpowers/specs/2026-06-15-kline-signals-design.md`
- 护栏函数：`/root/AI/WorkSpace/cursor/AI _Trading_System/src/analyzer.py:901`（读 `dashboard["data_perspective"]["price_position"]`，写 `dashboard["decision_stability"]`）
- 测试样板参照：`/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_decision_stability.py`
- 反算器落点：`/root/AI/WorkSpace/cursor/AI _Trading_System/src/services/volume_price_signals.py`（M1 创建，本段 Modify）
- 新测试文件：`/root/AI/WorkSpace/cursor/AI _Trading_System/tests/test_volume_price_levels.py`（本段 Create）

最关键的实现校正（区别于 spec 散文）：`stabilize_decision_with_structure` 只从嵌套键 `dashboard["data_perspective"]["price_position"]`（字段 `support_level`/`resistance_level`/`current_price`）读取，因此反算器必须写该嵌套路径；测试以护栏副作用 `dashboard["decision_stability"]["applied"]` 与 `decision_type` 翻转断言"经护栏非绕过"。

---

## M2c · 信号命中率回填(可信实证)

把"信号-回测同源"的最小切片前移以兑现"可信"诉求：对每类 `signal_type`，复用已落库的 `BacktestResult`（`src/storage.py:289+`，含 `direction_correct`）/ `BacktestEngine` 前向评估基元，聚合**历史方向命中率 + 样本数**，回填 `SignalMarker.hit_rate/hit_sample`；样本数达阈值（沿用回测 `eval_window_days` 默认）则 `verified=true`。**仅最小切片，不做全量滚动回测；命中率为历史统计，非未来保证。**

本段交付一个纯统计层 + 一个回填映射函数，二者均可在 M2a 端点落地前独立测试。M2c 不重定义 `SignalMarker`（那是 M2a 的接口，本段按字段名消费）。

### 跨段接口契约（本段冻结，后续 task 引用必须精确一致）

- 数据结构 `HitRate`（dataclass，`src/services/signal_hit_rate.py`）：
  - `hit_rate: float | None` — 历史方向命中率（0.0~1.0，四舍五入 4 位；无样本则 `None`）
  - `hit_sample: int` — 进入聚合的有效样本数（`direction_correct is not None` 的 `BacktestResult` 行数）
- 主入口 `backfill_signal_hit_rate(signal_type: str, code: str) -> HitRate`（`src/services/signal_hit_rate.py`）。
- 回填映射 `resolve_marker_hit_fields(signal_type: str, code: str) -> dict`（同文件），返回 `{'hit_rate': float|None, 'hit_sample': int|None, 'verified': bool}`，由 M2a marker 组装层 spread 进 `SignalMarker`。
- 阈值来源：config `signal_hit_verified_min_sample: int`（缺省回落到 `backtest_eval_window_days`），env `SIGNAL_HIT_VERIFIED_MIN_SAMPLE`。
- 新增 repo 查询：`BacktestRepository.get_completed_results_for_code(code: str, *, eval_window_days: int | None = None, engine_version: str | None = None) -> list[BacktestResult]`（`src/repositories/backtest_repo.py`，复用 `ix_backtest_code_date` 索引，仅取 `eval_status == 'completed'`）。

### 复用基元（不重写）

- 聚合口径**严格复用** `backtest_engine.py:369-370` 的现有规约：分母 = `sum(1 for r in completed if r.direction_correct is not None)`，分子 = `sum(1 for r in completed if r.direction_correct is True)`。M2c 不引入第二套命中率口径。
- `BacktestEngine.evaluate_single`（`src/core/backtest_engine.py:177`）是写库阶段已用的前向 N bar 评估基元；M2c 消费其落库产物 `BacktestResult.direction_correct`，不重新前向跑回测。

### File Structure

```
src/services/signal_hit_rate.py          (Create) HitRate / backfill_signal_hit_rate / resolve_marker_hit_fields
src/repositories/backtest_repo.py        (Modify) +get_completed_results_for_code
src/config.py                            (Modify) +signal_hit_verified_min_sample 字段与 env 解析
.env.example                             (Modify) +SIGNAL_HIT_VERIFIED_MIN_SAMPLE
tests/test_signal_hit_rate.py            (Create) 聚合/阈值置 verified/无样本 null 测试
docs/CHANGELOG.md                        (Modify) [Unreleased] 追加一行
```

---

### Task M2c-1：新增按 code 取已完成回测结果的 repo 查询

**Files**
- Modify: `src/repositories/backtest_repo.py`（在 `BacktestRepository` 类内追加方法；imports 已含 `select`、`desc`、`and_`、`BacktestResult`）
- Test: `tests/test_signal_hit_rate.py`（Create，仅本 task 的 repo 用例，后续 task 追加到同文件）

#### ① 写失败测试

Create `tests/test_signal_hit_rate.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for signal hit-rate backfill (M2c)."""

import os
import tempfile
import unittest
from datetime import date

from src.config import Config
from src.repositories.backtest_repo import BacktestRepository
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager


class SignalHitRateRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_hit_rate.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = BacktestRepository(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, eval_status, direction_correct,
             eval_window_days=10, engine_version="v1"):
        # BacktestResult.analysis_history_id 是 nullable=False 的 FK
        # （ForeignKey('analysis_history.id')），必须先落一条 AnalysisHistory 父行。
        # code 同为 nullable=False。其余 nullable=False 列均有默认值。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()  # 取得 history.id
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status=eval_status,
                    direction_correct=direction_correct,
                    eval_window_days=eval_window_days,
                    engine_version=engine_version,
                )
            )
            session.commit()

    def test_returns_only_completed_for_code(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1),
                  eval_status="completed", direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2),
                  eval_status="insufficient_data", direction_correct=None)
        self._add(code="000001", analysis_date=date(2024, 1, 3),
                  eval_status="completed", direction_correct=False)

        rows = self.repo.get_completed_results_for_code("600519")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "600519")
        self.assertEqual(rows[0].eval_status, "completed")

    def test_window_and_version_filter(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1),
                  eval_status="completed", direction_correct=True,
                  eval_window_days=10, engine_version="v1")
        self._add(code="600519", analysis_date=date(2024, 1, 2),
                  eval_status="completed", direction_correct=False,
                  eval_window_days=5, engine_version="v1")
        self._add(code="600519", analysis_date=date(2024, 1, 3),
                  eval_status="completed", direction_correct=True,
                  eval_window_days=10, engine_version="v2")

        rows = self.repo.get_completed_results_for_code(
            "600519", eval_window_days=10, engine_version="v1"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].eval_window_days, 10)
        self.assertEqual(rows[0].engine_version, "v1")


if __name__ == "__main__":
    unittest.main()
```

#### ② 跑命令验证失败

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py -q 2>&1 | tail -15
```

预期：收集到 2 个用例并失败，错误为
`AttributeError: 'BacktestRepository' object has no attribute 'get_completed_results_for_code'`。

#### ③ 写最小实现

在 `src/repositories/backtest_repo.py` 的 `BacktestRepository` 类内（紧随 `save_result` 方法之后）追加：

```python
    def get_completed_results_for_code(
        self,
        code: str,
        *,
        eval_window_days: Optional[int] = None,
        engine_version: Optional[str] = None,
    ) -> List[BacktestResult]:
        """Return completed BacktestResult rows for a single code.

        仅取 eval_status == 'completed' 的行（direction_correct 才有意义）；
        复用 ix_backtest_code_date 索引按 code 过滤。
        """
        with self.db.get_session() as session:
            conditions = [
                BacktestResult.code == code,
                BacktestResult.eval_status == "completed",
            ]
            if eval_window_days is not None:
                conditions.append(BacktestResult.eval_window_days == int(eval_window_days))
            if engine_version is not None:
                conditions.append(BacktestResult.engine_version == str(engine_version))

            query = (
                select(BacktestResult)
                .where(and_(*conditions))
                .order_by(desc(BacktestResult.analysis_date), desc(BacktestResult.evaluated_at))
            )
            return list(session.execute(query).scalars().all())
```

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py -q 2>&1 | tail -8
```

预期：`2 passed`。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/repositories/backtest_repo.py tests/test_signal_hit_rate.py && \
git commit -m "feat: 新增按 code 取已完成回测结果的查询(M2c)"
```

---

### Task M2c-2：新增 verified 样本阈值配置项

**Files**
- Modify: `src/config.py`（`Config` dataclass 字段 `backtest_eval_window_days: int = 10` 处@:891 之后追加字段；在 `_load_from_env`（`src/config.py:1100`）的 `cls(...)` 构造列表里、`backtest_eval_window_days=parse_env_int(...)`（@:1711）之后追加解析，复用现有 `parse_env_int`）
- Modify: `.env.example`（`BACKTEST_NEUTRAL_BAND_PCT=2.0`@:698 之后追加）
- Test: `tests/test_signal_hit_rate.py`（追加 config 用例）

#### ① 写失败测试

在 `tests/test_signal_hit_rate.py` 末尾（`if __name__` 之前）追加新用例类：

```python
class SignalHitVerifiedConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        Config._instance = None

    def tearDown(self) -> None:
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        Config._instance = None

    def test_default_falls_back_to_eval_window_days(self) -> None:
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        # Config 经单例 + 惰性 _load_from_env 构造：reset 后 get_instance 才重读 env。
        Config.reset_instance()
        cfg = Config.get_instance()
        self.assertEqual(
            cfg.signal_hit_verified_min_sample,
            cfg.backtest_eval_window_days,
        )

    def test_env_override(self) -> None:
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "7"
        Config.reset_instance()
        cfg = Config.get_instance()
        self.assertEqual(cfg.signal_hit_verified_min_sample, 7)
```

> 注：`Config` 无 `from_env()`/`load()` 工厂；唯一构造路径是单例 `Config.get_instance()`，其内部惰性调用 `_load_from_env()`（`src/config.py:1100`）一次读 env。测试必须先设 env、再 `reset_instance()` 清缓存、最后 `get_instance()` 触发重读。

#### ② 跑命令验证失败

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py::SignalHitVerifiedConfigTestCase -q 2>&1 | tail -15
```

预期：2 个用例失败，错误为
`AttributeError: 'Config' object has no attribute 'signal_hit_verified_min_sample'`。

#### ③ 写最小实现

在 `src/config.py:891`（`backtest_eval_window_days: int = 10`）之后追加字段：

```python
    # 信号命中率回填(M2c)：hit_sample 达此阈值则 verified=true；缺省回落到 backtest_eval_window_days
    signal_hit_verified_min_sample: int = 0
```

在 `_load_from_env`（`src/config.py:1100`）的 `cls(...)` 构造列表里、`backtest_eval_window_days=parse_env_int(...)`（@:1711）之后，紧随其后追加：

```python
            signal_hit_verified_min_sample=parse_env_int(
                os.getenv('SIGNAL_HIT_VERIFIED_MIN_SAMPLE'),
                parse_env_int(os.getenv('BACKTEST_EVAL_WINDOW_DAYS'), 10, field_name='BACKTEST_EVAL_WINDOW_DAYS', minimum=1),
                field_name='SIGNAL_HIT_VERIFIED_MIN_SAMPLE',
                minimum=1,
            ),
```

> 默认值用 `BACKTEST_EVAL_WINDOW_DAYS` 解析结果作为 fallback，保证"不配置 SIGNAL_HIT_VERIFIED_MIN_SAMPLE 时阈值 = eval_window_days"，符合 spec「沿用回测 eval_window_days 默认」。

在 `.env.example:698`（`BACKTEST_NEUTRAL_BAND_PCT=2.0`）之后追加：

```bash

# 信号命中率回填：hit_sample 达该阈值则 verified=true；不配置时回落为 BACKTEST_EVAL_WINDOW_DAYS
# SIGNAL_HIT_VERIFIED_MIN_SAMPLE=10
```

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py::SignalHitVerifiedConfigTestCase -q 2>&1 | tail -8
```

预期：`2 passed`。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/config.py .env.example tests/test_signal_hit_rate.py && \
git commit -m "feat: 新增信号命中率 verified 样本阈值配置(M2c)"
```

---

### Task M2c-3：实现 backfill_signal_hit_rate 命中率聚合

**Files**
- Create: `src/services/signal_hit_rate.py`（`HitRate` dataclass + `backfill_signal_hit_rate`）
- Test: `tests/test_signal_hit_rate.py`（追加聚合 / 无样本用例）

#### ① 写失败测试

在 `tests/test_signal_hit_rate.py` 顶部 import 区追加：

```python
from src.services.signal_hit_rate import HitRate, backfill_signal_hit_rate
```

在 `SignalHitRateRepoTestCase` 之后追加新用例类（复用其 `_add` 风格，独立 setUp/tearDown 建临时库）：

```python
class BackfillSignalHitRateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_backfill.db")
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["BACKTEST_EVAL_WINDOW_DAYS"] = "4"
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("BACKTEST_EVAL_WINDOW_DAYS", None)
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, direction_correct, eval_status="completed"):
        # 先落 AnalysisHistory 父行满足 BacktestResult.analysis_history_id（nullable=False FK）。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status=eval_status,
                    direction_correct=direction_correct,
                    eval_window_days=10,
                    engine_version="v1",
                )
            )
            session.commit()

    def test_aggregates_direction_hit_rate(self) -> None:
        # 3 correct, 1 incorrect -> 0.75 over 4 samples
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 3), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 4), direction_correct=False)

        result = backfill_signal_hit_rate("rule_score", "600519")

        self.assertIsInstance(result, HitRate)
        self.assertEqual(result.hit_sample, 4)
        self.assertAlmostEqual(result.hit_rate, 0.75)

    def test_direction_correct_none_excluded_from_sample(self) -> None:
        # completed but direction_correct None must not enter denominator
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=None)

        result = backfill_signal_hit_rate("rule_score", "600519")

        self.assertEqual(result.hit_sample, 1)
        self.assertAlmostEqual(result.hit_rate, 1.0)

    def test_no_sample_returns_null_hit_rate(self) -> None:
        result = backfill_signal_hit_rate("rule_score", "000002")

        self.assertIsNone(result.hit_rate)
        self.assertEqual(result.hit_sample, 0)
```

#### ② 跑命令验证失败

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py::BackfillSignalHitRateTestCase -q 2>&1 | tail -15
```

预期：collection 阶段即报
`ModuleNotFoundError: No module named 'src.services.signal_hit_rate'`（或 import 失败导致整文件 error）。

#### ③ 写最小实现

Create `src/services/signal_hit_rate.py`：

```python
# -*- coding: utf-8 -*-
"""信号历史方向命中率回填(M2c)。

最小切片：复用已落库的 BacktestResult.direction_correct（由 BacktestEngine 前向
N bar 评估写入）按 code 聚合历史方向命中率与样本数，回填 SignalMarker 的
hit_rate / hit_sample / verified。命中率为历史统计，非未来保证；不做全量滚动回测。

聚合口径严格对齐 src/core/backtest_engine.py 的现有规约：
  分母 = direction_correct is not None 的样本数
  分子 = direction_correct is True 的样本数
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.repositories.backtest_repo import BacktestRepository


@dataclass(frozen=True)
class HitRate:
    """单类信号的历史方向命中率聚合结果。"""

    hit_rate: Optional[float]  # 0.0~1.0，无样本则 None
    hit_sample: int            # direction_correct is not None 的样本数


def backfill_signal_hit_rate(signal_type: str, code: str) -> HitRate:
    """聚合某 code 的历史方向命中率。

    最小切片下 signal_type 不改变聚合源（统一取该 code 的 completed BacktestResult），
    保留入参以便后续按 signal_type 细分；当前仅用于契约稳定与日志归因。
    """
    repo = BacktestRepository()
    rows = repo.get_completed_results_for_code(code)

    sample = sum(1 for r in rows if r.direction_correct is not None)
    if sample == 0:
        return HitRate(hit_rate=None, hit_sample=0)

    correct = sum(1 for r in rows if r.direction_correct is True)
    return HitRate(hit_rate=round(correct / sample, 4), hit_sample=sample)
```

> `signal_type` 在最小切片中不切分聚合源（`BacktestResult` 不携带 `signal_type`，全量按 signal_type 滚动回测属本期非目标）。入参保留以冻结跨段契约，后续阶段细分时函数签名不变。本段聚合不依赖 config，**不**预先 import `get_config`（YAGNI，避免 F401 未使用告警）；threshold 在 M2c-4 的 resolve 层才用到，届时再 import。

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py::BackfillSignalHitRateTestCase -q 2>&1 | tail -8
```

预期：`3 passed`。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/signal_hit_rate.py tests/test_signal_hit_rate.py && \
git commit -m "feat: 实现信号历史方向命中率聚合(M2c)"
```

---

### Task M2c-4：实现 resolve_marker_hit_fields 阈值置 verified

**Files**
- Modify: `src/services/signal_hit_rate.py`（追加 `resolve_marker_hit_fields`）
- Test: `tests/test_signal_hit_rate.py`（追加 verified 阈值用例）

#### ① 写失败测试

在 `tests/test_signal_hit_rate.py` 的 import 区把命中率 import 行改为：

```python
from src.services.signal_hit_rate import (
    HitRate,
    backfill_signal_hit_rate,
    resolve_marker_hit_fields,
)
```

在 `BackfillSignalHitRateTestCase` 之后追加新用例类：

```python
class ResolveMarkerHitFieldsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_resolve.db")
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "3"

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, direction_correct):
        # 先落 AnalysisHistory 父行满足 BacktestResult.analysis_history_id（nullable=False FK）。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status="completed",
                    direction_correct=direction_correct,
                    eval_window_days=10,
                    engine_version="v1",
                )
            )
            session.commit()

    def test_sample_at_threshold_sets_verified_true(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 3), direction_correct=False)

        fields = resolve_marker_hit_fields("rule_score", "600519")

        self.assertEqual(fields["hit_sample"], 3)
        self.assertTrue(fields["verified"])
        self.assertAlmostEqual(fields["hit_rate"], round(2 / 3, 4))

    def test_sample_below_threshold_not_verified(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)

        fields = resolve_marker_hit_fields("rule_score", "600519")

        self.assertEqual(fields["hit_sample"], 2)
        self.assertFalse(fields["verified"])

    def test_no_sample_returns_null_fields_not_verified(self) -> None:
        fields = resolve_marker_hit_fields("rule_score", "000002")

        self.assertIsNone(fields["hit_rate"])
        self.assertIsNone(fields["hit_sample"])
        self.assertFalse(fields["verified"])
```

> `hit_sample` 在「无样本」分支返回 `None` 而非 `0`，对齐 M2a `SignalMarker.hit_sample: int | None` 契约中「无样本则 null」语义（spec 5.3b「无样本则 null」）。聚合层 `HitRate.hit_sample` 用 `0` 表示「确为零样本」，到 marker 字段层归一为 `None`，单点完成两套语义对齐，避免 M2a 端点二次判空。

#### ② 跑命令验证失败

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py::ResolveMarkerHitFieldsTestCase -q 2>&1 | tail -15
```

预期：collection 失败，`ImportError: cannot import name 'resolve_marker_hit_fields' from 'src.services.signal_hit_rate'`。

#### ③ 写最小实现

先在 `src/services/signal_hit_rate.py` 顶部的 import 区追加（resolve 层首次用到 config，M2c-3 故意未提前 import）：

```python
from src.config import get_config
```

再在 `src/services/signal_hit_rate.py` 末尾追加：

```python
def resolve_marker_hit_fields(signal_type: str, code: str) -> dict:
    """把命中率聚合结果映射为 SignalMarker 的 hit_rate/hit_sample/verified 字段。

    - 无样本：hit_rate=None, hit_sample=None（对齐 SignalMarker 契约「无样本则 null」），
      verified=False。
    - 有样本：hit_sample 达 signal_hit_verified_min_sample 阈值则 verified=True。
    """
    rate = backfill_signal_hit_rate(signal_type, code)

    if rate.hit_sample <= 0:
        return {"hit_rate": None, "hit_sample": None, "verified": False}

    config = get_config()
    min_sample = int(getattr(config, "signal_hit_verified_min_sample", 0) or 0)
    if min_sample <= 0:
        min_sample = int(getattr(config, "backtest_eval_window_days", 10))

    return {
        "hit_rate": rate.hit_rate,
        "hit_sample": rate.hit_sample,
        "verified": rate.hit_sample >= min_sample,
    }
```

> `min_sample` 二次回落（字段值为 0/缺失时回 `backtest_eval_window_days`）保证即使 config 工厂未经 env 路径构造（如旧实例化路径）也满足 spec「沿用回测 eval_window_days 默认」，与 M2c-2 的 env 默认形成双保险。

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py -q 2>&1 | tail -8
```

预期：全文件全绿、0 failed/error（M2c-1 的 2 repo 用例 + M2c-2 的 2 config 用例 + M2c-3 的 3 聚合用例 + M2c-4 的 3 verified 阈值用例；以实际收集数为准，关键是无 fail/error）。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/signal_hit_rate.py tests/test_signal_hit_rate.py && \
git commit -m "feat: 命中率达阈值置 verified 的 marker 字段映射(M2c)"
```

---

### Task M2c-5：把 `resolve_marker_hit_fields` 接入 M2a marker 组装（兑现 verified 回填）

M2c 产出的 `resolve_marker_hit_fields` 此前无人调用 → `SignalMarker.verified` 恒 `False`、`hit_rate/hit_sample` 恒 `None`。本 task 把它接进 M2a 的 `build_signals_payload`/`_marker_from_vpsignal`：对 `source=='rule'` 的 marker 调用 `resolve_marker_hit_fields(sig.signal_type, code)` 并 spread `hit_rate/hit_sample/verified`；LLM 点不回填（无回测口径）。为保持 M2a 单测纯净（不引入隐式 DB 依赖），用**可注入的 resolver 形参**：`build_signals_payload` 默认 `hit_fields_resolver=None`（不回填，保留 M2a 既有 `verified=False` 契约），`/signals` 端点显式传入 `resolve_marker_hit_fields`。

**Files**
- Modify: `src/services/signals_service.py`（`_marker_from_vpsignal` 接受可选 resolver；`build_signals_payload` 加 `code` + `hit_fields_resolver` 形参并透传）
- Modify: `api/v1/endpoints/stocks.py`（`/signals` handler 传 `code=stock_code, hit_fields_resolver=resolve_marker_hit_fields`，并补 import）
- Test: `tests/test_signals_service.py`（追加 resolver spread 用例）、`tests/test_signals_endpoint.py`（端点回归断言 verified 真被回填）

#### ① 写失败测试

在 `tests/test_signals_service.py` 末尾追加（验证 rule marker 经 resolver 回填、LLM 点不回填）：

```python
def test_build_payload_spreads_hit_fields_for_rule_markers_only():
    engine = _engine_result([_vpsignal(date_str_to_epoch_ms("2026-06-12"))])
    llm_record = SimpleNamespace(operation_advice="买入", created_at=datetime(2026, 6, 12))

    calls = []

    def _resolver(signal_type, code):
        calls.append((signal_type, code))
        return {"hit_rate": 0.6, "hit_sample": 20, "verified": True}

    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,
        latest_bar_date="2026-06-12",
        llm_record=llm_record,
        trading_days_elapsed=0,
        code="600519",
        hit_fields_resolver=_resolver,
    )

    rule_marker = next(m for m in payload["markers"] if m["source"] == "rule")
    assert rule_marker["hit_rate"] == 0.6
    assert rule_marker["hit_sample"] == 20
    assert rule_marker["verified"] is True
    # resolver 只对 rule marker 的 signal_type 调用，且带 code
    assert calls == [("volume_breakout", "600519")]

    # LLM 点不回填（无回测口径）
    llm_marker = next(m for m in payload["markers"] if m["source"] == "llm")
    assert llm_marker["hit_rate"] is None
    assert llm_marker["verified"] is False


def test_build_payload_without_resolver_keeps_m2a_defaults():
    engine = _engine_result([_vpsignal(date_str_to_epoch_ms("2026-06-12"))])
    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,
        latest_bar_date="2026-06-12",
        llm_record=None,
        trading_days_elapsed=None,
    )
    rule_marker = next(m for m in payload["markers"] if m["source"] == "rule")
    assert rule_marker["hit_rate"] is None
    assert rule_marker["hit_sample"] is None
    assert rule_marker["verified"] is False
```

并在 `tests/test_signals_endpoint.py` 追加端点级回归（resolver 真被接线、verified 真回填）：

```python
def test_signals_endpoint_backfills_verified_via_resolver(monkeypatch):
    engine = SimpleNamespace(
        markers=[_vpsignal(date_str_to_epoch_ms("2026-06-12"))],
        status="ok", degraded_reason=None,
    )
    _patch_common(monkeypatch, engine_result=engine, rule_signal=BuySignal.BUY, llm_record=None)
    # 端点内部用的 resolver 被替换为确定性桩，证明组装层确实调用了它
    monkeypatch.setattr(
        stocks_ep, "resolve_marker_hit_fields",
        lambda signal_type, code: {"hit_rate": 0.7, "hit_sample": 30, "verified": True},
    )

    resp = stocks_ep.get_stock_signals(stock_code="600519", days=120)

    rule_markers = [m for m in resp.markers if m.source == "rule"]
    assert rule_markers[0].verified is True
    assert rule_markers[0].hit_rate == 0.7
    assert rule_markers[0].hit_sample == 30
```

#### ② 跑命令验证失败

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signals_service.py -k hit_fields tests/test_signals_endpoint.py -k resolver -q 2>&1 | tail -12
```

预期：`build_signals_payload` 不接受 `hit_fields_resolver`/`code` → `TypeError: build_signals_payload() got an unexpected keyword argument`；端点用例因 `stocks_ep` 无 `resolve_marker_hit_fields` 属性而 `AttributeError`。

#### ③ 写最小实现

先把 `src/services/signals_service.py` 顶部的 `from typing import Any, List, Optional` 扩为：

```python
from typing import Any, Callable, List, Optional
```

再把 `_marker_from_vpsignal` 改为接受可选 resolver 并在 rule 分支回填：

```python
def _marker_from_vpsignal(
    sig: Any,
    *,
    code: Optional[str] = None,
    hit_fields_resolver: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """把 M1 VPSignal 映射为 SignalMarker dict（source=rule）。

    时间锚直接透传 M1 `VPSignal.timestamp`（epoch ms, Asia/Shanghai）。
    若提供 hit_fields_resolver 且有 code，则用其回填 hit_rate/hit_sample/verified
    （M2c 命中率实证）；否则保留 M2a 默认（全 None / verified=False）。
    """
    marker = {
        "timestamp": int(sig.timestamp),
        "price": float(sig.price),
        "anchor": sig.anchor,
        "direction": sig.direction,
        "signal_type": sig.signal_type,
        "source": "rule",
        "confidence": sig.confidence,
        "is_daily_approx": bool(sig.is_daily_approx),
        "is_anomalous": bool(sig.is_anomalous),
        "reason": sig.reason,
        "threshold": sig.threshold,
        "observed_value": sig.observed_value,
        # 以下默认值；rule marker 经 resolver 回填
        "hit_rate": None,
        "hit_sample": None,
        "verified": False,
        "as_of": None,
    }
    if hit_fields_resolver is not None and code:
        fields = hit_fields_resolver(sig.signal_type, code)
        marker["hit_rate"] = fields.get("hit_rate")
        marker["hit_sample"] = fields.get("hit_sample")
        marker["verified"] = bool(fields.get("verified", False))
    return marker
```

把 `build_signals_payload` 签名与 rule marker 组装改为透传 `code`/`hit_fields_resolver`：

```python
def build_signals_payload(
    *,
    engine_result: Any,
    rule_signal: Optional[BuySignal],
    latest_bar_date: str,
    latest_close: Optional[float] = None,
    llm_record: Any,
    trading_days_elapsed: Optional[int],
    stale_threshold: int = STALE_TRADING_DAYS_DEFAULT,
    code: Optional[str] = None,
    hit_fields_resolver: Optional[Callable[[str, str], dict]] = None,
) -> dict:
```

把组装 markers 的那行：

```python
    markers: List[dict] = [_marker_from_vpsignal(s) for s in (engine_result.markers or [])]
```

替换为：

```python
    markers: List[dict] = [
        _marker_from_vpsignal(s, code=code, hit_fields_resolver=hit_fields_resolver)
        for s in (engine_result.markers or [])
    ]
```

在 `api/v1/endpoints/stocks.py` 的 import 段追加（紧随 M2a 已加的 signals/storage import）：

```python
from src.services.signal_hit_rate import resolve_marker_hit_fields
```

把 `/signals` handler 内的 `build_signals_payload(...)` 调用补两个实参（`code` + `hit_fields_resolver`），保留 M2a 已有的 `latest_close`/`stale_threshold` 实参不动，最终调用如下：

```python
        payload = build_signals_payload(
            engine_result=engine_result,
            rule_signal=rule_signal,
            latest_bar_date=latest_bar_date,
            latest_close=latest_close,
            llm_record=llm_record,
            trading_days_elapsed=trading_days_elapsed,
            stale_threshold=stale_threshold,
            code=stock_code,
            hit_fields_resolver=resolve_marker_hit_fields,
        )
```

> 命中率回填失败不应拖垮 markers：`resolve_marker_hit_fields` 内部已对无样本/取数异常回落 `{None, None, False}`（M2c-4 定义）；本接线只在 rule marker 上 spread 其返回值，LLM 点不受影响。

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signals_service.py tests/test_signals_endpoint.py -q 2>&1 | tail -8 && .venv/bin/python -m py_compile src/services/signals_service.py api/v1/endpoints/stocks.py
```

预期：`tests/test_signals_service.py` + `tests/test_signals_endpoint.py` 全绿、0 failed/error（含新增 resolver 用例与既有 M2a 默认契约用例并存）；`py_compile` 无输出。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add src/services/signals_service.py api/v1/endpoints/stocks.py tests/test_signals_service.py tests/test_signals_endpoint.py && \
git commit -m "feat: /signals rule marker 经 resolve_marker_hit_fields 回填命中率与 verified(M2c)"
```

---

### Task M2c-6：CHANGELOG 与回归收口

> **非 TDD：文档/回归收口 task，以命令探针为红绿**——无新增单元测试，红=`[Unreleased]` 无对应新行 / 回归基线有 fail，绿=`grep` 命中 CHANGELOG 新行且既有回测套件全绿。

**Files**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 段，扁平格式追加一行）
- Test: 无新增；跑后端门禁回归确认无破坏

#### ① 写失败测试

本 task 为文档 + 回归收口，无新失败测试。先确认 `[Unreleased]` 段当前内容以便精确追加：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && grep -n "## \[Unreleased\]" docs/CHANGELOG.md
```

预期：输出 `[Unreleased]` 标题所在行号（用于在其下方紧贴追加，遵守扁平格式：每条独立一行 `- [类型] 描述`，禁止新增 `### 类目标题`）。

#### ② 跑命令验证失败（此处为回归基线）

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && .venv/bin/python -m pytest tests/test_signal_hit_rate.py tests/test_backtest_service.py tests/test_backtest_engine.py -q 2>&1 | tail -10
```

预期：全绿（M2c 新增为纯增量、未改回测写库/聚合既有口径，既有回测测试不受影响）。若此处出现 fail，先按 systematic-debugging 定位再继续，不得跳过。

#### ③ 写最小实现

在 `docs/CHANGELOG.md` 的 `## [Unreleased]` 标题下方，追加一行（扁平格式，不加类目标题）：

```markdown
- [新功能] 信号命中率回填(M2c)：复用 BacktestResult 历史方向命中率回填 SignalMarker 的 hit_rate/hit_sample，达样本阈值(沿用回测 eval_window_days 默认)置 verified，并接入 /signals 端点 rule marker（verified 不再恒 false）；新增 SIGNAL_HIT_VERIFIED_MIN_SAMPLE 配置
```

#### ④ 跑命令验证通过

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
.venv/bin/python -m py_compile src/services/signal_hit_rate.py src/repositories/backtest_repo.py src/config.py && \
.venv/bin/python -m pytest tests/test_signal_hit_rate.py -q 2>&1 | tail -5 && \
./scripts/ci_gate.sh 2>&1 | tail -15
```

预期：`py_compile` 无输出（成功）；`test_signal_hit_rate.py` 全绿；`ci_gate.sh` 通过（或仅出现与本段无关的既有 warning）。

#### ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && \
git add docs/CHANGELOG.md && \
git commit -m "docs: 记录信号命中率回填变更(M2c)"
```

---

### 验证矩阵（本段交付说明）

- **改了什么**：新增 `src/services/signal_hit_rate.py`（`HitRate` / `backfill_signal_hit_rate` / `resolve_marker_hit_fields`）；`BacktestRepository.get_completed_results_for_code`；config `signal_hit_verified_min_sample` + env `SIGNAL_HIT_VERIFIED_MIN_SAMPLE`；**M2c-5 把 `resolve_marker_hit_fields` 接进 M2a `build_signals_payload`/`_marker_from_vpsignal`（可注入 resolver），`/signals` 端点显式传入，rule marker 真回填 hit_rate/hit_sample/verified**；CHANGELOG 一行。
- **为什么**：兑现 spec「可信实证」——让 `SignalMarker.verified` 不再恒 false，命中率来自已落库的回测前向评估（同源、最小切片），并经 M2c-5 真正接线到端点。
- **验证情况**：`tests/test_signal_hit_rate.py` 覆盖命中率聚合、`direction_correct is None` 排除、无样本 null、阈值置 verified、阈值以下不 verified；`tests/test_signals_service.py`/`tests/test_signals_endpoint.py` 覆盖 resolver spread（rule 回填、LLM 不回填、无 resolver 保留 M2a 默认、端点 verified 真回填）；`./scripts/ci_gate.sh` + 既有回测测试回归。
- **未验证项**：命中率口径为「该 code 的历史方向命中率」近似（`BacktestResult` 无 `signal_type` 列，resolver 按 code 聚合），前端须如实标注「历史统计、非未来保证」。
- **风险点**：最小切片下 `signal_type` 不细分聚合源（`BacktestResult` 无 `signal_type` 列），命中率为「该 code 的历史 LLM/规则方向命中率」近似，前端须如实标注「历史统计、非未来保证」（spec 已明确为非目标范围）。
- **回滚方式**：删除 `src/services/signal_hit_rate.py`、`tests/test_signal_hit_rate.py`、`BacktestRepository.get_completed_results_for_code`、config 字段/env 与 CHANGELOG 行即可；均为纯增量，不影响回测写库与主流程。

---

## M2d · 前端双轨标注 + 钻取 + 价位线

> 依赖前置里程碑（本段不重复实现，仅引用其产物）：
> - **M0** 已建立：`apps/dsa-web/src/types/kline.ts` 中的 `KLine` 类型；`apps/dsa-web/src/api/stocks.ts` 中的 `getKlineHistory(code, days?)` 与 `mapKLineDataToKLine`；`apps/dsa-web/src/components/kline/KLineDrawer.tsx`（Drawer 壳 + lazy 重面板 + ErrorBoundary + chunk 兜底，仿 `ReportMarkdownDrawer`）；`apps/dsa-web/src/components/kline/KLineChartPanel.tsx`（被 `KLineDrawer` lazy 加载的重面板，**模块顶部静态 `import { dispose, init } from 'klinecharts'`** 渲染蜡烛 + 量副图，chart 实例存 `chartRef.current`）；`vite.config.ts` 已加 `'klinecharts': 'vendor-klinecharts'` 分包；`klinecharts@^9.8.12` 已在 `package.json`。
> - **M2a** 已上线 `GET /api/v1/stocks/{stock_code:path}/signals`，响应形状即下文 `SignalsResponse`。
>
> 本段只做前端 M2d 的五件事：① `stocks.ts` 新增 `getSignals` + `kline.ts` 新增 `SignalsResponse`/`SignalMarker`/`PriceLines` 契约类型；② 纯逻辑模块 `klineOverlays.ts`（双轨 glyph 描述、规则▲实心/LLM△空心、一致合并/冲突并排、B 类 `is_daily_approx` 弱化、点击 → 钻取 payload）；③ React 钻取面板 `SignalDrilldownPanel.tsx`（规则:指标值/阈值/hit_rate；LLM:advice+as_of）；④ 在 `KLineChartPanel` 内 `registerOverlay` 自绘 glyph + `onClick` 桥接 React 钻取面板 + 内置 `priceLine` 画 entry/stop/target；⑤ `/signals` 失败降级"有图无标注"。
>
> 测试约定（沿用仓库现状）：vitest + RTL，`environment: jsdom`，`globals: true`，`setupFiles: ./src/setupTests.ts`；api 测试用 `vi.mock('../index')` 替 axios（见 `src/api/__tests__/alerts.test.ts`）；重面板测试用 `vi.doMock('klinecharts', ...)` 与 `vi.doMock('../../../api/stocks', ...)` 隔离图表引擎与网络（仿 `ReportMarkdownDrawer.test.tsx` 的 `vi.resetModules` + `vi.doMock` 范式）。

### File Structure（本段触及）

```
apps/dsa-web/src/
  types/
    kline.ts                              # Modify: 追加 SignalMarker / PriceLines / SignalsResponse / Consistency / SignalSource ...
  api/
    stocks.ts                             # Modify: 追加 getSignals(code, days?)
    __tests__/
      stocks.signals.test.ts              # Create: getSignals snake→camel 映射 + 降级形状
  components/kline/
    klineOverlays.ts                      # Create: 纯逻辑——双轨 glyph 描述 + 合并/并排 + B 类弱化 + 钻取 payload
    SignalDrilldownPanel.tsx              # Create: React 钻取面板（规则/LLM 两态 + hit_rate/verified + as_of）
    KLineChartPanel.tsx                   # Modify (M0 产物): registerOverlay 自绘 + onClick 桥接 + priceLine + /signals 降级
    __tests__/
      klineOverlays.test.ts               # Create: glyph 双轨/合并/并排/B 类弱化/钻取 payload 单测
      SignalDrilldownPanel.test.tsx       # Create: 规则态/LLM 态/hit_rate verified/as_of 渲染
      KLineChartPanel.signals.test.tsx    # Create: overlay 注册、价位线、钻取打开、/signals 失败"有图无标注"
```

---

### Task M2d-1 · `kline.ts` 信号契约类型 + `stocks.ts` getSignals

对齐后端 `SignalsResponse` / `SignalMarker` / `PriceLines`，并在 `stocks.ts` 增加 `getSignals` client（snake_case 响应 → camelCase，与 `mapKLineDataToKLine`、`alerts.ts` 同风格）。

**Files**
- Modify: `apps/dsa-web/src/types/kline.ts`（M0 已建文件，追加导出类型，不动既有 `KLine`）
- Modify: `apps/dsa-web/src/api/stocks.ts`（追加 `getSignals`，复用 `apiClient`）
- Test (Create): `apps/dsa-web/src/api/__tests__/stocks.signals.test.ts`

#### 步骤 ① 写失败测试

Create `apps/dsa-web/src/api/__tests__/stocks.signals.test.ts`：

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

describe('stocksApi.getSignals', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('requests the signals endpoint with days query and maps snake_case markers to camelCase', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'ok',
        consistency: 'consistent',
        degraded_reason: null,
        price_lines: { entry: 1700.5, stop: 1620, target: 1850 },
        markers: [
          {
            timestamp: 1718323200000,
            price: 1700.5,
            anchor: 'low',
            direction: 'bullish',
            signal_type: 'volume_breakout',
            source: 'rule',
            confidence: 'high',
            is_daily_approx: false,
            is_anomalous: false,
            reason: '放量突破20日高',
            threshold: 2.0,
            observed_value: 2.4,
            hit_rate: 0.62,
            hit_sample: 18,
            verified: true,
            as_of: null,
          },
          {
            timestamp: 1718323200000,
            price: 1705,
            anchor: 'high',
            direction: 'bullish',
            signal_type: 'llm_advice',
            source: 'llm',
            confidence: 'medium',
            is_daily_approx: false,
            is_anomalous: false,
            reason: '建议买入',
            threshold: null,
            observed_value: null,
            hit_rate: null,
            hit_sample: null,
            verified: false,
            as_of: 1718236800000,
          },
        ],
      },
    });

    const result = await stocksApi.getSignals('600519', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/600519/signals', {
      params: { days: 120 },
    });
    expect(result.status).toBe('ok');
    expect(result.consistency).toBe('consistent');
    expect(result.priceLines).toEqual({ entry: 1700.5, stop: 1620, target: 1850 });
    expect(result.markers).toHaveLength(2);
    expect(result.markers[0].signalType).toBe('volume_breakout');
    expect(result.markers[0].isDailyApprox).toBe(false);
    expect(result.markers[0].observedValue).toBe(2.4);
    expect(result.markers[0].hitRate).toBe(0.62);
    expect(result.markers[0].hitSample).toBe(18);
    expect(result.markers[0].verified).toBe(true);
    expect(result.markers[1].source).toBe('llm');
    expect(result.markers[1].asOf).toBe(1718236800000);
  });

  it('omits the days param when not provided and preserves degraded shape', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'degraded',
        consistency: 'unknown',
        degraded_reason: '无可用历史数据',
        price_lines: { entry: null, stop: null, target: null },
        markers: [],
      },
    });

    const result = await stocksApi.getSignals('hk00700');

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/hk00700/signals', { params: {} });
    expect(result.status).toBe('degraded');
    expect(result.consistency).toBe('unknown');
    // 任意非空 degraded_reason 都按"有图无标注"降级；不断言特定字面量
    // （复权一致性检测非本期范围，同源由构造保证，detection 留后续）。
    expect(result.degradedReason).toBeTruthy();
    expect(result.priceLines).toEqual({ entry: null, stop: null, target: null });
    expect(result.markers).toEqual([]);
  });

  it('encodes crypto codes containing a slash for the {code:path} route', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'ok',
        consistency: 'unknown',
        degraded_reason: null,
        price_lines: { entry: null, stop: null, target: null },
        markers: [],
      },
    });

    await stocksApi.getSignals('BTC/USDT', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/signals', {
      params: { days: 120 },
    });
  });
});
```

#### 步骤 ② 跑命令验证失败

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-m2d/
cp -r "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/node_modules" /tmp/dsa-web-m2d/node_modules 2>/dev/null || (cd /tmp/dsa-web-m2d && npm ci)
cd /tmp/dsa-web-m2d && npx vitest run src/api/__tests__/stocks.signals.test.ts
```

预期输出（失败）：`TypeError: stocksApi.getSignals is not a function`（`getSignals` 尚未实现）。

> 说明：工作区路径含空格会导致 `npm ci` 残缺安装，按 MEMORY 约定在 `/tmp/dsa-web-m2d` 无空格副本跑测试；下文所有 vitest/lint/build 命令同此副本。每步实现写回原仓库路径后用 `rsync` 同步至副本再跑（见各步骤 ④）。

#### 步骤 ③ 写最小实现

在 `apps/dsa-web/src/types/kline.ts` 末尾追加（不动 M0 的 `KLine`）：

```ts
// ============ Signals contract (mirrors api/v1/schemas/stocks.py SignalsResponse) ============

export type SignalSource = 'rule' | 'llm';
export type SignalDirection = 'bullish' | 'bearish' | 'neutral';
export type SignalConfidence = 'high' | 'medium' | 'low';
export type SignalAnchor = 'low' | 'high' | 'close';
export type Consistency = 'consistent' | 'divergent' | 'conflict' | 'unknown' | 'stale';

export interface SignalMarker {
  timestamp: number; // epoch ms
  price: number;
  anchor: SignalAnchor;
  direction: SignalDirection;
  signalType: string;
  source: SignalSource;
  confidence: SignalConfidence;
  isDailyApprox: boolean;
  isAnomalous: boolean;
  reason: string;
  threshold: number | null;
  observedValue: number | null;
  hitRate: number | null;
  hitSample: number | null;
  verified: boolean;
  asOf: number | null; // epoch ms, llm only
}

export interface PriceLines {
  entry: number | null;
  stop: number | null;
  target: number | null;
}

export interface SignalsResponse {
  status: 'ok' | 'degraded';
  consistency: Consistency;
  degradedReason: string | null;
  priceLines: PriceLines;
  markers: SignalMarker[];
}
```

在 `apps/dsa-web/src/api/stocks.ts` 顶部导入并在 `stocksApi` 对象内追加方法。先在文件顶部导入区追加：

```ts
import type { SignalMarker, SignalsResponse } from '../types/kline';
```

再在 `stocksApi` 对象内（`parseImport` 之后、闭合 `}` 之前）追加：

```ts
  async getSignals(code: string, days?: number): Promise<SignalsResponse> {
    const params: { days?: number } = {};
    if (days !== undefined) {
      params.days = days;
    }
    // code 经 encodeURIComponent 兼容带 '/' 的 crypto 代码（后端 {code:path} 路由），
    // 与 getKlineHistory 同步同源。
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/signals`,
      { params },
    );
    const data = response.data as RawSignalsResponse;
    return {
      status: data.status,
      consistency: data.consistency,
      degradedReason: data.degraded_reason ?? null,
      priceLines: {
        entry: data.price_lines?.entry ?? null,
        stop: data.price_lines?.stop ?? null,
        target: data.price_lines?.target ?? null,
      },
      markers: (data.markers ?? []).map(mapSignalMarker),
    };
  },
```

并在 `stocksApi` 定义之前（`export const stocksApi` 上方）追加 raw 类型与映射 util：

```ts
type RawSignalMarker = {
  timestamp: number;
  price: number;
  anchor: SignalMarker['anchor'];
  direction: SignalMarker['direction'];
  signal_type: string;
  source: SignalMarker['source'];
  confidence: SignalMarker['confidence'];
  is_daily_approx: boolean;
  is_anomalous: boolean;
  reason: string;
  threshold: number | null;
  observed_value: number | null;
  hit_rate: number | null;
  hit_sample: number | null;
  verified: boolean;
  as_of: number | null;
};

type RawSignalsResponse = {
  status: SignalsResponse['status'];
  consistency: SignalsResponse['consistency'];
  degraded_reason: string | null;
  price_lines: { entry: number | null; stop: number | null; target: number | null } | null;
  markers: RawSignalMarker[] | null;
};

const mapSignalMarker = (raw: RawSignalMarker): SignalMarker => ({
  timestamp: raw.timestamp,
  price: raw.price,
  anchor: raw.anchor,
  direction: raw.direction,
  signalType: raw.signal_type,
  source: raw.source,
  confidence: raw.confidence,
  isDailyApprox: raw.is_daily_approx,
  isAnomalous: raw.is_anomalous,
  reason: raw.reason,
  threshold: raw.threshold ?? null,
  observedValue: raw.observed_value ?? null,
  hitRate: raw.hit_rate ?? null,
  hitSample: raw.hit_sample ?? null,
  verified: raw.verified,
  asOf: raw.as_of ?? null,
});
```

#### 步骤 ④ 跑命令验证通过

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/api/__tests__/stocks.signals.test.ts
```

预期输出：`Test Files  1 passed (1)` / `Tests  2 passed (2)`。

#### 步骤 ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/stocks.signals.test.ts
git commit -m "feat: 新增前端 signals 契约类型与 getSignals client"
```

---

### Task M2d-2 · `klineOverlays.ts` 双轨 glyph 纯逻辑

把"双轨 ▲/△ + 一致合并/冲突并排 + B 类弱化 + 点击 → 钻取 payload"全部收进**纯函数**，与 klinecharts 引擎解耦，便于单测。`KLineChartPanel` 后续只把这些描述喂给 `registerOverlay`。

**Files**
- Create: `apps/dsa-web/src/components/kline/klineOverlays.ts`
- Test (Create): `apps/dsa-web/src/components/kline/__tests__/klineOverlays.test.ts`

#### 步骤 ① 写失败测试

Create `apps/dsa-web/src/components/kline/__tests__/klineOverlays.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import type { SignalMarker } from '../../../types/kline';
import { buildSignalGlyphs, RULE_OPACITY, B_CLASS_OPACITY } from '../klineOverlays';

const ruleBull = (over: Partial<SignalMarker> = {}): SignalMarker => ({
  timestamp: 1718323200000,
  price: 1700,
  anchor: 'low',
  direction: 'bullish',
  signalType: 'volume_breakout',
  source: 'rule',
  confidence: 'high',
  isDailyApprox: false,
  isAnomalous: false,
  reason: '放量突破',
  threshold: 2.0,
  observedValue: 2.4,
  hitRate: 0.62,
  hitSample: 18,
  verified: true,
  asOf: null,
  ...over,
});

const llmBull = (over: Partial<SignalMarker> = {}): SignalMarker =>
  ruleBull({
    source: 'llm',
    signalType: 'llm_advice',
    anchor: 'high',
    price: 1705,
    confidence: 'medium',
    reason: '建议买入',
    threshold: null,
    observedValue: null,
    hitRate: null,
    hitSample: null,
    verified: false,
    asOf: 1718236800000,
    ...over,
  });

describe('buildSignalGlyphs', () => {
  it('renders a filled triangle for rule signals and a hollow triangle for llm signals', () => {
    const glyphs = buildSignalGlyphs([ruleBull(), llmBull({ timestamp: 1718409600000, price: 1720 })]);

    const rule = glyphs.find((g) => g.source === 'rule');
    const llm = glyphs.find((g) => g.source === 'llm');

    expect(rule?.shape).toBe('triangle-up');
    expect(rule?.filled).toBe(true);
    expect(llm?.shape).toBe('triangle-up');
    expect(llm?.filled).toBe(false);
  });

  it('uses triangle-down for bearish direction and neutral has no triangle direction', () => {
    const glyphs = buildSignalGlyphs([
      ruleBull({ direction: 'bearish', anchor: 'high' }),
      ruleBull({ timestamp: 1718409600000, direction: 'neutral' }),
    ]);

    expect(glyphs[0].shape).toBe('triangle-down');
    expect(glyphs[1].shape).toBe('dot');
  });

  it('merges a rule and llm signal on the same bar with same direction into one strong glyph', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([ruleBull({ timestamp: ts }), llmBull({ timestamp: ts })]);

    expect(glyphs).toHaveLength(1);
    expect(glyphs[0].mode).toBe('merged');
    expect(glyphs[0].markers).toHaveLength(2);
  });

  it('places conflicting rule and llm signals on the same bar side by side', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([
      ruleBull({ timestamp: ts, direction: 'bullish' }),
      llmBull({ timestamp: ts, direction: 'bearish' }),
    ]);

    expect(glyphs).toHaveLength(2);
    expect(glyphs.every((g) => g.mode === 'conflict')).toBe(true);
    expect(glyphs[0].offsetSlot).not.toBe(glyphs[1].offsetSlot);
  });

  it('weakens B-class (is_daily_approx) glyphs via lower opacity', () => {
    const glyphs = buildSignalGlyphs([
      ruleBull({ isDailyApprox: false }),
      ruleBull({ timestamp: 1718409600000, isDailyApprox: true, signalType: 'vsa_no_demand' }),
    ]);

    const aClass = glyphs.find((g) => !g.markers[0].isDailyApprox);
    const bClass = glyphs.find((g) => g.markers[0].isDailyApprox);

    expect(aClass?.opacity).toBe(RULE_OPACITY);
    expect(bClass?.opacity).toBe(B_CLASS_OPACITY);
    expect(B_CLASS_OPACITY).toBeLessThan(RULE_OPACITY);
  });

  it('builds a drilldown payload exposing every marker on the clicked glyph', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([ruleBull({ timestamp: ts }), llmBull({ timestamp: ts })]);

    expect(glyphs[0].drilldown.timestamp).toBe(ts);
    expect(glyphs[0].drilldown.markers).toHaveLength(2);
  });
});
```

#### 步骤 ② 跑命令验证失败

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/klineOverlays.test.ts
```

预期输出（失败）：`Error: Failed to resolve import "../klineOverlays"`（模块不存在）。

#### 步骤 ③ 写最小实现

Create `apps/dsa-web/src/components/kline/klineOverlays.ts`：

```ts
import type { SignalDirection, SignalMarker, SignalSource } from '../../types/kline';

/** Full-strength opacity for rule (A-class) glyphs. */
export const RULE_OPACITY = 1;
/** Reduced opacity for B-class (is_daily_approx) low-confidence glyphs. */
export const B_CLASS_OPACITY = 0.45;

export type GlyphShape = 'triangle-up' | 'triangle-down' | 'dot';
export type GlyphMode = 'single' | 'merged' | 'conflict';

export interface SignalDrilldownPayload {
  timestamp: number;
  markers: SignalMarker[];
}

export interface SignalGlyph {
  /** Stable id for klinecharts overlay (one overlay per glyph). */
  id: string;
  timestamp: number;
  /** Anchor price of the representative marker. */
  price: number;
  shape: GlyphShape;
  /** rule => filled triangle; llm => hollow triangle. */
  filled: boolean;
  source: SignalSource;
  direction: SignalDirection;
  opacity: number;
  mode: GlyphMode;
  /** Horizontal slot for side-by-side conflict rendering (0,1,...). */
  offsetSlot: number;
  /** Every marker represented by this glyph (1 for single, 2+ for merged). */
  markers: SignalMarker[];
  drilldown: SignalDrilldownPayload;
}

const shapeForDirection = (direction: SignalDirection): GlyphShape => {
  if (direction === 'bullish') return 'triangle-up';
  if (direction === 'bearish') return 'triangle-down';
  return 'dot';
};

const opacityForMarkers = (markers: SignalMarker[]): number =>
  markers.some((m) => m.isDailyApprox) ? B_CLASS_OPACITY : RULE_OPACITY;

const makeGlyph = (
  markers: SignalMarker[],
  mode: GlyphMode,
  offsetSlot: number,
): SignalGlyph => {
  const lead = markers[0];
  return {
    id: `${lead.timestamp}:${lead.source}:${lead.signalType}:${offsetSlot}`,
    timestamp: lead.timestamp,
    price: lead.price,
    shape: shapeForDirection(lead.direction),
    filled: lead.source === 'rule',
    source: lead.source,
    direction: lead.direction,
    opacity: opacityForMarkers(markers),
    mode,
    offsetSlot,
    markers,
    drilldown: { timestamp: lead.timestamp, markers },
  };
};

/**
 * Build dual-track glyph descriptors from signal markers.
 * - rule => filled triangle; llm => hollow triangle
 * - same bar + same direction across both tracks => one merged glyph
 * - same bar + conflicting directions => side-by-side glyphs (distinct offsetSlot)
 * - B-class (is_daily_approx) => reduced opacity
 */
export const buildSignalGlyphs = (markers: SignalMarker[]): SignalGlyph[] => {
  const byBar = new Map<number, SignalMarker[]>();
  for (const marker of markers) {
    const bucket = byBar.get(marker.timestamp);
    if (bucket) {
      bucket.push(marker);
    } else {
      byBar.set(marker.timestamp, [marker]);
    }
  }

  const glyphs: SignalGlyph[] = [];
  for (const [, barMarkers] of byBar) {
    if (barMarkers.length === 1) {
      glyphs.push(makeGlyph(barMarkers, 'single', 0));
      continue;
    }

    const directions = new Set(barMarkers.map((m) => m.direction));
    const hasRule = barMarkers.some((m) => m.source === 'rule');
    const hasLlm = barMarkers.some((m) => m.source === 'llm');

    if (directions.size === 1 && hasRule && hasLlm) {
      glyphs.push(makeGlyph(barMarkers, 'merged', 0));
      continue;
    }

    barMarkers.forEach((marker, idx) => {
      glyphs.push(makeGlyph([marker], 'conflict', idx));
    });
  }

  return glyphs;
};
```

#### 步骤 ④ 跑命令验证通过

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/klineOverlays.test.ts
```

预期输出：`Test Files  1 passed (1)` / `Tests  6 passed (6)`。

#### 步骤 ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/components/kline/klineOverlays.ts apps/dsa-web/src/components/kline/__tests__/klineOverlays.test.ts
git commit -m "feat: 新增双轨信号 glyph 纯逻辑（合并/并排/B类弱化/钻取payload）"
```

---

### Task M2d-3 · `SignalDrilldownPanel.tsx` 钻取面板

点击 glyph 后展示的 React 面板：规则信号显示 指标值/阈值/hit_rate/verified；LLM 信号显示 advice(reason)+as_of；同 bar 多 marker 全部列出。

**Files**
- Create: `apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`
- Test (Create): `apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`

#### 步骤 ① 写失败测试

Create `apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`：

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { SignalMarker } from '../../../types/kline';
import { SignalDrilldownPanel } from '../SignalDrilldownPanel';

const ruleMarker: SignalMarker = {
  timestamp: 1718323200000,
  price: 1700,
  anchor: 'low',
  direction: 'bullish',
  signalType: 'volume_breakout',
  source: 'rule',
  confidence: 'high',
  isDailyApprox: false,
  isAnomalous: false,
  reason: '放量突破20日高',
  threshold: 2.0,
  observedValue: 2.4,
  hitRate: 0.62,
  hitSample: 18,
  verified: true,
  asOf: null,
};

const llmMarker: SignalMarker = {
  ...ruleMarker,
  source: 'llm',
  signalType: 'llm_advice',
  anchor: 'high',
  confidence: 'medium',
  reason: '建议买入，回踩不破支撑',
  threshold: null,
  observedValue: null,
  hitRate: null,
  hitSample: null,
  verified: false,
  asOf: 1718236800000,
};

describe('SignalDrilldownPanel', () => {
  it('shows indicator value, threshold and hit rate for a rule marker', () => {
    render(<SignalDrilldownPanel markers={[ruleMarker]} onClose={vi.fn()} />);

    expect(screen.getByText('放量突破20日高')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-observed-value')).toHaveTextContent('2.4');
    expect(screen.getByTestId('drilldown-threshold')).toHaveTextContent('2');
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('62%');
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('18');
    expect(screen.getByTestId('drilldown-verified')).toHaveTextContent('已验证');
  });

  it('shows advice and as_of for an llm marker and marks unverified hit rate', () => {
    render(<SignalDrilldownPanel markers={[llmMarker]} onClose={vi.fn()} />);

    expect(screen.getByText('建议买入，回踩不破支撑')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-as-of')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('暂无样本');
    expect(screen.getByTestId('drilldown-verified')).toHaveTextContent('未验证');
  });

  it('lists both markers when a merged/conflict glyph is opened and closes via the handler', () => {
    const onClose = vi.fn();
    render(<SignalDrilldownPanel markers={[ruleMarker, llmMarker]} onClose={onClose} />);

    expect(screen.getAllByTestId('drilldown-marker')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: '关闭依据' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
```

#### 步骤 ② 跑命令验证失败

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/SignalDrilldownPanel.test.tsx
```

预期输出（失败）：`Error: Failed to resolve import "../SignalDrilldownPanel"`。

#### 步骤 ③ 写最小实现

Create `apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`：

```tsx
import type React from 'react';
import type { SignalMarker } from '../../types/kline';
import { cn } from '../../utils/cn';

interface SignalDrilldownPanelProps {
  markers: SignalMarker[];
  onClose: () => void;
}

const formatNumber = (value: number | null): string =>
  value === null || Number.isNaN(value) ? '—' : String(value);

const formatHitRate = (marker: SignalMarker): string => {
  if (marker.hitRate === null || marker.hitSample === null || marker.hitSample <= 0) {
    return '暂无样本';
  }
  const pct = Math.round(marker.hitRate * 100);
  return `命中率 ${pct}% · ${marker.hitSample} 样本`;
};

const formatAsOf = (asOf: number | null): string => {
  if (asOf === null) return '';
  return new Date(asOf).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
};

const directionLabel: Record<SignalMarker['direction'], string> = {
  bullish: '看多',
  bearish: '看空',
  neutral: '中性',
};

const MarkerCard: React.FC<{ marker: SignalMarker }> = ({ marker }) => {
  const isRule = marker.source === 'rule';
  return (
    <div
      data-testid="drilldown-marker"
      className={cn(
        'rounded-xl border border-border/60 bg-card/80 p-4',
        marker.isDailyApprox && 'opacity-70',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">
          {isRule ? '规则信号' : 'LLM 结论'} · {directionLabel[marker.direction]}
        </span>
        <span className="text-xs text-secondary-text">{marker.confidence}</span>
      </div>

      <p className="mt-2 text-sm text-foreground">{marker.reason}</p>

      {isRule ? (
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-secondary-text">
          <span data-testid="drilldown-observed-value">
            观测值：{formatNumber(marker.observedValue)}
          </span>
          <span data-testid="drilldown-threshold">阈值：{formatNumber(marker.threshold)}</span>
        </div>
      ) : (
        <div className="mt-3 text-xs text-secondary-text" data-testid="drilldown-as-of">
          结论时间：{formatAsOf(marker.asOf) || '—'}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between text-xs">
        <span data-testid="drilldown-hit-rate" className="text-secondary-text">
          {formatHitRate(marker)}
        </span>
        <span
          data-testid="drilldown-verified"
          className={cn(marker.verified ? 'text-success' : 'text-secondary-text')}
        >
          {marker.verified ? '已验证' : '未验证'}
        </span>
      </div>
    </div>
  );
};

export const SignalDrilldownPanel: React.FC<SignalDrilldownPanelProps> = ({ markers, onClose }) => (
  <div className="flex flex-col gap-3" role="group" aria-label="信号依据">
    <div className="flex items-center justify-between">
      <span className="label-uppercase">SIGNAL EVIDENCE</span>
      <button
        type="button"
        onClick={onClose}
        className="home-surface-button rounded-lg px-3 py-1 text-xs text-secondary-text"
      >
        关闭依据
      </button>
    </div>
    {markers.map((marker) => (
      <MarkerCard key={`${marker.timestamp}:${marker.source}:${marker.signalType}`} marker={marker} />
    ))}
  </div>
);
```

#### 步骤 ④ 跑命令验证通过

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/SignalDrilldownPanel.test.tsx
```

预期输出：`Test Files  1 passed (1)` / `Tests  3 passed (3)`。

#### 步骤 ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx
git commit -m "feat: 新增信号钻取面板（规则指标/阈值/命中率，LLM advice/as_of）"
```

---

### Task M2d-4 · `KLineChartPanel` 接入 registerOverlay 自绘 + onClick 桥接 + priceLine + 降级

在 M0 已渲染蜡烛/量副图的 `KLineChartPanel` 上，M2d 追加：拉 `/signals` → `buildSignalGlyphs` → 逐 glyph `registerOverlay`/`createOverlay` 自绘 ▲/△ 并 `onClick` 打开 `SignalDrilldownPanel`；`price_lines` 用内置 `priceLine` overlay 画 entry/stop/target；`/signals` 失败时图照常渲染、不画 overlay、展示"有图无标注"提示。

> 关键解耦：M0 的 `KLineChartPanel` 用**模块顶部静态 import**（`import { dispose, init } from 'klinecharts'`）拿到 klinecharts API，并把 `init()` 返回的 chart 实例存进 `chartRef.current`；该 panel 整体被 `KLineDrawer` 经 `lazy(() => import('./KLineChartPanel'))` 懒加载，故 klinecharts 仍只在 lazy chunk（**没有** `klineLibRef` 这类 lazy-ref；klinecharts 函数直接经静态 import 调用）。M2d 复用该静态 import（追加 `registerOverlay`），在面板内调用**模块级** `registerOverlay(...)` 与组件内 `chartRef.current.createOverlay(...)`。测试用 `vi.doMock('klinecharts', ...)` 提供 `init`/`registerOverlay`/`dispose` 的 spy，断言"注册了 overlay、画了价位线、点击回调打开钻取"，不依赖真实 canvas。

**Files**
- Modify: `apps/dsa-web/src/components/kline/KLineChartPanel.tsx`（M0 产物；追加 signals 拉取、overlay 注册、priceLine、钻取状态、降级提示）
- Test (Create): `apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.signals.test.tsx`

#### 步骤 ① 写失败测试

Create `apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.signals.test.tsx`：

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SignalsResponse } from '../../../types/kline';

const okSignals: SignalsResponse = {
  status: 'ok',
  consistency: 'consistent',
  degradedReason: null,
  priceLines: { entry: 1700.5, stop: 1620, target: 1850 },
  markers: [
    {
      timestamp: 1718323200000,
      price: 1700.5,
      anchor: 'low',
      direction: 'bullish',
      signalType: 'volume_breakout',
      source: 'rule',
      confidence: 'high',
      isDailyApprox: false,
      isAnomalous: false,
      reason: '放量突破20日高',
      threshold: 2.0,
      observedValue: 2.4,
      hitRate: 0.62,
      hitSample: 18,
      verified: true,
      asOf: null,
    },
  ],
};

const createOverlay = vi.fn().mockReturnValue('overlay-1');
const registerOverlay = vi.fn();
const setOverlayClickCallbacks: Array<(payload: unknown) => void> = [];

const setupChartMock = () => {
  const chart = {
    applyNewData: vi.fn(),
    createOverlay: createOverlay,
    removeOverlay: vi.fn(),
    subscribeAction: vi.fn(),
    resize: vi.fn(),
  };
  vi.doMock('klinecharts', () => ({
    init: vi.fn(() => chart),
    dispose: vi.fn(),
    registerOverlay: (template: { name: string; onClick?: (e: unknown) => boolean }) => {
      registerOverlay(template);
      if (template.onClick) {
        setOverlayClickCallbacks.push(template.onClick as (payload: unknown) => void);
      }
    },
  }));
  return chart;
};

const renderPanel = async () => {
  const { KLineChartPanel } = await import('../KLineChartPanel');
  render(<KLineChartPanel stockCode="600519" stockName="贵州茅台" market="CN" />);
};

describe('KLineChartPanel signals layer', () => {
  afterEach(() => {
    setOverlayClickCallbacks.length = 0;
    createOverlay.mockClear();
    registerOverlay.mockClear();
    vi.doUnmock('klinecharts');
    vi.doUnmock('../../../api/stocks');
    vi.resetModules();
  });

  it('registers signal overlays and draws entry/stop/target price lines on ok response', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue(okSignals),
      },
    }));

    await renderPanel();

    await waitFor(() => expect(registerOverlay).toHaveBeenCalled());
    expect(registerOverlay.mock.calls.some(([t]) => t.name === 'signalGlyph')).toBe(true);

    await waitFor(() => {
      const priceLineCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'priceLine',
      );
      expect(priceLineCalls).toHaveLength(3); // entry + stop + target
    });
  });

  it('opens the drilldown panel when a signal overlay is clicked', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue(okSignals),
      },
    }));

    await renderPanel();

    await waitFor(() => expect(setOverlayClickCallbacks.length).toBeGreaterThan(0));
    // klinecharts onClick passes the created overlay; our template stores glyph id in extendData.
    setOverlayClickCallbacks[0]({ overlay: { extendData: okSignals.markers } });

    expect(await screen.findByText('放量突破20日高')).toBeInTheDocument();
  });

  it('falls back to chart-without-annotations when /signals fails', async () => {
    vi.resetModules();
    setupChartMock();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.doMock('../../../api/stocks', () => ({
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockRejectedValue(new Error('signals 500')),
      },
    }));

    try {
      await renderPanel();

      expect(await screen.findByTestId('signals-unavailable')).toBeInTheDocument();
      // chart data still applied -> no signal overlay registered, no price lines drawn
      await waitFor(() => {
        const priceLineCalls = createOverlay.mock.calls.filter(([arg]) =>
          typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'priceLine',
        );
        expect(priceLineCalls).toHaveLength(0);
      });
    } finally {
      consoleError.mockRestore();
    }
  });

  it('weakens degraded markers visually but still renders the chart', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue({
          ...okSignals,
          markers: [{ ...okSignals.markers[0], isDailyApprox: true, signalType: 'vsa_no_demand' }],
        }),
      },
    }));

    await renderPanel();

    await waitFor(() => {
      const glyphCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'signalGlyph',
      );
      expect(glyphCalls).toHaveLength(1);
      const styles = (glyphCalls[0][0] as { styles?: { polygon?: { color?: string } } }).styles;
      // B-class opacity baked into rgba alpha < 1
      expect(styles?.polygon?.color).toMatch(/0\.45\)$/);
    });
  });
});
```

#### 步骤 ② 跑命令验证失败

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/KLineChartPanel.signals.test.tsx
```

预期输出（失败）：`registerOverlay` 从未被以 `name: 'signalGlyph'` 调用 / `signals-unavailable` 未渲染 / priceLine `createOverlay` 调用数为 0——即 M0 面板尚未含 signals 图层（断言全部失败）。

#### 步骤 ③ 写最小实现

在 M0 的 `apps/dsa-web/src/components/kline/KLineChartPanel.tsx` 上追加 signals 图层。M0 已有：模块顶部静态 `import { dispose, init } from 'klinecharts'`、`init()` 存入 `chartRef.current`、`applyNewData` 渲染、`stockCode/market/days` props、`getKlineHistory` 拉取。M2d 追加以下内容（保留 M0 既有渲染逻辑，仅扩静态 import、新增状态、effect、降级提示与钻取层）。

把 M0 的 klinecharts 静态 import 扩成（追加 `registerOverlay`，复用同一静态 import，不引入 lazy-ref；M0 已有的 `import type { Chart } from 'klinecharts'` 保留，下文辅助函数复用 `Chart` 类型）：

```tsx
import { dispose, init, registerOverlay } from 'klinecharts';
import type { Chart } from 'klinecharts';  // M0 已导入则去重
```

文件顶部其余 import 区追加：

```tsx
import { useCallback, useState } from 'react';  // useCallback 若 M0 未导入则补；useState M0 已有则去重
import { stocksApi } from '../../api/stocks';
import type { SignalMarker } from '../../types/kline';
import { buildSignalGlyphs, type SignalGlyph } from './klineOverlays';
import { SignalDrilldownPanel } from './SignalDrilldownPanel';
```

在组件函数体内（M0 的 chart 实例存在 `chartRef.current`；klinecharts 函数经模块级静态 import 直接调用，无 `klineLibRef`）追加状态与绘制逻辑：

```tsx
  const [signalsAvailable, setSignalsAvailable] = useState(true);
  const [drilldownMarkers, setDrilldownMarkers] = useState<SignalMarker[] | null>(null);

  // Draw dual-track glyph overlays + price lines. Failures degrade to chart-only.
  const applySignalsLayer = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    stocksApi
      .getSignals(stockCode)
      .then((signals) => {
        setSignalsAvailable(true);
        const glyphs = buildSignalGlyphs(signals.markers);
        // 模块级静态 registerOverlay：注册一次模板（重复 name 注册幂等）。
        registerSignalGlyphTemplate((markers) => setDrilldownMarkers(markers));
        for (const glyph of glyphs) {
          drawGlyphOverlay(chart, glyph);
        }
        drawPriceLines(chart, signals.priceLines);
      })
      .catch((error) => {
        console.error('Failed to load signals overlay:', error);
        setSignalsAvailable(false);
      });
  }, [stockCode]);
```

在 M0 的"数据 `applyNewData` 之后"调用 `applySignalsLayer()`（与 M0 拉取 history 成功的回调串联；history 成功即调用，signals 独立 catch，互不拖累；chart 实例从组件内 `chartRef.current` 读，klinecharts 模块函数经静态 import 直接调）。

文件底部（组件外）追加自绘与价位线辅助函数（纯封装 klinecharts API，便于 mock 断言）：

```tsx
const rgbaForGlyph = (glyph: SignalGlyph): string => {
  // 中式：bullish 红、bearish 绿；neutral 灰；alpha 由 B 类弱化决定
  const base =
    glyph.direction === 'bullish'
      ? '239, 68, 68'
      : glyph.direction === 'bearish'
        ? '34, 197, 94'
        : '148, 163, 184';
  return `rgba(${base}, ${glyph.opacity})`;
};

const registerSignalGlyphTemplate = (
  onPick: (markers: SignalMarker[]) => void,
): void => {
  // 模块级静态 registerOverlay（M0 同一静态 import），无 klineLib 形参。
  registerOverlay({
    name: 'signalGlyph',
    totalStep: 1,
    needDefaultPointFigure: false,
    createPointFigures: ({ overlay, coordinates }: {
      overlay: { extendData?: { glyph: SignalGlyph } };
      coordinates: Array<{ x: number; y: number }>;
    }) => {
      const point = coordinates[0];
      const glyph = overlay.extendData?.glyph;
      if (!point || !glyph) return [];
      const dir = glyph.shape === 'triangle-down' ? 1 : -1;
      const slot = glyph.offsetSlot * 10;
      const x = point.x + slot;
      const size = 6;
      const color = rgbaForGlyph(glyph);
      if (glyph.shape === 'dot') {
        return [{ type: 'circle', attrs: { x, y: point.y, r: 3 }, styles: { color } }];
      }
      return [
        {
          type: 'polygon',
          attrs: {
            coordinates: [
              { x, y: point.y },
              { x: x - size, y: point.y + dir * size * 1.6 },
              { x: x + size, y: point.y + dir * size * 1.6 },
            ],
          },
          // filled (rule) => solid color; hollow (llm) => transparent fill + border
          styles: glyph.filled
            ? { style: 'fill', color }
            : { style: 'stroke', borderColor: color, color: 'transparent' },
        },
      ];
    },
    onClick: (event: { overlay?: { extendData?: { glyph: SignalGlyph } } }) => {
      const glyph = event.overlay?.extendData?.glyph;
      if (glyph) onPick(glyph.drilldown.markers);
      return true;
    },
  });
};

const drawGlyphOverlay = (
  chart: Chart,
  glyph: SignalGlyph,
): void => {
  chart.createOverlay({
    name: 'signalGlyph',
    points: [{ timestamp: glyph.timestamp, value: glyph.price }],
    extendData: { glyph },
    styles: { polygon: { color: rgbaForGlyph(glyph) } },
  });
};

const drawPriceLines = (
  chart: Chart,
  priceLines: { entry: number | null; stop: number | null; target: number | null },
): void => {
  const lines: Array<[number | null, string]> = [
    [priceLines.entry, 'rgba(239, 68, 68, 0.9)'],
    [priceLines.stop, 'rgba(148, 163, 184, 0.9)'],
    [priceLines.target, 'rgba(34, 197, 94, 0.9)'],
  ];
  for (const [value, color] of lines) {
    if (value === null) continue;
    chart.createOverlay({
      name: 'priceLine',
      points: [{ value }],
      styles: { line: { color } },
    });
  }
};
```

在 M0 渲染的 JSX 容器内（图表 `<div>` 同级）追加降级提示与钻取层：

```tsx
      {!signalsAvailable && (
        <div
          data-testid="signals-unavailable"
          className="mt-2 rounded-lg border border-border/50 bg-card/60 px-3 py-2 text-xs text-secondary-text"
        >
          信号标注暂不可用，已展示纯 K 线图。
        </div>
      )}
      {drilldownMarkers && (
        <div className="mt-3">
          <SignalDrilldownPanel markers={drilldownMarkers} onClose={() => setDrilldownMarkers(null)} />
        </div>
      )}
```

> 注：`createPointFigures` 的具体 figure type 名（`polygon`/`circle`/`text`）以本机 `node_modules/klinecharts@9.8.x` 的 `OverlayTemplate` 类型为准；若该版本字段名有差异，按 `node_modules/klinecharts/dist/index.d.ts` 的 `registerOverlay`/`OverlayCreateFiguresCallbackParams` 实际签名对齐，但**不改** `signalGlyph`/`priceLine` 这两个 name 与 `extendData.glyph` 契约（测试据此断言）。`useCallback` 已在 M0 从 react 导入则复用，否则在 import 区补 `useCallback`。

#### 步骤 ④ 跑命令验证通过

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx vitest run src/components/kline/__tests__/KLineChartPanel.signals.test.tsx
```

预期输出：`Test Files  1 passed (1)` / `Tests  4 passed (4)`。

#### 步骤 ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/components/kline/KLineChartPanel.tsx apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.signals.test.tsx
git commit -m "feat: K线面板接入双轨信号自绘overlay、价位线、钻取与有图无标注降级"
```

---

### Task M2d-5 · 全量门禁（lint + build + 全套 vitest）+ 文档同步

> **非 TDD：门禁/文档 task，以命令探针为红绿**——无新增单元测试，红=`eslint`/`tsc -b && vite build` 失败或 CHANGELOG 无新行，绿=lint+build 通过、全套 vitest 绿、`grep` 命中 CHANGELOG 与专题文档更新。

收口：跑前端门禁等价命令（在 `/tmp` 无空格副本，避免工作区路径空格坑），并按硬规则同步 CHANGELOG 与受影响专题文档。

**Files**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式追加一行）
- Modify: 对应 K 线/可视化专题文档（若 M0 已建 `docs/*.md` 则在其"信号标注"小节补 M2d；否则在 spec 同目录新增的实现说明文档中补，**不写入 README**）
- Test: 复跑本段全部 vitest 用例 + lint + build

#### 步骤 ① 写失败测试

本步为门禁收口与文档，无新增单测；以"门禁命令通过"为完成判据（见步骤 ②/④）。先确认 CHANGELOG `[Unreleased]` 段当前缺少本段条目（即下方 grep 无输出，视为"待补失败态"）：

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
grep -n '双轨标注' docs/CHANGELOG.md || echo "MISSING: m2d changelog entry"
```

预期输出：`MISSING: m2d changelog entry`。

#### 步骤 ② 跑命令验证失败

先跑全套门禁以确认 lint/build 在加入新文件后仍可能有 TS/lint 报错（如未用导入、any 等）：

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d && npx eslint src/components/kline src/api/stocks.ts src/types/kline.ts
```

预期：若有任何 `@typescript-eslint/no-explicit-any`、未使用变量等告警则此处暴露并修复；干净则进入步骤 ③。

#### 步骤 ③ 写最小实现（文档 + 修复 lint 残留）

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段追加一行（扁平格式，禁止新增 `### 类目标题`）：

```md
- [新功能] K 线抽屉支持规则/LLM 双轨信号标注（合并/并排）、点击钻取依据、入/损/标价位线，/signals 失败降级有图无标注
```

在 K 线可视化专题文档（M0 已建文档的"信号标注"小节，或 spec 同目录实现说明）补充 M2d 用户可见行为：双轨 glyph 语义（规则实心 ▲/▼、LLM 空心 △/▽）、一致合并/冲突并排、B 类（`is_daily_approx`）视觉弱化、`hit_rate/verified` 展示口径、降级文案"信号标注暂不可用，已展示纯 K 线图"（前端对任意非空 `degraded_reason` / `status==='degraded'` 一律按"有图无标注"通用降级，不依赖特定原因字面量）。同时记一句：复权一致性检测非本期范围，`/signals` 与 `/history` 同源由构造保证（同一取数路径），detection 留后续。如步骤 ② 暴露 lint 残留（如 `createPointFigures` 入参类型 any），改为按 `OverlayTemplate` 精确类型注解后复跑。

#### 步骤 ④ 跑命令验证通过

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsa-web-m2d/src/
cd /tmp/dsa-web-m2d
npx vitest run src/api/__tests__/stocks.signals.test.ts src/components/kline
npx eslint .
npm run build
cd "/root/AI/WorkSpace/cursor/AI _Trading_System" && grep -n '双轨标注' docs/CHANGELOG.md
```

预期输出：vitest 全绿（本段 4 个测试文件全部 passed）；`eslint .` 无错误退出 0；`tsc -b && vite build` 成功产出 dist；grep 命中 CHANGELOG 新行。

> 注意：vitest 全绿不等于 web-gate 通过，必须真跑 `eslint .`（全量）与 `npm run build`（含 `tsc -b` 类型检查）才算等价门禁。

#### 步骤 ⑤ Commit

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add docs/CHANGELOG.md docs/
git add apps/dsa-web/src/components/kline apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/types/kline.ts
git commit -m "docs: 同步 K 线双轨标注/钻取/价位线说明与 CHANGELOG"
```

---

### M2d 验证与交付要点

- **测试覆盖映射**（对应本段要求）：overlay 渲染双轨 = `klineOverlays.test.ts`（filled/hollow、merged/conflict）+ `KLineChartPanel.signals.test.tsx`（`registerOverlay name=signalGlyph`）；钻取打开 = `KLineChartPanel.signals.test.tsx`（onClick → `findByText` 依据）+ `SignalDrilldownPanel.test.tsx`；价位线 = `KLineChartPanel.signals.test.tsx`（3 条 `priceLine` createOverlay）；降级"有图无标注" = `KLineChartPanel.signals.test.tsx`（getSignals reject → `signals-unavailable` + 0 价位线）；B 类弱化 = `klineOverlays.test.ts`（`B_CLASS_OPACITY`）+ `KLineChartPanel.signals.test.tsx`（rgba alpha `0.45`）。
- **契约一致性**：`getSignals`/`SignalsResponse`/`SignalMarker`/`PriceLines`/`mapKLineDataToKLine` 命名与跨里程碑接口契约逐字对齐；`signalGlyph`/`priceLine` overlay name 与 `extendData.glyph` 为面板↔测试稳定契约。
- **降级护栏**：`/signals` 失败仅置 `signalsAvailable=false`、不抛出，K 线（M0 渲染）不受影响；chunk/渲染异常由 M0 `KLineDrawer` 的 ErrorBoundary 兜底（本段不重复实现，依赖 M0）。
- **未验证项/风险**：`createPointFigures` 的 figure type 名以本机 klinecharts 9.8.x `OverlayTemplate` 类型为准，可能需按真实 `.d.ts` 微调字段（不改 name/extendData 契约）；真实 canvas 渲染外观（三角形像素位置/并排间距）非单测覆盖，需在 `npm run dev` 接 StockBar 入口（M0 已接）做一次人工目检。
- **回滚**：本段全为新增文件 + 对 M0 `KLineChartPanel` 的增量；删除 `klineOverlays.ts`/`SignalDrilldownPanel.tsx`、回退 `KLineChartPanel` 的 signals 图层、移除 `getSignals` 与 `kline.ts` 信号类型即恢复 M0 行为，主流程与后端不受影响。