"""Credential-backed Moltbook REST adapter with durable guarded action receipts."""

import asyncio
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import aiohttp

from gideon.core.sqlite_compat import sqlite3
from gideon.integrations.llm.credentials import CredentialStore

DEFAULT_BASE = "https://www.moltbook.com/api/v1"
SORTS = {"hot", "new", "top", "rising"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class MoltbookError(ValueError):
    def __init__(
        self,
        message,
        status=400,
        code="invalid_request",
        dispatched=False,
        retry_after=None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.dispatched = dispatched
        self.retry_after = retry_after


def utcnow():
    return datetime.now(timezone.utc)


def now():
    return utcnow().isoformat()


def clean(value, limit, required=True):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise MoltbookError("Invalid text field")
    return value.strip()


def ident(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise MoltbookError("Invalid identifier")
    return value


def fields(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise MoltbookError("Unexpected fields or invalid object")


def safe_base(value):
    parsed = urlsplit(value)
    official = value.rstrip("/") == DEFAULT_BASE
    loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        not (official or loopback)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise MoltbookError("Moltbook endpoint is not allowed")
    return value.rstrip("/")


class MoltbookAdapter:
    def __init__(self, home=None, base_url=DEFAULT_BASE, timeout=15):
        self.home = (
            Path(home)
            if home is not None
            else __import__(
                "gideon.core.config.loader", fromlist=["config_dir"]
            ).config_dir()
        )
        self.path = self.home / "capabilities/experience/moltbook.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base_url = safe_base(base_url)
        self.timeout = timeout
        with self.db() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS moltbook_config(id INTEGER PRIMARY KEY CHECK(id=1),record TEXT);CREATE TABLE IF NOT EXISTS moltbook_actions(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,digest TEXT,record TEXT);PRAGMA user_version=1;"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def configure(self, payload):
        fields(payload, {"credential_ref"})
        reference = clean(payload.get("credential_ref"), 128)
        credential = self._credential(reference)
        record = {
            "credential_ref": reference,
            "base_url": DEFAULT_BASE,
            "credential_kind": credential.kind,
            "configured": True,
            "registration_supported": False,
            "external_qualified": False,
            "updated_at": now(),
        }
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO moltbook_config VALUES(1,?)",
                (json.dumps(record),),
            )
        return record

    def config(self):
        with self.db() as db:
            row = db.execute("SELECT record FROM moltbook_config WHERE id=1").fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "configured": False,
                "base_url": DEFAULT_BASE,
                "registration_supported": False,
                "external_qualified": False,
            }
        )

    def _credential(self, reference=None):
        reference = reference or self.config().get("credential_ref")
        if not reference:
            raise MoltbookError(
                "Configure a named Moltbook credential first", 409, "credential_missing"
            )
        try:
            credential = CredentialStore(self.home).resolve(reference)
        except KeyError as exc:
            raise MoltbookError(
                "Named Moltbook credential is not configured", 409, "credential_missing"
            ) from exc
        if credential.kind not in {"api_key", "static_token"} or not credential.secret:
            raise MoltbookError(
                "Named Moltbook credential has no usable secret",
                409,
                "credential_missing",
            )
        return credential

    async def _request(self, method, endpoint, body=None):
        credential = self._credential()
        headers = {
            "Authorization": "Bearer " + credential.secret,
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        dispatched = method != "GET"
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, self.base_url + endpoint, headers=headers, json=body
                ) as response:
                    raw = await response.text()
                    data = json.loads(raw) if raw else {}
                    if not isinstance(data, (dict, list)):
                        raise MoltbookError(
                            "Moltbook returned an invalid document",
                            502,
                            "invalid_response",
                            dispatched,
                        )
                    if response.status >= 400:
                        message = (
                            (data.get("error") or data.get("message"))
                            if isinstance(data, dict)
                            else None
                        )
                        retry = response.headers.get("Retry-After")
                        retry = int(retry) if retry and retry.isdigit() else None
                        raise MoltbookError(
                            clean(str(message or f"HTTP {response.status}"), 500),
                            response.status if response.status < 500 else 503,
                            (
                                "rate_limited"
                                if response.status == 429
                                else "platform_error"
                            ),
                            dispatched,
                            retry,
                        )
                    return data
        except MoltbookError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            raise MoltbookError(
                (
                    "Moltbook request outcome is uncertain"
                    if dispatched
                    else "Moltbook is unavailable"
                ),
                503,
                "outcome_uncertain" if dispatched else "platform_unavailable",
                dispatched,
            ) from exc

    def _record(
        self,
        kind,
        method,
        endpoint,
        status,
        detail=None,
        request_id=None,
        digest_value=None,
        identity=None,
    ):
        created = now()
        record = {
            "id": identity or str(uuid4()),
            "request_id": request_id,
            "kind": kind,
            "method": method,
            "endpoint": endpoint,
            "status": status,
            "detail": detail or {},
            "created_at": created,
            "updated_at": created,
        }
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO moltbook_actions VALUES(?,?,?,?)",
                (
                    record["id"],
                    request_id,
                    digest_value,
                    json.dumps(record),
                ),
            )
        return record

    def _update(self, identity, status, detail):
        with self.db() as db:
            row = db.execute(
                "SELECT record FROM moltbook_actions WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise MoltbookError(
                    "Moltbook action receipt not found", 404, "not_found"
                )
            record = {
                **json.loads(row[0]),
                "status": status,
                "detail": detail,
                "updated_at": now(),
            }
            db.execute(
                "UPDATE moltbook_actions SET record=? WHERE id=?",
                (json.dumps(record), identity),
            )
            return record

    def history(self):
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT record FROM moltbook_actions ORDER BY rowid DESC LIMIT 200"
                )
            ]

    async def read(self, action, **args):
        if action == "profile":
            endpoint = "/agents/me"
        elif action == "status":
            endpoint = "/agents/status"
        elif action == "feed":
            sort = args.get("sort", "new")
            limit = args.get("limit", 15)
            if sort not in SORTS or type(limit) is not int or not 1 <= limit <= 50:
                raise MoltbookError("Invalid feed query")
            endpoint = f"/feed?sort={sort}&limit={limit}"
        elif action == "comments":
            endpoint = (
                "/posts/" + quote(ident(args.get("post_id")), safe="") + "/comments"
            )
        else:
            raise MoltbookError("Unknown Moltbook read action")
        try:
            data = await self._request("GET", endpoint)
        except MoltbookError as error:
            self._record(
                action,
                "GET",
                endpoint,
                "failed",
                {"code": error.code, "status": error.status},
            )
            raise
        count = (
            len(data.get("posts", data.get("comments", [])))
            if isinstance(data, dict)
            else len(data)
        )
        self._record(action, "GET", endpoint, "succeeded", {"items": count})
        return data

    def _cooldown(self, kind):
        seconds = 1800 if kind == "post" else 20
        with self.db() as db:
            rows = db.execute(
                "SELECT record FROM moltbook_actions ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        for row in rows:
            record = json.loads(row[0])
            if record["kind"] == kind and record["status"] in {
                "published",
                "pending_verification",
                "uncertain",
            }:
                remaining = (
                    datetime.fromisoformat(record["updated_at"])
                    + timedelta(seconds=seconds)
                    - utcnow()
                ).total_seconds()
                if remaining > 0:
                    raise MoltbookError(
                        f"Moltbook {kind} cooldown has not elapsed",
                        429,
                        "rate_limited",
                        retry_after=int(remaining) + 1,
                    )

    async def write(self, payload, approved=False):
        fields(
            payload,
            {
                "request_id",
                "kind",
                "post_id",
                "parent_id",
                "submolt",
                "title",
                "content",
            },
        )
        request_id = ident(payload.get("request_id"))
        kind = payload.get("kind")
        if kind not in {"post", "comment"}:
            raise MoltbookError("Moltbook write kind must be post or comment")
        if not approved:
            raise MoltbookError(
                "Explicit approval is required for Moltbook writes",
                403,
                "approval_required",
            )
        content = clean(payload.get("content"), 40000)
        summary = {"content_hash": hashlib.sha256(content.encode()).hexdigest()}
        if kind == "post":
            submolt = ident(payload.get("submolt"))
            title = clean(payload.get("title"), 300)
            endpoint = "/posts"
            body = {"submolt": submolt, "title": title, "content": content}
            summary.update(submolt=submolt, title=title)
        else:
            post_id = ident(payload.get("post_id"))
            parent = payload.get("parent_id")
            endpoint = "/posts/" + quote(post_id, safe="") + "/comments"
            body = {"content": content}
            summary["post_id"] = post_id
            if parent is not None:
                body["parent_id"] = ident(parent)
                summary["parent_id"] = parent
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        with self.db() as db:
            prior = db.execute(
                "SELECT digest,record FROM moltbook_actions WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if prior:
            if prior[0] != fingerprint:
                raise MoltbookError(
                    "Moltbook request ID already used", 409, "request_conflict"
                )
            return json.loads(prior[1])
        self._cooldown(kind)
        receipt = self._record(
            kind, "POST", endpoint, "prepared", summary, request_id, fingerprint
        )
        try:
            data = await self._request("POST", endpoint, body)
        except MoltbookError as error:
            return self._update(
                receipt["id"],
                (
                    "uncertain"
                    if error.dispatched and error.code == "outcome_uncertain"
                    else "failed"
                ),
                {
                    **summary,
                    "code": error.code,
                    "remote_status": error.status,
                    "retry_after": error.retry_after,
                },
            )
        verification = data.get("verification") if isinstance(data, dict) else None
        required = bool(
            isinstance(data, dict)
            and (data.get("verification_required") or verification)
        )
        remote = (
            data.get("id") or data.get("post_id") or (data.get("post") or {}).get("id")
            if isinstance(data, dict)
            else None
        )
        detail = {**summary, "remote_id": remote}
        if required:
            detail["verification"] = {
                "code": (
                    verification.get("code") if isinstance(verification, dict) else None
                ),
                "expires_at": (
                    verification.get("expires_at")
                    if isinstance(verification, dict)
                    else None
                ),
            }
        return self._update(
            receipt["id"], "pending_verification" if required else "published", detail
        )
