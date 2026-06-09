from src.config import Config


def test_derivatives_default_enabled(monkeypatch):
    monkeypatch.delenv("CRYPTO_DERIVATIVES_ENABLED", raising=False)
    assert Config._load_from_env().crypto_derivatives_enabled is True


def test_derivatives_env_disable(monkeypatch):
    monkeypatch.setenv("CRYPTO_DERIVATIVES_ENABLED", "false")
    assert Config._load_from_env().crypto_derivatives_enabled is False
