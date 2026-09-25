import json
import sqlite3
import subprocess

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from checks.runtime.capabilities.music.test_catalog import (
    attach_body,
    catalog_at,
    wav_bytes,
)
from gideon.interfaces.dashboard.handlers.capabilities_music_generation import register
from gideon.sdk.credentials import CredentialStore
from gideon.workspace.capabilities.music.generation import (
    ENDPOINT,
    MODELS,
    MusicGeneration,
)
from gideon.workspace.capabilities.music.generation_tools import MusicGenerationTools
from gideon.workspace.capabilities.music.store import DomainError


def service_at(home):
    return MusicGeneration(home, catalog_at(home))


def request_for(service):
    track = service.catalog.create("tracks", {"title": "Composition destination"})
    return {
        "request_id": "composition-request",
        "track_id": track["id"],
        "track_revision": 1,
        "prompt": "Gentle instrumental with acoustic guitar",
        "music_length_ms": 5000,
        "force_instrumental": True,
        "license": "Account license to be verified",
    }


def seed_job(service, request, status="running"):
    job = {
        "id": request["request_id"],
        "status": status,
        "request": request,
        "model": "music_v1",
        "artifact_ref": None,
        "error": None,
    }
    with sqlite3.connect(service.path) as db:
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?)",
            (job["id"], json.dumps(request, sort_keys=True), json.dumps(job)),
        )
    return job


def test_default_engine_is_explicitly_disabled_and_unverified(tmp_path):
    service = service_at(tmp_path)
    ready = service.readiness()
    assert ready["config"] == {
        "enabled": False,
        "model": "music_v1",
        "credential_name": "",
        "revision": 0,
    }
    assert ready["credential_available"] is False
    assert ready["ready_to_submit"] is False
    assert ready["remote_status"] == "unverified"
    assert ready["provider"] == "elevenlabs"
    assert ready["min_duration_ms"] == 3000
    assert ready["max_duration_ms"] == 600000
    assert ready["instrumental_supported"] is True
    assert ready["automatic_duration_supported"] is True
    assert service.list() == []
    assert service.tasks == {}


def test_configuration_reopens_and_secrets_stay_in_canonical_store(tmp_path):
    credentials = CredentialStore(tmp_path)
    credentials.save(
        {
            "composition-key": {
                "type": "api_key",
                "value": "fixture-never-sent-to-provider",
            }
        }
    )
    service = service_at(tmp_path)
    config = service.configure(
        {
            "enabled": True,
            "model": "music_v2",
            "credential_name": "composition-key",
            "revision": 0,
        }
    )
    assert config["revision"] == 1
    reopened = service_at(tmp_path)
    assert reopened.config() == config
    readiness = reopened.readiness()
    assert readiness["credential_available"] is True
    assert readiness["ready_to_submit"] is True
    assert readiness["remote_status"] == "unverified"
    assert "fixture-never-sent-to-provider" not in json.dumps(readiness)
    assert b"fixture-never-sent-to-provider" not in service.path.read_bytes()
    assert (
        credentials.resolve("composition-key").secret
        == "fixture-never-sent-to-provider"
    )
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600


def test_credential_rotation_and_removal_change_local_readiness(tmp_path):
    service = service_at(tmp_path)
    service.configure(
        {"enabled": True, "model": "music_v1", "credential_name": "key", "revision": 0}
    )
    assert service.readiness()["credential_available"] is False
    credentials = CredentialStore(tmp_path)
    credentials.save({"key": {"type": "api_key", "value": "local-test-secret"}})
    assert service.readiness()["ready_to_submit"] is True
    credentials.save({})
    assert service.readiness()["ready_to_submit"] is False
    assert service.config()["credential_name"] == "key"


def test_configuration_conflict_preserves_prior_value(tmp_path):
    service = service_at(tmp_path)
    first = service.configure(
        {
            "enabled": False,
            "model": "music_v2_5",
            "credential_name": "key",
            "revision": 0,
        }
    )
    with pytest.raises(DomainError) as err:
        service.configure(
            {
                "enabled": True,
                "model": "music_v1",
                "credential_name": "other",
                "revision": 0,
            }
        )
    assert err.value.status == 409
    assert err.value.code == "revision_conflict"
    assert service_at(tmp_path).config() == first


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"model": "invented-model"},
        {"credential_name": 12},
        {"credential_name": "x" * 101},
        {"revision": -1},
        {"api_key": "must-not-be-persisted"},
        {"endpoint": "https://example.com"},
        {"home": "/tmp/another"},
        {"provider": "unreviewed"},
    ],
)
def test_config_rejects_unpublished_controls_and_secret_payloads(tmp_path, changes):
    service = service_at(tmp_path)
    initial = service.config()
    with pytest.raises(DomainError):
        service.configure({**initial, **changes})
    assert service.config() == initial
    assert service.list() == []


