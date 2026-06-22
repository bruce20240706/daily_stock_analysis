# -*- coding: utf-8 -*-
"""signal_stats ORM + repo 单元测试（隔离 tmp sqlite）。"""

import pytest
from src.storage import DatabaseManager, SignalStatRow
from src.repositories.signal_stats_repo import SignalStatsRepository


@pytest.fixture
def tmp_db(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path}/m3.db")
    yield db
    DatabaseManager.reset_instance()


def _row(**kw):
    base = dict(
        signal_type="volume_breakout", market="cn", interval="1d", horizon=10,
        win=6, loss=2, sample=8, win_rate=0.75, ci_low=0.4, ci_high=0.9,
        baseline_win_rate=0.5, excess=-0.1,
    )
    base.update(kw)
    return SignalStatRow(**base)


def test_save_and_get_roundtrip(tmp_db):
    repo = SignalStatsRepository(tmp_db)
    repo.save_batch([_row()])
    got = repo.get("volume_breakout", "cn", interval="1d", horizon=10)
    assert got is not None and got.win == 6 and got.sample == 8 and got.market == "cn"


def test_save_batch_replace_existing_overwrites_same_key(tmp_db):
    repo = SignalStatsRepository(tmp_db)
    repo.save_batch([_row(win=6, sample=8)])
    repo.save_batch([_row(win=9, sample=12)], replace_existing=True)
    got = repo.get("volume_breakout", "cn", horizon=10)
    assert got.win == 9 and got.sample == 12  # 不重复累积
    with tmp_db.get_session() as s:
        assert s.query(SignalStatRow).filter_by(
            signal_type="volume_breakout", market="cn", interval="1d", horizon=10
        ).count() == 1


def test_get_missing_returns_none(tmp_db):
    assert SignalStatsRepository(tmp_db).get("nope", "cn", horizon=10) is None


def test_get_result_attributes_readable_after_session_closed(tmp_db):
    repo = SignalStatsRepository(tmp_db)
    repo.save_batch([_row(win=6, win_rate=0.75, ci_low=0.4, sample=8, baseline_win_rate=0.5, excess=-0.1)])
    got = repo.get("volume_breakout", "cn", interval="1d", horizon=10)
    assert got is not None
    # 验证 session 关闭后标量属性仍可读（模拟 A6 调用方式）
    assert got.win == 6
    assert got.win_rate == 0.75
    assert got.ci_low == 0.4
    assert got.sample == 8
    assert got.baseline_win_rate == 0.5
    assert got.excess == -0.1
