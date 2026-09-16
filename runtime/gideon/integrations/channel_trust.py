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
from typing import Any

logger = logging.getLogger(__name__)
_ENTITY = "channel_trust"
PAIRING_CODE_TTL_SECS = 600
PAIRING_CODE_DIGITS = 8
DM_POLICIES: tuple[str, ...] = ("pairing", "owner_only", "open")
GROUP_POLICIES: tuple[str, ...] = ("tracked_only", "off")
DEFAULT_DM_POLICY = "pairing"
DEFAULT_GROUP_POLICY = "tracked_only"
UNKNOWN_SENDER_RENOTIFY_SECS = 24 * 3600
CANNED_PAIRING_REPLY = (
    "I don't recognize you yet. Ask my owner for an 8-digit pairing code "
    "(they can run `gideon pair <provider>`), then send it here to start talking."
)
CANNED_PAIRED_REPLY = "Paired — you can talk to me now."
_STORE_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _default_provider() -> dict[str, Any]:
    record = {
        key: {}
        for key in (
            "allowed_senders",
            "tracked_channels",
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
        event = {
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
    _update_directory(provider, "allowed_senders", sender_id, _entry(name, via=via))
    _emit_sel("sender_paired", via, provider, sender_id)


def deny_sender(provider: str, sender_id: str) -> None:
    _update_directory(provider, "allowed_senders", sender_id, None)
    _emit_sel("sender_denied", "owner", provider, sender_id)


def is_tracked_channel(provider: str, channel_id: str) -> bool:
    return channel_id in _lookup(provider, "tracked_channels")


def track(provider: str, channel_id: str, name: str = "") -> None:
    _update_directory(provider, "tracked_channels", channel_id, _entry(name))


def untrack(provider: str, channel_id: str) -> None:
    _update_directory(provider, "tracked_channels", channel_id, None)


def trust_policies(provider: str) -> dict[str, str]:
    return dict(_lookup(provider, "policies"))


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
    return {
        "provider": provider,
        "policies": dict(record["policies"]),
        "allowed_senders": _directory_projection(
            record["allowed_senders"], "sender_id", ("name", "added_at", "via")
        ),
        "tracked_channels": _directory_projection(
            record["tracked_channels"], "channel_id", ("name", "added_at")
        ),
        "pairing_active": bool(code.get("code_hash")),
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


def redeem_pairing_code(provider: str, sender_id: str, code: str) -> bool:
    with _editing(provider) as change:
        ticket = _PairingTicket.from_record(change.record["pairing"])
        outcome = ticket.verdict(code, _now())
        if outcome in ("paired", "expired_code"):
            change.record["pairing"] = {}
            if outcome == "paired":
                change.record["allowed_senders"][sender_id] = _entry("", via="pairing")
            change.commit()
    accepted = outcome == "paired"
    _emit_sel(
        "sender_paired" if accepted else "sender_denied",
        "pairing" if accepted else outcome,
        provider,
        sender_id,
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
        decision = None
        if self.is_dm:
            if policy == "open" or is_allowed_sender(self.provider, self.sender_id):
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
    logger.log(
        logging.DEBUG if repeated else level,
        "channel inbound %s: provider=%s scope=%s reason=%s policy=%s sender=%s channel=%s%s%s",
        "admitted" if verdict.allowed else "discarded",
        provider,
        context.scope,
        verdict.reason or "-",
        policy or "-",
        sender_id or "-",
        channel_id or "-",
        context.remedy(policy, verdict.allowed),
        " (repeat inside the renotify window)" if repeated else "",
    )
    return verdict


def _claim_contact(provider: str, sender_id: str) -> bool:
    with _editing(provider) as change:
        rate = change.record["rate"]
        now = _now()
        previous = rate.get(sender_id, "")
        if previous:
            try:
                age = (now - datetime.fromisoformat(previous)).total_seconds()
                if age < UNKNOWN_SENDER_RENOTIFY_SECS:
                    return False
            except (ValueError, TypeError):
                pass
        rate[sender_id] = _iso(now)
        change.commit()
        return True


def note_unknown_sender(
    state: Any,
    provider: str,
    sender_id: str,
    sender_name: str = "",
    *,
    silent: bool = False,
) -> bool:
    if not _claim_contact(provider, sender_id):
        return False
    _emit_sel("sender_denied", "unknown_sender", provider, sender_id)
    if state is not None:
        try:
            from gideon.workspace import notification_kinds

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
                f"Unknown {provider} sender wants to talk",
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


def guard_inbound(
    state: Any,
    provider: str,
    sender_id: str,
    *,
    sender_name: str = "",
    channel_id: str = "",
    is_dm: bool = True,
    text: str = "",
) -> TrustVerdict:
    context = _InboundContext(provider, sender_id, channel_id, is_dm)
    policy = context.policy()
    decision = context.known_verdict(policy, text)
    if decision is None:
        candidate = (text or "").strip()
        eligible = (
            policy == "pairing"
            and _looks_like_a_pairing_code(candidate)
            and _pairing_code_outstanding(provider)
        )
        if eligible and redeem_pairing_code(provider, sender_id, candidate):
            decision = TrustVerdict(
                False, "paired", canned_reply=CANNED_PAIRED_REPLY, meta={"paired": True}
            )
            policy = ""
        else:
            announced = note_unknown_sender(
                state, provider, sender_id, sender_name, silent=policy == "owner_only"
            )
            decision = TrustVerdict(
                False,
                "unknown_sender",
                canned_reply="" if policy == "owner_only" else CANNED_PAIRING_REPLY,
                fired_notification=announced,
            )
    return report_inbound_verdict(
        provider,
        decision,
        sender_id=sender_id,
        channel_id=channel_id,
        is_dm=is_dm,
        policy=policy,
    )
