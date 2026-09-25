import base64
import hashlib
import hmac
import json
import struct

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    signal_archive,
)

KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
FIXTURE = __file__.replace(
    "test_signal_archive.py", "fixtures/signal_sqlcipher4.sqlite"
)


def encrypted():
    return open(FIXTURE, "rb").read()


def request(account="owned-signal", raw=None, key=KEY):
    return {
        "source_account_id": account,
        "content_base64": base64.b64encode(
            raw if raw is not None else encrypted()
        ).decode(),
        "key": key,
    }


def person(store, name="Signal Friend"):
    return store.save(
        {"name": name, "identities": [{"kind": "phone", "value": "+1 (202) 555-0123"}]}
    )


def reviewed(store, data=None):
    data = data or request()
    preview = signal_archive.preview(store, data)
    return {
        **data,
        "source_digest": preview["source_digest"],
        "review_token": preview["review_token"],
    }


def test_sqlcipher_cli_fixture_has_the_supported_authenticated_page_profile():
    raw = encrypted()
    assert len(raw) == 3 * signal_archive.PAGE_SIZE
    assert not raw.startswith(b"SQLite format 3\x00")
    salt = raw[:16]
    encryption_key = bytes.fromhex(KEY)
    hmac_key = hashlib.pbkdf2_hmac(
        "sha512", encryption_key, bytes(value ^ 0x3A for value in salt), 2, 32
    )
    for page in range(1, 4):
        start = (page - 1) * signal_archive.PAGE_SIZE
        body_start = start + (16 if page == 1 else 0)
        iv_start = start + signal_archive.PAGE_SIZE - signal_archive.RESERVE_BYTES
        authenticated = raw[body_start : iv_start + 16]
        stored = raw[iv_start + 16 : start + signal_archive.PAGE_SIZE]
        expected = hmac.new(
            hmac_key, authenticated + struct.pack("<I", page), hashlib.sha512
        ).digest()
        assert hmac.compare_digest(stored, expected)
    plaintext = signal_archive.decrypt_sqlcipher4(raw, encryption_key)
    try:
        assert plaintext.startswith(b"SQLite format 3\x00")
        assert plaintext[16:18] == b"\x10\x00"
        assert plaintext[20] == signal_archive.RESERVE_BYTES
    finally:
        plaintext[:] = b"\x00" * len(plaintext)


def test_actual_sqlcipher4_archive_authenticates_decrypts_and_normalizes(tmp_path):
    store = PeopleStore(tmp_path)
    friend = person(store)
    result = signal_archive.preview(store, request())
    assert result["qualification"] == "authenticated_sqlcipher4"
    assert result["coverage"] == "supplied_encrypted_archive"
    assert len(result["source_digest"]) == 64
    assert len(result["review_token"]) == 64
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["external_id"] == "conversation-1:message-1"
    assert row["message_id"] == "message-1"
    assert row["conversation_id"] == "conversation-1"
    assert row["occurred_at"] == "2023-11-14T22:13:20+00:00"
    assert row["direction"] == "inbound"
    assert row["body"] == "Encrypted hello"
    assert row["identity"] == {"kind": "phone", "value": "+12025550123"}
    assert row["person_id"] == friend["id"]
    assert row["eligible"] is True
    assert row["attachments"] == [
        {
            "path": "attachments/file-1",
            "name": "photo.jpg",
            "content_type": "image/jpeg",
            "size": 1234,
        }
    ]
    assert signal_archive.imports(store) == []
    assert signal_archive.history(store, "owned-signal") == []


def test_wrong_key_tamper_plaintext_and_bad_key_are_rejected(tmp_path):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError, match="authentication failed"):
        signal_archive.preview(store, request(key="ff" * 32))
    changed = bytearray(encrypted())
    changed[100] ^= 1
    with pytest.raises(PeopleError, match="authentication failed"):
        signal_archive.preview(store, request(raw=changed))
    with pytest.raises(PeopleError, match="encrypted SQLCipher"):
        signal_archive.preview(store, request(raw=b"SQLite format 3\x00" + bytes(4080)))
    with pytest.raises(PeopleError, match="64 hexadecimal"):
        signal_archive.preview(store, request(key="not-a-key"))
    with pytest.raises(PeopleError, match="SQLCipher-4 page file"):
        signal_archive.preview(store, request(raw=bytes(4095)))
    assert signal_archive.imports(store) == []


