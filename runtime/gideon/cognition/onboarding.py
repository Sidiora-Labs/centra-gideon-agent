"""Guided setup progress: tolerant reads and partial, validated entity-state writes."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ENTITY = "onboarding"

STEPS: tuple[str, ...] = ("name", "essentials", "first_success", "done")

_ESSENTIALS_SCHEMA: dict[str, type] = {
    "model": str,
    "search": bool,
    "speech": bool,
    "channel": str,
}

_FIRST_SUCCESS_KEYS: tuple[str, ...] = ("knowledge", "trigger", "loop")

_FIRST_SUCCESS_SCHEMA: dict[str, type] = {k: bool for k in _FIRST_SUCCESS_KEYS}

#: The document's nested blocks, in on-disk order. This single table is what the three views
#: of the schema share: :func:`default_state` builds from it, :func:`_sanitize` projects onto
#: it, and :func:`merge_onboarding_state` validates against it. One description means the
#: tolerant read path and the strict write path cannot drift on what a field is called, what
#: type it holds, or where it lives.
_SECTIONS: tuple[tuple[str, dict[str, type]], ...] = (
    ("essentials", _ESSENTIALS_SCHEMA),
    ("first_success", _FIRST_SUCCESS_SCHEMA),
)


def _fallback(typ: type) -> Any:
    """The value a missing (or mistyped) field of declared type ``typ`` falls back to."""
    return False if typ is bool else None


def _well_typed(typ: type, value: Any) -> bool:
    """Whether ``value`` may stand in for a field declared ``typ``."""
    if typ is bool:
        return isinstance(value, bool)
    return value is None or isinstance(value, str)


def _type_error(section: str, name: str, typ: type) -> str:
    """The 400-ready message for a value that is not ``typ``."""
    if typ is bool:
        return f"'{section}.{name}' must be a boolean"
    return f"'{section}.{name}' must be a string or null"


def default_state() -> dict[str, Any]:
    """A freshly-installed home's onboarding state."""
    state: dict[str, Any] = {"step": STEPS[0]}
    for section, schema in _SECTIONS:
        state[section] = {name: _fallback(typ) for name, typ in schema.items()}
    return state


def _sanitize(raw: Any) -> dict[str, Any]:
    """Project whatever is on disk onto the schema, field by field."""
    state = default_state()
    if not isinstance(raw, dict):
        return state

    step = raw.get("step")
    if isinstance(step, str) and step in STEPS:
        state["step"] = step

    for section, schema in _SECTIONS:
        stored = raw.get(section)
        if not isinstance(stored, dict):
            continue
        projected = state[section]
        for name, typ in schema.items():
            value = stored.get(name)
            if _well_typed(typ, value):
                projected[name] = value

    return state


def load_onboarding_state() -> dict[str, Any]:
    """The sanitized onboarding state. Never raises, never returns a partial shape."""
    try:
        from gideon.extensions.providers.entity_routes import _load_entity_settings

        return _sanitize(_load_entity_settings(_ENTITY) or {})
    except Exception:  # noqa: BLE001 — onboarding must never 500 the first-run signal
        logger.warning(
            "onboarding state unreadable — starting from the top", exc_info=True
        )
        return default_state()


def _validate_nested(name: str, patch: Any, schema: dict[str, type]) -> dict[str, Any]:
    """Validate one nested block of a patch, returning only its supplied keys."""
    if not isinstance(patch, dict):
        raise ValueError(f"'{name}' must be a JSON object")
    accepted: dict[str, Any] = {}
    for key, value in patch.items():
        typ = schema.get(key)
        if typ is None:
            raise ValueError(f"Unknown '{name}' field: {key!r}")
        if not _well_typed(typ, value):
            raise ValueError(_type_error(name, key, typ))
        accepted[key] = value
    return accepted


def merge_onboarding_state(patch: Any) -> dict[str, Any]:
    """Merge a partial patch into the stored state and persist it."""
    if not isinstance(patch, dict):
        raise ValueError("Body must be a JSON object")

    allowed = {"step"} | {section for section, _ in _SECTIONS}
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(repr(k) for k in unknown)}")

    state = load_onboarding_state()
    if "step" in patch:
        step = patch["step"]
        if not isinstance(step, str) or step not in STEPS:
            raise ValueError(f"'step' must be one of: {', '.join(STEPS)}")

    validated = {
        section: _validate_nested(section, patch[section], schema)
        for section, schema in _SECTIONS
        if section in patch
    }

    if "step" in patch:
        state["step"] = patch["step"]
    for section, supplied in validated.items():
        state[section].update(supplied)

    from gideon.extensions.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, state)
    return state
