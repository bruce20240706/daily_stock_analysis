# -*- coding: utf-8 -*-
"""perp 杠杆情景回测：引擎强平/放大分支、repo perp_only 粗滤、service 标签隔离、API 参数面。
L=1 与现货回归见锁套件（test_backtest_engine / test_crypto_backtest / test_backtest_summary / test_perp_backtest_engine，零改动）。"""
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from unittest.mock import patch

from src.config import Config
from src.core.backtest_engine import OVERALL_SENTINEL_CODE, BacktestEngine, EvaluationConfig
from src.repositories.backtest_repo import BacktestRepository
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, BacktestSummary, DatabaseManager, StockDaily

PERP_CODE = "BTC/USDT:PERP"


@dataclass
class Bar:
    date: date
    high: float
    low: float
    close: float


def _bars(start: date, closes, highs=None, lows=None):
    highs = highs or closes
    lows = lows or closes
    return [Bar(date=start + timedelta(days=i + 1), high=highs[i], low=lows[i], close=closes[i])
            for i in range(len(closes))]


def _eval(advice="买入", *, bars, leverage=None, stop_loss=None, take_profit=None,
          funding=0.0, is_perp=True, start_price=100.0):
    kwargs = dict(
        operation_advice=advice, analysis_date=date(2024, 1, 1), start_price=start_price,
        forward_bars=bars, stop_loss=stop_loss, take_profit=take_profit,
        config=EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0),
        is_perp=is_perp, funding_cost_pct=funding,
    )
    if leverage is not None:
        kwargs["leverage"] = leverage
    return BacktestEngine.evaluate_single(**kwargs)


