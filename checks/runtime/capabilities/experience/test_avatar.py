import hashlib
import json
import struct
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.assets.build_robot import build_robot
from gideon.workspace.capabilities.experience.avatar import (
    STATES,
    AvatarStore,
    avatar_info,
)
from gideon.workspace.capabilities.experience.store import Conflict, NotFound
from gideon.workspace.capabilities.experience.tools import ExperienceTools


def mutate_glb(data, change):
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + length])
    change(document)
    body = json.dumps(document, separators=(",", ":")).encode()
    body += b" " * ((-len(body)) % 4)
    remaining = data[20 + length :]
    return (
        struct.pack("<III", 0x46546C67, 2, 20 + len(body) + len(remaining))
        + struct.pack("<II", len(body), 0x4E4F534A)
        + body
        + remaining
    )


@pytest.fixture
def avatars(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return AvatarStore(ExperienceStore(tmp_path))


def test_original_asset_is_reproducible_actual_geometry_with_real_clips():
    data = build_robot()
    path = (
        Path(__file__).resolve().parents[4]
        / "runtime/gideon/workspace/capabilities/experience/assets/robot.glb"
    )
    assert data == path.read_bytes()
    info = avatar_info(data)
    assert info["meshes"] == 3
    assert info["animations"] == list(STATES)
    assert data[:4] == b"glTF"
    assert struct.unpack_from("<I", data, 8)[0] == len(data)
    document = json.loads(data[20 : 20 + struct.unpack_from("<I", data, 12)[0]])
    assert len(document["nodes"]) == 11
    assert document["nodes"][3]["name"] == "Left shoulder"
    assert document["animations"][1]["channels"][0]["target"]["node"] == 3
    assert document["buffers"][0]["byteLength"] > 0


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.update(animations=[]),
        lambda doc: doc.update(animations=[None]),
        lambda doc: doc["animations"][0].update(channels=[None]),
        lambda doc: doc["animations"][0].update(samplers=[None]),
        lambda doc: doc.update(nodes={}),
        lambda doc: doc["animations"][0].update(channels=[]),
        lambda doc: doc["animations"][0].update(name=""),
        lambda doc: doc["animations"][1].update(name="idle"),
        lambda doc: doc["animations"][0]["channels"][0]["target"].update(node=999),
        lambda doc: doc["animations"][0]["channels"][0]["target"].update(
            path="imagined"
        ),
        lambda doc: doc["animations"][0]["channels"][0].update(sampler=999),
        lambda doc: doc["animations"][0]["samplers"][0].update(input=999),
        lambda doc: doc["buffers"][0].update(uri="https://external.test/model.bin"),
    ],
)
def test_static_invalid_and_external_models_cannot_be_avatar_sources(change):
    with pytest.raises(ValueError):
        avatar_info(mutate_glb(build_robot(), change))


def test_bundled_installs_once_persists_selection_and_pins_actual_bytes(
    avatars, tmp_path
):
    assert avatars.list() == []
    assert avatars.selection() == {"revision": 1, "avatar_id": None, "entity_id": ""}
    installed = avatars.bundled()
    assert installed["availability"] == "ready"
    assert installed["source_hash"] == hashlib.sha256(build_robot()).hexdigest()
    assert installed["clips"] == {name: name for name in STATES}
    assert avatars.bundled() == installed
    assert len(avatars.artifacts.list(kind="model")) == 1
    assert (
        avatars.raw(installed["artifact_slug"], installed["artifact_version"])
        == build_robot()
    )
    selected = avatars.select(
        {
            "revision": 1,
            "avatar_id": installed["id"],
            "entity_id": "session:dashboard:ui",
        }
    )
    assert selected["revision"] == 2
    restarted = AvatarStore(ExperienceStore(tmp_path))
    assert restarted.selection() == selected
    assert restarted.list() == [installed]
    with pytest.raises(Conflict):
        restarted.select({"revision": 1, "avatar_id": None, "entity_id": ""})
    assert restarted.selection() == selected


