"""Provider-scoped sender trust, single-use pairing and inbound policy decisions."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import logging
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)
_ENTITY = "channel_trust"
_OWNER_PAIRING_ENTITY = "channel_owner_pairing"
PAIRING_CODE_TTL_SECS = 600
PAIRING_CODE_DIGITS = 8
DM_POLICIES: tuple[str, ...] = ("pairing", "owner_only", "open")
GROUP_POLICIES: tuple[str, ...] = ("tracked_only", "off")
DEFAULT_DM_POLICY = "pairing"
DEFAULT_GROUP_POLICY = "tracked_only"
UNKNOWN_SENDER_RENOTIFY_SECS = 24 * 3600
SEEN_SENDERS_MAX = 20
OWNER_PAIRING_MAX_ATTEMPTS = 5
_OWNER_PAIRING_EPOCH = secrets.token_hex(16)
_OWNER_PAIRING_SECRET = secrets.token_bytes(32)
CANNED_PAIRING_REPLY = (
    "I don't recognize you yet. Ask my owner for an 8-digit pairing code "
    "(they can run `gideon pair <provider>`), then send it here to start talking."
)
CANNED_PAIRED_REPLY = "Paired — you can talk to me now."
CANNED_OWNER_PAIRED_REPLY = "This channel is now paired as the owner. Ask for the dashboard sign-in link when you need it."
_STORE_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _default_provider() -> dict[str, Any]:
    record: dict[str, Any] = {
        key: {}
        for key in (
            "allowed_senders",
            "tracked_channels",
            "seen_channels",
            "seen_senders",
            "pairing",
            "policies",
            "rate",
        )
    }
    record["policies"].update(dm=DEFAULT_DM_POLICY, group=DEFAULT_GROUP_POLICY)
    return record


_DEFAULT_PROVIDER = _default_provider()


def _read_store() -> dict[str, Any]:
    from gideon.extensions.providers.entity_routes import _entity_settings_path

    with _STORE_LOCK:
        path = _entity_settings_path(_ENTITY)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "channel_trust store at %s is unreadable/corrupt — using defaults",
                path,
                exc_info=True,
            )
            return {}
        return value if isinstance(value, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    from gideon.extensions.providers.entity_routes import _save_entity_settings

    with _STORE_LOCK:
        _save_entity_settings(_ENTITY, data)


def _owner_pairing_record(provider: str) -> dict[str, Any]:
    from gideon.extensions.providers.entity_routes import _load_entity_settings

    with _STORE_LOCK:
        store = _load_entity_settings(_OWNER_PAIRING_ENTITY)
        if not isinstance(store, dict):
            return {}
        record = store.get(provider)
        return copy.deepcopy(record) if isinstance(record, dict) else {}


def _save_owner_pairing_record(provider: str, record: dict[str, Any]) -> None:
    from gideon.extensions.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    with _STORE_LOCK:
        store = _load_entity_settings(_OWNER_PAIRING_ENTITY)
        if store is None:
            raise OSError("owner pairing state is unreadable")
        store[provider] = copy.deepcopy(record)
        _save_entity_settings(_OWNER_PAIRING_ENTITY, store)


def _provider_record(store: dict[str, Any], provider: str) -> dict[str, Any]:
    defaults = _default_provider()
    stored = store.get(provider)
    if not isinstance(stored, dict):
        return defaults
    result = copy.deepcopy(stored)
    for section, fallback in defaults.items():
        value = result.get(section)
        result[section] = fallback | value if isinstance(value, dict) else fallback
    return result


@dataclass
class _ProviderChange:
    provider: str
    store: dict[str, Any]
    record: dict[str, Any] = field(init=False)
    dirty: bool = False

    def __post_init__(self):
        self.record = _provider_record(self.store, self.provider)

    def commit(self) -> None:
        self.dirty = True

    def flush(self) -> None:
        if self.dirty:
            self.store[self.provider] = self.record
            _write_store(self.store)


@contextlib.contextmanager
def _editing(provider: str):
    with _STORE_LOCK:
        change = _ProviderChange(provider, _read_store())
        yield change
        change.flush()


def _save_provider(provider: str, record: dict[str, Any]) -> None:
    with _editing(provider) as change:
        change.record = record
        change.commit()


def _emit_sel(operation: str, outcome: str, provider: str, sender_id: str = "") -> None:
    try:
        import uuid

        from gideon.security.sel import SecurityEvent, sel

        identity = ":".join((provider, sender_id)) if sender_id else provider
        event: dict = {
            "event_id": uuid.uuid4().hex[:16],
            "timestamp": _iso(_now()),
            "event_type": "channel_trust",
            "caller_identity": identity,
            "agent": "gideon",
            "source": "channel",
            "operation": operation,
            "outcome": outcome,
            "resources": f"provider={provider}",
        }
        sel().log(SecurityEvent(**event))
    except Exception:
        logger.debug("channel_trust SEL emit failed for %s", operation, exc_info=True)


def _lookup(provider: str, collection: str) -> dict[str, Any]:
    return _provider_record(_read_store(), provider)[collection]


def _entry(name: str, **extra: str) -> dict[str, str]:
    return {"name": name, "added_at": _iso(_now()), **extra}


def _update_directory(
    provider: str, collection: str, key: str, value: dict | None
) -> None:
    with _editing(provider) as change:
        directory = change.record[collection]
        if value is None:
            directory.pop(key, None)
        else:
            directory[key] = value
        change.commit()


def is_allowed_sender(provider: str, sender_id: str) -> bool:
    return sender_id in _lookup(provider, "allowed_senders")


def allow_sender(
    provider: str, sender_id: str, name: str = "", *, via: str = "owner"
) -> None:
    with _editing(provider) as change:
        change.record["allowed_senders"][sender_id] = _entry(name, via=via)
        change.record["seen_senders"].pop(sender_id, None)
        change.commit()
    _emit_sel("sender_paired", via, provider, sender_id)


def deny_sender(provider: str, sender_id: str) -> None:
    with _editing(provider) as change:
        if sender_id in change.record["allowed_senders"]:
            del change.record["allowed_senders"][sender_id]
            if sender_id in change.record["rate"]:
                change.record["rate"][sender_id] = ""
            change.record["seen_senders"].pop(sender_id, None)
            change.commit()
    _emit_sel("sender_denied", "owner", provider, sender_id)


def is_tracked_channel(provider: str, channel_id: str) -> bool:
    return channel_id in _lookup(provider, "tracked_channels")


def track(provider: str, channel_id: str, name: str = "") -> None:
    _update_directory(provider, "tracked_channels", channel_id, _entry(name))
    _emit_sel("channel_tracked", "owner", provider, channel_id)


def untrack(provider: str, channel_id: str) -> None:
    _update_directory(provider, "tracked_channels", channel_id, None)
    _emit_sel("channel_untracked", "owner", provider, channel_id)


def trust_policies(provider: str) -> dict[str, str]:
    return dict(_lookup(provider, "policies"))


def set_trust_policies(
    provider: str,
    *,
    dm: str | None = None,
    group: str | None = None,
    confirm_open: bool = False,
) -> dict[str, str]:
    """Persist provider-scoped trust choices; opening DMs requires explicit consent."""
    if dm is not None and dm not in DM_POLICIES:
        raise ValueError("invalid_dm_policy")
    if group is not None and group not in GROUP_POLICIES:
        raise ValueError("invalid_group_policy")
    if dm == "open" and not confirm_open:
        raise PermissionError("open_dm_requires_confirmation")
    with _editing(provider) as change:
        if dm is not None:
            change.record["policies"]["dm"] = dm
            if dm != "pairing" and change.record["pairing"]:
                change.record["pairing"] = {}
                _emit_sel("pairing_code_cancelled", "policy_changed", provider)
        if group is not None:
            change.record["policies"]["group"] = group
        change.commit()
        result = dict(change.record["policies"])
    _emit_sel("trust_policy_changed", "updated", provider)
    return result


def note_seen_channel(provider: str, channel_id: str, name: str = "") -> None:
    """Remember a bounded, non-secret projection of groups observed on this provider."""
    if not channel_id:
        return
    with _editing(provider) as change:
        seen = change.record["seen_channels"]
        seen[channel_id] = _entry(name)
        while len(seen) > 1000:
            del seen[next(iter(seen))]
        change.commit()


def list_seen_channels(provider: str) -> list[dict[str, str]]:
    return _directory_projection(
        _lookup(provider, "seen_channels"), "channel_id", ("name", "added_at")
    )


def cancel_pairing_code(provider: str) -> bool:
    with _editing(provider) as change:
        active = bool(change.record["pairing"].get("code_hash"))
        if active:
            change.record["pairing"] = {}
            change.commit()
    if active:
        _emit_sel("pairing_code_cancelled", "cancelled", provider)
    return active


def list_providers() -> list[str]:
    return sorted(_read_store())


def _directory_projection(
    directory: dict, identifier: str, columns: tuple[str, ...]
) -> list[dict]:
    projected = []
    for key in sorted(directory):
        values = directory[key]
        metadata = values if isinstance(values, dict) else {}
        projected.append(
            {
                identifier: key,
                **{column: str(metadata.get(column, "") or "") for column in columns},
            }
        )
    return projected


def provider_trust(provider: str) -> dict[str, Any]:
    record = _provider_record(_read_store(), provider)
    code = record["pairing"]
    from gideon.core.config.credentials import owner_id_for

    owner_id = owner_id_for(provider)
    senders = record["allowed_senders"]
    if owner_id:
        senders = {key: value for key, value in senders.items() if key != owner_id}
    return {
        "provider": provider,
        "policies": dict(record["policies"]),
        "allowed_senders": _directory_projection(
            senders, "sender_id", ("name", "added_at", "via")
        ),
        "tracked_channels": _directory_projection(
            record["tracked_channels"], "channel_id", ("name", "added_at")
        ),
        "seen_senders": [
            {
                "sender_id": key,
                "name": str(value.get("name") or "").strip(),
                "since": str(value.get("since") or ""),
                "last_seen": str(value.get("last_seen") or ""),
                "count": _messages_counted(value),
            }
            for key, value in sorted(
                record["seen_senders"].items(),
                key=lambda entry: (
                    str(entry[1].get("last_seen", ""))
                    if isinstance(entry[1], dict)
                    else ""
                ),
                reverse=True,
            )
            if isinstance(value, dict)
            and key not in record["allowed_senders"]
            and key != owner_id
        ],
        "pairing_active": _PairingTicket.from_record(code).verdict("", _now())
        == "wrong_code",
        "pairing_expires_at": str(code.get("expires_at", "") or ""),
    }


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _PairingTicket:
    digest: str
    deadline: Any

    @classmethod
    def from_record(cls, record: dict):
        return cls(record.get("code_hash", ""), record.get("expires_at", ""))

    def verdict(self, candidate: str, now: datetime) -> str:
        if not self.digest:
            return "no_active_code"
        try:
            if now > datetime.fromisoformat(self.deadline):
                return "expired_code"
        except (ValueError, TypeError):
            return "expired_code"
        matches = hmac.compare_digest(self.digest, _hash_code(candidate or ""))
        return "paired" if matches else "wrong_code"


def create_pairing_code(provider: str) -> str:
    number = secrets.randbelow(10**PAIRING_CODE_DIGITS)
    code = str(number).zfill(PAIRING_CODE_DIGITS)
    issued = _now()
    with _editing(provider) as change:
        change.record["pairing"] = {
            "code_hash": _hash_code(code),
            "created_at": _iso(issued),
            "expires_at": _iso(issued + timedelta(seconds=PAIRING_CODE_TTL_SECS)),
        }
        change.commit()
    _emit_sel("pairing_code_created", "created", provider)
    return code


def _pairing_code_outstanding(provider: str) -> bool:
    return bool(_lookup(provider, "pairing").get("code_hash"))


def _looks_like_a_pairing_code(text: str) -> bool:
    return len(text) == PAIRING_CODE_DIGITS and all(
        "0" <= character <= "9" for character in text
    )


def redeem_pairing_code(
    provider: str, sender_id: str, code: str, name: str = ""
) -> bool:
    with _editing(provider) as change:
        ticket = _PairingTicket.from_record(change.record["pairing"])
        outcome = (
            ticket.verdict(code, _now())
            if change.record["policies"].get("dm") == "pairing"
            else "pairing_policy_required"
        )
        if outcome in ("paired", "expired_code"):
            change.record["pairing"] = {}
            if outcome == "paired":
                change.record["allowed_senders"][sender_id] = _entry(
                    name, via="pairing"
                )
                change.record["seen_senders"].pop(sender_id, None)
            change.commit()
    accepted = outcome == "paired"
    _emit_sel(
        "sender_paired" if accepted else "sender_denied",
        "pairing" if accepted else outcome,
        provider,
        sender_id,
    )
    return accepted


def owner_pairing_code_outstanding(provider: str) -> bool:
    ticket = _owner_pairing_record(provider)
    return bool(ticket.get("code_hash")) and ticket.get("epoch") == _OWNER_PAIRING_EPOCH


def create_owner_pairing_code(provider: str) -> str:
    """Create a short-lived, single-use code that binds this channel's owner."""
    code = str(secrets.randbelow(10**PAIRING_CODE_DIGITS)).zfill(PAIRING_CODE_DIGITS)
    issued = _now()
    _save_owner_pairing_record(
        provider,
        {
            "code_hash": hmac.new(
                _OWNER_PAIRING_SECRET, code.encode(), hashlib.sha256
            ).hexdigest(),
            "epoch": _OWNER_PAIRING_EPOCH,
            "created_at": _iso(issued),
            "expires_at": _iso(issued + timedelta(seconds=PAIRING_CODE_TTL_SECS)),
            "attempts": 0,
            "ended": "",
        },
    )
    _emit_sel("owner_pairing_code_created", "created", provider)
    return code