@pytest.mark.asyncio
async def test_missing_credential_refuses_before_creating_job_or_artifact(tmp_path):
    service = service_at(tmp_path)
    request = request_for(service)
    service.configure(
        {
            "enabled": True,
            "model": "music_v1",
            "credential_name": "missing",
            "revision": 0,
        }
    )
    with pytest.raises(DomainError) as err:
        await service.submit(request)
    assert err.value.status == 503
    assert err.value.code == "engine_unavailable"
    assert service.list() == []
    assert service.tasks == {}
    assert service.catalog.get("tracks", request["track_id"])["renders"] == []
    assert service.catalog.artifacts.list() == []


@pytest.mark.asyncio
async def test_disabled_engine_refuses_even_with_local_credential(tmp_path):
    CredentialStore(tmp_path).save(
        {"key": {"type": "api_key", "value": "not-a-live-key"}}
    )
    service = service_at(tmp_path)
    service.configure(
        {"enabled": False, "model": "music_v1", "credential_name": "key", "revision": 0}
    )
    with pytest.raises(DomainError) as err:
        await service.submit(request_for(service))
    assert err.value.code == "engine_unavailable"
    assert service.readiness()["credential_available"] is True
    assert service.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", ""),
        ("prompt", ""),
        ("prompt", "x" * 4101),
        ("music_length_ms", 2999),
        ("music_length_ms", 600001),
        ("music_length_ms", True),
        ("force_instrumental", "true"),
        ("track_revision", 0),
        ("license", ""),
        ("track_id", False),
        ("api_key", "forbidden"),
        ("model", "caller-override"),
    ],
)
async def test_generation_request_validation_precedes_submission(
    tmp_path, field, value
):
    service = service_at(tmp_path)
    request = request_for(service)
    with pytest.raises(DomainError) as err:
        await service.submit({**request, field: value})
    assert err.value.code == "invalid_input"
    assert service.list() == []
    assert service.tasks == {}


@pytest.mark.asyncio
async def test_track_reference_and_revision_are_checked_before_provider(tmp_path):
    service = service_at(tmp_path)
    request = request_for(service)
    with pytest.raises(DomainError) as err:
        await service.submit({**request, "track_id": "missing"})
    assert err.value.code == "not_found"
    with pytest.raises(DomainError) as err:
        await service.submit({**request, "track_revision": 88})
    assert err.value.code == "revision_conflict"
    assert service.list() == []


@pytest.mark.asyncio
async def test_retry_of_persisted_request_never_restarts_provider(tmp_path):
    service = service_at(tmp_path)
    request = request_for(service)
    prior = seed_job(service, request, "interrupted")
    assert await service.submit(request) == prior
    assert service.tasks == {}
    with pytest.raises(DomainError) as err:
        await service.submit({**request, "prompt": "Different request"})
    assert err.value.code == "request_conflict"
    assert service.get(request["request_id"]) == prior
    assert service_at(tmp_path).get(request["request_id"]) == prior


def test_recovery_marks_actual_durable_inflight_state_without_retry(tmp_path):
    service = service_at(tmp_path)
    one = request_for(service)
    two = {**one, "request_id": "queued-request"}
    three = {**one, "request_id": "failed-request"}
    seed_job(service, one, "running")
    seed_job(service, two, "queued")
    failed = seed_job(service, three, "failed")
    restarted = service_at(tmp_path)
    restarted.recover()
    assert restarted.get(one["request_id"])["status"] == "interrupted"
    assert restarted.get(two["request_id"])["status"] == "interrupted"
    assert restarted.get(three["request_id"]) == failed
    assert "remote completion is unknown" in restarted.get(one["request_id"])["error"]
    assert restarted.tasks == {}
    assert len(restarted.list(limit=2)) == 2
    assert len(restarted.list(offset=2, limit=2)) == 1


