"""Payload analysis, frozen capability admission and typed preflight ledger records."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

from .screening_policy import (
    _ALWAYS_UNTRUSTED,
    _B64_MIN_LEN,
    _B64_RE,
    _COMPILED,
    _HOMOGLYPHS,
    _INVISIBLE,
    APP_DELIVERED_PROVIDERS,
    BLOCKING_GROUPS,
    CAPABILITY_KEYS,
    EMPTY_MEANS,
    INJECTION_GROUPS,
    READ_ONLY_PROVIDERS,
    UNTRUSTED_PAYLOAD_KEYS,
    WRITE_CAPABLE_PROVIDERS,
)


class Verdict(str, Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


@dataclass
class ScreenResult:
    verdict: str
    matched_group: str = ""
    matched_pattern: str = ""
    groups: tuple[str, ...] = ()
    evaded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return Verdict.BLOCKED.value == self.verdict

    @property
    def clean(self) -> bool:
        return Verdict.CLEAN.value == self.verdict

    def to_dict(self) -> dict[str, Any]:
        return dict(
            verdict=self.verdict,
            matched_group=self.matched_group,
            matched_pattern=self.matched_pattern,
            groups=list(self.groups),
            evaded=self.evaded,
            notes=list(self.notes),
        )


def normalize(text: str) -> str:
    if not text:
        return ""
    canonical = unicodedata.normalize("NFKC", text)
    visible = canonical.translate(_INVISIBLE).casefold()
    return re.sub(r"[ \t ]+", " ", visible.translate(_HOMOGLYPHS))


def _decode_token(token: str) -> str | None:
    try:
        padded = token.ljust(len(token) + (-len(token) % 4), "=")
        decoded = base64.b64decode(padded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not decoded.strip():
        return None
    printable = sum(
        character.isprintable() or character.isspace() for character in decoded
    )
    return decoded if printable / max(1, len(decoded)) > 0.9 else None


def decoded_segments(text: str, *, limit: int = 8) -> list[str]:
    decoded = []
    for candidate in _B64_RE.finditer(text or ""):
        if len(decoded) >= limit:
            break
        value = _decode_token(candidate.group())
        if value is not None:
            decoded.append(value)
    return decoded


@dataclass(frozen=True)
class MatchEvidence:
    group: str
    pattern: str
    concealed: bool

    @property
    def key(self) -> tuple[str, str]:
        return self.group, self.pattern


@dataclass
class PayloadScan:
    hits: list[MatchEvidence] = field(default_factory=list)
    visible: set[tuple[str, str]] = field(default_factory=set)

    def inspect(self, text: str, stage: str) -> None:
        for group, patterns in _COMPILED.items():
            for rule in patterns:
                if stage == "decoded":
                    matched = rule.search(text)
                else:
                    try:
                        matched = rule.search(text)
                    except Exception:
                        continue
                if matched is None:
                    continue
                key = (group, rule.pattern)
                hidden = stage == "decoded" or (
                    stage == "folded" and key not in self.visible
                )
                hit = MatchEvidence(group, rule.pattern, hidden)
                self.hits.append(hit)
                if stage == "raw":
                    self.visible.add(hit.key)
                break

    def result(self) -> ScreenResult:
        if not self.hits:
            return ScreenResult(Verdict.CLEAN.value)
        concealed = any(hit.concealed for hit in self.hits)
        primary = next((hit for hit in self.hits if hit.group in BLOCKING_GROUPS), None)
        blocked = primary is not None or concealed
        primary = primary or self.hits[0]
        note = f"matched the {primary.group} group"
        notes = [
            note if blocked else note + "; the payload is fenced rather than dropped"
        ]
        if blocked and concealed:
            notes.append("the match was hidden by encoding or invisible characters")
        return ScreenResult(
            Verdict.BLOCKED.value if blocked else Verdict.SUSPICIOUS.value,
            primary.group,
            primary.pattern,
            tuple(sorted({hit.group for hit in self.hits})),
            concealed,
            notes,
        )


def screen(text: str) -> ScreenResult:
    if not text or not text.strip():
        return ScreenResult(Verdict.CLEAN.value)
    scan = PayloadScan()
    scan.inspect(text, "raw")
    scan.inspect(normalize(text), "folded")
    for decoded in decoded_segments(text):
        scan.inspect(normalize(decoded), "decoded")
    return scan.result()


@dataclass
class CapabilityDecision:
    allowed: bool
    reason: str = ""
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(allowed=self.allowed, reason=self.reason, key=self.key)


def _matches_entry(value: str, entry: str) -> bool:
    if not entry:
        return False
    return value.startswith(entry[:-1]) if entry.endswith("*") else value == entry


def provider_is_read_only(provider: str) -> bool:
    return (provider or "").strip() in READ_ONLY_PROVIDERS


def requested_capabilities(trigger: Any) -> dict[str, list[str]]:
    action = getattr(trigger, "workflow", None)
    if not isinstance(action, dict):
        return {}
    inline = action.get("inline")
    if isinstance(inline, dict) and inline:
        action = inline
    provider = str(action.get("provider") or "").strip()
    return dict(providers=[provider]) if provider else {}


def capabilities_for_action(trigger: Any) -> dict[str, Any]:
    request = requested_capabilities(trigger)
    writes = list(
        filter(
            lambda name: not provider_is_read_only(name), request.get("providers", ())
        )
    )
    return dict(providers=writes) if writes else {}


@dataclass(frozen=True)
class CapabilityCheck:
    capabilities: dict[str, Any] | None
    key: str
    value: str

    def answer(self, allowed: bool, reason: str = "") -> CapabilityDecision:
        return CapabilityDecision(allowed, reason, self.key)

    def evaluate(self) -> CapabilityDecision:
        key = self.key
        if key not in CAPABILITY_KEYS:
            return self.answer(
                False,
                f"unknown capability {key!r}; expected one of {', '.join(sorted(CAPABILITY_KEYS))}",
            )
        if not self.capabilities:
            return self.answer(
                False, "this trigger declares no capabilities, so nothing is permitted"
            )
        entries = self.capabilities.get(key)
        if entries is None:
            return self.answer(
                False, f"this trigger declares no {key}, so none is permitted"
            )
        if not isinstance(entries, (list, tuple, set, frozenset)):
            return self.answer(
                False,
                f"the {key} allowlist must be a list; a {type(entries).__name__} is refused rather than coerced, so a malformed fence cannot silently grant access",
            )
        if key == "paths":
            from gideon.automation.triggers.pathguard import path_allowed

            return self.answer(*path_allowed(entries, self.value))
        if any(
            _matches_entry(self.value, entry)
            for entry in entries
            if isinstance(entry, str)
        ):
            return self.answer(True)
        return self.answer(
            False, f"{self.value!r} is not in this trigger's frozen {key} allowlist"
        )


def capability_allows(
    capabilities: dict[str, Any] | None, *, key: str, value: str
) -> CapabilityDecision:
    return CapabilityCheck(capabilities, key, value).evaluate()


def freeze_capabilities(capabilities: dict[str, Any] | None) -> dict[str, list[str]]:
    frozen = {}
    source = capabilities or {}
    for key in sorted(CAPABILITY_KEYS):
        value = source.get(key)
        if isinstance(value, str):
            frozen[key] = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            frozen[key] = sorted(
                set(str(item) for item in value if isinstance(item, str) and item)
            )
    return frozen


@dataclass(frozen=True)
class PayloadFields:
    kind: str

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(UNTRUSTED_PAYLOAD_KEYS.get(self.kind, ())).union(_ALWAYS_UNTRUSTED)
            )
        )

    def values(self, payload: dict[str, Any]) -> Iterator[Any]:
        return (payload[key] for key in self.keys if key in payload)

    @staticmethod
    def strings(values: Iterator[Any]) -> Iterator[str]:
        stack = [iter(values)]
        while stack:
            try:
                value = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if isinstance(value, str):
                if value.strip():
                    yield value
            elif isinstance(value, (list, tuple, dict)):
                stack.append(iter(value.values() if isinstance(value, dict) else value))


def payload_text_for(payload: dict[str, Any] | None, *, kind: str = "") -> str:
    if not isinstance(payload, dict):
        return ""
    fields = PayloadFields(kind)
    return "\n".join(fields.strings(fields.values(payload)))


@dataclass(frozen=True)
class PayloadFence:
    kind: str
    trigger_id: str

    def transform(self, value: Any) -> Any:
        if isinstance(value, list):
            return list(map(self.transform, value))
        if isinstance(value, tuple):
            return tuple(map(self.transform, value))
        if isinstance(value, dict):
            return {key: self.transform(item) for key, item in value.items()}
        if not isinstance(value, str) or not value.strip():
            return value
        from gideon.security.security import fence_untrusted, is_fenced

        if is_fenced(value):
            return value
        return fence_untrusted(
            value,
            source=(
                f"trigger:{self.trigger_id}" if self.trigger_id else "trigger-payload"
            ),
            source_type=self.kind or "trigger",
            source_id=self.trigger_id,
            transformation_path="fire:payload",
        )


def fence_payload(
    payload: dict[str, Any] | None, *, kind: str = "", trigger_id: str = ""
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    selected = PayloadFields(kind).keys
    fence = PayloadFence(kind, trigger_id)
    return {
        key: fence.transform(value) if key in selected else value
        for key, value in payload.items()
    }


def unfenced_actions(
    capabilities: dict[str, Any] | None, *, requested: dict[str, list[str]]
) -> list[tuple[str, str, str]]:
    refused = []
    for key, values in (requested or {}).items():
        for value in values:
            decision = capability_allows(capabilities, key=key, value=value)
            if decision.allowed:
                continue
            refused.append((key, value, decision.reason))
    return refused


_SCREEN_OUTCOMES = {
    Verdict.BLOCKED.value: "blocked_injection",
    Verdict.SUSPICIOUS.value: "ran",
    Verdict.CLEAN.value: "ran",
}


def screen_to_outcome(verdict: str) -> str:
    return _SCREEN_OUTCOMES.get(verdict, "blocked_injection")


def screen_ledger_row(
    *, trigger_id: str, result: ScreenResult, source: str = ""
) -> dict[str, Any] | None:
    if result.clean:
        return None
    reason = f"the injection screen matched the {result.matched_group} group"
    if result.evaded:
        reason += " (hidden by encoding)"
    row = dict(
        trigger_id=trigger_id,
        outcome=screen_to_outcome(result.verdict),
        reason=reason,
        screen_verdict=result.verdict,
        screen_group=result.matched_group,
        screen_pattern=result.matched_pattern,
        screen_groups=list(result.groups),
        screen_evaded=result.evaded,
        retryable=not result.blocked,
    )
    if source:
        row.update(source=source)
    return row


def capability_ledger_row(
    *, trigger_id: str, decision: CapabilityDecision, value: str
) -> dict[str, Any] | None:
    if decision.allowed:
        return None
    return dict(
        trigger_id=trigger_id,
        outcome="refused",
        reason=decision.reason,
        capability_key=decision.key,
        capability_value=value,
        retryable=False,
    )


def budget_ledger_row(
    *,
    trigger_id: str,
    spent: float,
    ceiling: float,
    window: str = "day",
    check_failed: bool = False,
) -> dict[str, Any]:
    row = dict(
        trigger_id=trigger_id,
        outcome="ran",
        reason="",
        budget_window=window,
        budget_verified=not check_failed,
        retryable=True,
    )
    if check_failed:
        row.update(
            reason="the budget check could not complete; the fire proceeded (budget gates fail open so a broken probe cannot wedge every automation) — spend was NOT verified"
        )
    else:
        row.update(budget_spent=spent, budget_ceiling=ceiling)
        if ceiling > 0 and spent >= ceiling:
            row.update(
                outcome="skipped_budget",
                reason=f"the {window} budget ceiling of {ceiling:g} was already reached (spent {spent:g})",
            )
    return row
