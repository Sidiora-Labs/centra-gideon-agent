"""Executable discovery and durable adapter installation for ACP launch plans."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from glob import glob
from pathlib import Path

__all__ = [
    "resolve_acp_cli",
    "node_argv_for_script",
    "is_npx_fallback",
    "resolve_node_ge",
    "provision_acp_adapter",
]
logger = logging.getLogger(__name__)
_MIN_NODE_MAJOR = 20
_MANAGER_LAYOUTS = (
    ".nvm/versions/node/*/bin",
    ".local/share/mise/installs/node/*/bin",
    ".local/share/rtx/installs/node/*/bin",
    ".asdf/installs/nodejs/*/bin",
    ".volta/bin",
    ".fnm/node-versions/*/installation/bin",
    ".npm-global/bin",
)


def node_argv_for_script(path: str) -> list[str]:
    launch = [path]
    if path.endswith(".js"):
        launch.insert(0, shutil.which("node") or "node")
    return launch


def _managed_bin_dir() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir().joinpath("acp-adapters")


def _node_manager_bin_globs() -> list[str]:
    root = Path(os.path.expanduser("~"))
    directories = [str(root / relative) for relative in _MANAGER_LAYOUTS]
    directories.extend(("/usr/local/bin", "/opt/homebrew/bin"))
    try:
        directories.append(str(_managed_bin_dir() / "node_modules" / ".bin"))
    except Exception:
        pass
    configured = os.environ.get("GIDEON_EXTRA_BIN_PATHS", "").split(os.pathsep)
    directories.extend(item.strip() for item in configured if item.strip())
    return directories


node_manager_bin_globs = _node_manager_bin_globs


def _npm_root_global_bin() -> str | None:
    npm = shutil.which("npm")
    if npm is None:
        return None
    try:
        result = subprocess.run(
            [npm, "root", "-g"], capture_output=True, text=True, timeout=5
        )
        root = (result.stdout or "").strip()
        directory = Path(root).parent / ".bin" if root else None
        return str(directory) if directory is not None and directory.is_dir() else None
    except Exception:
        return None


@dataclass(frozen=True)
class _ExecutableSearch:
    directories: tuple[str, ...]

    def find(self, name: str, *, newest_first: bool = False) -> Iterator[str]:
        for directory in self.directories:
            matches = glob(str(Path(directory) / name))
            for match in sorted(matches, reverse=newest_first):
                if os.access(match, os.X_OK) and not os.path.isdir(match):
                    yield match

    @classmethod
    def managers(cls, *, include_npm: bool = False) -> _ExecutableSearch:
        directories = _node_manager_bin_globs()
        npm_directory = _npm_root_global_bin() if include_npm else None
        if npm_directory:
            directories = [*directories, npm_directory]
        return cls(tuple(directories))


def resolve_acp_cli(
    *,
    env_var: str,
    bin_names: list[str],
    npm_pkg: str | None = None,
    subcommand: list[str] | None = None,
) -> list[str] | None:
    override = os.environ.get(env_var, "").split()
    if override:
        return node_argv_for_script(override[0]) if len(override) == 1 else override
    suffix = list(subcommand or ())
    found = next((path for name in bin_names if (path := shutil.which(name))), None)
    if found is None:
        search = _ExecutableSearch.managers(include_npm=True)
        found = next((path for name in bin_names for path in search.find(name)), None)
    if found is not None:
        return [*node_argv_for_script(found), *suffix]
    if npm_pkg:
        return [shutil.which("npx") or "npx", "-y", npm_pkg, *suffix]
    return None


def is_npx_fallback(argv: list[str] | None) -> bool:
    return bool(argv and Path(argv[0]).name.lower() in {"npx", "npx.cmd"})


def resolve_node_ge(min_major: int = _MIN_NODE_MAJOR) -> str | None:
    def candidates() -> Iterator[str]:
        installed = shutil.which("node")
        if installed:
            yield installed
        yield from _ExecutableSearch.managers().find("node", newest_first=True)

    for executable in candidates():
        try:
            probe = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=5
            )
            major = (probe.stdout or "").strip().lstrip("v").partition(".")[0]
            if major.isdigit() and int(major) >= min_major:
                return executable
        except Exception:
            continue
    return None


@dataclass
class _AdapterInstall:
    package: str
    names: list[str]
    prefix: Path
    version: str = ""
    integrity: str = ""

    def existing(self) -> str | None:
        directory = self.prefix / "node_modules" / ".bin"
        for name in self.names:
            executable = directory / name
            if executable.exists() and os.access(executable, os.X_OK):
                return str(executable)
        return None

    def execute(self) -> bool:
        node = resolve_node_ge()
        if node is None:
            logger.warning(
                "ACP adapter %s requires Node >= %d", self.package, _MIN_NODE_MAJOR
            )
            return False
        path = os.pathsep.join((str(Path(node).parent), os.environ.get("PATH", "")))
        npm = shutil.which("npm", path=path)
        if npm is None:
            logger.warning("ACP adapter %s: npm unavailable for %s", self.package, node)
            return False
        environment = dict(os.environ, PATH=path)
        specifier = (
            "@".join((self.package, self.version)) if self.version else self.package
        )
        command = [
            npm,
            "install",
            "--prefix",
            str(self.prefix),
            "--no-fund",
            "--no-audit",
            specifier,
        ]
        try:
            self.prefix.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=180, env=environment
            )
        except Exception:
            logger.warning(
                "ACP adapter %s installation failed", self.package, exc_info=True
            )
            return False
        if result.returncode:
            logger.warning(
                "ACP adapter %s install exited %d: %s",
                self.package,
                result.returncode,
                (result.stderr or "")[-400:],
            )
            return False
        return True

    def record(self) -> None:
        try:
            from gideon.engine.agents.runners import AdapterPin, record_provenance

            pin = None
            if self.version and self.integrity:
                pin = AdapterPin(
                    npm_pkg=self.package, version=self.version, integrity=self.integrity
                )
            if not record_provenance(self.package, pin=pin):
                logger.warning(
                    "ACP adapter %s installed without verified provenance", self.package
                )
        except Exception:
            logger.warning(
                "ACP adapter %s provenance unavailable", self.package, exc_info=True
            )


def provision_acp_adapter(
    npm_pkg: str,
    bin_names: list[str],
    *,
    pin_version: str = "",
    expected_integrity: str = "",
) -> str | None:
    job = _AdapterInstall(
        npm_pkg, list(bin_names), _managed_bin_dir(), pin_version, expected_integrity
    )
    existing = job.existing()
    if existing is not None:
        return existing
    if os.environ.get("GIDEON_ACP_NO_PROVISION") == "1":
        return None
    if not job.execute():
        return None
    job.record()
    result = job.existing()
    if result is None:
        logger.warning(
            "ACP adapter %s installed without an executable under %s",
            npm_pkg,
            job.prefix,
        )
    return result
