"""Installation discovery, release catalog decisions and update process primitives."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

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

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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
        return selected
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


@dataclass(frozen=True)
class ReleaseCache:
    locate: object

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


async def fetch_latest_release(*, offline: bool = False) -> dict[str, object]:
    cache = read_release_cache()
    if offline:
        return cache
    return await ReleaseRequest(_RELEASES_LATEST_URL, cache).refresh(
        _release_text, write_release_cache
    )


def _release_list(payload):
    return {
        "releases": [_release_view(row) for row in payload if isinstance(row, dict)]
    }


async def fetch_releases(*, offline: bool = False) -> list[dict[str, object]]:
    cache = read_releases_cache()
    if offline:
        return _releases_from_cache(cache)
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


async def resolve_target(channel: str, pin: str = "", *, offline: bool = False) -> str:
    return select_target(await fetch_releases(offline=offline), channel, pin)


def package_channel(channel: str) -> str:
    """The channel a PACKAGE install can actually install.

    There is no nightly wheel — nightly is a git-checkout-only branch-tracking
    channel — so a package install on ``nightly`` rides ``stable`` rather than
    refusing or silently doing nothing.
    """
    return "stable" if channel == "nightly" else channel


@dataclass(frozen=True)
class PackageTarget:
    """What a package upgrade should install, or why it cannot be chosen."""

    spec: str = ""
    version: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def missing_pin_error(pin: str) -> str:
    return (
        f"Version pin {normalize_version(pin)!r} is not a published release — "
        "clear the pin or set it to a released version in Settings > Updates."
    )


async def resolve_package_target(
    channel: str, pin: str = "", *, offline: bool = False
) -> PackageTarget:
    """Pick the release a package install should upgrade to.

    An exact pin that names no published release is REFUSED with an actionable
    message rather than silently upgrading to something else. With no pin an
    unresolvable channel (offline with a cold cache, or no release published
    yet) degrades to the unpinned upgrade the installer already performs — that
    is the offline story, and it is deliberately not an error.
    """
    tag = await resolve_target(package_channel(channel), pin, offline=offline)
    if pin and not tag:
        return PackageTarget(error=missing_pin_error(pin))
    return PackageTarget(spec=upgrade_spec(tag), version=normalize_version(tag))


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


async def build_update_status(
    current: str, *, offline: bool = False
) -> dict[str, object]:
    kind = detect_install_kind()
    release = await fetch_latest_release(offline=offline)
    behind = None
    if kind == "git" and not offline:
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


def git_fetch_tags(proj: str) -> subprocess.CompletedProcess[str]:
    return GitCheckout(proj).run("fetch", "--tags", "origin", timeout=60)


def git_commit_for(proj: str, ref: str) -> str:
    """The commit *ref* names locally, or "" when the ref is unknown here."""
    result = GitCheckout(proj).run(
        "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def git_is_fast_forward(proj: str, ref: str) -> bool:
    """True when HEAD is an ancestor of *ref*, i.e. moving there loses nothing."""
    return (
        GitCheckout(proj).run("merge-base", "--is-ancestor", "HEAD", ref).returncode
        == 0
    )


def git_merge_ff_only(proj: str, ref: str) -> subprocess.CompletedProcess[str]:
    """Advance the checked-out branch to *ref*, or fail — never rewrite history.

    ``--ff-only`` is the whole safety property: it moves the branch pointer when
    the move is a pure fast-forward and refuses otherwise. It replaced a blind
    ``git pull`` (which merges, and pulls whatever the branch's upstream happens
    to be) and a ``git reset --hard`` (which discards commits and edits).
    """
    return GitCheckout(proj).run("merge", "--ff-only", ref, timeout=60)


DIRTY_TREE_REASON = (
    "Working tree has uncommitted changes to tracked files — commit or stash "
    "them and the update applies on the next check."
)

SOURCE_MODE_TAG = "tag"
SOURCE_MODE_BRANCH = "branch"
SOURCE_MODE_PAUSED = "paused"
SOURCE_MODE_NONE = "none"


@dataclass(frozen=True)
class SourcePlan:
    """What a source (git) checkout should move to, or why it must not move.

    ``paused`` is the ONE dirty-tree safeguard for every source update surface:
    the unattended gateway apply, the dashboard apply and ``gideon update`` all
    read this plan instead of running their own ``git status`` gate, so an
    operator sees the same actionable reason and the same paused state wherever
    the refusal happens.
    """

    mode: str
    ref: str = ""
    reason: str = ""
    paths: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.mode in (SOURCE_MODE_TAG, SOURCE_MODE_BRANCH)

    @property
    def paused(self) -> bool:
        return self.mode == SOURCE_MODE_PAUSED


async def plan_source_update(
    proj: str, channel: str, pin: str = "", *, offline: bool = False
) -> SourcePlan:
    """Choose the ref a source install updates to, per release policy.

    Normal source installs ride release TAGS chosen by channel or exact pin, the
    same selection every other install kind uses. ``nightly`` is the only channel
    that tracks a branch, and only when no pin overrides it.
    """
    tracked = git_tracked_changes(proj)
    if tracked:
        return SourcePlan(
            SOURCE_MODE_PAUSED, reason=DIRTY_TREE_REASON, paths=tuple(tracked)
        )
    pin = (pin or "").strip()
    if channel == "nightly" and not pin:
        branch = current_branch(proj)
        if not branch:
            return SourcePlan(
                SOURCE_MODE_NONE,
                reason=(
                    "Nightly tracks the checked-out branch, but this checkout has a "
                    "detached HEAD — check out a branch first."
                ),
            )
        return SourcePlan(SOURCE_MODE_BRANCH, f"origin/{branch}")
    tag = await resolve_target(channel, pin, offline=offline)
    if not tag:
        return SourcePlan(
            SOURCE_MODE_NONE,
            reason=(
                missing_pin_error(pin)
                if pin
                else f"No published release found for the {channel!r} channel."
            ),
        )
    return SourcePlan(SOURCE_MODE_TAG, tag)


def fetch_for_plan(proj: str, plan: SourcePlan) -> subprocess.CompletedProcess[str]:
    """Fetch exactly what *plan* needs: the tag set, or the tracked branch."""
    if plan.mode == SOURCE_MODE_BRANCH:
        return git_fetch(proj, plan.ref.removeprefix("origin/"))
    return git_fetch_tags(proj)


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
