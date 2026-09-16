"""Fixture selection and guarded development-home creation."""

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from importlib.resources import files as _resource_files
except ImportError:
    _resource_files = None

from gideon.security.sel import sel

EXIT_OK, EXIT_IO_ERROR, EXIT_RAIL = 0, 1, 2
_FIXTURES_PKG = "tests_fixtures"


class SeedError(Exception):
    """Rail violation or lookup failure. ``code`` is the intended exit code.

    ``rail`` is a short code-controlled discriminator used in SEL audit
    events (see ``_safe_audit`` in ``seed_cmd``). Keeping it separate from
    the human-readable message keeps the audit stream path-free AND
    SOC-meaningful — ``type(exc).__name__`` would always be ``"SeedError"``
    and the message contains resolved filesystem paths we must NOT leak.
    """

    RAIL_UNSET_HOME = "unset_home"
    RAIL_MAIN_HOME = "main_home"
    RAIL_NON_EMPTY = "non_empty"
    RAIL_SYMLINK_REPLACE = "symlink_replace"
    RAIL_BAD_NAME = "bad_name"
    RAIL_UNKNOWN_FIXTURE = "unknown_fixture"
    RAIL_ROOT_ESCAPE = "root_escape"
    RAIL_RESOLVE_FAILED = "resolve_failed"

    def __init__(
        self,
        message: str,
        *,
        code: int = EXIT_RAIL,
        rail: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.rail = rail


def _fixtures_root() -> Path:
    if _resource_files is None:
        raise SeedError(
            "importlib.resources unavailable — need Python >= 3.9",
            rail=SeedError.RAIL_RESOLVE_FAILED,
        )
    return Path(str(_resource_files("gideon") / _FIXTURES_PKG))


@dataclass(frozen=True)
class FixtureSelection:
    name: str

    def resolve(self):
        name = self.name
        if name in ("", ".", "./") or any(ord(char) < 32 for char in name):
            raise SeedError(
                f"fixture name is empty or refers to the root: {name!r}",
                rail=SeedError.RAIL_BAD_NAME,
            )
        if "/" in name or "\\" in name or ".." in Path(name).parts:
            raise SeedError(
                f"fixture name has path separators or '..': {name!r}",
                rail=SeedError.RAIL_BAD_NAME,
            )
        root = _fixtures_root()
        selected = root.joinpath(name).resolve()
        if not selected.is_dir():
            available = sorted(
                path.name
                for path in root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
            names = ", ".join(available) if available else "(none)"
            raise SeedError(
                f"unknown fixture: {name!r}. Available fixtures: {names}.",
                rail=SeedError.RAIL_UNKNOWN_FIXTURE,
            )
        base = root.resolve()
        if selected == base:
            raise SeedError(
                f"fixture name resolves to fixtures root: {name!r}",
                rail=SeedError.RAIL_BAD_NAME,
            )
        if not selected.is_relative_to(base):
            raise SeedError(
                f"fixture name escapes fixtures root: {name!r}",
                rail=SeedError.RAIL_ROOT_ESCAPE,
            )
        return selected


def _resolve_fixture(name: str) -> Path:
    return FixtureSelection(name).resolve()


def _main_home() -> Path:
    return Path.home().joinpath(".gideon").expanduser().resolve()


def _resolve_target(*, for_main_home_check: bool = False) -> Path:
    raw = os.environ.get("GIDEON_HOME")
    if not raw:
        raise SeedError(
            "$GIDEON_HOME is not set. Point it at a dev directory "
            "(e.g. GIDEON_HOME=~/.gideon-dev gideon gateway --seed empty).",
            rail=SeedError.RAIL_UNSET_HOME,
        )
    target = Path(raw).expanduser()
    if not for_main_home_check:
        return target
    try:
        return target.resolve(strict=False)
    except OSError as error:
        raise SeedError(
            f"cannot resolve $GIDEON_HOME ({raw!r}) for main-home "
            f"safety check — refusing to proceed: {error}",
            rail=SeedError.RAIL_RESOLVE_FAILED,
        ) from error


@dataclass(frozen=True)
class SeedDestination:
    replace: bool

    def prepare(self):
        resolved = _resolve_target(for_main_home_check=True)
        if resolved == _main_home():
            raise SeedError(
                f"refusing to seed main gateway home: {resolved}. "
                "Point $GIDEON_HOME at a separate dev directory (e.g. ~/.gideon-dev).",
                rail=SeedError.RAIL_MAIN_HOME,
            )
        target = _resolve_target()
        if not target.exists():
            return target
        if not any(target.iterdir()):
            if not target.is_symlink():
                target.rmdir()
            return target
        if not self.replace:
            raise SeedError(
                f"$GIDEON_HOME is not empty: {target}. Pass --seed-replace to wipe it and re-seed.",
                rail=SeedError.RAIL_NON_EMPTY,
            )
        if target.is_symlink():
            raise SeedError(
                f"refusing to --seed-replace a symlinked $GIDEON_HOME: {target}. Point it at a real directory.",
                rail=SeedError.RAIL_SYMLINK_REPLACE,
            )
        shutil.rmtree(target)
        return target


def seed(fixture_name: str, *, replace: bool = False) -> None:
    source = _resolve_fixture(fixture_name)
    destination = SeedDestination(replace).prepare()
    shutil.copytree(source, destination)


@dataclass(frozen=True)
class SeedInvocation:
    fixture: str
    replace: bool
    target_set: bool

    def run(self):
        label = f"fixture={self.fixture!r} replace={self.replace}"
        try:
            seed(self.fixture, replace=self.replace)
        except (SeedError, OSError) as error:
            print(f"seed: error: {error}", file=sys.stderr)
            if isinstance(error, SeedError):
                code = error.code
                outcome = "denied" if code == EXIT_RAIL else "error"
                detail = f"rail={error.rail}"
            else:
                code, outcome, detail = (
                    EXIT_IO_ERROR,
                    "error",
                    f"reason={type(error).__name__}",
                )
            _safe_audit(outcome=outcome, resources=f"{label} {detail}")
            return code
        _safe_audit(
            outcome="allowed",
            resources=f"fixture={self.fixture!r} target_set={self.target_set} replace={self.replace}",
        )
        return EXIT_OK


def seed_cmd(args) -> int:
    return SeedInvocation(
        args.seed,
        bool(getattr(args, "seed_replace", False)),
        "GIDEON_HOME" in os.environ,
    ).run()


def _safe_audit(*, outcome: str, resources: str) -> None:
    try:
        sel().log_api_access(
            caller="cli",
            operation="seed",
            outcome=outcome,
            source="cli",
            resources=resources,
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "seed: SEL audit emit failed", exc_info=True
        )
