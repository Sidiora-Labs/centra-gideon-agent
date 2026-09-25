import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_labs import register
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import (
    MeasurementError,
    MeasurementStore,
)


def row(**changes):
    result = dict(
        analyte="Glucose",
        observed_at="2026-09-25T09:00:00+02:00",
        value=92,
        unit="mg/dL",
        reference_low=70,
        reference_high=100,
        external_id="sample-a",
        notes="fasting",
    )
    result.update(changes)
    return result


def document(rows=None, **changes):
    result = dict(
        filename="laboratory.json",
        format="json",
        source="User laboratory report",
        content=json.dumps(rows if rows is not None else [row()]),
    )
    result.update(changes)
    return result


def commit(store, payload=None, request_id="import-1"):
    payload = payload or document()
    preview = store.preview(payload)
    return store.commit(
        dict(payload, preview_id=preview["preview_id"], request_id=request_id)
    )


def test_preview_has_no_records_or_artifacts(tmp_path):
    store = LabStore(tmp_path)
    payload = document()
    preview = store.preview(payload)
    assert len(preview["preview_id"]) == 64
    assert preview["rows"] == [row()]
    assert preview["duplicates"] == 0
    assert store.list() == []
    assert not (tmp_path / "artifacts").exists()
    assert store.preview(payload) == preview
    assert store.path == MeasurementStore(tmp_path).path
    assert store.path.name == "wellbeing.sqlite3"


def test_commit_preserves_original_canonical_artifact_and_reopens(tmp_path):
    store = LabStore(tmp_path)
    payload = document()
    result = commit(store, payload)
    assert result["added"] == 1
    assert result["duplicates"] == 0
    assert len(result["records"]) == 1
    original = result["records"][0]
    assert original["value"] == 92
    assert original["unit"] == "mg/dL"
    assert original["reference_low"] == 70
    assert original["reference_high"] == 100
    assert original["source"] == payload["source"]
    assert original["kind"] == "laboratory"
    assert original["row_index"] == 1
    assert original["revision"] == 1
    provider = NativeArtifactProvider(tmp_path / "artifacts")
    source = provider.get(result["artifact"]["slug"], version=1)
    assert source is not None
    assert source.content == payload["content"]
    assert source.readonly
    assert source.source == "import"
    assert source.kind == "json"
    with pytest.raises(PermissionError):
        provider.update(source.slug, content="replacement")
    reopened = LabStore(tmp_path)
    assert reopened.get(original["id"]) == original
    assert reopened.history(original["id"]) == [original]
    assert reopened.list() == [original]
    assert MeasurementStore(tmp_path).list() == []


def test_csv_quoted_content_and_missing_ranges(tmp_path):
    content = 'analyte,observed_at,value,unit,reference_low,reference_high,notes\r\n"Iron, serum",2026-09-25T00:00:00Z,0,ug/dL,,,"line one\nline two"\r\n'
    payload = document(filename="sample.csv", format="csv", content=content)
    store = LabStore(tmp_path)
    preview = store.preview(payload)
    assert preview["rows"][0]["analyte"] == "Iron, serum"
    assert preview["rows"][0]["value"] == 0
    assert preview["rows"][0]["reference_low"] is None
    assert preview["rows"][0]["reference_high"] is None
    assert preview["rows"][0]["external_id"] == ""
    assert preview["rows"][0]["notes"] == "line one\nline two"
    result = commit(store, payload)
    artifact = store.artifacts.get(result["artifact"]["slug"], version=1)
    assert artifact.content == content.replace("\r\n", "\n")
    assert store.original(result["records"][0]["id"]) == content.encode()
    assert LabStore(tmp_path).original(result["records"][0]["id"]) == content.encode()
    assert artifact.kind == "csv"


def test_original_attachment_integrity_and_missing_state(tmp_path):
    store = LabStore(tmp_path)
    receipt = commit(store)
    reference = receipt["artifact"]
    path = (
        tmp_path / "artifacts" / reference["slug"] / "versions" / reference["filename"]
    )
    assert path.read_bytes() == document()["content"].encode()
    path.write_bytes(b"corrupted")
    with pytest.raises(MeasurementError) as failure:
        store.original(receipt["records"][0]["id"])
    assert failure.value.status == 409
    path.unlink()
    with pytest.raises(MeasurementError) as failure:
        store.original(receipt["records"][0]["id"])
    assert failure.value.status == 404
    assert store.get(receipt["records"][0]["id"])["revision"] == 1


