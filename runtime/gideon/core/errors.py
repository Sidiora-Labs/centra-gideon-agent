"""Platform-wide WHAT/WHY/FIX error envelope (PLATFORM-LEGIBILITY §2).

A failure returned into an LLM session is only useful if the model can act on it.
:class:`AgentError` carries the three facts that turn a dead-end into a
self-correction: **what** failed (with the concrete value), **why** (the
mechanism), and the exact **fix** (the next action) — plus optional
``suggestions`` (did-you-mean candidates the model can branch to). The harness
research this follows measured this shape converting failures into recovery
loops; the point is that the model reads structure, not prose.

**Distinct from the HTTP error envelope.** `AGENTS.md` §"Shared conventions" →
**Error envelope (HTTP)** owns the *wire* shape for API-route errors —
``{"error": {"code": "<lowercase_snake>", "message": ...}}`` — a thing a
browser/external client branches on, emitted by the one
:func:`gideon.http_errors.json_error` and registered in
:data:`gideon.http_errors.HTTP_ERROR_CODES`. ``AgentError`` is the carrier
*into an LLM session* (on ``ToolResult``/``ActionResult`` and the exceptions that
become tool-result text). The two never collide: HTTP codes are
``lowercase_snake``; agent codes are ``ERR_UPPER_SNAKE`` (asserted below). A route
handler keeps returning the wire envelope; a tool surfaces an ``AgentError``.

The registry :data:`ERROR_CODES` is **append-only** — a shipped code is a stable
surface an agent (and its saved prompts/SOPs) branch on, so it is never removed or
reworded once released (``tests/test_error_codes_append_only.py`` enforces it).
New failure paths add a code; they never repurpose one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

ERROR_CODES: dict[str, str] = {
    "ERR_TOOL_ARG_INVALID": (
        "A tool argument failed validation (wrong type, out of range, or not in "
        "the allowed set)."
    ),
    "ERR_MODEL_UNRESOLVED": (
        "The model/provider bound to a use case cannot be resolved — the pin names "
        "a provider absent from config, or no provider is configured."
    ),
    "ERR_HOOK_PROVIDER_UNKNOWN": (
        "A hook/trigger names an action provider that is not registered or not in "
        "the allowed set."
    ),
    "ERR_ACTION_PROVIDER_FAILED": (
        "An action provider raised while executing a trigger's action."
    ),
    "ERR_COMPUTER_USE_DISABLED": (
        "Desktop computer use is not armed on this machine — the out-of-band keystone "
        "enable file is absent, unreadable, or does not say enabled."
    ),
    "ERR_COMPUTER_USE_APP_NOT_ALLOWED": (
        "The target application is not on the operator's allowlist in the out-of-band "
        "keystone enable file. An empty or absent allowlist permits nothing, so an armed "
        "capability still drives no application until the operator names one."
    ),
    "ERR_COMPUTER_USE_SECURE_FIELD": (
        "The input destination is a secure/password field, a field whose label names a "
        "secret, a field already holding credential-shaped text, or a target shape the "
        "screen does not recognise. An unrecognised destination is refused like a password "
        "field is: a screen that only knows the shapes it was shown has a hole in it."
    ),
    "ERR_COMPUTER_USE_UNKNOWN_TOOL": (
        "The computer-use dispatch was asked for a tool it does not declare. The seven "
        "declared tools are the whole surface; an unknown name is refused rather than "
        "guessed at."
    ),
    "ERR_COMPUTER_USE_BAD_ARGUMENT": (
        "A computer-use argument is missing, the wrong type, or outside the range the "
        "snapshot supports (for example an element index past the last element the walked "
        "window exposes)."
    ),
    "ERR_COMPUTER_USE_STALE_INDEX": (
        "The element index names a snapshot that is unknown, has expired, or whose window "
        "has changed since it was walked. Acting on a stale index would press whatever now "
        "sits at that position, so it is refused and a fresh snapshot is required."
    ),
    "ERR_COMPUTER_USE_DRIVER_UNAVAILABLE": (
        "No accessibility driver is available for this platform, or the driver has no "
        "handler for this operation. A typed refusal, never a silent no-op — nothing was "
        "clicked, typed or changed on the desktop."
    ),
    "ERR_COMPUTER_USE_DRIVER_FAILED": (
        "The ceilinged driver subprocess could not be started, did not answer within its "
        "timeout, or returned something unreadable. Reported as a failure rather than as an "
        "empty result, because a computer-use no-op reads to a model as success."
    ),
    "ERR_COMPUTER_USE_AX_PERMISSION": (
        "The OS has not granted this process the accessibility permission needed to read a "
        "window's element tree or activate an element. Distinct from a driver failure because "
        "only a human can fix it, in the OS's own privacy settings — a program cannot grant "
        "itself input access, and this build never pops the system prompt on its own."
    ),
    "ERR_COMPUTER_USE_PLATFORM_UNSUPPORTED": (
        "This platform's desktop driver is not implemented yet. macOS is the only implemented "
        "driver; Windows (UI Automation) and Linux (AT-SPI) declare themselves and refuse. "
        "Distinct from a driver being unavailable: the platform is intended, named and "
        "resolvable — the implementation is simply absent, so nothing the operator configures "
        "on that machine changes the answer. Nothing was clicked, typed or changed."
    ),
    "ERR_COMPUTER_USE_UNATTENDED_NOT_GRANTED": (
        "A run with no human watching it (a cron fire, a channel message, an inbound caller) "
        "asked to drive the desktop, and the operator's out-of-band enable document does not "
        "grant that tool to unattended runs. Distinct from the capability being off: the "
        "machine is armed and the app is allowlisted — what is missing is the separate standing "
        "grant that lets it act with nobody present. An interactive run gets the approval "
        "prompt instead of this refusal."
    ),
    "ERR_BROWSE_CONFIG": (
        "A browse action's config is incomplete — it named no goal, or no page to start "
        "from. Refused before a browser is touched."
    ),
    "ERR_BROWSE_NO_TARGET": (
        "There is no Chrome DevTools page target for the browse provider to drive. A typed "
        "refusal rather than a silent no-op: an action that reports success while browsing "
        "nothing is indistinguishable to a workflow from one that did the work."
    ),
    "ERR_BROWSE_CONNECT_FAILED": (
        "Connecting to the configured CDP page target failed. The browser is not running, "
        "or the target's WebSocket URL has gone stale (they are per-tab and short-lived)."
    ),
    "ERR_BROWSE_INCIDENT_ACTIVE": (
        "Incident mode is on, which suspends unattended work, so the browse run was refused "
        "before it started rather than retried against a control someone deliberately pulled."
    ),
    "ERR_BROWSE_FAILED": (
        "A browse run ended without reaching its goal — the first navigation was denied by "
        "the BROWSE egress policy, the page could not be read, or the decision call failed. "
        "Distinct from a PARK, which succeeds with notes and asks for a human."
    ),
    "ERR_BROWSE_TARGET_UNKNOWN": (
        "A browse action named an execution target outside the closed `gateway`/`user_browser` "
        "vocabulary. Refused rather than read as the default: running on the gateway's own "
        "browser profile a task that asked for the operator's would use different logins than "
        "the config named."
    ),
    "ERR_BROWSE_TARGET_UNATTENDED": (
        "A scheduled, cron or otherwise unattended run named the `user_browser` execution "
        "target, which drives the browser the operator is already logged into and therefore "
        "requires a person present by construction. Refused at registration time where the "
        "automation is authored, and again at the provider before a browser is touched."
    ),
    "ERR_BROWSE_USER_BROWSER_DISCONNECTED": (
        "A `user_browser` browse task ran with no browser attached, so it was SKIPPED. It is "
        "never re-pointed at the gateway's own profile: that is a different cookie and "
        "credential context, so a fallback would do the work as somebody else."
    ),
    "ERR_SURFACE_OVERLAY_PATH": (
        "A user/agent surface overlay resolved outside $GIDEON_HOME/surfaces/ — a "
        "traversal, an absolute path, a symlink pointing away, or not a regular file."
    ),
    "ERR_SURFACE_OVERLAY_INVALID": (
        "A surface overlay is not the declarative DATA shape the L2 layer accepts: not "
        "JSON, not an object, an unknown key, a wrong value type, or over the size ceiling."
    ),
    "ERR_SURFACE_OVERLAY_COMPONENT": (
        "A surface overlay names a component the host registry does not have, or passes "
        "props its declared schema refuses. The whole overlay is refused rather than "
        "partially rendered, because a dropped node is an invisible failure."
    ),
    "ERR_NET_FETCH_CONFIG": (
        "A `net-fetch` action's config cannot be used: it named no URL, or the URL carries "
        "credentials in its userinfo. This action never sends credentials, so a URL holding "
        "them is refused rather than stripped and sent anyway."
    ),
    "ERR_NET_FETCH_EGRESS_BLOCKED": (
        "The egress guard refused a `net-fetch` before the request was made. Automated fetches "
        "are limited to an exclusive operator allow-list (`security.egress.allow_hosts`), which "
        "is EMPTY by default and therefore permits no host until one is added."
    ),
    "ERR_NET_FETCH_INCIDENT_ACTIVE": (
        "Incident mode is on, which suspends unattended work, so the fetch was refused before a "
        "request was made rather than retried against a control someone deliberately pulled."
    ),
    "ERR_NET_FETCH_FAILED": (
        "A permitted `net-fetch` did not return a usable body: the request failed before a "
        "response arrived (DNS, TLS, connection, timeout), or the host answered a non-2xx "
        "status. Distinct from an egress refusal, which never left the machine."
    ),
}


ENVELOPE_FIELD_CAP = 200

_ENVELOPE_LINES: tuple[str, ...] = ("what", "why", "fix")


@dataclass(frozen=True)
class AgentError:
    """One machine-readable failure an agent can recover from.

    ``code`` is a stable :data:`ERROR_CODES` key (branch on it, never on prose).
    ``what``/``why``/``fix`` are the three human/LLM-facing lines, each with the
    concrete value baked in. ``suggestions`` are did-you-mean candidates (nearest
    valid tool/provider/enum values) the model can pick from directly.
    """

    code: str
    what: str
    why: str
    fix: str
    suggestions: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        rows = [
            (label.upper(), getattr(self, label)) for label in ("what", "why", "fix")
        ]
        if self.suggestions:
            rows.append(("DID YOU MEAN", ", ".join(self.suggestions)))
        return "\n".join(f"{label}: {value}" for label, value in rows)

    def to_dict(self) -> dict[str, Any]:
        values = {name: getattr(self, name) for name in ("code", "what", "why", "fix")}
        return {**values, "suggestions": list(self.suggestions)}

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "AgentError":
        source = record if isinstance(record, dict) else {}
        values = {
            name: str(source.get(name, "") or "")
            for name in ("code", "what", "why", "fix")
        }
        suggestions = source.get("suggestions")
        candidates = suggestions if isinstance(suggestions, (list, tuple)) else ()
        return cls(**values, suggestions=tuple(str(item) for item in candidates))

    def bounded(self, limit: int = ENVELOPE_FIELD_CAP) -> "AgentError":
        """The same envelope with each line trimmed to *limit* characters.

        Bounded PER FIELD on purpose: capping the rendered envelope instead would cut
        it mid-line, and the line that goes first is FIX — the one an operator reading
        an unattended failure actually needs. A trimmed line ends in an ellipsis so a
        reader can tell a short explanation from a shortened one.
        """
        cap = max(1, int(limit))
        trimmed = {name: _clip(getattr(self, name), cap) for name in _ENVELOPE_LINES}
        return replace(self, **trimmed)


def _clip(text: str, limit: int) -> str:
    value = text or ""
    return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"


def redacted_envelope(
    envelope: AgentError, *, limit: int = ENVELOPE_FIELD_CAP
) -> AgentError:
    """*envelope* with credentials/exfiltration URLs scrubbed, then bounded per field.

    Redaction runs BEFORE the bound so a replacement marker cannot push a line back
    over the cap, which is what lets every carrier of this envelope — a run-history
    row, a notification body — state one size guarantee.
    """
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )
    except Exception:
        return envelope.bounded(limit)

    def scrub(text: str) -> str:
        current = text or ""
        for redact in (redact_exfiltration_urls, redact_credentials):
            current = redact(current)[0]
        return current

    try:
        cleaned = replace(
            envelope,
            **{name: scrub(getattr(envelope, name)) for name in _ENVELOPE_LINES},
        )
    except Exception:
        return envelope.bounded(limit)
    return cleaned.bounded(limit)
