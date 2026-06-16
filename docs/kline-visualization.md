# K 线可视化与信号标注

## 范围

- Web 个股栏（HomePage `StockBar`）每只个股新增「K 线」按钮，点击侧滑出 K 线抽屉。
- 抽屉渲染日线蜡烛 + 成交量副图，支持十字光标、缩放；涨跌颜色默认中式红涨绿跌，可切换为绿涨红跌。
- 数据来自 `GET /api/v1/stocks/{code}/history`（默认 `days=120`），经 `mapKLineDataToKLine` 映射为前端 `KLine`（日期按 Asia/Shanghai 锚定为 epoch ms，`amount` → `turnover`）。
- 图上叠加规则/LLM 双轨买卖信号标注、入/损/标价位线，并支持点击钻取依据（M2d，见下文「信号标注」）。

## 关键文件

- 类型：`apps/dsa-web/src/types/kline.ts`（`KLine`；以及 `SignalMarker`/`PriceLines`/`SignalsResponse` 等信号契约，镜像后端 `api/v1/schemas/stocks.py`）
- client/映射：`apps/dsa-web/src/api/stocks.ts`（`getKlineHistory`、`mapKLineDataToKLine`、`getSignals`）
- 组件：`apps/dsa-web/src/components/kline/KLineDrawer.tsx`（壳）、`KLineChartPanel.tsx`（lazy 重面板，唯一 import klinecharts 处，含信号图层）
- 信号纯逻辑：`apps/dsa-web/src/components/kline/klineOverlays.ts`（`buildSignalGlyphs`：双轨 glyph 描述/合并/并排/B 类弱化/钻取 payload）
- 钻取面板：`apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`
- 入口：`apps/dsa-web/src/components/history/StockBarItem.tsx` → `StockBar.tsx` → `HomePage.tsx`

## 依赖与分包

- `klinecharts@^9.8.12`（Apache-2.0；禁用 `@latest`/`10.x`）。
- `vite.config.ts` 将 klinecharts 单独打入 `vendor-klinecharts` chunk，且仅经 `KLineDrawer` 的 lazy import 加载，首屏不引入。

## 信号标注（M2d）

抽屉拉取 `GET /api/v1/stocks/{code}/signals`（与 `/history` 同源同复权），把规则量价信号与 LLM 最新结论以双轨 glyph 叠加在图上：

- **双轨形状**：规则信号（A 类）画**实心**三角，LLM 结论画**空心**三角；看多向上（▲/△）、看空向下（▼/▽）、中性为圆点。颜色沿用中式红涨绿跌（看多红、看空绿、中性灰）。
- **一致合并 / 冲突并排**：同一交易日上规则与 LLM **同向** → 合并为一个标注（`mode=merged`，钻取时同时列出两条依据）；**异向** → 并排横向错开（`offsetSlot` 区分）并标 `mode=conflict`，不互相覆盖、不强行二选一。
- **B 类弱化**：`is_daily_approx` 的 B 类信号（VSA/异常行为，日线近似）以更低透明度（`B_CLASS_OPACITY=0.45`，对比 A 类 `1.0`）弱化呈现，避免与确定性 A 类信号视觉等权。
- **价位线**：`price_lines` 的 entry/止损/目标各画一条内置 `priceLine`（仅对非空值绘制）。
- **点击钻取**：点击 glyph 打开 `SignalDrilldownPanel`——规则态展示观测值/阈值/命中率与「已验证/未验证」；LLM 态展示建议文案（advice）与结论时间（as_of）。
- **命中率口径**：`hit_rate`/`hit_sample` 来自历史回测前向评估的**该 code 历史方向命中率**近似（`BacktestResult` 不细分 `signal_type`），达样本阈值才标「已验证」。面板按「历史统计、非未来保证」呈现，不作为收益承诺。

### 降级

- 面板加载/渲染失败由 `KLineDrawer` 错误边界兜底，显示「K 线加载失败」，不影响页面其余部分。
- 后端无数据时显示「暂无 K 线数据」。
- **有图无标注**：`/signals` 请求失败，或返回 `status=degraded`（任意非空 `degraded_reason`）时，一律按通用降级处理——照常渲染纯 K 线图，仅在角落提示「信号标注暂不可用，已展示纯 K 线图」，不绘制任何 glyph/价位线，也不依赖特定原因字面量。信号层失败与 K 线渲染相互隔离（独立 promise 链、各自 catch）。

## 复权与同源

- `/signals` 与 `/history` 同源同复权由**构造**保证（同一取数路径、同一 `days` 窗口）；复权一致性主动检测非本期范围，留作后续里程碑。

## 后续里程碑

- 布局演进：A（抽屉，已上线）→ C（仪表盘）→ B（工作台）；复权一致性主动检测；多周期（周/月线）与盘中分时。
