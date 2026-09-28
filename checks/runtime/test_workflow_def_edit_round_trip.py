from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows import defs, service
from gideon.automation.workflows.bundled_defs import register_bundled_provider
from gideon.automation.workflows.handlers import register_workflow_routes
from gideon.automation.workflows.native_defs import DefinitionRevisionConflict, NativeWorkflowDefProvider, defs_root


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def providers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    tmp_path.mkdir(exist_ok=True)
    register_bundled_provider()
    provider = NativeWorkflowDefProvider()
    defs.register_provider(provider)
    yield provider
    defs.unregister_provider("native")
    defs.unregister_provider("bundled")


def _stored(name: str) -> dict:
    return json.loads((defs_root() / name / "workflow.json").read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_named_base_round_trip_and_historical_read_keep_hidden_values_and_fields(providers):
    source = {
        "name": "editor-round-trip",
        "description": "Original",
        "root": {"kind": "sequence", "id": "main", "children": [
            {"kind": "transform", "id": "draft", "config": {"expr": "before", "max_tokens": 900}},
        ]},
        "inputs": {"auth_header": {"type": "string", "help": "sent as-is"}},
        "defaults": {"budget": {"max_tokens": 0}},
        "on_overlap": "queue",
        "runtime_hints": {"judge": {"rubric": "keep this contract"}},
        "metadata": {"risk": "low", "editor_extension": {"kept": True}},
        "workspace": {"root": "project"},
        "provenance": "user",
    }
    saved = await providers.save_def(**source)
    assert saved.version == 1
    initial_defaults = _stored("editor-round-trip")["defaults"]
    assert initial_defaults["budget"]["max_tokens"] == 0
    opened = await service.get_def("editor-round-trip")
    projection = opened["definition"]
    assert projection["root"]["children"][0]["config"]["_has_max_tokens"] is True
    assert projection["inputs"]["_has_auth_header"] is True
    changed_root = json.loads(json.dumps(projection["root"]))
    changed_root["children"][0]["config"]["expr"] = "after"

    result = await service.author_def(
        name="editor-round-trip", root=changed_root, description=projection["description"],
        inputs=projection["inputs"], tags=projection["tags"], metadata=projection["metadata"],
        defaults=projection["defaults"], runtime_hints=projection["runtime_hints"],
        on_overlap=projection["on_overlap"], workspace=projection["workspace"],
        provenance="user", expected_revision=1, based_on="editor-round-trip",
    )
    assert result["saved"] is True
    current = _stored("editor-round-trip")
    assert current["version"] == 2
    assert current["root"]["children"][0]["config"] == {"expr": "after", "max_tokens": 900}
    assert current["inputs"]["auth_header"] == {"type": "string", "help": "sent as-is", "required": False, "default": None}
    assert current["defaults"] == initial_defaults
    assert current["runtime_hints"] == source["runtime_hints"]
    assert current["on_overlap"] == "queue"
    assert current["metadata"]["editor_extension"] == {"kept": True}

    app = web.Application()
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/workflows/editor-round-trip/versions/1")
        assert response.status == 200
        historical = await response.json()
    assert historical["version"] == 1
    assert historical["definition"]["root"]["children"][0]["config"]["_has_max_tokens"] is True
    assert "max_tokens" not in historical["definition"]["root"]["children"][0]["config"]


@pytest.mark.anyio
async def test_unmatched_hidden_flag_refuses_write_and_revision_guard_is_atomic(providers):
    original = await providers.save_def(
        name="editor-conflict", provenance="user", root={"kind": "sequence", "id": "main", "children": [
            {"kind": "transform", "id": "first", "config": {"expr": "one"}},
            {"kind": "sequence", "id": "nested", "children": [
                {"kind": "transform", "id": "a", "config": {"expr": "a"}},
                {"kind": "transform", "id": "b", "config": {"expr": "b"}},
                {"kind": "transform", "id": "identify", "config": {"expr": "authors"}},
            ]},
        ]},
    )
    opened = (await service.get_def("editor-conflict"))["definition"]
    second = json.loads(json.dumps(opened))
    second["root"]["children"][1]["children"][2]["config"]["_has_authors"] = True
    second["root"]["children"][1]["children"][2]["id"] = "identify-renamed"
    refused = await service.author_def(
        name="editor-copy", root=second["root"], inputs=second["inputs"], metadata=second["metadata"],
        provenance="user", based_on="editor-conflict",
    )
    assert refused["code"] == "WF_HIDDEN_VALUE_UNMATCHED"
    assert refused["issues"][0]["code"] == "WF_HIDDEN_VALUE_UNMATCHED"
    assert refused["issues"][0]["path"] == "root.children[1].children[2]"
    assert not (defs_root() / "editor-copy").exists()

    changed = await providers.save_def(name="editor-conflict", root=opened["root"], expected_revision=1, provenance="user")
    assert changed.version == 2
    with pytest.raises(DefinitionRevisionConflict):
        await providers.save_def(name="editor-conflict", root=opened["root"], expected_revision=1, provenance="user")


@pytest.mark.anyio
async def test_http_write_requires_the_captured_revision_and_copy_is_create_only(providers):
    app = web.Application()
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        body = {"name": "editor-http", "root": {"kind": "transform", "id": "draft", "config": {"expr": "one"}}, "provenance": "user"}
        created = await client.post("/api/workflows", json=body)
        assert created.status == 201
        read = await client.get("/api/workflows/editor-http")
        opened = await read.json()
        assert opened["definition"]["version"] == 1 and opened["revision"]
        edited = {**body, "root": {"kind": "transform", "id": "draft", "config": {"expr": "two"}}, "expected_revision": 1, "based_on": "editor-http"}
        no_base = await client.post("/api/workflows", json=edited)
        assert no_base.status == 428
        current = await client.post("/api/workflows", json=edited, headers={"If-Match": f'"{opened["revision"]}"'})
        assert current.status == 201
        copied = await client.post("/api/workflows", json={**edited, "name": "editor-http", "create_only": True, "expected_revision": None}, headers={})
        assert copied.status == 409
