from __future__ import annotations

import hashlib
import json
import re
import smtplib
import ssl
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from uuid import uuid4

from gideon.core.config.credentials import get_credential
from gideon.workspace.artifacts.native import NativeArtifactProvider

from . import mirrors
from .store import PeopleError, fields, text

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_DRAFT_FIELDS = {
    "request_key",
    "account_id",
    "to",
    "subject",
    "body",
    "source_message_id",
    "attachments",
}
_URL = re.compile(r'https?://[^\s<>"\']+', re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS outbound_email_drafts
        (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, request_key TEXT NOT NULL,
         body TEXT NOT NULL, revision INTEGER NOT NULL,
         UNIQUE(account_id, request_key))""")


def _addresses(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise PeopleError("to requires between one and ten recipients")
    result = []
    for item in value:
        address = text(item, "recipient", 320, True).casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
            raise PeopleError("Invalid recipient")
        if address not in result:
            result.append(address)
    return result


def _artifact(provider, value):
    slug = text(value.get("artifact_id"), "artifact_id", 200, True)
    version = value.get("version")
    if version is not None and (type(version) is not int or version < 1):
        raise PeopleError("Artifact version must be a positive integer")
    art = provider.get(slug, version=version)
    if art is None:
        raise PeopleError("Attachment artifact was not found", 404)
    raw = provider.raw_bytes(slug, version=version)
    if raw is None:
        content = (art.content or "").encode("utf-8")
        mime = art.mime or "text/plain"
    else:
        content, mime = raw
    return {
        "artifact_id": slug,
        "version": version,
        "name": art.name,
        "mime": mime,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }, content


def _message(row, provider):
    message = EmailMessage()
    message["From"] = row["sender"]
    message["To"] = ", ".join(row["to"])
    message["Subject"] = row["subject"]
    message["Date"] = format_datetime(datetime.fromisoformat(row["created_at"]))
    message["Message-ID"] = row["message_id"]
    message.set_content(row["body"])
    total = 0
    for ref in row["attachments"]:
        resolved, content = _artifact(provider, ref)
        if any(resolved[key] != ref[key] for key in ("sha256", "size", "mime")):
            raise PeopleError("Attachment changed after approval", 409)
        total += len(content)
        if total > MAX_ATTACHMENT_BYTES:
            raise PeopleError("Attachments exceed 10 MiB")
        major, _, minor = resolved["mime"].partition("/")
        message.add_attachment(
            content,
            maintype=major or "application",
            subtype=minor or "octet-stream",
            filename=resolved["name"],
        )
    return message.as_bytes(policy=SMTP)


class SMTPTransport:
    def __init__(
        self,
        *,
        host=None,
        port=587,
        starttls=True,
        authenticate=True,
        credential_loader=get_credential,
        timeout=20,
    ):
        self.host, self.port, self.starttls, self.authenticate = (
            host,
            port,
            starttls,
            authenticate,
        )
        self.credential_loader, self.timeout = credential_loader, timeout

    def send(self, account, envelope, raw):
        host = self.host or {
            "gmail": "smtp.gmail.com",
            "outlook": "smtp.office365.com",
        }.get(account["alias"], account["host"])
        if not host:
            raise PeopleError("SMTP host is unavailable for this account", 503)
        secret = self.credential_loader(account["credential_ref"])
        if self.authenticate and not secret:
            raise PeopleError(
                "Email credential is unavailable; connect this account first", 503
            )
        client = smtplib.SMTP(host, self.port, timeout=self.timeout)
        try:
            client.ehlo()
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self.authenticate:
                if account["auth_mode"] == "xoauth2":
                    token = (
                        f'user={account["username"]}\x01auth=Bearer {secret}\x01\x01'
                    )
                    client.auth("XOAUTH2", lambda _: token)
                else:
                    client.login(account["username"], secret)
            refused = client.sendmail(account["owner_email"], envelope, raw)
            if refused:
                raise PeopleError("SMTP rejected one or more recipients", 502)
            return {"code": 250, "response": "accepted"}
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                pass


class OutboundEmail:
    def __init__(self, store, *, artifacts=None, transport=None):
        self.store = store
        self.artifacts = artifacts or NativeArtifactProvider()
        self.transport = transport or SMTPTransport()
        with closing(store.connect()) as db, db:
            _schema(db)
            for raw, draft_id in db.execute(
                "SELECT body,id FROM outbound_email_drafts"
            ):
                row = json.loads(raw)
                if row["state"] == "sending":
                    row.update(
                        state="uncertain",
                        provider_acceptance="uncertain",
                        delivery="uncertain",
                        updated_at=_now(),
                        error="Transport outcome unknown after restart; automatic retry refused",
                    )
                    db.execute(
                        "UPDATE outbound_email_drafts SET body=?,revision=revision+1 WHERE id=?",
                        (json.dumps(row), draft_id),
                    )

    def _get(self, draft_id):
        with closing(self.store.connect()) as db:
            _schema(db)
            found = db.execute(
                "SELECT body,revision FROM outbound_email_drafts WHERE id=?",
                (draft_id,),
            ).fetchone()
        if not found:
            raise PeopleError("Outbound email draft not found", 404)
        return {**json.loads(found[0]), "revision": found[1]}

    def list(self):
        with closing(self.store.connect()) as db:
            _schema(db)
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM outbound_email_drafts ORDER BY rowid DESC LIMIT 200"
                )
            ]
        return [self._get(item) for item in ids]

    def draft(self, data):
        fields(data, _DRAFT_FIELDS)
        account = mirrors.get_account(
            self.store, text(data.get("account_id"), "account_id", 64, True)
        )
        if account["kind"] != "imap" or not account["credential_ref"]:
            raise PeopleError("Outbound email requires a credential-bound IMAP account")
        request_key = text(data.get("request_key"), "request_key", 200, True)
        source_id = text(data.get("source_message_id", ""), "source_message_id", 500)
        if source_id and not any(
            row["external_id"] == source_id
            for row in mirrors.messages(self.store, account["id"])
        ):
            raise PeopleError("Source message is not owned by this account", 404)
        supplied = data.get("attachments", [])
        if not isinstance(supplied, list) or len(supplied) > MAX_ATTACHMENTS:
            raise PeopleError("At most ten artifact attachments are allowed")
        attachments, total = [], 0
        for value in supplied:
            fields(value, {"artifact_id", "version"})
            ref, content = _artifact(self.artifacts, value)
            total += len(content)
            attachments.append(ref)
        if total > MAX_ATTACHMENT_BYTES:
            raise PeopleError("Attachments exceed 10 MiB")
        created = _now()
        row = {
            "id": uuid4().hex,
            "request_key": request_key,
            "account_id": account["id"],
            "account_revision": account["revision"],
            "credential_ref": account["credential_ref"],
            "sender": account["owner_email"],
            "to": _addresses(data.get("to")),
            "subject": text(data.get("subject", ""), "subject", 1000),
            "body": text(data.get("body"), "body", 100000, True),
            "source_message_id": source_id or None,
            "attachments": attachments,
            "message_id": make_msgid(domain=account["owner_email"].split("@", 1)[1]),
            "created_at": created,
            "updated_at": created,
            "state": "draft",
            "provider_acceptance": "not_submitted",
            "delivery": "not_submitted",
            "verification": None,
        }
        raw = _message(row, self.artifacts)
        row["content_sha256"] = hashlib.sha256(raw).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "sender",
                        "to",
                        "subject",
                        "body",
                        "source_message_id",
                        "attachments",
                    )
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _schema(db)
            old = db.execute(
                "SELECT id,body FROM outbound_email_drafts WHERE account_id=? AND request_key=?",
                (account["id"], request_key),
            ).fetchone()
            if old:
                existing = json.loads(old[1])
                if existing["fingerprint"] != fingerprint:
                    raise PeopleError(
                        "Request key already binds different email content", 409
                    )
                return self._get(old[0]), False
            row["fingerprint"] = fingerprint
            db.execute(
                "INSERT INTO outbound_email_drafts VALUES (?,?,?,?,?)",
                (row["id"], account["id"], request_key, json.dumps(row), 1),
            )
        return self._get(row["id"]), True

    def _change(self, draft_id, revision, fn):
        if type(revision) is not int:
            raise PeopleError("revision is required")
        with closing(self.store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _schema(db)
            found = db.execute(
                "SELECT body,revision FROM outbound_email_drafts WHERE id=?",
                (draft_id,),
            ).fetchone()
            if not found:
                raise PeopleError("Outbound email draft not found", 404)
            if found[1] != revision:
                raise PeopleError("Outbound email draft changed; reload", 409)
            row = json.loads(found[0])
            fn(row)
            row["updated_at"] = _now()
            db.execute(
                "UPDATE outbound_email_drafts SET body=?,revision=? WHERE id=?",
                (json.dumps(row), revision + 1, draft_id),
            )
        return self._get(draft_id)

    def approve(self, draft_id, data):
        fields(data, {"revision", "content_sha256", "confirm_exact"})

        def change(row):
            if row["state"] != "draft":
                raise PeopleError("Only a draft can be approved", 409)
            if (
                data.get("confirm_exact") is not True
                or data.get("content_sha256") != row["content_sha256"]
            ):
                raise PeopleError(
                    "Exact recipient and body approval digest is required"
                )
            row["state"] = "approved"
            row["approved_at"] = _now()

        return self._change(draft_id, data.get("revision"), change)

    def send(self, draft_id, data):
        fields(data, {"revision", "content_sha256", "confirm_send"})
        current = self._get(draft_id)
        if current["state"] != "approved":
            raise PeopleError("Approved email is required before dispatch", 409)
        if (
            data.get("confirm_send") is not True
            or data.get("content_sha256") != current["content_sha256"]
        ):
            raise PeopleError("Exact approved email confirmation is required")
        account = mirrors.get_account(self.store, current["account_id"])
        if (
            any(
                account[key] != current[key if key != "owner_email" else "sender"]
                for key in ("owner_email", "credential_ref")
            )
            or account["revision"] != current["account_revision"]
        ):
            raise PeopleError(
                "Account identity or credential binding changed; create a new draft",
                409,
            )
        current = self._change(
            draft_id, current["revision"], lambda row: row.update(state="sending")
        )
        try:
            receipt = self.transport.send(
                account, current["to"], _message(current, self.artifacts)
            )
        except PeopleError as exc:
            message = str(exc)
            self._change(
                draft_id,
                current["revision"],
                lambda row: row.update(
                    state="failed",
                    provider_acceptance="rejected",
                    delivery="not_accepted",
                    error=message,
                ),
            )
            raise
        except (OSError, smtplib.SMTPException) as exc:
            message = str(exc)
            return self._change(
                draft_id,
                current["revision"],
                lambda row: row.update(
                    state="uncertain",
                    provider_acceptance="uncertain",
                    delivery="uncertain",
                    error=message,
                ),
            )
        return self._change(
            draft_id,
            current["revision"],
            lambda row: row.update(
                state="accepted",
                provider_acceptance="accepted",
                delivery="uncertain",
                transport_receipt=receipt,
                accepted_at=_now(),
            ),
        )

    def correlate(self, draft_id):
        current = self._get(draft_id)
        if current["state"] not in ("accepted", "uncertain", "verified"):
            raise PeopleError("Dispatch must be attempted before correlation", 409)
        matches = [
            row
            for row in mirrors.messages(self.store, current["account_id"])
            if row["direction"] == "inbound"
            and row["thread_id"] == current["message_id"]
        ]
        if not matches:
            return current
        found = sorted(matches, key=lambda row: row.get("occurred_at") or "")[-1]
        verification = {
            "account_id": current["account_id"],
            "message_external_id": found["external_id"],
            "source_digest": found["source_digest"],
            "subject": found["subject"],
            "links": [
                {"url": url.rstrip(".,)"), "opened": False}
                for url in _URL.findall(found["body"])[:20]
            ],
            "correlated_at": _now(),
        }
        return self._change(
            draft_id,
            current["revision"],
            lambda row: row.update(state="verified", verification=verification),
        )
