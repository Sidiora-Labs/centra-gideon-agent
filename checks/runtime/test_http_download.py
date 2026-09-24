from __future__ import annotations

import ast
import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.http_download import download_headers


@pytest.mark.parametrize(
    "name",
    ["report.txt", "résumé 中文.txt", 'a"; injected="yes.txt', "100% complete.txt"],
)
def test_filename_roundtrips_without_header_syntax_injection(name):
    headers = download_headers(name)
    value = headers["Content-Disposition"]
    assert value.isascii()
    assert value.startswith('attachment; filename="')
    assert value.count('"') == 2
    assert unquote(value.split("filename*=UTF-8''", 1)[1]) == name
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "name",
    [
        "\rX-Evil: yes",
        "file\nInjected: yes",
        "bad\x00.txt",
        "tab\t.txt",
        "bad\x7f.txt",
        "bad\x85.txt",
    ],
)
def test_controls_are_refused_without_echoing_filename(name):
    with pytest.raises(web.HTTPBadRequest) as error:
        download_headers(name)
    assert error.value.status == 400
    assert name not in error.value.text


def test_redacts_before_encoding_and_removes_path_separators():
    header = download_headers("../résumé-AKIAIOSFODNN7EXAMPLE.txt")[
        "Content-Disposition"
    ]
    decoded = unquote(header)
    assert "AKIAIOSFODNN7EXAMPLE" not in decoded
    assert "REDACTED" in decoded
    assert "/" not in decoded and "\\" not in decoded
    assert "résumé" in decoded
    assert 'filename="download"' in download_headers(" ... ")["Content-Disposition"]
    assert download_headers("movie.mp4", inline=True)["Content-Disposition"].startswith(
        "inline;"
    )


CONSUMERS = {
    "interfaces/dashboard/session_starters.py",
    "interfaces/dashboard/handlers/memory.py",
    "interfaces/dashboard/handlers/durability.py",
    "interfaces/dashboard/handlers/files.py",
    "interfaces/dashboard/handlers/rooms.py",
    "engine/tasks/project_transfer.py",
}


def local_header_lines(source):
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    lines = []
    for node in ast.walk(tree):
        value = (
            node.value
            if isinstance(node, ast.Constant)
            else node.attr if isinstance(node, ast.Attribute) else ""
        )
        if id(node) in docstrings or not isinstance(value, str):
            continue
        normalized = value.lower().replace("_", "-")
        if (
            "content-disposition" in normalized
            or "attachment; filename" in normalized
            or "inline; filename" in normalized
        ):
            lines.append(node.lineno)
    return lines


def test_download_header_construction_has_one_owner_and_all_routes_consume_it():
    root = Path(__file__).resolve().parents[2] / "runtime/gideon"
    violations = {}
    consumers = set()
    paths = list(root.rglob("*.py"))
    assert len(paths) > 100
    for path in paths:
        relative = path.relative_to(root).as_posix()
        source = path.read_text()
        if relative != "http_download.py":
            lines = local_header_lines(source)
            if lines:
                violations[relative] = lines
        if "from gideon.http_download import download_headers" in source:
            consumers.add(relative)
    assert not violations
    assert CONSUMERS <= consumers
    for relative in CONSUMERS:
        tree = ast.parse((root / relative).read_text())
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "download_headers"
            for node in ast.walk(tree)
        )


@pytest.mark.parametrize(
    "source",
    [
        'headers = {"Content-Disposition": value}',
        'headers["content-disposition"] = value',
        "headers[hdrs.CONTENT_DISPOSITION] = value",
        'value = f"attachment; filename={name}"',
        'headers = {"Content-" "Disposition": value}',
    ],
)
def test_duplication_ratchet_rejects_local_constructors(source):
    assert local_header_lines(source)


@pytest.mark.asyncio
async def test_real_download_routes_use_shared_headers(tmp_path, monkeypatch):
    from gideon.cognition.history import ConversationLog
    from gideon.core.config import AppConfig
    from gideon.core.config.loader import outbox_dir
    from gideon.engine.rooms.store import RoomStore
    from gideon.engine.session import ConversationDirectory
    from gideon.engine.tasks import registry
    from gideon.engine.tasks.handlers import register_task_routes
    from gideon.interfaces.dashboard.handlers.durability import api_durability_export
    from gideon.interfaces.dashboard.handlers.files import api_outbox_download
    from gideon.interfaces.dashboard.handlers.memory import api_memory_graph_export
    from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
    from gideon.interfaces.dashboard.session_starters import api_session_export
    from gideon.interfaces.dashboard.state import ConsoleState

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"rooms": {"enabled": True}}))
    log = ConversationLog(tmp_path / "sessions")
    log.append("dashboard:export-test", "user", "Download me")
    log.update_metadata(
        "dashboard:export-test", {"title": "Report AKIAIOSFODNN7EXAMPLE"}
    )
    state = ConsoleState(
        ConversationDirectory(AppConfig()), start_time=0, conversation_log=log
    )
    app = web.Application()
    app["state"] = state
    app.router.add_get("/session/{session}", api_session_export)
    app.router.add_get("/memory", api_memory_graph_export)
    app.router.add_get("/outbox/{filename}", api_outbox_download)
    app.router.add_post("/durability", api_durability_export)
    setup_room_routes(app, RoomStore(tmp_path))
    register_task_routes(app)
    outbox_dir().mkdir(parents=True, exist_ok=True)
    filename = "résumé-AKIAIOSFODNN7EXAMPLE.txt"
    (outbox_dir() / filename).write_text("Download me")
    (outbox_dir() / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42binarydata")
    bad_name = "bad\r\nInjected.txt"
    (outbox_dir() / bad_name).write_text("Download me")
    previous = dict(registry._providers)
    registry._providers.clear()
    try:
        async with TestClient(TestServer(app)) as client:
            project_response = await client.post(
                "/api/projects", json={"name": "Download project"}
            )
            assert project_response.status == 201
            project = await project_response.json()
            room_response = await client.post(
                "/api/rooms",
                json={
                    "name": "Download room",
                    "members": [{"id": "writer", "agent": "gideon"}],
                },
            )
            assert room_response.status == 201
            room = (await room_response.json())["room"]
            for method, route in [
                ("get", "/session/export-test"),
                ("get", "/memory"),
                ("get", f"/outbox/{filename}"),
                ("get", "/outbox/clip.mp4"),
                ("post", "/durability"),
                ("get", f"/api/projects/{project['id']}/export"),
                ("get", f"/api/rooms/{room['id']}/export"),
            ]:
                response = await getattr(client, method)(route)
                assert response.status == 200, (route, await response.text())
                value = response.headers["Content-Disposition"]
                assert value.isascii() and "filename*=UTF-8''" in value
                assert "AKIAIOSFODNN7EXAMPLE" not in unquote(value)
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert await response.read()
            from urllib.parse import quote

            response = await client.get("/outbox/" + quote(bad_name, safe=""))
            assert response.status == 400
            assert "Content-Disposition" not in response.headers
    finally:
        registry._providers.clear()
        registry._providers.update(previous)