def owner_pairing_status(provider: str) -> dict[str, Any]:
    ticket = _owner_pairing_record(provider)
    active = (
        bool(ticket.get("code_hash")) and ticket.get("epoch") == _OWNER_PAIRING_EPOCH
    )
    ended = str(ticket.get("ended") or "")
    if ticket.get("code_hash") and not active:
        ended = "expired"
    if active:
        try:
            active = _now() <= datetime.fromisoformat(
                str(ticket.get("expires_at") or "")
            )
        except (TypeError, ValueError):
            active = False
        if not active:
            ended = "expired"
    attempts = int(ticket.get("attempts") or 0)
    return {
        "active": active,
        "created_at": str(ticket.get("created_at") or ""),
        "expires_at": str(ticket.get("expires_at") or ""),
        "attempts_left": max(0, OWNER_PAIRING_MAX_ATTEMPTS - attempts) if active else 0,
        "ended": ended,
    }


def cancel_owner_pairing(provider: str) -> bool:
    ticket = _owner_pairing_record(provider)
    if not ticket.get("code_hash"):
        return False
    _save_owner_pairing_record(provider, {"ended": "cancelled"})
    _emit_sel("owner_pairing_cancelled", "cancelled", provider)
    return True


def redeem_owner_pairing_code(
    provider: str, sender_id: str, code: str, name: str = ""
) -> bool:
    """Bind an owner only from a transport-declared direct-message path."""
    from gideon.core.config.credentials import owner_id_credential, save_credential
    from gideon.integrations.channel_transports import get_transport

    transport = get_transport(provider)
    capability = (
        getattr(transport, "capabilities", lambda: None)() if transport else None
    )
    if (
        not sender_id
        or transport is None
        or not bool(getattr(capability, "inbound", False))
        or not bool(getattr(capability, "owner_pairing", False))
        or not _looks_like_a_pairing_code(code)
    ):
        return False
    accepted = False
    with _STORE_LOCK:
        ticket = _owner_pairing_record(provider)
        digest = str(ticket.get("code_hash") or "")
        try:
            unexpired = _now() <= datetime.fromisoformat(
                str(ticket.get("expires_at") or "")
            )
        except (ValueError, TypeError):
            unexpired = False
        valid_epoch = ticket.get("epoch") == _OWNER_PAIRING_EPOCH
        if not digest or not unexpired or not valid_epoch:
            if digest:
                _save_owner_pairing_record(provider, {"ended": "expired"})
        else:
            expected = hmac.new(
                _OWNER_PAIRING_SECRET, code.encode(), hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(digest, expected):
                save_credential(owner_id_credential(provider), sender_id)
                _update_directory(
                    provider,
                    "allowed_senders",
                    sender_id,
                    _entry(name, via="owner_pairing"),
                )
                _save_owner_pairing_record(
                    provider, {"ended": "paired", "paired_at": _iso(_now())}
                )
                accepted = True
            else:
                attempts = int(ticket.get("attempts") or 0) + 1
                if attempts >= OWNER_PAIRING_MAX_ATTEMPTS:
                    ticket = {"ended": "too_many_attempts", "attempts": attempts}
                else:
                    ticket["attempts"] = attempts
                _save_owner_pairing_record(provider, ticket)
    _emit_sel(
        "owner_paired" if accepted else "owner_pairing_attempt",
        "paired" if accepted else "refused",
        provider,
    )
    return accepted


def fence_channel_content(text: str, provider: str, sender_id: str) -> str:
    from gideon.security.security import fence_untrusted

    origin = ":".join(("channel", provider, sender_id))
    return fence_untrusted(text, source=origin)


@dataclass
class TrustVerdict:
    allowed: bool
    reason: str = ""
    canned_reply: str = ""
    fired_notification: bool = False
    fenced_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


_REPORT_WINDOW_MAX = 512
_REPORTED: OrderedDict[str, datetime] = OrderedDict()
_REPORT_LOCK = threading.Lock()


def reset_inbound_reports() -> None:
    with _REPORT_LOCK:
        _REPORTED.clear()


def _visible_line_is_deduped(key: str) -> bool:
    with _REPORT_LOCK:
        current = _now()
        previous = _REPORTED.get(key)
        if (
            previous is not None
            and (current - previous).total_seconds() < UNKNOWN_SENDER_RENOTIFY_SECS
        ):
            return True
        _REPORTED.pop(key, None)
        _REPORTED[key] = current
        while len(_REPORTED) > _REPORT_WINDOW_MAX:
            del _REPORTED[next(iter(_REPORTED))]
        return False


@dataclass(frozen=True)
class _InboundContext:
    provider: str
    sender_id: str
    channel_id: str
    is_dm: bool

    @property
    def scope(self) -> str:
        return "dm" if self.is_dm else "group"

    @property
    def subject(self) -> str:
        return self.sender_id if self.is_dm else self.channel_id

    def policy(self) -> str:
        default = DEFAULT_DM_POLICY if self.is_dm else DEFAULT_GROUP_POLICY
        return trust_policies(self.provider).get(self.scope, default)

    def known_verdict(self, policy: str, text: str) -> TrustVerdict | None:
        from gideon.core.config.credentials import owner_id_for

        decision = None
        if self.is_dm:
            if (
                policy == "open"
                or is_allowed_sender(self.provider, self.sender_id)
                or self.sender_id == owner_id_for(self.provider)
            ):
                decision = TrustVerdict(True, "allowed")
        elif policy == "off":
            decision = TrustVerdict(False, "group_policy_off")
        elif not is_tracked_channel(self.provider, self.channel_id):
            decision = TrustVerdict(False, "untracked_channel")
        else:
            trusted = not text or is_allowed_sender(self.provider, self.sender_id)
            fenced = (
                ""
                if trusted
                else fence_channel_content(text, self.provider, self.sender_id)
            )
            decision = TrustVerdict(True, "tracked_channel", fenced_text=fenced)
        return decision

    def remedy(self, policy: str, allowed: bool) -> str:
        if not policy or allowed:
            return ""
        action = (
            "pair or allow this sender"
            if self.is_dm
            else "track this channel or change the policy"
        )
        return " — " + action


def report_inbound_verdict(
    provider: str,
    verdict: TrustVerdict,
    *,
    sender_id: str = "",
    channel_id: str = "",
    is_dm: bool = True,
    policy: str = "",
) -> TrustVerdict:
    context = _InboundContext(provider, sender_id, channel_id, is_dm)
    level = logging.DEBUG
    if not verdict.allowed:
        level = (
            logging.INFO
            if verdict.canned_reply or verdict.fired_notification
            else logging.WARNING
        )
    repeated = level > logging.DEBUG and _visible_line_is_deduped(
        "|".join((provider, context.scope, context.subject, verdict.reason))
    )
    reported_sender = (
        "<paired-owner>" if verdict.meta.get("owner_paired") else sender_id
    )
    logger.log(
        logging.DEBUG if repeated else level,
        "channel inbound %s: provider=%s scope=%s reason=%s policy=%s sender=%s channel=%s%s%s",
        "admitted" if verdict.allowed else "discarded",
        provider,
        context.scope,
        verdict.reason or "-",
        policy or "-",
        reported_sender or "-",
        channel_id or "-",
        context.remedy(policy, verdict.allowed),
        " (repeat inside the renotify window)" if repeated else "",
    )
    return verdict


def _messages_counted(metadata: Any) -> int:
    try:
        return (
            max(0, int(metadata.get("count", 0))) if isinstance(metadata, dict) else 0
        )
    except (ValueError, TypeError):
        return 0


def owner_ref(provider: str) -> dict[str, str]:
    import os

    from gideon.core.config import loader
    from gideon.core.config.credentials import get_credential, owner_id_credential

    owner, source = "", ""
    for key, candidate_source in (
        (owner_id_credential(provider), "channel"),
        (loader.CRED_OWNER_ID, "shared"),
    ):
        value = os.environ.get(key, "").strip() or get_credential(key).strip()
        if value:
            owner, source = value, candidate_source
            break
    metadata = _lookup(provider, "allowed_senders").get(owner)
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "owner_id": owner,
        "owner_name": str(metadata.get("name") or "").strip(),
        "owner_source": source,
    }


