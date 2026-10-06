"""A file editor's revision and its raw content validator protect different views."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_stale_file_draft_is_refused_without_replacing_current_bytes(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home / ".gideon"))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))
    path = workspace / "draft.md"
    path.write_text("first draft\n", encoding="utf-8")

    from gideon.interfaces.dashboard.handlers.files import api_file_read, api_file_write

    app = web.Application()
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    async with TestClient(TestServer(app)) as client:
        read = await client.get("/api/file-read", params={"path": str(path)})
        assert read.status == 200
        base_revision = read.headers["X-Content-Revision"]
        raw_validator = read.headers["X-Content-Validator"]
        assert await read.text() == "first draft\n"

        missing_base = await client.post(
            "/api/file-write",
            json={"path": str(path), "content": "blind draft\n"},
        )
        assert missing_base.status == 428
        assert (await missing_base.json())["error"]["code"] == "revision_required"
        assert path.read_text(encoding="utf-8") == "first draft\n"

        path.write_text("another editor's change\n", encoding="utf-8")
        refused = await client.post(
            "/api/file-write",
            json={
                "path": str(path),
                "content": "my stale draft\n",
                "expected_validator": raw_validator,
            },
            headers={"If-Match": f'"{base_revision}"'},
        )
        assert refused.status == 409
        assert (await refused.json())["error"]["code"] == "stale_write"

    assert path.read_bytes() == b"another editor's change\n"
