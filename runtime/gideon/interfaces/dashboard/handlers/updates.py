"""Update check/apply, log level, ring buffer, and SSE stream handlers."""

import asyncio
import collections
import json
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from gideon import __version__ as _local_version
from gideon import shutdown_event
from gideon.core.atomic_write import atomic_write
from gideon.core.cancellation import run_with_timeout
from gideon.core.config import loader as config_loader
from gideon.core.config.loader import AppConfig
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations import self_update
from gideon.operations.frontend import build_frontend_async


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


logger = logging.getLogger(__name__)


_update_info: dict[str, object] = {"available": False, "changes": "", "checked": False}
_UPDATE_CHECK_INTERVAL = 43200
_last_update_check: float = 0.0


def get_update_info() -> dict[str, object]:
    """Return a copy of the cached update-check state."""
    return dict(_update_info)


async def api_update_check(request: web.Request) -> web.Response:
    """GET /api/update/check — kind-aware update check (contract C2).

    Returns the tag-driven cross-kind status ({kind, current, latest,
    update_available, commits_behind, apply_method, instructions}) merged with
    the legacy git changelog-diff fields (available/changes) for backward
    compatibility with the existing panel. The git kind still runs the
    commits-behind probe; every kind gets the release-tag comparison. In git
    developer update mode a non-zero ``commits_behind`` also sets ``available``,
    so the check agrees with what the apply would actually do.
    """
    await _do_update_check()
    cfg = AppConfig.load()
    try:
        status = await self_update.build_update_status(_local_version)
    except Exception:
        logger.debug("build_update_status failed; returning legacy view", exc_info=True)
        status = {}
    merged: dict[str, object] = {**_update_info, **status}
    if status.get("latest"):
        merged["available"] = bool(status.get("update_available"))
    if cfg.dashboard.update_dev_mode and status.get("kind") == "git":
        _behind = status.get("commits_behind")
        if isinstance(_behind, int) and _behind > 0:
            merged["available"] = True
    merged["auto_update"] = cfg.auto_update
    merged["update_dev_mode"] = cfg.dashboard.update_dev_mode
    merged["version"] = _local_version
    kind = str(merged.get("kind") or self_update.detect_install_kind())
    merged.update(self_update.update_state_view(kind))
    return web.json_response(merged)


def _redact_log_text(text: str) -> str:
    """Redact credentials and exfiltration URLs from log text before streaming/buffering."""
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    text, _ = redact_credentials(text)
    text, _ = redact_exfiltration_urls(text)
    return text


