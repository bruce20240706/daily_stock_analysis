"""
TDD tests for Task A4: SIGNAL_BACKTEST_* config fields and VPS_CRYPTO_* bypass thresholds.

Config factory: Config._load_from_env() (singleton reset not needed for env reads;
the brief's Config.from_env() is an alias — actual method is _load_from_env).
"""
import re
from pathlib import Path

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


def _interval_override_keys():
    from src.core.intraday_backtest import INTRADAY_INTERVAL_MINUTES
    return {
        f"VPS_{field.upper()}_{interval.upper()}"
        for interval in INTRADAY_INTERVAL_MINUTES
        for field in _INTERVAL_OVERRIDE_FIELDS
    }


def _env_example_path():
    return Path(__file__).resolve().parents[1] / ".env.example"


def test_env_example_documents_all_interval_override_keys():
    # 防漂移闭环（I1）：canonical 每个分钟 interval × 4 字段，均须在 .env.example 有注释行。
    # 中心给 INTRADAY_INTERVAL_MINUTES 加新 interval（如 30m）→ 此测试 RED → 强制补文档。
    text = _env_example_path().read_text(encoding="utf-8")
    missing = sorted(k for k in _interval_override_keys() if f"# {k}=" not in text)
    assert missing == [], f"缺注释行的 interval 覆盖键: {missing}"


def test_interval_override_keys_only_commented_in_env_example():
    # I2：16 键严禁裸 KEY= 活动行（否则 RED env-example 覆盖门）。
    keys = _interval_override_keys()
    active = set()
    for line in _env_example_path().read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip())
        if m and m.group(1) in keys:
            active.add(m.group(1))
    assert active == set(), f"这些键必须为注释行、不得为活动赋值: {sorted(active)}"


def test_resolve_verified_min_sample_shared_helper(monkeypatch):
    """Inc 1c: 读写共用 min_sample 解析(spec §4.7);显式设 SIGNAL_HIT_VERIFIED_MIN_SAMPLE 优先。"""
    from src.services.signal_hit_rate import resolve_verified_min_sample
    monkeypatch.delenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", raising=False)
    monkeypatch.delenv("BACKTEST_EVAL_WINDOW_DAYS", raising=False)
    c = Config._load_from_env()
    # 未显式设置时 loader 已把 signal_hit_verified_min_sample 回落为 eval_window(=10)
    assert resolve_verified_min_sample(c) == 10
    monkeypatch.setenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", "3")
    assert resolve_verified_min_sample(Config._load_from_env()) == 3
    monkeypatch.delenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", raising=False)
    monkeypatch.setenv("BACKTEST_EVAL_WINDOW_DAYS", "15")
    assert resolve_verified_min_sample(Config._load_from_env()) == 15


def test_fwer_alpha_default(monkeypatch):
    monkeypatch.delenv("SIGNAL_BACKTEST_FWER_ALPHA", raising=False)
    assert Config._load_from_env().signal_backtest_fwer_alpha == 0.05


@pytest.mark.parametrize("raw,expected", [
    ("0.01", 0.01),        # 域内原样
    ("0.9", 0.05),         # 超上限 → 钳 0.05(保"只收紧")
    ("0", 0.0001),         # 0 → 钳下限(防 inv_cdf(1.0) 崩溃)
    ("-1", 0.0001),        # 负 → 钳下限
    ("1e-100", 0.0001),    # 极小 → 钳下限,不崩
    ("abc", 0.05),         # 非数字 → 回退默认
])
def test_fwer_alpha_clamped_not_fallback(monkeypatch, raw, expected):
    """spec §4.7: 钳制(clamp)非回退;仅非数字才回退默认。"""
    monkeypatch.setenv("SIGNAL_BACKTEST_FWER_ALPHA", raw)
    assert Config._load_from_env().signal_backtest_fwer_alpha == expected
