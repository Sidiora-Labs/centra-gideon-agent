import asyncio
import hashlib
import json
import os
import subprocess
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations.tts.openai_provider import OpenAITtsProvider
from gideon.interfaces.dashboard.handlers.capabilities_experience import (
    PREFIX,
    STORE,
    register,
)
from gideon.workspace.capabilities.experience import Conflict, ExperienceStore, NotFound
from gideon.workspace.capabilities.experience.narration import (
    NarrationJobs,
    audio_mime,
    digest,
    voice_digest,
)
from gideon.workspace.capabilities.experience.narration_http import JOBS


@pytest.fixture
def session_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = ExperienceStore(tmp_path)
    story = store.save(
        {
            "title": "A quiet path",
            "start_node": "start",
            "nodes": [
                {
                    "id": "start",
                    "text": "You walk into the woods.",
                    "kind": "scene",
                    "choices": [{"id": "walk", "label": "Continue", "target": "end"}],
                },
                {
                    "id": "end",
                    "text": "You have reached the clearing.",
                    "kind": "ending",
                    "choices": [],
                },
            ],
        }
    )
    session = store.start(
        {"story_id": story["id"], "story_revision": 1, "request_id": "start"}
    )["session"]
    return store, story, session


async def terminal(jobs, key):
    for _ in range(100):
        job = jobs.get(key)
        if job["status"] not in ("queued", "running"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("narration did not reach a terminal state")


@pytest.mark.asyncio
async def test_real_unconfigured_voice_reports_unavailable_without_audio(session_home):
    store, story, session = session_home
    jobs = NarrationJobs(store)
    job = jobs.start(session["id"], {"revision": 1, "request_id": "narrate"})
    assert job["status"] == "queued"
    assert job["node_id"] == "start"
    assert job["story_revision"] == 1
    expected = digest(
        {
            "story_id": story["id"],
            "story_revision": 1,
            "node_id": "start",
            "text": "You walk into the woods.",
        }
    )
    assert job["source_hash"] == expected
    finished = await terminal(jobs, job["id"])
    assert finished["status"] == "unavailable"
    assert finished["artifact_slug"] == ""
    assert finished["artifact_version"] is None
    assert "Configure Speech settings" in finished["error"]
    assert jobs.artifacts.list() == []
    with pytest.raises(Conflict, match="not ready"):
        jobs.audio(job["id"])
    assert jobs.get(job["id"])["source_hash"] == expected
    await jobs.close()


@pytest.mark.asyncio
async def test_narration_retries_resolve_same_job_and_conflicting_reuse_fails(
    session_home,
):
    store, _, session = session_home
    jobs = NarrationJobs(store)
    body = {"revision": 1, "request_id": "same"}
    first = jobs.start(session["id"], body)
    assert jobs.start(session["id"], body)["id"] == first["id"]
    final = await terminal(jobs, first["id"])
    assert jobs.start(session["id"], body) == final
    with pytest.raises(Conflict, match="different input"):
        jobs.start(session["id"], {"revision": 2, "request_id": "same"})
    with pytest.raises(Conflict, match="revision changed"):
        jobs.start(session["id"], {"revision": 2, "request_id": "stale"})
    replayed = NarrationJobs(ExperienceStore(store.path.parent.parent))
    assert replayed.get(first["id"]) == final
    assert replayed.start(session["id"], body) == final
    await jobs.close()
    await replayed.close()


@pytest.mark.asyncio
async def test_cancel_before_execution_prevents_provider_work_and_is_idempotent(
    session_home,
):
    store, _, session = session_home
    jobs = NarrationJobs(store)
    first = jobs.start(session["id"], {"revision": 1, "request_id": "cancel"})
    cancelled = await jobs.cancel(first["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["voice_hash"] == ""
    assert cancelled["artifact_slug"] == ""
    assert jobs.artifacts.list() == []
    assert await jobs.cancel(first["id"]) == cancelled
    assert (
        jobs.start(session["id"], {"revision": 1, "request_id": "cancel"}) == cancelled
    )
    with pytest.raises(Conflict):
        jobs.audio(first["id"])
    retry = jobs.start(session["id"], {"revision": 1, "request_id": "retry"})
    assert retry["id"] != first["id"]
    assert (await terminal(jobs, retry["id"]))["status"] == "unavailable"
    await jobs.close()


@pytest.mark.asyncio
async def test_source_snapshot_survives_edit_and_choice_and_new_node_has_new_key(
    session_home,
):
    store, story, session = session_home
    jobs = NarrationJobs(store)
    first = jobs.start(session["id"], {"revision": 1, "request_id": "first"})
    original_hash = first["source_hash"]
    changed = {k: story[k] for k in ("title", "start_node", "nodes", "revision")}
    changed["nodes"][0]["text"] = "New edition wording."
    store.save(changed, story["id"])
    assert jobs.get(first["id"])["source_hash"] == original_hash
    view = store.choose(
        session["id"], {"choice_id": "walk", "request_id": "walk", "revision": 1}
    )
    second = jobs.start(session["id"], {"revision": 2, "request_id": "second"})
    assert second["node_id"] == "end"
    assert second["story_revision"] == 1
    assert second["source_hash"] != original_hash
    assert (
        jobs.start(session["id"], {"revision": 1, "request_id": "first"})["id"]
        == first["id"]
    )
    assert view["story"]["nodes"][0]["text"] == "You walk into the woods."
    await jobs.close()


@pytest.mark.asyncio
async def test_bounded_active_jobs_and_shutdown(session_home):
    store, _, session = session_home
    jobs = NarrationJobs(store)
    pending = [
        jobs.start(session["id"], {"revision": 1, "request_id": f"job-{i}"})
        for i in range(4)
    ]
    with pytest.raises(Conflict, match="four"):
        jobs.start(session["id"], {"revision": 1, "request_id": "fifth"})
    assert (
        jobs.start(session["id"], {"revision": 1, "request_id": "job-0"}) == pending[0]
    )
    await jobs.close()
    assert all(jobs.get(job["id"])["status"] == "cancelled" for job in pending)
    assert jobs.artifacts.list() == []


def test_actual_process_exit_marks_unfinished_job_interrupted(session_home):
    store, _, session = session_home
    script = """
import asyncio,json,os,sys
from pathlib import Path
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.narration import NarrationJobs
async def main():
    jobs=NarrationJobs(ExperienceStore(Path(sys.argv[1])))
    job=jobs.start(sys.argv[2],{'revision':1,'request_id':'crash'})
    print(json.dumps(job),flush=True)
    os._exit(0)
asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(store.path.parent.parent), session["id"]],
        capture_output=True,
        text=True,
        check=True,
    )
    queued = json.loads(completed.stdout)
    assert queued["status"] == "queued"
    recovered = NarrationJobs(ExperienceStore(store.path.parent.parent)).get(
        queued["id"]
    )
    assert recovered["status"] == "interrupted"
    assert recovered["source_hash"] == queued["source_hash"]
    assert recovered["artifact_slug"] == ""
    assert "restart" in recovered["error"]


def test_voice_key_excludes_credentials_and_detects_voice_and_reference_changes(
    tmp_path,
):
    provider = OpenAITtsProvider(provider_name="speech", api_key="not-a-live-key")
    params = {
        "provider": provider,
        "voice": "model",
        "speech_voice": "alloy",
        "speed": 1.0,
    }
    first = voice_digest(params)
    assert len(first) == 64
    assert (
        voice_digest(
            {**params, "api_key": "another", "endpoint": "https://example.invalid"}
        )
        == first
    )
    assert voice_digest({**params, "speech_voice": "nova"}) != first
    assert voice_digest({**params, "speed": 1.2}) != first
    reference = tmp_path / "reference.bin"
    reference.write_bytes(
        b"reference content for fingerprint testing, not generated speech"
    )
    with_reference = voice_digest({**params, "ref_audio": str(reference)})
    assert with_reference != first
    reference.write_bytes(b"changed reference content")
    assert voice_digest({**params, "ref_audio": str(reference)}) != with_reference


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"revision": True, "request_id": "x"},
        {"revision": 1, "request_id": "../x"},
        {"revision": 1, "request_id": "x", "text": "override"},
        {"revision": 1, "request_id": "x", "provider": "override"},
    ],
)
def test_narration_rejects_untrusted_payload_and_never_creates_job(
    session_home, payload
):
    store, _, session = session_home
    jobs = NarrationJobs(store)
    with pytest.raises(ValueError):
        jobs.start(session["id"], payload)
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM narrations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"plain text",
        b"<html>failure</html>",
        b"RIFF",
        b"x" * (16 * 1024 * 1024 + 1),
    ],
)
def test_non_audio_or_oversized_provider_output_is_refused(data):
    with pytest.raises(ValueError):
        audio_mime(data)


@pytest.mark.asyncio
async def test_canonical_cache_missing_and_tampered_records_fail_closed(session_home):
    store, _, session = session_home
    jobs = NarrationJobs(store)
    job = jobs.start(session["id"], {"revision": 1, "request_id": "cache"})
    await jobs.cancel(job["id"])
    jobs.update(
        job["id"],
        status="ready",
        artifact_slug="missing",
        artifact_version=1,
        audio_hash="wrong",
    )
    with pytest.raises(NotFound, match="missing or changed"):
        jobs.audio(job["id"])
    record = jobs.artifacts.create(
        name="A text record", content="Not speech", kind="text", source="manual"
    )
    jobs.update(job["id"], artifact_slug=record.slug)
    with pytest.raises(NotFound):
        jobs.audio(job["id"])
    assert jobs.artifacts.get(record.slug).content == "Not speech"
    other = NarrationJobs(ExperienceStore(store.path.parent.parent / "other"))
    with pytest.raises(NotFound):
        other.get(job["id"])
    assert other.artifacts.list() == []
    await jobs.close()


@pytest.mark.asyncio
async def test_http_narration_uses_source_and_returns_unavailable_state(session_home):
    store, _, session = session_home
    app = web.Application()
    app[STORE] = store
    register(app)
    async with TestClient(TestServer(app)) as client:
        path = PREFIX + f"/sessions/{session['id']}/narration"
        response = await client.post(path, json={"revision": 1, "request_id": "http"})
        assert response.status == 202
        job = (await response.json())["narration"]
        finished = await terminal(app[JOBS], job["id"])
        response = await client.get(PREFIX + f"/narrations/{job['id']}")
        assert (await response.json())["narration"] == finished
        response = await client.get(job["audio_url"])
        assert response.status == 409
        response = await client.post(
            PREFIX + f"/narrations/{job['id']}/cancel", json={}
        )
        assert (await response.json())["narration"]["status"] == "unavailable"
        response = await client.post(
            path, json={"revision": 1, "request_id": "http", "voice": "override"}
        )
        assert response.status == 400
        response = await client.post(path, json={"revision": 2, "request_id": "http"})
        assert response.status == 409
        response = await client.get(PREFIX + "/narrations/missing")
        assert response.status == 404
        response = await client.get(PREFIX + "/narrations/missing/audio")
        assert response.status == 404
        response = await client.post(
            PREFIX + f"/narrations/{job['id']}/cancel", json={"home": "elsewhere"}
        )
        assert response.status == 400
