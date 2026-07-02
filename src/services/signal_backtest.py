# -*- coding: utf-8 -*-
"""信号规则级三重门回测（纯函数，bar-interval 无关）。

对历史逐 bar 因果重跑信号规则 + derive_price_levels，
前瞻 horizon 根 bar 判 赢/输/平（三重门）。

设计要点：
- 因果约束：评估 bar t 仅使用 df.iloc[:t+1]，绝无未来函数。
- 右端截尾：评估范围 range(min_history, n-horizon)，保证每个被评估 bar 都有完整
  horizon 根前瞻，消除右端欠龄 bar 引入的截尾偏差（见 C1 修复）。
- 信号查询：先对全 df 调用 compute_signals_for_all_bars(df) 单遍预计算，
  得到每根 bar 因果触发的 bullish signal_type 集合；_eval 逐 bar O(1) 查表命中，
  无需在每根 bar 内重算窗口。价位走 derive_price_levels_series 统一预计算。
- 因果修正语义（B 类）：_streaming_topk_kept 实现因果流式 top-k，
  去除逐窗非因果信号池、未确认 pivot 及历史 partial 墙钟 quirk，
  命中率统计更因果正确（见 docs §5.2）。
- 基线对照：baseline 每个有效入场 bar 均入场一次（全体 bar 基准），signal_type 固定为 __baseline__。
- sample = win + loss（expired 被排除在胜率分母外，因未触及止损）。
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import NormalDist
from typing import List, Optional

import pandas as pd

from src.services.volume_price_signals import (
    VPSConfig,
    compute_signals_for_all_bars,
    derive_price_levels_series,
)

BASELINE_SIGNAL_TYPE = "__baseline__"


@dataclass(frozen=True)
class SignalOutcome:
    """单次信号触发的三重门回测结果。

    signal_type  信号类型（与 VPSignal.signal_type 对应；baseline 固定 '__baseline__'）
    market       市场标识（'cn' / 'hk' / 'us' 等，透传自调用方）
    outcome      'win' | 'loss' | 'expired'
    """

    signal_type: str
    market: str
    outcome: str


@dataclass(frozen=True)
class TripleBarrierResult:
    outcome: str                      # 'win' | 'loss' | 'expired'
    return_pct: Optional[float]       # 保守跳空感知收益(%);失真形态/无效数据为 None


def _classify_core(forward_bars, *, stop, target):
    """共核:返回 (outcome, hit_idx);hit_idx=触障 bar 下标,expired 为 None。"""
    for i, bar in enumerate(forward_bars):
        hit_target = bar["high"] >= target
        hit_stop = bar["low"] <= stop
        if hit_target and hit_stop:
            return "loss", i     # 同 bar 两触:保守判 loss(语义不变)
        if hit_stop:
            return "loss", i
        if hit_target:
            return "win", i
    return "expired", None


def classify_triple_barrier(forward_bars, *, stop, target) -> str:
    """(既有签名/语义零变,变薄 wrapper)"""
    return _classify_core(forward_bars, stop=stop, target=target)[0]


def classify_triple_barrier_with_return(forward_bars, *, stop, target, entry) -> TripleBarrierResult:
    """三重门分类 + 保守跳空感知收益(D1 用户拍板 + 形态级失真守卫)。

    失真守卫(outcome 无关,round2 Blocker 修正):target <= entry(触发 close 已越过回踩锚
    target 的动量/突破形态)→ 不论 win/loss/expired 一律 return_pct=None——win-only 剔除会
    单边截断(赢的不计、输的全计),整层剔除才保收益序列无选择偏差。

    win:     exit = target(跳空高开不多计盈利)
    loss:    exit = min(触障 bar open, stop)(跳空低开按更差的 open;open 非有限或 <=0 回退 stop,
             0.0 哨兵/NaN 不产假 -100;含 open>=target 高开双杀子案,同取保守,见 §6)
    expired: exit = forward_bars[-1]['close'](窗末平仓,D8:计入收益序列)
    return_pct = (exit - entry)/entry*100,下钳 >= -100;
    entry/exit 非有限或 (==0) → None(防御,outcome 分类不受影响)。
    """
    outcome, hit_idx = _classify_core(forward_bars, stop=stop, target=target)
    if entry is None or not math.isfinite(entry) or entry <= 0 or not forward_bars:
        return TripleBarrierResult(outcome, None)
    if target <= entry:                        # 形态级失真守卫(D1,outcome 之外)
        return TripleBarrierResult(outcome, None)
    if outcome == "win":
        exit_price = target
    elif outcome == "loss":
        o = forward_bars[hit_idx]["open"]
        exit_price = min(o, stop) if (isinstance(o, (int, float)) and math.isfinite(o) and o > 0) else stop
    else:
        exit_price = forward_bars[-1]["close"]
    if not (isinstance(exit_price, (int, float)) and math.isfinite(exit_price) and exit_price != 0.0):
        return TripleBarrierResult(outcome, None)   # 终门:NaN/0 哨兵一律 None,绝不毒化聚合
    return TripleBarrierResult(outcome, max((exit_price - entry) / entry * 100.0, -100.0))


def _bars_as_dicts(df: pd.DataFrame) -> List[dict]:
    """将 DataFrame 子集转为 classify_triple_barrier 所需的 dict list。"""
    return df[["open", "high", "low", "close"]].to_dict("records")


def _eval(
    df: pd.DataFrame,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig],
    all_bars: bool,
    min_history: int,
) -> List[SignalOutcome]:
    """内部：逐 bar 因果走查，产出 SignalOutcome 列表。

    Args:
        df:          完整历史 OHLCV DataFrame（含 date 列）。
        market:      市场标识，透传至 SignalOutcome。
        horizon:     完整前瞻 bar 数；评估上界为 n-horizon，确保每个被评估 bar 都有
                     完整 horizon 根前瞻，消除右端截尾偏差。
        config:      VPSConfig，None 时使用默认值。
        all_bars:    True → baseline 模式（每个有效 bar 均入场）；
                     False → 信号模式（仅当前 bar 触发的 bullish 信号入场）。
        min_history: 进入评估前所需最小历史 bar 数（因果预热）。

    Returns:
        SignalOutcome 列表。
    """
    cfg = config or VPSConfig.from_env()
    df = df.reset_index(drop=True)
    out: List[SignalOutcome] = []
    n = len(df)

    levels = derive_price_levels_series(df)                                 # O(n)，全量预计算价位
    sig_by_bar = {} if all_bars else compute_signals_for_all_bars(df, config=cfg)  # O(n log k)

    for t in range(min_history, n - horizon):                              # 保证完整 horizon 根前瞻（右端欠龄不计入）
        lv = levels[t]
        if lv.stop is None or lv.target is None:
            continue
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue
        if all_bars:
            out.append(SignalOutcome(
                signal_type=BASELINE_SIGNAL_TYPE, market=market,
                outcome=classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
        else:
            for sig_type in sig_by_bar.get(t, ()):                         # O(1) 查表
                out.append(SignalOutcome(
                    signal_type=sig_type, market=market,
                    outcome=classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
    return out


def evaluate_signal_outcomes(
    df,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig] = None,
    min_history: int = 40,
) -> List[SignalOutcome]:
    """逐 bar 因果重跑信号规则，返回所有触发信号的三重门结果。

    仅收录 direction=='bullish' 且触发于当前 bar（timestamp 与 window 末行对齐）的信号。
    expired 结果保留在输出中；胜率分母（sample）= win + loss 由调用方计算。

    Args:
        df:          完整历史 OHLCV DataFrame（date/open/high/low/close/volume）。
        market:      市场标识（透传至 SignalOutcome.market）。
        horizon:     完整前瞻 bar 数；仅 [min_history, n-horizon) 内的 bar 参与评估。
        config:      VPSConfig，None 时使用默认值。
        min_history: 进入评估前所需最小历史 bar 数。

    Returns:
        SignalOutcome 列表，可能为空。
    """
    return _eval(
        df,
        market=market,
        horizon=horizon,
        config=config,
        all_bars=False,
        min_history=min_history,
    )


def evaluate_baseline_outcomes(
    df,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig] = None,
    min_history: int = 40,
) -> List[SignalOutcome]:
    """全体 bar 基准回测：每个有效入场 bar 均产生一条 __baseline__ 记录。

    用于与 evaluate_signal_outcomes 对比，衡量信号相对于随机入场的超额。
    signal_type 固定为 '__baseline__'（BASELINE_SIGNAL_TYPE）。

    Args:
        df:          完整历史 OHLCV DataFrame（date/open/high/low/close/volume）。
        market:      市场标识（透传至 SignalOutcome.market）。
        horizon:     完整前瞻 bar 数；仅 [min_history, n-horizon) 内的 bar 参与评估。
        config:      VPSConfig，None 时用于 derive_price_levels 默认值（baseline 不跑信号规则）。
        min_history: 进入评估前所需最小历史 bar 数。

    Returns:
        SignalOutcome 列表，数量 >= evaluate_signal_outcomes 的触发数。
    """
    return _eval(
        df,
        market=market,
        horizon=horizon,
        config=config,
        all_bars=True,
        min_history=min_history,
    )


# ---------------------------------------------------------------------------
# Task A2: 统计聚合 + Wilson CI + 基准超额（纯函数）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalStat:
    """单个 (signal_type × market) 的聚合统计结果。

    Attributes:
        signal_type:        信号类型（与 SignalOutcome.signal_type 对应）。
        market:             市场标识（与 SignalOutcome.market 对应）。
        interval:           K 线周期标识（如 '1d'）。
        horizon:            前瞻 bar 数（与回测 horizon 参数一致）。
        win:                胜次数（outcome == 'win'）。
        loss:               败次数（outcome == 'loss'）。
        sample:             有效样本数 = win + loss（expired 不计入）。
        win_rate:           胜率 = win / sample；sample == 0 时为 None。
        ci_low:             Wilson 95% CI 下界；sample == 0 时为 None。
        ci_high:            Wilson 95% CI 上界；sample == 0 时为 None。
        baseline_win_rate:  同市场全体 bar 基准胜率；无基准数据时为 None。
        excess:             超额 = ci_low - baseline_win_rate；任一为 None 时为 None。
        ci_low_corrected:   family-wise 校正后 Wilson 下界；N<=1 时==ci_low；sample==0 时 None。
        family_size:        本次聚合可检验格子数 N(sample>=min_sample 的格子数)。
    """

    signal_type: str
    market: str
    interval: str
    horizon: int
    win: int
    loss: int
    sample: int
    win_rate: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    baseline_win_rate: Optional[float]
    excess: Optional[float]
    ci_low_corrected: Optional[float] = None
    family_size: int = 0


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    """计算 Wilson score 95% 置信区间。

    Args:
        wins: 成功次数（0 <= wins <= n）。
        n:    总样本数。
        z:    正态分布临界值，默认 1.96（95% 双尾）。

    Returns:
        (ci_low, ci_high)，均 clamped 至 [0, 1]；n == 0 → (0.0, 0.0)。
    """
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bonferroni_z(family_n: int, fwer_alpha: float = 0.05) -> float:
    """family-wise Bonferroni 校正后的 Wilson z 值。

    N<=1 返回字面量 1.96(与 wilson_ci 默认 z 逐字节一致,保证退化恒等;
    不得改用 NormalDist().inv_cdf(0.975)=1.95996…,razor-edge 下会翻 verified)。
    N>=2 按双尾族水平 alpha 均摊:z = inv_cdf(1 - (alpha/2)/N)。
    alpha 在函数内钳制到 [0.0001, 0.05]:上限 0.05 保证 z >= 2.2414 > 1.96
    ("校正只收紧"不可绕过);下限 0.0001 防 inv_cdf(1.0) StatisticsError。
    注意 alpha 是双尾口径,等价单尾族错误率约 alpha/2(verified 为单尾判据)。
    """
    if family_n <= 1:
        return 1.96
    alpha = min(max(float(fwer_alpha), 0.0001), 0.05)
    return NormalDist().inv_cdf(1.0 - (alpha / 2.0) / family_n)


def _winrate(wins: int, n: int) -> Optional[float]:
    """返回 wins/n（保留 4 位小数），n == 0 时返回 None（避免 ZeroDivisionError）。"""
    return round(wins / n, 4) if n > 0 else None


def aggregate_signal_stats(
    outcomes: List[SignalOutcome],
    baseline_outcomes: List[SignalOutcome],
    *,
    horizon: int,
    interval: str = "1d",
    fwer_alpha: float = 0.05,
    min_sample: int = 10,
) -> List[SignalStat]:
    """按 (signal_type × market) 聚合回测结果，附 Wilson CI 与基准超额。

    Args:
        outcomes:          信号触发结果列表（来自 evaluate_signal_outcomes）。
        baseline_outcomes: 全体 bar 基准结果列表（来自 evaluate_baseline_outcomes 或手工构造）。
                           signal_type 应为 BASELINE_SIGNAL_TYPE（'__baseline__'）。
        horizon:           前瞻 bar 数，透传至 SignalStat.horizon。
        interval:          K 线周期标识，透传至 SignalStat.interval，默认 '1d'。
        fwer_alpha:        family-wise 双尾族水平(Bonferroni-CI 校正),函数内钳制到
                           [0.0001, 0.05]。默认 0.05 仅供纯函数独测;生产路径必须显式传
                           config.signal_backtest_fwer_alpha。
        min_sample:        可检验格子的最小样本阈值(family N 的口径)。默认 10 仅供独测;
                           生产路径必须显式传 resolve_verified_min_sample(cfg),
                           否则 N 与读路径 verified 判据漂移。

    Returns:
        每个 (signal_type × market) 对应一个 SignalStat 的列表。
        不包含 __baseline__ 自身的 SignalStat（仅作为基准参考）。
    """
    # Step 1: 计算各市场基准胜率（expired 及未知 outcome 排除在分母外）
    base_w: dict = defaultdict(int)
    base_n: dict = defaultdict(int)
    for o in baseline_outcomes:
        if o.outcome not in ("win", "loss"):
            continue
        base_n[o.market] += 1
        if o.outcome == "win":
            base_w[o.market] += 1
    baseline_rate = {m: _winrate(base_w[m], base_n[m]) for m in base_n}

    # Step 2: 按 (signal_type, market) 分桶统计（expired 及未知 outcome 排除在分母外）
    buckets: dict = defaultdict(lambda: {"win": 0, "loss": 0})
    for o in outcomes:
        if o.outcome not in ("win", "loss"):
            continue
        buckets[(o.signal_type, o.market)][o.outcome] += 1

    # Step 3a: 先算各格 win/loss/sample(第一遍,确定 family N)
    cells = []
    for (sig_type, market), wl in buckets.items():
        win = wl["win"]
        loss = wl["loss"]
        cells.append((sig_type, market, win, loss, win + loss))

    # Step 3b: family N = sample >= min_sample 的格子数;z_corr 每 family 只算一次
    family_n = sum(1 for _, _, _, _, sample in cells if sample >= min_sample)
    z_corr = bonferroni_z(family_n, fwer_alpha)

    # Step 3c: 构造 SignalStat(第二遍;raw ci_low/ci_high/excess 计算与现状逐字节一致)
    stats: List[SignalStat] = []
    for sig_type, market, win, loss, sample in cells:
        wr = _winrate(win, sample)
        if sample > 0:
            ci_low, ci_high = wilson_ci(win, sample)
            ci_low_corrected: Optional[float] = wilson_ci(win, sample, z_corr)[0]
        else:
            ci_low, ci_high = None, None
            ci_low_corrected = None
        base = baseline_rate.get(market)
        if ci_low is not None and base is not None:
            excess: Optional[float] = round(ci_low - base, 4)
        else:
            excess = None
        stats.append(
            SignalStat(
                signal_type=sig_type,
                market=market,
                interval=interval,
                horizon=horizon,
                win=win,
                loss=loss,
                sample=sample,
                win_rate=wr,
                ci_low=ci_low,
                ci_high=ci_high,
                baseline_win_rate=base,
                excess=excess,
                ci_low_corrected=ci_low_corrected,
                family_size=family_n,
            )
        )
    return stats