def _claim_contact(
    provider: str, sender_id: str, name: str = "", *, held: bool = False
) -> bool:
    with _editing(provider) as change:
        rate = change.record["rate"]
        now = _now()
        if not held:
            seen = change.record["seen_senders"]
            before = seen.get(sender_id)
            before = before if isinstance(before, dict) else {}
            seen[sender_id] = {
                "name": name or str(before.get("name") or ""),
                "since": str(before.get("since") or "") or _iso(now),
                "last_seen": _iso(now),
                "count": _messages_counted(before) + 1,
            }
            newest = sorted(
                seen, key=lambda key: str(seen[key].get("last_seen", "")), reverse=True
            )
            change.record["seen_senders"] = {
                key: seen[key] for key in newest[:SEEN_SENDERS_MAX]
            }
            change.commit()
        previous = rate.get(sender_id, "")
        if previous:
            try:
                age = (now - datetime.fromisoformat(previous)).total_seconds()
                if age < UNKNOWN_SENDER_RENOTIFY_SECS:
                    return False
            except (ValueError, TypeError):
                pass
        rate[sender_id] = _iso(now)
        while len(rate) > 4096:
            oldest = min(rate, key=lambda key: str(rate.get(key, "")))
            if oldest == sender_id and len(rate) > 1:
                oldest = min(
                    (key for key in rate if key != sender_id),
                    key=lambda key: str(rate.get(key, "")),
                )
            del rate[oldest]
        change.commit()
        return True


