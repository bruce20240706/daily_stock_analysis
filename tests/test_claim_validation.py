# -*- coding: utf-8 -*-
"""Tests for LLM claim-validation primitives (Inc 3)."""

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.claim_validation import (
    _is_claim_absent,
    claim_matches_fact,
    extract_numeric_claim,
)


class TestExtractNumericClaim(unittest.TestCase):
    def test_string_keeps_trailing_zero(self) -> None:
        self.assertEqual(extract_numeric_claim("12.30"), (12.30, 2))

    def test_integer_string_has_zero_decimals(self) -> None:
        # D17 回归锁：len('1800'.split('.')[-1]) == 4 会让 tol 退化成 1e-4
        self.assertEqual(extract_numeric_claim("1800"), (1800.0, 0))

    def test_scientific_notation_is_parsed_whole(self) -> None:
        # D17 回归锁：缺指数段的正则会抽成 1.23，错 5 个数量级
        value, decimals = extract_numeric_claim("1.23e-5")
        self.assertAlmostEqual(value, 1.23e-5)
        self.assertEqual(decimals, 7)

    def test_positive_exponent_clamps_to_zero(self) -> None:
        value, decimals = extract_numeric_claim("1e+16")
        self.assertEqual(value, 1e16)
        self.assertEqual(decimals, 0)

    def test_decimals_clamped_to_eight(self) -> None:
        _, decimals = extract_numeric_claim("0.1234567890123")
        self.assertEqual(decimals, 8)

    def test_json_number_float(self) -> None:
        self.assertEqual(extract_numeric_claim(12.3), (12.3, 1))

    def test_json_number_int(self) -> None:
        self.assertEqual(extract_numeric_claim(1800), (1800.0, 0))

    def test_chinese_suffix_and_percent(self) -> None:
        self.assertEqual(extract_numeric_claim("12.34元"), (12.34, 2))
        self.assertEqual(extract_numeric_claim("72.3%"), (72.3, 1))

    def test_bool_is_not_a_number(self) -> None:
        self.assertIsNone(extract_numeric_claim(True))

    def test_non_finite_is_none(self) -> None:
        self.assertIsNone(extract_numeric_claim(float("nan")))
        self.assertIsNone(extract_numeric_claim(float("inf")))

    def test_absent_returns_none(self) -> None:
        for bad in (None, "", "N/A", "待补充"):
            self.assertIsNone(extract_numeric_claim(bad), bad)

    def test_leading_dot_keeps_sign_and_magnitude(self) -> None:
        # 旧正则返回 (5.0, 0)：符号与数量级双双丢失 → 正确的 claim 被判成编造
        self.assertEqual(extract_numeric_claim("-.05%"), (-0.05, 2))
        self.assertEqual(extract_numeric_claim(".5"), (0.5, 1))
        self.assertEqual(extract_numeric_claim("+.25"), (0.25, 2))

    def test_out_of_range_int_returns_none(self) -> None:
        self.assertIsNone(extract_numeric_claim(10 ** 400))


class TestClaimMatchesFact(unittest.TestCase):
    def test_integer_rounding_is_legal(self) -> None:
        # 头号示例：fact=1800.4231，LLM 写 "1800"
        self.assertTrue(claim_matches_fact(1800.0, 0, 1800.4231))

    def test_fabricated_integer_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(1795.0, 0, 1800.4231))

    def test_truncation_and_rounding_both_pass(self) -> None:
        self.assertTrue(claim_matches_fact(3.4, 1, 3.4512))
        self.assertTrue(claim_matches_fact(3.5, 1, 3.4512))

    def test_one_ulp_off_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(3.3, 1, 3.4512))

    def test_transposition_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(21.34, 2, 12.34))

    def test_invented_precision_is_caught(self) -> None:
        # prompt 只给了 72.3，LLM 却写 72.34
        self.assertFalse(claim_matches_fact(72.34, 2, 72.3))

    def test_penny_stock(self) -> None:
        self.assertTrue(claim_matches_fact(0.53, 2, 0.5312))

    def test_crypto_tiny_price_still_catches_fabrication(self) -> None:
        # d=7 → tol=1e-7；若 d 被钳到 4，tol=1e-4 会让任何数都 pass
        self.assertTrue(claim_matches_fact(1.23e-5, 7, 1.23e-5))
        self.assertFalse(claim_matches_fact(4.56e-5, 7, 1.23e-5))

    def test_float_noise_floor(self) -> None:
        self.assertTrue(claim_matches_fact(1800.4231, 4, 1800.4231 + 1e-12))