def test_import_and_row_replays_are_duplicate_safe(tmp_path):
    store = LabStore(tmp_path)
    payload = document([row(), row()])
    assert store.preview(payload)["duplicates"] == 1
    result = commit(store, payload)
    assert result["added"] == 1
    assert result["duplicates"] == 1
    assert result["records"][0] == result["records"][1]
    assert commit(store, payload) == result
    assert commit(store, payload, "retry-new-id") == result
    other_file = dict(payload, filename="second-copy.json")
    copied = commit(store, other_file, "copy")
    assert copied["added"] == 0
    assert copied["duplicates"] == 2
    assert len(store.list()) == 1
    assert store.artifacts.get(copied["artifact"]["slug"]).content == payload["content"]
    assert copied["artifact"]["slug"] != result["artifact"]["slug"]


def test_preview_mismatch_and_reused_request_id(tmp_path):
    store = LabStore(tmp_path)
    payload = document()
    preview = store.preview(payload)
    with pytest.raises(MeasurementError) as failure:
        store.commit(
            dict(
                payload,
                source="changed",
                preview_id=preview["preview_id"],
                request_id="same",
            )
        )
    assert failure.value.status == 409
    assert store.list() == []
    result = commit(store, payload, "same")
    other = document([row(external_id="sample-b")])
    with pytest.raises(MeasurementError) as failure:
        commit(store, other, "same")
    assert failure.value.code == "conflict"
    assert store.list() == result["records"]


def test_external_source_identity_conflicts_and_independent_sources(tmp_path):
    store = LabStore(tmp_path)
    original = commit(store)["records"][0]
    with pytest.raises(MeasurementError) as failure:
        store.preview(document([row(value=95)]))
    assert failure.value.status == 409
    assert store.get(original["id"]) == original
    with pytest.raises(MeasurementError):
        store.preview(
            document([row(external_id="new"), row(external_id="new", value=95)])
        )
    distinct = commit(
        store, document([row(value=95)], source="Second laboratory"), "other-source"
    )
    assert distinct["records"][0]["id"] != original["id"]
    assert len(store.list()) == 2


def test_corrections_keep_provenance_and_import_receipts(tmp_path):
    store = LabStore(tmp_path)
    receipt = commit(store)
    original = receipt["records"][0]
    payload = dict(
        request_id="correct",
        revision=1,
        value=94,
        reference_low=None,
        reference_high=105,
        notes="transcription corrected",
    )
    changed = store.correct(original["id"], payload)
    assert changed["value"] == 94
    assert changed["reference_low"] is None
    assert changed["reference_high"] == 105
    assert changed["notes"] == "transcription corrected"
    assert changed["revision"] == 2
    for field in (
        "id",
        "artifact",
        "source",
        "analyte",
        "unit",
        "observed_at",
        "row_index",
        "created_at",
        "external_id",
    ):
        assert changed[field] == original[field]
    assert store.correct(original["id"], payload) == changed
    assert commit(store) == receipt
    assert LabStore(tmp_path).history(original["id"]) == [original, changed]
    assert store.trends("Glucose", "mg/dL") == [changed]
    with pytest.raises(MeasurementError) as failure:
        store.correct(original["id"], dict(payload, request_id="stale"))
    assert failure.value.status == 409
    assert store.history(original["id"]) == [original, changed]


