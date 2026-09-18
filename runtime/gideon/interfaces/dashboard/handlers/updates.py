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


def update_policy():
    """The ``updates`` config block, or its defaults when config cannot be read.

    Every release-check decision reads this — an unreadable config must not turn
    the kill switch into "check anyway", so the fallback is the shipped default
    block, not a hardcoded "enabled".
    """
    from gideon.core.config.loader import UpdatesConfig

    try:
        return AppConfig.load().updates
    except Exception:
        logger.debug("could not read updates config; using defaults", exc_info=True)
        return UpdatesConfig()


def update_check_interval_seconds(policy=None) -> float:
    """The configured gap between scheduled release checks, in seconds."""
    hours = getattr(policy or update_policy(), "check_interval_hours", 0)
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 0
    return float(hours * 3600) if hours > 0 else float(_UPDATE_CHECK_INTERVAL)


def update_check_due(policy=None) -> bool:
    """Whether a SCHEDULED release check may run right now.

    False while ``updates.check_enabled`` is off — the kill switch is consulted
    here, before any caller reaches code that could open a connection.
    """
    policy = policy or update_policy()
    if not getattr(policy, "check_enabled", True):
        return False
    return time.time() - _last_update_check >= update_check_interval_seconds(policy)


async def api_update_check(request: web.Request) -> web.Response:
    """GET /api/update/check — kind-aware update check (contract C2).

    Returns the tag-driven cross-kind status ({kind, current, latest,
    update_available, commits_behind, apply_method, instructions}) merged with
    the legacy git changelog-diff fields (available/changes) for backward
    compatibility with the existing panel, plus the live release policy
    (channel/pin/auto mode/check switch and interval).

    With ``updates.check_enabled`` off this answers entirely from the cached
    release view: no request is made, not even for an explicitly requested
    check. The switch is an egress kill switch, not a scheduling preference.
    """
    policy = update_policy()
    await _do_update_check(force=True)
    offline = not policy.check_enabled
    try:
        status = await self_update.build_update_status(_local_version, offline=offline)
    except Exception:
        logger.debug("build_update_status failed; returning legacy view", exc_info=True)
        status = {}
    merged: dict[str, object] = {**_update_info, **status}
    if status.get("latest"):
        merged["available"] = bool(status.get("update_available"))
    if policy.channel == "nightly" and status.get("kind") == "git":
        _behind = status.get("commits_behind")
        if isinstance(_behind, int) and _behind > 0:
            merged["available"] = True
    merged["auto_update"] = policy.auto == "staged"
    merged["update_mode"] = policy.auto
    merged["channel"] = policy.channel
    merged["pin"] = policy.pin
    merged["check_enabled"] = policy.check_enabled
    merged["check_interval_hours"] = policy.check_interval_hours
    merged["version"] = _local_version
    return web.json_response(merged)


def _redact_log_text(text: str) -> str:
    """Redact credentials and exfiltration URLs from log text before streaming/buffering."""
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    text, _ = redact_credentials(text)
    text, _ = redact_exfiltration_urls(text)
    return text


async def _do_update_check(*, force: bool = False) -> None:
    """Run git fetch and compare HEAD with remote.

    Two gates, both BEFORE the first subprocess/connection: ``check_enabled``
    (the egress kill switch — off means this function opens nothing, ever) and
    ``check_interval_hours`` (the scheduled cadence). ``force=True`` is a
    user-initiated check: it skips the interval, never the kill switch.
    """
    global _last_update_check

    policy = update_policy()
    if not policy.check_enabled:
        logger.debug("release check skipped: updates.check_enabled is off")
        return
    if not force and not update_check_due(policy):
        return
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


AUTO_MODES = ("off", "staged")


