from __future__ import annotations

import asyncio
import hashlib
import imaplib
import json
import mailbox
import re
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from uuid import uuid4

from gideon.core.atomic_write import atomic_write
from gideon.core.config.credentials import get_credential
from gideon.security.net import STRICT, EgressBlocked
from gideon.security.net.client import fetch
from gideon.workspace.artifacts.models import kind_for_mime
from gideon.workspace.artifacts.native import NativeArtifactProvider

from .evidence import ingest
from .store import PeopleError, fields, person_values, text

ACCOUNT_FIELDS = {
    "name",
    "kind",
    "owner_email",
    "alias",
    "host",
    "username",
    "credential_ref",
    "auth_mode",
    "inbox_folder",
    "sent_folder",
}
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 1000
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_SYNC_LOCK = threading.Lock()
COVERAGE = [
    {"adapter": "maildir", "implemented": True, "qualification": "local_files"},
    {"adapter": "mbox", "implemented": True, "qualification": "local_files"},
    {
        "adapter": "imap",
        "implemented": True,
        "qualification": "external_credentials_required",
    },
    {
        "adapter": "gmail",
        "implemented": True,
        "transport": "imap_tls",
        "qualification": "external_credentials_required",
    },
    {
        "adapter": "outlook",
        "implemented": True,
        "transport": "imap_tls",
        "qualification": "external_credentials_required",
    },
    {
        "adapter": "teams",
        "implemented": True,
        "integration": "communications.14",
        "qualification": "separate_connector",
    },
]


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS mirror_accounts (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS mirror_messages (account_id TEXT NOT NULL, external_id TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(account_id,external_id))"
    )


def account_values(data):
    fields(data, ACCOUNT_FIELDS)
    result = {
        key: text(data.get(key, default), key, limit, required)
        for key, default, limit, required in (
            ("name", "", 200, True),
            ("host", "", 253, False),
            ("username", "", 320, False),
            ("credential_ref", "", 120, False),
            ("inbox_folder", "INBOX", 128, True),
            ("sent_folder", "Sent", 128, True),
        )
    }
    owner = person_values(
        {
            "name": result["name"],
            "identities": [{"kind": "email", "value": data.get("owner_email")}],
        }
    )
    result.update(
        owner_email=owner["identities"][0]["value"],
        kind=data.get("kind"),
        alias=data.get("alias", "custom"),
        auth_mode=data.get("auth_mode", "password"),
    )
    if result["kind"] not in ("maildir", "mbox", "imap") or result["alias"] not in (
        "custom",
        "gmail",
        "outlook",
    ):
        raise PeopleError(
            "Unsupported mirror kind or provider alias; Teams needs a separate connector"
        )
    if result["auth_mode"] not in ("password", "xoauth2"):
        raise PeopleError("Unsupported IMAP authentication mode")
    if any(
        "\r" in result[key] or "\n" in result[key]
        for key in ("username", "credential_ref", "inbox_folder", "sent_folder")
    ):
        raise PeopleError("Account fields cannot contain control lines")
    if result["kind"] == "imap" and (
        not re.fullmatch(r"[A-Za-z0-9.-]+", result["host"])
        or not result["username"]
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result["credential_ref"])
    ):
        raise PeopleError("IMAP requires a hostname, username and credential reference")
    if result["kind"] != "imap" and any(
        result[k] for k in ("host", "username", "credential_ref")
    ):
        raise PeopleError("Local sources do not accept remote credentials")
    return result


def accounts(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            {**json.loads(body), "revision": revision}
            for body, revision in db.execute(
                "SELECT body,revision FROM mirror_accounts ORDER BY id"
            )
        ]


def get_account(store, account_id):
    found = next((row for row in accounts(store) if row["id"] == account_id), None)
    if found is None:
        raise PeopleError("Mirror account not found", 404)
    return found


