from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from gideon.core.cancellation import run_with_timeout, wait_with_timeout


@dataclass(frozen=True)
class CommandReply:
    code: int
    out: bytes = b""
    err: bytes = b""


async def run_command(
    args: tuple[str, ...],
    cwd: Any,
    timeout: int,
    *,
    quiet: bool = False,
    silence_errors: bool = False,
) -> CommandReply:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.DEVNULL if quiet else asyncio.subprocess.PIPE,
        stderr=(
            asyncio.subprocess.DEVNULL
            if quiet or silence_errors
            else asyncio.subprocess.PIPE
        ),
    )
    if quiet:
        await wait_with_timeout(process, timeout)
        return CommandReply(process.returncode)
    output, error = await run_with_timeout(process, timeout)
    return CommandReply(process.returncode, output, error)


class DependencyRepair:
    def __init__(
        self, requirements: list[tuple[str, str]], logger: logging.Logger
    ) -> None:
        self.requirements, self.logger = requirements, logger

    def run(self) -> None:
        missing = list(
            package
            for module, package in self.requirements
            if importlib.util.find_spec(module) is None
        )
        project = os.environ.get("GIDEON_PROJECT_DIR", "")
        if not missing or not project:
            return
        from gideon.operations._installer import NoInstallerError, install_argv

        self.logger.warning("Missing deps %s — installing directly", missing)
        print(f"Installing missing dependencies: {', '.join(missing)}")
        try:
            command = install_argv(["--quiet", *missing])
        except NoInstallerError as error:
            print(f"❌ {error}")
            self.logger.error("Dep repair impossible: %s", error)
            return
        reply = subprocess.run(command, cwd=project, capture_output=True, timeout=300)
        if reply.returncode:
            print("❌ Dependency install failed — run manually: gideon update")
            self.logger.error(
                "Dep repair failed: %s", reply.stderr.decode(errors="replace")[:500]
            )
            return
        importlib.invalidate_caches()
        print("✅ Dependencies installed")