async def api_update_auto(request: web.Request) -> web.Response:
    """POST /api/update/auto — set the automatic-update mode.

    Writes ``updates.auto``: ``off`` (notify only, the default) or ``staged``
    (apply at the next safe point, on the resolved channel/pin release). The
    older ``{"enabled": bool}`` body is still accepted and maps to the same two
    modes, so a client that has not been updated keeps working; once
    ``updates.auto`` exists in the file the legacy ``auto_update`` bool is inert.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "mode" in body:
        mode = body.get("mode")
        if mode not in AUTO_MODES:
            return web.json_response(
                {"error": f"mode must be one of: {', '.join(AUTO_MODES)}"}, status=400
            )
    else:
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            return web.json_response({"error": "enabled must be a boolean"}, status=400)
        mode = "staged" if enabled else "off"
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    updates = data.get("updates")
    if not isinstance(updates, dict):
        updates = {}
    updates["auto"] = mode
    data["updates"] = updates
    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
    return web.json_response(
        {"ok": True, "auto": mode, "auto_update": mode == "staged"}
    )


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

    Runs ``<installer> install -U gideon==<tag>`` where the TAG is the release
    the configured policy selects — the newest stable, the newest including
    prereleases on beta, or an exact pin — targeting the SAME interpreter/prefix
    the gateway runs from. ``nightly`` has no wheel, so a package install on that
    channel rides stable. A pin naming no published release is REFUSED with an
    actionable message instead of quietly installing something else; an
    unresolvable channel (offline, cold cache) still performs the unpinned
    upgrade. The installer is RESOLVED (uv or pip), not assumed: a uv-created
    venv ships no pip module (issue #51). No web build: the wheel already carries
    the SPA. The 409 concurrent-apply guard is shared with the git path.
    """
    global _apply_in_flight

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"}, status=409
        )
    _apply_in_flight = True

    policy = update_policy()
    try:
        target = await self_update.resolve_package_target(
            policy.channel, policy.pin, offline=not policy.check_enabled
        )
    except Exception:
        logger.debug("package target resolution failed", exc_info=True)
        target = self_update.PackageTarget(spec=self_update.upgrade_spec(""))
    if not target.ok:
        _apply_in_flight = False
        logger.warning("self-update refused: %s", target.error)
        state.push_update_progress("error", target.error)
        return web.json_response({"error": target.error}, status=409)

    state.push_refresh("updating")
    spec = target.spec
    auth_mode = _live_auth_mode(request)

    async def _apply() -> None:
        global _apply_in_flight
        try:
            from gideon.operations._installer import NoInstallerError, install_argv

            try:
                argv = install_argv(["-U", spec, "--quiet"])
            except NoInstallerError as exc:
                logger.error("self-update: %s", exc)
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
                state.push_update_progress("error", "pip upgrade timed out")
                return
            if pip_up.returncode != 0:
                detail = (pip_err or b"").decode(errors="replace").strip()
                logger.error(
                    "self-update failed (rc=%d): %s", pip_up.returncode, detail[:500]
                )
                summary = self_update.installer_error_summary(detail)
                state.push_update_progress(
                    "error",
                    f"Upgrade failed: {summary}" if summary else "Upgrade failed",
                )
                return
            state.push_update_progress("restarting", "Restarting server…")
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception:
            logger.exception("pip self-update failed")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    task = asyncio.create_task(_apply())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "updating", "kind": "pip"})