def save_account(store, data, account_id=None):
    fields(data, ACCOUNT_FIELDS | ({"revision"} if account_id else set()))
    revision = data.get("revision")
    if account_id and (type(revision) is not int or revision < 1):
        raise PeopleError("Account revision is required")
    values = account_values({k: v for k, v in data.items() if k != "revision"})
    account_id = account_id or uuid4().hex
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body,revision FROM mirror_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if revision is not None and old is None:
            raise PeopleError("Mirror account not found", 404)
        if old and old[1] != revision:
            raise PeopleError("Mirror account changed; reload", 409)
        if old and any(
            json.loads(old[0])[key] != values[key] for key in ("kind", "owner_email")
        ):
            raise PeopleError(
                "Mirror kind and owner identity are immutable; create another account",
                409,
            )
        values.update(
            id=account_id, sync={"state": "not_synced", "coverage": "unknown"}
        )
        next_revision = old[1] + 1 if old else 1
        db.execute(
            "INSERT INTO mirror_accounts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (account_id, json.dumps(values), next_revision),
        )
    return {**values, "revision": next_revision}


def source_root(store, account):
    root = store.path.parent / "mailboxes"
    target = root / account["id"]
    root.mkdir(exist_ok=True)
    if (
        root.is_symlink()
        or target.is_symlink()
        or not re.fullmatch(r"[a-f0-9]{32}", account["id"])
    ):
        raise PeopleError("Mailbox source must stay in its account directory")
    target.mkdir(exist_ok=True)
    if target.resolve().parent != root.resolve():
        raise PeopleError("Mailbox source escapes its account directory")
    return target


def upload(store, account_id, data):
    fields(data, {"content", "folder"})
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise PeopleError("Upload content is required")
    raw = content.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise PeopleError("Mailbox upload exceeds 2 MiB")
    account = get_account(store, account_id)
    if account["kind"] == "imap":
        raise PeopleError("Remote accounts do not accept local uploads")
    folder = data.get("folder", "INBOX")
    if folder not in ("INBOX", "Sent"):
        raise PeopleError("Upload folder must be INBOX or Sent")
    root = source_root(store, account)
    if account["kind"] == "mbox":
        if not content.startswith("From "):
            raise PeopleError("Mbox data must contain From envelope separators")
        target = root / "archive.mbox"
    else:
        boxroot = root / folder
        if boxroot.is_symlink():
            raise PeopleError("Mailbox folder cannot be a symlink")
        box = mailbox.Maildir(boxroot, create=True)
        box.close()
        if (boxroot / "cur").is_symlink():
            raise PeopleError("Mailbox storage cannot be a symlink")
        target = boxroot / "cur" / (hashlib.sha256(raw).hexdigest() + ":2,S")
    if target.is_symlink():
        raise PeopleError("Mailbox file cannot be a symlink")
    same = target.exists() and target.read_bytes() == raw
    if not same:
        atomic_write(target, content)
    return {
        "source_digest": hashlib.sha256(raw).hexdigest(),
        "changed": not same,
        "bytes": len(raw),
    }


def _fetch_attachment(url, content_type, policy):
    bounded = policy.with_overrides(
        max_redirects=0, max_bytes=MAX_ATTACHMENT_BYTES + 1, timeout_s=10
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(
                lambda: asyncio.run(
                    fetch(
                        str(url).strip(),
                        policy=bounded,
                        headers={
                            "Accept": content_type,
                            "User-Agent": "Gideon-Mail-Mirror/1",
                        },
                    )
                )
            ).result()
    except (EgressBlocked, OSError, ValueError):
        raise PeopleError("Attachment retrieval failed", 503) from None
    if response.status != 200:
        raise PeopleError("Attachment retrieval returned an unsuccessful status", 503)
    declared = response.headers.get("Content-Length")
    if declared is not None and (
        not declared.isdigit() or int(declared) > MAX_ATTACHMENT_BYTES
    ):
        raise PeopleError("Attachment response exceeds the size limit", 503)
    received_type = (
        response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
    )
    if received_type != content_type.casefold():
        raise PeopleError(
            "Attachment response content type does not match the message", 503
        )
    data = response.body
    if response.truncated or len(data) > MAX_ATTACHMENT_BYTES:
        raise PeopleError("Attachment response exceeds the size limit", 503)
    return data


def _valid_content(data, content_type):
    signatures = {
        "application/pdf": (b"%PDF-",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/webp": (b"RIFF",),
        "video/mp4": (b"\x00\x00\x00",),
        "video/webm": (b"\x1aE\xdf\xa3",),
        "video/quicktime": (b"\x00\x00\x00",),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
            b"PK\x03\x04",
        ),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
            b"PK\x03\x04",
        ),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
            b"PK\x03\x04",
        ),
    }
    expected = signatures.get(content_type)
    if (
        not data
        or expected is None
        or not any(data.startswith(prefix) for prefix in expected)
    ):
        return False
    if content_type == "image/webp" and data[8:12] != b"WEBP":
        return False
    if content_type in ("video/mp4", "video/quicktime") and data[4:8] != b"ftyp":
        return False
    return True


