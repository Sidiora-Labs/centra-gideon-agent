"""Install-source resolution — local path or git URL → a local directory.

``install``/``update`` (A1/A2) operate on a local source directory. The REST API
(A4) accepts two source kinds:

* **local path** — a directory already on disk (dev installs, bundled fixtures).
* **git URL** — ``https://…``, ``git@…``, or a ``.git`` URL — shallow-cloned into a
  temp dir the caller is responsible for cleaning up.

This module turns either into a directory + a derived ``origin`` for the scanner
trust tier (``local`` for a path, ``external`` for a remote clone). The clone is
bounded (``--depth 1`` + timeout) and never runs hooks — that's the lifecycle's
job, behind the scanner gate.
"""

from __future__ import annotations

import logging
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from gideon.extensions.apps.manager import APP_MANIFEST_FILENAME
from gideon.security.net.policy import EgressPolicy

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120
_MULTI_APP_PREVIEW = 5


class SourceError(Exception):
    """The install source could not be resolved (bad path / clone failed)."""

    code = "app_source_invalid"


class SourceRefused(SourceError):
    """A registry listing names a destination that cannot be fetched."""

    code = "app_listing_refused"


@dataclass
class ResolvedSource:
    path: Path
    origin: str
    cleanup: bool
    _cleanup_root: Path | None = (
        None  # when set, rmtree this instead of path (subdir installs)
    )

    @property
    def cleanup_path(self) -> Path:
        """The directory to remove when cleanup=True (the clone root)."""
        return self._cleanup_root or self.path


def _looks_like_git_url(source: str) -> bool:
    s = source.strip()
    return s.startswith(
        ("http://", "https://", "git://", "ssh://", "git@")
    ) or s.endswith(".git")


def git_pointer(source: str) -> tuple[str, str] | None:
    """Return the repository and app subdirectory named by a git install source.

    Store pointers use ``repository#subdirectory``; plain repository URLs name
    the root app. Local filesystem paths have no git pointer.
    """
    value = str(source).strip()
    base = value
    subdirectory = ""
    if "#" in value and _looks_like_git_url(value.split("#", 1)[0]):
        base, subdirectory = value.rsplit("#", 1)
    if not _looks_like_git_url(base):
        return None
    return base, subdirectory.strip("/")


def _subdir_app_names(root: Path) -> list[str]:
    """The immediate subdirectories of a clone that hold an ``app.json`` — i.e. the
    installable apps of a multi-app repository (the published apps repo's shape)."""
    out: list[str] = []
    for child in sorted(root.iterdir()):
        try:
            child_mode = child.lstat().st_mode
        except OSError:
            continue
        if not stat.S_ISDIR(child_mode) or child.name.startswith("."):
            continue
        manifest = child / APP_MANIFEST_FILENAME
        try:
            if stat.S_ISREG(manifest.lstat().st_mode):
                out.append(child.name)
        except OSError:
            pass
    return out


def _multi_app_hint(url: str, apps: list[str]) -> str:
    """The install error for a multi-app repo pasted WITHOUT a ``#app`` suffix.

    Renders verbatim in the Store, so it names the count, the exact source string the
    user has to type instead, and a bounded preview rather than a 45-name dump."""
    preview = ", ".join(apps[:_MULTI_APP_PREVIEW])
    if len(apps) > _MULTI_APP_PREVIEW:
        preview += f", and {len(apps) - _MULTI_APP_PREVIEW} more"
    return (
        f"{url} holds {len(apps)} apps, not one — install a single app by appending "
        f"#app to the URL, e.g. {url}#{apps[0]}. Available: {preview}."
    )


