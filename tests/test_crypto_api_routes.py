"""crypto API 路由回归测试。

BASE/QUOTE 形式（含 '/'，如 BTC/USDT）的代码必须能命中带 {code} 路径参数的路由。
默认 str 路径转换器不匹配 '/'，会把 BTC/USDT/quote 拆段导致路由层 404；
改用 :path 转换器后，raw 斜杠与 %2F 编码两种写法都应命中 handler，且对存量股票代码向后兼容。

测试只验证“路由是否命中”，不依赖真实网络/DB：
- stocks 的 quote/history：mock StockService，断言 handler 真正执行并完整收到 'BTC/USDT'；
- 其余 3 条路由：用 GET 探测，断言不是“路由层未命中的默认 404”（命中后即便方法不符也会是 405 而非 404）。
"""
import pytest
from fastapi.testclient import TestClient

from api.app import app
import api.v1.endpoints.stocks as stocks_ep

client = TestClient(app)

CRYPTO_RAW = "BTC/USDT"
CRYPTO_ENC = "BTC%2FUSDT"  # encodeURIComponent('BTC/USDT')


def _is_route_miss(resp) -> bool:
    """路由层未命中 = FastAPI 默认 404，body 恰为 {'detail': 'Not Found'}。

    handler 真正执行后返回的 404 使用自定义 dict detail，不会等于该签名。
    """
    if resp.status_code != 404:
        return False
    try:
        return resp.json() == {"detail": "Not Found"}
    except Exception:
        return False


class _FakeStockService:
    """仅覆盖被测 handler 用到的方法，回传含入参 code 的最小数据。"""

    def get_realtime_quote(self, code):
        return {"stock_code": code, "current_price": 1.0}

    def get_history_data(self, stock_code, period="daily", days=30):
        return {"stock_code": stock_code, "stock_name": None, "data": []}


@pytest.fixture
def mock_stock_service(monkeypatch):
    monkeypatch.setattr(stocks_ep, "StockService", lambda *a, **k: _FakeStockService())


# --- stocks: quote / history（mock 后强校验 handler 命中 + 完整提取 '/'）---

@pytest.mark.parametrize("code", [CRYPTO_RAW, CRYPTO_ENC])
def test_quote_route_matches_crypto(mock_stock_service, code):
    resp = client.get(f"/api/v1/stocks/{code}/quote")
    assert not _is_route_miss(resp), f"quote 路由未命中 crypto: {resp.status_code} {resp.text[:120]}"
    assert resp.status_code == 200
    assert resp.json()["stock_code"] == "BTC/USDT"  # '/' 被完整提取，未被截断


@pytest.mark.parametrize("code", [CRYPTO_RAW, CRYPTO_ENC])
def test_history_route_matches_crypto(mock_stock_service, code):
    resp = client.get(f"/api/v1/stocks/{code}/history?days=5")
    assert not _is_route_miss(resp), f"history 路由未命中 crypto: {resp.status_code} {resp.text[:120]}"
    assert resp.status_code == 200
    assert resp.json()["stock_code"] == "BTC/USDT"


def test_quote_route_still_matches_plain_stock(mock_stock_service):
    """存量股票代码必须保持向后兼容。"""
    resp = client.get("/api/v1/stocks/600519/quote")
    assert resp.status_code == 200
    assert resp.json()["stock_code"] == "600519"


# --- 其余含 {code}/{symbol} 路径参数的路由：仅验证路由命中（不依赖网络/DB 结果）---

SECONDARY_ROUTES = [
    "/api/v1/backtest/performance/{code}",
    "/api/v1/portfolio/positions/{code}/analysis",
    "/api/v1/history/by-code/{code}",
]


@pytest.mark.parametrize("template", SECONDARY_ROUTES)
@pytest.mark.parametrize("code", [CRYPTO_RAW, CRYPTO_ENC])
def test_secondary_routes_match_crypto(template, code):
    resp = client.get(template.format(code=code))
    assert not _is_route_miss(resp), (
        f"路由未命中 crypto: {template} code={code} -> {resp.status_code} {resp.text[:120]}"
    )
