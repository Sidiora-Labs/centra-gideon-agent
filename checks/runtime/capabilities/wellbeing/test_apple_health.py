import base64
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_apple import register
from gideon.workspace.capabilities.wellbeing.apple_health import AppleHealthStore, MAX_BYTES, MAX_RECORDS
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError

XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [<!ELEMENT HealthData ANY><!ELEMENT Record EMPTY>]>
<HealthData locale="en_US">
<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" unit="count" value="125" startDate="2026-09-25 09:00:00 +0200" endDate="2026-09-25 09:05:00 +0200"/>
<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch" value="HKCategoryValueSleepAnalysisAsleepCore" startDate="2026-09-24 23:00:00 +0200" endDate="2026-09-25 07:00:00 +0200"/>
<Record type="HKCategoryTypeIdentifierStandHour" value="0" startDate="2026-09-25 09:00:00 +0200"/>
<Workout workoutActivityType="HKWorkoutActivityTypeWalking"/>
</HealthData>'''


def observation(**changes):
    value = dict(resourceType="Observation", id="sample-a", status="final", category=[{"coding": [{"code": "laboratory"}]}], code={"coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}]}, effectiveDateTime="2026-09-25T09:00:00+02:00", valueQuantity={"value": 92, "unit": "mg/dL"}, referenceRange=[{"low": {"value": 70}, "high": {"value": 100}}])
    value.update(changes)
    return value


def payload(raw=XML, format_="xml", **changes):
    value = dict(filename="export." + format_, format=format_, content_base64=base64.b64encode(raw).decode(), source="User health export")
    value.update(changes)
    return value


def commit(store, data=None, request="import"):
    data = data or payload()
    return store.commit(dict(data, preview_id=store.preview(data)["preview_id"], request_id=request))


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
    return buffer.getvalue()


def test_native_xml_internal_schema_sleep_source_and_original(tmp_path):
    store = AppleHealthStore(tmp_path)
    preview = store.preview(payload())
    assert preview["metric_count"] == 2
    assert preview["lab_count"] == 0
    assert preview["skipped"] == {"unsupported_xml_record": 1, "unsupported_workout": 1}
    assert store.list_metrics() == []
    assert not (tmp_path / "artifacts").exists()
    step, sleep = preview["metrics"]
    assert step["metric"] == "HKQuantityTypeIdentifierStepCount"
    assert step["value"] == 125
    assert step["unit"] == "count"
    assert step["observed_at"] == "2026-09-25T09:00:00+02:00"
    assert step["end_at"] == "2026-09-25T09:05:00+02:00"
    assert step["device_source"] == "Watch"
    assert sleep["value"] == 8
    assert sleep["unit"] == "h"
    assert sleep["stage"] == "HKCategoryValueSleepAnalysisAsleepCore"
    result = commit(store)
    assert result["metrics_added"] == 2
    assert result["labs_added"] == 0
    assert result["duplicates"] == 0
    records = store.list_metrics()
    assert len(records) == 2
    assert records[0]["source"] == "User health export"
    assert store.original_metric(records[0]["id"]) == XML
    assert AppleHealthStore(tmp_path).original_metric(records[0]["id"]) == XML
    assert store.artifacts.get(result["artifact"]["slug"]).readonly


def test_auto_export_json_qty_and_heart_rate_components(tmp_path):
    exported = {"data": {"metrics": [
        {"name": "step_count", "units": "count", "data": [{"date": "2026-09-25 09:00:00 +0200", "qty": 250, "source": "Phone"}]},
        {"name": "heart_rate", "units": "count/min", "data": [{"date": "2026-09-25T09:00:00+02:00", "Min": 60, "Max": 100, "Avg": 75}]},
        {"name": "unknown", "units": "x", "data": [{"date": "2026-09-25T09:00:00+02:00", "text": "unsupported"}]},
    ]}}
    data = payload(json.dumps(exported).encode(), "json")
    store = AppleHealthStore(tmp_path)
    preview = store.preview(data)
    assert preview["metric_count"] == 4
    assert preview["skipped"] == {"unsupported_json_point": 1}
    assert [row["metric"] for row in preview["metrics"]] == ["step_count", "heart_rate:Min", "heart_rate:Max", "heart_rate:Avg"]
    result = commit(store, data)
    assert result["metrics_added"] == 4
    steps = store.list_metrics(metric="step_count")
    assert len(steps) == 1
    assert steps[0]["value"] == 250
    assert steps[0]["device_source"] == "Phone"
    assert store.list_metrics(metric="heart_rate:Avg")[0]["value"] == 75
    assert store.original_metric(steps[0]["id"]) == json.dumps(exported).encode()


def test_fhir_bundle_uses_existing_lab_history_and_original(tmp_path):
    bundle = {"resourceType": "Bundle", "entry": [{"resource": observation()}, {"resource": observation(id="cancelled", status="cancelled")}, {"resource": {"resourceType": "Patient", "id": "ignored"}}, {"resource": observation(id="nonnumeric", valueQuantity=None)}]}
    raw = json.dumps(bundle, indent=2).encode()
    store = AppleHealthStore(tmp_path)
    preview = store.preview(payload(raw, "fhir"))
    assert preview["lab_count"] == 1
    assert preview["metric_count"] == 0
    assert preview["skipped"] == {"nonactive_or_nonlaboratory_fhir": 2, "nonnumeric_fhir_observation": 1}
    result = commit(store, payload(raw, "fhir"))
    assert result["labs_added"] == 1
    assert result["metrics_added"] == 0
    labs = LabStore(tmp_path)
    record = labs.list()[0]
    assert record["analyte"] == "Glucose"
    assert record["value"] == 92
    assert record["reference_low"] == 70
    assert record["reference_high"] == 100
    assert record["external_id"] == "sample-a"
    assert record["artifact"] == result["artifact"]
    assert labs.original(record["id"]) == raw
    changed = labs.correct(record["id"], dict(request_id="correction", revision=1, value=93))
    assert labs.history(record["id"]) == [record, changed]
    assert labs.trends("Glucose", "mg/dL") == [changed]
    assert commit(store, payload(raw, "fhir")) == result
    assert labs.get(record["id"])["value"] == 93


def test_zip_import_commits_metrics_and_clinical_labs_together(tmp_path):
    raw = archive({"apple_health_export/export.xml": XML, "apple_health_export/clinical_records/lab.json": json.dumps(observation()), "apple_health_export/notes.txt": "ignored"})
    data = payload(raw, "zip")
    store = AppleHealthStore(tmp_path)
    preview = store.preview(data)
    assert preview["metric_count"] == 2
    assert preview["lab_count"] == 1
    result = commit(store, data)
    assert result["metrics_added"] == 2
    assert result["labs_added"] == 1
    assert LabStore(tmp_path).list()[0]["artifact"] == store.list_metrics()[0]["artifact"]
    assert store.original_metric(store.list_metrics()[0]["id"]) == raw
    assert LabStore(tmp_path).original(LabStore(tmp_path).list()[0]["id"]) == raw
    assert commit(store, data, "retry") == result
    renamed = commit(store, dict(data, filename="renamed.zip"), "renamed")
    assert renamed["metrics_added"] == 0
    assert renamed["labs_added"] == 0
    assert renamed["duplicates"] == 3
    assert len(store.list_metrics()) == 2
    assert len(LabStore(tmp_path).list()) == 1


def test_cross_domain_identity_conflict_rolls_back_metrics(tmp_path):
    store = AppleHealthStore(tmp_path)
    commit(store, payload(json.dumps(observation()).encode(), "fhir"))
    raw = archive({"export.xml": XML, "clinical_records/lab.json": json.dumps(observation(valueQuantity={"value": 99, "unit": "mg/dL"}))})
    with pytest.raises(MeasurementError) as failure:
        commit(store, payload(raw, "zip"), "conflict")
    assert failure.value.status == 409
    assert store.list_metrics() == []
    assert LabStore(tmp_path).list()[0]["value"] == 92
    assert len(LabStore(tmp_path).history(LabStore(tmp_path).list()[0]["id"])) == 1


def test_preview_conflict_request_collision_and_isolation(tmp_path):
    store = AppleHealthStore(tmp_path / "first")
    data = payload()
    preview = store.preview(data)
    with pytest.raises(MeasurementError) as failure:
        store.commit(dict(data, source="different", preview_id=preview["preview_id"], request_id="same"))
    assert failure.value.status == 409
    assert store.list_metrics() == []
    result = commit(store, data, "same")
    with pytest.raises(MeasurementError):
        commit(store, dict(data, filename="different.xml"), "same")
    other = AppleHealthStore(tmp_path / "second")
    assert other.list_metrics() == []
    assert LabStore(tmp_path / "second").list() == []
    with pytest.raises(MeasurementError) as failure:
        other.original_metric(store.list_metrics()[0]["id"])
    assert failure.value.status == 404
    assert commit(store, data, "another-request") == result


@pytest.mark.parametrize("data", [
    {}, [], payload(format="url"), payload(content_base64="not base64"), payload(b""),
    payload(b"x" * (MAX_BYTES + 1)), payload(source=""), payload(filename="x" * 201),
    payload(home="/other"), payload(b"<broken>"), payload(b'<HealthData/>'),
    payload(b'<!DOCTYPE HealthData SYSTEM "file:///etc/passwd"><HealthData/>'),
    payload(b'<!DOCTYPE HealthData [<!ENTITY x "repeat">]><HealthData>&x;</HealthData>'),
    payload('<HealthData/>'.encode('utf-16')), payload(b'[]', "json"),
    payload(b'{"data":{"metrics":{}}}', "json"), payload(b'{bad', "json"),
    payload(b'{"data":{"metrics":[{"data":[1]}]}}', "json"),
    payload(b'not zip', "zip"), payload(archive({"../export.xml": XML}), "zip"),
    payload(archive({"/export.xml": XML}), "zip"), payload(archive({"notes.txt": "empty"}), "zip"),
    payload(json.dumps({"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Bundle"}}]}).encode(), "fhir"),
])
def test_invalid_exports_never_create_records_or_artifacts(tmp_path, data):
    store = AppleHealthStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.preview(data)
    assert store.list_metrics() == []
    assert LabStore(tmp_path).list() == []
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("replacement", [b'value="NaN"', b'value="Infinity"', b'value="wrong"', b'value="1e20"'])
def test_numeric_xml_refuses_nonfinite_or_malformed_values(tmp_path, replacement):
    raw = XML.replace(b'value="125"', replacement)
    with pytest.raises(MeasurementError):
        AppleHealthStore(tmp_path).preview(payload(raw))


@pytest.mark.parametrize("changes", [
    {"category": None}, {"category": [{"coding": None}]}, {"code": []},
    {"referenceRange": {}}, {"referenceRange": [3]},
    {"referenceRange": [{"low": "bad"}]}, {"valueQuantity": {"value": True, "unit": "mg/dL"}},
    {"referenceRange": [{"low": {"value": 4, "unit": "mmol/L"}}]},
    {"referenceRange": [{"high": {"value": 100, "comparator": "<"}}]},
    {"valueQuantity": {"value": -1, "unit": "mg/dL"}}, {"effectiveDateTime": "2026-09-25"},
])
def test_malformed_fhir_reports_validation_error(tmp_path, changes):
    with pytest.raises(MeasurementError):
        AppleHealthStore(tmp_path).preview(payload(json.dumps(observation(**changes)).encode(), "fhir"))


def test_comparator_observations_are_not_exact_values(tmp_path):
    bundle = {"resourceType": "Bundle", "entry": [{"resource": observation()}, {"resource": observation(id="threshold", valueQuantity={"value": 5, "unit": "mg/dL", "comparator": "<"})}]}
    store = AppleHealthStore(tmp_path)
    data = payload(json.dumps(bundle).encode(), "fhir")
    preview = store.preview(data)
    assert preview["lab_count"] == 1
    assert preview["skipped"] == {"comparator_fhir_observation": 1}
    commit(store, data)
    assert [row["value"] for row in LabStore(tmp_path).list()] == [92]
    with pytest.raises(MeasurementError, match="Malformed JSON"):
        store.preview(payload(("[" * 10000 + "]" * 10000).encode(), "json"))


def test_limits_and_preview_pagination_are_explicit(tmp_path):
    store = AppleHealthStore(tmp_path)
    record = b'<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="1" startDate="2026-09-25T00:00:00Z"/>'
    raw = b'<HealthData>' + record * 101 + b'</HealthData>'
    preview = store.preview(payload(raw))
    assert preview["metric_count"] == 101
    assert len(preview["metrics"]) == 100
    with pytest.raises(MeasurementError):
        store.preview(payload(b'<HealthData>' + record * (MAX_RECORDS + 1) + b'</HealthData>'))
    with pytest.raises(MeasurementError):
        store.preview(payload(archive({"export.xml": b" " * (33 * 1024 * 1024)}), "zip"))
    assert store.list_metrics() == []


def test_metric_date_and_unit_filtering(tmp_path):
    store = AppleHealthStore(tmp_path)
    commit(store)
    step = store.list_metrics(metric="HKQuantityTypeIdentifierStepCount")[0]
    assert store.list_metrics(from_date="2026-09-25T07:00:00Z", to_date="2026-09-25T07:00:00.000000Z") == [step]
    assert store.list_metrics(unit="count") == [step]
    assert store.list_metrics(unit="unknown") == []
    assert len(store.list_metrics(limit=1)) == 1
    assert store.list_metrics(limit=1, offset=0) != store.list_metrics(limit=1, offset=1)
    for query in ({"limit": 0}, {"limit": True}, {"offset": -1}, {"from_date": "bad"}, {"from_date": "2026-09-26T00:00:00Z", "to_date": "2026-09-25T00:00:00Z"}):
        with pytest.raises(MeasurementError):
            store.list_metrics(**query)


def test_parallel_import_reuses_one_durable_receipt(tmp_path):
    AppleHealthStore(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(lambda n: commit(AppleHealthStore(tmp_path), request=f"request-{n}"), range(3)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(AppleHealthStore(tmp_path).list_metrics()) == 2
    assert len(list((tmp_path / "artifacts").iterdir())) == 1


@pytest.mark.asyncio
async def test_real_http_preview_commit_original_and_isolation(tmp_path):
    app, other = web.Application(), web.Application()
    register(app, tmp_path / "one")
    register(other, tmp_path / "two")
    async with TestClient(TestServer(app)) as client, TestClient(TestServer(other)) as isolated:
        base = "/api/capabilities/wellbeing/apple"
        data = payload()
        response = await client.post(base + "/import/preview", json=data)
        assert response.status == 200
        preview = await response.json()
        assert preview["metric_count"] == 2
        assert await (await client.get(base + "/metrics")).json() == {"metrics": []}
        response = await client.post(base + "/import/commit", json=dict(data, preview_id=preview["preview_id"], request_id="http"))
        assert response.status == 200
        receipt = await response.json()
        assert receipt["metrics_added"] == 2
        response = await client.get(base + "/metrics", params={"unit": "count", "from": "2026-09-25T07:00:00Z"})
        records = (await response.json())["metrics"]
        assert len(records) == 1
        original = await client.get(base + "/metrics/" + records[0]["id"] + "/source")
        assert await original.read() == XML
        assert (await isolated.get(base + "/metrics/" + records[0]["id"] + "/source")).status == 404
        assert await (await isolated.get(base + "/metrics")).json() == {"metrics": []}
        assert (await client.post(base + "/import/preview", json=payload(content_base64="broken"))).status == 400
        assert (await client.get(base + "/metrics", params={"limit": "bad"})).status == 400
        response = await client.post(base + "/import/preview", data="bad", headers={"Content-Type": "application/json"})
        assert response.status == 400


@pytest.mark.asyncio
async def test_native_tool_imports_without_secondary_store(tmp_path):
    provider = WellbeingProvider(tmp_path)
    definition = (await provider.list_tools())[0]
    assert "apple_preview" in definition.parameters["properties"]["operation"]["enum"]
    data = payload()
    result = await provider.invoke("wellbeing_records", dict(operation="apple_preview", payload=data))
    assert result.success
    preview = json.loads(result.output)
    assert AppleHealthStore(tmp_path).list_metrics() == []
    result = await provider.invoke("wellbeing_records", dict(operation="apple_commit", payload=dict(data, preview_id=preview["preview_id"], request_id="agent")))
    assert result.success
    assert json.loads(result.output)["metrics_added"] == 2
    result = await provider.invoke("wellbeing_records", dict(operation="apple_metrics", payload={"unit": "count"}))
    assert json.loads(result.output)[0]["value"] == 125
    result = await provider.invoke("wellbeing_records", dict(operation="apple_unknown"))
    assert not result.success
