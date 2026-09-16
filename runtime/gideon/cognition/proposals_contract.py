"""C6 — the Proposal contract and its apply dispatcher (INU-7).

One payload (:class:`Proposal`, carried in ``refs["proposal"]``) and one
dispatcher (:func:`apply_item`) own proposal mechanics. Execution stays in
the existing dispatchers for each case — this module owns no I/O.

The apply case set is CLOSED: exactly one of ``action``, ``workflow``,
``skill_promotion``, ``app_callback``. Zero, two, or unknown keys raise.
A failed apply keeps the item PENDING with the error recorded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

REFS_KEY = "proposal"
RESULT_KEY = "proposal_result"
ERROR_KEY = "proposal_error"
PREVIEW_KINDS = ("text", "diff")


class ApplyCase(str, Enum):
    ACTION = "action"
    WORKFLOW = "workflow"
    SKILL_PROMOTION = "skill_promotion"
    APP_CALLBACK = "app_callback"


class ProposalError(Exception):
    pass


# ---------------------------------------------------------------------------
# Handler registry — _HANDLERS[case_name] = async callable(args, ctx) → dict
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]] = {}


def _register_case(name: str):
    def deco(fn):
        _HANDLERS[name] = fn
        return fn

    return deco


@_register_case("action")
async def _handle_action(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    from gideon.automation.triggers.tools import manual_refusal
    from gideon.integrations.action_providers import registry
    from gideon.integrations.action_providers.base import ActionContext

    refusal = manual_refusal()
    if refusal:
        raise ProposalError(f"refused: {refusal}")
    provider_name = str(args.get("provider") or "")
    if not provider_name:
        raise ProposalError("apply.action needs a `provider`")
    registry._ensure_default_providers_registered()
    provider = registry.get_action_provider(provider_name)
    if provider is None:
        raise ProposalError(f"unknown action provider {provider_name!r}")
    result = await provider.execute(
        dict(args.get("config") or {}),
        ActionContext(event="proposal_apply", payload={"item_id": ctx.item_id}),
    )
    if not getattr(result, "success", False):
        detail = getattr(result, "error", "") or getattr(result, "stderr", "")
        raise ProposalError(str(detail or "action provider failed"))
    return {
        "provider": provider_name,
        "output": str(getattr(result, "stdout", "") or ""),
    }


@_register_case("workflow")
async def _handle_workflow(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    from gideon.automation.workflows.service import start_run

    ref = str(args.get("ref") or "")
    if not ref:
        raise ProposalError("apply.workflow needs a `ref` (a saved definition name)")
    res = await start_run(
        name=ref,
        inputs=dict(args.get("inputs") or {}),
        idempotency_key=f"proposal:{ctx.item_id}",
    )
    if not res.get("ok"):
        raise ProposalError(
            str(res.get("error") or res.get("code") or "workflow start failed")
        )
    return {"workflow": ref, "run_id": str(res.get("run_id") or "")}


@_register_case("skill_promotion")
async def _handle_skill_promotion(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    from gideon.cognition.learning import proposals as learning_proposals

    pid = str(args.get("pid") or "")
    if not pid:
        raise ProposalError("apply.skill_promotion needs a `pid`")
    install_fn = ctx.installer
    if install_fn is None:
        from gideon.cognition.learning import project_context_review
        from gideon.cognition.learning import skill_promotion as skill_promotion_mod
        from gideon.extensions.packs import prompt_cards

        def install_fn(prop) -> None:  # noqa: F811
            data = prop.to_dict()
            routes = (
                (
                    prompt_cards.is_prompt_card_proposal,
                    prompt_cards.install_accepted_prompt_card,
                ),
                (
                    project_context_review.is_project_context_proposal,
                    project_context_review.install_accepted_project_context,
                ),
                (
                    skill_promotion_mod.is_skill_promotion_proposal,
                    skill_promotion_mod.install_accepted_skill,
                ),
            )
            for accepts, install in routes:
                if accepts(data):
                    install(data)
                    break

    accepted = learning_proposals.accept(pid, installer=install_fn, actor=ctx.actor)
    return {"pid": pid, "status": getattr(accepted, "status", "")}


@_register_case("app_callback")
async def _handle_app_callback(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    from gideon.integrations.tool_providers.app_routes import (
        call_app_route,
        resolve_route,
    )

    app_name = str(args.get("app") or "")
    route = str(args.get("route") or "")
    if not app_name or not route:
        raise ProposalError("apply.app_callback needs `app` and `route`")
    resolution = resolve_route(app_name, route, dict(args.get("arguments") or {}))
    result = await call_app_route(resolution)
    if not getattr(result, "success", False):
        raise ProposalError(str(getattr(result, "error", "") or "app callback failed"))
    return {
        "app": app_name,
        "route": route,
        "output": str(getattr(result, "output", "") or ""),
    }


# Assert totality at import time.
_expected_cases = {c.value for c in ApplyCase}
_missing = _expected_cases - set(_HANDLERS)
if _missing:
    raise RuntimeError(f"ApplyCase members without a handler: {sorted(_missing)}")

# Re-export as the ApplyCase-keyed dispatch map for consumers that read _DISPATCH.
_DISPATCH: dict[
    ApplyCase, Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]
] = {ApplyCase(k): v for k, v in _HANDLERS.items()}


# ---------------------------------------------------------------------------
# Proposal payload
# ---------------------------------------------------------------------------


def _unpack_apply(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract the single (case_key, case_args) from an apply dict.

    Raises ProposalError on empty, multiple-key, or non-dict args.
    """
    if not isinstance(raw, dict) or not raw:
        raise ProposalError("proposal.apply is empty: exactly one apply case required")
    keys = sorted(str(k) for k in raw)
    if len(keys) > 1:
        raise ProposalError(
            f"proposal.apply declares {len(keys)} cases ({', '.join(keys)}): exactly one"
        )
    case_key = keys[0]
    case_args = raw.get(case_key)
    return case_key, case_args


