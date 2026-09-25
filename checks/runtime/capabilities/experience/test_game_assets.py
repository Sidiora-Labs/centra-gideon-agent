import io
import json
import os
import struct
import wave

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.extensions.apps import app_manager, manager
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.game_assets import GameAssets
from gideon.workspace.capabilities.experience.game_assets_http import (
    register_game_assets,
)
from gideon.workspace.capabilities.experience.game_assets_provider import GameAssetTools
from gideon.workspace.capabilities.experience.store import Conflict
from gideon.workspace.capabilities.experience.world_foundations import WorldFoundations


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 80)
    return output.getvalue()


def glb():
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [1, 1, 0],
            }
        ],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * ((-len(encoded)) % 4)
    body = (
        struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(positions), 0x004E4942)
        + positions
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


@pytest.fixture
def game(tmp_path, monkeypatch):
    import gideon.core.config.loader as loader

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    source = tmp_path / "source" / "real-game-app"
    source.mkdir(parents=True)
    (source / "app.json").write_text(
        json.dumps(
            {
                "name": "real-game-app",
                "version": "1.0.0",
                "displayName": "Real game",
                "description": "Consumes compiled game assets",
                "permissions": {"storage": True},
            }
        )
    )
    installed = app_manager.install(source, confirm=True)
    assert installed.ok
    store = ExperienceStore(tmp_path)
    foundation_service = WorldFoundations(store)
    foundation = foundation_service.record(
        {
            "title": "Arcade foundation",
            "controller": {"kind": "ambient_beacon", "position": [0, 0, 0]},
            "style": {"private": "local"},
        }
    )
    foundation = foundation_service.package(foundation["id"], foundation["revision"])
    foundation = foundation_service.promote(foundation["id"], foundation["revision"])
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    refs = {}
    values = {
        "sprite": ("Hero", png("red"), "image", "image/png"),
        "artwork": ("Backdrop", png("blue"), "image", "image/png"),
        "music": ("Theme", wav(), "audio", "audio/wav"),
        "model": ("Prop", glb(), "model", "model/gltf-binary"),
    }
    for role, (name, data, kind, mime) in values.items():
        artifact = artifacts.create_binary(
            name=name, data=data, kind=kind, mime=mime, source="manual"
        )
        refs[role] = {"slug": artifact.slug, "version": artifact.version}
    service = GameAssets(store, artifacts)
    project = service.create(
        {
            "title": "Runnable arcade",
            "app_id": "real-game-app",
            "foundation_id": foundation["id"],
        }
    )
    for role in ("sprite", "artwork", "music", "model"):
        project = service.bind(
            project["id"],
            {
                "revision": project["revision"],
                "role": role,
                "artifact_ref": refs[role],
                "label": role.title(),
            },
        )
    return service, project, refs, artifacts, foundation, tmp_path


def test_compile_runnable_export_reuses_inputs_and_publishes_verified_app_destination(
    game,
):
    service, project, _refs, artifacts, foundation, tmp_path = game
    compiled = service.compile(project["id"], project["revision"])
    assert compiled["created"] is True
    assert compiled["version"] == 1
    assert compiled["asset_count"] == 4
    export = artifacts.get(
        compiled["export_ref"]["slug"], version=compiled["export_ref"]["version"]
    )
    assert export is not None
    assert export.kind == "html"
    assert export.readonly is True
    assert "<canvas" in export.content
    assert "requestAnimationFrame(frame)" in export.content
    assert "new Audio(assets.music)" in export.content
    assert "assets.model" in export.content
    assert "globalThis.__GIDEON_GAME_MANIFEST__=manifest" in export.content
    assert foundation["fingerprint"] in export.content
    assert "private" not in export.content
    reopened = GameAssets(
        ExperienceStore(tmp_path), NativeArtifactProvider(tmp_path / "artifacts")
    )
    durable = reopened.get(project["id"])
    reused = reopened.compile(project["id"], durable["revision"])
    assert reused["created"] is False
    assert reused["version"] == 1
    assert reused["export_ref"] == compiled["export_ref"]
    durable = reopened.get(project["id"])
    receipt = reopened.publish(project["id"], durable["revision"])
    assert receipt["state"] == "verified"
    assert receipt["app_id"] == "real-game-app"
    destination = manager.app_data_dir("real-game-app") / receipt["destination"]
    assert destination.is_file()
    assert destination.read_text() == export.content
    assert receipt["destination_sha256"] == compiled["export_sha256"]
    persisted = reopened.get(project["id"])
    assert persisted["publication"] == receipt


def test_new_immutable_asset_version_creates_new_compile_version(game):
    service, project, refs, artifacts, _foundation, _tmp_path = game
    first = service.compile(project["id"], project["revision"])
    current = service.get(project["id"])
    updated = artifacts.update_binary(
        refs["sprite"]["slug"], data=png("green"), mime="image/png", expect_version=1
    )
    assert updated.version == 2
    current = service.bind(
        current["id"],
        {
            "revision": current["revision"],
            "role": "sprite",
            "artifact_ref": {"slug": updated.slug, "version": updated.version},
            "label": "Hero green",
        },
    )
    second = service.compile(current["id"], current["revision"])
    assert second["created"] is True
    assert second["version"] == 2
    assert second["input_sha256"] != first["input_sha256"]
    assert second["export_ref"] != first["export_ref"]
    history = service.get(project["id"])["compile_history"]
    assert [row["version"] for row in history] == [1, 2]
    original_bytes = artifacts.raw_bytes(refs["sprite"]["slug"], version=1)[0]
    changed_bytes = artifacts.raw_bytes(refs["sprite"]["slug"], version=2)[0]
    assert original_bytes == png("red")
    assert changed_bytes == png("green")