def resolve(source: str, *, registry: str | None = None) -> ResolvedSource:
    """Resolve an install source string to a local directory.

    A local directory path resolves in place (no cleanup). A git URL is
    shallow-cloned into a temp dir (caller cleans up). Supports the
    ``url#subdirectory`` format for installing a specific app from a
    multi-app git repo — a multi-app repo given WITHOUT that suffix raises
    with the ``#app`` form and the app names it found. Raises
    :class:`SourceError` on a missing path or a failed clone."""
    if not isinstance(source, str) or (
        registry is not None and not isinstance(registry, str)
    ):
        raise SourceError("source and registry must be strings")
    s = source.strip()
    if not s:
        raise SourceError("empty install source")

    pointer = git_pointer(s)
    base, subdir_value = pointer if pointer is not None else (s, "")
    subdir = subdir_value or None

    policy = _listing_fetch_policy(s, base, registry)
    if _looks_like_git_url(base):
        resolved = _clone_git(base, policy=policy)
        if subdir:
            target = _confined_subdirectory(resolved.path, subdir)
            if target is None:
                _rmtree(resolved.path)
                raise SourceError(f"subdirectory '{subdir}' not found in cloned repo")
            resolved = ResolvedSource(
                path=target,
                origin="external",
                cleanup=True,
                _cleanup_root=resolved.path,
            )
        elif not (resolved.path / APP_MANIFEST_FILENAME).is_file():
            apps = _subdir_app_names(resolved.path)
            if apps:
                _rmtree(resolved.path)
                raise SourceError(_multi_app_hint(base, apps))
        return resolved

    path = Path(s).expanduser()
    if not path.is_dir():
        raise SourceError(f"source is not a directory: {source}")
    return ResolvedSource(path=path, origin="local", cleanup=False)


def _listing_fetch_policy(
    source: str, base: str, registry: str | None
) -> EgressPolicy | None:
    from gideon.extensions.apps import catalog
    from gideon.security.net.git import preflight

    listed_by = (registry or "").strip() or catalog.listing_source_for(source)
    if listed_by is None:
        return None
    reason = catalog.listing_repo_refusal(base)
    if reason:
        raise SourceRefused(reason)
    policy = catalog.listing_policy(listed_by)
    decision = preflight(base, policy)
    if decision.allow:
        return policy
    if decision.category == "unresolvable":
        raise SourceError(
            catalog.listing_unreachable(decision.host, "it does not resolve")
        )
    raise SourceRefused(
        catalog.listing_address_refusal(decision)
        or "This app's download address is not permitted."
    )


def _clone_git(url: str, *, policy: EgressPolicy | None = None) -> ResolvedSource:
    from gideon.security.net.git import (
        GitEgressRefused,
        GitHostUnreachable,
        run_git_guarded,
    )

    tmp = Path(tempfile.mkdtemp(prefix="gideon-app-clone-"))
    clone = ["clone", "--depth", "1", "--", url, str(tmp)]
    try:
        if policy is None:
            proc = subprocess.run(
                ["git", *clone], capture_output=True, text=True, timeout=_CLONE_TIMEOUT
            )
        else:
            proc = run_git_guarded(clone, policy=policy, timeout=_CLONE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        _rmtree(tmp)
        raise SourceError(f"git clone timed out after {_CLONE_TIMEOUT}s") from exc
    except GitEgressRefused as exc:
        _rmtree(tmp)
        from gideon.extensions.apps.catalog import listing_fetch_refusal

        raise SourceRefused(listing_fetch_refusal(url, exc.refusal)) from exc
    except GitHostUnreachable as exc:
        _rmtree(tmp)
        from gideon.extensions.apps.catalog import listing_unreachable

        raise SourceError(listing_unreachable(exc.host, exc.reason)) from exc
    except OSError as exc:
        _rmtree(tmp)
        raise SourceError("git could not start to clone the app source") from exc
    if proc.returncode != 0:
        _rmtree(tmp)
        if policy is not None:
            raise SourceError("The app repository could not be downloaded.")
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise SourceError(f"git clone failed: {tail}")
    _rmtree(tmp / ".git")
    return ResolvedSource(path=tmp, origin="external", cleanup=True)


def _confined_subdirectory(root: Path, subdir: str) -> Path | None:
    """Resolve a URL-selected app folder without following any clone symlink."""
    parts = Path(subdir).parts
    if (
        not parts
        or Path(subdir).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    current = root
    try:
        for part in parts:
            current = current / part
            if not stat.S_ISDIR(current.lstat().st_mode):
                return None
    except OSError:
        return None
    return current


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
