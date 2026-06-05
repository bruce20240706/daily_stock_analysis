"""crypto 展示格式测试：价格动态精度 + 成交量/额单位。"""
from src.analyzer import GeminiAnalyzer  # _format_* 方法所属类

a = GeminiAnalyzer()


def test_price_dynamic_precision():
    assert a._format_price(45000.0) == "45000.00"   # 大额仍 2 位
    assert a._format_price(0.00000123) not in ("0.00", "N/A")  # 小币不丢精度
    assert a._format_price(None) == "N/A"


def test_volume_unit_market_aware():
    # 默认（股票）行为不变
    assert "股" in a._format_volume(12000.0)
    # crypto：传 unit 时以币种 base 为单位、不带"股"
    out = a._format_volume(12000.0, unit="BTC")
    assert "股" not in out and "BTC" in out


def test_amount_unit_market_aware():
    assert "元" in a._format_amount(12000.0)
    out = a._format_amount(12000.0, currency="USDT")
    assert "USDT" in out and "元" not in out
    assert a._format_amount(None) == "N/A"