def test_only_animated_actual_model_versions_enter_candidate_list(avatars):
    static = mutate_glb(build_robot(), lambda doc: doc.update(animations=[]))
    avatars.artifacts.create_binary(
        name="Static model",
        data=static,
        mime="model/gltf-binary",
        kind="model",
        source="manual",
    )
    invalid = avatars.artifacts.create_binary(
        name="Broken model",
        data=b"not glb",
        mime="model/gltf-binary",
        kind="model",
        source="manual",
    )
    assert avatars.models() == []
    with pytest.raises(ValueError):
        avatars.publish(
            {
                "title": "Broken",
                "artifact_slug": invalid.slug,
                "artifact_version": 1,
                "clips": {"idle": "idle"},
            }
        )
    installed = avatars.bundled()
    candidates = avatars.models()
    assert len(candidates) == 1
    assert candidates[0]["slug"] == installed["artifact_slug"]
    assert candidates[0]["clips"] == list(STATES)


def test_clip_mapping_and_immutable_version_references(avatars):
    installed = avatars.bundled()
    body = {
        "title": "Quiet robot",
        "artifact_slug": installed["artifact_slug"],
        "artifact_version": 1,
        "clips": {"idle": "idle"},
    }
    published = avatars.publish(body)
    assert published["clips"] == {"idle": "idle"}
    assert avatars.publish(body) == published
    for mapping in ({}, {"idle": "missing"}, {"idle": "idle", "invented": "working"}):
        with pytest.raises(ValueError):
            avatars.publish({**body, "clips": mapping})
    changed = mutate_glb(
        build_robot(), lambda doc: doc["nodes"][0].update(name="Revised robot")
    )
    avatars.artifacts.update_binary(
        installed["artifact_slug"],
        data=changed,
        mime="model/gltf-binary",
        expect_version=1,
    )
    assert avatars.raw(installed["artifact_slug"], 1) == build_robot()
    assert (
        avatars.artifacts.raw_bytes(installed["artifact_slug"], version=2)[0] == changed
    )
    with pytest.raises(NotFound):
        avatars.raw(installed["artifact_slug"], 2)
    assert all(row["availability"] == "ready" for row in avatars.list())


def test_title_spoof_cannot_replace_original_bundled_asset(avatars):
    alternate = mutate_glb(
        build_robot(), lambda doc: doc["nodes"][0].update(name="Alternate author")
    )
    art = avatars.artifacts.create_binary(
        name="Other model",
        data=alternate,
        mime="model/gltf-binary",
        kind="model",
        source="manual",
    )
    other = avatars.publish(
        {
            "title": "Gideon robot",
            "artifact_slug": art.slug,
            "artifact_version": art.version,
            "clips": {name: name for name in STATES},
        }
    )
    installed = avatars.bundled()
    assert installed["id"] != other["id"]
    assert installed["source_hash"] == hashlib.sha256(build_robot()).hexdigest()


def test_missing_artifact_disables_variant_and_other_home_has_no_source(
    avatars, tmp_path
):
    installed = avatars.bundled()
    other = AvatarStore(ExperienceStore(tmp_path / "other"))
    assert other.list() == []
    with pytest.raises(NotFound):
        other.raw(installed["artifact_slug"], 1)
    avatars.artifacts.delete(installed["artifact_slug"])
    assert avatars.list()[0]["availability"] == "missing"
    with pytest.raises(NotFound):
        avatars.select({"revision": 1, "avatar_id": installed["id"], "entity_id": ""})
    assert avatars.selection()["avatar_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"revision": 1, "avatar_id": None, "entity_id": "x" * 301},
        {"revision": 1, "avatar_id": None, "entity_id": "", "home": "other"},
    ],
)
def test_invalid_selection_never_mutates(avatars, body):
    before = avatars.selection()
    with pytest.raises(ValueError):
        avatars.select(body)
    assert avatars.selection() == before


@pytest.mark.asyncio
async def test_actual_http_and_native_provider_share_avatar_selection(avatars):
    app = web.Application()
    app[STORE] = avatars.store
    register(app)
    provider = ExperienceTools(avatars.store)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/experience"
        response = await client.post(base + "/avatars/bundled", json={})
        assert response.status == 201
        installed = (await response.json())["avatar"]
        result = await provider.invoke("experience_avatar_list", {})
        assert result.success
        assert json.loads(result.output)["avatars"] == [installed]
        select = await provider.invoke(
            "experience_avatar_select",
            {"revision": 1, "avatar_id": installed["id"], "entity_id": "loop:actual"},
        )
        assert select.success
        current = await client.get(base + "/avatar-selection")
        assert (await current.json())["selection"]["entity_id"] == "loop:actual"
        raw = await client.get(
            base + f"/avatar-assets/{installed['artifact_slug']}/1/raw"
        )
        assert raw.status == 200
        assert raw.headers["Content-Type"] == "model/gltf-binary"
        assert await raw.read() == build_robot()
        assert (
            await client.get(
                base + f"/avatar-assets/{installed['artifact_slug']}/999/raw"
            )
        ).status == 404
        assert (
            await client.post(base + "/avatars/bundled", json={"provider": "imagined"})
        ).status == 400
        assert (
            await client.put(
                base + "/avatar-selection",
                json={"revision": 1, "avatar_id": None, "entity_id": ""},
            )
        ).status == 409
        models = await client.get(base + "/avatar-models")
        assert len((await models.json())["models"]) == 1