async def _do_update_check() -> None:
    """Run git fetch and compare HEAD with remote."""
    global _last_update_check

    proj = os.environ.get("GIDEON_PROJECT_DIR", "")
    if not proj:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "fetch",
            "--quiet",
            cwd=proj,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _, fetch_err = await run_with_timeout(proc, 30)
        except asyncio.TimeoutError:
            logger.warning("git fetch timed out")
            return
        if proc.returncode != 0:
            logger.warning(
                "git fetch failed (rc=%s): %s",
                proc.returncode,
                (fetch_err or b"").decode(errors="replace").strip(),
            )
            return

        local = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=proj,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            local_out, _ = await run_with_timeout(local, 10)
        except asyncio.TimeoutError:
            return
        remote = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "@{u}",
            cwd=proj,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            remote_out, _ = await run_with_timeout(remote, 10)
        except asyncio.TimeoutError:
            return

        local_sha = local_out.decode(errors="replace").strip()
        remote_sha = remote_out.decode(errors="replace").strip()

        available = False
        remote_version = ""
        target_sha = remote_sha if local_sha != remote_sha else local_sha
        if local_sha and remote_sha:
            show = await asyncio.create_subprocess_exec(
                "git",
                "show",
                f"{target_sha}:./pyproject.toml",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                show_out, _ = await run_with_timeout(show, 10)
            except asyncio.TimeoutError:
                return
            m = re.search(
                r'^version\s*=\s*"(.+?)"',
                show_out.decode(errors="replace"),
                re.MULTILINE,
            )
            if m:
                remote_version = m.group(1)
            available = (
                self_update.version_tuple(remote_version)
                > self_update.version_tuple(_local_version)
                if remote_version
                else False
            )

        changes = ""
        if available:
            diff_base = f"v{_local_version}" if local_sha == remote_sha else local_sha
            diff = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                f"{diff_base}..{target_sha}",
                "--",
                "CHANGELOG.md",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                diff_out, _ = await run_with_timeout(diff, 10)
            except asyncio.TimeoutError:
                return
            lines: list[str] = []
            for line in diff_out.decode(errors="replace").splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    lines.append(line[1:])
            changes = "\n".join(lines).strip()

        _update_info["available"] = available
        _update_info["changes"] = changes
        _update_info["latest"] = remote_version
        _update_info["checked"] = True
        _last_update_check = time.time()
    except Exception:
        logger.debug("Update check failed", exc_info=True)


async def api_update_auto(request: web.Request) -> web.Response:
    """POST /api/update/auto — toggle auto-update on/off."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        return web.json_response({"error": "enabled must be a boolean"}, status=400)
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    data["auto_update"] = enabled
    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
    return web.json_response({"ok": True, "auto_update": enabled})


async def api_update_dev_mode(request: web.Request) -> web.Response:
    """POST /api/update/dev-mode — toggle git dev-mode (track commits vs tags).

    Persists ``dashboard.update_dev_mode`` (plan 34 T4.5). Only meaningful for a
    git checkout — for pip/container/desktop the updater always rides releases —
    but the flag is stored uniformly (the frontend only surfaces the control for
    the git kind).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    enabled = body.get("enabled", False)
    if not isinstance(enabled, bool):
        return web.json_response({"error": "enabled must be a boolean"}, status=400)
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    dash = data.get("dashboard")
    if not isinstance(dash, dict):
        dash = {}
    dash["update_dev_mode"] = enabled
    data["dashboard"] = dash
    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
    return web.json_response({"ok": True, "update_dev_mode": enabled})


async def api_changelog(request: web.Request) -> web.Response:
    """GET /api/changelog — read full CHANGELOG.md from project."""
    proj = os.environ.get("GIDEON_PROJECT_DIR", "")
    if not proj:
        return web.json_response({"content": ""})
    path = Path(proj) / "CHANGELOG.md"
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        content = ""
    return web.json_response({"content": content})


_apply_in_flight = False


async def _apply_pip_update(request: web.Request, state: ConsoleState) -> web.Response:
    """Upgrade a pip/uv/pipx install in place, then graceful re-exec (T4.3).

    Runs ``<installer> install -U gideon==<tag>`` (pinned to the latest
    release tag when known; unpinned ``-U`` otherwise) targeting the SAME
    interpreter/prefix the gateway runs from — mirrors the git path's editable
    install step. The installer is RESOLVED (uv or pip), not assumed: a uv-created
    venv ships no pip module (issue #51). No web build: the wheel already carries
    the SPA. The 409 concurrent-apply guard is shared with the git path.
    """
    global _apply_in_flight

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"}, status=409
        )
    _apply_in_flight = True
    try:
        status = await self_update.build_update_status(_local_version)
    except Exception:
        status = {}
    spec = self_update.upgrade_spec(str(status.get("latest") or ""))
    try:
        self_update.begin_update("pip", _local_version, str(status.get("latest") or ""))
    except Exception:
        _apply_in_flight = False
        logger.exception("Could not persist the update recovery point")
        return web.json_response(
            {"error": "Could not save the recovery point; update was not started"},
            status=500,
        )
    state.push_refresh("updating")
    auth_mode = _live_auth_mode(request)

    async def _apply() -> None:
        global _apply_in_flight
        try:
            from gideon.operations._installer import NoInstallerError, install_argv

            try:
                argv = install_argv(["-U", spec, "--quiet"])
            except NoInstallerError as exc:
                logger.error("self-update: %s", exc)
                self_update.fail_update(str(exc))
                state.push_update_progress("error", str(exc))
                return

            state.push_update_progress("installing", f"Upgrading {spec}…")
            pip_up = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                _, pip_err = await run_with_timeout(pip_up, 400)
            except asyncio.TimeoutError:
                self_update.fail_update("pip upgrade timed out")
                state.push_update_progress("error", "pip upgrade timed out")
                return
            if pip_up.returncode != 0:
                detail = (pip_err or b"").decode(errors="replace").strip()
                logger.error(
                    "self-update failed (rc=%d): %s", pip_up.returncode, detail[:500]
                )
                summary = self_update.installer_error_summary(detail)
                self_update.fail_update(summary or "Upgrade failed")
                state.push_update_progress(
                    "error",
                    f"Upgrade failed: {summary}" if summary else "Upgrade failed",
                )
                return
            self_update.complete_update()
            state.push_update_progress("restarting", "Restarting server…")
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception:
            logger.exception("pip self-update failed")
            self_update.fail_update("Update failed — check logs")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    task = asyncio.create_task(_apply())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "updating", "kind": "pip"})


