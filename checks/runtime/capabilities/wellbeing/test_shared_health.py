import base64
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_shared import register
from gideon.workspace.capabilities.wellbeing.exports import ExportStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.shared_health import (
    SCHEMA,
    SharedHealthStore,
)
from gideon.workspace.capabilities.wellbeing.store import (
    MeasurementError,
    MeasurementStore,
)


def row(**changes):
    value = dict(
        id=str(uuid4()),
        revision=1,
        kind="body_weight",
        observed_at="2026-09-25T09:00:00+02:00",
        unit="lb",
        values={"weight": 160},
        source="native scale",
        notes="recorded by user",
    )
    value.update(changes)
    return value


def document(rows=None, **changes):
    value = dict(
        schema=SCHEMA,
        version=1,
        store_id=str(uuid4()),
        records=rows if rows is not None else [row()],
    )
    value.update(changes)
    return value


def commit(store, value, request="import"):
    content = json.dumps(value)
    preview = store.preview({"content": content})
    return store.commit(
        dict(content=content, preview_id=preview["preview_id"], request_id=request)
    )


def measurement(store, request="local"):
    return store.create(
        dict(
            request_id=request,
            kind="blood_pressure",
            observed_at="2026-09-25T09:00:00Z",
            unit="mmHg",
            values={"systolic": 120, "diastolic": 80},
            source="home cuff",
            notes="original",
        )
    )


def test_actual_native_file_round_trip_canonical_corrections_and_normalization(
    tmp_path,
):
    store = SharedHealthStore(tmp_path)
    assert store.status()["state"] == "missing"
    value = document()
    store.file.parent.mkdir()
    original = ("\ufeff" + json.dumps(value, indent=2).replace("\n", "\r\n")).encode()
    store.file.write_bytes(original)
    status = store.status()
    assert status["state"] == "ready"
    assert status["sha256"] == hashlib.sha256(original).hexdigest()
    assert status["bytes"] == len(original)
    assert status["records"] == 1
    preview = store.preview_file()
    assert preview["counts"] == dict(create=1, correct=0, unchanged=0)
    assert store.list() == []
    result = store.commit_file(
        dict(preview_id=preview["preview_id"], request_id="native")
    )
    canonical = result["records"][0]
    assert canonical["unit"] == "kg"
    assert canonical["values"]["weight"] == pytest.approx(72.5747792)
    assert canonical["source"] == "native scale"
    assert canonical["revision"] == 1
    assert store.get(canonical["id"]) == canonical
    assert store.download() == original
    assert store.preview_file()["counts"] == dict(create=0, correct=0, unchanged=1)
    assert (
        store.commit_file(dict(preview_id=preview["preview_id"], request_id="native"))
        == result
    )
    value["records"][0].update(revision=2, values={"weight": 162})
    revised = commit(store, value, "correction")
    assert revised["counts"] == dict(create=0, correct=1, unchanged=0)
    assert revised["records"][0]["id"] == canonical["id"]
    assert revised["records"][0]["revision"] == 2
    assert revised["records"][0]["values"]["weight"] == pytest.approx(73.48196394)
    assert len(store.history(canonical["id"])) == 2
    assert store.history(canonical["id"])[0] == canonical
    assert SharedHealthStore(tmp_path).get(canonical["id"]) == revised["records"][0]
    reference = result["artifact"]
    raw = (
        tmp_path / "artifacts" / reference["slug"] / "versions" / reference["filename"]
    ).read_bytes()
    assert raw == original
    assert store.artifacts.get(reference["slug"], version=1).readonly


def test_remote_revision_conflicts_and_local_changes_reject_atomic_batch(tmp_path):
    store = SharedHealthStore(tmp_path)
    value = document(
        [
            row(),
            row(
                kind="blood_pressure",
                unit="mmHg",
                values={"systolic": 120, "diastolic": 80},
            ),
        ]
    )
    receipt = commit(store, value)
    changed = json.loads(json.dumps(value))
    changed["records"][0]["values"]["weight"] = 170
    with pytest.raises(MeasurementError, match="conflicting"):
        store.preview({"content": json.dumps(changed)})
    changed["records"][0]["revision"] = 2
    changed["records"][1]["revision"] = 2
    local = receipt["records"][1]
    store.correct(
        local["id"], dict(request_id="local-edit", revision=1, notes="Changed locally")
    )
    with pytest.raises(MeasurementError, match="locally"):
        commit(store, changed, "atomic-conflict")
    assert store.get(receipt["records"][0]["id"])["revision"] == 1
    assert store.get(local["id"])["revision"] == 2
    assert len(store.list()) == 2
    unchanged = commit(store, value, "replay-after-local")
    assert unchanged["counts"]["unchanged"] == 2
    assert unchanged["records"][1]["notes"] == "Changed locally"
    changed["records"] = [changed["records"][0]]
    commit(store, changed, "first-newer")
    with pytest.raises(MeasurementError, match="stale"):
        commit(store, value, "stale")
    assert len(store.list()) == 2


