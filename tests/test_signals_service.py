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
