import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.workflows.project_archive import decrypt_archive, is_encrypted
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register
from gideon.workspace.capabilities.wellbeing.exports import ExportStore
from gideon.workspace.capabilities.wellbeing.privacy import SCOPES, PrivacyStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError

PASSWORD = "a deliberately long local passphrase"
VALUE = "private-person-48291@example.test"


def subject(store, request="subject", **changes):
    return store.create_subject(
        dict(
            request_id=request,
            alias="Household alias",
            relationship="household",
            source="owner declaration",
            **changes,
        )
    )


def grant(store, identity, scope="vault", granted=True, revision=0, request=None):
    return store.consent(
        identity,
        dict(
            request_id=request or scope + str(revision),
            revision=revision,
            scope=scope,
            granted=granted,
            method="explicit local consent",
        ),
    )


def fact(**changes):
    value = dict(
        request_id="fact",
        type="email",
        label="Personal email",
        value=VALUE,
        passphrase=PASSWORD,
        source="directly supplied",
        use_for_scans=False,
    )
    value.update(changes)
    return value


def ready(home):
    store = PrivacyStore(home)
    person = subject(store)
    grant(store, person["id"])
    return store, person


def test_subject_scoped_consent_required_revoked_and_audited(tmp_path):
    store = PrivacyStore(tmp_path)
    person = subject(store)
    assert store.get_subject(person["id"]) == person
    assert store.list_subjects() == [person]
    assert store.consents(person["id"]) == []
    assert all(not store.allowed(person["id"], scope) for scope in SCOPES)
    with pytest.raises(MeasurementError) as error:
        store.create_fact(person["id"], fact())
    assert error.value.status == 403
    assert error.value.code == "consent_required"
    assert store.list_facts(person["id"]) == []
    consent = grant(store, person["id"])
    assert consent["revision"] == 1
    assert consent["granted"] is True
    assert consent["method"] == "explicit local consent"
    assert grant(store, person["id"]) == consent
    row = store.create_fact(person["id"], fact())
    revoke = grant(store, person["id"], granted=False, revision=1)
    assert revoke["revision"] == 2
    assert not store.allowed(person["id"], "vault")
    with pytest.raises(MeasurementError, match="consent"):
        store.create_fact(person["id"], fact())
    with pytest.raises(MeasurementError, match="consent"):
        store.correct_fact(
            row["id"],
            dict(request_id="correct", revision=1, value="new", passphrase=PASSWORD),
        )
    assert len(store.history_fact(row["id"])) == 1
    assert store.consents(person["id"]) == [consent, revoke]
    with pytest.raises(MeasurementError, match="changed"):
        grant(store, person["id"], revision=1, request="stale")
    events = store.audit(person["id"])
    assert [event["operation"] for event in events] == [
        "subject_created",
        "consent_recorded",
        "fact_written",
        "consent_recorded",
    ]
    assert events[-1]["granted"] is False
    assert VALUE not in json.dumps(events)
    assert PASSWORD not in json.dumps(events)


def test_real_authenticated_encryption_masks_every_read_and_plaintext_never_persists(
    tmp_path,
):
    store, person = ready(tmp_path)
    row = store.create_fact(person["id"], fact())
    assert row["masked_value"] == "••••"
    assert "value" not in row
    assert "cipher" not in row
    assert "passphrase" not in row
    assert row["type"] == "email"
    assert row["source"] == "directly supplied"
    assert row["use_for_scans"] is False
    assert store.list_facts(person["id"]) == [row]
    assert store.get_fact(row["id"]) == row
    assert store.history_fact(row["id"]) == [row]
    assert PrivacyStore(tmp_path).get_fact(row["id"]) == row
    with store.connection() as db:
        meta, cipher = db.execute("SELECT data,cipher FROM privacy_facts").fetchone()
        kind, receipt, result = db.execute(
            "SELECT kind,payload,result FROM privacy_requests WHERE id='fact'"
        ).fetchone()
    assert is_encrypted(bytes(cipher))
    assert is_encrypted(bytes(receipt))
    assert kind == "fact"
    plain = json.loads(decrypt_archive(bytes(cipher), PASSWORD))
    assert plain == dict(subject_id=person["id"], id=row["id"], revision=1, value=VALUE)
    request = json.loads(decrypt_archive(bytes(receipt), PASSWORD))
    assert request["value"] == VALUE
    assert "passphrase" not in request
    assert VALUE not in meta + result
    assert PASSWORD not in meta + result
    raw = store.path.read_bytes()
    assert VALUE.encode() not in raw
    assert PASSWORD.encode() not in raw
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / ".env").exists()
    assert len(list(tmp_path.rglob("*"))) == 2