def test_revised_kind_or_source_cannot_mutate_canonical_provenance(tmp_path):
    store = SharedHealthStore(tmp_path)
    value = document()
    receipt = commit(store, value)
    value["records"][0].update(revision=2, source="different device")
    with pytest.raises(MeasurementError, match="source cannot change"):
        commit(store, value, "changed-source")
    assert store.get(receipt["records"][0]["id"])["source"] == "native scale"
    assert len(store.history(receipt["records"][0]["id"])) == 1


def test_publication_compare_and_swap_download_and_external_edit(tmp_path):
    store = SharedHealthStore(tmp_path)
    local = measurement(store)
    first = store.publish(dict(request_id="publish", expected_sha256=None))
    raw = store.download()
    native = json.loads(raw)
    assert native["schema"] == SCHEMA
    assert native["version"] == 1
    assert native["store_id"] == first["store_id"]
    assert native["records"][0]["id"] == local["id"]
    assert first["records"] == 1
    assert hashlib.sha256(raw).hexdigest() == first["sha256"]
    assert os.stat(store.file).st_mode & 0o777 == 0o600
    assert store.publish(dict(request_id="publish", expected_sha256=None)) == first
    native["records"][0].update(revision=2, notes="Native client correction")
    store.file.write_text(json.dumps(native))
    with pytest.raises(MeasurementError, match="changed"):
        store.publish(dict(request_id="stale-publish", expected_sha256=first["sha256"]))
    preview = store.preview_file()
    assert preview["counts"]["correct"] == 1
    result = store.commit_file(
        dict(request_id="native-correction", preview_id=preview["preview_id"])
    )
    assert result["records"][0]["id"] == local["id"]
    assert store.get(local["id"])["notes"] == "Native client correction"
    second = store.publish(
        dict(request_id="publish-again", expected_sha256=store.status()["sha256"])
    )
    assert second["sha256"] != first["sha256"]
    assert second["store_id"] == first["store_id"]
    assert len(store.list()) == 1
    assert not list(store.file.parent.glob("*.tmp"))


def test_cross_home_document_transport_preserves_identity_mapping(tmp_path):
    sender = SharedHealthStore(tmp_path / "sender")
    receiver = SharedHealthStore(tmp_path / "receiver")
    original = measurement(sender)
    sender.publish(dict(request_id="publish", expected_sha256=None))
    native = json.loads(sender.download())
    receipt = commit(receiver, native)
    received = receipt["records"][0]
    assert received["source"] == original["source"]
    assert received["values"] == original["values"]
    assert received["id"] != original["id"]
    sender.correct(
        original["id"],
        dict(
            request_id="correct", revision=1, values={"systolic": 121, "diastolic": 81}
        ),
    )
    sender.publish(
        dict(request_id="publish2", expected_sha256=sender.status()["sha256"])
    )
    receipt2 = commit(receiver, json.loads(sender.download()), "update")
    assert receipt2["records"][0]["id"] == received["id"]
    assert receipt2["records"][0]["values"]["systolic"] == 121
    assert receiver.status()["state"] == "missing"
    assert len(receiver.history(received["id"])) == 2


def test_corrupt_present_file_never_seeded_or_overwritten(tmp_path):
    store = SharedHealthStore(tmp_path)
    store.file.parent.mkdir()
    store.file.write_bytes(b"{corrupt")
    assert store.status()["state"] == "invalid"
    with pytest.raises(MeasurementError, match="cannot be overwritten"):
        store.publish(dict(request_id="publish", expected_sha256=None))
    assert store.file.read_bytes() == b"{corrupt"
    store.file.write_bytes(b"\xff\xfe")
    assert store.status()["state"] == "unavailable"
    with pytest.raises(MeasurementError, match="cannot be overwritten"):
        store.publish(dict(request_id="publish", expected_sha256=None))
    assert store.file.read_bytes() == b"\xff\xfe"
    store.file.unlink()
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(document()))
    store.file.symlink_to(target)
    assert store.status()["state"] == "invalid"
    with pytest.raises(MeasurementError):
        store.publish(dict(request_id="publish", expected_sha256=None))
    assert target.exists()


