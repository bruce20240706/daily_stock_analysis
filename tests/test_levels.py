from __future__ import annotations

from quantpick.signals.levels import build_levels, stop_from_atr


def test_stop_uses_atr_when_tighter_than_swing() -> None:
    # entry 100, atr 5, mult 2 -> atr stop 90; swing 85 -> max(90, 85) = 90
    assert stop_from_atr(100.0, 5.0, 85.0, 2.0) == 90.0


def test_stop_uses_swing_when_above_atr_stop() -> None:
    # atr stop 90, swing 95 -> 95 (never risk more than the swing low)
    assert stop_from_atr(100.0, 5.0, 95.0, 2.0) == 95.0


def test_build_levels_reward_risk() -> None:
    lv = build_levels(entry=100.0, atr=5.0, swing_low=85.0, atr_mult=2.0, target_rr=2.0)
    assert lv.stop == 90.0
    assert lv.target == 120.0          # risk 10 -> target 100 + 2*10
    assert abs(lv.risk_reward - 2.0) < 1e-9
