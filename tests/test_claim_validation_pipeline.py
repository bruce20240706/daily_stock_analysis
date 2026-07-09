# -*- coding: utf-8 -*-
"""pipeline 级集成测试：锁住 claim-validation 的挂载顺序(Inc 3)。

§3.1 与 §3.3 的顺序约束**只存在于 pipeline.py 的插入位置**。
在 guardrail 层手工按正确顺序调两个函数的测试是 tautology ——
无论有人把 pipeline.py 里的调用挪到哪儿，它都恒绿。
因此这些测试必须驱动真实 pipeline。
"""

import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from data_provider.realtime_types import ChipDistribution
from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.stock_analyzer import TrendAnalysisResult

# 交易日盘中时刻（2026-03-27 周五 10:00，A股上午盘）：绝大多数测试用它，
# phase=intraday，不触发保守盘口分支。
_INTRADAY_TIME = datetime(2026, 3, 27, 10, 0)
# 交易日开盘前时刻（同日 08:00）：phase=premarket，属于
# CONSERVATIVE_ACTION_PHASES，用于驱动 phase guardrail 的 高→低 分支。
_PREMARKET_TIME = datetime(2026, 3, 27, 8, 0)


def _dashboard(*, ma5, sniper=None):
    return {
        "data_perspective": {"price_position": {"ma5": ma5}},
        "battle_plan": {"sniper_points": sniper or {}},
    }


def _analysis_result(*, confidence="高", ma5=22222.2222, prompt_facts=None, sniper=None) -> AnalysisResult:
    result = AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=80,
        trend_prediction="看多", operation_advice="立即买入",
        decision_type="buy", confidence_level=confidence,
    )
    result.dashboard = _dashboard(ma5=ma5, sniper=sniper)
    result.prompt_facts = prompt_facts
    return result


def _make_pipeline(
    *, agent_mode: bool, analysis_result: AnalysisResult, chip_data=None, enable_chip: bool = False
) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=enable_chip,
        realtime_source_priority=[],
        agent_mode=agent_mode,
        agent_skills=[],
        save_context_snapshot=False,
        report_language="zh",
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
        llm_claim_validation_enabled=True,
    )
    pipeline.source_message = None
    pipeline.query_id = None
    pipeline.query_source = "system"
    pipeline.save_context_snapshot = False
    pipeline.progress_callback = None
    pipeline.analysis_skills = None
    pipeline.analysis_phase = "auto"
    pipeline.social_sentiment_service = None

    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_realtime_quote.return_value = None
    pipeline.fetcher_manager.get_chip_distribution.return_value = chip_data
    pipeline.fetcher_manager.get_fundamental_context.return_value = {
        "market": "cn", "coverage": {"boards": "not_supported"}, "source_chain": [],
    }
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
        "market": "cn", "coverage": {"boards": "not_supported"}, "source_chain": [],
    }

    pipeline.db = MagicMock()
    # 默认 [] → historical_bars 恒空 → trend_analyzer.analyze 压根不会被调用
    # （见 analyze_stock 的 `if historical_bars:` 门控）。需要真实回填的测试
    # 必须显式覆盖为非空列表，否则 trend_result 恒为 None 是 tautology 的根源。
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519", "stock_name": "贵州茅台", "date": "2026-03-26", "today": {}, "yesterday": {},
    }

    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = analysis_result
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = False
    pipeline.search_service.news_window_days = 3
    pipeline._emit_progress = MagicMock()
    return pipeline


