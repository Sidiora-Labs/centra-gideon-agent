"""Workflow fork operations and run definition/output inspection."""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from gideon.automation.workflows.failure_taxonomy import with_breaker_window
from gideon.automation.workflows.models import InstanceState, RunStatus, sibling_group


async def fork_run_checked(
    run_id: str,
    *,
    checkpoint_id: str = "",
    note: str = "",
    supervisor: Any = None,
    caller: Any = None,
    private_origin: Any = ...,
) -> dict[str, Any]:
    """Admit a fork with a live caller and original restrictions, including terminal parents."""
    from gideon.automation.workflows import ownership, private_work
    from gideon.automation.workflows.service import (
        _service_failure,
        store,
    )
    from gideon.security.approval_answer import OWNER, UNKNOWN, principal_record
    from gideon.security.durable_work import (
        _seal_origin,
        recorded_run_origin,
        verified_run_origin,
    )
    from gideon.security.session_credentials import current_work

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    proof = current_work() if private_origin is ... else private_origin
    if proof is not None:
        from gideon.security.session_credentials import credential_for, verify

        if verify(credential_for(proof.session_key), proof.session_key) is not proof:
            return _service_failure(
                "WF_FORK_SOURCE_REQUIRED",
                "the current fork source credential is no longer valid",
            )
    actor = (proof.work_actor or proof.initiator) if proof is not None else caller
    if actor is None or actor.kind == UNKNOWN:
        return _service_failure(
            "WF_FORK_SOURCE_REQUIRED",
            "an authenticated current caller must request the fork",
        )
    values = recorded_run_origin(run) if run.is_terminal else verified_run_origin(run)
    private = ownership.run_mode(run) is not ownership.MemoryMode.NORMAL
    if private:
        if values is None or (
            actor.kind != OWNER
            and (
                proof is None
                or proof.origin_session_key != values.get("origin_session_key")
                or principal_record(proof.initiator) != values.get("initiator")
                or proof.created_by_app != values.get("created_by_app", "")
            )
        ):
            return _service_failure(
                "WF_FORK_SOURCE_REQUIRED",
                "the private fork requires its authenticated owner or original source",
            )
        from gideon.extensions.apps.app_work import from_record

        app = from_record(run.extra)
        if app is not None and not app.current_tier():
            return _service_failure(
                "WF_FORK_SCOPE_REVOKED", "the original app scope is no longer admitted"
            )
        try:
            await private_work.validate_run(supervisor, run)
        except RuntimeError as error:
            return _service_failure("WF_PRIVATE_SCOPE_UNAVAILABLE", str(error))
    origin = _seal_origin({**values, "run_id": ""}) if values is not None else None
    with private_work.admitted_fork(run.id, origin):
        return fork_run(
            run_id, checkpoint_id=checkpoint_id, note=note, supervisor=supervisor
        )


def fork_run(
    run_id: str, *, checkpoint_id: str = "", note: str = "", supervisor: Any = None
) -> dict[str, Any]:
    """Branch a new run. Works on a terminal run too — forking a finished result to explore
    an alternative is the main reason to fork at all."""
    from gideon.automation.workflows.checkpoints import fork_run as do_fork
    from gideon.automation.workflows.service import (
        _ok,
        _service_failure,
        store,
    )

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if spec is None:
        return _service_failure(
            "WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec"
        )
    try:
        result = do_fork(
            run,
            spec,
            store.read_state(run_id),
            checkpoint_id=checkpoint_id,
            note=note,
            now=_now(),
        )
    except ValueError as exc:
        return _service_failure("WF_FORK_FAILED", str(exc))
    return _ok(**result.to_dict())


def audit(*, dry_run: bool = True, supervisor: Any = None) -> dict[str, Any]:
    from gideon.automation.workflows.audit import audit as do_audit
    from gideon.automation.workflows.service import (
        _ok,
    )

    return _ok(**do_audit(dry_run=dry_run, supervisor=supervisor).to_dict())


