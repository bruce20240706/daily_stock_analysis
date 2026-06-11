from src.analyzer import GeminiAnalyzer

a = GeminiAnalyzer()

_BASE_CTX = {
    "code": "BTC/USDT",
    "stock_name": "BTC/USDT",
    "date": "2026-06-09",
    "today": {"close": 60000.0, "open": 60000.0, "high": 61000.0, "low": 59000.0, "pct_chg": 1.0, "ma5": 60000.0, "ma10": 59000.0, "ma20": 58000.0},
}


def test_prompt_includes_contract_block_when_present():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "mark_price": 62669.5, "open_interest": 2861888.58, "open_interest_usd": 1793545573.08, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "合约市场指标" in prompt
    assert "62669.5" in prompt          # mark price
    assert "0.0100%" in prompt          # funding_rate 0.0001 → *100 = 0.0100%
    assert "张" in prompt                       # OI 单位
    assert "1,793,545,573" in prompt            # open_interest_usd 千分位


def test_prompt_omits_contract_block_when_absent():
    prompt = a._format_prompt(dict(_BASE_CTX), "BTC/USDT", report_language="zh")
    assert "合约市场指标" not in prompt


def test_prompt_includes_long_short_ratio_rows():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "long_short_ratio": 1.23, "long_short_ratio_top": 0.85, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "多空比(全市场)" in prompt
    assert "1.23" in prompt
    assert "多空比(大户)" in prompt
    assert "0.85" in prompt


def test_prompt_omits_long_short_ratio_when_absent():
    ctx = dict(_BASE_CTX)
    ctx["crypto_contracts"] = {"funding_rate": 0.0001, "source": "okx"}
    prompt = a._format_prompt(ctx, "BTC/USDT", report_language="zh")
    assert "多空比" not in prompt
