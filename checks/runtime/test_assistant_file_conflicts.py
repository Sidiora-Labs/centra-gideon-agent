"""Versioned assistant file edits against the native dashboard handlers."""

import asyncio
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def native_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(workspace))

    from gideon.core.config.loader import config_dir, workspace_root
    from gideon.interfaces.dashboard.handlers import (
        api_file_list,
        api_file_read,
        api_file_write,
    )

    assert config_dir() == home
    assert workspace_root() == workspace

    app = web.Application()
    app.router.add_get("/api/file-list", api_file_list)
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    return app, home, workspace


def _audit_outcomes(home):
    return [
        (event["operation"], event["outcome"])
        for line in (home / "security_events.jsonl").read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line))["operation"] == "file_write"
    ]


@pytest.mark.asyncio
async def test_read_write_validator_rejects_stale_writer(native_files):
    app, home, workspace = native_files
    project = workspace / "project"
    project.mkdir()
    target = project / "notes.txt"
    target.write_text("first", encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        listed = await client.get("/api/file-list", params={"path": str(project)})
        assert listed.status == 200
        assert [row["path"] for row in (await listed.json())["entries"]] == [str(target)]

        first = await client.get("/api/file-read", params={"path": str(target)})
        assert first.status == 200
        assert await first.text() == "first"
        validator = first.headers["X-Content-Validator"]
        assert validator == hashlib.sha256(b"first").hexdigest()

        written = await client.post("/api/file-write", json={
            "path": str(target), "content": "second", "expected_validator": validator,
        })
        assert written.status == 200
        assert (await written.json())["validator"] == hashlib.sha256(b"second").hexdigest()
        assert target.read_text(encoding="utf-8") == "second"

        stale = await client.post("/api/file-write", json={
            "path": str(target), "content": "third", "expected_validator": validator,
        })
        assert stale.status == 409
        assert (await stale.json())["error"] == "file changed since read"
        assert target.read_text(encoding="utf-8") == "second"

    assert _audit_outcomes(home) == [
        ("file_write", "success"), ("file_write", "conflict"),
    ]


@pytest.mark.asyncio
async def test_concurrent_writes_with_one_validator_have_one_winner(native_files):
    app, home, workspace = native_files
    target = workspace / "notes.txt"
    target.write_text("first", encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        first = await client.get("/api/file-read", params={"path": str(target)})
        assert first.status == 200
        assert await first.text() == "first"
        validator = first.headers["X-Content-Validator"]

        async def write(content):
            response = await client.post("/api/file-write", json={
                "path": str(target), "content": content, "expected_validator": validator,
            })
            return response.status, await response.json()

        results = await asyncio.gather(write("second"), write("third"))
        assert sorted(status for status, _ in results) == [200, 409]
        winner = next(body for status, body in results if status == 200)
        loser = next(body for status, body in results if status == 409)
        assert loser["error"] == "file changed since read"
        assert target.read_text(encoding="utf-8") in ("second", "third")
        assert winner["validator"] == hashlib.sha256(target.read_bytes()).hexdigest()

    assert sorted(_audit_outcomes(home)) == [
        ("file_write", "conflict"), ("file_write", "success"),
    ]