async def _requested_update_action(request: web.Request) -> str:
    if not request.can_read_body:
        return "apply"
    try:
        body = await request.json()
    except Exception:
        return "apply"
    return str(body.get("action") or "apply") if isinstance(body, dict) else "apply"


async def _run_rollback(request: web.Request, state: ConsoleState) -> web.Response:
    """Restore the recovery point recorded before the last git or pip update."""
    global _apply_in_flight

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"}, status=409
        )
    kind = self_update.detect_install_kind()
    if kind not in ("git", "pip"):
        return web.json_response(
            {"error": f"Rollback is not managed in-app for {kind} installs"},
            status=409,
        )
    snapshot = self_update.rollback_snapshot(kind)
    if not snapshot:
        return web.json_response(
            {"error": "No previous core update is available to roll back"}, status=409
        )
    _apply_in_flight = True

    proj = self_update.project_dir() if kind == "git" else ""
    if kind == "git":
        if not proj:
            _apply_in_flight = False
            return web.json_response(
                {"error": "GIDEON_PROJECT_DIR not set"}, status=400
            )
        tracked = await asyncio.to_thread(self_update.git_tracked_changes, proj)
        if tracked:
            _apply_in_flight = False
            return web.json_response(
                {"error": self_update.DIRTY_TREE_REASON}, status=409
            )
        ref = snapshot["ref"]
        resolved = await asyncio.to_thread(self_update.git_commit_for, proj, ref)
        if not resolved:
            _apply_in_flight = False
            return web.json_response(
                {"error": "The saved rollback commit is no longer available"},
                status=409,
            )

    try:
        self_update.begin_rollback()
    except Exception:
        _apply_in_flight = False
        logger.exception("Could not persist the rollback transition")
        return web.json_response(
            {"error": "Could not save rollback state; rollback was not started"},
            status=500,
        )
    state.push_refresh("updating")
    auth_mode = _live_auth_mode(request)

    async def _rollback() -> None:
        global _apply_in_flight
        try:
            if kind == "pip":
                from gideon.operations._installer import NoInstallerError, install_argv

                spec = self_update.upgrade_spec(snapshot["version"])
                try:
                    argv = install_argv(["-U", spec, "--quiet"])
                except NoInstallerError as exc:
                    raise RuntimeError(str(exc)) from exc
                state.push_update_progress(
                    "rolling_back", f"Reinstalling {snapshot['version']}…"
                )
                install = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    _, install_err = await run_with_timeout(install, 400)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("Rollback install timed out") from exc
                if install.returncode:
                    detail = self_update.installer_error_summary(
                        (install_err or b"").decode(errors="replace")
                    )
                    raise RuntimeError(detail or "Rollback install failed")
            else:
                state.push_update_progress(
                    "rolling_back", "Restoring the previous source revision…"
                )
                reset = await asyncio.to_thread(
                    self_update.git_reset_to, proj, snapshot["ref"]
                )
                if reset.returncode:
                    raise RuntimeError(reset.stderr.strip() or "git rollback failed")
                pkg_root = self_update.package_root(proj)
                install = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    ".",
                    "--quiet",
                    cwd=pkg_root,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    _, install_err = await run_with_timeout(install, 400)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("Rollback install timed out") from exc
                if install.returncode:
                    detail = self_update.installer_error_summary(
                        (install_err or b"").decode(errors="replace")
                    )
                    raise RuntimeError(detail or "Rollback install failed")
                state.push_update_progress("building", "Building frontend…")
                await build_frontend_async(
                    pkg_root, push_progress=state.push_update_progress
                )

            self_update.complete_rollback()
            state.push_update_progress("restarting", "Rollback complete — restarting…")
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception as exc:
            logger.exception("Core update rollback failed")
            self_update.fail_update(str(exc))
            state.push_update_progress("error", f"Rollback failed: {exc}")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    task = asyncio.create_task(_rollback())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "rolling_back", "kind": kind})


