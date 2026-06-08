import data_provider.crypto_new_listings as nl
from src.services.crypto_new_listing_service import CryptoNewListingService
from src.config import Config


class _FakeRepo:
    def __init__(self, prior=None):
        self._prior = prior
        self.saved = None

    def get_base_assets(self, exchange):
        return self._prior

    def save_base_assets(self, exchange, bases):
        self.saved = set(bases)


def _cfg():
    c = Config._load_from_env()
    c.crypto_new_listing_sources = "binance"
    return c


def test_binance_first_run_seeds_no_report(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets", lambda: {"AAA": ("AAAUSDT", "USDT")})
    repo = _FakeRepo(prior=None)
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    out = svc.discover(now_ms=1_000)
    assert out == []
    assert repo.saved == {"AAA"}


def test_binance_reports_only_new_base(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets",
                        lambda: {"AAA": ("AAAUSDT", "USDT"), "NEW": ("NEWUSDT", "USDT")})
    repo = _FakeRepo(prior={"AAA"})
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    out = svc.discover(now_ms=1_000)
    assert [r["base"] for r in out] == ["NEW"]
    assert repo.saved == {"AAA", "NEW"}


def test_binance_fetch_empty_no_crash_no_save(monkeypatch):
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets", lambda: {})
    repo = _FakeRepo(prior={"AAA"})
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    assert svc.discover(now_ms=1_000) == []
    assert repo.saved is None


def test_binance_existing_base_new_pair_not_reported(monkeypatch):
    # AAA already in prior; fetch returns AAA with a different representative pair → no new base
    monkeypatch.setattr(nl, "fetch_binance_spot_base_assets", lambda: {"AAA": ("AAAFDUSD", "FDUSD")})
    repo = _FakeRepo(prior={"AAA"})
    svc = CryptoNewListingService(data_manager=None, repo=repo, config=_cfg())
    assert svc.discover(now_ms=1_000) == []     # AAA 仍是已知 base，新增计价对不报
    assert repo.saved == {"AAA"}
