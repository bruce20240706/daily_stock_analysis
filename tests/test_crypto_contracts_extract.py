# -*- coding: utf-8 -*-
"""crypto_contracts 抽取器：嵌套 enhanced_context + presence-only + JSON 字符串入参。"""
import json

from src.utils.data_processing import extract_crypto_contracts_detail_fields

_CONTRACTS = {
    "funding_rate": 0.0000059888,
    "mark_price": 62669.5,
    "open_interest": 2861888.58,
    "open_interest_usd": 1793545573.08,
    "source": "okx",
}


def test_nested_enhanced_context_returns_contracts():
    snapshot = {"enhanced_context": {"crypto_contracts": _CONTRACTS}}
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS


def test_json_string_input_is_parsed():
    snapshot = json.dumps({"enhanced_context": {"crypto_contracts": _CONTRACTS}})
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS


def test_missing_enhanced_context_returns_none():
    assert extract_crypto_contracts_detail_fields({"foo": 1})["crypto_contracts"] is None


def test_empty_contracts_dict_returns_none():
    snapshot = {"enhanced_context": {"crypto_contracts": {}}}
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] is None


def test_non_dict_snapshot_returns_none():
    for bad in (None, "not-json", 123, ["x"]):
        assert extract_crypto_contracts_detail_fields(bad)["crypto_contracts"] is None


def test_flat_top_level_fallback_returns_contracts():
    snapshot = {"crypto_contracts": _CONTRACTS}  # 防御性 dual-shape
    assert extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"] == _CONTRACTS


def test_long_short_ratio_fields_pass_through():
    contracts = {**_CONTRACTS, "long_short_ratio": 1.23, "long_short_ratio_top": 0.85}
    snapshot = {"enhanced_context": {"crypto_contracts": contracts}}
    out = extract_crypto_contracts_detail_fields(snapshot)["crypto_contracts"]
    assert out["long_short_ratio"] == 1.23
    assert out["long_short_ratio_top"] == 0.85