@pytest.mark.parametrize(
    "changes",
    [
        {"value": -1},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": "92"},
        {"value": 1e13},
        {"unit": ""},
        {"unit": None},
        {"analyte": ""},
        {"analyte": "x" * 121},
        {"reference_low": 110, "reference_high": 100},
        {"reference_low": -1},
        {"reference_high": "100"},
        {"observed_at": "2026-09-25"},
        {"observed_at": "bad-date"},
        {"source": "unexpected"},
        {"notes": None},
        {"external_id": 12},
        {"external_id": "x" * 257},
    ],
)
def test_invalid_json_rows_reject_whole_batch_with_row_number(tmp_path, changes):
    store = LabStore(tmp_path)
    with pytest.raises(MeasurementError) as failure:
        store.preview(document([row(), row(**changes)]))
    assert "Row 2" in str(failure.value)
    assert store.list() == []
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        document(content="not json"),
        document(content="{}"),
        document(content="[]"),
        document([row()] * 1001),
        document(format="xml"),
        document(source=""),
        document(filename="x" * 201),
        document(content="a" * 500001),
        document(content="ä" * 300000),
        document(unknown="field"),
        document(format="csv", content="analyte,value\nGlucose,92"),
        document(
            format="csv",
            content="analyte,analyte,observed_at,value,unit\nA,A,2026-09-25T00:00:00Z,1,x",
        ),
        document(
            format="csv",
            content="analyte,observed_at,value,unit\nGlucose,2026-09-25T00:00:00Z,not-numeric,mg/dL",
        ),
    ],
)
def test_bad_documents_do_not_create_artifacts(tmp_path, payload):
    store = LabStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.preview(payload)
    assert store.list() == []
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other"},
        {"artifact": {}},
        {"analyte": "other"},
        {"observed_at": "2026-09-26T00:00:00Z"},
        {"unit": "mmol/L"},
        {"revision": True},
        {"value": float("nan")},
        {"reference_low": 200},
    ],
)
def test_bad_correction_leaves_history_intact(tmp_path, changes):
    store = LabStore(tmp_path)
    original = commit(store)["records"][0]
    payload = dict(request_id="edit", revision=1, notes="correction")
    payload.update(changes)
    with pytest.raises(MeasurementError):
        store.correct(original["id"], payload)
    assert store.history(original["id"]) == [original]


def test_unit_specific_trends_date_filters_and_pagination(tmp_path):
    store = LabStore(tmp_path)
    rows = [
        row(external_id="earlier", observed_at="2026-09-24T23:00:00Z", value=90),
        row(external_id="later", value=92),
        row(
            external_id="other-unit",
            value=5.1,
            unit="mmol/L",
            reference_low=3.9,
            reference_high=5.6,
        ),
    ]
    records = commit(store, document(rows))["records"]
    assert store.trends("Glucose", "mg/dL") == records[:2]
    assert store.trends("Glucose", "mmol/L") == [records[2]]
    assert store.trends("Iron", "ug/dL") == []
    assert store.list(
        from_date="2026-09-24T23:00:00Z", to_date="2026-09-24T23:00:00.000000Z"
    ) == [records[0]]
    assert store.list(analyte="Unknown") == []
    assert len(store.list(limit=1)) == 1
    assert store.list(limit=1, offset=0) != store.list(limit=1, offset=1)
    for query in (
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"from_date": "bad"},
        {"from_date": "2026-09-26T00:00:00Z", "to_date": "2026-09-25T00:00:00Z"},
    ):
        with pytest.raises(MeasurementError):
            store.list(**query)
    with pytest.raises(MeasurementError):
        store.trends("", "mg/dL")


def test_concurrent_import_has_one_receipt_and_original(tmp_path):
    store = LabStore(tmp_path)
    payload = document()
    preview = store.preview(payload)

    def save(index):
        return LabStore(tmp_path).commit(
            dict(
                payload,
                preview_id=preview["preview_id"],
                request_id=f"concurrent-{index}",
            )
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(save, range(3)))
    assert all(receipt == results[0] for receipt in results)
    assert len(store.list()) == 1
    assert len(list((tmp_path / "artifacts").iterdir())) == 1
    assert store.history(results[0]["records"][0]["id"]) == results[0]["records"]