def test_file_changes_after_preview_and_unknown_fields_stop_import(tmp_path):
    store = SharedHealthStore(tmp_path)
    store.file.parent.mkdir()
    value = document()
    store.file.write_text(json.dumps(value))
    preview = store.preview_file()
    value["records"][0]["notes"] = "edited"
    store.file.write_text(json.dumps(value))
    with pytest.raises(MeasurementError, match="since preview"):
        store.commit_file(dict(request_id="changed", preview_id=preview["preview_id"]))
    assert store.list() == []
    with pytest.raises(MeasurementError, match="only"):
        store.commit_file(
            dict(
                request_id="changed",
                preview_id=preview["preview_id"],
                path="/elsewhere",
            )
        )
    with pytest.raises(MeasurementError):
        store.preview({"content": json.dumps(value), "home": "/elsewhere"})


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "version",
        "store_id",
        "duplicate",
        "id",
        "revision",
        "kind",
        "unit",
        "values",
        "observed_at",
        "source",
        "notes",
        "extra",
    ],
)
def test_invalid_typed_shared_document_rejected_before_any_writes(tmp_path, mutation):
    value = document()
    if mutation in ("schema", "version", "store_id"):
        value[mutation] = {"schema": "other", "version": True, "store_id": "invalid"}[
            mutation
        ]
    elif mutation == "duplicate":
        value["records"].append(value["records"][0].copy())
    else:
        value["records"][0][mutation] = {
            "id": "../escape",
            "revision": 0,
            "kind": "unknown",
            "unit": "stones",
            "values": {"weight": True},
            "observed_at": "2026-09-25",
            "source": "",
            "notes": 6,
            "extra": "unsupported",
        }[mutation]
    store = SharedHealthStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.preview({"content": json.dumps(value)})
    assert store.list() == []
    assert not (tmp_path / "artifacts").exists()
    assert store.status()["state"] == "missing"


def test_import_retry_concurrency_source_scope_and_request_conflict(tmp_path):
    store = SharedHealthStore(tmp_path)
    value = document()
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(
            pool.map(lambda _: commit(SharedHealthStore(tmp_path), value), range(4))
        )
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(store.list()) == 1
    other = json.loads(json.dumps(value))
    other["store_id"] = str(uuid4())
    with pytest.raises(MeasurementError, match="already used"):
        commit(store, other)
    assert commit(store, other, "different-source")["counts"]["create"] == 1
    assert len(store.list()) == 2
    assert len(list((tmp_path / "artifacts").glob("shared-health-*"))) == 2


def test_export_includes_shared_provenance_and_exact_original(tmp_path):
    store = SharedHealthStore(tmp_path)
    value = document()
    receipt = commit(store, value)
    exports = ExportStore(tmp_path)
    archive = exports.create({"request_id": "export"})
    snapshot = json.loads(exports.download(archive["id"]))
    section = snapshot["sections"]["measurements"]
    assert len(section["history"]) == 1
    assert section["shared_sources"][0] == receipt
    assert section["shared_links"][0]["external_id"] == value["records"][0]["id"]
    assert section["shared_links"][0]["record_id"] == receipt["records"][0]["id"]
    assert len(section["shared_identity"]) == 1
    assert snapshot["attachments"][0]["reference"] == receipt["artifact"]
    assert (
        base64.b64decode(snapshot["attachments"][0]["content_base64"])
        == json.dumps(value).encode()
    )