def _artifact_provider(store):
    root = store.path.parent
    if root.name == "communications" and root.parent.name == "capabilities":
        root = root.parent.parent
    return NativeArtifactProvider(root / "artifacts")


def _materialize_attachment(store, account_id, external_id, index, attachment):
    data = attachment.pop("_data")
    digest = hashlib.sha256(data).hexdigest()
    source_id = hashlib.sha256(
        f"{account_id}\0{external_id}\0{index}".encode()
    ).hexdigest()
    attachment.update(source_id=source_id, sha256=digest, size=len(data))
    kind = kind_for_mime(attachment["content_type"])
    if not kind or not _valid_content(data, attachment["content_type"]):
        attachment.update(state="unsupported_content", artifact_id=None)
        return
    slug = f"mail-{account_id[:12]}-{source_id[:16]}-{digest[:16]}"
    provider = _artifact_provider(store)
    existing = provider.get(slug)
    if existing is None:
        artifact = provider.create_binary(
            name=attachment["filename"] or "Mail attachment",
            data=data,
            mime=attachment["content_type"],
            kind=kind,
            source="import",
            slug=slug,
            description=f"Mail attachment from {external_id[:500]}",
            tags=["mail-attachment", account_id],
            actor="system",
            event_metadata={"source_id": source_id, "sha256": digest},
        )
    else:
        raw = provider.raw_bytes(slug)
        if (
            raw is None
            or raw[1] != attachment["content_type"]
            or hashlib.sha256(raw[0]).hexdigest() != digest
        ):
            raise PeopleError(
                "Attachment artifact identity conflicts with stored bytes", 409
            )
        artifact = existing
    attachment.update(
        state="materialized", artifact_id=artifact.slug, artifact_uri=artifact.content
    )


def normalize_message(raw, owner, fallback, attachment_policy=STRICT):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    external_id = str(message.get("Message-ID", "")).strip() or fallback
    references = (
        str(message.get("References", "")).split()
        or str(message.get("In-Reply-To", "")).split()
    )

    def addresses(header):
        return [
            address.casefold()
            for _, address in getaddresses(message.get_all(header, []))
            if address
        ]

    sender, recipients = addresses("From"), addresses("To") + addresses("Cc")
    direction = "outbound" if owner in sender else "inbound"
    occurred_at = None
    try:
        observed = parsedate_to_datetime(str(message.get("Date", "")))
        if observed.tzinfo is not None:
            occurred_at = observed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        pass
    bodies, attachments = [], []
    for part_index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            if len(attachments) >= MAX_ATTACHMENTS:
                raise PeopleError("Message has too many attachments")
            content_type = part.get_content_type().casefold()
            data = part.get_payload(decode=True) or b""
            location = part.get("Content-Location", "")
            if location:
                if data:
                    raise PeopleError(
                        "Attachment cannot contain bytes and a remote URL"
                    )
                data = _fetch_attachment(location, content_type, attachment_policy)
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise PeopleError("Attachment exceeds the size limit")
            filename = part.get_filename() or ""
            if (
                len(filename) > 500
                or "\x00" in filename
                or "/" in filename
                or "\\" in filename
            ):
                raise PeopleError("Attachment filename is invalid")
            attachments.append(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "part_index": part_index,
                    "_data": data,
                }
            )
        elif part.get_content_type() == "text/plain":
            try:
                bodies.append(part.get_content())
            except (LookupError, UnicodeError):
                bodies.append(
                    (part.get_payload(decode=True) or b"").decode(
                        "utf-8", errors="replace"
                    )
                )
    return {
        "external_id": external_id[:500],
        "thread_id": (references[0] if references else external_id)[:100],
        "occurred_at": occurred_at,
        "direction": direction,
        "subject": str(message.get("Subject", ""))[:1000],
        "body": "\n".join(bodies)[:100000],
        "sender": sender,
        "recipients": recipients,
        "attachments": attachments,
        "source_digest": hashlib.sha256(raw).hexdigest(),
    }


