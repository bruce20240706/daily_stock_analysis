from __future__ import annotations

from quantpick.portfolio.holdings import load_holdings


def test_load_example_holdings() -> None:
    res = load_holdings("config/holdings.example.yaml")
    assert res.ok
    holds = res.value or []
    assert any(h.code == "600519.SH" for h in holds)
    # watchlist entry without cost_price is allowed
    assert any(h.cost_price is None for h in holds)


def test_load_missing_holdings_is_empty_ok() -> None:
    res = load_holdings("config/__does_not_exist__.yaml")
    assert res.ok
    assert res.value == []