async def api_update_apply(request: web.Request) -> web.Response:
    """POST /api/update — apply an update, or roll back the last one.

    ``{"action": "rollback"}`` restores the durable recovery point. The default
    apply pipeline is ``git pull`` → ``pip install -e .`` (same interpreter)
    → frontend rebuild (``npm ci && npm run build`` in ``web/``) → graceful
    re-exec. Progress is broadcast as ``update_progress`` WS events with steps
    ``pulling`` → ``installing`` → ``building`` → ``restarting``
    (→ ``error``/``failed`` on failure).

    Graceful degradation: when there is NOTHING to pull (no upstream
    configured, or the upstream has zero new commits) the pipeline
    short-circuits straight to the ``restarting`` step — the user asked for
    "Update & Restart", and a restart is still meaningful (applies committed
    local changes). The dirty-tree gate only guards a REAL pull (pulling onto
    a dirty tree is dangerous); if nothing will be pulled, dirtiness doesn't
    matter, so the upstream probe runs BEFORE the dirty check.
    """
    global _apply_in_flight
    state: ConsoleState = request.app["state"]

    action = await _requested_update_action(request)
    if action == "rollback":
        return await _run_rollback(request, state)
    if action != "apply":
        return web.json_response(
            {"error": f"Unknown update action: {action}"}, status=400
        )

    kind = self_update.detect_install_kind()

    if kind in ("container", "desktop"):
        try:
            status = await self_update.build_update_status(_local_version)
        except Exception:
            status = {"kind": kind, "instructions": [], "apply_method": ""}
        return web.json_response(
            {
                "ok": True,
                "status": "instructions",
                "kind": kind,
                "apply_method": status.get("apply_method", ""),
                "instructions": status.get("instructions", []),
                "detail": (
                    "This is a container install — update by pulling the new "
                    "image and recreating."
                    if kind == "container"
                    else "This is a desktop install — install the new version from the "
                    "Gideon releases page, then reopen the app."
                ),
            }
        )

    if kind == "pip":
        return await _apply_pip_update(request, state)

    proj = os.environ.get("GIDEON_PROJECT_DIR", "")
    if not proj:
        return web.json_response({"error": "GIDEON_PROJECT_DIR not set"}, status=400)

    _dev_mode = AppConfig.load().dashboard.update_dev_mode
    _on_latest_tag = False
    if not _dev_mode:
        try:
            _cached_tag = self_update.normalize_version(
                str(self_update.read_release_cache().get("tag") or "")
            )
            if _cached_tag and self_update.version_tuple(
                _cached_tag
            ) <= self_update.version_tuple(_local_version):
                _on_latest_tag = True
        except Exception:
            _on_latest_tag = False
    logger.debug(
        "git update apply: update_dev_mode=%s on_latest_tag=%s",
        _dev_mode,
        _on_latest_tag,
    )

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"},
            status=409,
        )
    _apply_in_flight = True

    state.push_refresh("updating")

    behind = await self_update.commits_behind_upstream(proj)
    if behind is None or behind == 0 or _on_latest_tag:
        if _on_latest_tag and behind:
            note = (
                "On the latest release — restarting… "
                "(enable Developer update mode to track commits)"
            )
        elif behind is None:
            note = "No upstream configured — restarting…"
        else:
            note = "Already up to date — restarting…"
        logger.info("Update apply: nothing to pull (%s) — restarting only", note)
        _auth_mode = _live_auth_mode(request)

        async def _restart_only() -> None:
            global _apply_in_flight
            try:
                state.push_update_progress("restarting", note)
                await _graceful_reexec(state, auth_mode=_auth_mode)
            except Exception:
                logger.exception("Restart (nothing-to-pull update) failed")
                state.push_update_progress("error", "Restart failed — check logs")
            finally:
                _apply_in_flight = False

        task = asyncio.create_task(_restart_only())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        return web.json_response({"ok": True, "status": "restarting", "detail": note})

    dirty = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        cwd=proj,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        dirty_out, _ = await run_with_timeout(dirty, 10)
    except asyncio.TimeoutError:
        _apply_in_flight = False
        return web.json_response(
            {"error": "Timed out checking working tree status"},
            status=500,
        )
    if dirty_out and dirty_out.strip():
        logger.warning("Update skipped: working tree has uncommitted changes")
        _apply_in_flight = False
        return web.json_response(
            {"error": "Working tree has uncommitted changes — commit or stash first"},
            status=409,
        )

    rollback_ref = await asyncio.to_thread(self_update.git_commit_for, proj, "HEAD")
    if not rollback_ref:
        _apply_in_flight = False
        return web.json_response(
            {"error": "Could not save the current revision for rollback"}, status=500
        )
    try:
        update_status = await self_update.build_update_status(_local_version)
    except Exception:
        update_status = {}
    try:
        self_update.begin_update(
            "git",
            _local_version,
            str(update_status.get("latest") or ""),
            rollback_ref=rollback_ref,
        )
    except Exception:
        _apply_in_flight = False
        logger.exception("Could not persist the update recovery point")
        return web.json_response(
            {"error": "Could not save the recovery point; update was not started"},
            status=500,
        )

    async def _apply() -> None:
        global _apply_in_flight
        try:
            state.push_update_progress("pulling", "Pulling latest changes…")
            pull = await asyncio.create_subprocess_exec(
                "git",
                "pull",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                await run_with_timeout(pull, 60)
            except asyncio.TimeoutError:
                self_update.fail_update("git pull timed out")
                state.push_update_progress("error", "git pull timed out")
                return
            if pull.returncode != 0:
                self_update.fail_update("git pull failed")
                state.push_update_progress("error", "git pull failed")
                return

            pkg_root = self_update.package_root(proj)
            state.push_update_progress("installing", "Installing package…")
            pip_install = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-e",
                ".",
                "--quiet",
                cwd=pkg_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                _, pip_err = await run_with_timeout(pip_install, 400)
            except asyncio.TimeoutError:
                self_update.fail_update("pip install timed out")
                state.push_update_progress("error", "pip install timed out")
                return
            if pip_install.returncode != 0:
                logger.error(
                    "Update: pip install failed (rc=%d): %s",
                    pip_install.returncode,
                    (pip_err or b"").decode(errors="replace")[:500],
                )
                self_update.fail_update("pip install failed")
                state.push_update_progress("error", "pip install failed")
                return

            state.push_update_progress("building", "Building frontend…")
            await build_frontend_async(
                pkg_root, push_progress=state.push_update_progress
            )

            state.push_update_progress("restarting", "Restarting server…")
            logger.info(
                "Update complete — saving history and cleaning up before restart"
            )
            self_update.complete_update()
            await _graceful_reexec(state, auth_mode=_live_auth_mode(request))
        except Exception:
            logger.exception("Update failed")
            self_update.fail_update("Update failed — check logs")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    task = asyncio.create_task(_apply())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "updating"})


