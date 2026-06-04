"""Load named strategies from a directory of YAML files."""
from __future__ import annotations

from pathlib import Path

import yaml

from quantpick.core.errors import ErrorCode, Result
from quantpick.strategies.base import Strategy


def load_strategy_file(path: str | Path) -> Result[Strategy]:
    """Load and validate a single strategy YAML file."""
    p = Path(path)
    if not p.exists():
        return Result.fail(ErrorCode.STRATEGY_NOT_FOUND, f"no such strategy file: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return Result.success(Strategy.model_validate(raw))
    except (yaml.YAMLError, ValueError) as exc:
        return Result.fail(ErrorCode.CONFIG_INVALID, f"{p}: {exc}")


def load_strategies(directory: str | Path = "config/strategies") -> dict[str, Strategy]:
    """Load every ``*.yaml`` strategy in a directory, keyed by strategy name.

    Invalid files are skipped (a real run would log them); this keeps one bad
    file from breaking the whole set.
    """
    out: dict[str, Strategy] = {}
    d = Path(directory)
    if not d.exists():
        return out
    for f in sorted(d.glob("*.yaml")):
        res = load_strategy_file(f)
        if res.ok and res.value is not None:
            out[res.value.name] = res.value
    return out