class TestClaimValidationOrder(unittest.TestCase):
    def _run(self, pipeline, *, current_time: datetime = _INTRADAY_TIME) -> AnalysisResult:
        return pipeline.analyze_stock(
            "600519", ReportType.SIMPLE, "q-claim", current_time=current_time
        )

    def test_b_runs_after_phase_guardrail(self) -> None:
        """§3.1 Blocker 回归锁。

        若 apply_claim_validation 被挪到 apply_phase_decision_guardrails 之前，
        它会先把「高」降成「中」，guardrail 的 initially_high_confidence 变 False，
        高→低 分支静默失效，最终 confidence 会是「中」而非「低」。

        用开盘前时刻（premarket，属于 CONSERVATIVE_ACTION_PHASES）驱动 guardrail
        的「保守盘口阶段 + 立即买卖信号 + 原本高置信 → 高→低」分支；
        decision_type="buy" 保证立即买卖信号判据成立，不依赖交易时段文案巧合。
        """
        stub = _analysis_result(confidence="高", ma5=99999.9, prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        result = self._run(pipeline, current_time=_PREMARKET_TIME)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["status"], "mismatch")
        # guardrail 的高→低 仍然生效（保守盘口阶段 + 立即买入 + 原本高置信）
        self.assertEqual(result.confidence_level, "低")

    def test_a_runs_before_any_backfill_price_position(self) -> None:
        """§3.1 假警报侧回归锁。

        ma5 是占位符 → 若 A 挪到 fill_price_position_if_needed 之后，
        系统会用 trend_result 的重算值回填，与 prompt fact 不符 → 假 mismatch。

        必须让 trend_analyzer.analyze 真正被调用：db.get_data_range 覆盖为非空
        （否则 `if historical_bars:` 门控短路，trend_result 恒为 None，
        fill_price_position_if_needed 直接 no-op，测试无论 A 挪到哪都恒绿）。
        trend_result 用真实 TrendAnalysisResult（而非裸 SimpleNamespace）：
        _enhance_context 会无条件访问 trend_status.value / buy_signal.value 等
        约 10 个字段，裸桩会在 Step 6 AttributeError 崩溃、整条分析提前失败。
        """
        stub = _analysis_result(confidence="中", ma5="N/A", prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        pipeline.db.get_data_range.return_value = [
            SimpleNamespace(to_dict=lambda: {"date": "2026-03-26", "close": 12.0})
        ]
        pipeline.trend_analyzer.analyze.return_value = TrendAnalysisResult(code="600519", ma5=11.11)
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["mismatches"], [])

    def test_a_runs_before_chip_backfill(self) -> None:
        """§3.1 tautology 侧回归锁（D18）。

        profit_ratio 是占位符 + chip_data 有效 → 若 A 挪到
        normalize_chip_structure_availability 之后，系统回填的 "72.3%"
        会被当成 LLM claim 拿去和同源 fact 自比 → checked 计入。
        """
        stub = _analysis_result(confidence="中", prompt_facts={"profit_ratio": 72.3})
        stub.dashboard["data_perspective"]["chip_structure"] = {"profit_ratio": "N/A"}
        # 用真实 ChipDistribution（而非裸 SimpleNamespace）：_enhance_context 会无条件
        # 访问 concentration_70 / get_chip_status(...)，裸桩缺字段会在 Step 2 之后的
        # Step 6 AttributeError 崩溃、整条分析提前失败并返回 None。
        chip = ChipDistribution(code="600519", profit_ratio=0.7234, avg_cost=12.0, concentration_90=0.1234)
        # enable_chip=True 必须传 —— 否则 pipeline 压根不抓 chip_data，
        # normalize_chip_structure_availability 不回填，测试 trivially 通过。
        pipeline = _make_pipeline(
            agent_mode=False, analysis_result=stub, chip_data=chip, enable_chip=True
        )
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["checked"], 0)


class TestBothBranchesWiredInOrder(unittest.TestCase):
    """源码级顺序锁 —— 覆盖 agent 分支。

    agent 分支要端到端驱动需 mock agent executor，脆且易漂移。
    但顺序不变式本身是**静态**的：两条分支都必须满足
    extract_llm_claims 早于第一个 in-place 回填、
    apply_claim_validation 晚于 apply_phase_decision_guardrails。
    直接对源码断言，确定性且覆盖两条路径。
    """

    @staticmethod
    def _source() -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(
            encoding="utf-8"
        )

    @staticmethod
    def _positions(source: str, needle: str) -> list:
        start, out = 0, []
        while True:
            idx = source.find(needle, start)
            if idx == -1:
                return out
            out.append(idx)
            start = idx + 1

    def test_two_branches_each_have_both_hooks(self) -> None:
        source = self._source()
        self.assertEqual(len(self._positions(source, "extract_llm_claims(result)")), 2)
        self.assertEqual(len(self._positions(source, "apply_claim_validation(")), 2)
        self.assertEqual(len(self._positions(source, "normalize_chip_structure_availability(")), 2)
        self.assertEqual(len(self._positions(source, "apply_phase_decision_guardrails(")), 2)

    def test_extract_precedes_first_backfill_in_both_branches(self) -> None:
        source = self._source()
        extracts = self._positions(source, "extract_llm_claims(result)")
        chips = self._positions(source, "normalize_chip_structure_availability(")
        for branch, (extract_at, chip_at) in enumerate(zip(extracts, chips)):
            self.assertLess(
                extract_at, chip_at,
                f"分支 {branch}: extract_llm_claims 必须在 normalize_chip_structure_availability 之前",
            )

    def test_apply_follows_phase_guardrail_in_both_branches(self) -> None:
        source = self._source()
        applies = self._positions(source, "apply_claim_validation(")
        guards = self._positions(source, "apply_phase_decision_guardrails(")
        for branch, (apply_at, guard_at) in enumerate(zip(applies, guards)):
            self.assertGreater(
                apply_at, guard_at,
                f"分支 {branch}: apply_claim_validation 必须在 apply_phase_decision_guardrails 之后",
            )

    def test_gate_off_writes_nothing(self) -> None:
        stub = _analysis_result(confidence="高", ma5=99999.9, prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        pipeline.config.llm_claim_validation_enabled = False
        result = pipeline.analyze_stock(
            "600519", ReportType.SIMPLE, "q-claim", current_time=_INTRADAY_TIME
        )
        self.assertNotIn("claim_validation", result.dashboard)

    def test_structural_violation_flows_through(self) -> None:
        stub = _analysis_result(
            confidence="中",
            prompt_facts={},
            sniper={"ideal_buy": 12.5, "stop_loss": 13.0, "take_profit": 14.0},
        )
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        result = pipeline.analyze_stock(
            "600519", ReportType.SIMPLE, "q-claim", current_time=_INTRADAY_TIME
        )
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["structural"]["status"], "violation")
        self.assertIn("sniper_points_unexecutable", cv["actions"])


if __name__ == "__main__":
    unittest.main()
