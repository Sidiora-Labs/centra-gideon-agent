"""Installation discovery, release catalog decisions and update process primitives."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import importlib.metadata
import signal
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from gideon.core.versions import is_newer, normalize_version, order_key, parse_version, same_version

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

_ROLLBACK_PHASES = frozenset({"applying", "applied", "failed", "cancelled"})

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


def _install_kind_for_package(package_file: str | Path) -> InstallKind:
    """Classify the code location that contains the running package."""
    package_dir = Path(package_file).resolve().parents[1]
    runtime_dir = package_dir.parent
    if package_dir.name != "gideon" or runtime_dir.name != "runtime":
        return "pip"
    package_source_root = runtime_dir.parent
    return "git" if package_source_root.joinpath(".git").exists() else "pip"


def detect_install_kind() -> InstallKind:
    if getattr(sys, "frozen", False):
        return "desktop"
    selected = (os.environ.get("GIDEON_INSTALL_KIND") or "").strip().lower()
    if selected in _ENV_KINDS:
        return cast(InstallKind, selected)
    # A configured project/workspace can be any checkout. Classify the running
    # package itself so a wheel launched from a repository stays a pip install.
    return _install_kind_for_package(__file__)


def applies_updates_unattended(kind: InstallKind | str) -> bool:
    """Whether Gideon can safely install updates without its owner replacing it."""
    return kind == "git"


def package_root(proj: str) -> str:
    return InstallLayout(proj).package()


def container_instructions(tag: str = "") -> list[str]:
    from gideon.operations.container_host import update_commands

    return update_commands(tag)


def version_tuple(v: str):
    """Compatibility alias for canonical release ordering."""
    return order_key(v)


def moves_to(target: str, current: str, pin: str = "") -> bool:
    if (pin or "").strip():
        return same_version(target, pin) and not same_version(target, current)
    return is_newer(target, current)


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
    version = parse_version(str(release.get("tag") or ""))
    return bool(release.get("prerelease")) or (
        version is not None and version.is_prerelease
    )


@dataclass(frozen=True)
class ReleaseSelection:
    releases: list[dict]
    channel: str
    pin: str = ""

    def choose(self):
        pin = (self.pin or "").strip()
        if pin:
            return next(
                (
                    str(row.get("tag") or "")
                    for row in self.releases
                    if same_version(str(row.get("tag") or ""), pin)
                ),
                "",
            )
        if self.channel == "nightly":
            return ""
        candidates = [
            row
            for row in self.releases
            if parse_version(str(row.get("tag") or "")) is not None
            and (self.channel == "beta" or not _is_prerelease(row))
        ]
        if not candidates:
            return ""
        selected = max(
            candidates,
            key=lambda row: parse_version(str(row["tag"])),
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
            "update_available": moves_to(latest, self.current),
            "commits_behind": self.behind,
            "apply_method": _APPLY_METHOD.get(self.kind, "instructions"),
            "unattended_apply": applies_updates_unattended(self.kind),
            "instructions": (
                container_instructions(latest)
                if self.kind == "container" and moves_to(latest, self.current)
                else []
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
    if detect_install_kind() == "desktop" or any(
        part.endswith(".app") for part in Path(proj).parts
    ):
        raise RuntimeError("Git fetch is unavailable in a desktop bundle")
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
    if detect_install_kind() == "desktop" or any(
        part.endswith(".app") for part in Path(proj).parts
    ):
        return None
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


_active_update = contextvars.ContextVar("active_update", default=None)


async def launch_update_process(*args, **kwargs):
    """Finish child creation before propagating cancellation, then retire its group."""
    from gideon.core.cancellation import terminate_and_reap

    from gideon.operations._installer import installer_env

    kwargs.setdefault("env", installer_env())
    launch = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        child = await asyncio.shield(launch)
        operation = _active_update.get()
        if operation is not None:
            operation.children.append(child)
        return child
    except asyncio.CancelledError:
        child = await launch
        if not await terminate_and_reap(child):
            raise RuntimeError("Update child retirement could not be confirmed")
        raise


class UpdateOperation:
    """Own one update task through child retirement and durable cancellation."""

    def __init__(self, work, progress, project=""):
        try:
            self.metadata = installed_metadata()
        except Exception:
            self.metadata = None
        self.children = []
        self.cancellable = True
        self.stopping = False
        self.project = git_root(project) if project else ""
        self.head = git_commit_for(self.project, "HEAD") if self.project else ""
        self.branch = GitCheckout(self.project).branch() if self.project else ""
        self.started = asyncio.Event()
        self.task = asyncio.create_task(self._run(work, progress))

    async def _run(self, work, progress):
        self.started.set()
        token = _active_update.set(self)
        try:
            await work
            journal = read_update_state()
            if self.cancellable and journal.get("phase") == "failed":
                detail = str(journal.get("error") or "Update failed") + installation_delta(self.metadata)
                if self.head:
                    detail += await asyncio.to_thread(restore_checkout, self.project, self.head, self.branch)
                transition_update("failed", error=detail)
                progress("error", detail)
        except asyncio.CancelledError:
            from gideon.core.cancellation import terminate_and_reap

            for child in self.children:
                if child.returncode is None and not await terminate_and_reap(child):
                    raise RuntimeError("Update process has not stopped")
                if sys.platform.startswith("linux"):
                    for stat in Path("/proc").glob("[0-9]*/stat"):
                        try:
                            fields = stat.read_text().rsplit(")", 1)[1].split()
                            if int(fields[2]) == child.pid and fields[0] != "Z":
                                raise RuntimeError("Update process group has not stopped")
                        except (FileNotFoundError, ProcessLookupError, PermissionError):
                            continue
            # Inspect retirement before publishing any stopped state.
            detail = "Update stopped. Package installation may be incomplete; review or roll back before restarting."
            detail += installation_delta(self.metadata)
            if self.head:
                detail += await asyncio.to_thread(restore_checkout, self.project, self.head, self.branch)
            transition_update("cancelled", error=detail)
            progress("cancelled", detail)
        finally:
            _active_update.reset(token)

    async def cancel(self, timeout=15):
        await self.started.wait()
        if self.task.done():
            self.task.result()
            return "not_running"
        if not self.cancellable:
            return "too_late"
        if not self.stopping:
            self.stopping = True
            if not self.task.cancelling():
                self.task.cancel()
        done, _ = await asyncio.wait({self.task}, timeout=timeout)
        if done:
            # Surface journal/cleanup failures rather than claiming successful retirement.
            self.task.result()
            return "stopped"
        return "stopping"


def installed_metadata():
    """Snapshot installed distribution metadata, without trusting it as code integrity."""
    result = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "")
        if name:
            result[name] = (distribution.version, hashlib.sha256((distribution.read_text("RECORD") or "").encode()).hexdigest())
    return result


def installation_delta(before):
    try:
        after = installed_metadata()
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        if changed:
            return " Changed distribution metadata: " + ", ".join(changed) + ". Package files may be incomplete."
        return " No distribution metadata changes observed; package files may still be incomplete."
    except Exception:
        return " Distribution metadata could not be verified; package files may be incomplete."


def restore_checkout(project, head, branch):
    """Restore only a clean checkout that retained its original branch identity."""
    if not head:
        return ""
    status = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True)
    if status.returncode or status.stdout.strip() or GitCheckout(project).branch() != branch:
        return f" Checkout preserved at {project}; inspect changes and restore {head} manually."
    reset = git_reset_to(project, head)
    if reset.returncode:
        return f" Checkout restoration failed at {project}; restore {head} manually."
    return " Checkout restored."


def run_install_command(argv, *, cwd=None, timeout=400):
    """Own the CLI installer session through Ctrl-C and bounded retirement."""
    from gideon.operations._installer import installer_env

    child = subprocess.Popen(argv, cwd=cwd, env=installer_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            for action in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, action)
                except ProcessLookupError:
                    pass
                try:
                    child.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    continue
                if action == signal.SIGTERM:
                    # Descendants can outlive an already reaped group leader.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                break
            if child.returncode is None:
                raise RuntimeError("Installer retirement could not be confirmed")
        finally:
            signal.signal(signal.SIGINT, previous)
        raise


def cli_argv():
    """Launch this install's CLI, including its frozen executable entrypoint."""
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "gideon"]
