# -*- coding: utf-8 -*-
"""claim-validation 提示行必须同时出现在两套渲染引擎里(Inc 3)。"""

import unittest
from unittest import mock

from src.notification import _render_claim_validation_section


def _cv(*, transcription_status="ok", structural_status="ok", mismatches=0):
    return {
        "applied": True,
        "transcription": {
            "status": transcription_status,
            "reason": None,
            "checked": 5,
            "mismatches": [{"field": "price_position.ma5"}] * mismatches,
        },
        "structural": {"status": structural_status, "reason": None, "violations": ["stop_loss(13.0) >= ideal_buy(12.5)"]},
        "actions": [],
    }


class TestRenderClaimValidationSection(unittest.TestCase):
    def test_all_ok_renders_nothing(self) -> None:
        self.assertEqual(_render_claim_validation_section(_cv(), "zh"), "")

    def test_missing_block_renders_nothing(self) -> None:
        self.assertEqual(_render_claim_validation_section(None, "zh"), "")
        self.assertEqual(_render_claim_validation_section({}, "zh"), "")

    def test_not_applicable_renders_nothing(self) -> None:
        self.assertEqual(
            _render_claim_validation_section(
                _cv(transcription_status="not_applicable"), "zh"
            ),
            "",
        )

    def test_mismatch_renders_zh(self) -> None:
        text = _render_claim_validation_section(
            _cv(transcription_status="mismatch", mismatches=2), "zh"
        )
        self.assertIn("2", text)
        self.assertIn("置信度", text)

    def test_mismatch_renders_en(self) -> None:
        text = _render_claim_validation_section(
            _cv(transcription_status="mismatch", mismatches=2), "en"
        )
        self.assertIn("2", text)
        self.assertIn("confidence", text.lower())

    def test_violation_renders_both_languages(self) -> None:
        for lang in ("zh", "en"):
            text = _render_claim_validation_section(_cv(structural_status="violation"), lang)
            self.assertTrue(text, lang)


class TestJinjaTemplateHasSection(unittest.TestCase):
    def test_report_markdown_template_renders_claim_validation(self) -> None:
        from pathlib import Path

        template = Path(__file__).resolve().parents[1] / "templates" / "report_markdown.j2"
        content = template.read_text(encoding="utf-8")
        self.assertIn("claim_validation", content)


# --- 端到端集成:两套渲染引擎真的把顶层 dashboard['claim_validation'] 接进输出 ---
# (镜像 tests/test_margin_surface.py 里 margin_trading 的 legacy + Jinja 双引擎覆盖手法)


def _result_with_claim_validation(report_language="zh", cv=None):
    from src.analyzer import AnalysisResult

    return AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=72,
        trend_prediction="看多", operation_advice="持有", analysis_summary="稳健",
        report_language=report_language,
        dashboard={"claim_validation": cv if cv is not None else _cv()},
    )


class TestNotificationLegacyRendersClaimValidation(unittest.TestCase):
    @mock.patch("src.notification.get_config")
    def test_legacy_renders_mismatch_zh(self, mock_cfg) -> None:
        from tests.test_notification import _make_config
        from src.notification import NotificationService

        mock_cfg.return_value = _make_config(report_renderer_enabled=False)
        cv = _cv(transcription_status="mismatch", mismatches=2)
        out = NotificationService().generate_dashboard_report(
            [_result_with_claim_validation("zh", cv)], report_date="2026-07-09")
        self.assertIn("数值校验", out)
        self.assertIn("置信度", out)
        self.assertIn("2", out)

    @mock.patch("src.notification.get_config")
    def test_legacy_renders_violation_en(self, mock_cfg) -> None:
        from tests.test_notification import _make_config
        from src.notification import NotificationService

        mock_cfg.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        cv = _cv(structural_status="violation")
        out = NotificationService().generate_dashboard_report(
            [_result_with_claim_validation("en", cv)], report_date="2026-07-09")
        self.assertIn("Claim Validation", out)
        self.assertIn("not executable", out)

    @mock.patch("src.notification.get_config")
    def test_legacy_all_ok_not_rendered(self, mock_cfg) -> None:
        from tests.test_notification import _make_config
        from src.notification import NotificationService

        mock_cfg.return_value = _make_config(report_renderer_enabled=False)
        out = NotificationService().generate_dashboard_report(
            [_result_with_claim_validation("zh", _cv())], report_date="2026-07-09")
        self.assertNotIn("数值校验", out)


class TestJinjaRendersClaimValidation(unittest.TestCase):
    def test_jinja_renders_mismatch_zh(self) -> None:
        from src.services.report_renderer import render as _render

        cv = _cv(transcription_status="mismatch", mismatches=3)
        out = _render("markdown", [_result_with_claim_validation("zh", cv)], summary_only=False)
        self.assertIn("数值校验", out)
        self.assertIn("3", out)

    def test_jinja_renders_violation_en(self) -> None:
        from src.services.report_renderer import render as _render

        cv = _cv(structural_status="violation")
        out = _render("markdown", [_result_with_claim_validation("en", cv)], summary_only=False)
        self.assertIn("Claim Validation", out)
        self.assertIn("not executable", out)

    def test_jinja_all_ok_not_rendered(self) -> None:
        from src.services.report_renderer import render as _render

        out = _render("markdown", [_result_with_claim_validation("zh", _cv())], summary_only=False)
        self.assertNotIn("数值校验", out)


if __name__ == "__main__":
    unittest.main()
