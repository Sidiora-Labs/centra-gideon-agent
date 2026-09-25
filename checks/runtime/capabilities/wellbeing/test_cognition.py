import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_cognition import (
    register,
)
from gideon.workspace.capabilities.wellbeing.cognition import CognitiveStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def start(**changes):
    value = dict(
        request_id="start", kind="arithmetic", planned_trials=2, time_limit_seconds=60
    )
    value.update(changes)
    return value


def expected(row):
    stimulus = row["current_trial"]["stimulus"]
    if row["kind"] == "color_word":
        return stimulus["color"]
    return str(
        stimulus["left"] + stimulus["right"]
        if stimulus["operator"] == "+"
        else stimulus["left"] - stimulus["right"]
    )


def answer(row, request="answer", value=None):
    return dict(
        request_id=request,
        revision=row["revision"],
        answer=expected(row) if value is None else value,
    )


def test_start_actual_challenge_persistence_and_no_expected_answer_leak(tmp_path):
    store = CognitiveStore(tmp_path)
    row = store.start(start())
    assert row["status"] == "active"
    assert row["revision"] == 1
    assert row["rules_version"] == 1
    assert row["source"] == "local_exercise"
    assert row["planned_trials"] == 2
    assert row["kind"] == "arithmetic"
    assert row["trials"] == []
    assert row["score"]["accuracy"] is None
    assert row["score"]["mean_elapsed_ms"] is None
    assert row["score"]["total_elapsed_ms"] == 0
    assert row["current_trial"]["index"] == 1
    assert row["current_trial"]["presented_at"] == row["started_at"]
    stimulus = row["current_trial"]["stimulus"]
    assert 0 <= stimulus["left"] <= 99
    assert 0 <= stimulus["right"] <= 99
    assert stimulus["operator"] in ("+", "-")
    assert "expected" not in row["current_trial"]
    assert (
        datetime.fromisoformat(row["deadline"])
        - datetime.fromisoformat(row["started_at"])
    ).total_seconds() == 60
    assert store.start(start()) == row
    assert CognitiveStore(tmp_path).get(row["id"]) == row
    assert store.list_sessions() == [row]
    with pytest.raises(MeasurementError, match="Request ID"):
        store.start(start(planned_trials=3))


def test_scoring_real_submission_times_retry_and_completion(tmp_path):
    store = CognitiveStore(tmp_path)
    row = store.start(start())
    first_payload = answer(row)
    first = store.answer(row["id"], first_payload)
    assert first["status"] == "active"
    assert first["revision"] == 2
    assert first["current_trial"]["index"] == 2
    assert first["trials"][0]["answer"] == first_payload["answer"]
    assert first["trials"][0]["correct"] is True
    assert first["trials"][0]["stimulus"] == row["current_trial"]["stimulus"]
    elapsed = (
        datetime.fromisoformat(first["trials"][0]["answered_at"])
        - datetime.fromisoformat(row["current_trial"]["presented_at"])
    ).total_seconds() * 1000
    assert first["trials"][0]["elapsed_ms"] == elapsed
    assert elapsed >= 0
    assert first["score"]["accuracy"] == 1
    assert first["score"]["mean_elapsed_ms"] == elapsed
    assert first["current_trial"]["presented_at"] == first["trials"][0]["answered_at"]
    assert store.answer(row["id"], first_payload) == first
    assert len(store.get(row["id"])["trials"]) == 1
    with pytest.raises(MeasurementError, match="changed"):
        store.answer(row["id"], dict(first_payload, request_id="stale"))
    with pytest.raises(MeasurementError, match="Request ID"):
        store.answer(row["id"], dict(first_payload, answer="different"))
    completed = store.answer(row["id"], answer(first, "second", "wrong answer"))
    assert completed["status"] == "completed"
    assert completed["current_trial"] is None
    assert completed["revision"] == 3
    assert completed["score"]["answered"] == 2
    assert completed["score"]["correct"] == 1
    assert completed["score"]["accuracy"] == 0.5
    assert completed["trials"][1]["correct"] is False
    total = sum(trial["elapsed_ms"] for trial in completed["trials"])
    assert completed["score"]["total_elapsed_ms"] == total
    assert completed["score"]["mean_elapsed_ms"] == total / 2
    assert all("expected" not in trial for trial in completed["trials"])
    assert CognitiveStore(tmp_path).get(row["id"]) == completed
    with pytest.raises(MeasurementError, match="terminal"):
        store.answer(row["id"], dict(request_id="late", revision=3, answer="0"))
    with pytest.raises(MeasurementError, match="terminal"):
        store.cancel(row["id"], dict(request_id="late-cancel", revision=3))


