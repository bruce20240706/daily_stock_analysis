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
| `obv_bottom_divergence` | bullish | low/medium/high | OBV+CMF+MFI 多源共振底背离；置信度由共振源数决定（1源=low,2源=medium,3源=high）；时间锚定确认 bar |
| `obv_top_divergence` | bearish | low/medium/high | OBV+CMF+MFI 多源共振顶背离；置信度由共振源数决定；时间锚定确认 bar |
| `volume_breakout` | bullish | high | close >= high.rolling(N).max().shift(1) 且 rel_vol >= 阈值 |
| `shrink_pullback` | bullish | medium | 回调幅度 <= ATR × 倍数 且段内 rel_vol <= 上界；reason 含量能形态档 |
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

## 5. M3-B 量价丰富度扩展

### 5.1 CMF / MFI 量能指标

M3-B 新增两个内部量能指标，仅用于背离检测，不单独产出 signal_type：

| 指标 | 函数 | 窗口 | 量纲 | 说明 |
|------|------|------|------|------|
| CMF（Chaikin Money Flow）| `_cmf(high,low,close,volume,window)` | 14 bar（`_DIV_CMF_WINDOW`） | `[-1, 1]` | 量权资金流方向；window 内正值为净流入，负值为净流出 |
| MFI（Money Flow Index）| `_mfi(high,low,close,volume,window)` | 14 bar（`_DIV_MFI_WINDOW`） | `[0, 100]` | 量权 RSI 变体；全上涨窗口（neg==0）标准定义为 100；flat 窗口（pos==neg==0）返回 NaN |

两个窗口常量（`_DIV_CMF_WINDOW`、`_DIV_MFI_WINDOW`）目前固定为 14，与 `VPSConfig` 解耦，不通过环境变量配置。

### 5.2 OBV + CMF + MFI 多源背离共振

背离检测（`_detect_obv_divergence`）在 M3-B 升级为三源共振：

**共振规则**

| 共振源数 (k) | confidence | 行为 |
|-------------|------------|------|
| 3（OBV+CMF+MFI 全部同向背离） | `high` | 强置信背离信号 |
| 2（任意两源同向背离） | `medium` | 中置信背离信号 |
| 1（单源背离） | `low` | 弱提示，仍产出 marker（emit-low 规则） |
| 0（无源背离） | — | 不产 marker |

**signal_type 兼容性**：保持 `obv_bottom_divergence` / `obv_top_divergence`，不新增类型，原有 API / 看板契约不变。

**reason 格式**（示例）：`"价格创新极值但量能指标未同步（OBV+CMF 背离，强度:medium）"`

**强度分级**（`_divergence_strength_grade`）

强度档由「共振源数 × 各源归一化背离幅度均值」决定：

| 条件 | 强度档 |
|------|--------|
| k >= 3 且均值 > 0.15 | `strong` |
| k >= 2 且均值 > 0.05 | `medium` |
| 其余 | `weak` |

各源归一化方式：OBV（无界累积量）→ `|curr-prev| / max(|curr|, |prev|, 1)`；CMF → `|curr-prev| / 2`；MFI → `|curr-prev| / 100`。

### 5.3 量能形态分级

`classify_volume_pattern(rel_vol, pct_chg, atr_norm, config)` 对每根 bar 做一次量能形态分级，结果注入相关信号的 `reason` 字段（作为语义标注），不单独产出 signal_type：

| 返回值 | 触发条件（概述） |
|--------|----------------|
| `climax_volume` | rel_vol >= vol_high 且 pct_chg > eps（天量上涨） |
| `dry_up` | rel_vol < vol_low（地量，任意方向） |
| `shrink_pullback` | rel_vol ∈ [vol_low, vol_shrink) 且 pct_chg < -eps 且 \|pct_chg\| <= 1.5 × atr_norm |
| `mild_expand` | rel_vol ∈ [vol_up, vol_high) 且 pct_chg > eps（温和放量上涨） |
| `normal` | 其余所有组合（含跌幅超 ATR 边界的缩量下跌） |

当前使用场景：`volume_breakout` reason 含 `[量能形态:xxx]`；`shrink_pullback` reason 含 `[量能形态:xxx]`。

### 5.4 Crypto 参数差异化

`VPSConfig.for_market(market)` 工厂方法（M3-B 新增）：
- `market == "crypto"`：以 `VPS_CRYPTO_*` 三个值覆盖 `breakout_window`、`atr_period`、`breakout_rel_vol`，其余字段保持 `from_env()` 默认。
- 其余市场（含 `None`、未知字符串）：直接返回 `from_env()`，行为字节一致。
- 用法：`/signals` 端点传入 `VPSConfig.for_market(get_market_for_stock(code))`。

---

## 6. 鲁棒性契约

