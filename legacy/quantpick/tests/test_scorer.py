from __future__ import annotations

from quantpick.screening.scorer import score_and_rank, weighted_score


def test_weighted_score_normalized() -> None:
    assert abs(weighted_score({"a": 1.0, "b": 0.0}, {"a": 1.0, "b": 1.0}) - 0.5) < 1e-9


def test_weighted_score_missing_factor_is_zero() -> None:
    assert abs(weighted_score({"a": 1.0}, {"a": 1.0, "b": 1.0}) - 0.5) < 1e-9


def test_weighted_score_zero_weights() -> None:
    assert weighted_score({"a": 1.0}, {}) == 0.0


def test_score_and_rank_orders_desc(sample_rows) -> None:
    ranked = score_and_rank(sample_rows, {"f1": 1.0, "f2": 0.0})
    assert ranked[0].code == "600519.SH"     # highest f1
    assert ranked[0].rank == 1
    assert ranked[-1].rank == len(ranked)


def test_score_and_rank_top_n(sample_rows) -> None:
    ranked = score_and_rank(sample_rows, {"f1": 1.0, "f2": 1.0}, top_n=2)
    assert len(ranked) == 2
