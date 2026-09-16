"""Resolve an installer and target its command at the active interpreter."""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_UV_REJECTS: frozenset[str] = frozenset({"--disable-pip-version-check"})


class NoInstallerError(RuntimeError):
    """The active environment has neither supported installation route."""


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _have_pip() -> bool:
    try:
        available = importlib.util.find_spec("pip")
    except Exception:
        return False
    return available is not None


def installer_name() -> str:
    for name, probe in (("uv", _have_uv), ("pip", _have_pip)):
        if probe():
            return name
    return ""


@dataclass(frozen=True)
class InstallerPlan:
    family: str
    interpreter: str

    def command(self, arguments):
        if self.family == "uv":
            prefix = ["uv", "pip", "install", "--python", self.interpreter]
            return prefix + [item for item in arguments if item not in _UV_REJECTS]
        if self.family == "pip":
            return [self.interpreter, "-m", "pip", "install", *arguments]
        raise NoInstallerError(
            "No package installer is available for this environment: "
            f"{self.interpreter} has no `pip` module and `uv` is not on PATH. "
            "Install uv (https://docs.astral.sh/uv/) or add pip to the environment "
            "(`python -m ensurepip --upgrade`), then retry."
        )


def install_argv(args: list[str]) -> list[str]:
    return InstallerPlan(installer_name(), sys.executable).command(args)