class RuntimeUpdates:
    def __init__(
        self, runtime: Any, build_frontend: Any, logger: logging.Logger
    ) -> None:
        self.runtime, self.build_frontend, self.logger = runtime, build_frontend, logger

    def progress(self, stage: str, message: str) -> None:
        if self.runtime.dashboard_state:
            self.runtime.dashboard_state.push_update_progress(stage, message)

    def clear(self) -> None:
        if self.runtime.dashboard_state:
            self.runtime.dashboard_state.clear_update_progress()

    async def check(self) -> None:
        """The boot-path release check, then the staged apply if it is opted in.

        ``updates.check_enabled`` is read BEFORE anything that could open a
        connection, and ``updates.auto`` decides what happens with a result:
        ``off`` (the default) only raises the notification; ``staged`` hands off
        to the apply, which waits for active work before touching anything.
        """
        try:
            from gideon.core.config import AppConfig
            from gideon.interfaces.dashboard.handlers import (
                _do_update_check,
                _update_info,
            )

            policy = AppConfig.load().updates
            if not policy.check_enabled:
                self.logger.debug("Release check skipped: updates.check_enabled is off")
                return
            await _do_update_check()
            if not _update_info.get("available"):
                print("Already on latest version")
                return
            self.logger.info("Updates available from remote")
            if policy.auto == "staged":
                self.logger.info("Automatic updates are staged — applying")
                await self.runtime._auto_apply_update()
            elif self.runtime.dashboard_state:
                self.runtime.dashboard_state.push_refresh("update_available")
        except Exception:
            self.logger.debug("Update check failed", exc_info=True)

    async def stage(self) -> bool:
        """Hold the apply until chats and subagents finish. False ⇒ do not apply.

        This is what "staged" means: an opted-in automatic update never lands on
        top of running work. A wait that runs out defers to the next check
        rather than interrupting anything.
        """
        state = self.runtime.dashboard_state
        if state is None:
            return True
        from gideon.interfaces.dashboard.handlers.updates import (
            await_quiescence,
            is_quiescent,
        )

        if is_quiescent(state):
            return True
        self.logger.info(
            "Auto-update staged — waiting for active chats and subagents to finish"
        )
        self.progress("staged", "Update staged — waiting for active work to finish…")
        if await await_quiescence(state):
            return True
        self.logger.info(
            "Auto-update: work still in flight after the staging window — "
            "deferring to the next check"
        )
        self.progress("staged", "Update deferred — active work is still running.")
        return False

    async def plan(self, project: str, policy: Any) -> Any:
        """The ref this checkout should move to, or None with the reason reported."""
        from gideon.operations import self_update

        plan = await self_update.plan_source_update(
            project, policy.channel, policy.pin, offline=not policy.check_enabled
        )
        if plan.paused:
            self.logger.warning(
                "Auto-update paused: %s (%d tracked change(s) in %s)",
                plan.reason,
                len(plan.paths),
                project,
            )
            self.progress("paused", plan.reason)
            return None
        if not plan.ok:
            self.logger.info("Auto-update: %s", plan.reason)
            self.clear()
            return None
        return plan

    async def refresh_source(self, project: str, plan: Any) -> bool:
        """Fast-forward the checkout onto the planned ref. Never resets, never pulls."""
        from gideon.operations import self_update

        self.progress("pulling", f"Fetching {plan.ref}…")
        fetched = await asyncio.to_thread(self_update.fetch_for_plan, project, plan)
        if fetched.returncode:
            self.logger.error(
                "Auto-update: git fetch failed (rc=%d)", fetched.returncode
            )
            self.clear()
            return False
        target = await asyncio.to_thread(self_update.git_commit_for, project, plan.ref)
        if not target:
            self.logger.info(
                "Auto-update: %s is not available in %s", plan.ref, project
            )
            self.clear()
            return False
        head = await asyncio.to_thread(self_update.git_commit_for, project, "HEAD")
        if head == target:
            self.clear()
            return False
        if not await asyncio.to_thread(
            self_update.git_is_fast_forward, project, plan.ref
        ):
            self.logger.warning(
                "Auto-update: refusing — %s is not a fast-forward from HEAD in %s",
                plan.ref,
                project,
            )
            self.progress(
                "error",
                f"Update paused — {plan.ref} is not a fast-forward from this checkout.",
            )
            return False
        merged = await asyncio.to_thread(
            self_update.git_merge_ff_only, project, plan.ref
        )
        if merged.returncode:
            self.logger.error(
                "Auto-update: fast-forward to %s failed (rc=%d)",
                plan.ref,
                merged.returncode,
            )
            self.clear()
            return False
        self.logger.info("Auto-update: fast-forwarded to %s, rebuilding", plan.ref)
        return True

    async def rebuild(self, project: str) -> bool:
        from gideon.operations.self_update import package_root

        root = package_root(project)
        self.progress("installing", "Installing package…")
        installed = await run_command(
            (sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"), root, 400
        )
        if installed.code:
            self.logger.error(
                "Auto-update: pip install failed (rc=%d): %s",
                installed.code,
                installed.err.decode(errors="replace")[:500],
            )
            self.progress("error", "pip install failed")
            return False
        self.progress("building", "Building frontend…")
        state = self.runtime.dashboard_state
        await self.build_frontend(
            root, push_progress=state.push_update_progress if state else None
        )
        return True

    async def restart(self) -> None:
        self.logger.info("Auto-update: rebuild complete, restarting")
        print("Update applied — restarting gateway…")
        if self.runtime.dashboard_state:
            from gideon.interfaces.dashboard.handlers.updates import _graceful_reexec

            self.progress("restarting", "Restarting server…")
            await _graceful_reexec(self.runtime.dashboard_state)
        else:
            if self.runtime.sessions:
                await self.runtime.sessions.close_all()
            os.execv(sys.executable, [sys.executable, "-m", "gideon", *sys.argv[1:]])

    async def apply(self) -> None:
        project = os.environ.get("GIDEON_PROJECT_DIR", "")
        if not project:
            return
        try:
            from gideon.core.config import AppConfig

            policy = AppConfig.load().updates
            plan = await self.plan(project, policy)
            if plan is None:
                return
            if not await self.stage():
                return
            if not await self.refresh_source(project, plan):
                return
            if await self.rebuild(project):
                await self.restart()
        except Exception:
            self.logger.warning("Auto-update failed", exc_info=True)
