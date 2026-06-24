"""市场检测 helper:is_a_share_code / market_of(A股盘中回测路由前置)。

覆盖沪深主板/科创/创业 + 北交所放行,crypto/perp/HK/US 拒绝;market_of 仅
归类 crypto 与 cn,其余抛 ValueError(分钟回测暂不支持的市场显式失败,而非静默)。
"""
import pytest

from data_provider.base import is_a_share_code, market_of


@pytest.mark.parametrize(
    "code",
    ["600519", "601318", "603288", "605499", "688981",
     "000001", "001979", "002415", "003816", "300750", "301029"],
)
def test_is_a_share_true_sh_sz(code):
    assert is_a_share_code(code) is True


@pytest.mark.parametrize("code", ["830799", "920819"])
def test_is_a_share_bse(code):
    # 北交所(is_bse_code 规则:8x/92/43 等前缀,排除 900xxx 沪 B)
    assert is_a_share_code(code) is True


def test_is_a_share_accepts_prefixed_forms():
    # 带交易所前缀/后缀的写法经 normalize 后仍判为 A股
    assert is_a_share_code("SH600519") is True
    assert is_a_share_code("000001.SZ") is True
    assert is_a_share_code("BJ920819") is True


@pytest.mark.parametrize(
    "code",
    ["BTC/USDT", "ETH/USDT:PERP", "00700", "HK00700", "01810.HK", "AAPL", "900901"],
)
def test_is_a_share_false_others(code):
    # crypto/perp/港股(5 位或 HK 前缀)/美股/沪 B(900xxx)均非 A股盘中标的
    assert is_a_share_code(code) is False


def test_market_of():
    assert market_of("600519") == "cn"
    assert market_of("000001.SZ") == "cn"
    assert market_of("BTC/USDT") == "crypto"
    assert market_of("ETH/USDT:PERP") == "crypto"


@pytest.mark.parametrize("code", ["830799", "920819"])
def test_market_of_bse_is_cn(code):
    # 北交所标的归类为 cn(分钟回测市场化窗口据此取 bars_per_day=240)
    assert market_of(code) == "cn"


@pytest.mark.parametrize("code", ["AAPL", "00700", "HK00700"])
def test_market_of_unsupported_raises(code):
    with pytest.raises(ValueError):
        market_of(code)