def note_unknown_sender(
    state: Any,
    provider: str,
    sender_id: str,
    sender_name: str = "",
    *,
    silent: bool = False,
    held: bool = False,
) -> bool:
    if not _claim_contact(provider, sender_id, sender_name, held=held):
        return False
    _emit_sel("sender_denied", "unknown_sender", provider, sender_id)
    if state is not None:
        try:
            from gideon.workspace import notification_kinds

            if held:
                title = f"Someone new wrote to you on {provider}"
                message = (
                    "{} wrote to you on {} and isn't paired. Nothing was sent to them. "
                    "Their message is in your Inbox: reply to it, pair them, or ignore it."
                )
            else:
                title = f"Unknown {provider} sender wants to talk"
                message = "{} messaged your agent on {} but isn't paired. Allow them to converse, or deny."
            details = {
                "event": "channel.unknown_sender",
                "provider": provider,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "actions": ["allow", "deny"],
            }
            state.notify(
                notification_kinds.WARNING,
                title,
                message.format(sender_name or sender_id, provider),
                meta=details,
            )
        except Exception:
            logger.warning("unknown-sender owner notification failed", exc_info=True)
    return True


def apply_trust_action(
    action: str, provider: str, sender_id: str, name: str = ""
) -> bool:
    normalized = (action or "").strip().lower()
    if normalized in {"allow", "deny"}:
        if normalized == "allow":
            allow_sender(provider, sender_id, name, via="owner")
        else:
            deny_sender(provider, sender_id)
        return normalized == "allow"
    logger.debug("apply_trust_action: unknown action %r", action)
    return False


