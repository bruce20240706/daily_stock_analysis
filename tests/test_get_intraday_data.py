"""Tests for DataFetcherManager.get_intraday_data and CryptoExchangeBase.get_intraday_data.

All tests are fully offline: real HTTP is never invoked. We mock either
``_request_klines`` (whole fetch) or ``_http_get`` (transport) so a failing path
cannot silently fall through to a live exchange request.
"""
import pandas as pd
import pytest

from data_provider.base import DataFetcherManager, DataFetchError


# Binance kline row layout (>=8 elements; _parse_klines reads r[7] quoteAssetVolume):
#   [openTime, open, high, low, close, volume, closeTime, quoteAssetVolume, ...]
# 1_700_000_000_000 ms == 2023-11-14T22:13:20Z (intentionally non-midnight).
_BAR_0 = [1_700_000_000_000, "100", "102", "99", "101", "10", 1_700_000_300_000, "1010"]
_BAR_1 = [1_700_000_300_000, "101", "103", "100", "102", "12", 1_700_000_600_000, "1224"]
_BAR_2 = [1_700_000_600_000, "102", "104", "101", "103", "11", 1_700_000_900_000, "1133"]


def _clear_cache():
    import data_provider.base as base_mod
    if hasattr(base_mod, "_INTRADAY_CACHE"):
        base_mod._INTRADAY_CACHE.clear()


def test_non_crypto_intraday_raises():
    # A股已支持，故用仍不支持的美股码验证"非支持市场在任何 fetcher 调用前被拒"
    mgr = DataFetcherManager()
    with pytest.raises(DataFetchError):
        mgr.get_intraday_data("AAPL", interval="5m", days=1)


def test_crypto_intraday_returns_ohlc_without_indicators(monkeypatch):
    """mock single-page klines: 3 x 5m bars; assert OHLCV+datetime cols, no indicators,
    and that datetime carries REAL intraday time resolution (distinct, non-midnight)."""
    from data_provider.binance_fetcher import BinanceFetcher

    page = [_BAR_0, _BAR_1, _BAR_2]
    captured = {}

    def fake_request_klines(self, symbol, days, interval="1d", start_ms=None):
        captured["interval"] = interval
        return page

    monkeypatch.setattr(BinanceFetcher, "_request_klines", fake_request_klines)

    f = BinanceFetcher()
    df = f.get_intraday_data("BTC/USDT", interval="5m", days=1)
    assert captured["interval"] == "5m"  # interval passed through
    assert {"datetime", "open", "high", "low", "close", "volume"}.issubset(set(df.columns))
    assert "ma20" not in df.columns and "atr" not in df.columns  # no indicators
    assert len(df) == 3

    # --- finding #1 guard: datetime must keep minute resolution, not be date-only midnight ---
    dts = pd.to_datetime(df["datetime"])
    assert dts.nunique() == 3, "three 5m bars must have distinct timestamps"
    # None should be midnight (00:00:00); all are within the same UTC day.
    assert not (dts.dt.hour.eq(0) & dts.dt.minute.eq(0) & dts.dt.second.eq(0)).any(), (
        "intraday datetime collapsed to midnight (lost time-of-day)"
    )
    # Spacing between consecutive 5m bars is exactly 5 minutes (300_000 ms).
    deltas = dts.sort_values().diff().dropna()
    assert (deltas == pd.Timedelta(minutes=5)).all()


def test_manager_get_intraday_data_crypto(monkeypatch):
    """DataFetcherManager.get_intraday_data routes to a crypto fetcher and returns (df, name)."""
    from data_provider.binance_fetcher import BinanceFetcher

    page = [_BAR_0, _BAR_1]

    def fake_request_klines(self, symbol, days, interval="1d", start_ms=None):
        return page

    monkeypatch.setattr(BinanceFetcher, "_request_klines", fake_request_klines)

    mgr = DataFetcherManager()
    _clear_cache()
    df, source = mgr.get_intraday_data("BTC/USDT", interval="5m", days=1)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "datetime" in df.columns
    assert "ma20" not in df.columns
    assert source == "BinanceFetcher"


def _patch_all_crypto_request_klines(monkeypatch, fn):
    """Patch _request_klines on ALL crypto fetchers so a routing fallthrough cannot
    reach a live exchange (offline guarantee)."""
    from data_provider.binance_fetcher import BinanceFetcher
    from data_provider.okx_fetcher import OkxFetcher
    from data_provider.coinbase_fetcher import CoinbaseFetcher
    for cls in (BinanceFetcher, OkxFetcher, CoinbaseFetcher):
        monkeypatch.setattr(cls, "_request_klines", fn)