def manifest() -> dict[str, Any]:
    """The node taxonomy, pipes and op catalog, GENERATED from the real registries.

    Generated, never hand-written: a hand-maintained catalog drifts from the code the
    moment either changes, and an author following a stale catalog writes specs the engine
    rejects. A CI drift test can compare this against the code because both come from the
    same enums.
    """
    from gideon.automation.workflows import loop_aliases
    from gideon.automation.workflows.bindings import PIPES
    from gideon.automation.workflows.models import (
        CONTAINER_KINDS,
        GateKind,
        ItemErrorPolicy,
        JoinMode,
        LoopMode,
        NodeKind,
        lane_for,
    )
    from gideon.automation.workflows.service import (
        RunStatus,
        _ok,
        blocks,
        macros,
        mutations,
    )

    return _ok(
        spec_semver=__import__(
            "gideon.automation.workflows.models", fromlist=["SPEC_SEMVER"]
        ).SPEC_SEMVER,
        node_kinds=[
            {
                "kind": k.value,
                "container": k in CONTAINER_KINDS,
                "lane": lane_for(k),
            }
            for k in NodeKind
        ],
        gate_kinds=[g.value for g in GateKind],
        join_modes=[j.value for j in JoinMode],
        loop_modes=[m.value for m in LoopMode],
        item_error_policies=[p.value for p in ItemErrorPolicy],
        pipes=sorted(PIPES),
        mutation_ops=[o.value for o in mutations.OpKind],
        instance_states=[s.value for s in InstanceState],
        run_statuses=[s.value for s in RunStatus],
        macros=macros.macro_names(),
        shared_blocks=blocks.block_names(),
        loop_aliases=loop_aliases.alias_manifest(),
    )


async def _raw_def(name: str) -> Any | None:
    from gideon.automation.workflows.service import (
        defs_mod,
    )

    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def _missing_required_inputs(
    spec: dict[str, Any], provided: dict[str, Any]
) -> list[str]:
    declared = spec.get("inputs") or {}
    if not isinstance(declared, dict):
        return []
    missing: list[str] = []
    for key, meta in declared.items():
        if not isinstance(meta, dict) or not meta.get("required"):
            continue
        if key in provided or meta.get("default") is not None:
            continue
        missing.append(str(key))
    return sorted(missing)


def _with_declared_defaults(
    spec: dict[str, Any], provided: dict[str, Any]
) -> dict[str, Any]:
    """Fill in every declared input the caller omitted, using its declared default.

    Applied at RUN START, once, so the run record shows the values the run actually used — a run
    whose inputs were completed lazily at each binding would leave a record that does not explain
    its own behaviour.

    A declared input with NO default still gets a value in its declared type so a binding
    can resolve it without creating an invalid run.

    The caller's value always wins, including an explicit empty string — a user who deliberately
    cleared a field is not asking for the default back.
    """
    declared = spec.get("inputs") or {}
    if not isinstance(declared, dict):
        return provided
    out = dict(provided)
    for key, meta in declared.items():
        if key in out:
            continue
        default = meta.get("default") if isinstance(meta, dict) else None
        if default is None and isinstance(meta, dict):
            default = {
                "number": 0,
                "integer": 0,
                "boolean": False,
                "array": [],
                "object": {},
            }.get(str(meta.get("type", "") or "").lower(), "")
        out[str(key)] = default
    return out