class LeverageEngineTestCase(unittest.TestCase):
    def test_l1_explicit_equals_default_per_field(self):
        # L=1 不变量（最高优先级）：显式 leverage=1 与不传 leverage 输出逐字段相等（long/short/cash 三态）
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        cases = [
            ("买入", dict(stop_loss=95.0, take_profit=110.0, funding=0.5)),
            ("卖出", dict(funding=0.3)),
            ("观望", dict(funding=0.9)),
        ]
        for advice, extra in cases:
            with self.subTest(advice=advice):
                self.assertEqual(_eval(advice, bars=bars, leverage=1, **extra),
                                 _eval(advice, bars=bars, **extra))

    def test_l1_short_does_not_liquidate_at_double_entry(self):
        # L=1 门控判别：1x 做空理论爆仓点 +100%（high≥2×entry）不模拟强平（与 E 一致）；
        # 若门控误写 leverage >= 1，本用例即挂（liq_short=200，high 210 会被误判强平）
        bars = _bars(date(2024, 1, 1), [95, 90, 85], highs=[210, 95, 90], lows=[94, 88, 84])
        res = _eval("卖出", bars=bars, leverage=1, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], (100 - 85) / 100 * 100 + 0.3)

    def test_long_liquidation_overrides_later_tp(self):
        # L=4 → liq=75；bar1 low 74 触强平，bar2 才触 TP=110 → 强平（已出场，后续 TP 不改结果）
        bars = _bars(date(2024, 1, 1), [80, 85, 90], highs=[95, 110, 112], lows=[74, 80, 85])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        self.assertAlmostEqual(res["simulated_entry_price"], 100.0)
        # 预测质量字段维持既有逻辑（不感知强平）：TP 在 bar2 命中照旧记录
        self.assertTrue(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "take_profit")
        self.assertEqual(res["first_hit_trading_days"], 2)
        # direction_correct 同样不感知强平：与无杠杆评估一致（此 fixture 为方向判错+强平并存）
        base = _eval("买入", bars=bars, take_profit=110.0, funding=0.5)
        self.assertEqual(res["direction_correct"], base["direction_correct"])
        self.assertIs(res["direction_correct"], False)

    def test_long_exit_before_liq_touch_is_not_liquidated(self):
        # 判别用例（整窗 min/max 误实现的唯一可挂点）：bar1 触 TP 出场，bar2 才跌穿强平线 75
        # → 止盈放大 4×10% − 4×0.5 = 38.0，非 liquidated
        bars = _bars(date(2024, 1, 1), [105, 80, 78], highs=[111, 95, 90], lows=[90, 70, 72])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "take_profit")
        self.assertAlmostEqual(res["simulated_exit_price"], 110.0)
        self.assertAlmostEqual(res["simulated_return_pct"], 38.0)

    def test_same_bar_liq_and_tp_prefers_liquidation(self):
        # 同 bar 双触（bar1 high 111≥TP 且 low 74≤liq 75）→ 保守强平优先
        bars = _bars(date(2024, 1, 1), [100, 100, 100], highs=[111, 100, 100], lows=[74, 99, 99])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_same_bar_liq_and_sl_prefers_liquidation(self):
        # SL=80 > liq=75：触强平的 bar 必同触 SL（杠杆强平最常见形态）→ 强平优先，非 stop_loss
        bars = _bars(date(2024, 1, 1), [85, 90, 95], highs=[100, 95, 96], lows=[74, 85, 90])
        res = _eval("买入", bars=bars, leverage=4, stop_loss=80.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        # 预测字段照旧：SL 命中记录不变
        self.assertTrue(res["hit_stop_loss"])
        self.assertEqual(res["first_hit"], "stop_loss")

    def test_short_liquidation_on_high_touch(self):
        # L=4 → liq_short=125；bar1 high 126 → 强平
        bars = _bars(date(2024, 1, 1), [120, 110, 105], highs=[126, 115, 108], lows=[110, 105, 100])
        res = _eval("卖出", bars=bars, leverage=4, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 125.0)

    def test_short_window_end_amplification(self):
        # 未触强平（liq_short=133.33，high≤99）：L×(entry−end)/entry×100 + L×funding = 3×10 + 3×0.6 = 31.8
        bars = _bars(date(2024, 1, 1), [95, 92, 90], highs=[99, 96, 93], lows=[94, 91, 89])
        res = _eval("卖出", bars=bars, leverage=3, funding=0.6)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], 31.8)

    def test_clamp_at_minus_100_without_liq_touch(self):
        # 近似声明④判别：价格未触强平线（liq_short=133.33，high≤132）但负资金费拖穿
        # 3×(−30) + 3×(−5) = −105 → 钳制输出恰 −100.0
        bars = _bars(date(2024, 1, 1), [120, 125, 130], highs=[125, 128, 132], lows=[115, 120, 126])
        res = _eval("卖出", bars=bars, leverage=3, funding=-5.0)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_non_perp_ignores_leverage(self):
        # is_perp=False + leverage=5 → 与不传 leverage 完全一致（bar1 low 74 若误进杠杆分支会被判强平）
        bars = _bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[74, 105, 106])
        self.assertEqual(
            _eval("买入", bars=bars, leverage=5, take_profit=110.0, is_perp=False),
            _eval("买入", bars=bars, take_profit=110.0, is_perp=False),
        )


class _TempDbTestCase(unittest.TestCase):
    """临时 DB + 配置隔离（形态同 tests/test_perp_backtest_service.py）。"""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "leverage_bt.db")
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _add_analysis(self, *, query_id, code, created_at, advice="买入"):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id=query_id, code=code, name=code, report_type="simple",
                sentiment_score=40, operation_advice=advice, trend_prediction="测试",
                analysis_summary="leverage test", created_at=created_at,
                context_snapshot=json.dumps({"enhanced_context": {"date": created_at.strftime("%Y-%m-%d")}}),
            ))
            session.commit()


class GetCandidatesPerpOnlyTestCase(_TempDbTestCase):
    def test_sql_filter_prevents_starvation(self):
        # 饥饿判别：5 条更新的股票分析 + 1 条更老的 perp，limit=3 < 股票行数。
        # 若只靠循环内跳过（无 SQL 粗滤），配额会被 3 条最新股票占满、永远选不到 perp。
        for i in range(5):
            self._add_analysis(query_id=f"s{i}", code="600519", created_at=datetime(2024, 1, 10 + i))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")

        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=3, eval_window_days=3,
            engine_version="v1-x3", force=False, perp_only=True,
        )
        self.assertEqual([r.code for r in rows], [PERP_CODE])

    def test_default_perp_only_false_keeps_existing_behavior(self):
        self._add_analysis(query_id="s0", code="600519", created_at=datetime(2024, 1, 2))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")
        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=10, eval_window_days=3,
            engine_version="v1", force=False,
        )
        self.assertEqual({r.code for r in rows}, {"600519", PERP_CODE})