def local_messages(store, account):
    root = source_root(store, account)
    payloads, complete = [], True
    if account["kind"] == "mbox":
        path = root / "archive.mbox"
        if not path.exists() or path.is_symlink():
            raise PeopleError("Upload an mbox archive before syncing")
        box = mailbox.mbox(path, create=False)
        try:
            for key in box.iterkeys():
                if len(payloads) >= MAX_MESSAGES:
                    complete = False
                    break
                raw = box.get_bytes(key)
                if sum(map(len, payloads)) + len(raw) > MAX_SCAN_BYTES:
                    complete = False
                    break
                payloads.append(raw)
        finally:
            box.close()
    else:
        for folder in ("INBOX", "Sent"):
            path = root / folder
            if path.is_symlink():
                raise PeopleError("Mailbox folder cannot be a symlink")
            box = mailbox.Maildir(path, create=True)
            try:
                for key in box.iterkeys():
                    if len(payloads) >= MAX_MESSAGES:
                        complete = False
                        break
                    for sub in ("cur", "new"):
                        if (path / sub).is_symlink() or any(
                            p.is_symlink() for p in (path / sub).iterdir()
                        ):
                            raise PeopleError("Mailbox files must remain account-owned")
                    raw = box.get_bytes(key)
                    if sum(map(len, payloads)) + len(raw) > MAX_SCAN_BYTES:
                        complete = False
                        break
                    payloads.append(raw)
            finally:
                box.close()
    if any(len(raw) > MAX_SOURCE_BYTES for raw in payloads):
        raise PeopleError("Source message exceeds 2 MiB")
    return payloads, complete


def imap_messages(account):
    secret = get_credential(account["credential_ref"])
    if not secret:
        raise PeopleError(
            "IMAP credential is unavailable; connect this account first", 503
        )
    client = None
    try:
        client = imaplib.IMAP4_SSL(
            account["host"], 993, ssl_context=ssl.create_default_context(), timeout=20
        )
        if account["auth_mode"] == "xoauth2":
            client.authenticate(
                "XOAUTH2",
                lambda _: f"user={account['username']}\x01auth=Bearer {secret}\x01\x01".encode(),
            )
        else:
            client.login(account["username"], secret)
        payloads, complete = [], True
        for folder in dict.fromkeys((account["inbox_folder"], account["sent_folder"])):
            if client.select(folder, readonly=True)[0] != "OK":
                raise PeopleError("IMAP folder is unavailable", 503)
            validity = client.response("UIDVALIDITY")[1]
            if not validity or not validity[0] or validity[0] == b"0":
                complete = False
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise PeopleError("IMAP search failed", 503)
            uids = data[0].split() if data and data[0] else []
            for uid in uids:
                if len(payloads) >= MAX_MESSAGES:
                    complete = False
                    break
                status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                raw = next(
                    (item[1] for item in fetched or [] if isinstance(item, tuple)), None
                )
                if (
                    status != "OK"
                    or not isinstance(raw, bytes)
                    or len(raw) > MAX_SOURCE_BYTES
                ):
                    raise PeopleError(
                        "IMAP message fetch failed or exceeded the size limit", 503
                    )
                if sum(map(len, payloads)) + len(raw) > MAX_SCAN_BYTES:
                    complete = False
                    break
                payloads.append(raw)
        return payloads, complete
    except (imaplib.IMAP4.error, OSError, ValueError):
        raise PeopleError(
            "IMAP TLS sync failed; check account connection and folder access", 503
        ) from None
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def messages(store, account_id):
    get_account(store, account_id)
    with closing(store.connect()) as db:
        return [
            json.loads(body)
            for body, in db.execute(
                "SELECT body FROM mirror_messages WHERE account_id=? ORDER BY external_id",
                (account_id,),
            )
        ]


def sync(store, account_id, *, attachment_policy=STRICT):
    if not _SYNC_LOCK.acquire(blocking=False):
        raise PeopleError(
            "A mail sync is already running; retry after it completes", 409
        )
    try:
        return _sync(store, account_id, attachment_policy=attachment_policy)
    finally:
        _SYNC_LOCK.release()


