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
