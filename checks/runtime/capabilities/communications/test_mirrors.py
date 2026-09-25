import asyncio
import hashlib
import json
import mailbox
import os
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.security.net import LOOPBACK_INTERNAL
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    mirrors,
)
from gideon.workspace.capabilities.communications.evidence import report
from gideon.workspace.capabilities.communications.tools import create_provider


def mail(
    identifier="question",
    sender="friend@example.com",
    recipient="owner@example.com",
    reply=None,
    date=True,
    attachment=False,
):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Message-ID"] = f"<{identifier}@example.com>"
    message["Subject"] = "A real message " + identifier
    if date:
        message["Date"] = format_datetime(
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    if reply:
        message["References"] = f"<{reply}@example.com>"
    message.set_content("Plain body with café and source evidence\n")
    if attachment:
        message.add_attachment(
            b"%PDF-1.4\nactual bytes",
            maintype="application",
            subtype="pdf",
            filename="report.pdf",
        )
    return message.as_string()


def remote_mail(url, content_type="application/pdf"):
    message = EmailMessage()
    message["From"] = "friend@example.com"
    message["To"] = "owner@example.com"
    message["Message-ID"] = "<remote@example.com>"
    message["Subject"] = "Remote attachment"
    message["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=1))
    message.set_content("Remote attachment body")
    maintype, subtype = content_type.split("/", 1)
    message.add_attachment(
        b"", maintype=maintype, subtype=subtype, filename="remote.pdf"
    )
    message.get_payload()[-1]["Content-Location"] = url
    return message.as_string()


class AttachmentServer:
    def __init__(self):
        self.status = 200
        self.body = b"%PDF-1.4\nremote bytes"
        self.content_type = "application/pdf"
        self.location = ""
        self.requests = 0

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests += 1
                self.send_response(owner.status)
                self.send_header("Content-Type", owner.content_type)
                if owner.location:
                    self.send_header("Location", owner.location)
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/attachment"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def account(store, kind="maildir", **extra):
    return mirrors.save_account(
        store,
        {"name": "Personal", "kind": kind, "owner_email": "OWNER@EXAMPLE.COM", **extra},
    )


def friend(store):
    return store.save(
        {
            "name": "Friend",
            "identities": [{"kind": "email", "value": "friend@example.com"}],
        }
    )


def put(store, row, content=None, folder="INBOX"):
    return mirrors.upload(
        store, row["id"], {"content": content or mail(), "folder": folder}
    )


def test_account_restart_revision_and_no_secrets(tmp_path):
    store = PeopleStore(tmp_path)
    row = account(store)
    assert row["revision"] == 1
    assert row["owner_email"] == "owner@example.com"
    assert row["sync"]["coverage"] == "unknown"
    assert mirrors.get_account(PeopleStore(tmp_path), row["id"]) == row
    assert mirrors.accounts(store) == [row]
    data = {key: row[key] for key in mirrors.ACCOUNT_FIELDS}
    updated = mirrors.save_account(
        store, {**data, "name": "Renamed", "revision": 1}, row["id"]
    )
    assert updated["name"] == "Renamed"
    assert updated["revision"] == 2
    with pytest.raises(PeopleError) as error:
        mirrors.save_account(store, {**data, "revision": 1}, row["id"])
    assert error.value.status == 409
    assert mirrors.get_account(store, row["id"]) == updated
    with pytest.raises(PeopleError):
        mirrors.save_account(
            store,
            {**data, "owner_email": "different@example.com", "revision": 2},
            row["id"],
        )
    assert "password" not in row


@pytest.mark.parametrize(
    "extra",
    [
        {"home": "/tmp"},
        {"kind": "teams"},
        {"owner_email": "invalid"},
        {"name": ""},
        {"host": "remote.example"},
        {"auth_mode": "plain"},
        {"sent_folder": "Sent\r\nX"},
    ],
)
def test_account_validation_no_partial_write(tmp_path, extra):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        account(store, **extra)
    assert mirrors.accounts(store) == []


def test_maildir_original_upload_dedup_and_sync(tmp_path):
    store = PeopleStore(tmp_path)
    person = friend(store)
    row = account(store)
    raw = mail(attachment=True)
    result = put(store, row, raw)
    assert result["changed"] is True
    assert result["source_digest"] == hashlib.sha256(raw.encode()).hexdigest()
    assert result["bytes"] == len(raw.encode())
    replay = put(store, row, raw)
    assert replay["changed"] is False
    path = mirrors.source_root(store, row) / "INBOX"
    box = mailbox.Maildir(path)
    assert len(box) == 1
    assert next(iter(box.values()))["Subject"] == "A real message question"
    box.close()
    state = mirrors.sync(store, row["id"])
    assert state["state"] == "synced"
    assert state["seen"] == 1
    assert state["coverage"] == "complete_source_snapshot"
    assert state["scope"] == "uploaded_or_local_source"
    assert state["evidence_status"] == "recorded"
    rows = mirrors.messages(store, row["id"])
    assert len(rows) == 1
    assert rows[0]["source_digest"] == result["source_digest"]
    assert rows[0]["body"].startswith("Plain body with café")
    attachment = rows[0]["attachments"][0]
    assert attachment["filename"] == "report.pdf"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["size"] == len(b"%PDF-1.4\nactual bytes")
    assert attachment["sha256"] == hashlib.sha256(b"%PDF-1.4\nactual bytes").hexdigest()
    assert attachment["state"] == "materialized"
    assert attachment["artifact_id"].startswith("mail-" + row["id"][:12])
    provider = NativeArtifactProvider(tmp_path / "artifacts")
    assert provider.raw_bytes(attachment["artifact_id"]) == (
        b"%PDF-1.4\nactual bytes",
        "application/pdf",
    )
    assert rows[0]["direction"] == "inbound"
    assert rows[0]["sender"] == ["friend@example.com"]
    assert rows[0]["recipients"] == ["owner@example.com"]
    assert mirrors.messages(PeopleStore(tmp_path), row["id"]) == rows
    threads = report(store)["threads"]
    assert threads[0]["person_id"] == person["id"]
    assert threads[0]["state"] == "unanswered"
    assert threads[0]["qualification"] == "recorded_evidence_only"
    assert mirrors.sync(store, row["id"])["seen"] == 1
    assert len(report(store)["threads"]) == 1
    assert provider.get(attachment["artifact_id"]).version == 1


def test_remote_attachment_real_guarded_protocol_and_restart_idempotence(tmp_path):
    remote = AttachmentServer()
    try:
        store = PeopleStore(tmp_path)
        row = account(store)
        put(store, row, remote_mail(remote.url))
        with pytest.raises(PeopleError, match="retrieval failed"):
            mirrors.sync(store, row["id"])
        assert remote.requests == 0
        first = mirrors.sync(store, row["id"], attachment_policy=LOOPBACK_INTERNAL)
        assert first["seen"] == 1
        attachment = mirrors.messages(store, row["id"])[0]["attachments"][0]
        assert attachment["state"] == "materialized"
        assert attachment["sha256"] == hashlib.sha256(remote.body).hexdigest()
        restarted = PeopleStore(tmp_path)
        mirrors.sync(restarted, row["id"], attachment_policy=LOOPBACK_INTERNAL)
        persisted = mirrors.messages(restarted, row["id"])[0]["attachments"][0]
        assert persisted == attachment
        provider = NativeArtifactProvider(tmp_path / "artifacts")
        assert provider.raw_bytes(attachment["artifact_id"]) == (
            remote.body,
            "application/pdf",
        )
        assert provider.get(attachment["artifact_id"]).version == 1
    finally:
        remote.close()


@pytest.mark.parametrize("failure", ["redirect", "type", "oversize"])
def test_remote_attachment_rejects_redirect_type_and_size(tmp_path, failure):
    remote = AttachmentServer()
    try:
        if failure == "redirect":
            remote.status, remote.location = 302, remote.url
        elif failure == "type":
            remote.content_type = "text/plain"
        else:
            remote.body = b"x" * (mirrors.MAX_ATTACHMENT_BYTES + 1)
        store = PeopleStore(tmp_path)
        row = account(store)
        put(store, row, remote_mail(remote.url))
        with pytest.raises(PeopleError):
            mirrors.sync(store, row["id"], attachment_policy=LOOPBACK_INTERNAL)
        assert mirrors.messages(store, row["id"]) == []
        assert mirrors.get_account(store, row["id"])["sync"]["state"] == "failed"
    finally:
        remote.close()


def test_unsupported_attachment_bytes_are_honest_and_hashed(tmp_path):
    store = PeopleStore(tmp_path)
    row = account(store)
    message = EmailMessage()
    message["From"] = "friend@example.com"
    message["To"] = "owner@example.com"
    message["Message-ID"] = "<opaque@example.com>"
    message["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=1))
    message.set_content("body")
    message.add_attachment(
        b"opaque bytes",
        maintype="application",
        subtype="octet-stream",
        filename="data.bin",
    )
    put(store, row, message.as_string())
    mirrors.sync(store, row["id"])
    attachment = mirrors.messages(store, row["id"])[0]["attachments"][0]
    assert attachment["state"] == "unsupported_content"
    assert attachment["artifact_id"] is None
    assert attachment["sha256"] == hashlib.sha256(b"opaque bytes").hexdigest()


def test_reply_and_equal_date_ambiguity(tmp_path):
    store = PeopleStore(tmp_path)
    friend(store)
    row = account(store)
    incoming = mail()
    outgoing = mail("reply", "owner@example.com", "friend@example.com", "question")
    put(store, row, incoming)
    put(store, row, outgoing, "Sent")
    state = mirrors.sync(store, row["id"])
    assert state["seen"] == 2
    threads = report(store)["threads"]
    assert len(threads) == 1
    assert threads[0]["state"] == "unknown"
    assert {m["direction"] for m in mirrors.messages(store, row["id"])} == {
        "inbound",
        "outbound",
    }


def test_no_date_keeps_unknown_and_raw_content(tmp_path):
    store = PeopleStore(tmp_path)
    friend(store)
    row = account(store)
    put(store, row, mail(date=False))
    state = mirrors.sync(store, row["id"])
    assert state["coverage"] == "partial_source_snapshot"
    assert mirrors.messages(store, row["id"])[0]["occurred_at"] is None
    assert report(store)["threads"] == []
    assert report(store)["people"][0]["care"]["state"] == "missing"


def test_conflicting_message_id_atomic_failure_preserves_prior(tmp_path):
    store = PeopleStore(tmp_path)
    row = account(store)
    original = mail()
    put(store, row, original)
    mirrors.sync(store, row["id"])
    before = mirrors.messages(store, row["id"])
    put(
        store, row, original.replace("A real message question", "A conflicting subject")
    )
    with pytest.raises(PeopleError) as error:
        mirrors.sync(store, row["id"])
    assert error.value.status == 409
    assert mirrors.messages(store, row["id"]) == before
    assert mirrors.get_account(store, row["id"])["sync"]["state"] == "failed"
    assert mirrors.get_account(store, row["id"])["sync"]["coverage"] == "unknown"


def test_same_message_in_two_folders_deduplicates(tmp_path):
    store = PeopleStore(tmp_path)
    row = account(store)
    raw = mail()
    put(store, row, raw)
    put(store, row, raw, "Sent")
    assert mirrors.sync(store, row["id"])["seen"] == 1
    assert len(mirrors.messages(store, row["id"])) == 1


def test_real_mbox_replacement_prunes_snapshot(tmp_path):
    source = tmp_path / "input.mbox"
    box = mailbox.mbox(source)
    box.add(mail("first"))
    box.add(mail("second"))
    box.flush()
    box.close()
    store = PeopleStore(tmp_path / "runtime")
    row = account(store, "mbox")
    upload = put(store, row, source.read_text())
    assert upload["changed"] is True
    assert mirrors.sync(store, row["id"])["seen"] == 2
    assert len(mirrors.messages(store, row["id"])) == 2
    replacement = tmp_path / "replacement.mbox"
    box = mailbox.mbox(replacement)
    box.add(mail("third"))
    box.flush()
    box.close()
    put(store, row, replacement.read_text())
    assert mirrors.sync(store, row["id"])["seen"] == 1
    assert mirrors.messages(store, row["id"])[0]["external_id"] == "<third@example.com>"
    assert (
        mirrors.get_account(store, row["id"])["sync"]["scope"]
        == "uploaded_or_local_source"
    )


def test_missing_mbox_records_failure_then_recovers(tmp_path):
    store = PeopleStore(tmp_path)
    row = account(store, "mbox")
    with pytest.raises(PeopleError):
        mirrors.sync(store, row["id"])
    assert mirrors.get_account(store, row["id"])["sync"]["state"] == "failed"
    assert mirrors.messages(store, row["id"]) == []
    put(store, row, "From sender Tue Jan 1 00:00:00 2020\n" + mail())
    assert mirrors.sync(store, row["id"])["state"] == "synced"
    assert (
        mirrors.get_account(store, row["id"])["sync"]["evidence_status"] == "recorded"
    )


@pytest.mark.parametrize(
    "content", ["", " ", None, 1, "a" * (mirrors.MAX_SOURCE_BYTES + 1)]
)
def test_upload_input_bounds(tmp_path, content):
    store = PeopleStore(tmp_path)
    row = account(store)
    with pytest.raises(PeopleError):
        mirrors.upload(store, row["id"], {"content": content})
    assert mirrors.messages(store, row["id"]) == []


@pytest.mark.parametrize("level", ["root", "account", "folder", "cur", "file"])
def test_source_symlinks_rejected_without_external_write(tmp_path, level):
    store = PeopleStore(tmp_path / "runtime")
    row = account(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = store.path.parent / "mailboxes"
    raw = mail()
    if level == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif level == "account":
        root.mkdir()
        (root / row["id"]).symlink_to(outside, target_is_directory=True)
    else:
        path = mirrors.source_root(store, row)
        if level == "folder":
            (path / "INBOX").symlink_to(outside, target_is_directory=True)
        else:
            box = mailbox.Maildir(path / "INBOX")
            box.close()
            cur = path / "INBOX" / "cur"
            if level == "cur":
                cur.rmdir()
                cur.symlink_to(outside, target_is_directory=True)
            else:
                (cur / (hashlib.sha256(raw.encode()).hexdigest() + ":2,S")).symlink_to(
                    outside / "target"
                )
    with pytest.raises(PeopleError):
        put(store, row, raw)
    assert list(outside.iterdir()) == []


def test_runtime_and_account_isolation(tmp_path):
    first = PeopleStore(tmp_path / "first")
    second = PeopleStore(tmp_path / "second")
    row = account(first)
    other = account(first)
    put(first, row)
    mirrors.sync(first, row["id"])
    assert mirrors.messages(first, other["id"]) == []
    assert mirrors.accounts(second) == []
    with pytest.raises(PeopleError) as error:
        mirrors.get_account(second, row["id"])
    assert error.value.status == 404
    with pytest.raises(PeopleError):
        mirrors.upload(second, row["id"], {"content": mail()})
    assert len(mirrors.messages(first, row["id"])) == 1


def test_missing_remote_secret_is_honest_failure(tmp_path):
    store = PeopleStore(tmp_path)
    key = "GIDEON_ABSENT_MIRROR_TEST_SECRET_6F801"
    assert key not in os.environ
    row = account(
        store,
        "imap",
        host="localhost",
        username="owner",
        credential_ref=key,
        alias="gmail",
        auth_mode="xoauth2",
    )
    assert row["alias"] == "gmail"
    assert row["credential_ref"] == key
    with pytest.raises(PeopleError) as error:
        mirrors.sync(store, row["id"])
    assert error.value.status == 503
    assert "credential" in str(error.value)
    assert mirrors.messages(store, row["id"]) == []
    state = mirrors.get_account(store, row["id"])["sync"]
    assert state["coverage"] == "unknown"
    assert state["state"] == "failed"
    with pytest.raises(PeopleError):
        put(store, row)
    providers = {value["adapter"]: value for value in mirrors.COVERAGE}
    assert providers["teams"]["implemented"] is True
    assert providers["teams"]["integration"] == "communications.14"
    assert providers["gmail"]["qualification"] == "external_credentials_required"
    assert providers["outlook"]["transport"] == "imap_tls"


def test_native_mirror_tools_real_runtime(tmp_path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(tmp_path)
    try:
        provider = create_provider({"home": "/ignored"})

        async def call(name, data):
            result = await provider.invoke(name, data)
            assert result.success, result.error
            return json.loads(result.output)

        async def scenario():
            assert (await call("people_mirror_accounts", {}))["accounts"] == []
            row = (
                await call(
                    "people_mirror_create",
                    {
                        "account": {
                            "name": "Native",
                            "kind": "maildir",
                            "owner_email": "owner@example.com",
                        }
                    },
                )
            )["account"]
            upload = await call(
                "people_mirror_upload", {"account_id": row["id"], "content": mail()}
            )
            assert upload["changed"] is True
            result = await call("people_mirror_sync", {"account_id": row["id"]})
            assert result["sync"]["seen"] == 1
            data = await call("people_mirror_messages", {"account_id": row["id"]})
            assert data["messages"][0]["subject"] == "A real message question"
            update = {key: row[key] for key in mirrors.ACCOUNT_FIELDS}
            update.update(name="Updated native", revision=1)
            changed = await call(
                "people_mirror_update", {"account_id": row["id"], "account": update}
            )
            assert changed["account"]["revision"] == 2
            definitions = {tool.name: tool for tool in await provider.list_tools()}
            assert definitions["people_mirror_sync"].requires_approval is True
            assert definitions["people_mirror_messages"].requires_approval is False
            denied = await provider.invoke(
                "people_mirror_sync", {"account_id": row["id"], "home": "/outside"}
            )
            assert denied.success is False
            assert denied.metadata["status"] == 400
            missing = await provider.invoke(
                "people_mirror_messages", {"account_id": "missing"}
            )
            assert missing.success is False
            assert missing.metadata["status"] == 404
            coverage = await call("people_mirror_capabilities", {})
            assert len(coverage["adapters"]) == 6

        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def test_actual_http_routes_and_failure_statuses(tmp_path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(tmp_path)

    async def scenario():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/mirror"
        try:
            async with ClientSession() as client:
                async with client.get(base + "/capabilities") as response:
                    assert response.status == 200
                    assert len((await response.json())["adapters"]) == 6
                async with client.post(
                    base + "/accounts",
                    json={
                        "name": "HTTP",
                        "kind": "maildir",
                        "owner_email": "owner@example.com",
                    },
                ) as response:
                    assert response.status == 201
                    row = (await response.json())["account"]
                path = base + "/accounts/" + row["id"]
                async with client.get(path) as response:
                    assert (await response.json())["account"] == row
                async with client.post(
                    path + "/upload", json={"content": mail()}
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["changed"] is True
                async with client.post(path + "/sync", json={}) as response:
                    assert response.status == 200
                    assert (await response.json())["sync"]["seen"] == 1
                async with client.get(path + "/messages") as response:
                    data = await response.json()
                    assert len(data["messages"]) == 1
                    assert data["account"]["sync"]["state"] == "synced"
                async with client.post(
                    path + "/sync", json={"home": "/outside"}
                ) as response:
                    assert response.status == 400
                async with client.get(base + "/accounts/absent/messages") as response:
                    assert response.status == 404
                update = {key: row[key] for key in mirrors.ACCOUNT_FIELDS}
                async with client.put(path, json={**update, "revision": 1}) as response:
                    assert response.status == 200
                    assert (await response.json())["account"]["revision"] == 2
                async with client.put(path, json={**update, "revision": 1}) as response:
                    assert response.status == 409
                async with client.post(path + "/upload", data="not JSON") as response:
                    assert response.status == 400
                async with client.get(base + "/accounts") as response:
                    assert len((await response.json())["accounts"]) == 1
        finally:
            await runner.cleanup()

    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous
