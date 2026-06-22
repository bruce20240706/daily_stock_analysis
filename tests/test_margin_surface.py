# -*- coding: utf-8 -*-
import copy
import types

from src.schemas.report_schema import DataPerspective, MarginTrading


def test_margin_trading_model_fields():
    mt = MarginTrading(
        financing_balance=1.23e8, financing_buy=4.5e7,
        short_volume=1000, trade_date="20260619", exchange="SSE",
    )
    assert mt.financing_balance == 1.23e8
    assert mt.trade_date == "20260619"
    assert mt.exchange == "SSE"


def test_margin_trading_all_optional():
    mt = MarginTrading()
    assert mt.financing_balance is None
    assert mt.exchange is None


def test_data_perspective_backward_compatible_without_margin():
    # 旧 payload 无 margin_trading 键仍解析；默认 None。
    dp = DataPerspective.model_validate({"chip_structure": {"chip_health": "健康"}})
    assert dp.margin_trading is None


def test_data_perspective_accepts_margin_trading():
    dp = DataPerspective.model_validate(
        {"margin_trading": {"financing_balance": 1.0e8, "exchange": "SZSE"}}
    )
    assert dp.margin_trading.exchange == "SZSE"
