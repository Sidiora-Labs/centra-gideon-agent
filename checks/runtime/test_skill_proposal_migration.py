import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.skills import loader, marketplace, overlays, proposals
from gideon.integrations.inbox import InboxItem, InboxStore
from gideon.interfaces.dashboard.handlers import skills


@pytest.fixture
async def queue(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_PROJECT_DIR", str(project))
    root = project / "skills"
    monkeypatch.setattr(marketplace, "SKILL_DISCOVERY_PATHS", [root, home / "skills"])
    monkeypatch.setattr(
        "gideon.engine.agent._all_skill_paths",
        lambda: [str(root), str(home / "skills")],
    )
    app = web.Application()
    app.router.add_get("/proposals", skills.api_skill_proposals_list)
    app.router.add_get("/proposals/{id}", skills.api_skill_proposal_detail)
    app.router.add_post("/proposals/{id}/accept", skills.api_skill_proposal_accept)
    app.router.add_delete("/proposals/{id}", skills.api_skill_proposal_reject)
    app.router.add_get("/skills", skills.api_skills_list)
    app.router.add_post("/revert", skills.api_skill_overlay_revert)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, root, home
    await client.close()


def proposal(name, *, kind="refine", target=None):
    at = "2026-10-06T10:00:00+00:00"
    return proposals.SkillProposal(
        proposals._make_id(name, "session", at),
        name,
        "Improvement",
        "When useful",
        "Accepted guidance",
        "session",
        at,
        kind=kind,
        refine_target=target or name,
    )


@pytest.mark.asyncio
async def test_legacy_namespaced_proposal_migrates_inbox_and_accepts_selected_copy(
    queue,
):
    client, root, home = queue
    name = "imported/source/sample"
    marketplace.install_skill_files(
        [
            {
                "path": "SKILL.md",
                "contents": "---\nname: sample\ndescription: Sample\n---\nOwner text\n",
            }
        ],
        "sample",
        root / "imported/source",
    )
    base = root / name / "SKILL.md"
    lock = base.parent / ".gideon-lock.json"
    marketplace.write_install_record(
        base.parent,
        skill_id="sample",
        source="local fixture",
        trust_tier="trusted",
        verdict="allow",
        sha256=marketplace.file_digests(base.parent),
    )
    original, locked = base.read_bytes(), lock.read_bytes()
    identity = loader.overlay_identity(root, name)
    overlays.apply_overlay(identity, procedure_md="Earlier accepted addition")
    prop = proposal(name)
    old_id = (
        name
        + "-"
        + hashlib.sha1(f"{name}|session|{prop.created_at}".encode()).hexdigest()[:12]
    )
    prop.id = old_id
    old_path = proposals._proposals_dir() / (old_id + ".json")
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text(json.dumps(prop.to_dict()))
    store = InboxStore()
    store.load()
    item = InboxItem(
        "standing-request",
        "skills",
        "skills",
        None,
        "Original request",
        "system",
        "System",
        refs={"skill_proposal": old_id, "dedup_key": f"skill_proposal:{old_id}"},
        status="seen",
    )
    store.add(item)
    store.save()
    response = await client.get("/proposals")
    assert response.status == 200
    row = (await response.json())["proposals"][0]
    pid = row["id"]
    assert "/" not in pid and pid != old_id and row["slug"] == name
    assert not old_path.exists() and proposals._path(pid).exists()
    store = InboxStore()
    store.load()
    assert list(store.items) == ["standing-request"]
    assert store.items[item.id].refs["skill_proposal"] == pid
    assert store.items[item.id].refs["dedup_key"] == f"skill_proposal:{pid}"
    assert store.items[item.id].status == "seen"
    await client.get("/proposals")
    store = InboxStore()
    store.load()
    assert len(store.items) == 1
    assert (
        proposals.enqueue(
            slug=name,
            description="Duplicate",
            triggers="x",
            procedure_md="x",
            session_key="later",
            created_at=prop.created_at,
            kind="refine",
            refine_target=name,
        )
        is None
    )
    detail = await (await client.get("/proposals/" + pid)).json()
    assert detail["version"] == 2
    assert "v2" in detail["diff"] or "version 2" in detail["diff"].lower()
    accepted = await client.post(
        "/proposals/" + pid + "/accept",
        json={"procedure_md": "Owner reviewed addition"},
    )
    assert accepted.status == 200, await accepted.text()
    assert (await accepted.json())["version"] == 2
    assert base.read_bytes() == original and lock.read_bytes() == locked
    applied = overlays.applied(identity)
    assert (
        len(applied) == 2
        and applied[1].record["procedure_md"] == "Owner reviewed addition"
    )
    assert not overlays.applied(name)
    rows = await (await client.get("/skills")).json()
    selected = next(
        row for row in rows if row["name"] == name and row["path"] == str(base)
    )
    reverted = await client.post(
        "/revert",
        json={"name": name, "copy": selected["copy"], "refinement": applied[1].id},
    )
    assert reverted.status == 200
    assert [a.id for a in overlays.applied(identity)] == [applied[0].id]
    assert base.read_bytes() == original and lock.read_bytes() == locked


@pytest.mark.asyncio
async def test_removed_refine_target_creates_valid_auto_skill_and_unsafe_ids_are_confined(
    queue,
):
    client, root, home = queue
    for name in [
        "auto/missing",
        "imported/source/removed",
        "../../escape",
        "a" * 500,
        "a\\b",
        "a/b",
        "a-b",
    ]:
        prop = proposal(name)
        assert len(prop.id) <= 61 and "/" not in prop.id and "\\" not in prop.id
        path = proposals._path(prop.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prop.to_dict()))
        if name == "auto/missing":
            accepted = await client.post("/proposals/" + prop.id + "/accept", json={})
            assert accepted.status == 200, await accepted.text()
            assert (await accepted.json())["name"] == "auto/missing"
        elif name == "imported/source/removed":
            accepted = await client.post("/proposals/" + prop.id + "/accept", json={})
            assert accepted.status == 200, await accepted.text()
            assert (await accepted.json())["name"] == "auto/imported-source-removed"
        else:
            assert (await client.delete("/proposals/" + prop.id)).status == 200
    assert proposals._make_id("a/b", "s", "at") != proposals._make_id("a-b", "s", "at")
    assert not (home.parent / "escape.json").exists()
