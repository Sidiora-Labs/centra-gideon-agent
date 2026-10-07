"""Request-local home for dashboard session and audit persistence."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_home: ContextVar[Path | None] = ContextVar("dashboard_auth_home", default=None)


def bound_home() -> Path | None:
    return _home.get()


@contextmanager
def bind_home(home: Path) -> Iterator[None]:
    token = _home.set(home)
    try:
        yield
    finally:
        _home.reset(token)
