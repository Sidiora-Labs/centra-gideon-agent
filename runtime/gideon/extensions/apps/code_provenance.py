"""Identify in-process app code from the directories its loader admitted."""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_takebacks: dict[str, list[tuple[object | None, Callable[[], None]]]] = {}
_loading_scope: contextvars.ContextVar[tuple[str, object] | None] = (
    contextvars.ContextVar("app_registration_scope", default=None)
)
_roots: dict[Path, str] = {}
_pending: dict[Path, tuple[str, int]] = {}
_package = Path(__file__).resolve().parents[2]
_native = Path(__file__).resolve().parent / "native"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _snapshot() -> tuple[tuple[Path, str], ...]:
    with _lock:
        entries = dict(_roots)
        entries.update({root: app for root, (app, _count) in _pending.items()})
        return tuple(
            sorted(entries.items(), key=lambda item: len(item[0].parts), reverse=True)
        )


@contextmanager
def loading(app: str, directory: Path) -> Iterator[None]:
    root = directory.resolve(strict=True)
    if not app or not root.is_dir():
        raise ImportError("App code requires a named, existing directory")
    if _inside(_package, root) or (
        _inside(root, _package) and not _inside(root, _native)
    ):
        raise ImportError("Core code cannot be registered as an app")
    with _lock:
        current = _roots.get(root)
        pending, count = _pending.get(root, (app, 0))
        if (current is not None and current != app) or pending != app:
            raise ImportError("App code directory already belongs to another app")
        _pending[root] = (app, count + 1)
    scope = object()
    token = _loading_scope.set((app, scope))
    try:
        yield
    except BaseException:
        _withdraw(app, scope=scope)
        raise
    else:
        with _lock:
            _roots[root] = app
    finally:
        _loading_scope.reset(token)
        with _lock:
            pending, count = _pending[root]
            if count == 1:
                del _pending[root]
            else:
                _pending[root] = (pending, count - 1)


def loaded_app(path: str) -> str | None:
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return None
    for root, app in _snapshot():
        if _inside(resolved, root):
            return app
    return None


def owner() -> str | None:
    from types import FrameType

    frame: FrameType | None = sys._getframe(1)
    try:
        while frame is not None:
            name = frame.f_globals.get("__name__")
            module = sys.modules.get(name) if isinstance(name, str) else None
            if module is not None and vars(module) is frame.f_globals:
                filename = getattr(module, "__file__", None)
                if isinstance(filename, str):
                    try:
                        source = Path(filename).resolve(strict=True)
                        executing = Path(frame.f_code.co_filename).resolve(strict=True)
                    except (OSError, ValueError, RuntimeError):
                        pass
                    else:
                        if source == executing:
                            app = loaded_app(str(source))
                            if app is not None:
                                return app
            if frame.f_code.co_name == "<module>":
                return None
            frame = frame.f_back
        return None
    finally:
        del frame


def keep(take_back: Callable[[], None]) -> None:
    """Attach a registration's identity-checked withdrawal to its admitted app code."""
    app = owner()
    if app is None:
        return
    current = _loading_scope.get()
    scope = current[1] if current is not None and current[0] == app else None
    with _lock:
        _takebacks.setdefault(app, []).append((scope, take_back))


def _withdraw(app: str, *, scope: object | None = None) -> None:
    with _lock:
        registered = _takebacks.get(app, [])
        selected = (
            registered
            if scope is None
            else [row for row in registered if row[0] is scope]
        )
        remaining = (
            [] if scope is None else [row for row in registered if row[0] is not scope]
        )
        if remaining:
            _takebacks[app] = remaining
        else:
            _takebacks.pop(app, None)
    failures = []
    for _scope, take_back in reversed(selected):
        try:
            take_back()
        except Exception as exc:
            logger.exception("app %s registration withdrawal failed", app)
            failures.append((_scope, take_back, exc))
    if failures:
        with _lock:
            _takebacks.setdefault(app, []).extend(
                (failed_scope, callback) for failed_scope, callback, _exc in failures
            )
        raise RuntimeError(
            f"App {app!r} retained registrations after withdrawal"
        ) from failures[0][2]


def release(app: str) -> None:
    """Withdraw registrations before releasing their app code provenance."""
    _withdraw(app)
    with _lock:
        for root in [root for root, registered in _roots.items() if registered == app]:
            del _roots[root]
