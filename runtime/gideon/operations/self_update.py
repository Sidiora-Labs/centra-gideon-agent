"""Installation discovery, release catalog decisions and update process primitives."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

logger = logging.getLogger(__name__)

InstallKind = Literal["git", "pip", "container", "desktop"]

INSTALL_KINDS: tuple[InstallKind, ...] = get_args(InstallKind)

_ENV_KINDS: frozenset[str] = frozenset({"container", "desktop"})

_RELEASE_REPOSITORY = os.environ.get("GIDEON_RELEASE_REPOSITORY", "").strip("/")

_RELEASES_LATEST_URL = (
    f"https://api.github.com/repos/{_RELEASE_REPOSITORY}/releases/latest"
    if _RELEASE_REPOSITORY
    else ""
)

_CACHE_FILENAME = "update_check.json"

_HTTP_TIMEOUT_S = 10.0

_APPLY_METHOD: dict[str, str] = {
    "git": "pipeline",
    "pip": "pip_upgrade",
    "container": "instructions",
    "desktop": "desktop_delegate",
}

DEFAULT_BRANCH_FALLBACK = "main"

_RELEASES_LIST_URL = (
    f"https://api.github.com/repos/{_RELEASE_REPOSITORY}/releases?per_page=100"
    if _RELEASE_REPOSITORY
    else ""
)

_LIST_CACHE_FILENAME = "update_releases.json"

_UPDATE_STATE_FILENAME = "update_state.json"

_ROLLBACK_PHASES = frozenset({"applying", "applied", "failed"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

DIRTY_TREE_REASON = (
    "Working tree has uncommitted changes to tracked files — commit or stash "
    "them before rolling back."
)


@dataclass(frozen=True)
class InstallLayout:
    project: str

    def checkout(self):
        if not self.project:
            return ""
        return next(
            (
                str(path)
                for path in _git_dir_candidates(self.project)
                if path.joinpath(".git").exists()
            ),
            "",
        )

    def package(self):
        root = Path(self.project)
        return next(
            (
                str(path)
                for path in (root, root / "Gideon")
                if path.joinpath("pyproject.toml").is_file()
            ),
            self.project,
        )


def project_dir() -> str:
    return os.environ.get("GIDEON_PROJECT_DIR", "") or ""


def _git_dir_candidates(proj: str) -> list[Path]:
    return [Path(proj), Path(proj).parent]


def git_root(proj: str) -> str:
    return InstallLayout(proj).checkout()


def detect_install_kind() -> InstallKind:
    selected = (os.environ.get("GIDEON_INSTALL_KIND") or "").strip().lower()
    if selected in _ENV_KINDS:
        return cast(InstallKind, selected)
    return "git" if git_root(project_dir()) else "pip"


def package_root(proj: str) -> str:
    return InstallLayout(proj).package()


def container_instructions() -> list[str]:
    return [
        f"docker compose -f infrastructure/compose/compose.yaml {action}"
        for action in ("pull", "up -d")
    ]


def normalize_version(v: str) -> str:
    return (v or "").strip().removeprefix("v")


def version_tuple(v: str) -> tuple[int, ...]:
    core = normalize_version(v).partition("+")[0].partition("-")[0]
    try:
        return tuple(map(int, core.split(".")))
    except (ValueError, AttributeError):
        return (0,)


def _cache_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / _CACHE_FILENAME


def _list_cache_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / _LIST_CACHE_FILENAME


def _update_state_path() -> Path:
    from gideon.core.config.loader import config_dir

    return config_dir() / _UPDATE_STATE_FILENAME


@dataclass(frozen=True)
class ReleaseCache:
    locate: Callable[[], Path]

    def read(self):
        try:
            return json.loads(self.locate().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def write(self, value):
        from gideon.core.atomic_write import atomic_write

        try:
            atomic_write(self.locate(), json.dumps(value, indent=2) + "\n", fsync=True)
        except Exception:
            logger.debug("could not persist release cache", exc_info=True)


def read_release_cache() -> dict[str, object]:
    return ReleaseCache(_cache_path).read()


def write_release_cache(data: dict[str, object]) -> None:
    ReleaseCache(_cache_path).write(data)


def read_releases_cache() -> dict[str, object]:
    return ReleaseCache(_list_cache_path).read()


def write_releases_cache(data: dict[str, object]) -> None:
    ReleaseCache(_list_cache_path).write(data)


def read_update_state() -> dict[str, object]:
    """Read the durable journal for the most recent core update."""
    value = ReleaseCache(_update_state_path).read()
    return value if isinstance(value, dict) else {}


def write_update_state(data: dict[str, object]) -> None:
    """Persist the core update journal atomically."""
    from gideon.core.atomic_write import atomic_write

    atomic_write(_update_state_path(), json.dumps(data, indent=2) + "\n", fsync=True)


def _state_version(value: object) -> str:
    return normalize_version(str(value or ""))


def begin_update(
    kind: str,
    current: str,
    target: str,
    *,
    rollback_ref: str = "",
) -> dict[str, object]:
    """Record the recovery point before an in-process update mutates the install."""
    now = time.time()
    state: dict[str, object] = {
        "schema": 1,
        "phase": "applying",
        "kind": kind,
        "from_version": _state_version(current),
        "to_version": _state_version(target),
        "rollback_ref": str(rollback_ref or ""),
        "started_at": now,
        "updated_at": now,
        "error": "",
    }
    write_update_state(state)
    return state


def transition_update(phase: str, *, error: str = "") -> dict[str, object]:
    """Move an existing update journal to a new, documented lifecycle phase."""
    state = read_update_state()
    if not state:
        return {}
    state["phase"] = phase
    state["updated_at"] = time.time()
    state["error"] = str(error or "")
    write_update_state(state)
    return state


def complete_update() -> dict[str, object]:
    return transition_update("applied")


def fail_update(error: str) -> dict[str, object]:
    return transition_update("failed", error=error)


def begin_rollback() -> dict[str, object]:
    return transition_update("rolling_back")


def complete_rollback() -> dict[str, object]:
    return transition_update("rolled_back")


def rollback_snapshot(kind: str) -> dict[str, str]:
    """Return the validated recovery point for *kind*, or an empty mapping."""
    state = read_update_state()
    if state.get("kind") != kind or state.get("phase") not in _ROLLBACK_PHASES:
        return {}
    version = _state_version(state.get("from_version"))
    ref = str(state.get("rollback_ref") or "")
    if kind == "pip" and version:
        return {"version": version, "ref": ""}
    if kind == "git" and ref:
        return {"version": version, "ref": ref}
    return {}


def update_state_view(kind: str) -> dict[str, object]:
    """Stable API fields describing update progress and rollback availability."""
    state = read_update_state()
    snapshot = rollback_snapshot(kind)
    return {
        "update_state": str(state.get("phase") or "idle"),
        "update_from_version": _state_version(state.get("from_version")),
        "update_target": _state_version(state.get("to_version")),
        "update_started_at": state.get("started_at"),
        "update_updated_at": state.get("updated_at"),
        "update_error": str(state.get("error") or ""),
        "rollback_available": bool(snapshot),
        "rollback_version": snapshot.get("version", ""),
    }


def _release_text(item):
    return {
        key: str(item.get(source) or "")
        for key, source in (("tag", "tag_name"), ("name", "name"), ("body", "body"))
    }


def _release_view(item: dict[str, object]) -> dict[str, object]:
    return {**_release_text(item), "prerelease": bool(item.get("prerelease"))}


def _releases_from_cache(cache: dict[str, object]) -> list[dict[str, object]]:
    values = cache.get("releases")
    return (
        [row for row in values if isinstance(row, dict)]
        if isinstance(values, list)
        else []
    )


@dataclass(frozen=True)
class ReleaseRequest:
    url: str
    cache: dict

    async def refresh(self, project, save):
        if not self.url:
            return self.cache
        import aiohttp

        etag = str(self.cache.get("etag") or "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gideon-update-check",
        }
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_S)
            ) as client:
                async with client.get(self.url, headers=headers) as response:
                    if response.status != 200:
                        return self.cache
                    value = {
                        **project(await response.json()),
                        "etag": response.headers.get("ETag", "") or etag,
                        "checked_at": time.time(),
                    }
                    save(value)
                    return value
        except Exception:
            logger.debug("release request failed; keeping cached view", exc_info=True)
            return self.cache


async def fetch_latest_release() -> dict[str, object]:
    return await ReleaseRequest(_RELEASES_LATEST_URL, read_release_cache()).refresh(
        _release_text, write_release_cache
    )


def _release_list(payload):
    return {
        "releases": [_release_view(row) for row in payload if isinstance(row, dict)]
    }


async def fetch_releases() -> list[dict[str, object]]:
    cache = read_releases_cache()
    _releases_from_cache(cache)
    refreshed = await ReleaseRequest(_RELEASES_LIST_URL, cache).refresh(
        _release_list, write_releases_cache
    )
    return _releases_from_cache(refreshed)


def _is_prerelease(release: dict[str, object]) -> bool:
    return bool(release.get("prerelease")) or "-" in normalize_version(
        str(release.get("tag") or "")
    )


@dataclass(frozen=True)
class ReleaseSelection:
    releases: list[dict]
    channel: str
    pin: str = ""

    def choose(self):
        pin = (self.pin or "").strip()
        if pin:
            desired = normalize_version(pin)
            return next(
                (
                    str(row.get("tag") or "")
                    for row in self.releases
                    if row.get("tag") and normalize_version(str(row["tag"])) == desired
                ),
                "",
            )
        if self.channel == "nightly":
            return ""
        candidates = [
            row
            for row in self.releases
            if row.get("tag") and (self.channel == "beta" or not _is_prerelease(row))
        ]
        if not candidates:
            return ""
        selected = max(
            candidates,
            key=lambda row: (version_tuple(str(row["tag"])), not _is_prerelease(row)),
        )
        return str(selected["tag"])


def select_target(
    releases: list[dict[str, object]], channel: str, pin: str = ""
) -> str:
    return ReleaseSelection(releases, channel, pin).choose()


async def resolve_target(channel: str, pin: str = "") -> str:
    return select_target(await fetch_releases(), channel, pin)


@dataclass(frozen=True)
class UpdateStatus:
    kind: str
    current: str
    release: dict
    behind: int | None

    def wire(self):
        latest = normalize_version(str(self.release.get("tag") or ""))
        return {
            "kind": self.kind,
            "current": normalize_version(self.current),
            "latest": latest,
            "update_available": bool(latest)
            and version_tuple(latest) > version_tuple(self.current),
            "commits_behind": self.behind,
            "apply_method": _APPLY_METHOD.get(self.kind, "instructions"),
            "instructions": (
                container_instructions() if self.kind == "container" else []
            ),
            "release_name": str(self.release.get("name") or ""),
            "release_notes": str(self.release.get("body") or ""),
        }


async def build_update_status(current: str) -> dict[str, object]:
    kind = detect_install_kind()
    release = await fetch_latest_release()
    behind = None
    if kind == "git":
        project = project_dir()
        if project:
            try:
                behind = await commits_behind_upstream(project)
            except Exception:
                pass
    return UpdateStatus(kind, current, release, behind).wire()


def installer_error_summary(stderr: str, *, limit: int = 200) -> str:
    lines = [
        line.strip(" \t│╰─▶×") for line in _ANSI_RE.sub("", stderr or "").splitlines()
    ]
    lines = [line for line in lines if line.strip()]
    offset = next(
        (index for index, line in enumerate(lines) if line.lower().startswith("error")),
        0,
    )
    return " ".join(lines[offset:])[:limit].strip()


def upgrade_spec(latest: str) -> str:
    version = normalize_version(latest)
    return "gideon-agent-harness" + (f"=={version}" if version else "")


def _run_git(
    args: list[str], *, cwd: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        code, error = 124, f"`{' '.join(command)}` timed out after {timeout:g}s"
    except OSError as failure:
        code, error = 127, f"cannot run git: {failure}"
    return subprocess.CompletedProcess(command, code, "", error)


@dataclass(frozen=True)
class GitCheckout:
    path: str

    def run(self, *args, timeout=10):
        return _run_git(list(args), cwd=self.path, timeout=timeout)

    def branch(self):
        result = self.run("rev-parse", "--abbrev-ref", "HEAD")
        name = (result.stdout or "").strip() if result.returncode == 0 else ""
        return "" if name == "HEAD" else name

    def default_branch(self):
        current = current_branch(self.path)
        if current:
            return current
        symbolic = self.run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        ref = (symbolic.stdout or "").strip() if symbolic.returncode == 0 else ""
        if ref:
            return ref.removeprefix("origin/")
        remote = self.run("remote", "show", "origin", timeout=30)
        if remote.returncode == 0:
            for line in (remote.stdout or "").splitlines():
                heading, separator, name = line.strip().partition(":")
                if heading == "HEAD branch" and separator:
                    name = name.strip()
                    if name and not name.startswith("("):
                        return name
        return DEFAULT_BRANCH_FALLBACK

    def tracked(self):
        result = self.run("status", "--porcelain")
        if result.returncode:
            return []
        return [
            line
            for line in (result.stdout or "").splitlines()
            if line.strip() and not line.startswith("??")
        ]


def current_branch(proj: str) -> str:
    return GitCheckout(proj).branch()


def resolve_default_branch(proj: str) -> str:
    return GitCheckout(proj).default_branch()


def git_fetch(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    return GitCheckout(proj).run("fetch", "origin", branch, timeout=60)


def git_is_up_to_date(proj: str, branch: str) -> bool:
    return (
        GitCheckout(proj).run("diff", "HEAD", f"origin/{branch}", "--quiet").returncode
        == 0
    )


def git_tracked_changes(proj: str) -> list[str]:
    return GitCheckout(proj).tracked()


def git_commit_for(proj: str, ref: str) -> str:
    """Return the commit named by *ref*, or an empty string when unavailable."""
    result = GitCheckout(proj).run(
        "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def git_reset_hard(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    return GitCheckout(proj).run("reset", "--hard", f"origin/{branch}")


def git_reset_to(proj: str, ref: str) -> subprocess.CompletedProcess[str]:
    """Reset a clean checkout to an already validated rollback commit."""
    return GitCheckout(proj).run("reset", "--hard", ref)


async def _git_output(project, arguments, timeout, capture):
    import asyncio

    from gideon.core.cancellation import run_with_timeout

    process = await asyncio.create_subprocess_exec(
        "git",
        *arguments,
        cwd=project,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    output, _ = await run_with_timeout(process, timeout)
    return process.returncode, output


async def commits_behind_upstream(proj: str) -> int | None:
    try:
        await _git_output(proj, ["fetch", "--quiet"], 15, False)
    except Exception:
        pass
    try:
        code, output = await _git_output(
            proj, ["rev-list", "--count", "HEAD..@{u}"], 10, True
        )
        return None if code else int(output.decode(errors="replace").strip())
    except Exception:
        return None
