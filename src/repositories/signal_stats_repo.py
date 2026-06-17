# -*- coding: utf-8 -*-
"""SignalStatsRepository — signal_stats 表数据访问层。"""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import DatabaseManager, SignalStatRow

logger = logging.getLogger(__name__)


class SignalStatsRepository:
    """signal_stats 表 CRUD，支持按 (signal_type, market, interval, horizon) 唯一键覆盖写。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_batch(self, rows: List[SignalStatRow], *, replace_existing: bool = True) -> int:
        """批量写入 signal_stats 行。replace_existing=True 时先删同唯一键再插，避免重复累积。"""
        if not rows:
            return 0
        with self.db.get_session() as session:
            try:
                if replace_existing:
                    keys = sorted({
                        (r.signal_type, r.market, r.interval, r.horizon) for r in rows
                    })
                    for st, mk, iv, hz in keys:
                        session.execute(
                            delete(SignalStatRow).where(and_(
                                SignalStatRow.signal_type == st,
                                SignalStatRow.market == mk,
                                SignalStatRow.interval == iv,
                                SignalStatRow.horizon == hz,
                            ))
                        )
                session.add_all(rows)
                session.commit()
                return len(rows)
            except Exception as exc:
                session.rollback()
                logger.error(f"保存 signal_stats 失败: {exc}")
                raise

    def get(
        self,
        signal_type: str,
        market: str,
        *,
        interval: str = "1d",
        horizon: Optional[int] = None,
    ) -> Optional[SignalStatRow]:
        """查询 signal_stats 行；horizon=None 时取最新 computed_at。"""
        with self.db.get_session() as session:
            cond = [
                SignalStatRow.signal_type == signal_type,
                SignalStatRow.market == market,
                SignalStatRow.interval == interval,
            ]
            if horizon is not None:
                cond.append(SignalStatRow.horizon == int(horizon))
            q = (
                select(SignalStatRow)
                .where(and_(*cond))
                .order_by(desc(SignalStatRow.computed_at))
            )
            return session.execute(q).scalars().first()