def _live_auth_mode(request: web.Request) -> str:
    """The running gateway's resolved auth mode (e.g. 'none' / 'local_token'), read
    from ``app['auth_cfg']``, for preserving across a re-exec (#46). Empty string if
    unavailable — the restart then inherits the env as-is (prior behavior)."""
    try:
        auth_cfg = request.app.get("auth_cfg")
        mode = getattr(auth_cfg, "mode", None)
        return str(getattr(mode, "value", mode) or "")
    except Exception:
        return ""


async def _graceful_reexec(state: ConsoleState, *, auth_mode: str = "") -> None:
    """Save history, close sessions, drain frames, then exec a fresh gateway
    in-place. Shared by the update-apply restart and the standalone restart
    endpoint so both use the identical proven sequence. ``os.execv`` replaces
    this process image (same PID) — the kernel hands the listen socket to the
    new image after it binds, so there is no window where nothing is running.
    Uses ``-m gideon`` (not ``sys.argv[0]``) because a build-artifact
    clean may have removed the original ``__main__`` path.

    Preserves the resolved AUTH MODE across the re-exec (#46): the gateway reads
    ``GIDEON_AUTH_MODE`` from the env at boot, but the original launcher's env
    may not survive (e.g. the parent shell that exported ``=none`` exits, the
    process gets reparented to PID 1, and a plain ``os.execv`` that relied on that
    var being in ``os.environ`` would come back token-required). That's a SURPRISING
    security-posture flip on a Restart. So snapshot the LIVE mode from the running
    app's ``auth_cfg`` and pass it explicitly via ``os.execve`` — a Restart re-applies
    code without ever changing whether auth is on/off."""
    exe = sys.executable
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        state.push_update_progress(
            "error", "Cannot restart: invalid Python executable path"
        )
        return
    from gideon.interfaces.dashboard.chat import save_all_sessions_to_history

    child_env = dict(os.environ)
    if auth_mode:
        child_env["GIDEON_AUTH_MODE"] = str(auth_mode)

    try:
        save_all_sessions_to_history(state)
    except Exception:
        logger.debug("History save before restart failed", exc_info=True)
    try:
        await state.sessions.close_all()
    except Exception:
        logger.debug("Session cleanup before restart failed", exc_info=True)
    sys.stdout.flush()
    sys.stderr.flush()
    await asyncio.sleep(0.5)
    os.execve(exe, [exe, "-m", "gideon"] + sys.argv[1:], child_env)