def test_manager_intraday_cache_ttl_zero_skips_cache(monkeypatch):
    """TTL=0 -> no caching: the second call recomputes (fetcher invoked twice)."""
    call_count = {"n": 0}

    def fake_request_klines(self, symbol, days, interval="1d", start_ms=None):
        call_count["n"] += 1
        return [_BAR_0, _BAR_1]

    _patch_all_crypto_request_klines(monkeypatch, fake_request_klines)

    class FakeCfg:
        crypto_intraday_minute_cache_ttl_s = 0

    monkeypatch.setattr("data_provider.base._get_intraday_config", lambda: FakeCfg())

    mgr = DataFetcherManager()
    _clear_cache()

    df1, _ = mgr.get_intraday_data("BTC/USDT", interval="5m", days=1)
    df2, _ = mgr.get_intraday_data("BTC/USDT", interval="5m", days=1)
    assert not df1.empty and not df2.empty
    assert call_count["n"] == 2, "TTL=0 must recompute on every call (no cache)"


def test_manager_intraday_cache_ttl_positive_hits_cache(monkeypatch):
    """Positive TTL -> second call served from cache (fetcher invoked once)."""
    call_count = {"n": 0}

    def fake_request_klines(self, symbol, days, interval="1d", start_ms=None):
        call_count["n"] += 1
        return [_BAR_0, _BAR_1]

    _patch_all_crypto_request_klines(monkeypatch, fake_request_klines)

    class FakeCfg:
        crypto_intraday_minute_cache_ttl_s = 900

    monkeypatch.setattr("data_provider.base._get_intraday_config", lambda: FakeCfg())

    mgr = DataFetcherManager()
    _clear_cache()
    mgr.get_intraday_data("BTC/USDT", interval="5m", days=1)
    df2, source2 = mgr.get_intraday_data("BTC/USDT", interval="5m", days=1)
    assert call_count["n"] == 1, "second call must hit cache, not the fetcher"
    assert not df2.empty
    assert source2 == "cache"


