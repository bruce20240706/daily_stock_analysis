# 量价信号引擎字段契约（M1）

> 对应实现：`src/services/volume_price_signals.py`
> 本文档描述 M1 静态输出契约与阈值语义，不涉及 M2a+ 端点集成。

---

## 1. 核心数据结构

### VPSResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `markers` | `list[VPSignal]` | 所有已触发信号（A 类 + 限流后 B 类 + 量价八法最新 bar 单点） |
| `status` | `str` | `'ok'` / `'degraded'`（数据不足或 vol_ma<=0 时） |
| `degraded_reason` | `str \| None` | degraded 时的原因说明，ok 时为 None |

### VPSignal

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | `int` | epoch ms（Asia/Shanghai 午夜锚定） |
| `price` | `float` | 信号锚定价格（与 `anchor` 对应） |
| `anchor` | `str` | `'low'` / `'high'` / `'close'` |
| `direction` | `str` | `'bullish'` / `'bearish'` / `'neutral'` |
| `signal_type` | `str` | 见第 2 节全集 |
| `confidence` | `str` | `'high'` / `'medium'` / `'low'` |
| `is_daily_approx` | `bool` | 日线近似（非 tick 级精度） |
| `is_anomalous` | `bool` | VSA 异常成交量（Upthrust/Spring/No Demand/No Supply） |
| `reason` | `str` | 人类可读触发说明 |
| `threshold` | `float \| None` | 触发阈值（如突破价位、rel_vol 阈值） |
| `observed_value` | `float \| None` | 实测值（如实际 rel_vol） |

### Pivot

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | `int` | DataFrame 行号（已确认，滞后 k 根） |
| `timestamp` | `int` | epoch ms（Asia/Shanghai） |
| `price` | `float` | 极值价格 |
| `kind` | `str` | `'high'` / `'low'` |

---

## 2. signal_type 全集与方向语义

> 说明：`signal_type` 字面量以 `src/services/volume_price_signals.py` 实际产出为准（下表已对齐）。
> consistency 由收敛后的单个 `BuySignal` 计算，不由 marker 投票；A 类标注为高/中置信方向性信号。

### A 类信号（高/中置信方向性，A 类语义参与判定）

| signal_type | direction | confidence | 触发条件概述 |
|-------------|-----------|------------|-------------|
| `obv_bottom_divergence` | bullish | medium | OBV 低点抬升 + 价格创新低（底背离）；时间锚定确认 bar（枢轴 index + swing_k） |
| `obv_top_divergence` | bearish | medium | OBV 高点下移 + 价格创新高（顶背离）；时间锚定确认 bar（枢轴 index + swing_k） |
| `volume_breakout` | bullish | high | close >= high.rolling(N).max().shift(1) 且 rel_vol >= 阈值 |
| `shrink_pullback` | bullish | medium | 回调幅度 <= ATR × 倍数 且段内 rel_vol <= 上界 |
| `anchored_vwap_reclaim` | bullish | medium | 价格上穿锚定突破日 AVWAP |
| `anchored_vwap_loss` | bearish | medium | 价格下穿锚定突破日 AVWAP |

### B 类信号（VSA / 量价八法，强制降权）

> B 类不进 consistency 投票、不驱动 price_lines、置信硬上限 low、`is_daily_approx=True`。
> VSA/Upthrust/Spring 受 top-k 限流；量价八法 vfx 单点独立追加（见第 6 节）。

| signal_type | direction | 触发条件概述 |
|-------------|-----------|-------------|
| `upthrust` | bearish | 假突破顶：高于前一确认高点后收回其下方 |
| `spring` | bullish | 假跌破底：低于前一确认低点后收回其上方 |
| `vsa_no_demand` | bearish | 缩量上涨、收在区间下半（无力） |
| `vsa_no_supply` | bullish | 缩量下跌、收在区间上半（无量承接） |
| `vsa_stopping` | neutral | 高量大幅波动后收回中部（止跌/止涨迹象） |
| `vsa_effort_vs_result` | neutral | 高量但实体极小（努力无果） |
| `vfx_*`（量价八法最新 bar） | bullish/bearish | 最新 bar 量价八法分类，仅在方向性（非 neutral）时产出 **1 个**降权标注，取值：`vfx_shrink_up` / `vfx_shrink_down` / `vfx_expand_up` / `vfx_expand_down` / `vfx_climax_up` / `vfx_climax_down` |

---

## 3. 量价八法 5×3 真值表

rel_vol 分档（纵）× pct_chg 分档（横）：

|  | 价跌 (down) | 价平 (flat) | 价涨 (up) |
|---|---|---|---|
| **量极低 (low)** | bearish/medium | bearish/low | neutral/low |
| **量缩 (shrink)** | bearish/low | neutral/low | bullish/low |
| **量平 (normal)** | bearish/low | neutral/low | bullish/low |
| **量升 (up)** | bearish/medium | neutral/medium | bullish/medium |
| **量放 (high)** | bearish/high | neutral/medium | bullish/high |

