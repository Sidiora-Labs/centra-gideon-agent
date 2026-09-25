import asyncio
import json
import socketserver
import threading
from contextlib import closing
from email import policy
from email.parser import BytesParser

import pytest

from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    mirrors,
)
from gideon.workspace.capabilities.communications.outbound_email import (
    OutboundEmail,
    SMTPTransport,
)
from gideon.workspace.capabilities.communications.outbound_email_tools import (
    OutboundEmailTools,
)


class SMTPContract:
    def __init__(self, *, drop_after_data=False):
        self.messages = []
        self.commands = []
        self.drop_after_data = drop_after_data

    def start(self):
        contract = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 local contract SMTP\r\n")
                sender, recipients = "", []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    value = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    contract.commands.append(value)
                    upper = value.upper()
                    if upper.startswith(("EHLO ", "HELO ")):
                        self.wfile.write(b"250-local\r\n250 SIZE 20971520\r\n")
                    elif upper.startswith("MAIL FROM:"):
                        sender = value.split(":", 1)[1].split()[0].strip("<>")
                        self.wfile.write(b"250 sender ok\r\n")
                    elif upper.startswith("RCPT TO:"):
                        recipients.append(value.split(":", 1)[1].strip("<>"))
                        self.wfile.write(b"250 recipient ok\r\n")
                    elif upper == "DATA":
                        self.wfile.write(b"354 end with dot\r\n")
                        payload = bytearray()
                        while True:
                            item = self.rfile.readline()
                            if item == b".\r\n":
                                break
                            payload.extend(item[1:] if item.startswith(b"..") else item)
                        contract.messages.append(
                            {
                                "sender": sender,
                                "recipients": recipients,
                                "data": bytes(payload),
                            }
                        )
                        if contract.drop_after_data:
                            return
                        self.wfile.write(b"250 accepted as local-1\r\n")
                    elif upper == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"250 ok\r\n")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def environment(tmp_path):
    store = PeopleStore(tmp_path / "people")
    account = mirrors.save_account(
        store,
        {
            "name": "Owned mail",
            "kind": "imap",
            "owner_email": "owner@example.com",
            "alias": "custom",
            "host": "imap.example.com",
            "username": "owner@example.com",
            "credential_ref": "OWNER_MAIL_TOKEN",
            "auth_mode": "password",
            "inbox_folder": "INBOX",
            "sent_folder": "Sent",
        },
    )
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    return store, account, artifacts


@pytest.fixture
def smtp_server():
    server = SMTPContract().start()
    yield server
    server.close()


def service(environment, smtp_server):
    store, _, artifacts = environment
    transport = SMTPTransport(
        host="127.0.0.1", port=smtp_server.port, starttls=False, authenticate=False
    )
    return OutboundEmail(store, artifacts=artifacts, transport=transport)


def payload(account, **changes):
    value = {
        "request_key": "whitepages-1",
        "account_id": account["id"],
        "to": ["privacyrequest@whitepages.com"],
        "subject": "Privacy opt-out request",
        "body": "Please remove https://www.whitepages.com/me",
    }
    value.update(changes)
    return value


def approve(outbound, row):
    return outbound.approve(
        row["id"],
        {
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
            "confirm_exact": True,
        },
    )


def dispatch(outbound, row):
    return outbound.send(
        row["id"],
        {
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
            "confirm_send": True,
        },
    )