def _active_work_snapshot(state: ConsoleState) -> dict[str, int]:
    """Count in-flight work a restart would interrupt, for the confirm gate:
    running (not-done) background subagents + live chat sessions."""
    running_agents = 0
    subs = getattr(state, "subagents", None)
    if subs is not None:
        try:
            running_agents = sum(1 for a in subs.all_agents if not a.done)
        except Exception:
            running_agents = 0
    try:
        sessions = len(state.sessions._sessions)
    except Exception:
        sessions = 0
    return {"running_agents": running_agents, "sessions": sessions}


async def api_restart(request: web.Request) -> web.Response:
    """POST /api/system/restart — bounce the gateway to apply committed backend
    changes WITHOUT a git pull (the update-free counterpart of ``/api/update``).

    GET-style pre-flight: ``?probe=1`` returns the active-work snapshot (running
    agents + sessions) so the UI can warn before confirming, without restarting.
    A real POST kicks off the graceful re-exec in the background and returns
    immediately (the connection drops as the process restarts)."""
    state: ConsoleState = request.app["state"]

    if request.query.get("probe"):
        return web.json_response({"ok": True, **_active_work_snapshot(state)})

    logger.info("Manual gateway restart requested via /api/system/restart")
    state.push_update_progress("restarting", "Restarting gateway…")
    _auth_mode = _live_auth_mode(request)

    async def _restart() -> None:
        try:
            await _graceful_reexec(state, auth_mode=_auth_mode)
        except Exception:
            logger.exception("Manual restart failed")
            state.push_update_progress("error", "Restart failed — check logs")

    task = asyncio.create_task(_restart())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "restarting"})