def test_binance_paging_multi_page_forward(monkeypatch):
    """finding #2 guard: limit > MAX_LIMIT(1000) triggers forward paging.

    Mock _http_get to return two full pages (1000 each) then a short final page.
    Assert: assembled result exceeds 1000 bars, terminates, startTime advances
    FORWARD (each page request's startTime strictly increases, never into future
    past available data), and result is truncated to the requested count.
    """
    from data_provider.binance_fetcher import BinanceFetcher

    f = BinanceFetcher()
    # Deterministic clock so days->startTime is reproducible.
    anchor_now_ms = 2_000_000_000_000
    monkeypatch.setattr(BinanceFetcher, "_now_ms", staticmethod(lambda: anchor_now_ms))

    interval_ms = 5 * 60 * 1000  # 5m
    requested_start_times = []
    # Build three pages of consecutive 5m bars starting from each requested startTime.
    pages = []  # list of generated pages, consumed in order

    def make_page(start_ms, count):
        rows = []
        t = start_ms
        for _ in range(count):
            close = t + interval_ms - 1
            rows.append([t, "1", "2", "0", "1", "5", close, "5"])
            t += interval_ms
        return rows

    state = {"call": 0}
    # Full pages every time: paging continues until requested count is reached, then
    # the result is truncated to the requested limit (proves count-bounded termination).
    page_full = 1000

    def fake_http_get(self, url, params):
        start = params["startTime"]
        requested_start_times.append(start)
        state["call"] += 1
        return make_page(start, page_full)

    monkeypatch.setattr(BinanceFetcher, "_http_get", fake_http_get)

    # days=8 with 5m bars -> _intraday_limit = 8 * 288 = 2304 (> 1000), forces paging.
    days = 8
    out = f._request_klines("BTCUSDT", days=days, interval="5m")

    expected_limit = f._intraday_limit(days, "5m")
    assert expected_limit > 1000
    assert len(out) == expected_limit  # truncated to requested count (count-bounded)
    assert len(out) > 1000  # multiple pages assembled

    # startTime must advance strictly FORWARD across requests (guards the future-stall bug).
    assert requested_start_times == sorted(requested_start_times)
    assert len(set(requested_start_times)) == len(requested_start_times)
    # First request anchors at now - days*86400*1000 (not unanchored -> no "latest N" trap).
    assert requested_start_times[0] == anchor_now_ms - days * 86400 * 1000
    # Terminates: enough pages to cover the limit, plus the protective cap, no infinite loop.
    assert 3 <= state["call"] <= (expected_limit // 1000) + 2


def test_binance_paging_terminates_on_empty(monkeypatch):
    """Paging must terminate (not infinite-loop) if the endpoint returns empty early."""
    from data_provider.binance_fetcher import BinanceFetcher

    f = BinanceFetcher()
    monkeypatch.setattr(BinanceFetcher, "_now_ms", staticmethod(lambda: 2_000_000_000_000))
    calls = {"n": 0}

    def fake_http_get(self, url, params):
        calls["n"] += 1
        return []  # immediately empty

    monkeypatch.setattr(BinanceFetcher, "_http_get", fake_http_get)
    out = f._request_klines("BTCUSDT", days=8, interval="5m")
    assert out == []
    assert calls["n"] == 1  # stopped after first empty page


# ---------------------------------------------------------------------------
# Finding #1 (CRITICAL): historical anchor — start_date threads to HTTP startTime
# ---------------------------------------------------------------------------

def _make_historical_page(start_ms, count, interval_ms):
    """Build count fake kline rows starting at start_ms with given interval."""
    rows = []
    t = start_ms
    for _ in range(count):
        close = t + interval_ms - 1
        rows.append([t, "100", "102", "99", "101", "10", close, "1010"])
        t += interval_ms
    return rows


def test_historical_anchor_single_page(monkeypatch):
    """When start_date is provided and result fits in one page,
    the HTTP request's startTime must equal the historical start (NOT now-days).

    Specifically: get_intraday_data(..., start_date='2024-01-15', days=1)
    must pass startTime = ms(2024-01-15T00:00:00Z) to Binance, not now-1day.
    """
    from data_provider.binance_fetcher import BinanceFetcher
    import datetime

    # Anchor now to a known value to confirm it is NOT used as the startTime.
    anchor_now_ms = 2_000_000_000_000  # far in the future
    monkeypatch.setattr(BinanceFetcher, "_now_ms", staticmethod(lambda: anchor_now_ms))

    historical_date = "2024-01-15"
    historical_ms = int(
        datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
    )

    interval_ms = 5 * 60 * 1000  # 5m
    captured_params = {}

    def fake_http_get(self, url, params):
        captured_params.update(params)
        # Return a small page (< MAX_LIMIT) to simulate single-page path
        return _make_historical_page(historical_ms, 288, interval_ms)

    monkeypatch.setattr(BinanceFetcher, "_http_get", fake_http_get)

    f = BinanceFetcher()
    # days=1 → limit = 1*288 = 288 ≤ MAX_LIMIT(1000) → single-page path
    # With start_date given, startTime must be anchored to historical_ms
    f.get_intraday_data("BTC/USDT", interval="5m", start_date=historical_date, days=1)

    assert "startTime" in captured_params, (
        "startTime must be passed in HTTP request when start_date is provided"
    )
    assert captured_params["startTime"] == historical_ms, (
        f"startTime must be historical_ms={historical_ms} (2024-01-15), "
        f"got {captured_params.get('startTime')} — anchor must NOT be now-days"
    )


def test_historical_anchor_multi_page(monkeypatch):
    """When start_date is provided and result requires multiple pages,
    the FIRST page's startTime must be anchored to the historical start.
    """
    from data_provider.binance_fetcher import BinanceFetcher
    import datetime

    anchor_now_ms = 2_000_000_000_000
    monkeypatch.setattr(BinanceFetcher, "_now_ms", staticmethod(lambda: anchor_now_ms))

    historical_date = "2024-01-10"
    historical_ms = int(
        datetime.datetime(2024, 1, 10, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000
    )
    interval_ms = 5 * 60 * 1000

    first_start_times = []
    call_count = {"n": 0}

    def fake_http_get(self, url, params):
        start = params["startTime"]
        if call_count["n"] == 0:
            first_start_times.append(start)
        call_count["n"] += 1
        # Return MAX_LIMIT (1000) bars so paging continues until count limit hit
        return _make_historical_page(start, 1000, interval_ms)

    monkeypatch.setattr(BinanceFetcher, "_http_get", fake_http_get)

    f = BinanceFetcher()
    # days=8 → limit = 8*288 = 2304 > 1000 → triggers _page_klines (multi-page)
    f.get_intraday_data("BTC/USDT", interval="5m", start_date=historical_date, days=8)

    assert len(first_start_times) == 1
    assert first_start_times[0] == historical_ms, (
        f"first page startTime must be historical_ms={historical_ms} (2024-01-10), "
        f"got {first_start_times[0]} — must NOT anchor to now-days"
    )


def test_no_start_date_keeps_now_minus_days_anchor(monkeypatch):
    """Without start_date (Task 4 / recent-data path),
    the anchor must remain now - days*86400*1000 (backward compat).
    """
    from data_provider.binance_fetcher import BinanceFetcher

    anchor_now_ms = 2_000_000_000_000
    monkeypatch.setattr(BinanceFetcher, "_now_ms", staticmethod(lambda: anchor_now_ms))

    interval_ms = 5 * 60 * 1000
    first_start_times = []
    call_count = {"n": 0}

    def fake_http_get(self, url, params):
        start = params["startTime"]
        if call_count["n"] == 0:
            first_start_times.append(start)
        call_count["n"] += 1
        return _make_historical_page(start, 1000, interval_ms)

    monkeypatch.setattr(BinanceFetcher, "_http_get", fake_http_get)

    f = BinanceFetcher()
    days = 8
    # start_date=None → classic "now - days" anchor must be unchanged
    f.get_intraday_data("BTC/USDT", interval="5m", days=days)

    expected_start = anchor_now_ms - days * 86400 * 1000
    assert len(first_start_times) == 1
    assert first_start_times[0] == expected_start, (
        f"Without start_date, startTime must be now-days={expected_start}, "
        f"got {first_start_times[0]}"
    )
