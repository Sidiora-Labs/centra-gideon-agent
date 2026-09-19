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
        try:
            from gideon.core.config import AppConfig
            from gideon.interfaces.dashboard.handlers import (
                _do_update_check,
                _update_info,
            )

            await _do_update_check()
            if not _update_info.get("available"):
                print("Already on latest version")
                return
            self.logger.info("Updates available from remote")
            if AppConfig.load().auto_update:
                self.logger.info("Auto-update enabled — applying update")
                await self.runtime._auto_apply_update()
            elif self.runtime.dashboard_state:
                self.runtime.dashboard_state.push_refresh("update_available")
        except Exception:
            self.logger.debug("Update check failed", exc_info=True)

    async def branch(self, project: str) -> str | None:
        result = await run_command(
            ("git", "rev-parse", "--abbrev-ref", "HEAD"),
            project,
            10,
            silence_errors=True,
        )
        if result.code:
            self.logger.error("Auto-update: could not determine current branch")
            return None
        name = result.out.strip().decode() if result.out else ""
        name = "main" if name in ("", "HEAD") else name
        if name == "main":
            return name
        self.logger.debug("Auto-update: skipping — on branch %s, not main", name)
        return None

    async def refresh_source(self, project: str, branch: str) -> bool:
        from gideon.operations import self_update

        self.progress("pulling", "Fetching latest changes…")
        fetched = await run_command(("git", "fetch", "origin", branch), project, 60)
        if fetched.code:
            self.clear()
            return False
        reference = f"origin/{branch}"
        delta = await run_command(
            ("git", "diff", "HEAD", reference, "--quiet"), project, 10, quiet=True
        )
        if delta.code == 0:
            self.clear()
            return False
        changed = await asyncio.to_thread(self_update.git_tracked_changes, project)
        if changed:
            self.logger.warning(
                "Auto-update: refusing to apply — %d uncommitted tracked-file change(s) in %s would be discarded by a hard reset. Commit or `git stash` them; the update applies on the next check.",
                len(changed),
                project,
            )
            self.progress(
                "error", "Update paused — commit or stash your local changes first."
            )
            return False
        reset = await run_command(
            ("git", "reset", "--hard", reference), project, 10, quiet=True
        )
        if reset.code:
            self.logger.error(
                "Auto-update: git reset --hard failed (rc=%d)", reset.code
            )
            self.clear()
            return False
        self.logger.info("Auto-update: reset to origin/%s, rebuilding", branch)
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
            branch = await self.branch(project)
            if branch is None or not await self.refresh_source(project, branch):
                return
            if await self.rebuild(project):
                await self.restart()
        except Exception:
            self.logger.warning("Auto-update failed", exc_info=True)