async def api_update_cancel(request: web.Request) -> web.Response:
    """POST /api/update/cancel — dismiss a stuck/failed update overlay."""
    state: ConsoleState = request.app["state"]
    state.clear_update_progress()
    state.push_update_progress("failed", "Update cancelled by user")
    await asyncio.sleep(0.2)
    state.clear_update_progress()
    return web.json_response({"ok": True})


async def api_update_simulate(request: web.Request) -> web.Response:
    """POST /api/update/simulate — walk through update steps with delays.

    For local testing only. Cycles through each progress step with a
    configurable delay (default 2s per step).
    """
    state: ConsoleState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        body = {}

    if body.get("reject"):
        msg = body.get(
            "reject_message",
            "Working tree has uncommitted changes — commit or stash first",
        )
        return web.json_response({"error": msg}, status=409)

    delay = body.get("delay", 2)
    fail_at = body.get("fail_at", "")

    async def _sim() -> None:
        steps = [
            ("pulling", "Pulling latest changes…"),
            ("installing", "Installing package…"),
            ("building", "Building frontend…"),
            ("restarting", "Restarting server…"),
        ]
        for step, detail in steps:
            if fail_at and step == fail_at:
                state.push_update_progress("failed", f"Simulated failure at {step}")
                return
            state.push_update_progress(step, detail)
            await asyncio.sleep(delay)
        state.push_update_progress("done", "Update complete")
        state.clear_update_progress()

    task = asyncio.create_task(_sim())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "simulating"})


_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def apply_log_level(level_name: str) -> bool:
    """Set the backend log level LIVE: every logger root we own plus their
    RotatingFileHandler(s). Returns False for an unrecognized level so a caller
    can decide whether that is an error.

    Shared by ``POST /api/logs/level`` and the config PATCH path so a write to
    ``agent.log_level`` from EITHER Settings surface takes effect immediately
    rather than only at the next restart. The handler pass is load-bearing: a
    boot-time RotatingFileHandler level would otherwise keep filtering
    gateway.log at the old verbosity even after the logger level moved.
    """
    name = (level_name or "").upper()
    if name not in _LOG_LEVELS:
        return False
    from gideon.extensions.apps.catalog import installed_logger_roots

    for _lname in ("gideon", *installed_logger_roots()):
        _lg = logging.getLogger(_lname)
        _lg.setLevel(_LOG_LEVELS[name])
        for _h in _lg.handlers:
            if isinstance(_h, RotatingFileHandler):
                _h.setLevel(_LOG_LEVELS[name])
    return True


