# -*- coding: utf-8 -*-
"""Tests for LLM claim-validation primitives (Inc 3)."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
