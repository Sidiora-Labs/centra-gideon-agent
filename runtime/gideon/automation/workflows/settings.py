"""Resolve workflow limits from the active configuration on every call."""

from __future__ import annotations


def _workflows_config() -> object | None:
    try:
        from gideon.core.config.loader import AppConfig

        return AppConfig.load().workflows
    except Exception:
        return None


def _integer_setting(name: str, fallback: int) -> int:
    raw = getattr(_workflows_config(), name, None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def surface_mode_default() -> str:
    value = str(getattr(_workflows_config(), "surface_mode_default", "off") or "off")
    return next(
        (mode for mode in ("off", "passive", "suggest") if mode == value), "off"
    )


def fanout_task_cap() -> int:
    from gideon.automation.workflows.materialize import FANOUT_TASK_CAP

    value = _integer_setting("max_materialized_per_foreach", FANOUT_TASK_CAP)
    return FANOUT_TASK_CAP if value < 1 else value


def confirmation_ttl_secs() -> int:
    from gideon.automation.workflows.confirmation import DEFAULT_TTL_SECS

    return max(0, _integer_setting("confirmation_ttl_secs", DEFAULT_TTL_SECS))


def lease_ttl_secs() -> int:
    from gideon.automation.workflows.pool import DEFAULT_LEASE_SECS, MAX_LEASE_SECS

    value = _integer_setting("lease_ttl_secs", DEFAULT_LEASE_SECS)
    return min(value, MAX_LEASE_SECS) if value >= 1 else DEFAULT_LEASE_SECS
