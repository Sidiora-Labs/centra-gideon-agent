"""Durable lifetimes for customer-owned inbound integration credentials.

The registry stores SHA-256 digests and lifecycle metadata only. It never stores
or returns a bearer value. Surface credentials and registered-client credentials
share one 90-day policy, while client identity and bindings remain owned by
``clients.py``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from datetime import datetime

from gideon.security.auth.lifetimes import MAX_SESSION_TTL_SECS, parse_lifetime

logger = logging.getLogger(__name__)
INTEGRATION_TTL_SECS = MAX_SESSION_TTL_SECS
LIVE, EXPIRED, REVOKED, REPLACED = "live", "expired", "revoked", "replaced"
_FILE = "inbound_tokens.json"
_LOCK = threading.RLock()


class RegistryUnavailable(RuntimeError):
    """The durable token registry could not be read or safely updated."""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_ttl(value: str) -> int | None:
    return parse_lifetime(value, units="mhd")


def registry_path() -> Path:
    from gideon.core.config.loader import config_dir

    return Path(os.environ.get("GIDEON_HOME", config_dir())) / _FILE


def _read() -> dict[str, Any]:
    path = registry_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": 1, "tokens": {}}
    except OSError as exc:
        raise RegistryUnavailable(f"{path} cannot be read") from exc
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RegistryUnavailable(f"{path} is invalid") from exc
    rows = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        raise RegistryUnavailable(f"{path} has no token registry")
    return {"version": 1, "tokens": {str(k): v for k, v in rows.items() if isinstance(v, dict)}}


def _write(data: dict[str, Any]) -> None:
    from gideon.core.atomic_write import atomic_write

    path = registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, sort_keys=True, indent=2) + "\n", mode=0o600)
    except OSError as exc:
        raise RegistryUnavailable(f"{path} cannot be written") from exc


@contextmanager
def _transaction() -> Iterator[dict[str, Any]]:
    path = registry_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(path.parent / f".{_FILE}.lock", os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(lock_fd, 0o600)
            with os.fdopen(lock_fd, "a", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = _read()
                    before = json.dumps(data, sort_keys=True)
                    yield data
                    if json.dumps(data, sort_keys=True) != before:
                        _write(data)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            raise RegistryUnavailable(f"{path} cannot be locked") from exc


def _state(row: dict[str, Any], now: float) -> str:
    try:
        if float(row.get("revoked_at") or 0):
            return REVOKED
        if float(row.get("replaced_at") or 0):
            return REPLACED
        if float(row.get("expires_at") or 0) <= now:
            return EXPIRED
        return LIVE
    except (TypeError, ValueError, OverflowError):
        return EXPIRED


def _surface_row(digest: str, row: dict[str, Any], surface: str, now: float) -> dict[str, Any] | None:
    if row.get("kind") != "surface" or row.get("surface") != surface:
        return None
    return {
        "id": f"integration-surface-{surface}-{int(float(row.get('issued_at') or 0) * 1000000)}",
        "label": f"{surface.upper()} integration token",
        "name": f"{surface.upper()} integration token",
        "kind": "integration",
        "issuer": "integration",
        "current": False,
        "ip": "",
        "surface": surface,
        "issued_at": float(row.get("issued_at") or 0),
        "minted_at": float(row.get("issued_at") or 0),
        "expires_at": float(row.get("expires_at") or 0),
        "last_seen": float(row.get("last_seen") or 0),
        "state": _state(row, now),
    }


def _record_surface(data: dict[str, Any], surface: str, digest: str, now: float) -> dict[str, Any]:
    rows = data["tokens"]
    row = rows.get(digest)
    if isinstance(row, dict):
        # Revoked, expired, and replaced tokens never regain authority if restored.
        if row.get("kind") == "surface" and row.get("replaced_at"):
            return row
        if row.get("kind") == "surface" and row.get("surface") == surface:
            return row
    record = {
        "kind": "surface",
        "surface": surface,
        "issued_at": now,
        "expires_at": now + INTEGRATION_TTL_SECS,
        "revoked_at": 0.0,
        "replaced_at": 0.0,
        "last_seen": 0.0,
        "found": True,
    }
    rows[digest] = record
    for other_digest, other in list(rows.items()):
        if other_digest == digest or other.get("kind") != "surface" or other.get("surface") != surface:
            continue
        if _state(other, now) == LIVE:
            other["replaced_at"] = now
    return record


def issue_surface_token(surface: str, token: str, ttl: str = "90d", *, now: float | None = None) -> dict[str, Any]:
    seconds = parse_lifetime(ttl, units="dh")
    if seconds is None:
        raise ValueError("integration token lifetime must be between 1d and 90d")
    now = time.time() if now is None else float(now)
    digest = token_hash(token)
    with _transaction() as data:
        row = {
            "kind": "surface", "surface": surface, "issued_at": now,
            "expires_at": now + seconds, "revoked_at": 0.0,
            "replaced_at": 0.0, "last_seen": 0.0, "found": False,
        }
        prior = data["tokens"].get(digest)
        if isinstance(prior, dict) and _state(prior, now) != LIVE:
            raise ValueError("an ended integration token cannot be reissued")
        data["tokens"][digest] = row
        for old_digest, old in list(data["tokens"].items()):
            if old_digest != digest and old.get("kind") == "surface" and old.get("surface") == surface and _state(old, now) == LIVE:
                old["replaced_at"] = now
    _signin(f"surface-{surface}", "surface_token", "session_signed_in", {
        "surface": surface, "issued_at": now, "expires_at": now + seconds,
    })
    return {"surface": surface, "issued_at": now, "expires_at": now + seconds, "state": LIVE}


def surface_token(surface: str, token: str, *, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    digest = token_hash(token)
    with _transaction() as data:
        row = _record_surface(data, surface, digest, now)
        return {**row, "state": _state(row, now)}


def surface_usable(surface: str, token: str, *, now: float | None = None) -> bool:
    try:
        record = surface_token(surface, token, now=now)
        if record["state"] != LIVE:
            return False
        note_use(token, now=now)
        return True
    except RegistryUnavailable:
        logger.warning("inbound token registry is unavailable; refusing surface token", exc_info=True)
        return False


def revoke_surface_token(surface: str, token: str, *, now: float | None = None) -> bool:
    now = time.time() if now is None else float(now)
    digest = token_hash(token)
    with _transaction() as data:
        row = data["tokens"].get(digest)
        if not isinstance(row, dict) or row.get("kind") != "surface" or row.get("surface") != surface:
            row = _record_surface(data, surface, digest, now)
        if row.get("revoked_at"):
            return False
        row["revoked_at"] = now
        return True


def surface_rows(*, now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else float(now)
    try:
        data = _read()
    except RegistryUnavailable:
        raise
    rows = []
    for digest, value in data["tokens"].items():
        row = _surface_row(digest, value, str(value.get("surface") or ""), now)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: (row["surface"], row["issued_at"]))
    return rows


def revoke_surface_id(surface: str, issued_at: float, *, now: float | None = None) -> bool:
    now = time.time() if now is None else float(now)
    with _transaction() as data:
        for row in data["tokens"].values():
            if (
                row.get("kind") == "surface"
                and row.get("surface") == surface
                and int(float(row.get("issued_at") or 0) * 1000000) == int(issued_at)
            ):
                if row.get("revoked_at"):
                    return False
                row["revoked_at"] = now
                return True
    return False


def record_client(token_digest: str, client_id: str, label: str, surfaces: list[str], ttl: str, *, now: float) -> float:
    seconds = parse_ttl(ttl)
    if seconds is None:
        raise ValueError("integration token lifetime must be between 1d and 90d")
    expiry = now + seconds
    with _transaction() as data:
        data["tokens"][token_digest] = {
            "kind": "client", "client_id": client_id, "label": label,
            "surfaces": list(surfaces), "issued_at": now, "expires_at": expiry,
            "revoked_at": 0.0,
        }
    _signin(f"client-{client_id}", "client", "session_signed_in", {
        "client_id": client_id, "surfaces": list(surfaces), "expires_at": expiry,
    })
    return expiry


def client_state(token_digest: str, *, now: float | None = None) -> tuple[str, dict[str, Any] | None]:
    now = time.time() if now is None else float(now)
    try:
        row = _read()["tokens"].get(token_digest)
    except RegistryUnavailable:
        return "unavailable", None
    if not isinstance(row, dict) or row.get("kind") != "client":
        return "unknown", None
    return _state(row, now), row


def note_use(token: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    digest = token_hash(token)
    try:
        with _transaction() as data:
            row = data["tokens"].get(digest)
            if isinstance(row, dict) and now - float(row.get("last_seen") or 0) >= 300:
                row["last_seen"] = now
    except RegistryUnavailable:
        logger.debug("integration token last use could not be recorded", exc_info=True)


def validate_client(client: Any, *, now: float | None = None) -> tuple[str, dict[str, Any] | None]:
    """Resolve old client records once, preserving their original 90-day boundary."""
    now = time.time() if now is None else float(now)
    digest = str(getattr(client, "token_hash", "") or "")
    if not digest:
        return "unknown", None
    try:
        with _transaction() as data:
            row = data["tokens"].get(digest)
            if not isinstance(row, dict) or row.get("kind") != "client":
                try:
                    issued = float(getattr(client, "expires_at", 0.0) or 0.0)
                except (TypeError, ValueError):
                    issued = 0.0
                try:
                    issued_at = datetime.fromisoformat(str(client.created_at)).timestamp()
                except (TypeError, ValueError, AttributeError):
                    issued_at = 0.0
                expiry = issued if issued > 0 else (
                    issued_at + INTEGRATION_TTL_SECS if issued_at else 0.0
                )
                row = {
                    "kind": "client", "client_id": client.client_id,
                    "label": client.label, "surfaces": list(client.surfaces),
                "issued_at": issued_at, "expires_at": expiry, "revoked_at": 0.0,
                }
                data["tokens"][digest] = row
            if row.get("client_id") != client.client_id:
                return "unknown", None
            if _state(row, now) == LIVE and now - float(row.get("last_seen") or 0) >= 300:
                row["last_seen"] = now
            return _state(row, now), dict(row)
    except RegistryUnavailable:
        return "unavailable", None


def client_rows(clients: Any, *, now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else float(now)
    rows = []
    for client in clients.values():
        state, record = validate_client(client, now=now)
        if record is None:
            continue
        rows.append({
            "id": f"integration-client-{client.client_id}",
            "label": client.label,
            "name": client.label,
            "kind": "integration",
            "issuer": "integration",
            "current": False,
            "ip": "",
            "minted_at": float(record.get("issued_at") or 0),
            "surface": ", ".join(client.surfaces),
            "issued_at": float(record.get("issued_at") or 0),
            "expires_at": float(record.get("expires_at") or 0),
            "last_seen": 0.0,
            "state": state,
        })
    return rows


def _signin(session_id: str, kind: str, operation: str, extra: dict[str, Any]) -> None:
    try:
        from gideon.security.auth.signins import record

        record(
            operation,
            caller="owner",
            session_id=session_id,
            issuer="inbound",
            kind=kind,
            source="inbound",
            extra=extra,
        )
    except Exception:  # noqa: BLE001
        logger.debug("integration credential audit unavailable", exc_info=True)


def revoke_client_token(token_digest: str, *, now: float | None = None) -> bool:
    now = time.time() if now is None else float(now)
    with _transaction() as data:
        row = data["tokens"].get(token_digest)
        if not isinstance(row, dict) or row.get("kind") != "client":
            return False
        if row.get("revoked_at"):
            return False
        row["revoked_at"] = now
        detail = {"client_id": str(row.get("client_id") or ""), "reason": REVOKED}
        session = f"client-{row.get('client_id') or 'unknown'}"
    _signin(session, "client", "session_signed_out", detail)
    return True


def client_sentence(row: dict[str, Any], state: str) -> str:
    label = str(row.get("label") or "integration")
    return f"The token for {label} is {state}; register a new integration token."


def refusal(surface: str, token: str) -> str | None:
    if not token:
        return None
    digest = token_hash(token)
    try:
        data = _read()
    except RegistryUnavailable:
        return "integration token registry unavailable"
    row = data["tokens"].get(digest)
    if not isinstance(row, dict):
        return None
    state = _state(row, time.time())
    if row.get("kind") == "surface" and row.get("surface") == surface and state != LIVE:
        return f"{surface} integration token {state}; create a new token with `gideon inbound token create {surface} --rotate`"
    if row.get("kind") == "client" and surface in row.get("surfaces", []) and state != LIVE:
        return client_sentence(row, state)
    return None
