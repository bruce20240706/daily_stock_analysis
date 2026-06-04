"""Error codes and a lightweight Result type for boundary error handling.

Convention (see CLAUDE.md): at data-fetch / batch boundaries, return a
``Result`` carrying an :class:`ErrorCode` instead of raising, so a single
failing symbol never aborts a whole batch. Pure internal logic may still raise.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class ErrorCode(IntEnum):
    """Stable numeric error codes, grouped by subsystem."""

    OK = 0

    # data layer (1xxx)
    DATA_SOURCE_UNAVAILABLE = 1001
    SYMBOL_NOT_FOUND = 1002
    EMPTY_DATA = 1003
    RATE_LIMITED = 1004
    CACHE_MISS = 1005

    # config (2xxx)
    CONFIG_INVALID = 2001
    CONFIG_NOT_FOUND = 2002

    # strategy / factor (3xxx)
    STRATEGY_NOT_FOUND = 3001
    FACTOR_NOT_FOUND = 3002
    INVALID_FACTOR_WEIGHT = 3003

    # ai layer (4xxx)
    AI_PROVIDER_ERROR = 4001
    AI_NOT_CONFIGURED = 4002

    # generic (9xxx)
    UNKNOWN = 9999


@dataclass(slots=True)
class Result(Generic[T]):
    """Carries either a value (on success) or an error code plus message."""

    code: ErrorCode = ErrorCode.OK
    value: T | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code == ErrorCode.OK

    def unwrap(self) -> T:
        """Return the value, or raise if this Result is an error.

        Use only where a failure truly is exceptional; prefer checking ``ok``.
        """
        if not self.ok or self.value is None:
            raise RuntimeError(f"unwrap on error result: {self.code.name}: {self.message}")
        return self.value

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        return cls(ErrorCode.OK, value, "")

    @classmethod
    def fail(cls, code: ErrorCode, message: str = "") -> "Result[T]":
        return cls(code, None, message)