def _parse_timestamp_iso(value: str, *, now: float | None) -> bool:
    """True when the ISO-8601 value is in the past. Unparseable → not expired."""
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        logger.debug("proposal expires_at unparseable: %r", value)
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() < (now if now is not None else time.time())


# Declarative schema for from_dict: (field_name, default_factory, coerce)
# coerce receives the raw value and returns the coerced value.
_FIELD_SCHEMA = (
    ("title", lambda: "", lambda v: str(v or "")),
    ("preview", lambda: "", lambda v: str(v or "")),
    (
        "preview_kind",
        lambda: "text",
        lambda v: str(v or "text") if str(v or "text") in PREVIEW_KINDS else "text",
    ),
    ("provenance", lambda: "", lambda v: str(v or "")),
    ("expires_at", lambda: None, lambda v: str(v) if v else None),
    ("editable", lambda: False, lambda v: bool(v) if v is not None else False),
    ("apply", lambda: {}, lambda v: dict(v) if isinstance(v, dict) else {}),
)


@dataclass(frozen=True)
class Proposal:
    title: str
    preview: str = ""
    preview_kind: str = "text"
    provenance: str = ""
    expires_at: str | None = None
    editable: bool = False
    apply: dict[str, Any] = field(default_factory=dict)

    def apply_case(self) -> ApplyCase:
        """The single apply case this proposal declares. Raises ProposalError on deviation."""
        case_key, _ = _unpack_apply(self.apply)
        try:
            return ApplyCase(case_key)
        except ValueError:
            known = ", ".join(c.value for c in ApplyCase)
            raise ProposalError(
                f"unknown apply case {case_key!r} (known: {known})"
            ) from None

    def payload(self) -> dict[str, Any]:
        """The declared case's argument dict."""
        case = self.apply_case()
        arguments = self.apply.get(case.value)
        return dict(arguments) if isinstance(arguments, dict) else {}

    def is_expired(self, *, now: float | None = None) -> bool:
        """True when expires_at is in the past. Unparseable → not expired."""
        if not self.expires_at:
            return False
        return _parse_timestamp_iso(self.expires_at, now=now)

    def to_dict(self) -> dict[str, Any]:
        out = {name: getattr(self, name) for name, _, _ in _FIELD_SCHEMA}
        out["apply"] = dict(self.apply)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "Proposal":
        """Tolerant deserialization. apply shape validated only on apply_case()."""
        if not isinstance(data, dict):
            raise ProposalError("proposal payload is not an object")
        kwargs: dict[str, Any] = {}
        for field_name, default_fn, coerce_fn in _FIELD_SCHEMA:
            raw = data.get(field_name)
            kwargs[field_name] = coerce_fn(raw) if raw is not None else default_fn()
        return cls(**kwargs)


