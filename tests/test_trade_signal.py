# -*- coding: utf-8 -*-
"""Inc 0 canonical TradeSignal 契约:schema 与校验器测试。

设计见 docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md。
"""

import pytest

from src.schemas import analysis_context_pack


def test_iso8601_validator_is_public_and_aliased():
    """公开名可导入,私有别名指向同一对象(现有引用零改)。"""
    public = analysis_context_pack.validate_iso8601_timestamp
    private = analysis_context_pack._validate_iso8601_timestamp
    assert public is private

    assert public(None) is None
    assert public("2026-07-10T00:00:00") == "2026-07-10T00:00:00"
    assert public("2026-07-10T00:00:00Z") == "2026-07-10T00:00:00Z"
    with pytest.raises(ValueError):
        public("2026-07-10")          # 裸日期无 'T'
    with pytest.raises(ValueError):
        public("not-a-timestamp-T")   # 含 'T' 但不可解析