def test_commit_persists_only_normalized_history_and_is_exactly_retryable(tmp_path):
    store = PeopleStore(tmp_path)
    friend = person(store)
    data = reviewed(store)
    receipt, created = signal_archive.commit(store, data)
    assert created is True
    assert receipt["messages"] == 1
    assert receipt["inserted"] == 1
    assert receipt["linked"] == 1
    assert receipt["coverage"] == "supplied_encrypted_archive"
    assert receipt["qualification"] == "authenticated_sqlcipher4"
    assert signal_archive.commit(PeopleStore(tmp_path), data) == (receipt, False)
    assert signal_archive.imports(PeopleStore(tmp_path)) == [receipt]
    history = signal_archive.history(PeopleStore(tmp_path), "owned-signal")
    assert history[0]["body"] == "Encrypted hello"
    assert history[0]["attachments"][0]["name"] == "photo.jpg"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM signal_messages").fetchone()[0] == 1
        event = json.loads(
            db.execute(
                'SELECT body FROM relationship_messages WHERE source="signal"'
            ).fetchone()[0]
        )
        assert event["person_id"] == friend["id"]
        assert event["external_id"] == "conversation-1:message-1"
    persisted = store.path.read_bytes()
    assert KEY.encode() not in persisted
    assert encrypted() not in persisted
    plaintext = signal_archive.decrypt_sqlcipher4(encrypted(), bytes.fromhex(KEY))
    try:
        assert bytes(plaintext) not in persisted
    finally:
        plaintext[:] = b"\x00" * len(plaintext)


def test_review_binding_and_absent_identity_prevent_unreviewed_linkage(tmp_path):
    store = PeopleStore(tmp_path)
    preview = signal_archive.preview(store, request())
    assert preview["rows"][0]["person_id"] is None
    assert preview["rows"][0]["eligible"] is False
    data = {
        **request(),
        "source_digest": preview["source_digest"],
        "review_token": preview["review_token"],
    }
    with pytest.raises(PeopleError, match="preview it again") as changed:
        signal_archive.commit(store, {**data, "review_token": "different"})
    assert changed.value.status == 409
    receipt, created = signal_archive.commit(store, data)
    assert created is True
    assert receipt["inserted"] == 1
    assert receipt["linked"] == 0
    assert store.people() == []


def test_plaintext_buffer_is_zeroed_after_success_and_schema_failure(
    tmp_path, monkeypatch
):
    store = PeopleStore(tmp_path)
    captured = []
    original = signal_archive._rows

    def inspect(raw):
        captured.append(raw)
        return original(raw)

    monkeypatch.setattr(signal_archive, "_rows", inspect)
    signal_archive.preview(store, request())
    assert captured and set(captured[0]) == {0}

    def fail(raw):
        captured.append(raw)
        raise PeopleError("Unsupported Signal Desktop schema")

    monkeypatch.setattr(signal_archive, "_rows", fail)
    with pytest.raises(PeopleError, match="Unsupported Signal"):
        signal_archive.preview(store, request())
    assert set(captured[-1]) == {0}


@pytest.mark.asyncio
async def test_real_http_preview_commit_list_and_history(
    tmp_path, monkeypatch, unused_tcp_port
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = PeopleStore(tmp_path / "capabilities" / "communications")
    person(store)
    app = web.Application()
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    root = f"http://127.0.0.1:{unused_tcp_port}/api/capabilities/communications/signal-archive"
    try:
        async with ClientSession() as client:
            preview_response = await client.post(root + "/preview", json=request())
            assert preview_response.status == 200
            preview = await preview_response.json()
            assert preview["rows"][0]["body"] == "Encrypted hello"
            payload = {
                **request(),
                "source_digest": preview["source_digest"],
                "review_token": preview["review_token"],
            }
            commit_response = await client.post(root + "/commit", json=payload)
            assert commit_response.status == 201
            committed = await commit_response.json()
            assert committed["created"] is True
            assert committed["receipt"]["linked"] == 1
            listed = await (await client.get(root + "/imports")).json()
            assert listed["imports"] == [committed["receipt"]]
            history = await (
                await client.get(root + "/history?source_account_id=owned-signal")
            ).json()
            assert history["messages"][0]["external_id"] == "conversation-1:message-1"
            failed = await client.post(root + "/preview", json=request(key="00" * 32))
            assert failed.status == 400
            assert "authentication failed" in (await failed.json())["error"]
    finally:
        await runner.cleanup()


def test_attachment_metadata_is_bounded_and_malformed_json_is_safe():
    oversized = {
        "attachments": [{"path": "p", "fileName": "f", "contentType": "x", "size": 1}]
        * 150
    }
    assert len(signal_archive._attachment_refs(json.dumps(oversized))) == 100
    assert signal_archive._attachment_refs("{broken") == []
    assert signal_archive._attachment_refs(json.dumps({"attachments": "wrong"})) == []
