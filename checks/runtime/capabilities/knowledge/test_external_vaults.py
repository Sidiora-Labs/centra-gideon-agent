import json
import os
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_vaults import register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.external_vaults import (
    ExternalVaults,
    digest,
    note_path,
)


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    home = tmp_path / "home"
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    home.mkdir()
    allowed.mkdir()
    other.mkdir()
    (home / "config.json").write_text(
        json.dumps({"knowledge": {"external_vault_roots": [str(allowed)]}})
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    store = KnowledgeStore(str(home / "knowledge.db"))
    service = ExternalVaults(store, home)
    yield service, allowed, other
    store.close()


def register_vault(service, root, name="Research"):
    root.mkdir(exist_ok=True)
    return service.register({"name": name, "path": str(root)})


@pytest.mark.parametrize(
    "value", ["Note.md", "folder/Note.md", "many.parts/note.v1.md", "Unicode/世界.md"]
)
def test_note_path_accepts_safe_relative_markdown(value):
    assert note_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/abs.md",
        "../escape.md",
        "folder/../escape.md",
        "./note.md",
        "folder\\note.md",
        "note.txt",
        ".hidden.md",
        "folder/.secret.md",
        3,
        None,
    ],
)
def test_note_path_rejects_absolute_traversal_hidden_and_nonmarkdown(value):
    with pytest.raises(CaptureError, match="relative"):
        note_path(value)


def test_registration_uses_existing_canonical_source_registry(vaults):
    service, allowed, _ = vaults
    root = allowed / "one"
    registered = register_vault(service, root)
    source = service.store.get_source(registered["id"])
    assert source["provider"] == "external_vault"
    assert source["kind"] == "filesystem"
    assert source["spec"] == {"path": str(root.resolve())}
    assert source["item_type"] == "note"
    assert service.list()["items"][0]["note_count"] == 0
    assert service.list()["allowed_roots"] == [str(allowed.resolve())]


def test_registration_requires_explicit_allowed_root(vaults):
    service, allowed, other = vaults
    with pytest.raises(CaptureError, match="outside configured"):
        register_vault(service, other / "refused")
    nested = allowed / "nested" / "vault"
    nested.mkdir(parents=True)
    assert service.register({"name": "Nested", "path": str(nested)})["spec"][
        "path"
    ] == str(nested.resolve())


def test_duplicate_real_path_is_refused_even_through_alias(vaults):
    service, allowed, _ = vaults
    root = allowed / "actual"
    root.mkdir()
    alias = allowed / "alias"
    alias.symlink_to(root, target_is_directory=True)
    first = service.register({"name": "First", "path": str(root)})
    with pytest.raises(CaptureError, match="already registered"):
        service.register({"name": "Alias", "path": str(alias)})
    assert service.list()["items"][0]["id"] == first["id"]


