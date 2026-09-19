"""Durable signing key + session records (REMOTE-USER-AUTH S1).

Before this, `token_auth._SECRET = os.urandom(32)` ran at module scope and the valid-nonce
set lived in memory. Both consequences were the same: **every gateway restart invalidated
every token.** On a local box that means re-running `gideon token` after each restart;
off-network it means you cannot get back in at all, because minting a fresh URL requires
being on the machine. That is the concrete pain this fixes.

Two pieces of state, deliberately separate files:

* **the signing key** (`session_key`) — 32 random bytes, 0600. Rotating it invalidates
  everything at once, which is exactly what you want from a panic button and exactly what
  you do NOT want to happen accidentally on reboot.
* **the session records** (`sessions.json`) — one entry per minted nonce with its expiry,
  so a token minted before a restart still verifies afterwards.

**Row shape (COMPANION-APPS C1).** A row is a RECORD, not a bare expiry:
``{"exp": float, "issuer": str, "device": {...}}``. The extra two fields are what make a
device registry possible without a second credential type — a paired phone holds an ordinary
session, and the only thing that distinguishes it from the owner's browser is provenance
written down at mint time. Without ``issuer`` the registry cannot tell a phone from a laptop,
and "revoke this device" degrades into "log everyone out".

:func:`load_sessions` remains the ``{nonce: exp}`` PROJECTION of that one shape — it is the
only thing the token middleware needs, and narrowing there keeps the hot path from carrying a
registry it never reads. One stored shape, two typed views; not two paths.

**An old-shape file (a bare float per row) is DISCARDED, not upgraded.** Deliberate, and the
reason is not laziness about a three-line branch: a row with no ``issuer`` is a live session
the registry can neither describe nor revoke, which is precisely the audit gap this record
exists to close. Admitting one would mean shipping a device list that is silently incomplete.
The cost of discarding is bounded and already documented by the pre-1.0 banner — one
``gideon token`` re-mint, which is exactly the pre-S1 behavior this store replaced.

**Why a file and not the credential store:** the key must be readable during middleware
setup, before any provider or keychain prompt can run, and on a headless box there may be no
keychain at all. A 0600 file in the config dir is the same trust level as
`.local_secret`, which already guards the same surface.

**Failure posture is FAIL-CLOSED, unlike most of this codebase.** If the key cannot be read
or written, `load_or_create_key()` raises rather than falling back to an ephemeral key.
A silent fallback would look identical to working — until the next restart logged everyone
out again, which is the bug being fixed. An auth surface that cannot persist its trust root
should refuse to pretend otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write, atomic_write_bytes
from gideon.core.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

KEY_FILE = "session_key"
SESSIONS_FILE = "sessions.json"

KEY_BYTES = 32

MAX_SESSIONS = 2000

ISSUER_UNKNOWN = "unknown"
ISSUER_PAIR = "pair"

DEVICE_KINDS: tuple[str, ...] = ("browser", "mobile", "desktop", "cli", "unknown")

MAX_DEVICE_NAME = 64

LAST_SEEN_THROTTLE_SECS = 60.0


def key_path() -> Path:
    return config_dir() / KEY_FILE


def sessions_path() -> Path:
    return config_dir() / SESSIONS_FILE


def load_or_create_key() -> bytes:
    """The persistent signing key, creating it on first use.

    Raises ``OSError`` when the key can neither be read nor written — see the module note on
    fail-closed. A caller that genuinely wants ephemeral behavior (tests, `--test-mode`) asks
    for it explicitly rather than getting it from a swallowed error.
    """
    path = key_path()
    try:
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) >= KEY_BYTES:
                _ensure_owner_only(path)
                return raw
            logger.warning(
                "session key at %s is too short (%d bytes) — regenerating",
                path,
                len(raw),
            )
    except OSError:
        logger.warning("session key unreadable at %s", path, exc_info=True)

    key = os.urandom(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, key, mode=0o600)
    logger.info("created a persistent session signing key at %s", path)
    return key


def rotate_key() -> bytes:
    """Replace the signing key, invalidating every existing token. Returns the new key."""
    key = os.urandom(KEY_BYTES)
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, key, mode=0o600)
    clear_sessions()
    logger.info("rotated the session signing key; all existing tokens are now invalid")
    return key


def _ensure_owner_only(path: Path) -> None:
    """Tighten the key file to 0600 if something loosened it.

    Not merely cosmetic: a key readable by another local account is a key that account can
    use to mint a dashboard session for itself.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
            logger.warning("session key had mode %o — tightened to 0600", mode)
    except OSError:
        logger.debug("could not verify session key permissions", exc_info=True)