def _seed_perp(db, *, code=PERP_CODE):
    """perp 分析 + 起始 bar + 3 根下跌前向 bar（同 E 的 service 测试种子：做空 1x 收益 5% + funding）。"""
    with db.get_session() as session:
        session.add(AnalysisHistory(
            query_id=f"q-{code}", code=code, name=code, report_type="simple",
            sentiment_score=40, operation_advice="卖出", trend_prediction="看空",
            analysis_summary="leverage test", created_at=datetime(2024, 1, 1),
            context_snapshot=json.dumps({"enhanced_context": {"date": "2024-01-01"}}),
        ))
        session.add(StockDaily(code=code, date=date(2024, 1, 1), open=100000.0, high=100500.0, low=99500.0, close=100000.0))
        session.add_all([
            StockDaily(code=code, date=date(2024, 1, 2), open=100000.0, high=100000.0, low=97000.0, close=98000.0),
            StockDaily(code=code, date=date(2024, 1, 3), open=98000.0, high=98000.0, low=95000.0, close=96000.0),
            StockDaily(code=code, date=date(2024, 1, 4), open=96000.0, high=96000.0, low=94000.0, close=95000.0),
        ])
        session.commit()


FUNDING_PATCH = patch("data_provider.crypto_derivatives.fetch_funding_rate_history", return_value=[0.0003])  # 0.03%


class LeverageServiceTestCase(_TempDbTestCase):
    def _rows(self):
        with self.db.get_session() as session:
            return session.query(BacktestResult).order_by(BacktestResult.id).all()

    def test_leverage_run_writes_tagged_rows_coexisting_with_v1(self):
        _seed_perp(self.db)
        with FUNDING_PATCH:
            svc = BacktestService(self.db)
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)              # 1x → v1
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=3)  # 3x → v1-x3
        rows = {r.engine_version: r for r in self._rows()}
        self.assertEqual(set(rows), {"v1", "v1-x3"})                                  # 标签隔离共存，互不覆盖
        self.assertAlmostEqual(rows["v1"].simulated_return_pct, 5.03, places=4)       # E 语义不变
        # liq_short=133333 未触（max high 100000）→ 放大：3×5 + 3×0.03 = 15.09
        self.assertAlmostEqual(rows["v1-x3"].simulated_return_pct, 15.09, places=4)
        self.assertEqual(rows["v1-x3"].simulated_exit_reason, "window_end_short")

    def test_non_perp_candidate_excluded_in_leverage_run(self):
        _seed_perp(self.db)
        self._add_analysis(query_id="s1", code="600519", created_at=datetime(2024, 1, 1))
        with FUNDING_PATCH:
            stats = BacktestService(self.db).run_backtest(eval_window_days=3, min_age_days=0, limit=10, leverage=3)
        self.assertEqual([r.code for r in self._rows()], [PERP_CODE])   # 股票候选不写入
        self.assertEqual(stats["processed"], 1)                          # 统计照实反映 perp-only run

    def test_loop_guard_catches_sql_filter_leak(self):
        # 两层过滤判别：ABC/BUSD:PERP 过得了 SQL 粗滤（后缀 :PERP）但 is_perp_code 为假（quote 非 USDT/USDC）
        # → 循环内兜底跳过，不写入、不计入统计
        self._add_analysis(query_id="b1", code="ABC/BUSD:PERP", created_at=datetime(2024, 1, 1), advice="卖出")
        with FUNDING_PATCH:
            stats = BacktestService(self.db).run_backtest(eval_window_days=3, min_age_days=0, limit=10, leverage=3)
        self.assertEqual(self._rows(), [])
        self.assertEqual(stats["processed"], 0)

    def test_leverage_none_falls_back_to_config(self):
        os.environ["CRYPTO_BACKTEST_LEVERAGE"] = "3"
        Config._instance = None
        _seed_perp(self.db)
        with FUNDING_PATCH:
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        self.assertEqual(self._rows()[0].engine_version, "v1-x3")

    def test_leverage_clamped_to_bounds(self):
        _seed_perp(self.db)
        with FUNDING_PATCH:
            svc = BacktestService(self.db)
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=0)    # → 1：不打标签
            svc.run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10, leverage=126)  # → 125
        self.assertEqual({r.engine_version for r in self._rows()}, {"v1", "v1-x125"})