def _coerce_declared_inputs(
    spec: dict[str, Any], provided: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    from gideon.automation.workflows.service import json

    declared = spec.get("inputs")
    if not isinstance(declared, dict):
        return dict(provided), []
    result = dict(provided)
    invalid: list[str] = []
    for name, meta in declared.items():
        if not isinstance(meta, dict) or name not in result:
            continue
        kind = str(meta.get("type", "") or "").lower()
        value = result[name]
        if kind not in {"string", "number", "integer", "boolean", "array", "object"}:
            continue
        try:
            if kind == "string":
                if not isinstance(value, str):
                    raise ValueError("expected text")
            elif kind in {"number", "integer"}:
                if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    raise ValueError("expected a numeric value")
                parsed = Decimal(value.strip()) if isinstance(value, str) else value
                if isinstance(parsed, Decimal) and not parsed.is_finite():
                    raise ValueError("expected a finite number")
                if isinstance(parsed, float) and not math.isfinite(parsed):
                    raise ValueError("expected a finite number")
                if kind == "integer":
                    if int(parsed) != parsed:
                        raise ValueError("expected a whole number")
                    value = int(parsed)
                else:
                    value = float(parsed) if isinstance(parsed, Decimal) else parsed
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("expected a finite number")
            elif kind == "boolean":
                if isinstance(value, str):
                    if value.strip().lower() not in {"true", "false"}:
                        raise ValueError("expected true or false")
                    value = value.strip().lower() == "true"
                elif not isinstance(value, bool):
                    raise ValueError("expected true or false")
            else:
                if isinstance(value, str):
                    value = json.loads(value)
                if not isinstance(value, list if kind == "array" else dict):
                    raise ValueError(f"expected a JSON {kind}")
        except (ValueError, TypeError, OverflowError, InvalidOperation) as exc:
            invalid.append(f"input {name!r} declares {kind}: {exc}")
            continue
        result[name] = value
    return result, invalid


def _nodes_of(run_id: str) -> list[dict[str, Any]]:
    from gideon.automation.workflows.service import (
        Node,
        journal_mod,
        spec_path,
        store,
        walk,
    )

    instances = store.read_state(run_id)
    spec = store.read_spec(run_id)
    ids: dict[str, str] = {}
    if spec:
        try:
            for path, node in walk(Node.from_dict(spec.get("root") or {})):
                if node.id:
                    ids[path] = node.id
        except ValueError:
            pass
    totals: dict[str, int] = {}
    for path in instances:
        group = sibling_group(path)
        totals[group] = totals.get(group, 0) + 1

    out: list[dict[str, Any]] = []
    terminal_rows: dict[str, list[dict[str, Any]]] = {}
    for record in journal_mod.journal_records(run_id):
        kind = str(record.get("kind", ""))
        if kind not in {
            journal_mod.STEP_COMPLETED,
            journal_mod.STEP_FAILED,
            journal_mod.STEP_CANCELLED,
        }:
            continue
        path = str(record.get("instance_path", "") or "")
        if not path:
            continue
        attempt = dict(record)
        attempt["attempt"] = int(
            record.get("attempt", int(record.get("retries", 0) or 0) + 1)
        )
        terminal_rows.setdefault(path, []).append(attempt)
    for path in sorted(instances):
        inst = instances[path]
        base = spec_path(path)
        failure = with_breaker_window(inst.failure)
        row: dict[str, Any] = {
            "instance_path": path,
            "node_id": ids.get(base, ""),
            "state": inst.state.value,
            "attempt": inst.attempt,
            "degraded_reason": inst.degraded_reason,
            "failure": failure.to_dict() if failure is not None else None,
            "attempts": [
                {
                    key: record.get(key)
                    for key in (
                        "attempt",
                        "kind",
                        "tokens",
                        "cost_usd",
                        "model_calls_open",
                        "model",
                        "provider",
                        "model_substituted",
                        "failure",
                    )
                    if key in record
                }
                for record in terminal_rows.get(path, [])
            ],
        }
        if inst.model_substitutions:
            row["model_substitutions"] = list(inst.model_substitutions)
        markers = list(re.finditer(r"[#@](\d+)(?=\.|$)", path))
        total = inst.item_total or totals.get(sibling_group(path), 0)
        if markers and (inst.item_total > 0 or total > 1):
            row["item_index"] = int(markers[-1].group(1))
            row["item_total"] = total
            if inst.item_label:
                row["item_label"] = inst.item_label
        out.append(row)
    return out


def _escalations(run_id: str) -> list[dict[str, Any]]:
    from gideon.automation.workflows.service import (
        journal_mod,
    )

    return [
        {
            "kind": "escalation",
            "instance_path": str(record.get("instance_path", "") or ""),
            "node_id": str(record.get("node_id", "") or ""),
            "reason": str(record.get("reason", "") or ""),
            "detail": str(record.get("detail", "") or ""),
            "attempts": list(record.get("attempts", []) or []),
        }
        for record in journal_mod.journal_records(
            run_id, kinds={journal_mod.STEP_ESCALATED}
        )
    ]


def _completion_summary(run: Any, status: RunStatus) -> str:
    """A one-line completion summary for the launching session's mirror.

    Drawn from the run's own recorded handoff summary — what the run said it produced —
    rather than fabricated, matching `controller._revise_project_overview`. A run that said
    nothing hands over nothing, so the line falls back to name + status, which is honest.
    """
    from gideon.automation.workflows.service import (
        logger,
    )

    name = getattr(run, "workflow_name", "") or "run"
    line = f"{name} → {status.value}"
    try:
        handoff = (getattr(run, "extra", {}) or {}).get("summary")
        if isinstance(handoff, str) and handoff.strip():
            line += f": {handoff.strip().splitlines()[0][:200]}"
    except Exception:
        logger.debug(
            "completion summary handoff read failed for %r", name, exc_info=True
        )
    return line


def _status_of(run_id: str) -> str:
    from gideon.automation.workflows.service import (
        store,
    )

    run = store.get(run_id)
    return run.status.value if run else "unknown"


def _now() -> str:
    from gideon.automation.workflows.service import (
        time,
    )

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
