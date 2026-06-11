# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪聚合：OI 加权资金费率 + 总 OI + top-mover，presence-only。"""
import data_provider.crypto_derivatives as cd


def _fake_metrics(table):
    def _impl(base, quote):
        return table.get(f"{base}/{quote}", {})
    return _impl


def test_aggregates_weighted_funding_and_total_oi(monkeypatch):
    table = {
        "BTC/USDT": {"funding_rate": 0.0001, "open_interest_usd": 1000.0, "source": "okx"},
        "ETH/USDT": {"funding_rate": 0.0003, "open_interest_usd": 3000.0, "source": "okx"},
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert abs(out["avg_funding_rate"] - 0.00025) < 1e-12
    assert out["total_open_interest_usd"] == 4000.0
    assert [c["symbol"] for c in out["coins"]] == ["ETH/USDT", "BTC/USDT"]


def test_top_movers_capped_at_5_and_sorted(monkeypatch):
    table = {f"C{i}/USDT": {"funding_rate": (i + 1) * 0.0001, "open_interest_usd": 100.0} for i in range(7)}
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot([f"C{i}/USDT" for i in range(7)])
    assert len(out["coins"]) == 5
    assert out["coins"][0]["symbol"] == "C6/USDT"


def test_presence_only_partial_fields(monkeypatch):
    table = {
        "BTC/USDT": {"funding_rate": 0.0002},
        "ETH/USDT": {"open_interest_usd": 5000.0},
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert "avg_funding_rate" not in out
    assert out["total_open_interest_usd"] == 5000.0
    assert len(out["coins"]) == 2


def test_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: {})
    assert cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"]) == {}


def test_empty_symbols_returns_empty():
    assert cd.fetch_perp_market_snapshot([]) == {}


def test_aggregates_oi_weighted_long_short_ratio(monkeypatch):
    table = {
        "BTC/USDT": {"open_interest_usd": 1000.0, "long_short_ratio": 1.0, "long_short_ratio_top": 0.8, "source": "okx"},
        "ETH/USDT": {"open_interest_usd": 3000.0, "long_short_ratio": 2.0, "long_short_ratio_top": 1.2, "source": "okx"},
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    # 全市场 (1*1000 + 2*3000)/4000 = 1.75；大户 (0.8*1000 + 1.2*3000)/4000 = 1.1
    assert abs(out["avg_long_short_ratio"] - 1.75) < 1e-12
    assert abs(out["avg_long_short_ratio_top"] - 1.1) < 1e-12
    by_sym = {c["symbol"]: c for c in out["coins"]}
    assert by_sym["ETH/USDT"]["long_short_ratio"] == 2.0
    assert by_sym["BTC/USDT"]["long_short_ratio_top"] == 0.8


def test_long_short_ratio_excludes_coin_without_oi(monkeypatch):
    table = {
        "BTC/USDT": {"open_interest_usd": 1000.0, "long_short_ratio": 1.0},
        "ETH/USDT": {"long_short_ratio": 5.0},  # 无 OI：进篮子但不参与权重
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert abs(out["avg_long_short_ratio"] - 1.0) < 1e-12  # 仅 BTC 计入
    assert len(out["coins"]) == 2                          # ETH 仅有 ls 也进篮子


def test_no_long_short_ratio_when_absent(monkeypatch):
    table = {"BTC/USDT": {"funding_rate": 0.0001, "open_interest_usd": 1000.0}}
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT"])
    assert "avg_long_short_ratio" not in out
    assert "avg_long_short_ratio_top" not in out


def test_long_short_ratio_denominators_independent(monkeypatch):
    table = {
        "BTC/USDT": {"open_interest_usd": 1000.0, "long_short_ratio": 1.0},                              # 只有全市场
        "ETH/USDT": {"open_interest_usd": 3000.0, "long_short_ratio": 2.0, "long_short_ratio_top": 1.2},  # 两者都有
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert abs(out["avg_long_short_ratio"] - 1.75) < 1e-12       # (1*1000+2*3000)/4000，两币都计
    assert abs(out["avg_long_short_ratio_top"] - 1.2) < 1e-12    # 仅 ETH 计（分母独立，不共享）
