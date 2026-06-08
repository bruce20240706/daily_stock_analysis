# -*- coding: utf-8 -*-
"""CryptoListingRepository 单元测试（隔离内存库）。"""

import unittest

from src.storage import DatabaseManager


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


if __name__ == "__main__":
    unittest.main()
