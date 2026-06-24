"""市场检测:market_of 美股归类(美股盘中回测路由前置)。

复用既有 is_us_stock_code(1–5 大写字母 + 可选 .X,排除美股指数/crypto)。
market_of 顺序:crypto/perp → crypto;a_share → cn;us_stock → us;其余抛 ValueError。
"""
import pytest

from data_provider.base import market_of


@pytest.mark.parametrize("code", ["AAPL", "TSLA", "MSFT", "NVDA", "BRK.B"])
def test_market_of_us(code):
    assert market_of(code) == "us"


def test_market_of_us_index_raises():
    # 美股指数无 operation_advice、非回测候选;is_us_stock_code 排除指数 → market_of 抛 ValueError
    with pytest.raises(ValueError):
        market_of("SPX")


def test_market_of_order_no_collision():
    assert market_of("600519") == "cn"
    assert market_of("BTC/USDT") == "crypto"
    assert market_of("ETH/USDT:PERP") == "crypto"
    with pytest.raises(ValueError):
        market_of("HK00700")   # 港股不归类(分钟回测暂不支持)