def test_concurrent_identical_publication_has_one_durable_variant(avatars):
    from concurrent.futures import ThreadPoolExecutor

    original = avatars.bundled()
    body = {
        "title": "Shared variant",
        "artifact_slug": original["artifact_slug"],
        "artifact_version": 1,
        "clips": {"idle": "idle", "working": "working"},
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: avatars.publish(body), range(8)))
    assert len({row["id"] for row in rows}) == 1
    assert len(avatars.list()) == 2
    assert rows[0]["availability"] == "ready"
    assert avatars.raw(original["artifact_slug"], 1) == build_robot()


def test_tampered_pinned_source_cannot_remain_ready(avatars):
    original = avatars.bundled()
    path = avatars.artifacts._binary_version_path(original["artifact_slug"], 1)
    assert path is not None
    altered = mutate_glb(
        build_robot(),
        lambda doc: doc["nodes"][0].update(name="Tampered after publication"),
    )
    path.write_bytes(altered)
    assert avatars.list()[0]["availability"] == "missing"
    with pytest.raises(NotFound):
        avatars.raw(original["artifact_slug"], 1)
    with pytest.raises(NotFound):
        avatars.select({"revision": 1, "avatar_id": original["id"], "entity_id": ""})
    assert avatars.selection()["revision"] == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"title": ""},
        {"artifact_version": True},
        {"artifact_version": 0},
        {"home": "/other"},
        {"clips": []},
        {"clips": {"idle": "imagined"}},
    ],
)
def test_malformed_publication_is_not_registered(avatars, patch):
    original = avatars.bundled()
    before = avatars.list()
    body = {
        "title": "Variant",
        "artifact_slug": original["artifact_slug"],
        "artifact_version": 1,
        "clips": {"idle": "idle"},
    }
    with pytest.raises(ValueError):
        avatars.publish({**body, **patch})
    assert avatars.list() == before


@pytest.mark.asyncio
async def test_native_publication_and_selection_use_authoritative_registry(avatars):
    provider = ExperienceTools(avatars.store)
    installed = await provider.invoke("experience_avatar_bundled", {})
    assert installed.success
    original = json.loads(installed.output)["avatar"]
    published = await provider.invoke(
        "experience_avatar_publish",
        {
            "title": "Agent variant",
            "artifact_slug": original["artifact_slug"],
            "artifact_version": 1,
            "clips": {"idle": "idle", "error": "error"},
        },
    )
    assert published.success
    variant = json.loads(published.output)["avatar"]
    selected = await provider.invoke(
        "experience_avatar_select",
        {"revision": 1, "avatar_id": variant["id"], "entity_id": "session:live"},
    )
    assert selected.success
    reread = await provider.invoke("experience_avatar_selection_get", {})
    assert json.loads(reread.output) == json.loads(selected.output)
    assert avatars.selection()["avatar_id"] == variant["id"]
    missing = await provider.invoke(
        "experience_avatar_select",
        {"revision": 2, "avatar_id": "missing", "entity_id": ""},
    )
    assert not missing.success
    assert missing.metadata == {"kind": "NotFound"}
    assert avatars.selection()["revision"] == 2


def test_concurrent_selection_requires_current_revision(avatars):
    from concurrent.futures import ThreadPoolExecutor

    first = avatars.bundled()
    second = avatars.publish(
        {
            "title": "Alternative",
            "artifact_slug": first["artifact_slug"],
            "artifact_version": 1,
            "clips": {"idle": "idle"},
        }
    )

    def select(avatar):
        try:
            return avatars.select(
                {"revision": 1, "avatar_id": avatar["id"], "entity_id": ""}
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, [first, second]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["revision"] == 2
    assert avatars.selection() == winners[0]
    reopened = AvatarStore(ExperienceStore(avatars.store.path.parent.parent))
    assert reopened.selection() == winners[0]
