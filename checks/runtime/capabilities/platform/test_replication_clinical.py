import copy
import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.operations.durability import conflicts
from gideon.workspace.capabilities.platform import replication_clinical as adapter
from gideon.workspace.capabilities.platform.peers import PeerStore
from gideon.workspace.capabilities.platform.replication import (
    RECEIVE_PATH,
    ReplicationError,
    ReplicationService,
)
from gideon.workspace.capabilities.wellbeing.body_composition import (
    BodyCompositionStore,
)
from gideon.workspace.capabilities.wellbeing.epigenetic import EpigeneticStore
from gideon.workspace.capabilities.wellbeing.eyes import EyePrescriptionStore
from gideon.workspace.capabilities.wellbeing.lifestyle_profile import (
    LifestyleProfileStore,
)
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def eye(sphere=-1.25, cylinder=-0.5, axis=90):
    return {
        "sphere": sphere,
        "sphere_unit": "D",
        "cylinder": cylinder,
        "cylinder_unit": "D",
        "axis": axis,
        "axis_unit": "degrees",
    }


def seed(home, suffix="one"):
    epigenetic = EpigeneticStore(home).create(
        {
            "request_id": "epi-" + suffix,
            "source_report_id": "report-" + suffix,
            "observed_at": "2026-09-20",
            "source": "Owner supplied report",
            "biological_age": {"value": 38.4, "unit": "years"},
            "chronological_age": {"value": 41, "unit": "years"},
            "pace_of_aging": {"value": 0.91, "scale": "years/year"},
            "organ_scores": {"heart": {"value": 36.2, "unit": "years"}},
            "notes": "Reported values",
        }
    )
    eyes = EyePrescriptionStore(home).create(
        {
            "request_id": "eyes-" + suffix,
            "observed_date": "2026-09-24",
            "source": "Optometrist paper prescription",
            "notes": "Authored copy",
            "left": eye(),
            "right": eye(-1, -0.25, 80),
        }
    )
    lifestyle = LifestyleProfileStore(home).create(
        {
            "request_id": "life-" + suffix,
            "observed_at": "2026-09-25T08:30:00+02:00",
            "source": "Owner-authored intake",
            "reported_sex": "female",
            "sex_source": "Owner report",
            "smoking_status": "former",
            "diet_quality": {
                "value": 7,
                "scale": {"minimum": 0, "maximum": 10, "label": "owner rating"},
            },
            "stress": {
                "value": 3,
                "scale": {"minimum": 0, "maximum": 10, "label": "owner rating"},
            },
            "reported_bmi": 23.4,
            "condition_labels": ["Asthma"],
            "reported_daily_alcohol": {"value": 0, "unit": "standard_drinks_per_day"},
        }
    )
    body = BodyCompositionStore(home).create(
        {
            "request_id": "body-" + suffix,
            "observed_at": "2026-09-25T08:30:00+02:00",
            "source": "Owner-authored scale",
            "values": {
                "muscle_percent": 41.2,
                "fat_percent": 18.4,
                "bone_mass": {"value": 6.6, "unit": "lb"},
                "temperature": {"value": 98.6, "unit": "F"},
            },
            "notes": "Morning observation",
        }
    )
    return {
        adapter.EPIGENETIC: epigenetic,
        adapter.EYES: eyes,
        adapter.LIFESTYLE: lifestyle,
        adapter.BODY: body,
    }


def pair(first_home, second_home, enabled=True):
    first_home.mkdir(parents=True, exist_ok=True)
    second_home.mkdir(parents=True, exist_ok=True)
    first, second = PeerStore(first_home), PeerStore(second_home)
    first_id, second_id = first.snapshot()["self"], second.snapshot()["self"]
    categories = [adapter.SCOPE] if enabled else []
    first.put(
        second_id["peer_id"],
        {
            "label": "Second",
            "endpoint": "https://second.example",
            "public_key": second_id["public_key"],
            "enabled": True,
            "send_categories": categories,
            "receive_categories": categories,
            "revision": 0,
        },
    )
    second.put(
        first_id["peer_id"],
        {
            "label": "First",
            "endpoint": "https://first.example",
            "public_key": first_id["public_key"],
            "enabled": True,
            "send_categories": categories,
            "receive_categories": categories,
            "revision": 0,
        },
    )
    return first_id, second_id


