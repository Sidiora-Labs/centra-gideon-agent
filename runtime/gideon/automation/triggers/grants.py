"""Owner-confirmed execution grants bound to a trigger's exact action."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SEAL_KEY = "_owner_action_seal"
SEAL_VERSION = 1
GRANT_BOOK = "trigger_actions"


@dataclass(frozen=True)
class GrantQuestion:
    provider: str
    revision: str
    sentence: str


def _book() -> Any:
    from gideon.security.owner_grants import GrantBook

    return GrantBook(GRANT_BOOK)


def _action(trigger: Any) -> tuple[str, dict[str, Any]]:
    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return "", {}
    inline = workflow.get("inline")
    selected = inline if isinstance(inline, dict) else workflow
    provider = str(selected.get("provider") or "").strip()
    return provider, dict(selected)


def action_revision(trigger: Any) -> str:
    provider, action = _action(trigger)
    capabilities = getattr(trigger, "capabilities", None)
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    try:
        from gideon.automation.triggers.screening_policy import CAPABILITY_KEYS

        reach = {
            key: capabilities[key]
            for key in sorted(CAPABILITY_KEYS - {"providers"})
            if key in capabilities
        }
        encoded = json.dumps(
            {
                "version": SEAL_VERSION,
                "trigger_id": str(getattr(trigger, "id", "")),
                "kind": str(getattr(trigger, "kind", "")),
                "provider": provider,
                "action": action,
                "reach": reach,
                "spec": getattr(trigger, "spec", {}) or {},
                "gates": getattr(trigger, "gates", {}) or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(encoded).hexdigest()


def required_provider(trigger: Any) -> str:
    provider, _ = _action(trigger)
    if not provider:
        return ""
    try:
        from gideon.automation.triggers.screen import provider_is_read_only

        return "" if provider_is_read_only(provider) else provider
    except Exception:
        return provider


def _owner_recorded(trigger: Any, revision: str) -> bool:
    trigger_id = str(getattr(trigger, "id", ""))
    if not trigger_id or not revision:
        return False
    try:
        return _book().holds(trigger_id, revision)
    except Exception:
        return False


def is_granted(trigger: Any) -> bool:
    provider = required_provider(trigger)
    if not provider:
        return True
    revision = action_revision(trigger)
    capabilities = getattr(trigger, "capabilities", None)
    if not revision or not isinstance(capabilities, dict):
        return False
    seal = capabilities.get(SEAL_KEY)
    return bool(
        isinstance(seal, dict)
        and seal.get("version") == SEAL_VERSION
        and seal.get("provider") == provider
        and seal.get("revision") == revision
        and provider in (capabilities.get("providers") or [])
        and _owner_recorded(trigger, revision)
    )


def missing(trigger: Any) -> list[str]:
    provider = required_provider(trigger)
    return [provider] if provider and not is_granted(trigger) else []


def question(trigger: Any) -> GrantQuestion | None:
    provider = required_provider(trigger)
    revision = action_revision(trigger)
    if not provider or not revision or is_granted(trigger):
        return None
    return GrantQuestion(
        provider,
        revision,
        f"Allow this trigger to use {provider} with its current action and reach?",
    )


def grant(trigger: Any, *, confirmed_revision: str, principal: Any) -> bool:
    from gideon.security.approval_answer import OWNER

    provider = required_provider(trigger)
    revision = action_revision(trigger)
    trigger_id = str(getattr(trigger, "id", ""))
    if (
        getattr(principal, "kind", "") != OWNER
        or not provider
        or not trigger_id
        or not revision
        or confirmed_revision != revision
    ):
        return False
    try:
        _book().give(trigger_id, revision, principal=str(principal.label))
    except Exception:
        return False
    capabilities = getattr(trigger, "capabilities", None)
    updated = dict(capabilities) if isinstance(capabilities, dict) else {}
    providers = updated.get("providers")
    providers = list(providers) if isinstance(providers, (list, tuple)) else []
    if provider not in providers:
        providers.append(provider)
    updated["providers"] = providers
    updated[SEAL_KEY] = {
        "version": SEAL_VERSION,
        "provider": provider,
        "revision": revision,
    }
    trigger.capabilities = updated
    return True


def revoke(trigger_id: str) -> None:
    if not trigger_id:
        return
    _book().revoke(trigger_id)


def narrow(trigger: Any, before: Any) -> list[str]:
    old = getattr(before, "capabilities", None)
    old = old if isinstance(old, dict) else {}
    old_seal = old.get(SEAL_KEY)
    current_revision = action_revision(trigger)
    old_revision = action_revision(before)
    updated = dict(getattr(trigger, "capabilities", {}) or {})
    trigger_id = str(getattr(trigger, "id", ""))
    old_seal = old_seal if isinstance(old_seal, dict) else {}
    old_provider = str(old_seal.get("provider") or "")
    prior_is_owner_granted = bool(
        old_seal.get("version") == SEAL_VERSION
        and old_seal.get("provider") == required_provider(before)
        and old_seal.get("revision") == old_revision
        and _owner_recorded(before, old_revision)
    )
    provider = required_provider(trigger)
    candidate = updated.get(SEAL_KEY)
    candidate_is_owner_granted = bool(
        provider
        and isinstance(candidate, dict)
        and candidate.get("version") == SEAL_VERSION
        and candidate.get("provider") == provider
        and candidate.get("revision") == current_revision
        and provider in (updated.get("providers") or [])
        and _owner_recorded(trigger, current_revision)
    )
    unchanged = bool(
        prior_is_owner_granted
        and current_revision
        and current_revision == old_revision
    )
    if candidate_is_owner_granted or unchanged:
        if unchanged and not candidate_is_owner_granted:
            updated[SEAL_KEY] = dict(old_seal)
            providers = updated.get("providers")
            providers = list(providers) if isinstance(providers, (list, tuple)) else []
            if old_provider and old_provider not in providers:
                providers.append(old_provider)
            if providers:
                updated["providers"] = providers
        trigger.capabilities = updated
        return []
    if trigger_id:
        try:
            revoke(trigger_id)
        except OSError:
            if getattr(trigger, "enabled", True) is not False:
                raise
    updated.pop(SEAL_KEY, None)
    updated.pop("providers", None)
    trigger.capabilities = updated
    return [old_provider or provider] if old_provider or provider else []