| 场景 | 行为 |
|------|------|
| `vol_ma <= 0` | `rel_vol = None`，进入 `degraded`，`degraded_reason` 说明原因 |
| 一字板（high == low） | 跳过该 bar 的 VSA 判断，不产生 Upthrust/Spring |
| 窗口不足（行数 < vol_ma_window + swing_k） | 返回 `degraded`，`markers` 为空 |
| 数据缺列（缺 open/high/low/close/volume） | 返回 `degraded`（`markers=[]`，`degraded_reason` 标注缺列）；`normalize_ohlcv` 内部 `ValueError` 由 `_normalize` 捕获转 degraded，不向上抛出 |

---

## 7. B 类降权契约

B 类信号（VSA Upthrust / Spring / No Demand / No Supply）受以下约束：

1. **置信硬上限**：`confidence` 强制为 `'low'`（覆盖计算值）。
2. **不进 consistency 投票**：M2a consistency 聚合只读取 A 类方向集合，B 类不参与。
3. **不驱动 price_lines**：M2b 价位反算器不消费 B 类信号的锚定价。
4. **top-k 限流**：每次 `compute_volume_price_signals` 调用，VSA/Upthrust/Spring 类总数上限为 `VPS_B_CLASS_TOP_K`（按 `observed_value` 绝对值降序保留 top-k）。量价八法 `vfx_*` 最新 bar 标注为单点，独立追加，不计入该 top-k。

---

## 7. VPS_* 可配项与默认值

所有可配项均由 `VPSConfig.from_env()` 读取，覆盖 `VPSConfig` 默认值。
`/signals` 端点已以 `compute_volume_price_signals(df, config=VPSConfig.from_env())` 接线，下列变量真正生效；未配置时使用括号内默认值，不影响运行。

### 7.1 通用参数（所有市场）

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

### 7.2 Crypto 旁路参数（仅 crypto 市场生效）

当 `VPSConfig.for_market("crypto")` 被调用时，下列三个参数会覆盖对应的通用值；其余参数保持通用默认，非 crypto 行为字节一致。

| 环境变量 | 默认值 | 约束 | 语义 |
|----------|--------|------|------|
| `VPS_CRYPTO_BREAKOUT_WINDOW` | `20` | >= 2，取整 | crypto 放量突破 high.rolling 窗口（覆盖 VPS_BREAKOUT_WINDOW） |
| `VPS_CRYPTO_ATR_PERIOD` | `14` | >= 2，取整 | crypto ATR 计算周期（覆盖 VPS_ATR_PERIOD） |
| `VPS_CRYPTO_BREAKOUT_REL_VOL` | `2.0` | >= 1 | crypto 放量突破 rel_vol 阈值（覆盖 VPS_BREAKOUT_REL_VOL） |

> **注意**：量基准口径（20 日）与 `_analyze_volume` 的 `VolumeStatus`（5 日）刻意分离，两者并存不互替。

### 7.3 interval 维度窗口阈值覆盖（仅链路B 分钟回测）

`VPSConfig.for_market_interval(market, interval)` 支持按分钟粒度覆盖 4 个 window 类参数，键命名 `VPS_<FIELD>_<INTERVAL 大写>`（`FIELD ∈ {VOL_MA_WINDOW, BREAKOUT_WINDOW, ATR_PERIOD, SWING_K}`，`INTERVAL ∈ {1M, 5M, 15M, 1H}`，共 16 键，见 `.env.example` 对应注释块）。

- **优先级**：interval 覆盖 > crypto 旁路（`VPS_CRYPTO_*`，见 §7.2）> 日线默认（§7.1）。
- **默认不配 = 复用日线值**：全部 16 键默认不设置，行为与不加本通道前字节一致。
- **interval-only**：仅在传入非日线 `interval`（如 `5m`/`15m`/`1h`）时生效；`interval=None` 或 `1d` 走既有 `for_market()` 路径，不受影响。
- **仅覆盖这 4 个 window 字段，通道存在明显部分性**：`ma5`/`ma20`、背离窗口（`_DIV_CMF_WINDOW`/`_DIV_MFI_WINDOW`，均为 14）、价位窗口（`_PRICE_LEVEL_WINDOW`，20）仍为日线硬编码常量，不受本通道影响，分钟粒度下这些窗口的语义仍按"根数"而非"日"解释。
- **warmup 门耦合**：warmup 判定 `min_bars = max(vol_ma_window, atr_period, breakout_window) + 1`，仅调小 `VPS_BREAKOUT_WINDOW_<interval>` 或 `VPS_ATR_PERIOD_<interval>` 未必缩短 warmup（若 `VPS_VOL_MA_WINDOW_<interval>` 仍为默认 20 会继续主导三者 max）；需要真正缩短分钟 warmup 须一并设置 `VPS_VOL_MA_WINDOW_<interval>`。
- **本期不做经验定值**：16 键均以注释形式出现在 `.env.example`，不预置分钟专用默认值；具体分钟窗口的合理取值待真实数据标定后再补。

---

## 8. 回滚方式

删除以下文件即可完整回滚 M1，无任何主流程依赖：

- `src/services/volume_price_signals.py`
- `tests/test_volume_price_signals.py`
- `docs/volume-price-signals.md`
- 还原 `.env.example` 末尾 `VPS_*` 追加段
- 还原 `docs/CHANGELOG.md` 对应一行