def test_color_word_matches_ink_and_case_whitespace(tmp_path):
    store = CognitiveStore(tmp_path)
    row = store.start(start(kind="color_word", planned_trials=1))
    stimulus = row["current_trial"]["stimulus"]
    assert stimulus["word"] in ("red", "blue", "green", "yellow")
    assert stimulus["color"] in ("red", "blue", "green", "yellow")
    completed = store.answer(
        row["id"], answer(row, value=" " + stimulus["color"].upper() + " ")
    )
    assert completed["status"] == "completed"
    assert completed["score"]["correct"] == 1
    assert completed["trials"][0]["stimulus"] == stimulus
    assert completed["score"]["accuracy"] == 1
    wrong = store.start(
        start(request_id="second-start", kind="color_word", planned_trials=1)
    )
    alternatives = [
        color
        for color in ("red", "blue", "green", "yellow")
        if color != wrong["current_trial"]["stimulus"]["color"]
    ]
    failed = store.answer(wrong["id"], answer(wrong, "wrong", alternatives[0]))
    assert failed["score"]["accuracy"] == 0
    assert failed["trials"][0]["correct"] is False


def test_real_deadline_expires_without_invented_elapsed_or_extra_trials(tmp_path):
    store = CognitiveStore(tmp_path)
    row = store.start(start(time_limit_seconds=1))
    time.sleep(1.05)
    expired = CognitiveStore(tmp_path).get(row["id"])
    assert expired["status"] == "expired"
    assert expired["revision"] == 1
    assert expired["current_trial"] is None
    assert expired["score"]["answered"] == 0
    assert expired["score"]["accuracy"] is None
    assert store.list_sessions()[0]["status"] == "expired"
    late = store.answer(row["id"], answer(row))
    assert late["status"] == "expired"
    assert late["revision"] == 2
    assert late["trials"] == []
    assert late["score"]["total_elapsed_ms"] == 0
    assert store.answer(row["id"], answer(row)) == late
    assert store.get(row["id"]) == late


def test_cancellation_keeps_answered_trials_and_receipts(tmp_path):
    store = CognitiveStore(tmp_path)
    row = store.start(start())
    first = store.answer(row["id"], answer(row))
    payload = dict(request_id="cancel", revision=2)
    cancelled = store.cancel(row["id"], payload)
    assert cancelled["status"] == "cancelled"
    assert cancelled["current_trial"] is None
    assert cancelled["trials"] == first["trials"]
    assert cancelled["score"] == first["score"]
    assert cancelled["revision"] == 3
    assert store.cancel(row["id"], payload) == cancelled
    assert CognitiveStore(tmp_path).get(row["id"]) == cancelled


@pytest.mark.parametrize(
    "changes",
    [
        dict(kind="reaction"),
        dict(planned_trials=0),
        dict(planned_trials=21),
        dict(planned_trials=True),
        dict(planned_trials=1.5),
        dict(time_limit_seconds=0),
        dict(time_limit_seconds=601),
        dict(time_limit_seconds=True),
        dict(request_id=""),
        dict(extra=1),
    ],
)
def test_invalid_start_does_not_create_sessions(tmp_path, changes):
    store = CognitiveStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.start(start(**changes))
    assert store.list_sessions() == []