class TestIsClaimAbsent(unittest.TestCase):
    def test_absent_values(self) -> None:
        for v in (None, "", "  ", "N/A", "n/a", "null", "待补充", "数据缺失"):
            self.assertTrue(_is_claim_absent(v), v)

    def test_numeric_zero_is_a_valid_claim(self) -> None:
        # 锁住「不复用 _is_value_placeholder」：它把 0 判为占位符，
        # 而 bias_ma5 = 0 在 prompt 里就渲染成 +0.00%，是合法 claim。
        self.assertFalse(_is_claim_absent(0))
        self.assertFalse(_is_claim_absent(0.0))
        self.assertFalse(_is_claim_absent("0"))


class TestComposedTolerance(unittest.TestCase):
    """§4.1 容差表的端到端锁：抽取 + 比对合起来跑。

    两个半边各自的单测都绿，组合起来仍可能错（例如 d 从错误的
    repr 求出）。这一类才是守卫真正的行为。
    """

    def _matches(self, claim, fact) -> bool:
        parsed = extract_numeric_claim(claim)
        assert parsed is not None, claim
        value, decimals = parsed
        return claim_matches_fact(value, decimals, fact)

    def test_composed_integer_claim_against_fractional_fact(self) -> None:
        self.assertTrue(self._matches("1800", 1800.4231))   # 整数 round 合法
        self.assertFalse(self._matches("1795", 1800.4231))  # 编造

    def test_composed_json_number_int(self) -> None:
        self.assertTrue(self._matches(1800, 1800.4231))

    def test_composed_truncation_and_rounding(self) -> None:
        self.assertTrue(self._matches("3.4", 3.4512))
        self.assertTrue(self._matches("3.5", 3.4512))
        self.assertFalse(self._matches("3.3", 3.4512))

    def test_composed_invented_precision(self) -> None:
        self.assertFalse(self._matches("72.34", 72.3))

    def test_composed_crypto_tiny_price(self) -> None:
        self.assertTrue(self._matches("1.23e-5", 1.23e-5))
        self.assertFalse(self._matches("4.56e-5", 1.23e-5))


from src.claim_validation import validate_structure  # noqa: E402


