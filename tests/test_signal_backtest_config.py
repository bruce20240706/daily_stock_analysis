"""
TDD tests for Task A4: SIGNAL_BACKTEST_* config fields and VPS_CRYPTO_* bypass thresholds.

Config factory: Config._load_from_env() (singleton reset not needed for env reads;
the brief's Config.from_env() is an alias — actual method is _load_from_env).
"""
import pytest
from src.config import Config
from src.services.volume_price_signals import VPSConfig, _INTERVAL_OVERRIDE_FIELDS  # noqa: F401


def test_signal_backtest_config_defaults(monkeypatch):
    for k in ("SIGNAL_BACKTEST_ENABLED", "SIGNAL_BACKTEST_HORIZON_BARS"):
        monkeypatch.delenv(k, raising=False)
    c = Config._load_from_env()
    assert c.signal_backtest_enabled is False
    assert c.signal_backtest_horizon_bars == 10


def test_signal_backtest_enabled_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_BACKTEST_ENABLED", "true")
    assert Config._load_from_env().signal_backtest_enabled is True


def test_signal_backtest_horizon_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_BACKTEST_HORIZON_BARS", "15")
    assert Config._load_from_env().signal_backtest_horizon_bars == 15


def test_vpsconfig_crypto_params_defaults():
    """crypto fields must default to the same values as their non-crypto counterparts."""
    cfg = VPSConfig()
    assert cfg.crypto_breakout_window == cfg.breakout_window
    assert cfg.crypto_atr_period == cfg.atr_period
    assert cfg.crypto_breakout_rel_vol == cfg.breakout_rel_vol


def test_vpsconfig_crypto_params_from_env(monkeypatch):
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    cfg = VPSConfig.from_env()
    assert cfg.crypto_breakout_window == 30
    # 非 crypto 默认不被污染
    monkeypatch.delenv("VPS_CRYPTO_BREAKOUT_WINDOW", raising=False)
    assert VPSConfig.from_env().crypto_breakout_window == VPSConfig().crypto_breakout_window


def test_vpsconfig_crypto_atr_period_from_env(monkeypatch):
    monkeypatch.setenv("VPS_CRYPTO_ATR_PERIOD", "21")
    cfg = VPSConfig.from_env()
    assert cfg.crypto_atr_period == 21


def test_vpsconfig_crypto_breakout_rel_vol_from_env(monkeypatch):
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_REL_VOL", "1.5")
    cfg = VPSConfig.from_env()
    assert cfg.crypto_breakout_rel_vol == 1.5


def test_vpsconfig_non_crypto_behavior_unchanged(monkeypatch):
    """Setting VPS_CRYPTO_* must not alter any non-crypto VPSConfig fields."""
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "30")
    monkeypatch.setenv("VPS_CRYPTO_ATR_PERIOD", "21")
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_REL_VOL", "1.5")
    cfg = VPSConfig.from_env()
    assert cfg.breakout_window == 20
    assert cfg.atr_period == 14
    assert cfg.breakout_rel_vol == 2.0


@pytest.mark.parametrize("interval", ["1d", "1m", "5m", "15m", "1h"])
@pytest.mark.parametrize("market", ["cn", "crypto"])
def test_for_market_interval_default_matches_for_market(interval, market):
    # 无任何 VPS_*_<interval> env → 与 for_market 字段级一致（F3：用字段相等，非 identity）
    assert VPSConfig.for_market_interval(market, interval) == VPSConfig.for_market(market)


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1h"])
def test_for_market_interval_single_field_override(monkeypatch, interval):
    # F4：参数化跑全 4 个分钟 interval，catch 1h->1H 等后缀映射 bug
    monkeypatch.setenv(f"VPS_BREAKOUT_WINDOW_{interval.upper()}", "10")
    cfg = VPSConfig.for_market_interval("cn", interval)
    assert cfg.breakout_window == 10
    # 其余 window 字段保持日线默认
    assert cfg.vol_ma_window == 20
    assert cfg.atr_period == 14
    assert cfg.swing_k == 3
    # 乘数类不受影响
    assert cfg.breakout_rel_vol == 2.0


def test_for_market_interval_crypto_unset_keeps_crypto_window():
    expected = VPSConfig.from_env().crypto_breakout_window
    assert VPSConfig.for_market_interval("crypto", "5m").breakout_window == expected


def test_for_market_interval_override_beats_crypto(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "7")
    assert VPSConfig.for_market_interval("crypto", "5m").breakout_window == 7


def test_for_market_interval_clamps_to_minimum(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "1")  # 低于下限 2
    assert VPSConfig.for_market_interval("cn", "5m").breakout_window == 2


def test_for_market_interval_invalid_falls_back_cn(monkeypatch):
    monkeypatch.setenv("VPS_ATR_PERIOD_15M", "abc")
    assert VPSConfig.for_market_interval("cn", "15m").atr_period == 14


def test_for_market_interval_invalid_falls_back_to_crypto_base(monkeypatch):
    # F4：crypto 非法值变体——回落到 crypto base（21），而非日线 14
    monkeypatch.setenv("VPS_CRYPTO_ATR_PERIOD", "21")
    monkeypatch.setenv("VPS_ATR_PERIOD_5M", "abc")
    assert VPSConfig.for_market_interval("crypto", "5m").atr_period == 21


def test_for_market_interval_isolation_across_intervals(monkeypatch):
    monkeypatch.setenv("VPS_SWING_K_5M", "5")
    assert VPSConfig.for_market_interval("cn", "5m").swing_k == 5
    assert VPSConfig.for_market_interval("cn", "15m").swing_k == 3  # 5m 不泄漏到 15m


def test_for_market_interval_1d_and_none_unchanged(monkeypatch):
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "10")  # 不应影响 1d / None
    base = VPSConfig.for_market("cn")
    assert VPSConfig.for_market_interval("cn", "1d") == base
    assert VPSConfig.for_market_interval("cn", None) == base


def test_for_market_interval_warmup_gate_coupling(monkeypatch):
    # M7：warmup min_bars = max(vol_ma_window, atr_period, breakout_window)+1。
    # 仅调小 breakout_window 不缩短（vol_ma_window=20 主导）；三者同调才缩短。
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW_5M", "5")
    cfg = VPSConfig.for_market_interval("cn", "5m")
    assert max(cfg.vol_ma_window, cfg.atr_period, cfg.breakout_window) == 20
    monkeypatch.setenv("VPS_VOL_MA_WINDOW_5M", "6")
    monkeypatch.setenv("VPS_ATR_PERIOD_5M", "4")
    cfg2 = VPSConfig.for_market_interval("cn", "5m")
    assert max(cfg2.vol_ma_window, cfg2.atr_period, cfg2.breakout_window) == 6
