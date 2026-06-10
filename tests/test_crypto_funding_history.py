# -*- coding: utf-8 -*-
"""fetch_funding_rate_history：半开窗口求和/边界、向后分页、降级、超页截断。"""
import data_provider.crypto_derivatives as cd


def _page_fake(settlements):
    """模拟 OKX：按 after=cursor 返回 fundingTime < cursor 的最多 100 条，最新在前。
    settlements 须按 ts 降序。返回 (fake, calls)。"""
    calls = []

    def fake(url, params=None, headers=None):
        after = int(params["after"])
        calls.append(after)
        recs = [s for s in settlements if s[0] < after][:100]
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "fundingTime": str(ts), "fundingRate": str(fr)}
            for ts, fr in recs
        ]}

    return fake, calls


def test_half_open_window_sum_and_boundaries(monkeypatch):
    # 窗口 [1000, 4000)：含 ts==1000，排除 ts==4000；窗口外 500 排除
    settlements = [(4000, 0.04), (3000, 0.03), (2000, 0.02), (1000, 0.01), (500, 0.005)]
    fake, _calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", 1000, 4000)
    assert abs(sum(rates) - (0.03 + 0.02 + 0.01)) < 1e-9
    assert len(rates) == 3


def test_backward_pagination_concatenates(monkeypatch):
    step = 28_800_000  # 8h ms
    end_ms = 100 * step
    # 130 个结算，全部 < end_ms，降序；窗口含前 120 个
    settlements = [(end_ms - (i + 1) * step, 0.0001) for i in range(130)]
    start_ms = end_ms - 120 * step  # ts >= start_ms 命中 i=0..119（半开含 start）
    fake, calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", start_ms, end_ms)
    assert len(rates) == 120
    assert abs(sum(rates) - 0.012) < 1e-9
    assert len(calls) >= 2  # 跨页发生


def test_non_linear_quote_short_circuits_no_call(monkeypatch):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(cd, "_http_get_json", spy)
    assert cd.fetch_funding_rate_history("BTC", "USD", 0, 1) == []
    assert called["n"] == 0


def test_exception_fails_soft_to_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("okx down")

    monkeypatch.setattr(cd, "_http_get_json", boom)
    assert cd.fetch_funding_rate_history("BTC", "USDT", 0, 10_000) == []


def test_max_pages_truncation(monkeypatch):
    # 1300 个结算全部落窗口内 → 12 页 ×100 截断为 1200
    end_ms = 2000
    settlements = [(end_ms - (i + 1), 0.0001) for i in range(1300)]
    start_ms = end_ms - 1300 - 1
    fake, calls = _page_fake(settlements)
    monkeypatch.setattr(cd, "_http_get_json", fake)
    rates = cd.fetch_funding_rate_history("BTC", "USDT", start_ms, end_ms)
    assert len(rates) == 1200
    assert len(calls) == 12
