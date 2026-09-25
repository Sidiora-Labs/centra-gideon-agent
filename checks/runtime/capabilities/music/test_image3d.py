import base64
import io
import json
import sqlite3
import struct

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from gideon.interfaces.dashboard.handlers.capabilities_music_models3d import register
from gideon.sdk.credentials import CredentialStore
from gideon.workspace.artifacts.models import ext_for_mime, kind_for_mime, mime_for_ext
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.image3d import (
    ENDPOINT,
    MODELS,
    Image3DStore,
    asset_url,
    validate_glb,
)
from gideon.workspace.capabilities.music.models3d_tools import Image3DTools
from gideon.workspace.capabilities.music.store import DomainError


def glb(document=None):
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = (
        document
        if document is not None
        else {
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
    )
    encoded = json.dumps(document).encode()
    encoded += b" " * ((-len(encoded)) % 4)
    body = (
        struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(positions), 0x004E4942)
        + positions
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def service_at(home):
    return Image3DStore(home, NativeArtifactProvider(root=home / "artifacts"))


def source(service):
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(output, format="PNG")
    artifact = service.artifacts.create_binary(
        name="Authored input image",
        data=output.getvalue(),
        kind="image",
        mime="image/png",
        source="manual",
    )
    return {
        "request_id": "asset-request",
        "title": "Reconstructed object",
        "image_ref": {"slug": artifact.slug, "version": artifact.version},
        "target_polycount": 30000,
        "should_texture": True,
        "license": "Operator to verify provider account rights",
    }


def seed_interrupted(service, request):
    job = {
        "id": request["request_id"],
        "status": "interrupted",
        "request": request,
        "model": "meshy-6",
        "credential_name": "missing",
        "provider_task_id": None,
        "progress": 0,
        "artifact_ref": None,
        "error": "Prior runtime ended during submission",
    }
    with sqlite3.connect(service.path) as db:
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?)",
            (job["id"], json.dumps(request, sort_keys=True), json.dumps(job)),
        )
    return job


def test_disabled_default_configuration_never_claims_remote_availability(tmp_path):
    service = service_at(tmp_path)
    ready = service.readiness()
    assert ready["config"] == {
        "enabled": False,
        "credential_name": "",
        "model": "meshy-6",
        "revision": 0,
    }
    assert ready["credential_available"] is False
    assert ready["ready_to_submit"] is False
    assert ready["remote_status"] == "unverified"
    assert ready["provider"] == "meshy"
    assert service.list() == []
    assert ENDPOINT == "https://api.meshy.ai/openapi/v1/image-to-3d"
    with sqlite3.connect(service.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_canonical_named_credential_readiness_reopens_without_copying_secret(tmp_path):
    credentials = CredentialStore(tmp_path)
    credentials.save(
        {"geometry-key": {"type": "api_key", "value": "local-test-secret-never-sent"}}
    )
    service = service_at(tmp_path)
    config = service.configure(
        {
            "enabled": True,
            "credential_name": "geometry-key",
            "model": "meshy-6",
            "revision": 0,
        }
    )
    assert config["revision"] == 1
    assert service_at(tmp_path).config() == config
    ready = service_at(tmp_path).readiness()
    assert ready["ready_to_submit"] is True
    assert ready["remote_status"] == "unverified"
    assert "local-test-secret-never-sent" not in json.dumps(ready)
    assert b"local-test-secret-never-sent" not in service.path.read_bytes()
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    credentials.save({})
    assert service.readiness()["credential_available"] is False
    assert service.config() == config


def test_configuration_conflict_preserves_saved_model_and_alias(tmp_path):
    service = service_at(tmp_path)
    first = service.configure(
        {
            "enabled": False,
            "credential_name": "first",
            "model": "meshy-6-lite",
            "revision": 0,
        }
    )
    with pytest.raises(DomainError) as err:
        service.configure(
            {
                "enabled": True,
                "credential_name": "second",
                "model": "meshy-7.1",
                "revision": 0,
            }
        )
    assert err.value.code == "revision_conflict"
    assert service_at(tmp_path).config() == first
    second = service.configure({**first, "model": "meshy-7.1"})
    assert second["revision"] == 2
    assert second["model"] == "meshy-7.1"


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"model": "caller-model"},
        {"model": "latest"},
        {"credential_name": None},
        {"revision": True},
        {"revision": -1},
        {"api_key": "secret"},
        {"endpoint": "http://localhost"},
        {"home": "/tmp"},
    ],
)
def test_untrusted_configuration_fields_refused(tmp_path, changes):
    service = service_at(tmp_path)
    data = {
        "enabled": False,
        "credential_name": "missing",
        "model": "meshy-6",
        "revision": 0,
    }
    with pytest.raises(DomainError):
        service.configure({**data, **changes})
    assert service.config()["revision"] == 0
    assert service.list() == []