async def api_log_level(request: web.Request) -> web.Response:
    """POST /api/logs/level — change the backend logger level at runtime.

    Also persists the new level to config so it survives restarts.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_level = body.get("level")
    if not isinstance(raw_level, str):
        return web.json_response({"error": "level must be a string"}, status=400)
    level_name = raw_level.upper()
    if level_name not in _LOG_LEVELS:
        return web.json_response({"error": f"invalid level: {level_name}"}, status=400)
    apply_log_level(level_name)
    logger.info("Log level changed to %s via dashboard", level_name)

    persisted = False
    try:
        cfg = AppConfig.load()
        cfg.agent.log_level = level_name
        cfg.save()
        persisted = True
    except Exception:
        logger.warning("Failed to persist log level to config", exc_info=True)

    return web.json_response({"ok": True, "level": level_name, "persisted": persisted})


async def api_log_level_get(request: web.Request) -> web.Response:
    """GET /api/logs/level — current backend logger level."""
    root = logging.getLogger("gideon")
    return web.json_response({"level": logging.getLevelName(root.level)})


class _QueueLogHandler(logging.Handler):
    """Logging handler that enqueues formatted log entries for SSE delivery."""

    def __init__(self, queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._queue: asyncio.Queue[str] = queue  # type: ignore[type-arg]

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = _redact_log_text(self.format(record))
            data = json.dumps({"level": record.levelname, "msg": msg})
            self._queue.put_nowait(data)
        except Exception:
            pass


_LOG_RING_SIZE = 1000
_log_ring: collections.deque[str] = collections.deque(maxlen=_LOG_RING_SIZE)
_log_ring_handler_installed = False
_log_ring_handler: "_RingLogHandler | None" = None


async def _safe_ws_send(
    ws: web.WebSocketResponse, msg: str, state: ConsoleState
) -> None:
    """Send to WS, removing dead subscribers on failure."""
    try:
        await ws.send_str(msg)
    except Exception:
        state._ws_log_subscribers.discard(ws)


class _RingLogHandler(logging.Handler):
    """Always-on handler that keeps the last N log entries in a ring buffer.

    Also pushes log events to WebSocket log subscribers.
    """

    def __init__(
        self,
        ring: collections.deque[str],
        max_size: int = _LOG_RING_SIZE,
    ) -> None:
        super().__init__()
        self._ring = ring
        self._max = max_size
        self._state: ConsoleState | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_state(self, state: ConsoleState) -> None:
        """Attach ConsoleState for WS log broadcasting."""
        self._state = state
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = _redact_log_text(self.format(record))
            data = json.dumps({"level": record.levelname, "msg": msg})
            self._ring.append(data)
            if self._state and self._loop and self._state._ws_log_subscribers:
                ws_msg = json.dumps(
                    {"type": "log", "data": {"level": record.levelname, "msg": msg}}
                )
                for ws in list(self._state._ws_log_subscribers):
                    try:
                        self._loop.call_soon_threadsafe(
                            self._loop.create_task,
                            _safe_ws_send(ws, ws_msg, self._state),
                        )
                    except RuntimeError:
                        pass
        except Exception:
            pass


def install_log_ring_handler() -> _RingLogHandler | None:
    """Install the persistent ring buffer handler (call once at startup)."""
    global _log_ring_handler_installed, _log_ring_handler  # noqa: PLW0603
    if _log_ring_handler_installed:
        return _log_ring_handler
    _log_ring_handler_installed = True
    handler = _RingLogHandler(_log_ring, _LOG_RING_SIZE)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("gideon").addHandler(handler)
    _log_ring_handler = handler
    return handler


async def api_logs(request: web.Request) -> web.StreamResponse:
    """GET /api/logs — SSE stream of live log entries.

    Query params:
      - ``lines``: max ring-buffer entries to replay on connect (default 200, max 1000).

    On connect, replays the last *lines* log entries from the ring buffer
    so the client sees history even if the Logs page wasn't open.
    """
    try:
        lines_cap = min(max(int(request.query.get("lines", "200")), 1), _LOG_RING_SIZE)
    except (TypeError, ValueError):
        lines_cap = 200
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    try:
        await resp.prepare(request)
    except (ConnectionResetError, ClientConnectionResetError):
        return resp

    try:
        await resp.write(b": connected\n\n")
    except (ConnectionResetError, ClientConnectionResetError):
        return resp

    ring_snapshot = list(_log_ring)
    for data in ring_snapshot[-lines_cap:]:
        try:
            await resp.write(f"data: {data}\n\n".encode())
        except (ConnectionResetError, ClientConnectionResetError):
            return resp

    log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
    handler = _QueueLogHandler(log_queue)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("gideon")
    root.addHandler(handler)
    try:
        while not shutdown_event.is_set():
            while not log_queue.empty():
                try:
                    data = log_queue.get_nowait()
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.QueueEmpty:
                    break

            try:
                data = await asyncio.wait_for(log_queue.get(), timeout=30)
                await resp.write(f"data: {data}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        root.removeHandler(handler)
    return resp
