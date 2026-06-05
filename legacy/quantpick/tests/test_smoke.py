from __future__ import annotations


def test_version() -> None:
    import quantpick

    assert quantpick.__version__


def test_core_result() -> None:
    from quantpick.core import ErrorCode, Result

    ok = Result.success(42)
    assert ok.ok and ok.unwrap() == 42

    bad = Result.fail(ErrorCode.EMPTY_DATA, "no data")
    assert not bad.ok and bad.code == ErrorCode.EMPTY_DATA


def test_factor_registry_populated() -> None:
    from quantpick.factors import all_factors

    names = set(all_factors())
    assert {"momentum_20d", "value_pe", "quality_roe"} <= names


def test_cli_app_exists() -> None:
    from quantpick.cli import app, main

    assert app is not None and callable(main)
