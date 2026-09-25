import asyncio

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.dashboard.handlers.capabilities_pr_screening import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.workspace.capabilities.platform import pr_screening as p


async def main():
    CredentialStore(config_dir()).save({"github-review": {"type": "none"}})
    request = {
        "repo": "example/project",
        "number": 7,
        "credential": "github-review",
        "screen_provider": "missing-screen",
        "review_provider": "missing-review",
    }
    pr = {
        "state": "open",
        "draft": False,
        "head": {"sha": "a" * 40},
        "changed_files": 1,
        "commits": 1,
        "title": "Parser change",
    }
    source = p.snapshot(
        pr,
        [{"filename": "parser.py", "patch": "-old\n+new"}],
        [{"sha": "a" * 40, "commit": {"message": "Parser change"}}],
    )
    p.record(request, source, "user:dev-local")
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
