import base64
import json
import sqlite3
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.automation.workflows.project_archive import encrypt_archive, decrypt_archive
from gideon.workspace.capabilities.identity.bundles import BundleService, FORMAT
from gideon.workspace.capabilities.identity.continuity import ContinuityStore
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_bundles import register, PREFIX

PASSPHRASE = "a careful human passphrase"


def source_bundle(home):
    continuity = ContinuityStore(home)
    continuity.append_anchor(slot="persona", text="My name is Gideon.")
    continuity.append_anchor(slot="self_notes", text="Retain telescope calibration.")
    service = BundleService(home)
    bundle = service.export_bundle(groups=["persona", "self_notes"], passphrase=PASSPHRASE)
    return service, bundle


def preview(service, bundle, groups=None):
    return service.preview(bundle=bundle, passphrase=PASSPHRASE, groups=groups or ["persona", "self_notes"])


def apply(service, bundle, plan, **changes):
    return service.apply_bundle(**dict(bundle=bundle, passphrase=PASSPHRASE, groups=[row["id"] for row in plan["groups"]], preview_token=plan["preview_token"], request_id="import", **changes))


def test_existing_authenticated_envelope_omits_excluded_state_and_randomizes(tmp_path):
    source, bundle = source_bundle(tmp_path / "source")
    source.continuity.configure(heartbeat_paused=True, expected_revision=0, request_id="pause")
    (source.home / "config.json").write_text('{"provider_secret":"DO_NOT_EXPORT"}')
    encoded = json.dumps(bundle)
    assert bundle["format"] == FORMAT
    assert "Gideon" not in encoded
    assert "telescope" not in encoded
    assert "DO_NOT_EXPORT" not in encoded
    raw = json.loads(decrypt_archive(base64.b64decode(bundle["ciphertext"]), PASSPHRASE))
    assert set(raw) == {"format", "groups"}
    assert raw["groups"]["persona"] == ["My name is Gideon."]
    assert raw["groups"]["self_notes"] == ["Retain telescope calibration."]
    assert "heartbeat_paused" not in json.dumps(raw)
    second = source.export_bundle(groups=["persona", "self_notes"], passphrase=PASSPHRASE)
    assert second["ciphertext"] != bundle["ciphertext"]
    assert decrypt_archive(base64.b64decode(second["ciphertext"]), PASSPHRASE) == decrypt_archive(base64.b64decode(bundle["ciphertext"]), PASSPHRASE)
    inventory = source.inventory()
    assert inventory["groups"] == [{"id": "persona", "count": 1, "cap_chars": 400}, {"id": "self_notes", "count": 1, "cap_chars": 500}]
    assert "provider_configuration" in inventory["exclusions"]


def test_two_home_selective_import_replay_and_destination_policy_unchanged(tmp_path):
    source, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    destination.continuity.configure(heartbeat_paused=True, expected_revision=0, request_id="pause")
    before_policy = destination.continuity.policy()
    plan = preview(destination, bundle, ["persona"])
    assert plan["can_apply"] is True
    assert plan["groups"] == [{"id": "persona", "new": ["My name is Gideon."], "duplicates": [], "tombstoned": [], "blocked": False}]
    assert destination.continuity.status()["slots"]["persona"] == []
    result = apply(destination, bundle, plan)
    assert result == {"status": "applied", "applied": [{"slot": "persona", "count": 1}], "skipped": [{"slot": "persona", "count": 0}], "errors": []}
    assert destination.continuity.policy() == before_policy
    assert destination.continuity.status()["slots"]["self_notes"] == []
    assert destination.continuity.status()["slots"]["persona"][0]["text"] == "My name is Gideon."
    reopened = BundleService(destination.home)
    assert apply(reopened, bundle, plan) == result
    assert len(reopened.continuity.status()["memory_events"]) == 1
    assert source.continuity.status()["slots"]["self_notes"][0]["text"] == "Retain telescope calibration."
    stored = destination.path.read_bytes()
    assert PASSPHRASE.encode() not in stored
    assert b"My name is Gideon" not in stored


def test_preview_duplicates_human_tombstones_and_no_resurrection(tmp_path):
    _, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    destination.continuity.append_anchor(slot="persona", text="My name is Gideon.")
    destination.continuity.append_anchor(slot="self_notes", text="Retain telescope calibration.")
    destination.continuity.remove_anchor(slot="self_notes", text="Retain telescope calibration.")
    plan = preview(destination, bundle)
    assert plan["groups"][0]["duplicates"] == ["My name is Gideon."]
    assert plan["groups"][1]["tombstoned"] == ["Retain telescope calibration."]
    before = destination.continuity.status()["memory_events"]
    result = apply(destination, bundle, plan)
    assert result["status"] == "applied"
    assert sum(row["count"] for row in result["applied"]) == 0
    assert sum(row["count"] for row in result["skipped"]) == 2
    assert destination.continuity.status()["memory_events"] == before
    assert destination.continuity.status()["slots"]["self_notes"][0]["tombstoned"] is True
    exported = destination.export_bundle(groups=["self_notes"], passphrase=PASSPHRASE)
    assert json.loads(decrypt_archive(base64.b64decode(exported["ciphertext"]), PASSPHRASE))["groups"]["self_notes"] == []