def test_actual_canonical_input_bytes_become_documented_provider_payload(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    validated, raw = service.request(request)
    assert validated == request
    payload = service.payload(validated, raw, "meshy-6")
    mime, encoded = payload["image_url"].split(";base64,")
    assert mime == "data:image/png"
    assert base64.b64decode(encoded) == raw[0]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == (32, 32)
        assert image.getpixel((16, 16)) == (0, 128, 0)
    assert payload["ai_model"] == "meshy-6"
    assert payload["model_type"] == "standard"
    assert payload["should_remesh"] is True
    assert payload["target_polycount"] == 30000
    assert payload["should_texture"] is True
    assert payload["target_formats"] == ["glb"]
    assert "license" not in payload
    assert "request_id" not in payload


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": ""},
        {"title": ""},
        {"license": ""},
        {"target_polycount": 99},
        {"target_polycount": 300001},
        {"target_polycount": True},
        {"should_texture": 1},
        {"image_ref": {}},
        {"image_ref": {"slug": "missing", "version": 1}},
        {"image_ref": {"slug": "missing", "version": 0}},
        {"image_ref": {"slug": "missing", "version": 1, "path": "/tmp"}},
        {"endpoint": "https://other"},
        {"provider": "other"},
        {"model": "other"},
    ],
)
def test_invalid_job_requests_never_create_jobs(tmp_path, changes):
    service = service_at(tmp_path)
    request = source(service)
    with pytest.raises(DomainError):
        service.request({**request, **changes})
    assert service.list() == []


def test_image_source_version_is_pinned_and_foreign_home_is_refused(tmp_path):
    one = service_at(tmp_path / "one")
    request = source(one)
    two = service_at(tmp_path / "two")
    with pytest.raises(DomainError) as err:
        two.request(request)
    assert err.value.code == "invalid_image"
    ref = request["image_ref"]
    original = one.artifacts.raw_bytes(ref["slug"], version=1)
    assert one.request(request)[1] == original
    assert two.list() == []
    assert one.list() == []


@pytest.mark.asyncio
async def test_missing_credential_refuses_submission_without_network_or_job(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    for enabled in (False, True):
        service.configure(
            {
                "enabled": enabled,
                "credential_name": "missing",
                "model": "meshy-6",
                "revision": service.config()["revision"],
            }
        )
        with pytest.raises(DomainError) as err:
            await service.submit(request)
        assert err.value.status == 503
        assert err.value.code == "provider_unavailable"
        assert service.list() == []


@pytest.mark.asyncio
async def test_interrupted_request_replay_never_reissues_remote_submission(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    interrupted = seed_interrupted(service, request)
    assert await service.submit(request) == interrupted
    assert await service_at(tmp_path).submit(request) == interrupted
    with pytest.raises(DomainError) as err:
        await service.submit({**request, "title": "Different input"})
    assert err.value.code == "request_conflict"
    with pytest.raises(DomainError) as err:
        await service.refresh(request["request_id"])
    assert err.value.code == "submission_unknown"
    assert service.list() == [interrupted]
    assert interrupted["artifact_ref"] is None


@pytest.mark.asyncio
async def test_stopped_tracking_is_durable_and_not_remote_cancellation_claim(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    seed_interrupted(service, request)
    stopped = service.stop(request["request_id"])
    assert stopped["status"] == "stopped"
    assert "billing" in stopped["error"]
    assert stopped["artifact_ref"] is None
    assert service_at(tmp_path).get(request["request_id"]) == stopped
    assert service.stop(request["request_id"]) == stopped
    assert await service.refresh(request["request_id"]) == stopped
    assert await service.submit(request) == stopped


def test_glb_actual_triangle_roundtrips_canonical_model_kind_and_version(tmp_path):
    service = service_at(tmp_path)
    raw = glb()
    info = validate_glb(raw)
    assert info == {"meshes": 1, "animations": []}
    artifact = service.artifacts.create_binary(
        name="Original authored triangle",
        data=raw,
        mime="model/gltf-binary",
        kind="model",
        source="manual",
    )
    assert artifact.kind == "model"
    assert artifact.version == 1
    assert kind_for_mime("model/gltf-binary") == "model"
    assert mime_for_ext("glb") == "model/gltf-binary"
    assert ext_for_mime("model/gltf-binary") == "glb"
    assert service.artifacts.raw_bytes(artifact.slug, version=1) == (
        raw,
        "model/gltf-binary",
    )
    assert service_at(tmp_path).artifacts.get(artifact.slug, version=1).kind == "model"
    assert service.artifacts.raw_bytes(artifact.slug, version=2) is None
    assert service.list() == []


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not GLB",
        glb()[:-1],
        b"glTF" + struct.pack("<II", 1, 20) + b" " * 8,
        glb({"asset": None}),
        glb({"asset": {"version": "2.0"}, "meshes": []}),
        glb({"asset": {"version": "1.0"}, "meshes": [{}]}),
        glb({"asset": {"version": "2.0"}, "meshes": [{}], "buffers": [None]}),
        glb({"asset": {"version": "2.0"}, "meshes": [{}], "images": [{"uri": 123}]}),
    ],
)
def test_malformed_model_input_returns_domain_error(raw):
    with pytest.raises(DomainError) as err:
        validate_glb(raw)
    assert err.value.code == "invalid_model"


@pytest.mark.parametrize("collection", ["buffers", "images"])
@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/image",
        "file:///etc/passwd",
        "../image.png",
        "//example.test/x",
    ],
)
def test_model_external_resources_rejected_before_viewer_loading(collection, uri):
    raw = glb({"asset": {"version": "2.0"}, "meshes": [{}], collection: [{"uri": uri}]})
    with pytest.raises(DomainError) as err:
        validate_glb(raw)
    assert err.value.code == "external_model_resource"


