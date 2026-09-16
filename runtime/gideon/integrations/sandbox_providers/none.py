"""The in-core ``none`` sandbox provider (EXECUTION-ISOLATION EI-1).

``none`` composes the two host primitives Gideon already ships — the OS-level path
sandbox (:func:`gideon.security.sandbox.wrap_argv`) and the post-exec resource ceilings
(:func:`gideon.security.sandbox.create_subprocess_limited`) — and adds NO further isolation. It
is the default backend and is behaviour-identical to the inline logic it replaces in
``acp/transport.py``: the seam exists so a stronger container/VM tier can slot in as an
installable ``sandbox`` app without touching a single spawn site.
"""

from __future__ import annotations

import asyncio
import os

from gideon.integrations.sandbox_providers.base import (
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
)
from gideon.security.sandbox import create_subprocess_limited, wrap_argv

NONE_PROVIDER_NAME = "none"


class _NoneHandle(SandboxHandle):
    """A ``none``-wrapped command: OS sandbox applied to argv, ceilings applied at exec."""

    def __init__(
        self, argv: list[str], profile: str, ceilings, cleanup_path: str | None
    ) -> None:
        self._argv = list(argv)
        self._profile = profile
        self._ceilings = ceilings
        self._cleanup_path = cleanup_path

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    async def exec(self, **kwargs: object) -> asyncio.subprocess.Process:
        return await create_subprocess_limited(
            *self._argv, profile=self._profile, ceilings=self._ceilings, **kwargs
        )

    def cleanup(self) -> None:
        path = self._cleanup_path
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass
        self._cleanup_path = None


class NoneSandboxProvider(SandboxProvider):
    """The default, always-available backend: OS path sandbox + resource ceilings, nothing more."""

    name = NONE_PROVIDER_NAME
    display_name = "No isolation (host)"

    def available(self) -> bool:
        return True

    def wrap(self, spec: SandboxSpec, argv: list[str]) -> _NoneHandle:
        wrapped, cleanup_path = wrap_argv(list(argv), mode=spec.mode)
        return _NoneHandle(wrapped, spec.profile, spec.ceilings, cleanup_path)


def create_provider(config: object | None = None) -> NoneSandboxProvider:
    """Factory mirroring the installable-provider entry-point shape (unused for the builtin)."""
    return NoneSandboxProvider()