class LeverageApiTestCase(_TempDbTestCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from api.app import app
        self.client = TestClient(app)

    def test_run_request_leverage_bounds_rejected(self):
        for bad in (0, 126):
            resp = self.client.post("/api/v1/backtest/run", json={"leverage": bad})
            self.assertEqual(resp.status_code, 422, f"leverage={bad} 应被 Field 校验拒绝")

    def test_run_passes_leverage_to_service(self):
        import api.v1.endpoints.backtest as backtest_ep
        captured = {}

        class _Spy:
            def __init__(self, *args, **kwargs):
                pass

            def run_backtest(self, **kwargs):
                captured.update(kwargs)
                return {"processed": 0, "saved": 0, "completed": 0, "insufficient": 0, "errors": 0}

        with patch.object(backtest_ep, "BacktestService", _Spy):
            resp = self.client.post("/api/v1/backtest/run", json={"leverage": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["leverage"], 3)

    def _seed_tagged_results(self):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id="pq1", code=PERP_CODE, name="BTC perp", report_type="simple",
                operation_advice="卖出", created_at=datetime(2024, 1, 1),
            ))
            session.commit()
            ah_id = session.query(AnalysisHistory).filter_by(query_id="pq1").one().id
            for version, ret in (("v1", 5.0), ("v1-x3", 15.0)):
                session.add(BacktestResult(
                    analysis_history_id=ah_id, code=PERP_CODE, analysis_date=date(2024, 1, 1),
                    eval_window_days=3, engine_version=version, eval_status="completed",
                    evaluated_at=datetime(2024, 1, 10), position_recommendation="short",
                    simulated_return_pct=ret,
                ))
            session.commit()

    def test_results_engine_version_filters_and_default_unchanged(self):
        self._seed_tagged_results()
        tagged = self.client.get("/api/v1/backtest/results", params={"engine_version": "v1-x3"}).json()
        self.assertEqual(tagged["total"], 1)
        self.assertEqual(tagged["items"][0]["engine_version"], "v1-x3")
        # 默认行为不变判别：不带参数仍只看 config 基础版本 v1
        default = self.client.get("/api/v1/backtest/results").json()
        self.assertEqual(default["total"], 1)
        self.assertEqual(default["items"][0]["engine_version"], "v1")

    def test_performance_engine_version_param(self):
        with self.db.get_session() as session:
            for version in ("v1", "v1-x3"):
                session.add(BacktestSummary(
                    scope="overall", code=OVERALL_SENTINEL_CODE, eval_window_days=3,
                    engine_version=version, total_evaluations=1, completed_count=1,
                ))
            session.commit()
        tagged = self.client.get("/api/v1/backtest/performance",
                                 params={"engine_version": "v1-x3", "eval_window_days": 3})
        self.assertEqual(tagged.status_code, 200)
        self.assertEqual(tagged.json()["engine_version"], "v1-x3")
        default = self.client.get("/api/v1/backtest/performance", params={"eval_window_days": 3})
        self.assertEqual(default.json()["engine_version"], "v1")

    def test_openapi_declares_engine_version_on_all_read_endpoints(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/v1/backtest/results",
                     "/api/v1/backtest/performance",
                     "/api/v1/backtest/performance/{code}"):
            names = {p["name"] for p in paths[path]["get"]["parameters"]}
            self.assertIn("engine_version", names, f"{path} 缺 engine_version 查询参数")


if __name__ == "__main__":
    unittest.main()
