"""/history 端点周/月线端到端回归（M4-A）。

验证 period=weekly/monthly 能正常通过并返回正确 period 字段；
同时验证 M4-A 将 days 上限从 365 放宽至 1825 后，1825 返回 200 而 1826 返回 422。
不依赖真实网络或 DB，mock StockService。
"""
import pytest
from fastapi.testclient import TestClient

from api.app import app
import api.v1.endpoints.stocks as stocks_ep

client = TestClient(app)


class _FakeService:
    """返回最小合法结构，并将 period 原样回传便于断言。"""

    def get_history_data(self, stock_code, period="daily", days=30):
        return {
            "stock_code": stock_code,
            "stock_name": "X",
            "period": period,
            "data": [
                {
                    "date": "2024-01-05",
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "change_percent": None,
                }
            ],
        }


@pytest.fixture
def mock_fake_service(monkeypatch):
    monkeypatch.setattr(stocks_ep, "StockService", lambda *a, **k: _FakeService())


def test_history_weekly_returns_200(mock_fake_service):
    r = client.get("/api/v1/stocks/600519/history?period=weekly&days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "weekly"
    assert len(body["data"]) == 1


def test_history_monthly_returns_200(mock_fake_service):
    r = client.get("/api/v1/stocks/600519/history?period=monthly&days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "monthly"
    assert len(body["data"]) == 1


def test_history_days_cap_widened_to_1825(mock_fake_service):
    assert (
        client.get(
            "/api/v1/stocks/600519/history?period=monthly&days=1825"
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/stocks/600519/history?period=monthly&days=1826"
        ).status_code
        == 422
    )