@pytest.mark.asyncio
async def test_signed_two_home_current_rows_preserve_local_history_and_exclude_requests(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    canonical = seed(source)
    receiver = ReplicationService(target)

    async def receive(request):
        envelope = await request.json()
        assert envelope["proof"]["scope"] == adapter.SCOPE
        peer = PeerStore(target).verify_proof(envelope["proof"])
        return web.json_response(receiver.apply_batch(peer["id"], envelope["payload"]))

    application = web.Application()
    application.router.add_post(RECEIVE_PATH, receive)
    async with TestServer(application) as server:
        source_id, target_id = pair(source, target)
        source_peer = PeerStore(source).get(target_id["peer_id"])
        source_peer["endpoint"] = str(server.make_url("/")).rstrip("/")
        PeerStore(source).put(
            target_id["peer_id"],
            {
                key: source_peer[key]
                for key in (
                    "label",
                    "endpoint",
                    "public_key",
                    "enabled",
                    "send_categories",
                    "receive_categories",
                    "revision",
                )
            },
        )
        batch = ReplicationService(source).export_batch(
            target_id["peer_id"], adapter.SCOPE
        )
        assert [entry["entry_id"] for entry in batch["entries"]] == list(
            adapter.ENTRIES
        )
        assert all(len(entry["rows"]) == 1 for entry in batch["entries"])
        wire = json.dumps(batch)
        for forbidden in (
            "request_id",
            "requests",
            "permission",
            "credential",
            "history",
        ):
            assert forbidden not in wire
        accepted = await ReplicationService(source).push(
            target_id["peer_id"], adapter.SCOPE
        )
        assert accepted["accepted"] is True
    assert (
        EpigeneticStore(target).get(canonical[adapter.EPIGENETIC]["id"])
        == canonical[adapter.EPIGENETIC]
    )
    assert (
        EyePrescriptionStore(target).get(canonical[adapter.EYES]["id"])
        == canonical[adapter.EYES]
    )
    assert (
        LifestyleProfileStore(target).get(canonical[adapter.LIFESTYLE]["id"])
        == canonical[adapter.LIFESTYLE]
    )
    assert (
        BodyCompositionStore(target).get(canonical[adapter.BODY]["id"])
        == canonical[adapter.BODY]
    )
    with sqlite3.connect(target / "capabilities/wellbeing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM eye_prescription_requests"
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM body_composition_requests"
        ).fetchone() == (0,)
    assert source_id["peer_id"] == PeerStore(target).get(source_id["peer_id"])["id"]


def test_four_family_conflicts_restore_selected_fields_and_append_local_history(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    source_id, target_id = pair(source, target)
    values = seed(source, "conflict")
    sender, receiver = ReplicationService(source), ReplicationService(target)
    baseline = sender.export_batch(target_id["peer_id"], adapter.SCOPE)
    receiver.apply_batch(source_id["peer_id"], baseline)
    sender._record_sent(target_id["peer_id"], baseline)
    EpigeneticStore(source).correct(
        values[adapter.EPIGENETIC]["id"],
        {"request_id": "epi-peer", "revision": 1, "notes": "Peer epi"},
    )
    EpigeneticStore(target).correct(
        values[adapter.EPIGENETIC]["id"],
        {"request_id": "epi-local", "revision": 1, "notes": "Local epi"},
    )
    EyePrescriptionStore(source).correct(
        values[adapter.EYES]["id"],
        {"request_id": "eyes-peer", "revision": 1, "notes": "Peer eyes"},
    )
    EyePrescriptionStore(target).correct(
        values[adapter.EYES]["id"],
        {"request_id": "eyes-local", "revision": 1, "notes": "Local eyes"},
    )
    LifestyleProfileStore(source).correct(
        values[adapter.LIFESTYLE]["id"],
        {"request_id": "life-peer", "revision": 1, "reported_bmi": 24.1},
    )
    LifestyleProfileStore(target).correct(
        values[adapter.LIFESTYLE]["id"],
        {"request_id": "life-local", "revision": 1, "reported_bmi": 22.8},
    )
    BodyCompositionStore(source).correct(
        values[adapter.BODY]["id"],
        {"request_id": "body-peer", "revision": 1, "notes": "Peer body"},
    )
    BodyCompositionStore(target).correct(
        values[adapter.BODY]["id"],
        {"request_id": "body-local", "revision": 1, "notes": "Local body"},
    )
    result = receiver.apply_batch(
        source_id["peer_id"], sender.export_batch(target_id["peer_id"], adapter.SCOPE)
    )
    assert [entry["conflicts"] for entry in result["entries"]] == [1, 1, 1, 1]
    fields = {
        adapter.EPIGENETIC: "notes",
        adapter.EYES: "notes",
        adapter.LIFESTYLE: "reported_bmi",
        adapter.BODY: "notes",
    }
    for conflict in conflicts.ConflictQueue(target).items(
        status=conflicts.STATUS_NEEDS_REVIEW
    ):
        receiver.restore_fields(conflict.id, [fields[conflict.entry_id]])
    assert (
        EpigeneticStore(target).get(values[adapter.EPIGENETIC]["id"])["notes"]
        == "Peer epi"
    )
    assert (
        EyePrescriptionStore(target).get(values[adapter.EYES]["id"])["notes"]
        == "Peer eyes"
    )
    assert (
        LifestyleProfileStore(target).get(values[adapter.LIFESTYLE]["id"])[
            "reported_bmi"
        ]
        == 24.1
    )
    assert (
        BodyCompositionStore(target).get(values[adapter.BODY]["id"])["notes"]
        == "Peer body"
    )
    assert len(EpigeneticStore(target).history(values[adapter.EPIGENETIC]["id"])) == 3
    assert len(EyePrescriptionStore(target).history(values[adapter.EYES]["id"])) == 3
    assert (
        len(LifestyleProfileStore(target).history(values[adapter.LIFESTYLE]["id"])) == 3
    )
    assert len(BodyCompositionStore(target).history(values[adapter.BODY]["id"])) == 3
    with sqlite3.connect(target / "capabilities/wellbeing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone() == (2,)
        assert database.execute(
            "SELECT count(*) FROM eye_prescription_requests"
        ).fetchone() == (1,)
        assert database.execute(
            "SELECT count(*) FROM body_composition_requests"
        ).fetchone() == (1,)


def test_default_policy_and_complete_preflight_reject_private_or_noncanonical_rows(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    _, target_id = pair(source, target, enabled=False)
    seed(source, "validation")
    with pytest.raises(ReplicationError, match="policy denies"):
        ReplicationService(source).export_batch(target_id["peer_id"], adapter.SCOPE)
    canonical = [
        {"entry_id": entry, "rows": adapter.read_rows(source, entry)}
        for entry in adapter.ENTRIES
    ]
    invalid = []
    value = copy.deepcopy(canonical)
    value[0]["rows"][0]["data"]["request_id"] = "remote-request"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[1]["rows"][0]["data"]["left"]["sphere_unit"] = "m"
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[2]["rows"][0]["data"]["reported_bmi"] = float("nan")
    invalid.append(value)
    value = copy.deepcopy(canonical)
    value[3]["rows"][0]["data"]["normalized_values"]["temperature_c"] = 99
    invalid.append(value)
    for entries in invalid:
        with pytest.raises(ValueError):
            adapter.validate_entries(entries)
        assert all(adapter.read_rows(target, entry) == [] for entry in adapter.ENTRIES)
    with pytest.raises(ValueError, match="coverage"):
        adapter.validate_entries(list(reversed(canonical)))


def test_owner_import_apis_append_exact_revisions_without_request_receipts(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    records = seed(source, "owners")
    source_stores = {
        adapter.EPIGENETIC: EpigeneticStore(source),
        adapter.EYES: EyePrescriptionStore(source),
        adapter.LIFESTYLE: LifestyleProfileStore(source),
        adapter.BODY: BodyCompositionStore(source),
    }
    target_stores = {
        adapter.EPIGENETIC: EpigeneticStore(target),
        adapter.EYES: EyePrescriptionStore(target),
        adapter.LIFESTYLE: LifestyleProfileStore(target),
        adapter.BODY: BodyCompositionStore(target),
    }
    for entry_id in adapter.ENTRIES:
        assert target_stores[entry_id].import_current(records[entry_id]) == "imported"
        assert target_stores[entry_id].import_current(records[entry_id]) == "unchanged"
        assert target_stores[entry_id].get(records[entry_id]["id"]) == records[entry_id]
    revisions = {
        adapter.EPIGENETIC: source_stores[adapter.EPIGENETIC].correct(
            records[adapter.EPIGENETIC]["id"],
            {
                "request_id": "owner-epi-2",
                "revision": 1,
                "notes": "Second exact epigenetic revision",
            },
        ),
        adapter.EYES: source_stores[adapter.EYES].correct(
            records[adapter.EYES]["id"],
            {
                "request_id": "owner-eyes-2",
                "revision": 1,
                "notes": "Second exact eye revision",
            },
        ),
        adapter.LIFESTYLE: source_stores[adapter.LIFESTYLE].correct(
            records[adapter.LIFESTYLE]["id"],
            {"request_id": "owner-life-2", "revision": 1, "reported_bmi": 24.8},
        ),
        adapter.BODY: source_stores[adapter.BODY].correct(
            records[adapter.BODY]["id"],
            {
                "request_id": "owner-body-2",
                "revision": 1,
                "notes": "Second exact body revision",
            },
        ),
    }
    for entry_id in adapter.ENTRIES:
        assert revisions[entry_id]["revision"] == 2
        assert target_stores[entry_id].import_current(revisions[entry_id]) == "imported"
        assert (
            target_stores[entry_id].get(records[entry_id]["id"]) == revisions[entry_id]
        )
        assert target_stores[entry_id].history(records[entry_id]["id"]) == [
            records[entry_id],
            revisions[entry_id],
        ]
    with sqlite3.connect(target / "capabilities/wellbeing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM eye_prescription_requests"
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM body_composition_requests"
        ).fetchone() == (0,)


def test_owner_import_apis_refuse_stale_divergence_and_noncanonical_values(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    records = seed(source, "refusal")
    stores = {
        adapter.EPIGENETIC: EpigeneticStore(target),
        adapter.EYES: EyePrescriptionStore(target),
        adapter.LIFESTYLE: LifestyleProfileStore(target),
        adapter.BODY: BodyCompositionStore(target),
    }
    for entry_id in adapter.ENTRIES:
        assert stores[entry_id].import_current(records[entry_id]) == "imported"
    divergent = copy.deepcopy(records)
    divergent[adapter.EPIGENETIC]["notes"] = "Same-revision peer overwrite"
    divergent[adapter.EYES]["notes"] = "Same-revision peer overwrite"
    divergent[adapter.LIFESTYLE]["reported_bmi"] = 25.2
    divergent[adapter.BODY]["notes"] = "Same-revision peer overwrite"
    for entry_id in adapter.ENTRIES:
        with pytest.raises(MeasurementError, match="conflicts with local history"):
            stores[entry_id].import_current(divergent[entry_id])
        assert stores[entry_id].get(records[entry_id]["id"]) == records[entry_id]
        assert len(stores[entry_id].history(records[entry_id]["id"])) == 1
    invalid_epigenetic = copy.deepcopy(records[adapter.EPIGENETIC])
    invalid_epigenetic["evidence_basis"] = "model_inferred"
    with pytest.raises(MeasurementError, match="canonical epigenetic"):
        EpigeneticStore(tmp_path / "invalid-epi").import_current(invalid_epigenetic)
    invalid_eyes = copy.deepcopy(records[adapter.EYES])
    invalid_eyes["updated_at"] = (
        datetime.fromisoformat(invalid_eyes["created_at"]) - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(MeasurementError, match="revision or timestamp"):
        EyePrescriptionStore(tmp_path / "invalid-eyes").import_current(invalid_eyes)
    invalid_lifestyle = copy.deepcopy(records[adapter.LIFESTYLE])
    invalid_lifestyle["reported_bmi"] = float("nan")
    with pytest.raises(MeasurementError, match="finite"):
        LifestyleProfileStore(tmp_path / "invalid-life").import_current(
            invalid_lifestyle
        )
    alternate_lifestyle_id = copy.deepcopy(records[adapter.LIFESTYLE])
    alternate_lifestyle_id["id"] = alternate_lifestyle_id["id"].upper()
    with pytest.raises(MeasurementError, match="identity or timestamp"):
        LifestyleProfileStore(tmp_path / "alternate-life-id").import_current(
            alternate_lifestyle_id
        )
    invalid_body = copy.deepcopy(records[adapter.BODY])
    invalid_body["normalized_values"]["temperature_c"] += 1
    with pytest.raises(MeasurementError, match="not exact"):
        BodyCompositionStore(tmp_path / "invalid-body").import_current(invalid_body)
    alternate_body_id = copy.deepcopy(records[adapter.BODY])
    alternate_body_id["id"] = alternate_body_id["id"].upper()
    with pytest.raises(MeasurementError, match="identity or timestamp"):
        BodyCompositionStore(tmp_path / "alternate-body-id").import_current(
            alternate_body_id
        )
    private = copy.deepcopy(records[adapter.BODY])
    private["request_id"] = "must-not-cross"
    with pytest.raises(MeasurementError, match="Invalid canonical"):
        BodyCompositionStore(tmp_path / "private-body").import_current(private)


def test_owner_import_apis_accept_a_current_snapshot_without_copying_prior_history(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "target"
    records = seed(source, "snapshot")
    current = {
        adapter.EPIGENETIC: EpigeneticStore(source).correct(
            records[adapter.EPIGENETIC]["id"],
            {
                "request_id": "snapshot-epi",
                "revision": 1,
                "notes": "Current epigenetic snapshot",
            },
        ),
        adapter.EYES: EyePrescriptionStore(source).correct(
            records[adapter.EYES]["id"],
            {
                "request_id": "snapshot-eyes",
                "revision": 1,
                "notes": "Current eye snapshot",
            },
        ),
        adapter.LIFESTYLE: LifestyleProfileStore(source).correct(
            records[adapter.LIFESTYLE]["id"],
            {"request_id": "snapshot-life", "revision": 1, "reported_bmi": 25.1},
        ),
        adapter.BODY: BodyCompositionStore(source).correct(
            records[adapter.BODY]["id"],
            {
                "request_id": "snapshot-body",
                "revision": 1,
                "notes": "Current body snapshot",
            },
        ),
    }
    targets = {
        adapter.EPIGENETIC: EpigeneticStore(target),
        adapter.EYES: EyePrescriptionStore(target),
        adapter.LIFESTYLE: LifestyleProfileStore(target),
        adapter.BODY: BodyCompositionStore(target),
    }
    for entry_id in adapter.ENTRIES:
        assert current[entry_id]["revision"] == 2
        assert targets[entry_id].import_current(current[entry_id]) == "imported"
        assert targets[entry_id].get(current[entry_id]["id"]) == current[entry_id]
        assert targets[entry_id].history(current[entry_id]["id"]) == [current[entry_id]]
    with sqlite3.connect(target / "capabilities/wellbeing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM requests").fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM eye_prescription_requests"
        ).fetchone() == (0,)
        assert database.execute(
            "SELECT count(*) FROM body_composition_requests"
        ).fetchone() == (0,)