def _start_background(state: ConsoleState, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return task


def _restart_only(state: ConsoleState, note: str, auth_mode: str) -> web.Response:
    """Nothing to move to — honor "Update & Restart" by just restarting."""
    logger.info("Update apply: nothing to apply (%s) — restarting only", note)

    async def _run() -> None:
        global _apply_in_flight
        try:
            state.push_update_progress("restarting", note)
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception:
            logger.exception("Restart (nothing-to-apply update) failed")
            state.push_update_progress("error", "Restart failed — check logs")
        finally:
            _apply_in_flight = False

    _start_background(state, _run())
    return web.json_response({"ok": True, "status": "restarting", "detail": note})


async def api_update_apply(request: web.Request) -> web.Response:
    """POST /api/update — move a source checkout onto the selected release, restart.

    Source pipeline: fetch the release tags (or, on nightly ONLY, the tracked
    branch) → fast-forward → ``pip install -e .`` (same interpreter) → frontend
    rebuild (``npm ci && npm run build``) → graceful re-exec. Progress is
    broadcast as ``update_progress`` WS events with steps ``pulling`` →
    ``installing`` → ``building`` → ``restarting`` (→ ``paused``/``error``/
    ``failed``).

    There is no ``git pull`` and no ``git reset --hard``. The ref comes from
    release policy (channel or exact pin), and the checkout only ever moves by a
    pure fast-forward, so an update can neither merge something nobody selected
    nor discard committed work.

    Uncommitted tracked edits PAUSE the update: 409 with ``status: "paused"``
    and an actionable reason. That refusal lives in
    :func:`self_update.plan_source_update` — one safeguard shared with the
    unattended updater and ``gideon update``, not a second parallel gate here.

    Graceful degradation: with nothing to move to (no release for the channel,
    or already on the selected ref) the pipeline short-circuits to
    ``restarting`` — the user asked for "Update & Restart", and a restart still
    applies committed local changes.
    """
    global _apply_in_flight
    state: ConsoleState = request.app["state"]

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

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"},
            status=409,
        )
    _apply_in_flight = True

    policy = update_policy()
    try:
        plan = await self_update.plan_source_update(
            proj, policy.channel, policy.pin, offline=not policy.check_enabled
        )
    except Exception:
        logger.exception("Could not plan the source update")
        _apply_in_flight = False
        return web.json_response(
            {"error": "Could not read the checkout state"}, status=500
        )

    if plan.paused:
        _apply_in_flight = False
        logger.warning("Update paused: %s", plan.reason)
        state.push_update_progress("paused", plan.reason)
        return web.json_response(
            {"error": plan.reason, "status": "paused", "paths": list(plan.paths)},
            status=409,
        )

    auth_mode = _live_auth_mode(request)
    if not plan.ok:
        return _restart_only(state, f"{plan.reason} Restarting…", auth_mode)

    state.push_refresh("updating")

    async def _apply() -> None:
        global _apply_in_flight
        try:
            state.push_update_progress("pulling", f"Fetching {plan.ref}…")
            fetched = await asyncio.to_thread(self_update.fetch_for_plan, proj, plan)
            if fetched.returncode != 0:
                detail = (fetched.stderr or "").strip()[:200]
                logger.error("Update: git fetch failed: %s", detail)
                state.push_update_progress("error", "git fetch failed")
                return
            target = await asyncio.to_thread(self_update.git_commit_for, proj, plan.ref)
            if not target:
                state.push_update_progress(
                    "error", f"{plan.ref} is not available in this checkout"
                )
                return
            head = await asyncio.to_thread(self_update.git_commit_for, proj, "HEAD")
            if head == target:
                state.push_update_progress(
                    "restarting", f"Already on {plan.ref} — restarting…"
                )
                await _graceful_reexec(state, auth_mode=auth_mode)
                return
            if not await asyncio.to_thread(
                self_update.git_is_fast_forward, proj, plan.ref
            ):
                state.push_update_progress(
                    "error",
                    f"Cannot fast-forward to {plan.ref} — this checkout has "
                    "commits that are not in it.",
                )
                return
            merged = await asyncio.to_thread(
                self_update.git_merge_ff_only, proj, plan.ref
            )
            if merged.returncode != 0:
                logger.error(
                    "Update: fast-forward to %s failed: %s",
                    plan.ref,
                    (merged.stderr or "").strip()[:200],
                )
                state.push_update_progress(
                    "error", f"Fast-forward to {plan.ref} failed"
                )
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
                state.push_update_progress("error", "pip install timed out")
                return
            if pip_install.returncode != 0:
                logger.error(
                    "Update: pip install failed (rc=%d): %s",
                    pip_install.returncode,
                    (pip_err or b"").decode(errors="replace")[:500],
                )
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
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception:
            logger.exception("Update failed")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    _start_background(state, _apply())
    return web.json_response(
        {"ok": True, "status": "updating", "ref": plan.ref, "mode": plan.mode}
    )


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


QUIESCENCE_POLL_S = 2.0
QUIESCENCE_TIMEOUT_S = 900.0


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


def is_quiescent(state: ConsoleState) -> bool:
    """True when no chat session and no background subagent is in flight."""
    snapshot = _active_work_snapshot(state)
    return not snapshot["running_agents"] and not snapshot["sessions"]


async def await_quiescence(
    state: ConsoleState,
    *,
    timeout: float | None = None,
    poll: float | None = None,
) -> bool:
    """Wait for chats and subagents to finish, up to *timeout* seconds.

    This is what makes an automatic update STAGED rather than unattended-now: a
    live chat or a running subagent holds the restart off until it finishes.
    False means the wait timed out and the caller must not apply — the update is
    deferred to the next check, never forced onto work in progress.
    """
    if state is None:
        return True
    timeout = QUIESCENCE_TIMEOUT_S if timeout is None else timeout
    poll = QUIESCENCE_POLL_S if poll is None else poll
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if is_quiescent(state):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(poll, remaining))


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
        msg = body.get("reject_message", self_update.DIRTY_TREE_REASON)
        return web.json_response({"error": msg, "status": "paused"}, status=409)

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
    ws: web.WebSocketResponse, data: dict[str, object], state: ConsoleState
) -> None:
    """Push one live log frame to ONE subscriber, removing it on failure.

    Goes through ``state.send_ws_event`` rather than writing the socket directly: that is
    the single deny-by-default event gate, and a log subscriber can be an app-scoped socket
    that never declared ``log``. Writing here directly is exactly how live logs bypassed the
    app permission the broadcast fan-out enforces."""
    if not await state.send_ws_event(ws, "log", data):
        if ws.closed:
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
                frame = {"level": record.levelname, "msg": msg}
                for ws in list(self._state._ws_log_subscribers):
                    try:
                        self._loop.call_soon_threadsafe(
                            self._loop.create_task,
                            _safe_ws_send(ws, frame, self._state),
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