> 表中每格唯一、穷尽、互斥。flat 判定条件：\|pct_chg\| <= `VPS_PRICE_EPS`。量档边界见第 5 节配置。
> **落地**：`compute_volume_price_signals` 对**最新 bar**做一次量价八法分类，仅当结果为方向性（非 neutral、非 undefined）时产出 1 个 `vfx_*` 降权 B 类标注（`confidence=low`、`is_daily_approx=True`），不逐 bar 刷屏；neutral/异常格不产标注。量增价升（up/high × up）需 `body>0` 或 `range_pos>0.5` 确认，否则降级 neutral（即不产标注）。

---

## 4. 未来函数防护

- **rel_vol 基准**：`vol_ma(VPS_VOL_MA_WINDOW).shift(1)`，当日量不纳入基准。
- **swing pivot**：左右各确认 k 根（`VPS_SWING_K`），最新确认点天然滞后 k 根。
- **放量突破**：`close >= high.rolling(N).max().shift(1)`，滚动最高价不含当日。
- **OBV 背离**：背离判断基于已确认 swing pivot（滞后 k），不用未来极值；标注**时间锚定确认 bar**（枢轴 index + swing_k），不早于背离可知日（y 仍锚枢轴极值）。
- **AVWAP 锚点**：仅在突破日成立后方可锚定，无提前感知。

---

## 5. 鲁棒性契约

| 场景 | 行为 |
|------|------|
| `vol_ma <= 0` | `rel_vol = None`，进入 `degraded`，`degraded_reason` 说明原因 |
| 一字板（high == low） | 跳过该 bar 的 VSA 判断，不产生 Upthrust/Spring |
| 窗口不足（行数 < vol_ma_window + swing_k） | 返回 `degraded`，`markers` 为空 |
| 数据缺列（缺 open/high/low/close/volume） | 返回 `degraded`（`markers=[]`，`degraded_reason` 标注缺列）；`normalize_ohlcv` 内部 `ValueError` 由 `_normalize` 捕获转 degraded，不向上抛出 |

---

## 6. B 类降权契约

B 类信号（VSA Upthrust / Spring / No Demand / No Supply）受以下约束：

1. **置信硬上限**：`confidence` 强制为 `'low'`（覆盖计算值）。
2. **不进 consistency 投票**：M2a consistency 聚合只读取 A 类方向集合，B 类不参与。
3. **不驱动 price_lines**：M2b 价位反算器不消费 B 类信号的锚定价。
4. **top-k 限流**：每次 `compute_volume_price_signals` 调用，VSA/Upthrust/Spring 类总数上限为 `VPS_B_CLASS_TOP_K`（按 `observed_value` 绝对值降序保留 top-k）。量价八法 `vfx_*` 最新 bar 标注为单点，独立追加，不计入该 top-k。

---

## 7. VPS_* 可配项与默认值

所有可配项均由 `VPSConfig.from_env()` 读取，覆盖 `VPSConfig` 默认值。
`/signals` 端点已以 `compute_volume_price_signals(df, config=VPSConfig.from_env())` 接线，下列 13 个 `VPS_*` 变量真正生效；未配置时使用括号内默认值，不影响运行。

| 环境变量 | 默认值 | 约束 | 语义 |
|----------|--------|------|------|
| `VPS_PRICE_EPS` | `0.004` | >= 0 | 价档 flat 半带宽（pct_chg 绝对值 <= 该值视为持平） |
| `VPS_VOL_LOW` | `0.7` | >= 0 | 量档 low 上界（< 该值为极低量） |
| `VPS_VOL_SHRINK` | `0.8` | >= 0 | 量档 shrink 上界 |
| `VPS_VOL_UP` | `1.2` | >= 0 | 量档 normal 上界 / up 下界 |
| `VPS_VOL_HIGH` | `1.5` | >= 0 | 量档 up 上界 / high 下界 |
| `VPS_SWING_K` | `3` | >= 1，取整 | swing pivot 左右确认根数（越大越稳但越滞后） |
| `VPS_VOL_MA_WINDOW` | `20` | >= 5，取整 | 量基准滚动窗口（bar 数），不足时进入 degraded |
| `VPS_BREAKOUT_WINDOW` | `20` | >= 2，取整 | 放量突破 high.rolling 窗口 N |
| `VPS_BREAKOUT_REL_VOL` | `2.0` | >= 1 | 放量突破 rel_vol 阈值 |
| `VPS_PULLBACK_REL_VOL` | `0.9` | >= 0 | 缩量回调段内 rel_vol 上界 |
| `VPS_PULLBACK_ATR_MULT` | `3.0` | >= 0.5 | 缩量回调最大回撤 = ATR × 倍数 |
| `VPS_ATR_PERIOD` | `14` | >= 2，取整 | ATR 计算周期（Wilder） |
| `VPS_B_CLASS_TOP_K` | `2` | >= 1，取整 | B 类每结果集保留最新 top-k 个 |

> **注意**：量基准口径（20 日）与 `_analyze_volume` 的 `VolumeStatus`（5 日）刻意分离，两者并存不互替。

---

## 8. 回滚方式

删除以下文件即可完整回滚 M1，无任何主流程依赖：

- `src/services/volume_price_signals.py`
- `tests/test_volume_price_signals.py`
- `docs/volume-price-signals.md`
- 还原 `.env.example` 末尾 `VPS_*` 追加段
- 还原 `docs/CHANGELOG.md` 对应一行