@pytest.mark.asyncio
async def test_cancel_ownerless_job_preserves_unknown_remote_state(tmp_path):
    service = service_at(tmp_path)
    request = request_for(service)
    seed_job(service, request)
    cancelled = await service.cancel(request["request_id"])
    assert cancelled["status"] == "interrupted"
    assert cancelled["artifact_ref"] is None
    assert "remote completion is unknown" in cancelled["error"]
    assert await service.cancel(request["request_id"]) == cancelled
    await service.close()
    assert service_at(tmp_path).get(request["request_id"]) == cancelled


def test_homes_isolate_configuration_credentials_and_job_history(tmp_path):
    first, second = service_at(tmp_path / "one"), service_at(tmp_path / "two")
    CredentialStore(tmp_path / "one").save(
        {"key": {"type": "api_key", "value": "one-home-only"}}
    )
    first.configure(
        {"enabled": True, "model": "music_v1", "credential_name": "key", "revision": 0}
    )
    request = request_for(first)
    seed_job(first, request, "interrupted")
    assert second.config()["revision"] == 0
    assert second.readiness()["credential_available"] is False
    assert second.list() == []
    with pytest.raises(DomainError):
        second.get(request["request_id"])


def test_actual_mp3_encoding_is_measured_from_canonical_bytes(tmp_path):
    service = service_at(tmp_path)
    original = tmp_path / "recording.wav"
    encoded = tmp_path / "recording.mp3"
    original.write_bytes(wav_bytes())
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original), str(encoded)],
        check=True,
        timeout=20,
    )
    art = service.catalog.artifacts.create_binary(
        name="Encoded recorded fixture",
        kind="audio",
        mime="audio/mpeg",
        data=encoded.read_bytes(),
        source="import",
    )
    track = service.catalog.create("tracks", {"title": "Measured encoding"})
    attached = service.catalog.attach(track["id"], attach_body(track, art))
    duration = attached["renders"][0]["duration_seconds"]
    assert 1 <= duration < 1.5
    assert attached["renders"][0]["source"]["kind"] == "imported"
    assert (
        service.catalog.artifacts.raw_bytes(art.slug, version=1)[0]
        == encoded.read_bytes()
    )


@pytest.mark.asyncio
async def test_actual_http_config_readiness_refusal_and_job_recovery(tmp_path):
    service = service_at(tmp_path)
    request = request_for(service)
    seed_job(service, {**request, "request_id": "old-request"})
    app = web.Application()
    register(app, service)
    prefix = "/api/capabilities/music/generation"
    async with TestClient(TestServer(app)) as client:
        response = await client.get(prefix + "/readiness")
        assert response.status == 200
        assert (await response.json())["remote_status"] == "unverified"
        response = await client.patch(
            prefix + "/config",
            json={
                "enabled": True,
                "model": "music_v2",
                "credential_name": "missing",
                "revision": 0,
            },
        )
        assert response.status == 200
        assert (await response.json())["config"]["revision"] == 1
        refusal = await client.post(prefix + "/jobs", json=request)
        assert refusal.status == 503
        assert (await refusal.json())["error"] == "engine_unavailable"
        old = await client.get(prefix + "/jobs/old-request")
        assert (await old.json())["job"]["status"] == "interrupted"
        listing = await client.get(prefix + "/jobs")
        assert len((await listing.json())["jobs"]) == 1
        unknown = await client.get(prefix + "/jobs/missing")
        assert unknown.status == 404
        invalid = await client.get(prefix + "/jobs?limit=101")
        assert invalid.status == 400


@pytest.mark.asyncio
async def test_native_controls_preserve_provider_absence_and_secret_boundary(tmp_path):
    service = service_at(tmp_path)
    provider = MusicGenerationTools(service)
    definitions = await provider.list_tools()
    assert len(definitions) == 6
    assert all("api_key" not in tool.parameters["properties"] for tool in definitions)
    status = await provider.invoke("music_generation_readiness", {})
    assert status.success
    assert json.loads(status.output)["ready_to_submit"] is False
    configured = await provider.invoke(
        "music_generation_configure",
        {
            "data": {
                "enabled": True,
                "model": "music_v1",
                "credential_name": "missing",
                "revision": 0,
            }
        },
    )
    assert configured.success
    refusal = await provider.invoke(
        "music_generation_submit", {"data": request_for(service)}
    )
    assert refusal.success is False
    assert refusal.metadata["code"] == "engine_unavailable"
    invalid = await provider.invoke(
        "music_generation_readiness", {"home": "/tmp/another"}
    )
    assert invalid.success is False
