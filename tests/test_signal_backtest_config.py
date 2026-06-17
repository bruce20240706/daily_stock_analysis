"""
TDD tests for Task A4: SIGNAL_BACKTEST_* config fields and VPS_CRYPTO_* bypass thresholds.

Config factory: Config._load_from_env() (singleton reset not needed for env reads;
the brief's Config.from_env() is an alias — actual method is _load_from_env).
"""
import os
from src.config import Config
from src.services.volume_price_signals import VPSConfig


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
