# -*- coding: utf-8 -*-
"""Unit tests for signals_service consistency/stale logic (M2a)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from src.stock_analyzer import BuySignal
from src.services.signals_service import (
    buy_signal_to_direction,
    compute_consistency,
    date_str_to_epoch_ms,
    datetime_to_epoch_ms,
    STALE_TRADING_DAYS_DEFAULT,
    build_signals_payload,
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


# ---------------------------------------------------------------------------
# Task 4: build_signals_payload tests
# ---------------------------------------------------------------------------

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


def test_build_payload_llm_marker_price_uses_latest_close_when_provided():
    """N1 fix: LLM marker 的 price 应等于 latest_close，而非默认 0.0。"""
    engine = _engine_result([])  # 无 rule markers，隔离 LLM 路径
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
        latest_close=150.0,
    )

    llm_markers = [m for m in payload["markers"] if m["source"] == "llm"]
    assert len(llm_markers) == 1
    assert llm_markers[0]["price"] == 150.0


# ---------------------------------------------------------------------------
# M2c-5：resolver 注入 & verified 回填
# ---------------------------------------------------------------------------

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


def test_build_payload_memoizes_resolver_per_code_across_markers():
    """D7 修正：M3-A6 起 resolve_marker_hit_fields 按 (signal_type, market) 查
    signal_stats，signal_type 改变聚合源，故缓存键须是 (signal_type, code)。多个
    rule marker 时，同一 (signal_type, code) 的 resolver 只应调用一次（按需复用），
    不同 signal_type 各查各的，避免同键重复 SELECT 的同时不再跨 signal_type 错挂字段。"""
    # 三个 rule marker，signal_type 各异，但 code 相同
    engine = _engine_result([
        _vpsignal(date_str_to_epoch_ms("2026-06-10"), signal_type="volume_breakout"),
        _vpsignal(date_str_to_epoch_ms("2026-06-11"), signal_type="upthrust"),
        _vpsignal(date_str_to_epoch_ms("2026-06-12"), signal_type="volume_breakout"),
    ])

    calls = []

    def _resolver(signal_type, code):
        calls.append((signal_type, code))
        return {"hit_rate": 0.6, "hit_sample": 20, "verified": True}

    payload = build_signals_payload(
        engine_result=engine,
        rule_signal=BuySignal.BUY,
        latest_bar_date="2026-06-12",
        llm_record=None,
        trading_days_elapsed=None,
        code="600519",
        hit_fields_resolver=_resolver,
    )

    # 同一 (signal_type, code) 只查一次：2 个不同 signal_type → 2 次调用
    # （非修复前的 1 次 code-only 缓存，也非无缓存时的 3 次）
    assert len(calls) == 2
    assert sorted(calls) == [("upthrust", "600519"), ("volume_breakout", "600519")]

    # 所有 rule marker 仍都被回填
    rule_markers = [m for m in payload["markers"] if m["source"] == "rule"]
    assert len(rule_markers) == 3
    for m in rule_markers:
        assert m["hit_rate"] == 0.6
        assert m["hit_sample"] == 20
        assert m["verified"] is True


# ---------------------------------------------------------------------------
# M3-A6：marker 透传 ci_low/ci_high/baseline_excess 字段
# ---------------------------------------------------------------------------

def _engine_with_one_rule_marker():
    """构造含一个 bullish rule-type VPSignal 的 VPSResult (SimpleNamespace)。"""
    sig = SimpleNamespace(
        timestamp=date_str_to_epoch_ms("2026-06-16"),
        price=1800.0,
        anchor="close",
        direction="bullish",
        signal_type="volume_breakout",
        confidence="high",
        is_daily_approx=False,
        is_anomalous=False,
        reason="放量突破",
        threshold=2.0,
        observed_value=2.5,
    )
    return SimpleNamespace(markers=[sig], status="ok", degraded_reason=None)


def test_marker_carries_ci_fields_from_resolver():
    def resolver(signal_type, code):
        return {"hit_rate": 0.68, "hit_sample": 20, "verified": True,
                "ci_low": 0.55, "ci_high": 0.80, "baseline_excess": 0.05}

    payload = build_signals_payload(
        engine_result=_engine_with_one_rule_marker(), rule_signal=None,
        latest_bar_date="2026-06-16", llm_record=None,
        trading_days_elapsed=None, code="600519", hit_fields_resolver=resolver,
    )
    m = next(x for x in payload["markers"] if x["source"] == "rule")
    assert m["ci_low"] == 0.55 and m["ci_high"] == 0.80 and m["baseline_excess"] == 0.05


# ---------------------------------------------------------------------------
# D7: per-call resolver cache 曾按 code-only 缓存,跨 signal_type 错挂 stats(现役 bug 修复)
# ---------------------------------------------------------------------------

def test_resolver_cache_keyed_by_signal_type_and_code():
    """同股两个不同 signal_type 的 marker 必须各拿各的 resolver fields。

    修复前:cache 键=code,第二个 signal_type 拿到第一个的 fields(hit_rate/verified 全错挂)。
    """
    calls = []

    def resolver(signal_type, code):
        calls.append(signal_type)
        return {"hit_rate": 0.9 if signal_type == "volume_breakout" else 0.1,
                "hit_sample": 30, "verified": signal_type == "volume_breakout",
                "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": 10,
                "ci_low_corrected": None, "family_size": None}

    engine = _engine_result([
        _vpsignal(date_str_to_epoch_ms("2026-06-01"), signal_type="volume_breakout"),
        _vpsignal(date_str_to_epoch_ms("2026-06-01"), signal_type="obv_top_divergence"),
    ])
    payload = build_signals_payload(
        engine_result=engine, rule_signal=None, llm_record=None,
        latest_bar_date="2026-06-01", latest_close=10.0,
        trading_days_elapsed=0, code="600519", hit_fields_resolver=resolver,
    )
    m1, m2 = payload["markers"][0], payload["markers"][1]
    assert m1["hit_rate"] == 0.9 and m1["verified"] is True
    assert m2["hit_rate"] == 0.1 and m2["verified"] is False      # 修复前=0.9/True(错挂)
    # 缓存仍有效:每 signal_type 恰好一次 resolver 调用
    assert sorted(calls) == ["obv_top_divergence", "volume_breakout"]
