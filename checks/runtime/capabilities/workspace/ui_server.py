import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import os
import pty
import tempfile
from types import SimpleNamespace

from aiohttp import web


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["GIDEON_HOME"] = directory
        os.environ["GIDEON_WORKSPACE"] = str(root / "workspace")
        from gideon.core.config.loader import workspace_root

        from checks.runtime.capabilities.workspace.test_workspace import repository

        repo = repository(workspace_root() / "repo")
        (root / "config.json").write_text(
            json.dumps({"dashboard": {"terminal": {"enabled": True}}})
        )
        from gideon.engine.tasks.handlers import register_task_routes
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.handlers.terminal import (
            _TerminalSession,
            api_terminal_list,
        )
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )

        reset_secret_cache()
        app = web.Application(middlewares=[token_auth_middleware()])
        master_fd, slave_fd = pty.openpty()
        terminal = await asyncio.create_subprocess_exec(
            "/bin/sleep",
            "300",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(repo),
            start_new_session=True,
        )
        os.close(slave_fd)
        app["state"] = SimpleNamespace(
            _terminal_sessions={
                "native-workspace-terminal": _TerminalSession(
                    session_id="native-workspace-terminal",
                    master_fd=master_fd,
                    proc=terminal,
                    cwd=str(repo),
                    shell="/bin/sleep",
                )
            }
        )
        register(app)
        register_task_routes(app)
        app.router.add_get("/api/terminal/sessions", api_terminal_list)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{port}",
                    "repo": str(repo),
                    "token": generate_token("workspace-browser"),
                }
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            terminal.terminate()
            await terminal.wait()
            os.close(master_fd)


if __name__ == "__main__":
    asyncio.run(main())