@dataclass
class DeviceInfo:
    """The paired device behind a session row.

    ``id`` is the registry handle the revoke route takes; it is NOT the nonce, because the
    nonce is the credential and a revoke URL must not carry one.

    ``last_seen`` is written by :func:`touch_device_last_seen` from the one honest place —
    where a device's request is AUTHORIZED (`TokenStateManager.is_nonce_valid`) — and nowhere
    else. It was held back from C1 because that means a throttled write on the request path,
    which is a per-request cost rather than a field to declare; the surface that needs it (the
    Settings → Devices list) is what justified paying it.

    **0.0 means "never made an authorized request", and that is load-bearing.** It is NOT
    backfilled from ``minted_at``: a ``last_seen`` set at pairing time would read as fresh
    forever, which is worse than an absent value, because the owner would use it to decide a
    device is still in use. A device that paired and never came back must render as "never".
    """

    id: str
    name: str = ""
    kind: str = "unknown"
    minted_at: float = 0.0
    last_seen: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "minted_at": self.minted_at,
            "last_seen": self.last_seen,
        }


@dataclass
class SessionRecord:
    """One ``sessions.json`` row."""

    expiry: float
    issuer: str = ISSUER_UNKNOWN
    device: DeviceInfo | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"exp": self.expiry, "issuer": self.issuer}
        if self.device is not None:
            out["device"] = self.device.to_dict()
        return out


def sanitize_device_name(name: str) -> str:
    """A display name safe to store and render: printable, single-line, bounded."""
    cleaned = "".join(
        ch for ch in str(name or "") if ch.isprintable() and ch not in "\r\n\t"
    )
    return cleaned.strip()[:MAX_DEVICE_NAME]


def sanitize_device_kind(kind: str) -> str:
    """Clamp to :data:`DEVICE_KINDS`. An unrecognized kind becomes ``unknown``, never itself."""
    candidate = str(kind or "").strip().lower()
    return candidate if candidate in DEVICE_KINDS else "unknown"


def _parse_device(raw: Any) -> DeviceInfo | None:
    if not isinstance(raw, dict):
        return None
    device_id = str(raw.get("id") or "")
    if not device_id:
        return None
    try:
        minted_at = float(raw.get("minted_at") or 0.0)
    except (TypeError, ValueError):
        minted_at = 0.0
    try:
        last_seen = float(raw.get("last_seen") or 0.0)
    except (TypeError, ValueError):
        last_seen = 0.0
    return DeviceInfo(
        id=device_id,
        name=sanitize_device_name(raw.get("name", "")),
        kind=sanitize_device_kind(raw.get("kind", "")),
        minted_at=minted_at,
        last_seen=last_seen,
    )


def _parse_record(raw: Any) -> SessionRecord | None:
    """One stored row → a record, or *None* when the row is not one.

    **The single place the old bare-float shape is handled**, and it is handled by refusing
    it: see the module docstring. A non-dict row returns *None*, so a pre-C1 store reads as
    empty and every token in it is re-minted rather than admitted un-attributed.
    """
    if not isinstance(raw, dict):
        return None
    try:
        expiry = float(raw.get("exp"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    issuer = str(raw.get("issuer") or ISSUER_UNKNOWN)
    return SessionRecord(
        expiry=expiry, issuer=issuer, device=_parse_device(raw.get("device"))
    )


def load_session_records() -> dict[str, SessionRecord]:
    """``{nonce: SessionRecord}`` for every stored session, expired ones dropped.

    Returns ``{}`` on any read failure. Fail-CLOSED in effect: an unreadable store means no
    nonce validates, so tokens are rejected rather than blanket-accepted.
    """
    path = sessions_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("session store unreadable — treating every session as absent")
        return {}
    if not isinstance(raw, dict):
        return {}
    now = time.time()
    out: dict[str, SessionRecord] = {}
    dropped = 0
    for nonce, row in (raw.get("sessions") or {}).items():
        record = _parse_record(row)
        if record is None:
            dropped += 1
            continue
        if record.expiry > now:
            out[str(nonce)] = record
    if dropped:
        logger.info(
            "dropped %d session row(s) that predate the device-session record shape — "
            "re-run `gideon token` (or log in) to get a fresh session",
            dropped,
        )
    return out


def load_sessions() -> dict[str, float]:
    """``{nonce: session_exp}`` — the expiry projection of :func:`load_session_records`.

    The token middleware asks exactly one question of this store ("is this nonce live, and
    until when?"), so it gets exactly that. Same file, same rows, narrower view.
    """
    return {nonce: record.expiry for nonce, record in load_session_records().items()}


def save_session_records(records: dict[str, SessionRecord]) -> None:
    """Persist *records*, dropping expired entries and capping the total."""
    now = time.time()
    live = {n: r for n, r in records.items() if r.expiry > now}
    if len(live) > MAX_SESSIONS:
        live = dict(
            sorted(live.items(), key=lambda kv: kv[1].expiry, reverse=True)[
                :MAX_SESSIONS
            ]
        )
    payload = {"sessions": {n: r.to_dict() for n, r in live.items()}}
    path = sessions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload, indent=2) + "\n", mode=0o600)
    except OSError:
        logger.warning("could not persist the session store", exc_info=True)


