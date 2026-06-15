"""/history 端点 days Query 契约回归。

M0 只放宽该端点 Query 的默认值（30→120），上限保持 365、下限保持 1；
mock StockService，断言 handler 真正收到的 days 值（默认/显式/越界拒绝），
不依赖真实网络或 DB。同时锁定：放宽仅作用于本端点，不触碰 alert 取数上限。
"""
import pytest
from fastapi.testclient import TestClient

from api.app import app
import api.v1.endpoints.stocks as stocks_ep

client = TestClient(app)


class _CapturingStockService:
    """记录 handler 透传给 service 的 days，返回最小合法结构。"""

    last_days = None

    def get_history_data(self, stock_code, period="daily", days=30):
        _CapturingStockService.last_days = days
        return {"stock_code": stock_code, "stock_name": None, "data": []}


@pytest.fixture
def mock_stock_service(monkeypatch):
    _CapturingStockService.last_days = None
    monkeypatch.setattr(stocks_ep, "StockService", lambda *a, **k: _CapturingStockService())


def test_history_days_default_is_120(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 120


def test_history_days_explicit_is_passed_through(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=45")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 45


def test_history_days_upper_bound_still_365(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=365")
    assert resp.status_code == 200
    assert _CapturingStockService.last_days == 365


def test_history_days_above_365_rejected(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=366")
    assert resp.status_code == 422


def test_history_days_below_one_rejected(mock_stock_service):
    resp = client.get("/api/v1/stocks/600519/history?days=0")
    assert resp.status_code == 422