def test_scan_indexes_real_markdown_into_canonical_items(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    (root / "Projects").mkdir()
    (root / "Home.md").write_text("# Home\n#start [[Project Alpha]]")
    (root / "Projects/Project Alpha.md").write_text(
        "# Project Alpha\n#work Actual plan"
    )
    source = service.register({"name": "Vault", "path": str(root)})
    result = service.scan(source["id"])
    assert result["total"] == 2
    assert result["deleted_refs"] == 0
    assert [note["path"] for note in result["notes"]] == [
        "Home.md",
        "Projects/Project Alpha.md",
    ]
    home = result["notes"][0]
    assert home["tags"] == ["start"]
    assert home["wikilinks"] == ["Project Alpha"]
    item = service.store.get_item(home["item_id"])
    assert item["source_id"] == source["id"]
    assert item["provider"] == "external_vault"
    assert item["content"] == "# Home\n#start [[Project Alpha]]"
    assert item["file_metadata"]["external_vault_path"] == "Home.md"
    assert home["source_link"] == "#/knowledge/item/" + item["id"]


def test_scan_skips_operational_directories_and_symlink_files(vaults):
    service, allowed, other = vaults
    root = allowed / "vault"
    root.mkdir()
    for directory in (".git", ".obsidian", ".trash", "node_modules"):
        (root / directory).mkdir()
        (root / directory / "Ignored.md").write_text("ignored")
    outside = other / "Outside.md"
    outside.write_text("secret")
    (root / "Escape.md").symlink_to(outside)
    (root / "Good.md").write_text("visible")
    source = service.register({"name": "Vault", "path": str(root)})
    assert [note["path"] for note in service.scan(source["id"])["notes"]] == ["Good.md"]


def test_read_refuses_symlink_swap_after_index(vaults):
    service, allowed, other = vaults
    root = allowed / "vault"
    root.mkdir()
    note = root / "Note.md"
    note.write_text("safe")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    note.unlink()
    outside = other / "secret.md"
    outside.write_text("secret")
    note.symlink_to(outside)
    with pytest.raises(CaptureError, match="Symlink"):
        service.detail(source["id"], "Note.md")


def test_create_requires_existing_contained_parent(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    source = service.register({"name": "Vault", "path": str(root)})
    with pytest.raises(CaptureError, match="parent"):
        service.write(
            source["id"],
            {
                "request_id": "new-missing-parent",
                "path": "missing/New.md",
                "content": "text",
                "expected_hash": "",
            },
        )
    (root / "folder").mkdir()
    result = service.write(
        source["id"],
        {
            "request_id": "new-contained",
            "path": "folder/New.md",
            "content": "text",
            "expected_hash": "",
        },
    )
    assert result["created"] is True
    assert (root / "folder/New.md").read_text() == "text"


def test_atomic_update_requires_current_hash_and_preserves_external_change(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    path = root / "Note.md"
    path.write_text("one")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    opened = service.detail(source["id"], "Note.md")
    path.write_text("external two")
    with pytest.raises(CaptureError, match="changed"):
        service.write(
            source["id"],
            {
                "request_id": "stale-write",
                "path": "Note.md",
                "content": "mine",
                "expected_hash": opened["current_hash"],
            },
        )
    assert path.read_text() == "external two"
    assert not list(root.glob("*.tmp"))
    saved = service.write(
        source["id"],
        {
            "request_id": "fresh-write",
            "path": "Note.md",
            "content": "mine",
            "expected_hash": digest("external two"),
        },
    )
    assert saved["current_hash"] == digest("mine")
    assert path.read_text() == "mine"


def test_write_request_replay_is_immutable(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    (root / "Note.md").write_text("one")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    body = {
        "request_id": "repeat-write",
        "path": "Note.md",
        "content": "two",
        "expected_hash": digest("one"),
    }
    first = service.write(source["id"], body)
    second = service.write(source["id"], body)
    assert second == first
    with pytest.raises(CaptureError, match="different"):
        service.write(source["id"], {**body, "content": "changed"})
    assert (root / "Note.md").read_text() == "two"


def test_delete_conflict_preserves_file_and_success_archives_reference(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    path = root / "Note.md"
    path.write_text("one")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    opened = service.detail(source["id"], "Note.md")
    path.write_text("external")
    with pytest.raises(CaptureError, match="changed"):
        service.delete(
            source["id"],
            {
                "request_id": "stale-delete",
                "path": "Note.md",
                "expected_hash": opened["current_hash"],
            },
        )
    assert path.read_text() == "external"
    result = service.delete(
        source["id"],
        {
            "request_id": "fresh-delete",
            "path": "Note.md",
            "expected_hash": digest("external"),
        },
    )
    assert not path.exists()
    assert result["file_recreated"] is False
    item = service.store.get_item(result["item_id"])
    assert item["is_archived"] is True
    assert item["file_metadata"]["external_deleted"] is True
    assert service.scan(source["id"])["total"] == 0
    assert not path.exists()


def test_external_deletion_is_archived_and_never_resurrected(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    path = root / "Gone.md"
    path.write_text("gone")
    source = service.register({"name": "Vault", "path": str(root)})
    first = service.scan(source["id"])
    item_id = first["notes"][0]["item_id"]
    path.unlink()
    result = service.scan(source["id"])
    assert result["deleted_refs"] == 1
    assert result["notes"] == []
    assert service.store.get_item(item_id)["is_archived"] is True
    assert not path.exists()


def test_search_and_graph_use_live_content_and_resolve_within_vault(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    (root / "A.md").write_text("Alpha text [[B]] [[Missing]]")
    (root / "B.md").write_text("Beta text [[A]]")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    assert [row["path"] for row in service.search(source["id"], "beta")["results"]] == [
        "B.md"
    ]
    graph = service.graph(source["id"])
    assert len(graph["nodes"]) == 2
    assert {edge["resolved"] for edge in graph["edges"]} == {True, False}
    assert (
        next(edge for edge in graph["edges"] if edge["target"] == "B.md")["source"]
        == "A.md"
    )
    assert all(
        node["source_link"].startswith("#/knowledge/item/") for node in graph["nodes"]
    )


def test_unregister_disables_source_and_preserves_external_files(vaults):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    path = root / "Keep.md"
    path.write_text("keep")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    result = service.remove(source["id"])
    assert result["enabled"] is False
    assert result["preserved_items"] == 1
    assert path.read_text() == "keep"
    assert service.store.get_source(source["id"])["enabled"] is False
    assert service.list()["items"][0]["enabled"] is False


def test_bound_home_refuses_new_registration_and_writes(vaults, monkeypatch, tmp_path):
    service, allowed, _ = vaults
    root = allowed / "vault"
    root.mkdir()
    (root / "Note.md").write_text("one")
    source = service.register({"name": "Vault", "path": str(root)})
    service.scan(source["id"])
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "foreign"))
    with pytest.raises(CaptureError, match="Runtime home changed"):
        service.write(
            source["id"],
            {
                "request_id": "foreign-write",
                "path": "Note.md",
                "content": "two",
                "expected_hash": digest("one"),
            },
        )
    assert (root / "Note.md").read_text() == "one"


@pytest.mark.asyncio
async def test_http_register_scan_read_conflict_search_graph_and_delete(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    allowed = tmp_path / "allowed"
    vault = allowed / "vault"
    home.mkdir()
    vault.mkdir(parents=True)
    (vault / "Home.md").write_text("# Home [[Other]] #root")
    (vault / "Other.md").write_text("Other")
    (home / "config.json").write_text(
        json.dumps({"knowledge": {"external_vault_roots": [str(allowed)]}})
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    store = KnowledgeStore(str(home / "knowledge.db"))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"X-Session-Key": "dashboard:ui"}
    try:
        registered = await (
            await client.post(
                "/api/capabilities/knowledge/vaults/register",
                json={"name": "HTTP", "path": str(vault)},
                headers=headers,
            )
        ).json()
        scan = await (
            await client.post(
                f"/api/capabilities/knowledge/vaults/{registered['id']}/scan",
                headers=headers,
            )
        ).json()
        assert scan["total"] == 2
        opened = await (
            await client.post(
                f"/api/capabilities/knowledge/vaults/{registered['id']}/read",
                json={"path": "Home.md"},
                headers=headers,
            )
        ).json()
        assert opened["tags"] == ["root"]
        conflict = await client.post(
            f"/api/capabilities/knowledge/vaults/{registered['id']}/write",
            json={
                "request_id": "bad-http",
                "path": "Home.md",
                "content": "new",
                "expected_hash": "wrong",
            },
            headers=headers,
        )
        assert conflict.status == 409
        search = await (
            await client.post(
                f"/api/capabilities/knowledge/vaults/{registered['id']}/search",
                json={"query": "other"},
                headers=headers,
            )
        ).json()
        assert len(search["results"]) == 2
        graph = await (
            await client.get(
                f"/api/capabilities/knowledge/vaults/{registered['id']}/graph",
                headers=headers,
            )
        ).json()
        assert graph["edges"][0]["resolved"] is True
        deleted = await (
            await client.post(
                f"/api/capabilities/knowledge/vaults/{registered['id']}/delete",
                json={
                    "request_id": "delete-http",
                    "path": "Home.md",
                    "expected_hash": opened["current_hash"],
                },
                headers=headers,
            )
        ).json()
        assert deleted["deleted"] == "Home.md"
        assert (
            await client.get(
                "/api/capabilities/knowledge/vaults?path=/tmp", headers=headers
            )
        ).status == 400
    finally:
        await client.close()
        store.close()
