"""QuantPick command-line interface.

Heavy modules are imported lazily inside commands so ``quantpick --help`` stays
fast and dependency-light. Commands that need unimplemented phases print a
friendly notice instead of raising.
"""
from __future__ import annotations

import typer

from quantpick import __version__
from quantpick.core.config import AppConfig, load_config
from quantpick.core.logging import setup_logging

app = typer.Typer(
    add_completion=False,
    help="QuantPick - personal A-share / HK stock screener (rule-based + AI).",
)

_CONFIG_OPT = typer.Option("config/config.yaml", "--config", "-c", help="Path to config YAML.")


def _load(config_path: str) -> AppConfig:
    res = load_config(config_path)
    if not res.ok or res.value is None:
        typer.echo(f"config error: {res.message}")
        raise typer.Exit(code=2)
    setup_logging(res.value.log_level)
    return res.value


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"quantpick {__version__}")


@app.command()
def update(config: str = _CONFIG_OPT) -> None:
    """Incrementally update the local market-data cache."""
    _load(config)
    typer.echo("data update is not implemented yet (phase 1).")


@app.command()
def screen(
    strategy: str = typer.Option(..., "--strategy", "-s", help="Strategy name."),
    config: str = _CONFIG_OPT,
) -> None:
    """Run a screening strategy and print ranked candidates."""
    cfg = _load(config)
    from quantpick.strategies.loader import load_strategies

    strategies = load_strategies(cfg.strategies_dir)
    if strategy not in strategies:
        available = ", ".join(sorted(strategies)) or "(none found)"
        typer.echo(f"unknown strategy '{strategy}'. available: {available}")
        raise typer.Exit(code=1)
    strat = strategies[strategy]
    typer.echo(
        f"loaded strategy '{strat.name}': weights={strat.factor_weights}, top_n={strat.top_n}"
    )
    typer.echo("screening run is not implemented yet (phase 1).")


@app.command()
def backtest(
    strategy: str = typer.Option(..., "--strategy", "-s", help="Strategy name."),
    config: str = _CONFIG_OPT,
) -> None:
    """Validate a strategy with forward-return backtesting."""
    _load(config)
    typer.echo("backtest is not implemented yet (phase 2).")


@app.command()
def report(config: str = _CONFIG_OPT) -> None:
    """Generate the daily Markdown report."""
    _load(config)
    typer.echo("report is not implemented yet (phase 2).")


@app.command()
def advise(config: str = _CONFIG_OPT) -> None:
    """Buy signals for screened candidates + sell signals for holdings."""
    _load(config)
    from quantpick.portfolio.holdings import load_holdings

    res = load_holdings()
    n = len(res.value) if res.ok and res.value is not None else 0
    typer.echo(f"holdings loaded: {n} (set config/holdings.yaml for sell signals)")
    typer.echo("signal engine is not implemented yet (signals phase).")


@app.command()
def run(
    strategy: str = typer.Option("ma_trend", "--strategy", "-s", help="Strategy name."),
    config: str = _CONFIG_OPT,
) -> None:
    """One-shot pipeline: update -> screen -> advise -> (AI) -> report."""
    _load(config)
    typer.echo("end-to-end run is not implemented yet (phases 1-3 + signals).")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