def test_explicit_reveal_consent_wrong_passphrase_revocation_and_audit(tmp_path):
    store, person = ready(tmp_path)
    row = store.create_fact(person["id"], fact())
    payload = dict(passphrase=PASSWORD, reason="Verify my saved contact")
    with pytest.raises(MeasurementError, match="consent"):
        store.reveal(row["id"], payload)
    assert store.audit(person["id"])[-1]["outcome"] == "denied"
    grant(store, person["id"], "reveal")
    with pytest.raises(MeasurementError, match="authenticated"):
        store.reveal(row["id"], dict(payload, passphrase="a wrong but long password"))
    assert store.audit(person["id"])[-1]["outcome"] == "denied"
    result = store.reveal(row["id"], payload)
    assert result == dict(id=row["id"], revision=1, value=VALUE)
    event = store.audit(person["id"])[-1]
    assert event["operation"] == "fact_reveal"
    assert event["reason"] == payload["reason"]
    assert event["outcome"] == "revealed"
    grant(store, person["id"], "reveal", False, 1)
    with pytest.raises(MeasurementError, match="consent"):
        PrivacyStore(tmp_path).reveal(row["id"], payload)
    assert store.audit(person["id"])[-1]["outcome"] == "denied"
    assert VALUE not in json.dumps(store.audit(person["id"]))
    assert PASSWORD not in store.path.read_bytes().decode("latin1")


def test_corrections_preserve_encrypted_history_require_old_key_and_archive(tmp_path):
    store, person = ready(tmp_path)
    row = store.create_fact(person["id"], fact())
    grant(store, person["id"], "reveal")
    payload = dict(
        request_id="correction",
        revision=1,
        value="new-private-value@example.test",
        passphrase=PASSWORD,
        label="Updated email",
        use_for_scans=True,
    )
    with pytest.raises(MeasurementError, match="authenticated"):
        store.correct_fact(
            row["id"], dict(payload, passphrase="different valid long password")
        )
    corrected = store.correct_fact(row["id"], payload)
    assert corrected["revision"] == 2
    assert corrected["label"] == "Updated email"
    assert corrected["source"] == row["source"]
    assert corrected["created_at"] == row["created_at"]
    assert corrected["use_for_scans"] is True
    assert store.correct_fact(row["id"], payload) == corrected
    assert store.history_fact(row["id"]) == [row, corrected]
    assert (
        store.reveal(row["id"], dict(passphrase=PASSWORD, reason="Verify correction"))[
            "value"
        ]
        == payload["value"]
    )
    with pytest.raises(MeasurementError, match="changed"):
        store.correct_fact(row["id"], dict(payload, request_id="stale"))
    archived = store.correct_fact(
        row["id"],
        dict(
            request_id="archive",
            revision=2,
            value=payload["value"],
            passphrase=PASSWORD,
            archived=True,
        ),
    )
    assert archived["archived"] is True
    with pytest.raises(MeasurementError, match="Archived"):
        store.reveal(
            row["id"], dict(passphrase=PASSWORD, reason="Should refuse archive")
        )
    assert store.get_fact(row["id"]) == archived
    assert len(store.history_fact(row["id"])) == 3
    assert payload["value"].encode() not in store.path.read_bytes()


def test_ciphertext_tamper_and_swapping_across_facts_are_detected(tmp_path):
    store, person = ready(tmp_path)
    first = store.create_fact(person["id"], fact())
    second = store.create_fact(
        person["id"], fact(request_id="second", value="other-private@example.test")
    )
    grant(store, person["id"], "reveal")
    with store.connection() as db:
        original = bytes(
            db.execute(
                "SELECT cipher FROM privacy_facts WHERE id=?", (first["id"],)
            ).fetchone()[0]
        )
        other = bytes(
            db.execute(
                "SELECT cipher FROM privacy_facts WHERE id=?", (second["id"],)
            ).fetchone()[0]
        )
        db.execute("UPDATE privacy_facts SET cipher=? WHERE id=?", (other, first["id"]))
    with pytest.raises(MeasurementError, match="identity authentication"):
        store.reveal(first["id"], dict(passphrase=PASSWORD, reason="Integrity check"))
    with store.connection() as db:
        altered = original[:-1] + bytes([original[-1] ^ 1])
        db.execute(
            "UPDATE privacy_facts SET cipher=? WHERE id=?", (altered, first["id"])
        )
    with pytest.raises(MeasurementError, match="authenticated"):
        store.reveal(first["id"], dict(passphrase=PASSWORD, reason="Integrity check"))
    assert store.audit(person["id"])[-1]["outcome"] == "denied"
    assert (
        store.reveal(
            second["id"], dict(passphrase=PASSWORD, reason="Unaffected record")
        )["value"]
        == "other-private@example.test"
    )