def remember_session(
    nonce: str,
    expiry: float,
    *,
    issuer: str = ISSUER_UNKNOWN,
    device: DeviceInfo | None = None,
) -> None:
    """Record one minted session so it survives a restart."""
    if not nonce:
        return
    records = load_session_records()
    records[nonce] = SessionRecord(expiry=float(expiry), issuer=issuer, device=device)
    save_session_records(records)


def attach_device(nonce: str, device: DeviceInfo, *, issuer: str = ISSUER_PAIR) -> bool:
    """Mark an already-minted session as belonging to *device*. Returns whether it landed.

    Two steps rather than one because the mint itself belongs to ``token_auth`` and stays
    device-unaware: pairing does not introduce a token type, it annotates an ordinary one.
    A nonce that is absent (expired between mint and annotate) is NOT recreated here — that
    would resurrect a session the store had already retired.
    """
    if not nonce:
        return False
    records = load_session_records()
    existing = records.get(nonce)
    if existing is None:
        logger.warning("no live session row to attach a device to")
        return False
    records[nonce] = SessionRecord(expiry=existing.expiry, issuer=issuer, device=device)
    save_session_records(records)
    return True


def touch_device_last_seen(nonce: str, *, now: float | None = None) -> bool:
    """Best-effort: stamp ``last_seen`` on *nonce*'s device row. Returns whether it WROTE.

    Three no-ops, each deliberate:

    * **no row** — a nonce the store never recorded is not resurrected here, for the same
      reason :func:`attach_device` refuses to.
    * **no device** — a plain owner-token session has no ``DeviceInfo`` and nothing to be
      "last seen"; the field belongs to the device registry, not to every session.
    * **still fresh** — while the recorded stamp is newer than
      :data:`LAST_SEEN_THROTTLE_SECS`, this returns without touching the file. That throttle
      is the whole reason the field was safe to add: see the constant.

    **Never raises.** This is called from inside the authorization path, so every failure mode
    — an unreadable store, an unwritable directory, a corrupt row — must degrade to "no stamp
    written", never to "session denied". A device list with a stale timestamp is a cosmetic
    defect; an auth path that fails closed on a cosmetic write is an outage.
    """
    if not nonce:
        return False
    try:
        stamp = time.time() if now is None else float(now)
        records = load_session_records()
        record = records.get(nonce)
        if record is None or record.device is None:
            return False
        if stamp - record.device.last_seen < LAST_SEEN_THROTTLE_SECS:
            return False
        record.device.last_seen = stamp
        save_session_records(records)
        return True
    except Exception:  # noqa: BLE001 — best-effort by contract; see the docstring
        logger.debug("could not stamp device last_seen", exc_info=True)
        return False


def device_sessions() -> dict[str, SessionRecord]:
    """Only the rows that carry a device — the registry, keyed by nonce."""
    return {n: r for n, r in load_session_records().items() if r.device is not None}


def paired_session_record(nonce: str) -> SessionRecord | None:
    """Return the live paired-device row for *nonce*, or ``None``.

    A device-shaped row is not sufficient: callers using this as request identity require the
    pairing provenance too. Unknown, expired, and non-pair rows therefore all fail closed.
    """
    if not nonce:
        return None
    record = load_session_records().get(nonce)
    if record is None or record.issuer != ISSUER_PAIR or record.device is None:
        return None
    return record


def nonces_for_device(device_id: str) -> list[str]:
    """Every live nonce belonging to *device_id*.

    A list, not one nonce: re-pairing the same device before the old session expires is
    legitimate, and a revoke that only dropped the newest would leave the device logged in.
    """
    if not device_id:
        return []
    return [
        n for n, r in device_sessions().items() if r.device and r.device.id == device_id
    ]


def forget_session(nonce: str) -> None:
    """Drop one session (logout / eviction)."""
    records = load_session_records()
    if records.pop(nonce, None) is not None:
        save_session_records(records)


def clear_sessions() -> None:
    """Drop every stored session."""
    save_session_records({})


def session_stats() -> dict[str, Any]:
    """Counts for the doctor / status surface — never the nonces themselves."""
    records = load_session_records()
    return {
        "sessions": len(records),
        "devices": sum(1 for r in records.values() if r.device is not None),
        "key_present": key_path().is_file(),
        "soonest_expiry": min((r.expiry for r in records.values()), default=0.0),
    }