def _sync(store, account_id, *, attachment_policy=STRICT):
    account = get_account(store, account_id)
    captured = datetime.now(timezone.utc).isoformat()
    try:
        payloads, complete = (
            imap_messages(account)
            if account["kind"] == "imap"
            else local_messages(store, account)
        )
        normalized = {}
        for raw in payloads:
            row = normalize_message(
                raw,
                account["owner_email"],
                hashlib.sha256(raw).hexdigest(),
                attachment_policy,
            )
            if (
                row["external_id"] in normalized
                and normalized[row["external_id"]] != row
            ):
                raise PeopleError("Conflicting messages share the same Message-ID", 409)
            normalized[row["external_id"]] = row
        for row in normalized.values():
            for attachment in row["attachments"]:
                _materialize_attachment(
                    store,
                    account_id,
                    row["external_id"],
                    attachment.pop("part_index"),
                    attachment,
                )
        people = store.people()
        events = []
        for row in normalized.values():
            if row["occurred_at"] is None or row["occurred_at"] > captured:
                complete = False
                continue
            counterparts = (
                row["recipients"] if row["direction"] == "outbound" else row["sender"]
            )
            matches = [
                person
                for person in people
                if any(
                    i["kind"] == "email" and i["value"] in counterparts
                    for i in person["identities"]
                )
            ]
            if len(matches) == 1:
                events.append(
                    {
                        "person_id": matches[0]["id"],
                        "thread_id": row["thread_id"],
                        "external_id": row["external_id"],
                        "occurred_at": row["occurred_at"],
                        "direction": row["direction"],
                        "summary": row["subject"][:2000],
                    }
                )
        result = {
            "state": "synced",
            "seen": len(normalized),
            "coverage": (
                "complete_source_snapshot" if complete else "partial_source_snapshot"
            ),
            "captured_at": captured,
            "scope": (
                "remote_selected_folders"
                if account["kind"] == "imap"
                else "uploaded_or_local_source"
            ),
            "evidence_status": "pending",
            "external_delivery": "not_performed",
        }
        with closing(store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT revision FROM mirror_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if current[0] != account["revision"]:
                raise PeopleError(
                    "Account changed during sync; retry with current settings", 409
                )
            for row in normalized.values():
                db.execute(
                    "INSERT INTO mirror_messages VALUES (?,?,?) ON CONFLICT(account_id,external_id) DO UPDATE SET body=excluded.body",
                    (account_id, row["external_id"], json.dumps(row)),
                )
            if complete:
                for (external_id,) in db.execute(
                    "SELECT external_id FROM mirror_messages WHERE account_id=?",
                    (account_id,),
                ).fetchall():
                    if external_id not in normalized:
                        db.execute(
                            "DELETE FROM mirror_messages WHERE account_id=? AND external_id=?",
                            (account_id, external_id),
                        )
            body = {k: v for k, v in account.items() if k != "revision"}
            body["sync"] = result
            db.execute(
                "UPDATE mirror_accounts SET body=? WHERE id=?",
                (json.dumps(body), account_id),
            )
        evidence_batch = {
            "source": account["kind"],
            "source_account_id": account_id,
            "captured_at": captured,
            "coverage_start": min(
                (event["occurred_at"] for event in events), default=captured
            ),
            "coverage_end": captured,
            "incoming_complete": complete,
            "outgoing_complete": complete,
            "messages": events,
        }
        try:
            ingest(store, evidence_batch)
            result["evidence_status"] = "recorded"
        except PeopleError as exc:
            result["evidence_status"] = "conflict"
            result["evidence_error"] = str(exc)
        with closing(store.connect()) as db, db:
            body["sync"] = result
            db.execute(
                "UPDATE mirror_accounts SET body=? WHERE id=? AND revision=?",
                (json.dumps(body), account_id, account["revision"]),
            )
        return result
    except (PeopleError, OSError) as exc:
        if not isinstance(exc, PeopleError):
            exc = PeopleError("Mailbox source could not be read", 503)
        with closing(store.connect()) as db, db:
            body = {k: v for k, v in account.items() if k != "revision"}
            body["sync"] = {"state": "failed", "error": str(exc), "coverage": "unknown"}
            db.execute(
                "UPDATE mirror_accounts SET body=? WHERE id=? AND revision=?",
                (json.dumps(body), account_id, account["revision"]),
            )
        raise exc