def test_stale_preview_and_capacity_refuse_before_any_apply(tmp_path):
    _, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    plan = preview(destination, bundle)
    destination.continuity.append_anchor(slot="persona", text="x" * 395)
    with pytest.raises(ConflictError, match="preview again"):
        apply(destination, bundle, plan)
    current = preview(destination, bundle)
    assert current["can_apply"] is False
    assert current["groups"][0]["blocked"] is True
    with pytest.raises(ValueError, match="capacity"):
        apply(destination, bundle, current)
    assert destination.continuity.status()["slots"]["self_notes"] == []
    assert destination.continuity.status()["slots"]["persona"][0]["text"] == "x" * 395


def test_wrong_passphrase_and_tampering_do_not_create_destination_memory(tmp_path):
    _, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    with pytest.raises(ValueError, match="authentication"):
        destination.preview(bundle=bundle, passphrase="wrong passphrase value", groups=["persona"])
    raw = bytearray(base64.b64decode(bundle["ciphertext"]))
    raw[-1] ^= 1
    altered = {**bundle, "ciphertext": base64.b64encode(raw).decode()}
    with pytest.raises(ValueError, match="authentication"):
        preview(destination, altered)
    assert not (destination.home / "memory.db").exists()
    assert destination.continuity.policy()["revision"] == 0


@pytest.mark.parametrize("payload", [
    {"format": "other", "groups": {"persona": []}},
    {"format": FORMAT, "groups": {"credentials": []}},
    {"format": FORMAT, "groups": {"persona": ["duplicate", "duplicate"]}},
    {"format": FORMAT, "groups": {"persona": ["x" * 401]}},
    {"format": FORMAT, "groups": {"persona": [" trim "]}},
    {"format": FORMAT, "groups": {"persona": []}, "provider": "override"},
])
def test_authenticated_but_invalid_payload_refused(payload, tmp_path):
    encrypted = encrypt_archive(json.dumps(payload).encode(), PASSPHRASE)
    bundle = {"format": FORMAT, "ciphertext": base64.b64encode(encrypted).decode()}
    destination = BundleService(tmp_path)
    with pytest.raises(ValueError):
        preview(destination, bundle, ["persona"])
    assert not (tmp_path / "memory.db").exists()


def test_real_database_write_failure_reports_partial_and_does_not_replay(tmp_path):
    _, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    destination.continuity.append_anchor(slot="persona", text="Existing")
    with sqlite3.connect(destination.home / "memory.db") as db:
        db.execute("CREATE TRIGGER refuse_self_notes BEFORE INSERT ON semantic_memory WHEN NEW.key='slot.self_notes' BEGIN SELECT RAISE(ABORT,'write refused'); END")
    plan = preview(destination, bundle)
    result = apply(destination, bundle, plan)
    assert result["status"] == "partial"
    assert result["applied"] == [{"slot": "persona", "count": 1}, {"slot": "self_notes", "count": 0}]
    assert result["errors"][0]["slot"] == "self_notes"
    assert "My name is Gideon." in destination.continuity.status()["context"]
    assert destination.continuity.status()["slots"]["self_notes"] == []
    assert apply(destination, bundle, plan) == result
    assert len(destination.continuity.status()["slots"]["persona"]) == 2


def test_receipt_failure_retains_honest_applying_state(tmp_path):
    _, bundle = source_bundle(tmp_path / "source")
    destination = BundleService(tmp_path / "destination")
    plan = preview(destination, bundle, ["persona"])
    with sqlite3.connect(destination.path) as db:
        db.execute("CREATE TRIGGER refuse_receipt BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        apply(destination, bundle, plan)
    replay = apply(destination, bundle, plan)
    assert replay["status"] == "applying"
    assert replay["applied"] == []
    assert len(destination.continuity.status()["slots"]["persona"]) == 1
    with pytest.raises(ConflictError, match="different bundle"):
        destination.apply_bundle(bundle=bundle, passphrase=PASSPHRASE, groups=["persona"], preview_token="changed", request_id="import")


@pytest.mark.asyncio
async def test_actual_http_encryption_preview_import_and_native_inventory(tmp_path):
    source, bundle = source_bundle(tmp_path / "source")
    app = web.Application()
    register(app, home=tmp_path / "destination")
    async with TestClient(TestServer(app)) as client:
        inventory = await client.get(PREFIX)
        assert inventory.status == 200
        assert inventory.headers["Cache-Control"] == "no-store"
        assert (await inventory.json())["groups"][0]["count"] == 0
        body = dict(bundle=bundle, passphrase=PASSPHRASE, groups=["persona"])
        response = await client.post(PREFIX + "/preview", json=body)
        assert response.status == 200
        plan = await response.json()
        response = await client.post(PREFIX + "/apply", json={**body, "preview_token": plan["preview_token"], "request_id": "http"})
        assert response.status == 200
        assert (await response.json())["status"] == "applied"
        response = await client.post(PREFIX + "/export", json={"groups": ["persona"], "passphrase": PASSPHRASE})
        exported = await response.json()
        assert exported["format"] == FORMAT
        assert "My name" not in json.dumps(exported)
        assert (await client.post(PREFIX + "/preview", json={**body, "passphrase": "incorrect passphrase"})).status == 400
        assert (await client.post(PREFIX + "/apply", json={**body, "preview_token": "stale", "request_id": "new"})).status == 409
    provider = IdentityToolProvider(tmp_path / "destination")
    token = set_current_session_key("dashboard:bundles")
    try:
        result = await provider.invoke("identity_bundle_inventory", {})
        assert result.success
        assert json.loads(result.output)["groups"][0]["count"] == 1
        assert "My name" not in result.output
        refused = await provider.invoke("identity_bundle_export", {"passphrase": PASSPHRASE})
        assert not refused.success
        assert "Unknown" in refused.error
    finally:
        reset_current_session_key(token)
