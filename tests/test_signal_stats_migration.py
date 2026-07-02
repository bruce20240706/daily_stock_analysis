# -*- coding: utf-8 -*-
"""Inc 1c: signal_stats 幂等补列迁移 + 两新列落库读回(spec §4.4/§7.7)。"""
import sqlite3

from sqlalchemy import inspect

from src.storage import DatabaseManager, SignalStatRow


def _columns(db) -> set:
    insp = inspect(db._engine)
    return {c["name"] for c in insp.get_columns("signal_stats")}


def test_new_columns_present_on_fresh_db(tmp_path):
    # DatabaseManager 为单例(见 src/storage.py test_storage.py 同款用法):须显式 reset，
    # 否则跨用例复用已初始化实例导致本用例实际未绑定到本函数的 tmp_path db。
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh.db'}")
    cols = _columns(db)
    assert "ci_low_corrected" in cols
    assert "family_size" in cols
    DatabaseManager.reset_instance()


def test_guarded_alter_adds_columns_to_legacy_db_and_is_idempotent(tmp_path):
    DatabaseManager.reset_instance()
    # 造"老库":手工建缺两新列的 signal_stats 表(唯一键与现 schema 一致)
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE signal_stats ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, signal_type VARCHAR(64) NOT NULL, "
        "market VARCHAR(16) NOT NULL, interval VARCHAR(8) NOT NULL DEFAULT '1d', "
        "horizon INTEGER NOT NULL, win INTEGER, loss INTEGER, sample INTEGER, "
        "win_rate FLOAT, ci_low FLOAT, ci_high FLOAT, baseline_win_rate FLOAT, "
        "excess FLOAT, computed_at DATETIME)"
    )
    con.execute(
        "INSERT INTO signal_stats (signal_type, market, interval, horizon, win, loss, "
        "sample, win_rate, ci_low, ci_high, baseline_win_rate, excess) "
        "VALUES ('volume_breakout','cn','1d',10,7,3,10,0.7,0.60,0.82,0.50,0.10)"
    )
    con.commit()
    con.close()

    db = DatabaseManager(db_url=f"sqlite:///{legacy}")   # init 触发 guarded ALTER
    cols = _columns(db)
    assert "ci_low_corrected" in cols and "family_size" in cols

    # 老行补列后为 NULL(legacy 哨兵,plain nullable 无 default)
    with db.get_session() as s:
        row = s.query(SignalStatRow).first()
        assert row.ci_low_corrected is None
        assert row.family_size is None

    # 幂等:再次调用不抛
    db._ensure_signal_stats_columns()
    assert "ci_low_corrected" in _columns(db)
    DatabaseManager.reset_instance()


def test_two_new_columns_roundtrip(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'rt.db'}")
    row = SignalStatRow(signal_type="volume_breakout", market="cn", interval="1d",
                        horizon=10, win=7, loss=3, sample=10, win_rate=0.7,
                        ci_low=0.60, ci_high=0.82, baseline_win_rate=0.50,
                        excess=0.10, ci_low_corrected=0.55, family_size=20)
    with db.get_session() as s:
        s.add(row)
        s.commit()
        fetched = s.query(SignalStatRow).first()
        assert fetched.ci_low_corrected == 0.55
        assert fetched.family_size == 20
    DatabaseManager.reset_instance()
