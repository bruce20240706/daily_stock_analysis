from src.core.trading_calendar import compute_effective_region


def test_crypto_always_eligible_when_selected():
    # crypto 24/7：只要被选中且在 open set 中即返回 crypto
    assert compute_effective_region("crypto", {"crypto"}) == "crypto"
    assert compute_effective_region("crypto", {"cn", "crypto"}) == "crypto"


def test_crypto_not_open_returns_empty():
    assert compute_effective_region("crypto", {"cn"}) == ""


def test_both_still_excludes_crypto():
    assert compute_effective_region("both", {"cn", "hk", "us", "crypto"}) == "cn,hk,us"
