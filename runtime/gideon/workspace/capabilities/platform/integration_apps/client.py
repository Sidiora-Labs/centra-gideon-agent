"""Guarded real API transport and GitHub sealed-box encryption."""

import base64
import ctypes
import ctypes.util
import json
from urllib.parse import urlencode

from gideon.sdk.net import CONNECTOR, fetch
from gideon.workspace.capabilities.music.store import DomainError


def sodium():
    path = ctypes.util.find_library("sodium")
    if not path:
        raise DomainError(
            "Actions secret encryption requires operator-installed libsodium",
            503,
            "encryption_unavailable",
        )
    library = ctypes.CDLL(path)
    library.sodium_init.restype = ctypes.c_int
    if library.sodium_init() < 0:
        raise DomainError("Secret encryption initialization failed", 503)
    library.crypto_box_seal.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
    ]
    library.crypto_box_seal.restype = ctypes.c_int
    return library


def seal(value, key):
    try:
        public = base64.b64decode(key, validate=True)
    except (ValueError, TypeError) as exc:
        raise DomainError("Invalid GitHub public key", 502) from exc
    if (
        len(public) != 32
        or not isinstance(value, str)
        or not 1 <= len(value.encode()) <= 48000
    ):
        raise DomainError("Invalid secret size or public key")
    raw = value.encode()
    result = ctypes.create_string_buffer(len(raw) + 48)
    if sodium().crypto_box_seal(result, raw, len(raw), public) != 0:
        raise DomainError("Secret encryption failed", 502)
    return base64.b64encode(result.raw).decode()


def headers(connection, resolve):
    secret = resolve(connection["credential_name"])
    if not secret:
        raise DomainError("Named credential unavailable", 503, "credential_unavailable")
    result = {"Accept": "application/json", "Content-Type": "application/json"}
    if connection["kind"] == "jira":
        result["Authorization"] = (
            "Basic "
            + base64.b64encode(
                (connection["username"] + ":" + secret).encode()
            ).decode()
        )
    elif connection["kind"] == "github":
        result.update(
            Authorization="Bearer " + secret,
            **{
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
    else:
        app = resolve(connection["aux_credential_name"])
        if not app:
            raise DomainError(
                "Named application credential unavailable",
                503,
                "credential_unavailable",
            )
        result.update({"DD-API-KEY": secret, "DD-APPLICATION-KEY": app})
    return result


async def request(connection, plan, auth):
    url = connection["endpoint"] + plan["path"]
    if plan.get("params"):
        url += "?" + urlencode(plan["params"])
    response = await fetch(
        url,
        policy=CONNECTOR.with_overrides(max_redirects=0, max_bytes=2 * 1024 * 1024),
        method=plan["method"],
        headers=auth,
        data=(
            json.dumps(plan["body"]).encode() if plan.get("body") is not None else None
        ),
    )
    if response.truncated:
        raise DomainError("Remote response exceeds size bound", 502, "response_limit")
    if not 200 <= response.status < 300:
        raise DomainError(
            "Remote API returned HTTP " + str(response.status),
            response.status if response.status in (401, 403, 404, 409, 429) else 502,
            "remote_error",
        )
    try:
        body = json.loads(response.body) if response.body else None
    except (ValueError, UnicodeError) as exc:
        raise DomainError(
            "Remote API did not return JSON", 502, "remote_error"
        ) from exc
    link = response.headers.get("Link", response.headers.get("link", ""))
    cursor = body.get("nextPageToken") if isinstance(body, dict) else None
    if isinstance(body, dict) and isinstance(body.get("meta"), dict):
        cursor = body["meta"].get("page", {}).get("after") or cursor
    partial = bool(
        'rel="next"' in link
        or cursor
        or isinstance(body, dict)
        and body.get("isLast") is False
    )
    return {
        "http_status": response.status,
        "body": body,
        "partial": partial,
        "next_cursor": cursor,
        "link": link,
        "retry_after": response.headers.get("Retry-After"),
    }


async def execute_remote(connection, plan, resolve):
    auth = headers(connection, resolve)
    operation = plan["operation"]
    outgoing = {**plan}
    if operation == "github_secret_sync":
        secret = resolve(plan["body"]["credential_ref"])
        if not secret:
            raise DomainError(
                "Assigned secret credential unavailable", 503, "credential_unavailable"
            )
        key_path = plan["path"].rsplit("/", 1)[0] + "/public-key"
        key = (await request(connection, {"method": "GET", "path": key_path}, auth))[
            "body"
        ]
        if not isinstance(key, dict) or not isinstance(key.get("key_id"), str):
            raise DomainError("Invalid GitHub encryption response", 502)
        outgoing["body"] = {
            "encrypted_value": seal(secret, key.get("key")),
            "key_id": key["key_id"],
        }
    result = await request(connection, outgoing, auth)
    if operation in (
        "jira_create",
        "jira_update",
        "jira_comment",
        "jira_transition",
        "github_archive",
    ):
        path = plan["path"]
        if operation == "jira_create":
            path = "/rest/api/3/issue/" + str((result["body"] or {}).get("key", ""))
        if operation in ("jira_comment", "jira_transition"):
            path = path.rsplit("/", 1)[0]
        result["verification"] = await request(
            connection, {"method": "GET", "path": path}, auth
        )
    return result