def test_subject_home_isolation_and_no_health_export_or_artifact_projection(tmp_path):
    store, person = ready(tmp_path / "one")
    row = store.create_fact(person["id"], fact())
    second = subject(store, request="second-subject")
    assert store.list_facts(second["id"]) == []
    assert not store.allowed(second["id"], "vault")
    with pytest.raises(MeasurementError, match="consent"):
        store.create_fact(second["id"], fact(request_id="other-fact"))
    isolated = PrivacyStore(tmp_path / "two")
    assert isolated.list_subjects() == []
    with pytest.raises(MeasurementError) as missing:
        isolated.get_fact(row["id"])
    assert missing.value.status == 404
    exports = ExportStore(tmp_path / "one")
    metadata = exports.create({"request_id": "archive"})
    raw = exports.download(metadata["id"])
    for secret in (
        VALUE,
        PASSWORD,
        person["id"],
        person["alias"],
        row["id"],
        row["label"],
    ):
        assert secret.encode() not in raw
    assert all(count == 0 for count in metadata["counts"].values())
    assert metadata["attachment_count"] == 0


@pytest.mark.parametrize(
    "change",
    [
        dict(type="unknown"),
        dict(type=[]),
        dict(type="tax_id", use_for_scans=True),
        dict(type="passport", use_for_scans=True),
        dict(use_for_scans="yes"),
        dict(label=""),
        dict(value=""),
        dict(source=""),
        dict(passphrase="short"),
        dict(value={"bad": True}),
        dict(home="/elsewhere"),
    ],
)
def test_invalid_secret_writes_do_not_persist_values(tmp_path, change):
    store, person = ready(tmp_path)
    with pytest.raises(MeasurementError):
        store.create_fact(person["id"], fact(**change))
    assert store.list_facts(person["id"]) == []
    assert VALUE.encode() not in store.path.read_bytes()
    assert PASSWORD.encode() not in store.path.read_bytes()


@pytest.mark.parametrize(
    "scope,granted,revision",
    [
        ("unknown", True, 0),
        ([], True, 0),
        ("vault", "yes", 0),
        ("vault", True, True),
        ("vault", True, -1),
    ],
)
def test_invalid_consent_is_not_granted(tmp_path, scope, granted, revision):
    store = PrivacyStore(tmp_path)
    person = subject(store)
    with pytest.raises(MeasurementError):
        store.consent(
            person["id"],
            dict(
                request_id="bad",
                scope=scope,
                granted=granted,
                revision=revision,
                method="owner",
            ),
        )
    assert store.consents(person["id"]) == []


