"""Build and locate the console from source or an installed distribution."""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from gideon.core.cancellation import wait_with_timeout
from gideon.core.layout import package_root

logger = logging.getLogger(__name__)
_DIR_NAME = "apps/console"


def _resolve_website_dist(pkg_dir: Path) -> Path | None:
    candidate = pkg_dir.parent.parent / _DIR_NAME / "dist"
    return candidate.resolve() if (candidate / "index.html").is_file() else None


def ensure_dev_dist_symlink() -> Path | None:
    destination = package_root() / "static/dist"
    if (destination / "index.html").is_file():
        return destination.resolve()
    source = _resolve_website_dist(package_root())
    if source is None:
        return None
    _link_bundle(source, destination, logger.warning)
    return source if (destination / "index.html").is_file() else None


def _link_bundle(
    source: Path, destination: Path, report: Callable[[str], None]
) -> None:
    try:
        if destination.is_symlink():
            if destination.resolve() == source.resolve():
                return
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source.resolve(), target_is_directory=True)
    except OSError as error:
        report(f"Could not link console bundle: {error}")


def _propagate_dist(
    built_dist: Path, proj_path: Path, log: Callable[[str], None] = print
) -> None:
    _link_bundle(built_dist, proj_path / "runtime/gideon/static/dist", log)


def _build_steps(project: Path):
    if not (project / _DIR_NAME / "package.json").is_file():
        raise FileNotFoundError(f"{_DIR_NAME}/ not found — skipping frontend build")
    if not shutil.which("node") or not shutil.which("npm"):
        raise FileNotFoundError("Node.js and npm are required to build the console")
    return (
        (["npm", "ci", "--no-audit", "--no-fund"], 180),
        (["npm", "run", "build", "--workspace=apps/console"], 120),
    )


def build_frontend_sync(proj_path: Path, log: Callable[[str], None] = print) -> None:
    try:
        for command, timeout in _build_steps(proj_path):
            result = subprocess.run(
                command, cwd=proj_path, capture_output=True, timeout=timeout
            )
            if result.returncode:
                log("Frontend build failed — dashboard may be stale")
                return
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        log(str(error))
        return
    _propagate_dist(proj_path / _DIR_NAME / "dist", proj_path, log)


async def build_frontend_async(
    proj: str, push_progress: Callable[[str, str], None] | None = None
) -> None:
    project = Path(proj)

    def report(message: str) -> None:
        if push_progress:
            push_progress("warning", message)

    try:
        for command, timeout in _build_steps(project):
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(project),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            await wait_with_timeout(process, timeout)
            if process.returncode:
                report("Frontend build failed — dashboard may be stale")
                return
    except (FileNotFoundError, TimeoutError) as error:
        report(str(error))
        return
    _propagate_dist(project / _DIR_NAME / "dist", project, report)
