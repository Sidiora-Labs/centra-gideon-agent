import asyncio
import os
import subprocess

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.interfaces.dashboard.handlers.capabilities_references import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


def git(path, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Reference UI",
            "-c",
            "user.email=reference@example.invalid",
            "-C",
            str(path),
            *args,
        ],
        check=True,
        capture_output=True,
    )


async def main():
    root = config_dir()
    workspace = root / "workspace"
    source = root / "upstream"
    workspace.mkdir(parents=True)
    source.mkdir()
    os.environ["GIDEON_WORKSPACE"] = str(workspace)
    git(source, "init", "-b", "main")
    for number in (1, 2):
        (source / "notes.txt").write_text(str(number))
        git(source, "add", "notes.txt")
        git(source, "commit", "-m", f"actual reference revision {number}")
    git(workspace, "clone", str(source), "reference")
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())
