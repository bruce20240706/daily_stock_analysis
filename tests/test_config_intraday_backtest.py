# -*- coding: utf-8 -*-
"""Tests for intraday backtest config fields (Task 2)."""
import os
from unittest.mock import patch

import pytest

from src.config import Config


def _load_config(**env):
    """Load a fresh Config from env via _load_from_env with the given overrides."""
    with patch("src.config.setup_env"), \
         patch.object(Config, "_parse_litellm_yaml", return_value=[]), \
         patch.dict(os.environ, env, clear=False):
        Config.reset_instance()
        cfg = Config._load_from_env()
    Config.reset_instance()
    return cfg


def test_intraday_defaults_are_status_quo():
    """All 7 new fields must have status-quo defaults when no env var is set."""
    env_overrides = {}
    for k in [
        "CRYPTO_INTRADAY_BACKTEST_INTERVAL", "CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S",
        "CRYPTO_INTRADAY_BACKTEST_FEE_BPS", "CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS",
        "INTRADAY_BACKTEST_ENABLED", "INTRADAY_BACKTEST_SCHEDULE_MINUTES",
        "ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS",
        "HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS",
    ]:
        # Ensure these vars are absent from env during the test
        os.environ.pop(k, None)

    cfg = _load_config(**env_overrides)
    assert cfg.crypto_intraday_backtest_interval == "5m"
    assert cfg.crypto_intraday_minute_cache_ttl_s == 900
    assert cfg.crypto_intraday_backtest_fee_bps == 0.0
    assert cfg.crypto_intraday_backtest_slippage_bps == 0.0
    assert cfg.intraday_backtest_enabled is False
    assert cfg.intraday_backtest_schedule_minutes == 60
    assert cfg.ashare_intraday_backtest_stamp_duty_bps == 0.0
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 0.0


def test_intraday_env_override():
    """Env vars must override each field correctly."""
    cfg = _load_config(
        CRYPTO_INTRADAY_BACKTEST_INTERVAL="15m",
        CRYPTO_INTRADAY_BACKTEST_FEE_BPS="4",
        INTRADAY_BACKTEST_ENABLED="true",
        INTRADAY_BACKTEST_SCHEDULE_MINUTES="30",
        ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS="5",
        HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS="10",
    )
    assert cfg.crypto_intraday_backtest_interval == "15m"
    assert cfg.crypto_intraday_backtest_fee_bps == 4.0
    assert cfg.intraday_backtest_enabled is True
    assert cfg.intraday_backtest_schedule_minutes == 30
    assert cfg.ashare_intraday_backtest_stamp_duty_bps == 5.0
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 10.0


def test_hk_stamp_duty_negative_clamped_to_zero():
    # parse_env_float minimum=0.0: 负值被钳制到 0.0(记 warning,不抛错、不回退 default)
    cfg = _load_config(HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS="-3")
    assert cfg.hk_intraday_backtest_stamp_duty_bps == 0.0