def test_missing_or_corrupted_exact_bytes_refuse_compilation_and_publication(game):
    service, project, refs, artifacts, _foundation, _tmp_path = game
    sprite_path = artifacts._binary_version_path(refs["sprite"]["slug"], 1)
    sprite_path.write_bytes(b"corrupt")
    with pytest.raises(Conflict, match="corrupt|integrity"):
        service.compile(project["id"], project["revision"])
    sprite_path.write_bytes(png("red"))
    compiled = service.compile(project["id"], project["revision"])
    current = service.get(project["id"])
    export_path = (
        artifacts._artifact_dir(compiled["export_ref"]["slug"]) / "versions" / "v1.html"
    )
    export_path.write_text("corrupt")
    with pytest.raises(Conflict, match="missing or corrupt"):
        service.publish(project["id"], current["revision"])
    assert service.get(project["id"])["publication"] is None


@pytest.mark.asyncio
async def test_http_and_native_operations_use_canonical_compiler(game):
    service, project, _refs, _artifacts, _foundation, _tmp_path = game
    app = web.Application()
    register_game_assets(app, service.store, service.artifacts)
    async with TestClient(TestServer(app)) as client:
        listed = await client.get("/api/capabilities/experience/game-assets/projects")
        assert listed.status == 200
        assert (await listed.json())["projects"][0]["id"] == project["id"]
        compiled = await client.post(
            f"/api/capabilities/experience/game-assets/projects/{project['id']}/compile",
            json={"revision": project["revision"]},
        )
        assert compiled.status == 200
        assert (await compiled.json())["compile"]["created"] is True
        current = service.get(project["id"])
        stale = await client.post(
            f"/api/capabilities/experience/game-assets/projects/{project['id']}/publish",
            json={"revision": project["revision"]},
        )
        assert stale.status == 409
        published = await client.post(
            f"/api/capabilities/experience/game-assets/projects/{project['id']}/publish",
            json={"revision": current["revision"]},
        )
        assert published.status == 200
        assert (await published.json())["publish"]["state"] == "verified"
    provider = GameAssetTools(service.store, service.artifacts)
    definitions = {
        definition.name: definition for definition in await provider.list_tools()
    }
    assert not definitions["experience_game_assets_get"].requires_approval
    assert definitions["experience_game_assets_compile"].requires_approval
    assert definitions["experience_game_assets_publish"].requires_approval
    result = await provider.invoke("experience_game_assets_get", {})
    assert result.success
    assert (
        json.loads(result.output)["projects"][0]["publication"]["state"] == "verified"
    )
    refused = await provider.invoke(
        "experience_game_assets_compile", {"id": project["id"]}
    )
    assert not refused.success


def test_validation_foundation_and_managed_app_boundaries(game):
    service, project, refs, artifacts, foundation, tmp_path = game
    for body in (
        None,
        [],
        {},
        {"title": "x", "app_id": "../bad", "foundation_id": foundation["id"]},
        {"title": "x", "app_id": "missing", "foundation_id": foundation["id"]},
        {"title": "x", "app_id": "real-game-app", "foundation_id": "missing"},
    ):
        with pytest.raises((ValueError, Conflict)):
            service.create(body)
    with pytest.raises(Conflict, match="revision"):
        service.bind(
            project["id"],
            {
                "revision": 1,
                "role": "sprite",
                "artifact_ref": refs["sprite"],
                "label": "stale",
            },
        )
    wrong = artifacts.create_binary(
        name="Wrong kind",
        data=png("white"),
        kind="image",
        mime="image/png",
        source="manual",
    )
    with pytest.raises(Conflict, match="music"):
        service.bind(
            project["id"],
            {
                "revision": project["revision"],
                "role": "music",
                "artifact_ref": {"slug": wrong.slug, "version": 1},
                "label": "wrong",
            },
        )
    incomplete = service.create(
        {
            "title": "Incomplete",
            "app_id": "real-game-app",
            "foundation_id": foundation["id"],
        }
    )
    with pytest.raises(Conflict, match="bindings"):
        service.compile(incomplete["id"], incomplete["revision"])
    app_manager.disable("real-game-app")
    with pytest.raises(Conflict, match="installed and enabled"):
        service.compile(project["id"], project["revision"])
    assert service.get(project["id"])["compiled"] is None


def test_withdrawn_foundation_blocks_export_without_erasing_bindings(game):
    service, project, _refs, _artifacts, foundation, _tmp_path = game
    foundations = WorldFoundations(service.store)
    withdrawal = foundations.withdraw(foundation["id"], foundation["revision"])
    assert withdrawal["fingerprint"] == foundation["fingerprint"]
    with pytest.raises(Conflict, match="promoted or adopted"):
        service.compile(project["id"], project["revision"])
    unchanged = service.get(project["id"])
    assert set(unchanged["bindings"]) == {"sprite", "artwork", "music", "model"}
    assert unchanged["compiled"] is None
    assert unchanged["publication"] is None
