"""LLM API key 健壮性回归。

误把中文占位说明（含 U+2190 '←' 等非 ASCII 字符）填进 LLM_*_API_KEY 时，该值会被塞进
HTTP header，litellm 在编码 header 阶段抛 'ascii' codec can't encode character ...，且要在
多次重试后才暴露，信息极不友好（曾导致大盘复盘 LLM 调用失败）。
约定：非 ASCII / 空白的 key 一律视为“未配置”，在配置层提前过滤并清晰告警。
"""
import os

from src.config import Config, get_api_keys_for_model, _is_usable_api_key

# 模拟误填的占位说明：'sk' + '←'(U+2190) + 中文，整体非 ASCII（不是真实密钥）
PLACEHOLDER_KEY = "sk←在此填入你的KEY不要提交"
VALID_KEY = "valid-ascii-key-0001"  # 纯 ASCII 的假 key，仅作测试占位（非真实密钥）


def test_is_usable_api_key_accepts_only_nonempty_ascii():
    assert _is_usable_api_key(VALID_KEY) is True
    assert _is_usable_api_key("") is False
    assert _is_usable_api_key(None) is False
    assert _is_usable_api_key("   ") is False
    assert _is_usable_api_key(PLACEHOLDER_KEY) is False
    assert _is_usable_api_key("key-with-中文") is False


def test_channel_parser_drops_non_ascii_key(monkeypatch):
    for name in [k for k in os.environ if k.startswith("LLM_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_ANTHROPIC_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", PLACEHOLDER_KEY)
    monkeypatch.setenv("LLM_ANTHROPIC_MODELS", "claude-sonnet-4-6")

    channels = Config._parse_llm_channels("anthropic")

    all_keys = [k for ch in channels for k in ch.get("api_keys", [])]
    assert PLACEHOLDER_KEY not in all_keys  # 占位 key 不得进入任何 channel
    # anthropic 协议不允许空 key，过滤后无可用 key → 整条 channel 被跳过
    assert all(ch["name"] != "anthropic" for ch in channels)


def test_channel_parser_keeps_valid_ascii_key(monkeypatch):
    for name in [k for k in os.environ if k.startswith("LLM_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_ANTHROPIC_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", VALID_KEY)
    monkeypatch.setenv("LLM_ANTHROPIC_MODELS", "claude-sonnet-4-6")

    channels = Config._parse_llm_channels("anthropic")
    anthropic = [ch for ch in channels if ch["name"] == "anthropic"]
    assert len(anthropic) == 1
    assert VALID_KEY in anthropic[0]["api_keys"]


def test_get_api_keys_for_model_drops_non_ascii():
    cfg = Config()
    cfg.anthropic_api_keys = [PLACEHOLDER_KEY, VALID_KEY]
    keys = get_api_keys_for_model("anthropic/claude-sonnet-4-6", cfg)
    assert PLACEHOLDER_KEY not in keys
    assert VALID_KEY in keys