@pytest.mark.asyncio
async def test_real_http_import_correction_trends_and_isolation(tmp_path):
    app, other = web.Application(), web.Application()
    register(app, tmp_path / "one")
    register(other, tmp_path / "two")
    async with (
        TestClient(TestServer(app)) as client,
        TestClient(TestServer(other)) as isolated,
    ):
        base = "/api/capabilities/wellbeing/labs"
        payload = document()
        response = await client.post(base + "/import/preview", json=payload)
        assert response.status == 200
        preview = await response.json()
        assert preview["duplicates"] == 0
        assert await (await client.get(base)).json() == {"records": []}
        response = await client.post(
            base + "/import/commit",
            json=dict(payload, preview_id=preview["preview_id"], request_id="import"),
        )
        assert response.status == 200
        receipt = await response.json()
        original = receipt["records"][0]
        path = base + "/" + original["id"]
        assert await (await client.get(path)).json() == original
        source = await client.get(path + "/source")
        assert source.status == 200
        assert await source.read() == payload["content"].encode()
        assert "attachment" in source.headers["Content-Disposition"]
        assert (await isolated.get(path + "/source")).status == 404
        assert (await isolated.get(path)).status == 404
        assert await (await isolated.get(base)).json() == {"records": []}
        correction = dict(request_id="edit", revision=1, value=93, notes="corrected")
        response = await client.put(path, json=correction)
        assert response.status == 200
        changed = await response.json()
        assert changed["value"] == 93
        assert await (await client.get(path + "/history")).json() == {
            "history": [original, changed]
        }
        response = await client.get(
            base + "/trends", params={"analyte": "Glucose", "unit": "mg/dL"}
        )
        assert await response.json() == {"records": [changed]}
        response = await client.get(
            base,
            params={
                "analyte": "Glucose",
                "from": "2026-09-25T07:00:00Z",
                "to": "2026-09-25T07:00:00Z",
            },
        )
        assert await response.json() == {"records": [changed]}
        response = await client.put(path, json=dict(correction, request_id="stale"))
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "conflict"
    assert LabStore(tmp_path / "one").history(original["id"]) == [original, changed]
    assert not (tmp_path / "two" / "artifacts").exists()


@pytest.mark.asyncio
async def test_http_validation_returns_errors_not_empty_success(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/labs"
        for payload in ([], {}, document([row(value=-1)])):
            response = await client.post(base + "/import/preview", json=payload)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_request"
        response = await client.post(
            base + "/import/preview",
            data="bad",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400
        for query in ({"limit": "bad"}, {"offset": "-1"}, {"from": "unknown"}):
            assert (await client.get(base, params=query)).status == 400
        assert (await client.get(base + "/trends")).status == 400
        assert (await client.get(base + "/unknown/history")).status == 404
        assert await (await client.get(base)).json() == {"records": []}


@pytest.mark.asyncio
async def test_native_agent_lab_tools_use_same_ledger(tmp_path):
    provider = WellbeingProvider(tmp_path)
    definition = (await provider.list_tools())[0]
    assert "labs_preview" in definition.parameters["properties"]["operation"]["enum"]
    payload = document()
    result = await provider.invoke(
        "wellbeing_records", dict(operation="labs_preview", payload=payload)
    )
    assert result.success
    preview = json.loads(result.output)
    assert LabStore(tmp_path).list() == []
    result = await provider.invoke(
        "wellbeing_records",
        dict(
            operation="labs_commit",
            payload=dict(
                payload, preview_id=preview["preview_id"], request_id="agent-import"
            ),
        ),
    )
    assert result.success
    original = json.loads(result.output)["records"][0]
    result = await provider.invoke(
        "wellbeing_records", dict(operation="labs_get", id=original["id"])
    )
    assert json.loads(result.output) == original
    result = await provider.invoke("wellbeing_records", dict(operation="labs_list"))
    assert json.loads(result.output) == [original]
    result = await provider.invoke(
        "wellbeing_records",
        dict(
            operation="labs_correct",
            id=original["id"],
            payload=dict(request_id="agent-edit", revision=1, value=93),
        ),
    )
    updated = json.loads(result.output)
    assert updated["revision"] == 2
    result = await provider.invoke(
        "wellbeing_records", dict(operation="labs_history", id=original["id"])
    )
    assert json.loads(result.output) == [original, updated]
    result = await provider.invoke(
        "wellbeing_records",
        dict(operation="labs_trends", payload=dict(analyte="Glucose", unit="mg/dL")),
    )
    assert json.loads(result.output) == [updated]
    result = await provider.invoke("wellbeing_records", dict(operation="labs_unknown"))
    assert not result.success
