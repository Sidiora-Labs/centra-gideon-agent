"""Read the canonical proactive schedule, persisted digest output and ledger."""

from __future__ import annotations

from typing import Any

TRIAGE_TRIGGER_ID = "system:triage:digest"


def _config() -> Any:
    from gideon.core.config.loader import AppConfig

    return AppConfig.load()


def _proactive(config: Any) -> Any:
    return getattr(config, "proactive", None)


def _trigger_store() -> Any:
    from gideon.automation.triggers.store import TriggerStore

    return TriggerStore()


def _find_schedule(store: Any) -> Any:
    """The digest schedule, by its deterministic id, or None.

    `store.get` returns a `LoadedTrigger` — the row PLUS whatever was wrong with reading it. The
    entity to write is the `.trigger` inside; upserting the pair would set attributes on the
    wrapper and persist something with no id.
    """
    row = store.get(TRIAGE_TRIGGER_ID)
    return None if row is None else row.trigger


def _schedule_payload(trigger: Any) -> dict[str, Any]:
    spec = getattr(trigger, "spec", None) or {}
    return {
        "id": str(getattr(trigger, "id", "") or ""),
        "name": str(getattr(trigger, "name", "") or ""),
        "cron": str(spec.get("expr", "") or "") if isinstance(spec, dict) else "",
        "enabled": bool(getattr(trigger, "enabled", False)),
        "created_by": str(getattr(trigger, "created_by", "") or ""),
    }


def _install_state(
    *,
    config: Any = None,
    store: Any = None,
    find_schedule: Any = None,
    schedule_payload: Any = None,
) -> dict[str, Any]:
    """Installedness + the drift between the config switch and the schedule's own flag.

    ``drift`` is reported rather than silently repaired on a READ. Criterion 10 wants disabling
    ``triage_enabled`` to retire the schedule, and the reconcile that does it is a POST — so a
    GET that quietly fixed the divergence would hide from the user that two switches had
    disagreed, and would make the read a writer.
    """
    config = _config() if config is None else config
    proactive = _proactive(config)
    enabled = bool(getattr(proactive, "triage_enabled", False))
    reader = _find_schedule if find_schedule is None else find_schedule
    trigger = reader(_trigger_store() if store is None else store)
    if trigger is None:
        return {
            "installed": False,
            "enabled": enabled,
            "schedule": None,
            "drift": False,
        }
    payload = (_schedule_payload if schedule_payload is None else schedule_payload)(
        trigger
    )
    return {
        "installed": True,
        "enabled": enabled,
        "schedule": payload,
        "drift": payload["enabled"] != enabled,
    }


def _latest_digest() -> tuple[dict | None, dict | None, list[dict]]:
    """The most recent triage run, its node output and its ledger slice.

    Returns ``(None, None, [])`` when no run exists — which the view turns into ``never_run``,
    never into an empty digest.
    """
    from gideon.automation.workflows import journal, service, store
    from gideon.cognition.proactive.surface import TRIAGE_NODE_ID, TRIAGE_WORKFLOW

    runs, _total = store.list_runs(workflow_name=TRIAGE_WORKFLOW, limit=1, offset=0)
    if not runs:
        return None, None, []
    run = runs[0].to_dict()
    run_id = str(run.get("run_id", "") or run.get("id", "") or "")
    result = service.output(run_id, TRIAGE_NODE_ID)
    output: dict | None = None
    if result.get("ok"):
        output = _decode_output(result.get("output"))
    return run, output, journal.ledger(run_id)


def _decode_output(value: Any) -> dict | None:
    """The triage node's output as a dict, whether it was stored as JSON text or as an object.

    The provider returns its summary as `ActionResult.stdout` (a JSON string), and the engine may
    hand it back either already-parsed or verbatim depending on the node's transform. Both are
    accepted; anything else is `None`, which the view reports as `never_run` rather than as a
    digest with every section empty.
    """
    import json

    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