@dataclass(frozen=True)
class ApplyOutcome:
    ok: bool
    case: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "case": self.case,
            "result": dict(self.result),
            "error": self.error,
        }


@dataclass
class ApplyContext:
    item_id: str = ""
    actor: str = "user"
    installer: Callable[[Any], None] | None = None


# ---------------------------------------------------------------------------
# Core apply flow — validation → dispatch → outcome
# ---------------------------------------------------------------------------


def _format_exception(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _build_outcome_error(case_value: str, exc: Exception) -> ApplyOutcome:
    """Build a failure outcome, logging unexpected exceptions."""
    if not isinstance(exc, ProposalError):
        logger.warning("proposal apply (%s) failed: %s", case_value, exc, exc_info=True)
    detail = str(exc) if isinstance(exc, ProposalError) else _format_exception(exc)
    return ApplyOutcome(ok=False, case=case_value, error=detail)


async def apply_proposal(
    proposal: Proposal,
    *,
    item_id: str = "",
    actor: str = "user",
    installer: Callable[[Any], None] | None = None,
) -> ApplyOutcome:
    """Run one proposal's apply case. Never raises — returns a typed outcome."""
    try:
        case = proposal.apply_case()
    except ProposalError as exc:
        return ApplyOutcome(ok=False, error=str(exc))

    handler = _DISPATCH[case]
    ctx = ApplyContext(item_id=item_id, actor=actor, installer=installer)
    try:
        result = await handler(proposal.payload(), ctx)
    except Exception as exc:
        return _build_outcome_error(case.value, exc)
    return ApplyOutcome(ok=True, case=case.value, result=result)


def proposal_of(item: Any) -> Proposal | None:
    """The C6 payload on an inbox item, or None when it carries none."""
    refs = getattr(item, "refs", None)
    if not isinstance(refs, dict):
        return None
    raw = refs.get(REFS_KEY)
    if raw is None:
        return None
    try:
        return Proposal.from_dict(raw)
    except ProposalError:
        logger.debug(
            "item %s carries an unreadable proposal payload", getattr(item, "id", "?")
        )
        return None


def _validate_edit(proposal: Proposal, edited: dict[str, Any]) -> Proposal:
    """Parse an edited payload, raising on non-editable or malformed."""
    if not proposal.editable:
        raise ProposalError("this proposal is not editable")
    try:
        return Proposal.from_dict(edited)
    except ProposalError as exc:
        raise ProposalError(f"edited payload rejected: {exc}") from exc


async def apply_item(
    item: Any,
    *,
    store: Any = None,
    edited: dict[str, Any] | None = None,
    actor: str = "user",
    installer: Callable[[Any], None] | None = None,
) -> ApplyOutcome:
    """Apply the proposal on item and write the outcome back to the row.

    Success → HANDLED + refs[proposal_result]. Failure → status untouched,
    refs[proposal_error] recorded. Persisted on both paths.
    """
    from gideon.integrations.inbox import ItemStatus

    proposal = proposal_of(item)
    if proposal is None:
        return ApplyOutcome(ok=False, error="item carries no proposal payload")

    if edited is not None:
        try:
            proposal = _validate_edit(proposal, edited)
        except ProposalError as exc:
            return ApplyOutcome(ok=False, error=str(exc))
        item.refs[REFS_KEY] = proposal.to_dict()

    if proposal.is_expired():
        outcome = ApplyOutcome(ok=False, error="proposal expired")
        _record(item, store, outcome)
        return outcome

    outcome = await apply_proposal(
        proposal, item_id=str(getattr(item, "id", "")), actor=actor, installer=installer
    )
    if outcome.ok:
        item.status = ItemStatus.HANDLED.value
        item.refs[RESULT_KEY] = outcome.to_dict()
        item.refs.pop(ERROR_KEY, None)
    _record(item, store, outcome)
    return outcome


def _record(item: Any, store: Any, outcome: ApplyOutcome) -> None:
    """Record the outcome and persist to the store if available."""
    if not outcome.ok:
        item.refs[ERROR_KEY] = outcome.to_dict()
    if store is None:
        return
    try:
        if (
            store.update(getattr(item, "id", ""), status=item.status, refs=item.refs)
            is None
        ):
            logger.warning(
                "proposal apply: item %s not in store", getattr(item, "id", "?")
            )
    except Exception:
        logger.warning(
            "proposal apply: inbox write failed for %s", getattr(item, "id", "?")
        )


# ---------------------------------------------------------------------------
# App emission naming + kind registration
# ---------------------------------------------------------------------------

APP_SOURCE_PREFIX = "app:"
APP_KIND_PREFIX = "proposal:"


def app_source(app_name: str) -> str:
    return f"{APP_SOURCE_PREFIX}{app_name}"


def app_kind(kind_suffix: str) -> str:
    return f"{APP_KIND_PREFIX}{kind_suffix}"


def _manifest_proposals(manifest: Any) -> list[Any]:
    return list(getattr(getattr(manifest, "permissions", None), "proposals", []) or [])


def _register_one_kind(nk_mod: Any, app_name: str, suffix: str, label: str) -> bool:
    """Register a single proposal kind. Returns True on success, False on duplicate."""
    kind = app_kind(suffix)
    try:
        nk_mod.register(
            nk_mod.NotificationKind(
                source=app_source(app_name),
                kind=kind,
                label=label or suffix,
                attention=True,
                verifiable=True,
            )
        )
    except ValueError:
        logger.debug("app %s: proposal kind %s already registered", app_name, kind)
        return False
    return True


def register_app_proposal_kinds(app_name: str, manifest: Any) -> list[str]:
    """Register every declared proposal kind for app_name. Idempotent."""
    from gideon.workspace import notification_kinds

    registered: list[str] = []
    for entry in _manifest_proposals(manifest):
        suffix = getattr(entry, "kind_suffix", "")
        if not suffix or not getattr(entry, "is_valid", lambda: False)():
            logger.warning(
                "app %s: skipping invalid proposal kind_suffix %r", app_name, suffix
            )
            continue
        _register_one_kind(
            notification_kinds, app_name, suffix, getattr(entry, "label", "") or ""
        )
        registered.append(app_kind(suffix))
    return registered


def deregister_app_proposal_kinds(app_name: str, manifest: Any) -> list[str]:
    """Drop app_name's proposal kinds on disable/uninstall."""
    from gideon.workspace import notification_kinds

    src = app_source(app_name)
    dropped: list[str] = []
    for entry in _manifest_proposals(manifest):
        suffix = getattr(entry, "kind_suffix", "")
        if not suffix:
            continue
        kind = app_kind(suffix)
        if notification_kinds.unregister(src, kind):
            dropped.append(kind)
    return dropped


_apply_action = _handle_action
_apply_workflow = _handle_workflow
_apply_skill_promotion = _handle_skill_promotion
_apply_app_callback = _handle_app_callback
