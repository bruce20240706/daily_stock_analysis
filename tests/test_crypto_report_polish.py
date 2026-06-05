"""crypto 报告文案/展示瑕疵回归。

瑕疵 1：LLM 正文把 USDT 计价的 crypto 价格写成“元”。在 crypto 市场指引中加入单位说明，
        要求统一用计价币（如 USDT）表述价格/目标位/止损位，不要用“元”/CNY/USD。
瑕疵 2：crypto 无量比/换手率时，报告渲染出字面 "None"（如“量比 None / 换手率 None%”）。
        新增 GeminiAnalyzer._na() 把 None/缺失统一显示为 "N/A"。
"""
from src.analyzer import GeminiAnalyzer
from src.market_context import get_market_guidelines

a = GeminiAnalyzer()


# --- 瑕疵 1：crypto 价格单位指引 ---

def test_crypto_guidelines_zh_mention_quote_currency_unit():
    g = get_market_guidelines("BTC/USDT", lang="zh")
    assert "计价币" in g
    assert "元" in g  # 出现在“不要使用‘元’”的否定指引里
    # 股票市场指引不应被污染
    assert "计价币" not in get_market_guidelines("600519", lang="zh")


def test_crypto_guidelines_en_mention_quote_currency_unit():
    g = get_market_guidelines("BTC/USDT", lang="en")
    assert "quote currency" in g.lower()


# --- 瑕疵 2：None -> N/A ---

def test_na_renders_none_as_na():
    assert a._na(None) == "N/A"
    assert a._na(None, suffix="%") == "N/A"  # None 不带多余的 %
    assert a._na(1.2) == "1.2"
    assert a._na(5, suffix="%") == "5%"
    assert a._na("okx") == "okx"