def owner_was_asked_about(provider: str, sender_id: str) -> bool:
    """Whether the trust gate recorded this sender as an unknown contact."""
    if not sender_id:
        return False
    rate = _lookup(provider, "rate")
    return isinstance(rate, dict) and sender_id in rate


def guard_inbound(
    state: Any,
    provider: str,
    sender_id: str,
    *,
    sender_name: str = "",
    channel_id: str = "",
    is_dm: bool = True,
    text: str = "",
    hold_for_owner: Callable[[], bool] | None = None,
) -> TrustVerdict:
    context = _InboundContext(provider, sender_id, channel_id, is_dm)
    if not is_dm and channel_id:
        note_seen_channel(provider, channel_id, channel_id)
    policy = context.policy()
    candidate = (text or "").strip()
    if (
        is_dm
        and _looks_like_a_pairing_code(candidate)
        and owner_pairing_code_outstanding(provider)
    ):
        if redeem_owner_pairing_code(provider, sender_id, candidate, sender_name):
            return report_inbound_verdict(
                provider,
                TrustVerdict(
                    False,
                    "owner_paired",
                    canned_reply=CANNED_OWNER_PAIRED_REPLY,
                    meta={"owner_paired": True},
                ),
                sender_id=sender_id,
                channel_id=channel_id,
                is_dm=is_dm,
                policy=policy,
            )
    decision = context.known_verdict(policy, text)
    if decision is None:
        eligible = (
            policy == "pairing"
            and _looks_like_a_pairing_code(candidate)
            and _pairing_code_outstanding(provider)
        )
        if eligible and redeem_pairing_code(
            provider, sender_id, candidate, sender_name
        ):
            decision = TrustVerdict(
                False, "paired", canned_reply=CANNED_PAIRED_REPLY, meta={"paired": True}
            )
            policy = ""
        else:
            held = False
            if hold_for_owner is not None:
                try:
                    held = bool(hold_for_owner())
                except Exception:
                    logger.warning("unknown-sender inbox hold failed", exc_info=True)
                announced = (
                    note_unknown_sender(
                        state,
                        provider,
                        sender_id,
                        sender_name,
                        silent=True,
                        held=True,
                    )
                    if held
                    else False
                )
            else:
                announced = note_unknown_sender(
                    state,
                    provider,
                    sender_id,
                    sender_name,
                    silent=policy == "owner_only",
                )
            decision = TrustVerdict(
                False,
                "unknown_sender",
                canned_reply=(
                    CANNED_PAIRING_REPLY
                    if announced and policy != "owner_only" and hold_for_owner is None
                    else ""
                ),
                fired_notification=announced,
                meta={"held_for_owner": held} if hold_for_owner is not None else {},
            )
    return report_inbound_verdict(
        provider,
        decision,
        sender_id=sender_id,
        channel_id=channel_id,
        is_dm=is_dm,
        policy=policy,
    )