def test_refuses_send_before_exact_approval_and_has_no_sender_or_credential_override(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    _, account, _ = environment
    with pytest.raises(PeopleError, match="Unknown or invalid fields"):
        outbound.draft(payload(account, sender="attacker@example.com"))
    with pytest.raises(PeopleError, match="Unknown or invalid fields"):
        outbound.draft(payload(account, credential="secret"))
    row, created = outbound.draft(payload(account))
    assert created and row["sender"] == "owner@example.com"
    assert row["credential_ref"] == "OWNER_MAIL_TOKEN"
    assert row["state"] == "draft" and row["provider_acceptance"] == "not_submitted"
    with pytest.raises(PeopleError, match="Approved email"):
        dispatch(outbound, row)
    with pytest.raises(PeopleError, match="Exact recipient"):
        outbound.approve(
            row["id"],
            {
                "revision": row["revision"],
                "content_sha256": "0" * 64,
                "confirm_exact": True,
            },
        )
    assert smtp_server.messages == []


def test_real_smtp_acceptance_is_durable_but_delivery_stays_uncertain(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    _, account, _ = environment
    row, _ = outbound.draft(payload(account))
    row = approve(outbound, row)
    row = dispatch(outbound, row)
    assert row["state"] == "accepted"
    assert row["provider_acceptance"] == "accepted"
    assert row["delivery"] == "uncertain"
    assert row["transport_receipt"] == {"code": 250, "response": "accepted"}
    assert len(smtp_server.messages) == 1
    captured = smtp_server.messages[0]
    assert captured["sender"] == "owner@example.com"
    assert captured["recipients"] == ["privacyrequest@whitepages.com"]
    parsed = BytesParser(policy=policy.default).parsebytes(captured["data"])
    assert parsed["From"] == "owner@example.com"
    assert parsed["To"] == "privacyrequest@whitepages.com"
    assert parsed["Subject"] == "Privacy opt-out request"
    assert (
        "Please remove https://www.whitepages.com/me" in parsed.get_body().get_content()
    )
    with pytest.raises(PeopleError, match="Approved email"):
        dispatch(outbound, row)
    assert len(smtp_server.messages) == 1
    restarted = service(environment, smtp_server)
    durable = restarted._get(row["id"])
    assert durable["state"] == "accepted" and durable["delivery"] == "uncertain"
    with pytest.raises(PeopleError, match="Approved email"):
        dispatch(restarted, durable)
    assert len(smtp_server.messages) == 1


def test_request_key_is_idempotent_and_cannot_rebind_changed_content(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    _, account, _ = environment
    first, created = outbound.draft(payload(account))
    second, replay_created = outbound.draft(payload(account))
    assert created is True and replay_created is False and second["id"] == first["id"]
    with pytest.raises(PeopleError, match="different email content"):
        outbound.draft(payload(account, body="changed after retry"))
    assert len(outbound.list()) == 1


def test_artifact_attachment_is_bounded_pinned_and_revalidated(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    _, account, artifacts = environment
    artifact = artifacts.create(
        name="Identity proof",
        slug="identity-proof",
        content="owned evidence",
        kind="document",
    )
    row, _ = outbound.draft(
        payload(
            account,
            request_key="attachment-1",
            attachments=[{"artifact_id": artifact.slug, "version": 1}],
        )
    )
    assert row["attachments"][0]["artifact_id"] == "identity-proof"
    assert row["attachments"][0]["sha256"]
    row = approve(outbound, row)
    sent = dispatch(outbound, row)
    assert sent["state"] == "accepted"
    parsed = BytesParser(policy=policy.default).parsebytes(
        smtp_server.messages[0]["data"]
    )
    attachments = list(parsed.iter_attachments())
    assert (
        len(attachments) == 1
        and attachments[0].get_payload(decode=True) == b"owned evidence"
    )
    with pytest.raises(PeopleError, match="At most ten"):
        outbound.draft(
            payload(
                account,
                request_key="too-many",
                attachments=[{"artifact_id": artifact.slug}] * 11,
            )
        )
    with pytest.raises(PeopleError, match="not found"):
        outbound.draft(
            payload(
                account,
                request_key="missing",
                attachments=[{"artifact_id": "not-owned"}],
            )
        )


def test_source_message_must_be_in_selected_accounts_canonical_inbox(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    store, account, _ = environment
    with pytest.raises(PeopleError, match="not owned"):
        outbound.draft(
            payload(
                account,
                request_key="source-missing",
                source_message_id="<missing@example>",
            )
        )
    raw = b"From: contact@example.net\r\nTo: owner@example.com\r\nMessage-ID: <owned@example>\r\nSubject: Owned\r\n\r\nHello\r\n"
    normalized = mirrors.normalize_message(raw, account["owner_email"], "fallback")
    with closing(store.connect()) as db, db:
        mirrors.schema(db)
        db.execute(
            "INSERT INTO mirror_messages VALUES (?,?,?)",
            (account["id"], normalized["external_id"], json.dumps(normalized)),
        )
    row, _ = outbound.draft(
        payload(
            account, request_key="source-owned", source_message_id="<owned@example>"
        )
    )
    assert row["source_message_id"] == "<owned@example>"


def test_ingested_reply_correlation_records_canonical_ref_and_never_opens_links(
    environment, smtp_server, monkeypatch
):
    outbound = service(environment, smtp_server)
    store, account, _ = environment
    row, _ = outbound.draft(payload(account))
    row = approve(outbound, row)
    row = dispatch(outbound, row)
    opened = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: opened.append(args)
        or (_ for _ in ()).throw(AssertionError("must not open")),
    )
    raw = (
        f"From: privacyrequest@whitepages.com\r\nTo: owner@example.com\r\nMessage-ID: <reply-1@whitepages.com>\r\n"
        f'In-Reply-To: {row["message_id"]}\r\nDate: Tue, 15 Sep 2026 12:00:00 +0000\r\nSubject: Re: Privacy opt-out request\r\n\r\n'
        "Verify at https://untrusted.example/confirm?id=7 and then wait.\r\n"
    ).encode()
    normalized = mirrors.normalize_message(raw, account["owner_email"], "fallback")
    with closing(store.connect()) as db, db:
        mirrors.schema(db)
        db.execute(
            "INSERT INTO mirror_messages VALUES (?,?,?)",
            (account["id"], normalized["external_id"], json.dumps(normalized)),
        )
    correlated = outbound.correlate(row["id"])
    assert correlated["state"] == "verified"
    assert (
        correlated["verification"]["message_external_id"] == "<reply-1@whitepages.com>"
    )
    assert correlated["verification"]["source_digest"] == normalized["source_digest"]
    assert correlated["verification"]["links"] == [
        {"url": "https://untrusted.example/confirm?id=7", "opened": False}
    ]
    assert opened == []


def test_connection_loss_after_data_is_uncertain_and_restart_never_retries(environment):
    smtp = SMTPContract(drop_after_data=True).start()
    try:
        outbound = service(environment, smtp)
        _, account, _ = environment
        row, _ = outbound.draft(payload(account))
        row = approve(outbound, row)
        row = dispatch(outbound, row)
        assert (
            row["state"] == "uncertain"
            and row["provider_acceptance"] == "uncertain"
            and row["delivery"] == "uncertain"
        )
        assert len(smtp.messages) == 1
        restarted = service(environment, smtp)
        durable = restarted._get(row["id"])
        with pytest.raises(PeopleError, match="Approved email"):
            dispatch(restarted, durable)
        assert len(smtp.messages) == 1
    finally:
        smtp.close()


def test_interrupted_sending_recovers_to_uncertain_without_network_replay(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    store, account, _ = environment
    row, _ = outbound.draft(payload(account))
    row = approve(outbound, row)
    with closing(store.connect()) as db, db:
        body = json.loads(
            db.execute(
                "SELECT body FROM outbound_email_drafts WHERE id=?", (row["id"],)
            ).fetchone()[0]
        )
        body["state"] = "sending"
        db.execute(
            "UPDATE outbound_email_drafts SET body=? WHERE id=?",
            (json.dumps(body), row["id"]),
        )
    restarted = service(environment, smtp_server)
    recovered = restarted._get(row["id"])
    assert recovered["state"] == "uncertain"
    assert recovered["error"].startswith("Transport outcome unknown after restart")
    assert smtp_server.messages == []


def test_native_tools_expose_same_durable_approval_and_send_contract(
    environment, smtp_server
):
    outbound = service(environment, smtp_server)
    _, account, _ = environment
    provider = OutboundEmailTools(outbound)
    definitions = asyncio.run(provider.list_tools())
    assert {item.name for item in definitions} == {
        "outbound_email_list",
        "outbound_email_draft",
        "outbound_email_approve",
        "outbound_email_send",
        "outbound_email_correlate",
    }
    send_tool = next(item for item in definitions if item.name == "outbound_email_send")
    assert (
        send_tool.requires_approval is True
        and send_tool.risk_level.value == "destructive"
    )
    created = asyncio.run(
        provider.invoke(
            "outbound_email_draft", payload(account, request_key="native-1")
        )
    )
    assert created.success is True
    row = json.loads(created.output)["draft"]
    refused = asyncio.run(
        provider.invoke(
            "outbound_email_send",
            {
                "draft_id": row["id"],
                "revision": row["revision"],
                "content_sha256": row["content_sha256"],
                "confirm_send": True,
            },
        )
    )
    assert refused.success is False and "Approved email" in refused.error
    approved = asyncio.run(
        provider.invoke(
            "outbound_email_approve",
            {
                "draft_id": row["id"],
                "revision": row["revision"],
                "content_sha256": row["content_sha256"],
                "confirm_exact": True,
            },
        )
    )
    row = json.loads(approved.output)["draft"]
    sent = asyncio.run(
        provider.invoke(
            "outbound_email_send",
            {
                "draft_id": row["id"],
                "revision": row["revision"],
                "content_sha256": row["content_sha256"],
                "confirm_send": True,
            },
        )
    )
    assert sent.success is True
    receipt = json.loads(sent.output)["draft"]
    assert (
        receipt["provider_acceptance"] == "accepted"
        and receipt["delivery"] == "uncertain"
    )
    assert len(smtp_server.messages) == 1
