# -*- coding: utf-8 -*-
"""CryptoListingRepository 单元测试（隔离内存库）。"""

import unittest

from sqlalchemy import select

from src.storage import DatabaseManager, CryptoSymbolSnapshot


class TestCryptoListingRepository(unittest.TestCase):
    """快照仓储读写测试，使用 sqlite:///:memory: 隔离真实 DB。"""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()

    def test_snapshot_roundtrip(self) -> None:
        from src.repositories.crypto_listing_repo import CryptoListingRepository

        repo = CryptoListingRepository(db_manager=self.db)

        # 尚无快照 → None
        assert repo.get_base_assets("binance") is None

        # 写入首条快照
        repo.save_base_assets("binance", {"AAA", "BBB"})
        assert repo.get_base_assets("binance") == {"AAA", "BBB"}

        # 覆盖更新
        repo.save_base_assets("binance", {"AAA", "CCC"})
        assert repo.get_base_assets("binance") == {"AAA", "CCC"}

    def test_corrupt_base_assets_returns_empty_set(self) -> None:
        """base_assets 存储了非列表 JSON（如 dict）时，get_base_assets 应返回 set()。"""
        from src.repositories.crypto_listing_repo import CryptoListingRepository

        repo = CryptoListingRepository(db_manager=self.db)

        # 先用正常路径写入一条快照
        repo.save_base_assets("okx", {"BTC", "ETH"})
        assert repo.get_base_assets("okx") == {"BTC", "ETH"}

        # 直接在 DB 中将 base_assets 改为非列表 JSON（模拟快照损坏）
        with self.db.get_session() as session:
            row = session.execute(
                select(CryptoSymbolSnapshot).where(CryptoSymbolSnapshot.exchange == "okx")
            ).scalar_one()
            row.base_assets = '{"a": 1}'
            session.commit()

        # 损坏的行应降级为 set()，而不是返回 dict 的键
        result = repo.get_base_assets("okx")
        assert result == set(), f"期望 set()，实际得到 {result!r}"


if __name__ == "__main__":
    unittest.main()
