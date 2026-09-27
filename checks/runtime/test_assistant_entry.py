import os
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


_ARTIFACT = Path(__file__).parents[2] / "apps" / "assistant" / "dist" / "web"
_REPOSITORY = Path(__file__).parents[2]


def _artifact_file(suffix: str) -> Path:
    return next(path for path in _ARTIFACT.rglob(f"*{suffix}") if path.is_file())


def _assistant_client(core) -> TestClient:
    from gideon.interfaces.dashboard.token_auth import token_auth_middleware

    app = web.Application()
    app.middlewares.append(token_auth_middleware(port=7777))
    app.router.add_route("*", "/{tail:.*}", core.index)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_assistant_entry_serves_nested_reload_and_exported_chunk(monkeypatch):
    from gideon.interfaces.dashboard.handlers import core

    assert (_ARTIFACT / "index.html").is_file(), "build the assistant web artifact first"
    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", _ARTIFACT)

    async with _assistant_client(core) as client:
        entry = await client.get("/assistant/chat", headers={"Accept": "text/html"})
        assert entry.status == 200
        assert entry.content_type == "text/html"
        assert await entry.read() == (_ARTIFACT / "index.html").read_bytes()

        for suffix, content_type in ((".js", "text/javascript"), (".css", "text/css")):
            asset = _artifact_file(suffix)
            relative_asset = asset.relative_to(_ARTIFACT).as_posix()
            response = await client.get(f"/assistant/{relative_asset}")
            assert response.status == 200
            assert response.content_type == content_type
            assert await response.read() == asset.read_bytes()


@pytest.mark.asyncio
async def test_assistant_artifact_absence_is_explicit_and_keeps_console_fallback(monkeypatch, tmp_path):
    from gideon.interfaces.dashboard.handlers import core

    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", tmp_path / "not-installed")

    async with _assistant_client(core) as client:
        response = await client.get("/assistant/", headers={"Accept": "text/html"})
        assert response.status == 503
        body = await response.read()
        assert b"Gideon Assistant is unavailable" in body
        assert b' href="/">Open the existing Gideon Console (fallback)</a>' in body


@pytest.mark.asyncio
async def test_missing_assistant_assets_do_not_fall_back_to_console(monkeypatch):
    from gideon.interfaces.dashboard.handlers import core

    monkeypatch.setattr(core, "_ASSISTANT_DIST_DIR", _ARTIFACT)
    assert (_ARTIFACT / "index.html").is_file(), "build the assistant web artifact first"

    async with _assistant_client(core) as client:
        response = await client.get(
            "/assistant/_expo/static/js/web/missing.js", headers={"Accept": "*/*"}
        )
        assert response.status == 404
        assert response.content_type == "text/plain"
        assert await response.read() == b"Assistant asset not found"


@pytest.mark.asyncio
async def test_assistant_static_bypass_does_not_cover_api_mutations_or_traversal():
    from gideon.interfaces.dashboard.handlers import core

    async with _assistant_client(core) as client:
        for path in ("/api/auth/session", "/api/chat/sessions"):
            response = await client.get(path)
            assert response.status == 403
            assert response.headers["X-Auth-Required"] == "true"
            await response.read()

        mutation = await client.post("/assistant/", data="ignored")
        assert mutation.status == 403
        assert mutation.headers["X-Auth-Required"] == "true"
        await mutation.read()

        traversal = await client.get("/assistant/%2e%2e/api/chat/sessions")
        assert traversal.status == 403
        assert traversal.headers["X-Auth-Required"] == "true"
        await traversal.read()

        assistant_api = await client.get(
            "/assistant/api/chat", headers={"Accept": "text/html"}
        )
        assert assistant_api.status == 403
        assert assistant_api.headers["X-Auth-Required"] == "true"
        await assistant_api.read()

        unknown_asset = await client.get(
            "/assistant/unknown-route", headers={"Accept": "*/*"}
        )
        assert unknown_asset.status == 403
        assert unknown_asset.headers["X-Auth-Required"] == "true"
        await unknown_asset.read()


def test_runtime_build_includes_the_complete_assistant_web_artifact(tmp_path):
    assert (_ARTIFACT / "index.html").is_file(), "build the assistant web artifact first"
    build_lib = tmp_path / "runtime-build-lib"
    isolated_home = tmp_path / "gideon-home"
    env = os.environ.copy()
    env["GIDEON_HOME"] = str(isolated_home)

    subprocess.run(
        [
            sys.executable,
            "setup.py",
            "build_py",
            "--build-lib",
            str(build_lib),
        ],
        cwd=_REPOSITORY,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    packaged = build_lib / "gideon" / "static" / "assistant"
    relative_paths = [
        Path("index.html"),
        Path("assistant-source-notices.txt"),
        Path(_artifact_file(".js").relative_to(_ARTIFACT)),
        Path(_artifact_file(".css").relative_to(_ARTIFACT)),
    ]
    for relative_path in relative_paths:
        assert (packaged / relative_path).read_bytes() == (
            _ARTIFACT / relative_path
        ).read_bytes()