def test_invalid_chunk_length_and_duplicate_json_are_refused():
    raw = bytearray(glb())
    struct.pack_into("<I", raw, 12, 0xFFFFFFFF)
    with pytest.raises(DomainError):
        validate_glb(bytes(raw))
    original = glb()
    json_size = struct.unpack_from("<I", original, 12)[0]
    duplicate = original + original[12 : 20 + json_size]
    duplicate = duplicate[:8] + struct.pack("<I", len(duplicate)) + duplicate[12:]
    with pytest.raises(DomainError):
        validate_glb(duplicate)


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.meshy.ai/model.glb",
        "https://localhost/model.glb",
        "https://assets.meshy.ai.evil.test/model.glb",
        "file:///model.glb",
        "https://name:secret@assets.meshy.ai/model.glb",
        "https://assets.meshy.ai:9443/model.glb",
        "https://assets.meshy.ai/model.glb#fragment",
    ],
)
def test_provider_download_url_has_fixed_https_origin(url):
    with pytest.raises(DomainError):
        asset_url(url)
    assert (
        asset_url("https://assets.meshy.ai/tasks/a/model.glb?Expires=123")
        == "https://assets.meshy.ai/tasks/a/model.glb?Expires=123"
    )


@pytest.mark.asyncio
async def test_real_http_settings_refusal_jobs_and_pinned_raw_model(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    model = service.artifacts.create_binary(
        name="Authored HTTP triangle",
        data=glb(),
        mime="model/gltf-binary",
        kind="model",
        source="manual",
    )
    app = web.Application()
    register(app, service)
    prefix = "/api/capabilities/music/models3d"
    async with TestClient(TestServer(app)) as client:
        config = await client.get(prefix + "/config")
        assert (await config.json())["config"]["revision"] == 0
        saved = await client.patch(
            prefix + "/config",
            json={
                "enabled": True,
                "credential_name": "missing",
                "model": "meshy-6",
                "revision": 0,
            },
        )
        assert saved.status == 200
        ready = await client.get(prefix + "/readiness")
        assert (await ready.json())["ready_to_submit"] is False
        refused = await client.post(prefix + "/jobs", json=request)
        assert refused.status == 503
        listing = await client.get(prefix + "/jobs")
        assert (await listing.json()) == {"jobs": []}
        raw = await client.get(prefix + f"/artifacts/{model.slug}/1/raw")
        assert raw.status == 200
        assert raw.content_type == "model/gltf-binary"
        assert await raw.read() == glb()
        missing = await client.get(prefix + f"/artifacts/{model.slug}/2/raw")
        assert missing.status == 404
        wrong = await client.get(
            prefix + f"/artifacts/{request['image_ref']['slug']}/1/raw"
        )
        assert wrong.status == 404
        seed_interrupted(service, request)
        stopped = await client.post(
            prefix + "/jobs/" + request["request_id"] + "/stop", json={}
        )
        assert (await stopped.json())["job"]["status"] == "stopped"
        bad = await client.post(
            prefix + "/jobs/" + request["request_id"] + "/refresh",
            json={"home": "/tmp"},
        )
        assert bad.status == 400
    assert service_at(tmp_path).config()["enabled"] is True


@pytest.mark.asyncio
async def test_real_native_operations_keep_provider_unavailable_honest(tmp_path):
    service = service_at(tmp_path)
    request = source(service)
    tools = Image3DTools(service)
    assert len(await tools.list_tools()) == 8
    ready = await tools.invoke("music_models3d_readiness", {})
    assert ready.success and json.loads(ready.output)["ready_to_submit"] is False
    config = await tools.invoke("music_models3d_config", {})
    assert json.loads(config.output)["revision"] == 0
    saved = await tools.invoke(
        "music_models3d_configure",
        {
            "data": {
                "enabled": True,
                "credential_name": "missing",
                "model": "meshy-6",
                "revision": 0,
            }
        },
    )
    assert saved.success
    refused = await tools.invoke("music_models3d_submit", {"data": request})
    assert not refused.success
    assert refused.metadata["status"] == 503
    seed_interrupted(service, request)
    fetched = await tools.invoke("music_models3d_get", {"id": request["request_id"]})
    assert json.loads(fetched.output)["artifact_ref"] is None
    refreshed = await tools.invoke(
        "music_models3d_refresh", {"id": request["request_id"]}
    )
    assert not refreshed.success
    stopped = await tools.invoke("music_models3d_stop", {"id": request["request_id"]})
    assert json.loads(stopped.output)["status"] == "stopped"
    listed = await tools.invoke("music_models3d_list", {})
    assert len(json.loads(listed.output)) == 1
    denied = await tools.invoke("music_models3d_config", {"home": "/tmp"})
    assert not denied.success