@pytest.mark.parametrize(
    "changes",
    [
        dict(answer=""),
        dict(answer=None),
        dict(answer=10),
        dict(answer="a" * 129),
        dict(revision=True),
        dict(revision="1"),
        dict(request_id=""),
        dict(correct=True),
    ],
)
def test_invalid_answers_preserve_pending_trial(tmp_path, changes):
    store = CognitiveStore(tmp_path)
    row = store.start(start())
    payload = answer(row)
    payload.update(changes)
    with pytest.raises(MeasurementError):
        store.answer(row["id"], payload)
    assert store.get(row["id"]) == row


def test_concurrent_receipts_home_isolation_and_limits(tmp_path):
    store = CognitiveStore(tmp_path / "one")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                lambda _: CognitiveStore(tmp_path / "one").start(start()), range(4)
            )
        )
    assert rows.count(rows[0]) == 4
    payload = answer(rows[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        answered = list(
            pool.map(
                lambda _: CognitiveStore(tmp_path / "one").answer(
                    rows[0]["id"], payload
                ),
                range(4),
            )
        )
    assert answered.count(answered[0]) == 4
    assert store.get(rows[0]["id"])["score"]["answered"] == 1
    other = CognitiveStore(tmp_path / "two")
    assert other.list_sessions() == []
    with pytest.raises(MeasurementError) as caught:
        other.get(rows[0]["id"])
    assert caught.value.status == 404
    for value in [0, 501, True, "100"]:
        with pytest.raises(MeasurementError, match="limit"):
            store.list_sessions(value)
    assert len(store.list_sessions(1)) == 1


@pytest.mark.asyncio
async def test_actual_http_start_answer_reload_cancel_and_errors(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/cognition/sessions"
        response = await client.post(base, json=start())
        assert response.status == 200
        row = await response.json()
        response = await client.get(base + "/" + row["id"])
        assert await response.json() == row
        response = await client.get(base)
        assert (await response.json())["sessions"] == [row]
        response = await client.post(
            base + "/" + row["id"] + "/answers", json=answer(row)
        )
        assert response.status == 200
        answered = await response.json()
        assert answered["score"]["correct"] == 1
        response = await client.post(
            base + "/" + row["id"] + "/cancel",
            json=dict(request_id="cancel", revision=2),
        )
        assert response.status == 200
        cancelled = await response.json()
        assert cancelled["status"] == "cancelled"
        response = await client.get(base + "/" + row["id"])
        assert await response.json() == cancelled
        assert (await client.get(base + "?limit=invalid")).status == 400
        assert (await client.get(base + "/missing")).status == 404
        assert (
            await client.post(
                base, data="bad", headers={"Content-Type": "application/json"}
            )
        ).status == 400
        assert (
            await client.post(
                base + "/" + row["id"] + "/answers", json=answer(row, "stale")
            )
        ).status == 409


@pytest.mark.asyncio
async def test_native_provider_uses_real_shared_sessions(tmp_path):
    provider = WellbeingProvider(tmp_path)

    async def invoke(operation, identity=None, payload=None):
        result = await provider.invoke(
            "wellbeing_records",
            dict(
                operation="cognition_" + operation, id=identity, payload=payload or {}
            ),
        )
        assert result.success, result.error
        return json.loads(result.output)

    row = await invoke("start", payload=start())
    assert await invoke("get", row["id"]) == row
    assert await invoke("list") == [row]
    answered = await invoke("answer", row["id"], answer(row))
    assert answered["score"]["correct"] == 1
    cancelled = await invoke("cancel", row["id"], dict(request_id="cancel", revision=2))
    assert cancelled["status"] == "cancelled"
    assert CognitiveStore(tmp_path).get(row["id"]) == cancelled
    failure = await provider.invoke(
        "wellbeing_records", {"operation": "cognition_missing"}
    )
    assert not failure.success
    assert "Unknown" in failure.error
    assert (
        "cognition_start"
        in (await provider.list_tools())[0].parameters["properties"]["operation"][
            "enum"
        ]
    )
