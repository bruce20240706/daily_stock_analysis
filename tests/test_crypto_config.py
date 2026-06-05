"""crypto 数据源相关配置项的默认值与环境变量装载测试。"""

from src.config import Config


def test_crypto_config_defaults(monkeypatch):
    """未设置环境变量时，crypto 配置项应回落到内置默认值。"""
    for k in ("CRYPTO_DATA_PRIORITY", "CRYPTO_REALTIME_PRIORITY", "BINANCE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config()
    assert cfg.crypto_data_priority == "binance,okx,coinbase"
    assert cfg.crypto_realtime_priority == "binance,okx,coinbase"
    assert cfg.binance_base_url == "https://api.binance.com"


def test_crypto_config_env_override(monkeypatch):
    """设置环境变量后，_load_from_env 应注入自定义值。"""
    monkeypatch.setenv("CRYPTO_DATA_PRIORITY", "okx,binance")
    monkeypatch.setenv("CRYPTO_REALTIME_PRIORITY", "coinbase,okx")
    monkeypatch.setenv("BINANCE_BASE_URL", "https://data-api.binance.vision")
    cfg = Config._load_from_env()
    assert cfg.crypto_data_priority == "okx,binance"
    assert cfg.crypto_realtime_priority == "coinbase,okx"
    assert cfg.binance_base_url == "https://data-api.binance.vision"