class TestValidateStructure(unittest.TestCase):
    def test_valid_long_plan(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": 13.5}
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["violations"], [])

    def test_breakout_buy_above_current_price_is_valid(self) -> None:
        # 文档性护栏：真正的防线是签名拿不到 current_price。
        # 若有人给本函数加上 current_price 参数并引入 entry<=current 判据，本例会红。
        out = validate_structure(
            {"ideal_buy": 12.8, "stop_loss": 12.0, "take_profit": 13.5}
        )
        self.assertEqual(out["status"], "ok")

    def test_stop_above_entry_is_violation(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 13.0, "take_profit": 14.0}
        )
        self.assertEqual(out["status"], "violation")
        self.assertTrue(any("stop_loss" in v for v in out["violations"]))

    def test_target_below_entry_is_violation(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": 12.1}
        )
        self.assertEqual(out["status"], "violation")

    def test_secondary_buy_outside_band_is_violation(self) -> None:
        out = validate_structure(
            {
                "ideal_buy": 12.5,
                "secondary_buy": 14.0,
                "stop_loss": 12.0,
                "take_profit": 13.5,
            }
        )
        self.assertEqual(out["status"], "violation")

    def test_range_ideal_buy_uses_last_number(self) -> None:
        # 与落库口径一致：'180-182' → 182（不是 180）。
        # 夹具必须能分辨首/尾 —— 把边界卡在 181，两种取法结论相反。
        # 取尾(182)：181 < 182 < 190 → ok ；取首(180)：181 < 180 为假 → violation
        ok = validate_structure({"ideal_buy": "180-182", "stop_loss": 181.0, "take_profit": 190.0})
        self.assertEqual(ok["status"], "ok")
        # 取尾(182)：182 < 181 为假 → violation ；取首(180)：180 < 181 → ok
        bad = validate_structure({"ideal_buy": "180-182", "stop_loss": 175.0, "take_profit": 181.0})
        self.assertEqual(bad["status"], "violation")

    def test_non_positive_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": "-5", "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "violation")
        self.assertTrue(any("ideal_buy" in v and "<= 0" in v for v in out["violations"]))

    def test_zero_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": "0元", "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "violation")

    def test_non_finite_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": "inf"})
        self.assertEqual(out["status"], "violation")

    def test_violation_wins_over_insufficient_fields(self) -> None:
        # 只有一个非正字段，凑不出任何序关系，但仍是 violation
        out = validate_structure({"ideal_buy": "-5"})
        self.assertEqual(out["status"], "violation")

    def test_numeric_non_positive_is_absent_not_violation(self) -> None:
        # parse_sniper_value(-5) → None（上游即滤除，与落库 NULL 一致）
        out = validate_structure({"ideal_buy": -5, "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "ok")

    def test_insufficient_fields_is_not_applicable(self) -> None:
        out = validate_structure({"stop_loss": 12.0})
        self.assertEqual(out["status"], "not_applicable")
        self.assertEqual(out["reason"], "insufficient_fields")

    def test_malformed_input_is_not_applicable(self) -> None:
        for bad in (None, "", [], 42):
            out = validate_structure(bad)
            self.assertEqual(out["status"], "not_applicable", bad)
            self.assertEqual(out["reason"], "no_sniper_points")


from src.claim_validation import collect_prompt_facts  # noqa: E402


# 唯一哨兵值：消除「短字面量恒真」——若用 0 / 1 这类值，
# "0" 在 prompt 里到处都是，drift-lock 的子串断言会恒绿。
_SENTINEL_CONTEXT = {
    "code": "600519",
    "stock_name": "贵州茅台",
    "date": "2026-03-16",
    "today": {
        "close": 11111.1111,
        "ma5": 22222.2222,
        "ma10": 33333.3333,
        "ma20": 44444.4444,
        "pct_chg": 1.11,
        "volume": 123456,
        "amount": 987654321,
    },
    "realtime": {
        "price": 55555.5555,
        "volume_ratio": 66666.6666,
        "turnover_rate": 77777.7777,
    },
    "chip": {
        "profit_ratio": 0.888888,
        "avg_cost": 99999.9999,
        "concentration_90": 0.123456,
        "concentration_70": 0.234567,
    },
    "trend_analysis": {
        "bias_ma5": 8.7654,
        "bias_ma10": 1.2345,
        "trend_status": "多头",
        "signal_score": 60,
    },
}


# 每个 fact 的期望值都由 _SENTINEL_CONTEXT 唯一确定。
# 精确相等断言同时锁住三件事：键集完整、字段没串位、量纲转换正确。
_EXPECTED_SENTINEL_FACTS = {
    "ma5": 22222.2222,
    "ma10": 33333.3333,
    "ma20": 44444.4444,
    "volume_ratio": 66666.6666,
    "turnover_rate": 77777.7777,
    "profit_ratio": 88.9,      # f"{0.888888:.1%}" == "88.9%" —— 反解，不是 0.888888*100
    "avg_cost": 99999.9999,
    "bias_ma5": 8.77,          # f"{8.7654:+.2f}" == "+8.77"
    "current_price": [11111.1111, 55555.5555],   # today['close'] 与 realtime['price'] 的值集合
}


class TestCollectPromptFacts(unittest.TestCase):
    def test_exact_fact_mapping_pins_every_key(self) -> None:
        """完整性 + 字段身份。

        drift-lock 的子串断言对「漏掉一个 fact」和「ma10/ma20 互换」是盲的
        —— 两个值都真实出现在 prompt 里，只是挂在对方标签下。
        精确映射断言是唯一能逮住这两类的锁。
        """
        self.assertEqual(collect_prompt_facts(_SENTINEL_CONTEXT), _EXPECTED_SENTINEL_FACTS)

    def test_current_price_is_a_value_set(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        self.assertEqual(sorted(facts["current_price"]), [11111.1111, 55555.5555])

    def test_percent_fields_are_reverse_parsed_from_rendered_string(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        # prompt 写的是 f"{0.888888:.1%}" == "88.9%"，不是 88.8888
        self.assertEqual(facts["profit_ratio"], 88.9)
        # prompt 写的是 f"{8.7654:+.2f}%" == "+8.77%"
        self.assertEqual(facts["bias_ma5"], 8.77)

    def test_bare_fields_are_raw(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        self.assertEqual(facts["ma5"], 22222.2222)
        self.assertEqual(facts["avg_cost"], 99999.9999)
        self.assertEqual(facts["volume_ratio"], 66666.6666)

    def test_missing_blocks_yield_missing_facts(self) -> None:
        ctx = {"today": {"close": 10.0, "ma5": 11.0}}
        facts = collect_prompt_facts(ctx)
        self.assertEqual(facts["current_price"], [10.0])
        self.assertEqual(facts["ma5"], 11.0)
        for absent in ("volume_ratio", "turnover_rate", "profit_ratio", "avg_cost", "bias_ma5"):
            self.assertNotIn(absent, facts)

    def test_non_numeric_is_skipped(self) -> None:
        ctx = {"today": {"close": "N/A", "ma5": None}}
        self.assertEqual(collect_prompt_facts(ctx), {})


class TestPromptFactsDriftLock(unittest.TestCase):
    """facts 一旦对不上 prompt，守卫就会拿错的基准去判 LLM「幻觉」，
    反而制造假警报。这个测试是 collect_prompt_facts 与 _format_prompt
    之间唯一的防漂移保证。"""

    def _render(self, *, legacy: bool) -> str:
        from src.analyzer import GeminiAnalyzer

        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()
        analyzer._use_legacy_default_prompt_override = legacy
        fake_cfg = SimpleNamespace(news_max_age_days=30, news_strategy_profile="medium")
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            return analyzer._format_prompt(dict(_SENTINEL_CONTEXT), "贵州茅台", news_context=None)

    def _assert_facts_in_prompt(self, prompt: str) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        # 键集完整性：漏掉一个 fact 不能悄悄通过
        self.assertEqual(set(facts), set(_EXPECTED_SENTINEL_FACTS))
        for key, value in facts.items():
            candidates = value if isinstance(value, list) else [value]
            for number in candidates:
                token = str(number)
                # 数字边界：裸 `in` 会让 '88.9' 匹配到 prompt 里的 '88.89%'，
                # 于是 prompt 改了格式而 facts 没跟上时 drift-lock 反而不红。
                pattern = rf"(?<![\d.]){re.escape(token)}(?![\d])"
                self.assertRegex(
                    prompt,
                    pattern,
                    f"fact {key}={token} 未以完整数字出现在 prompt 里 —— "
                    f"collect_prompt_facts 与 _format_prompt 已漂移",
                )

    def test_drift_lock_legacy_branch(self) -> None:
        self._assert_facts_in_prompt(self._render(legacy=True))

    def test_drift_lock_non_legacy_branch(self) -> None:
        self._assert_facts_in_prompt(self._render(legacy=False))


if __name__ == "__main__":
    unittest.main()
