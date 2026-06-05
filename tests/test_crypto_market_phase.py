"""crypto 盘口阶段回归。

数字货币 7×24 连续交易：恒视为“盘中”（市场开放、当日 K 线持续形成未完结）。
此前 crypto 不在 MARKET_EXCHANGE，infer_market_phase / build_market_phase_context
都会落到 UNKNOWN，导致分析上下文拿不到“连续交易、当日 bar 未完结”的语义。
"""
from src.core.trading_calendar import (
    MarketPhase,
    infer_market_phase,
    build_market_phase_context,
)


def test_infer_market_phase_crypto_is_intraday():
    assert infer_market_phase("crypto") == MarketPhase.INTRADAY


def test_build_market_phase_context_crypto_continuous():
    ctx = build_market_phase_context(market="crypto")
    assert ctx.phase == MarketPhase.INTRADAY
    assert ctx.is_trading_day is True
    assert ctx.is_market_open_now is True
    assert ctx.is_partial_bar is True  # 当日 UTC K 线仍在形成
    # 24/7 无开盘/收盘概念
    assert ctx.minutes_to_open is None
    assert ctx.minutes_to_close is None


def test_build_market_phase_context_crypto_respects_explicit_phase():
    # 显式指定 phase 时仍以显式值为准（不被 crypto 分支覆盖）
    ctx = build_market_phase_context(market="crypto", analysis_phase="postmarket")
    assert ctx.phase == MarketPhase.POSTMARKET


def test_stock_market_phase_unaffected():
    # 非 crypto 市场行为不变：cn 仍走日历推断（结果取决于运行时刻，但不应为 INTRADAY 硬编码）
    phase = infer_market_phase("cn")
    assert isinstance(phase, MarketPhase)
