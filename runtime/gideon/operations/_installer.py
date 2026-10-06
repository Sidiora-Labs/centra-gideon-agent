"""Resolve an installer and target its command at the active interpreter."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import tomllib
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_UV_REJECTS: frozenset[str] = frozenset({"--disable-pip-version-check"})


class NoInstallerError(RuntimeError):
    """The active environment has neither supported installation route."""

    def __init__(self, message: str, *, problem: str = "", fix: str = "") -> None:
        super().__init__(message)
        self.problem = problem
        self.fix = fix


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _have_pip() -> bool:
    try:
        available = importlib.util.find_spec("pip")
    except Exception:
        return False
    return available is not None


def own_installer() -> str:
    """Read the installer recorded by the running distribution."""
    dist = _own_distribution()
    record = (dist.read_text("INSTALLER") or "") if dist is not None else ""
    return "uv" if record.strip() == "uv" else "pip"


def installer_name() -> str:
    return own_installer()


def require_own_installer() -> str:
    tool = own_installer()
    if tool == "uv" and _have_uv():
        return tool
    if tool == "pip" and _have_pip():
        return tool
    if tool == "uv":
        raise NoInstallerError(
            "Nothing was changed: uv made Gideon's environment, but uv is not on its PATH. "
            "Run `gideon update` from a terminal where uv works."
        )
    problem, fix = missing_pip()
    raise NoInstallerError(f"Nothing was changed: {problem}; {fix}, then run `gideon update`.", problem=problem, fix=fix)


def installer_env() -> dict[str, str]:
    """Keep an installer in the environment the running process imports from."""
    return {**os.environ, **installer_cache_env(), "UV_PROJECT_ENVIRONMENT": sys.prefix}


def _own_distribution():
    try:
        return importlib.metadata.distribution("gideon-agent-harness")
    except importlib.metadata.PackageNotFoundError:
        return None


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
            "Install uv (https://docs.astral.sh/uv/), or reinstall Gideon into this "
            "interpreter to restore its declared pip dependency, then retry."
        )


def install_argv(args: list[str]) -> list[str]:
    return InstallerPlan(require_own_installer(), sys.executable).command(args)


def missing_pip() -> tuple[str, str]:
    return (
        f"{sys.executable} has no pip module",
        f"reinstall Gideon into {sys.executable} to restore its declared pip dependency",
    )


def _pip() -> list[str]:
    from gideon.core import python_children

    if not python_children.available():
        raise NoInstallerError(python_children.refusal("install Python packages"))
    if _have_pip():
        return [sys.executable, "-m", "pip"]
    problem, fix = missing_pip()
    raise NoInstallerError(f"{problem}; {fix}", problem=problem, fix=fix)


def prefix_install_argv(args: list[str]) -> list[str]:
    """Resolve app-prefix requirements against this environment with its own pip."""
    return [*_pip(), "install", *args]


def env_install_argv(python: str | Path, args: list[str]) -> list[str]:
    """Run this environment's pip against a pip-less sidecar interpreter."""
    return [*_pip(), "--python", str(python), "install", *args]


def checkout_install_argv(package_root: str | Path) -> list[str]:
    """The argv that installs the source checkout at *package_root* into Gideon's own
    environment once an update has moved it, with the tool that made the environment. It runs in
    *package_root*.

    pip: ``pip install -e .``, editable, the way a checkout runs.

    uv: ``uv sync --locked``, the versions the checkout's lockfile names, the way uv made the
    environment. ``--inexact`` keeps what the environment has beyond them (a package installed by
    hand, an extra nothing here can tell was asked for), so an update adds and upgrades and never
    removes. The extras the environment has are synced with the rest (:func:`_installed_extras`).
    :func:`installer_env` names the running environment uv's project environment.

    Raises:
        NoInstallerError: that tool is not there (:func:`require_own_installer`).
    """
    if require_own_installer() == "uv":
        argv = ["uv", "sync", "--locked", "--inexact", "--python", sys.executable, "--quiet"]
        for extra in _installed_extras(_declared_extras(Path(package_root))):
            argv += ["--extra", extra]
        return argv
    return [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"]


def _declared_extras(package_root: Path) -> set[str]:
    """The extras the checkout at *package_root* declares (``[project.optional-dependencies]``),
    normalized. An extra the running version has and the checkout no longer declares is not one
    ``uv sync`` can be asked for."""
    from packaging.utils import canonicalize_name

    try:
        project = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    table = project.get("project", {}).get("optional-dependencies", {})
    return {canonicalize_name(name) for name in table} if isinstance(table, dict) else set()


def _installed_extras(declared: set[str]) -> list[str]:
    """The extras of the running Gideon that this environment has, among *declared*.

    Nothing records which extras an environment was synced with, so it is read back from what is
    installed. An extra counts when every package it adds on this platform is installed, and an
    extra that names others of Gideon's (``dev`` naming ``test``) counts when they all do.
    One whose packages are there for another reason counts too, which only syncs those packages
    to the versions the lockfile names.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    dist = _own_distribution()
    if dist is None:
        return []
    own = canonicalize_name(dist.metadata["Name"] or "gideon-agent-harness")
    extras = {canonicalize_name(e) for e in dist.metadata.get_all("Provides-Extra") or []}
    needs: dict[str, list[Requirement]] = {}
    for line in dist.requires or []:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        marker = requirement.marker
        if marker is None or marker.evaluate({"extra": ""}):
            continue  # one every install has, not an extra's
        for extra in extras:
            if marker.evaluate({"extra": extra}):
                needs.setdefault(extra, []).append(requirement)

    def installed(extra: str, asking: frozenset[str]) -> bool:
        if extra in asking:
            return True  # an extra naming itself back decides nothing
        wanted = needs.get(extra)
        if not wanted:
            return False
        for requirement in wanted:
            if canonicalize_name(requirement.name) == own:
                named = (canonicalize_name(e) for e in requirement.extras)
                if not all(installed(e, asking | {extra}) for e in named):
                    return False
            elif not _installed(requirement.name):
                return False
        return True

    return sorted(e for e in needs if e in declared and installed(e, frozenset()))


def _installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True



INSTALLER_CACHE_DIRNAME = "installer-cache"


def installer_cache_env() -> dict[str, str]:
    """Keep installer scratch files and npm cache in Gideon's home."""
    from gideon.core.config.loader import config_dir

    root = config_dir() / INSTALLER_CACHE_DIRNAME
    scratch = root / "tmp"
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "PIP_NO_CACHE_DIR": "1", "UV_NO_CACHE": "1",
        "NODE_DISABLE_COMPILE_CACHE": "1",
        "npm_config_cache": str(root / "npm"), "TMPDIR": str(scratch),
    }
