# -*- coding: utf-8 -*-
"""crypto 新上线快照仓储（Binance 差分用）。"""
import json
import logging
from datetime import datetime
from typing import Optional, Set

from sqlalchemy import select

from src.storage import DatabaseManager, CryptoSymbolSnapshot

logger = logging.getLogger(__name__)


class CryptoListingRepository:
    """读写每交易所 spot base-asset 快照。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def get_base_assets(self, exchange: str) -> Optional[Set[str]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(CryptoSymbolSnapshot).where(CryptoSymbolSnapshot.exchange == exchange).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            try:
                return set(json.loads(row.base_assets))
            except (TypeError, ValueError):
                return set()

    def save_base_assets(self, exchange: str, bases: Set[str]) -> None:
        payload = json.dumps(sorted(bases))
        with self.db.get_session() as session:
            row = session.execute(
                select(CryptoSymbolSnapshot).where(CryptoSymbolSnapshot.exchange == exchange).limit(1)
            ).scalar_one_or_none()
            if row is None:
                session.add(CryptoSymbolSnapshot(
                    exchange=exchange,
                    base_assets=payload,
                    captured_at=datetime.now(),
                ))
            else:
                row.base_assets = payload
                row.captured_at = datetime.now()
            session.commit()