def test_missing_oversized_and_malformed_inputs_are_explicit(tmp_path):
    store = SharedHealthStore(tmp_path)
    with pytest.raises(MeasurementError) as missing:
        store.download()
    assert missing.value.status == 404
    for content in ("{", "[" * 10000, "x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises(MeasurementError):
            store.preview({"content": content})
    store.file.parent.mkdir()
    store.file.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert store.status()["state"] == "invalid"
    assert store.list() == []


@pytest.mark.asyncio
async def test_native_provider_real_file_operations_and_rejected_overrides(tmp_path):
    provider = WellbeingProvider(tmp_path)
    result = await provider.invoke("wellbeing_records", {"operation": "shared_status"})
    assert result.success
    assert json.loads(result.output)["state"] == "missing"
    content = json.dumps(document())
    preview = await provider.invoke(
        "wellbeing_records",
        {"operation": "shared_preview", "payload": {"content": content}},
    )
    assert preview.success
    result = await provider.invoke(
        "wellbeing_records",
        {
            "operation": "shared_commit",
            "payload": {
                "content": content,
                "preview_id": json.loads(preview.output)["preview_id"],
                "request_id": "native",
            },
        },
    )
    assert result.success
    result = await provider.invoke(
        "wellbeing_records",
        {
            "operation": "shared_publish",
            "payload": {"request_id": "publish", "expected_sha256": None},
        },
    )
    assert result.success
    result = await provider.invoke(
        "wellbeing_records", {"operation": "shared_preview_file"}
    )
    assert json.loads(result.output)["counts"]["unchanged"] == 1
    result = await provider.invoke(
        "wellbeing_records", {"operation": "shared_download"}
    )
    assert (
        base64.b64decode(json.loads(result.output)["content_base64"])
        == SharedHealthStore(tmp_path).download()
    )
    result = await provider.invoke(
        "wellbeing_records",
        {"operation": "shared_status", "payload": {"path": "/tmp/elsewhere"}},
    )
    assert not result.success
    result = await provider.invoke("wellbeing_records", {"operation": "shared_unknown"})
    assert not result.success


@pytest.mark.asyncio
async def test_actual_http_native_document_import_publish_isolation_and_errors(
    tmp_path,
):
    app, other = web.Application(), web.Application()
    register(app, tmp_path / "one")
    register(other, tmp_path / "two")
    base = "/api/capabilities/wellbeing/shared"
    async with (
        TestClient(TestServer(app)) as client,
        TestClient(TestServer(other)) as isolated,
    ):
        assert (await (await client.get(base)).json())["state"] == "missing"
        assert (await client.get(base + "/download")).status == 404
        content = json.dumps(document())
        preview = await (
            await client.post(base + "/preview", json={"content": content})
        ).json()
        response = await client.post(
            base + "/commit",
            json=dict(
                content=content, preview_id=preview["preview_id"], request_id="http"
            ),
        )
        assert response.status == 200
        receipt = await response.json()
        assert receipt["counts"]["create"] == 1
        response = await client.post(
            base + "/publish", json=dict(request_id="publish", expected_sha256=None)
        )
        assert response.status == 200
        published = await response.json()
        response = await client.get(base + "/download")
        assert response.status == 200
        raw = await response.read()
        assert hashlib.sha256(raw).hexdigest() == published["sha256"]
        assert "attachment" in response.headers["Content-Disposition"]
        file_preview = await (await client.post(base + "/file/preview", json={})).json()
        assert file_preview["counts"]["unchanged"] == 1
        result = await (
            await client.post(
                base + "/file/commit",
                json=dict(request_id="file", preview_id=file_preview["preview_id"]),
            )
        ).json()
        assert result["counts"]["unchanged"] == 1
        assert (await (await isolated.get(base)).json())["state"] == "missing"
        assert (await isolated.get(base + "/download")).status == 404
        assert (
            await client.post(
                base + "/file/preview", json={"path": str(tmp_path / "two")}
            )
        ).status == 400
        assert (
            await client.post(
                base + "/preview",
                data="{",
                headers={"Content-Type": "application/json"},
            )
        ).status == 400
        assert MeasurementStore(tmp_path / "two").list() == []


def test_parent_directory_symlink_cannot_cross_home(tmp_path):
    store = SharedHealthStore(tmp_path / "one")
    outside = tmp_path / "two"
    outside.mkdir()
    target = outside / "wellbeing.json"
    original = json.dumps(document())
    target.write_text(original)
    store.file.parent.symlink_to(outside, target_is_directory=True)
    assert store.status()["state"] == "invalid"
    with pytest.raises(MeasurementError, match="directory symlinks"):
        store.preview_file()
    with pytest.raises(MeasurementError, match="directory symlinks"):
        store.publish(dict(request_id="blocked", expected_sha256=None))
    assert target.read_text() == original
    assert not (outside / ".wellbeing.lock").exists()


@pytest.mark.parametrize("expected", ["", "bad", True, 1, [], {}, "A" * 64])
def test_publish_requires_explicit_sha256_or_absence(tmp_path, expected):
    store = SharedHealthStore(tmp_path)
    with pytest.raises(MeasurementError, match="expected_sha256"):
        store.publish(dict(request_id="bad", expected_sha256=expected))
    assert store.status()["state"] == "missing"
    assert not store.file.parent.exists()
