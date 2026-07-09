# -*- coding: utf-8 -*-
"""Tests for the shared sniper price-text parser (Inc 3)."""

import unittest

from src.sniper_parsing import parse_sniper_value


class TestParseSniperValue(unittest.TestCase):
    def test_numeric_passthrough(self) -> None:
        self.assertEqual(parse_sniper_value(12.5), 12.5)
        self.assertEqual(parse_sniper_value(13), 13.0)

    def test_non_positive_numeric_is_none(self) -> None:
        self.assertIsNone(parse_sniper_value(0))
        self.assertIsNone(parse_sniper_value(-1.0))

    def test_plain_number_string(self) -> None:
        self.assertEqual(parse_sniper_value("12.34"), 12.34)

    def test_chinese_price_format(self) -> None:
        self.assertEqual(parse_sniper_value("止损位：18.50元"), 18.50)

    def test_range_takes_last_number(self) -> None:
        self.assertEqual(parse_sniper_value("180-182"), 182.0)

    def test_ma_indicator_digits_are_skipped(self) -> None:
        self.assertEqual(parse_sniper_value("93.40下方（MA20支撑）"), 93.4)
        self.assertIn(parse_sniper_value("1.52-1.53 (回踩MA5/10附近)"), [1.52, 1.53])

    def test_absent_inputs(self) -> None:
        for bad in (None, "", "-", "—", "N/A", "没有数字", "MA5但没有元"):
            self.assertIsNone(parse_sniper_value(bad), bad)


if __name__ == "__main__":
    unittest.main()