def test_private_request_conflict_is_authenticated_and_concurrent_retry_single_revision(
    tmp_path,
):
    store, person = ready(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(
            pool.map(
                lambda _: PrivacyStore(tmp_path).create_fact(person["id"], fact()),
                range(3),
            )
        )
    assert all(row == rows[0] for row in rows)
    assert len(store.list_facts(person["id"])) == 1
    assert len(store.history_fact(rows[0]["id"])) == 1
    with pytest.raises(MeasurementError, match="already used"):
        store.create_fact(person["id"], fact(value="different-secret"))
    with pytest.raises(MeasurementError, match="authenticated"):
        store.create_fact(person["id"], fact(passphrase="wrong long passphrase"))
    with pytest.raises(MeasurementError, match="already used"):
        store.create_subject(
            dict(request_id="fact", alias="other", relationship="self", source="owner")
        )
    assert VALUE.encode() not in store.path.read_bytes()


@pytest.mark.asyncio
async def test_agent_tools_are_metadata_only_without_secret_parameters(tmp_path):
    store, person = ready(tmp_path)
    row = store.create_fact(person["id"], fact())
    provider = WellbeingProvider(tmp_path)
    tool = (await provider.list_tools())[0]
    operations = tool.parameters["properties"]["operation"]["enum"]
    assert "privacy_fact" in operations
    assert "privacy_reveal" not in operations
    assert "privacy_create_fact" not in operations
    for operation, identity in [
        ("privacy_subjects", None),
        ("privacy_subject", person["id"]),
        ("privacy_consents", person["id"]),
        ("privacy_facts", person["id"]),
        ("privacy_fact", row["id"]),
        ("privacy_fact_history", row["id"]),
        ("privacy_audit", person["id"]),
    ]:
        result = await provider.invoke(
            "wellbeing_records", dict(operation=operation, id=identity)
        )
        assert result.success
        assert VALUE not in result.output
        assert PASSWORD not in result.output
        assert "cipher" not in result.output
    for operation in ("privacy_reveal", "privacy_create_fact", "privacy_fact"):
        result = await provider.invoke(
            "wellbeing_records",
            dict(
                operation=operation,
                id=row["id"],
                payload={"passphrase": PASSWORD, "value": VALUE},
            ),
        )
        assert not result.success
        assert VALUE not in str(result)
        assert PASSWORD not in str(result)


@pytest.mark.asyncio
async def test_real_http_owner_consent_create_correct_reveal_and_wrong_home(tmp_path):
    app, other = web.Application(), web.Application()
    register(app, tmp_path / "a")
    register(other, tmp_path / "b")
    base = "/api/capabilities/wellbeing/privacy"
    async with (
        TestClient(TestServer(app)) as client,
        TestClient(TestServer(other)) as isolated,
    ):
        response = await client.post(
            base + "/subjects",
            json=dict(
                request_id="subject",
                alias="Self alias",
                relationship="self",
                source="owner",
            ),
        )
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        person = await response.json()
        subject_url = base + "/subjects/" + person["id"]
        assert await (await client.get(subject_url)).json() == person
        assert (await client.post(subject_url + "/facts", json=fact())).status == 403
        assert (
            await client.post(
                subject_url + "/consents",
                json=dict(
                    request_id="vault",
                    revision=0,
                    scope="vault",
                    granted=True,
                    method="local consent",
                ),
            )
        ).status == 200
        response = await client.post(subject_url + "/facts", json=fact())
        assert response.status == 200
        row = await response.json()
        assert VALUE not in json.dumps(row)
        fact_url = base + "/facts/" + row["id"]
        assert (
            await client.post(
                fact_url + "/reveal", json=dict(passphrase=PASSWORD, reason="check")
            )
        ).status == 403
        assert (
            await client.post(
                subject_url + "/consents",
                json=dict(
                    request_id="reveal",
                    revision=0,
                    scope="reveal",
                    granted=True,
                    method="local consent",
                ),
            )
        ).status == 200
        response = await client.post(
            fact_url + "/reveal", json=dict(passphrase=PASSWORD, reason="check")
        )
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert (await response.json())["value"] == VALUE
        response = await client.put(
            fact_url,
            json=dict(
                request_id="correct",
                revision=1,
                value="replacement@example.test",
                passphrase=PASSWORD,
            ),
        )
        assert response.status == 200
        assert (await response.json())["revision"] == 2
        assert (
            len((await (await client.get(fact_url + "/history")).json())["history"])
            == 2
        )
        assert VALUE not in await (await client.get(subject_url + "/audit")).text()
        assert VALUE not in await (await client.get(subject_url + "/facts")).text()
        assert (await isolated.get(fact_url)).status == 404
        assert (
            await isolated.post(
                fact_url + "/reveal",
                json=dict(passphrase=PASSWORD, reason="wrong home"),
            )
        ).status == 404
        assert (await isolated.get(subject_url)).status == 404
        assert (
            await client.post(
                base + "/subjects",
                data="{",
                headers={"Content-Type": "application/json"},
            )
        ).status == 400
        assert (
            await client.post(
                fact_url + "/reveal",
                json=dict(
                    passphrase=PASSWORD, reason="check", home=str(tmp_path / "b")
                ),
            )
        ).status == 400
        assert VALUE.encode() not in PrivacyStore(tmp_path / "a").path.read_bytes()
        assert PASSWORD.encode() not in PrivacyStore(tmp_path / "a").path.read_bytes()
