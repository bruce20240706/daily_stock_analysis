import sqlite3

from sqlalchemy import inspect

from src.storage import DatabaseManager, BacktestResult


def _columns(db) -> set:
    insp = inspect(db._engine)
    return {c["name"] for c in insp.get_columns("backtest_results")}


def test_new_columns_present_on_fresh_db(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh.db'}")
    cols = _columns(db)
    assert "bar_interval" in cols
    assert "first_hit_bar_index" in cols


def test_guarded_alter_adds_columns_to_legacy_db_and_is_idempotent(tmp_path):
    # 造一个"老库":手工建一个缺新列的 backtest_results 表
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE backtest_results ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_history_id INTEGER NOT NULL, "
        "code VARCHAR(10) NOT NULL, eval_window_days INTEGER NOT NULL DEFAULT 10, "
        "engine_version VARCHAR(16) NOT NULL DEFAULT 'v1', eval_status VARCHAR(16) NOT NULL DEFAULT 'pending')"
    )
    con.commit()
    con.close()

    db = DatabaseManager(db_url=f"sqlite:///{legacy}")     # init 触发 guarded ALTER
    cols = _columns(db)
    assert "bar_interval" in cols and "first_hit_bar_index" in cols

    # 幂等:再次调用不抛
    db._ensure_backtest_intraday_columns()
    assert "bar_interval" in _columns(db)


def test_bar_interval_defaults_to_1d(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'d.db'}")
    row = BacktestResult(analysis_history_id=1, code="BTC/USDT", eval_status="completed")
    with db.get_session() as s:
        s.add(row); s.commit()
        fetched = s.query(BacktestResult).first()
        assert fetched.bar_interval == "1d"
        assert fetched.first_hit_bar_index is None
