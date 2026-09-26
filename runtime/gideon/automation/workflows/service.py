"""Workflow operations — the ONE implementation the chat tools and HTTP routes share.

Every workflow operation lives here as a plain function returning a plain dict. The chat
tools (Slice 6a) call it in-process; the REST handlers (Slice 7a) will call the same
functions and serialize the same dicts. That is deliberate: two surfaces over one engine
must not grow two behaviours, and "the tool did X but the API did Y" is the bug class this
prevents by construction.

Three rules the shape follows:

**Never raise across the boundary.** Every function returns `{"ok": bool, ...}` with a
stable `code` on failure. A tool call that raises burns the model's turn on a traceback it
cannot act on; a coded error it can read and correct.

**The supervisor is injected, never imported from a global.** A run needs a controller to
drive it, and that controller must be the one the watchdog knows about — otherwise a
restart adopts the run a second time and two writers race. Callers pass the supervisor;
tests pass a fake.

**Reads never mutate.** `status`/`output`/`observe` construct no controller and start no
run. A read that lazily started something would make polling a side-effecting act.
"""

from __future__ import annotations

import logging
import json
import math
import re
import shutil
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from gideon.automation.workflows import (
    attention,
    blocks,
)
from gideon.automation.workflows import defs as defs_mod
from gideon.automation.workflows import journal as journal_mod
from gideon.automation.workflows import (
    judge_calibration,
    macros,
    models,
    mutations,
    provisioning,
    secrets,
    store,
    template_lint,
)
from gideon.automation.workflows.models import (
    RUN_PHASES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    InstanceState,
    LifecyclePhase,
    Node,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowDef,
    WorkflowRun,
    sibling_group,
    spec_path,
    valid_name,
    walk,
)
from gideon.automation.workflows.validator import validate_spec

logger = logging.getLogger(__name__)

MIN_OBSERVE_MS = 100
MAX_OBSERVE_MS = 30_000
DEFAULT_OBSERVE_MS = 5_000
PORTED_LOOP_KINDS = frozenset({"general"})


def _service_failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A SERVICE-result failure — deliberately NOT the wire envelope.

    This layer is transport-independent: the same dict is the MCP tool result
    (``mcp_workflows.py`` returns it verbatim) and the input to
    ``workflows/handlers.py``'s ``_fail``, which translates ``code`` through
    ``_STATUS_MAP`` into an HTTP status + a ``lowercase_snake`` wire code and
    emits it via :func:`gideon.http_errors.json_error`. So ``code`` here is
    the third vocabulary (``WF_UPPER_SNAKE``), not a wire code, and this helper
    must not return a ``web.Response``. It was named ``_err`` — the same name as
    twelve Response-returning handler helpers — which made a copy-paste between
    the two layers a type error waiting to happen (PL-8).
    """
    return {"ok": False, "code": code, "message": message, **extra}


def _ok(**fields: Any) -> dict[str, Any]:
    return {"ok": True, **fields}


async def list_defs(*, tag: str = "", source: str = "") -> dict[str, Any]:
    """Every definition across every registered provider."""
    out: list[dict[str, Any]] = []
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found, _total = await provider.list_defs(limit=500)
        except Exception:
            logger.debug("workflow def provider %s failed to list", provider_name)
            continue
        for item in found:
            d = (
                item
                if isinstance(item, dict)
                else getattr(item, "to_dict", lambda: {})()
            )
            if not isinstance(d, dict) or not d.get("name"):
                continue
            if tag and tag not in (d.get("tags") or []):
                continue
            if source and str(d.get("source", "")) != source:
                continue
            out.append(
                {
                    "name": d.get("name"),
                    "description": d.get("description", ""),
                    "source": d.get("source", "user"),
                    "version": d.get("version", 1),
                    "tags": d.get("tags") or [],
                    "provider": provider_name,
                }
            )
    out.sort(key=lambda d: str(d.get("name")))
    return _ok(defs=out, total=len(out))


async def list_defs_surfacing(*, now: float = 0.0) -> dict[str, Any]:
    """The templates list WITH its surfacing state — freshness, scope, packs, route, reachability.

    `list_defs` deliberately returns a thin projection (name/description/source/version/tags/
    provider). Measured (S61b): that projection drops `metadata` entirely, so a templates list built
    on it CANNOT render a freshness gradient, a scope chip, or a surfacing toggle no matter what the
    def declares — the fields would be present on disk and invisible to every surface. This is the
    read the UX consumes.

    Cadence facts are batched here rather than looked up per def: one `list_runs` call per template
    on every list render is the shape that makes a list feel broken on a machine with history.
    """
    from gideon.automation.workflows import surfacing_channels as channels

    defs_by_name: dict[str, Any] = {}
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found, _total = await provider.list_defs(limit=500)
        except Exception:
            logger.debug("workflow def provider %s failed to list", provider_name)
            continue
        for item in found:
            name = ""
            metadata: Any = None
            if isinstance(item, dict):
                name = str(item.get("name", "") or "")
                metadata = models.DefMetadata.from_dict(item.get("metadata") or {})
            else:
                name = str(getattr(item, "name", "") or "")
                metadata = getattr(item, "metadata", None)
            if not name or metadata is None:
                continue
            defs_by_name.setdefault(name, (provider_name, metadata))

    rows: list[dict[str, Any]] = []
    doctor_entries: list[dict[str, Any]] = []
    for name, (provider_name, metadata) in sorted(defs_by_name.items()):
        cadence = channels.cadence_from_def(
            name, metadata, last_completed_at=channels.last_completed(name)
        )
        rows.append(
            {
                "name": name,
                "provider": provider_name,
                "surface_mode": metadata.surface_mode,
                "summary": metadata.summary,
                "when_to_use": metadata.when_to_use,
                "cadence_days": metadata.cadence_days,
                "escalation": metadata.escalation,
                "packs": list(metadata.packs),
                "guided": metadata.guided,
                "freshness": channels.freshness(cadence, now).value,
                "overdue": channels.overdue(cadence, now),
                "last_completed_at": cadence.last_completed_at,
                "hands_off_to": [
                    h.to_dict() for h in channels.handoffs_from_def(metadata)
                ],
            }
        )
        doctor_entries.append(channels.doctor_entry(name, metadata))

    order = {
        name: channels.sort_key(
            channels.cadence_from_def(
                name,
                meta,
                last_completed_at=next(
                    (r["last_completed_at"] for r in rows if r["name"] == name), 0.0
                ),
            ),
            now,
        )
        for name, (_prov, meta) in defs_by_name.items()
    }
    rows.sort(key=lambda r: order.get(str(r["name"]), (9, 0.0, str(r["name"]))))
    return _ok(
        defs=rows,
        total=len(rows),
        findings=[f.to_dict() for f in channels.doctor(doctor_entries)],
    )


async def get_def(name: str) -> dict[str, Any]:
    """One definition in full, with secret values stripped to `_has*` flags.

    Stripped on the way OUT, always: a def read is rendered in a UI and echoed into a chat
    turn, and a credential that reaches either is a credential leaked to both (WF2-R14).
    """
    if not name:
        return _service_failure("WF_DEF_NAME_REQUIRED", "a definition name is required")
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            logger.debug("workflow def provider %s failed on %s", provider_name, name)
            continue
        if found is None:
            continue
        raw = (
            found
            if isinstance(found, dict)
            else getattr(found, "to_dict", lambda: {})()
        )
        return _ok(
            definition=secrets.strip_secrets(raw),
            provider=provider_name,
            default_eligibility=_default_eligibility(name),
        )
    return _service_failure(
        "WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}"
    )


async def extract_inputs(
    name: str,
    candidates: Any,
    *,
    declined: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Extract launch inputs for a definition from eligible conversation values."""
    definition = await _raw_def(name)
    if definition is None:
        return _service_failure(
            "WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}"
        )
    spec = definition if isinstance(definition, dict) else definition.to_dict()
    from gideon.automation.workflows import contracts

    return _ok(
        name=name,
        **contracts.extract_inputs(spec, candidates, declined=declined).to_dict(),
    )


def _default_eligibility(name: str) -> dict[str, Any]:
    """R6a: may this template become its kind's default? (LOOPS-EVOLUTION R6 criterion 1).

    Reads every `judge_verdict` this template's runs recorded to the ledger and asks the
    nodding-loop detector. A gate that has never rejected across enough real runs blocks the
    template from becoming a default and surfaces as a warning badge. Read-only projection over
    the ledger — no model call, no separate store.
    """
    records: list[judge_calibration.VerdictRecord] = []
    runs, _total = store.list_runs(workflow_name=name, limit=200)
    for run in runs:
        run_id = getattr(run, "id", "")
        if not run_id:
            continue
        entries = journal_mod.ledger(run_id, kinds={journal_mod.JUDGE_VERDICT})
        records.extend(judge_calibration.verdicts_from_journal(entries))
    allowed, reason = judge_calibration.may_become_default(records, template=name)
    return {"may_become_default": allowed, "reason": reason, "verdicts": len(records)}


async def _reserved_name_provider(name: str) -> str:
    """The read-only provider already serving ``name``, or ``""``.

    Provider-agnostic on purpose: it asks every registered provider whether it is `readonly` and
    whether it holds the name, rather than naming `bundled`. A pack provider (`"research-pack"`)
    is read-only for the same reason and reserves its names on the same terms, so a rule written
    against one provider id would have to be rewritten for the second.

    Fails OPEN — a provider that raises is treated as not holding the name, with a warning. The
    protocol says `get_def` must not raise on a miss, so this is a contract violation rather than
    an expected path; the choice is between one possible shadow and a broken third-party pack
    blocking ALL authoring, and the latter is worse. This is a name-collision guard, not a
    security boundary, which is why fail-open is the right direction here and is not in
    `apps/permissions.py`.
    """
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None or not provider.readonly:
            continue
        try:
            if await provider.get_def(name) is not None:
                return provider.name
        except Exception:  # noqa: BLE001 - see the fail-open note above
            logger.warning(
                "provider %s raised while checking whether %s is reserved; treating as free",
                provider_name,
                name,
                exc_info=True,
            )
    return ""


async def author_def(
    *,
    name: str,
    root: dict[str, Any],
    description: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    save: bool = True,
    provenance: str = "chat",
    strict: bool = True,
    workspace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a spec and (optionally) save it.

    `save=False` is a real dry run: it validates and returns the issues WITHOUT writing, so
    an author can iterate before committing anything. Validating only at save time would
    mean every failed attempt leaves a broken def on disk.

    `strict` rejects on WARNINGS too. Authoring is exactly when a warning is cheap to fix,
    and a template that ships with a known smell propagates it to every run.

    `metadata` is the def's declared surfacing/matching block. Measured (S61b): there was NO write
    path for it — the parameter did not exist, so every `DefMetadata` field (including the
    `surface_mode`, `cadence_days` and `packs` the surfacing channels read) could be loaded from
    disk and never SET through the API. A field with a read path and no write path is a field only a
    hand-edited file can use, which is the config round-trip contract's exact failure.
    """
    if not valid_name(name):
        return _service_failure(
            "WF_DEF_NAME_INVALID",
            f"{name!r} is not a valid name — use lowercase letters, digits and hyphens "
            "(it becomes a directory)",
        )
    reserved_by = await _reserved_name_provider(name)
    if reserved_by:
        return _service_failure(
            "WF_DEF_NAME_RESERVED",
            f"{name!r} is the name of a read-only {reserved_by} template. Save your version "
            "under a different name — a copy under your own name is never touched by an "
            "upgrade, while a shadow of a bundled name would be ignored at run time.",
            provider=reserved_by,
        )
    spec = {
        "name": name,
        "description": description,
        "root": root or {},
        "inputs": inputs or {},
        "tags": tags or [],
        "provenance": provenance,
    }
    if isinstance(workspace, dict) and workspace:
        spec[provisioning.WORKSPACE_KEY] = dict(workspace)
    if metadata:
        spec["metadata"] = models.DefMetadata.from_dict(metadata).to_dict()

    try:
        spec = macros.expand_spec(spec)
        spec = blocks.resolve_spec(spec)
    except (macros.MacroError, blocks.BlockError) as exc:
        return _service_failure("WF_DEF_MACRO_INVALID", str(exc), repromptable=True)
    expanded_root = spec.get("root")
    root = expanded_root if isinstance(expanded_root, dict) else root

    inline = secrets.find_inline_secrets(spec)
    if inline:
        return _service_failure(
            "WF_DEF_INLINE_SECRET",
            "the spec contains literal credentials — use {{secret:KEY}} instead",
            findings=[f.to_dict() for f in inline],
        )

    result = validate_spec(spec, strict=strict)
    body = {
        "valid": result.ok,
        "issues": [i.to_dict() for i in result.issues],
        "levels": result.levels,
        "lint": template_lint.lint_template(spec).to_dict(),
    }
    if not result.ok:
        return _service_failure(
            "WF_DEF_INVALID", "the spec did not validate", **body, repromptable=True
        )
    if not save:
        return _ok(saved=False, dry_run=True, **body)

    dry_run_report: dict[str, Any] | None = None
    if provenance == "chat":
        from gideon.automation.workflows.preflight import preflight as run_preflight

        dry_run_report = run_preflight(spec).to_dict()

    writable = [
        p
        for p in (defs_mod.get_provider(n) for n in defs_mod.list_providers())
        if p is not None and not p.readonly
    ]
    if not writable:
        return _service_failure(
            "WF_DEF_NO_WRITABLE_PROVIDER",
            "no writable workflow definition provider is registered",
        )
    try:
        saved = await writable[0].save_def(**spec)
    except Exception as exc:
        return _service_failure(
            "WF_DEF_SAVE_FAILED", f"could not save the definition: {exc}"
        )
    raw = saved if isinstance(saved, dict) else getattr(saved, "to_dict", lambda: {})()
    return _ok(
        saved=True,
        definition=secrets.strip_secrets(raw),
        provenance=provenance,
        preflight=dry_run_report,
        **body,
    )


async def set_a2a_published(name: str, published: bool) -> dict[str, Any]:
    """Flip one template's ``metadata.a2a_published`` (EXTERNAL-ACCESS §5, EA-8).

    A DEDICATED write path rather than routing the toggle through :func:`author_def`, and the
    reason is a data-loss hazard rather than taste. The detail UI holds the def it got from
    :func:`get_def`, which is the STRIPPED read — credential values are replaced with ``_has*``
    flags. Handing that copy back to ``author_def`` would persist the stripped form and destroy
    the template's real credential bindings, so a one-bool toggle would silently break every
    node that resolved a secret. This function mutates the RAW stored def instead and never
    round-trips through the client.

    It also declines to re-validate: publishing does not change the graph, and a template that
    was savable when it was authored must not become unpublishable because the validator grew a
    new warning since.
    """
    if not name:
        return _service_failure("WF_DEF_NAME_REQUIRED", "a definition name is required")
    definition = await _raw_def(name)
    if definition is None:
        return _service_failure(
            "WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}"
        )
    spec = definition if isinstance(definition, dict) else definition.to_dict()
    spec = dict(spec)
    metadata = dict(spec.get("metadata") or {})
    metadata["a2a_published"] = bool(published)
    spec["metadata"] = metadata
    writable = [
        p
        for p in (defs_mod.get_provider(n) for n in defs_mod.list_providers())
        if p is not None and not p.readonly
    ]
    if not writable:
        return _service_failure(
            "WF_DEF_NO_WRITABLE_PROVIDER",
            "no writable workflow definition provider is registered",
        )
    try:
        saved = await writable[0].save_def(**spec)
    except Exception as exc:
        return _service_failure(
            "WF_DEF_SAVE_FAILED", f"could not save the definition: {exc}"
        )
    raw = saved if isinstance(saved, dict) else getattr(saved, "to_dict", lambda: {})()
    return _ok(
        name=name,
        a2a_published=bool((raw.get("metadata") or {}).get("a2a_published") is True),
        definition=secrets.strip_secrets(raw),
    )


async def delete_def(name: str) -> dict[str, Any]:
    if not name:
        return _service_failure("WF_DEF_NAME_REQUIRED", "a definition name is required")
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None or provider.readonly:
            continue
        try:
            if await provider.delete_def(name):
                return _ok(deleted=True, name=name, provider=provider_name)
        except Exception as exc:
            return _service_failure(
                "WF_DEF_DELETE_FAILED", f"could not delete {name!r}: {exc}"
            )
    return _service_failure(
        "WF_DEF_NOT_FOUND", f"no writable definition named {name!r}"
    )


def _origin_metadata(session_key: str) -> dict[str, Any]:
    """The launching session's durable metadata head, for memory-mode inheritance.

    Read here rather than inside `inherit_mode` because that module is pure-by-design (no I/O, so
    it stays testable without a home). A missing key or an unreadable log yields `{}`, and
    `inherit_mode` then falls back to the process-global registry — the same both-sources order
    `session_search.is_restricted` uses so a restart cannot silently un-mark an in-flight run.
    """
    if not session_key:
        return {}
    try:
        from gideon.cognition.history import ConversationLog

        return ConversationLog().get_metadata(session_key) or {}
    except Exception:
        logger.debug("origin metadata read failed for %r", session_key, exc_info=True)
        return {}


async def start_run(
    *,
    name: str,
    inputs: dict[str, Any] | None = None,
    mode: str = "background",
    supervisor: Any = None,
    origin_kind: OriginKind = OriginKind.CHAT,
    session_key: str = "",
    project_id: str = "",
    idempotency_key: str = "",
    blocking_timeout: float = 0.0,
    skip_preflight: bool = False,
) -> dict[str, Any]:
    """Instantiate a def and start driving it.

    A caller idempotency key returns the EXISTING run rather than minting a second one — a
    retried tool call is a retry, not a new request (WF2-R1).

    Preflight runs first unless explicitly skipped: a missing credential caught here costs
    nothing, and caught at node 7 has already paid for six nodes of model calls.
    """
    from gideon.automation.workflows.effects import START_DEDUPE

    if idempotency_key:
        existing = START_DEDUPE.lookup(idempotency_key)
        if existing:
            return _ok(run_id=existing, deduped=True, status=_status_of(existing))

    found = await get_def(name)
    if not found.get("ok"):
        return found
    definition = await _raw_def(name)
    if definition is None:
        return _service_failure(
            "WF_DEF_NOT_FOUND", f"no workflow definition named {name!r}"
        )

    spec = definition if isinstance(definition, dict) else definition.to_dict()
    missing = _missing_required_inputs(spec, inputs or {})
    if missing:
        return _service_failure(
            "WF_RUN_MISSING_INPUTS",
            f"missing required input(s): {', '.join(missing)}",
            missing=missing,
        )
    inputs = _with_declared_defaults(spec, inputs or {})
    inputs, invalid = _coerce_declared_inputs(spec, inputs)
    if invalid:
        return _service_failure(
            "WF_RUN_INVALID_INPUT",
            "; ".join(invalid),
            invalid_inputs=invalid,
        )

    if not skip_preflight:
        from gideon.automation.workflows.preflight import preflight as run_preflight

        checks = run_preflight(spec)
        if not checks.ok:
            return _service_failure(
                "WF_RUN_PREFLIGHT_FAILED",
                "the run cannot start: " + "; ".join(f.message for f in checks.errors),
                preflight=checks.to_dict(),
            )
        if checks.warnings:
            logger.info(
                "workflow %s: starting with %d unverifiable requirement(s)",
                name,
                len(checks.warnings),
            )

    from gideon.automation.workflows import ownership

    inherited = ownership.inherit_mode(
        session_key, origin_metadata=_origin_metadata(session_key)
    )
    run_extra: dict[str, Any] = {}
    if inherited is not ownership.MemoryMode.NORMAL:
        run_extra = ownership.stamp_run_mode({}, inherited)

    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            status=RunStatus.DRAFT,
            spec_version=int(spec.get("version", 1) or 1),
            inputs=dict(inputs or {}),
            mode=mode if mode in ("blocking", "background") else "background",
            project_id=project_id,
            origin=RunOrigin(kind=origin_kind, session_key=session_key),
            extra=run_extra,
        )
    )
    store.write_spec(run.id, spec)
    if idempotency_key:
        START_DEDUPE.remember(idempotency_key, run.id)

    if supervisor is None:
        return _service_failure(
            "WF_NO_SUPERVISOR",
            "the workflow supervisor is unavailable, so the run was created but not started",
            run_id=run.id,
        )
    try:
        controller = await supervisor.launch(run, spec)
    except Exception as exc:
        return _service_failure(
            "WF_RUN_LAUNCH_FAILED", f"could not start the run: {exc}", run_id=run.id
        )

    if run.mode == "blocking":
        status = await controller.wait_for_terminal(
            timeout=blocking_timeout or 0.0,
            on_progress=lambda snap: controller._publish("workflow_progress", snap),
        )
        body = _ok(
            run_id=run.id, status=status.value, blocking=True, nodes=_nodes_of(run.id)
        )
        if status == RunStatus.NEEDS_INPUT:
            from gideon.automation.workflows.human_input import list_continuations

            pending = list_continuations(run.id)
            body["needs_input"] = [
                {
                    "node_id": c.node_id,
                    "resume_token": c.token,
                    "ask": c.ask,
                    "handoff": c.handoff,
                }
                for c in pending
            ]
        else:
            note = ownership.announcement(
                origin_key=run.origin.session_key,
                text=_completion_summary(store.get(run.id) or run, status),
                mode=inherited,
            )
            body["announcement"] = note.to_dict()
        return body
    return _ok(run_id=run.id, status=RunStatus.RUNNING.value, blocking=False)


async def start_kind_run(
    *,
    kind: str,
    inputs: dict[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    from gideon.automation.workflows.bundled_defs import register_bundled_provider
    from gideon.automation.workflows.loop_aliases import resolve_kind

    normalized = str(kind or "").strip().lower()
    if normalized not in PORTED_LOOP_KINDS:
        return _service_failure(
            "WF_KIND_NOT_SUPPORTED",
            f"loop kind {kind!r} has no bundled convergence launch",
        )
    register_bundled_provider()
    return await start_run(name=resolve_kind(normalized), inputs=inputs, **options)


async def start_draft(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Launch an existing prelaunch run after its draft controls have been reviewed."""
    run = store.get(run_id)
    if run is None:
        return _run_not_found(run_id)
    if RUN_PHASES[run.status] is not LifecyclePhase.PRELAUNCH:
        return _service_failure(
            "WF_RUN_NOT_PRELAUNCH", f"run is already {run.status.value}"
        )
    if supervisor is None:
        return _service_failure(
            "WF_NO_SUPERVISOR", "the workflow supervisor is unavailable"
        )
    spec = store.read_spec(run_id)
    if not isinstance(spec, dict):
        return _service_failure(
            "WF_RUN_NO_SPEC", "the run spec is missing or unreadable"
        )
    try:
        await supervisor.launch(run, spec)
    except Exception as exc:
        return _service_failure(
            "WF_RUN_LAUNCH_FAILED", f"could not start the run: {exc}"
        )
    return _ok(run_id=run_id, status=RunStatus.RUNNING.value)


def status(run_id: str) -> dict[str, Any]:
    """Run status plus node-level progress. Pure read — constructs no controller."""
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    from gideon.automation.workflows.round_protocol import read_rounds

    return _ok(
        run_id=run.id,
        workflow=run.workflow_name,
        status=run.status.value,
        spec_version=run.spec_version,
        error=run.error_message,
        attention=run.attention,
        tokens=run.total_tokens,
        elapsed_secs=run.elapsed_seconds,
        project_id=run.project_id,
        policy_overrides=run.policy_overrides,
        budget=run.budget.to_dict(),
        round_handoff=(run.extra or {}).get("round_handoff") or {},
        round_interrupted=bool((run.extra or {}).get("round_interrupted")),
        rounds=read_rounds(run_id),
        nodes=_nodes_of(run_id),
    )


async def resume_interrupted_round(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Explicitly re-adopt one interrupted round run at its persisted frontier."""
    from gideon.automation.workflows.round_protocol import has_round_protocol

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if not has_round_protocol(spec) or run.status != RunStatus.RUNNING or not (run.extra or {}).get("round_interrupted"):
        return _service_failure("WF_RUN_NOT_INTERRUPTED", "this round run is not awaiting explicit resume")
    if supervisor is None:
        return _service_failure("WF_NO_SUPERVISOR", "the workflow supervisor is unavailable")
    if supervisor.controller(run_id) is not None:
        return _service_failure("WF_RUN_ALREADY_LIVE", "this round run already has a live controller")
    run.extra.pop("round_interrupted", None)
    store.save(run)
    try:
        await supervisor.launch(run, spec)
    except Exception as exc:
        run.extra["round_interrupted"] = True
        store.save(run)
        return _service_failure("WF_RUN_LAUNCH_FAILED", f"could not resume the round run: {exc}")
    return _ok(run_id=run_id, resumed=True, status=RunStatus.RUNNING.value)


def extend_round_budget(run_id: str, limits: dict, *, supervisor: Any = None) -> dict[str, Any]:
    """Raise a paused round run's own persisted soft cap before ordinary resume."""
    import math
    from gideon.automation.workflows.round_protocol import has_round_protocol

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status != RunStatus.PAUSED or not has_round_protocol(store.read_spec(run_id)):
        return _service_failure("WF_RUN_NOT_PAUSED_ROUND", "only a paused round run can extend its budget")
    try:
        tokens = int(limits.get("max_tokens", run.budget.max_tokens))
        cost = float(limits.get("max_cost", run.budget.max_cost))
    except (TypeError, ValueError):
        return _service_failure("WF_BUDGET_INVALID", "budget limits must be numeric")
    def raised(old: float, new: float) -> bool:
        return old > 0 and (new == 0 or new > old)

    def lowered(old: float, new: float) -> bool:
        return (old == 0 and new > 0) or (old > 0 and 0 < new < old)

    if (not math.isfinite(cost) or tokens < 0 or cost < 0
            or lowered(run.budget.max_tokens, tokens) or lowered(run.budget.max_cost, cost)
            or not (raised(run.budget.max_tokens, tokens) or raised(run.budget.max_cost, cost))):
        return _service_failure("WF_BUDGET_INVALID", "raise at least one budget limit without lowering another")
    run.budget.max_tokens = tokens
    run.budget.max_cost = cost
    controller = supervisor.controller(run_id) if supervisor is not None else None
    if controller is not None:
        controller.run.budget.max_tokens = tokens
        controller.run.budget.max_cost = cost
    store.save(run)
    return resume_run(run_id, supervisor=supervisor)


def set_policy_overrides(run_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """Replace a run's sparse ``SupervisorPolicy`` overlay (PP-16 seam 4f).

    REPLACE semantics — the store's contract: the dict IS the new overlay, so ``{}``
    clears every override and the run falls back to its kind/template defaults.

    PRE-LAUNCH ONLY, gated on the run's lifecycle PHASE (``RUN_PHASES[...] is
    LifecyclePhase.PRELAUNCH``) rather than the literal ``DRAFT`` status, so a future
    prelaunch status inherits the gate without this function changing. The two reasons
    the gate is FORCED (a write-write race with the engine's ``_save_run``, and parity
    with the loop side's launch freeze) are recorded on the HTTP route,
    :func:`gideon.automation.workflows.handlers.api_run_policy_overrides`.

    The store's strict unknown-key refusal surfaces here as ``WF_POLICY_KEY_UNKNOWN``
    carrying the offending keys AND the overridable set — a typo'd knob must name what
    was wrong and what would have been right, or the user retries blind.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if RUN_PHASES[run.status] is not LifecyclePhase.PRELAUNCH:
        return _service_failure(
            "WF_RUN_NOT_PRELAUNCH",
            f"run {run_id!r} has launched ({run.status.value}) and its policy overlay is "
            "frozen — the engine's own saves would silently revert a live edit. "
            "Edit the overrides before launch.",
        )
    try:
        updated = store.set_policy_overrides(run_id, dict(overrides))
    except ValueError:
        from gideon.automation.workflows.supervisor_policy import (
            OVERRIDABLE_POLICY_KEYS,
        )

        unknown = sorted(set(overrides) - OVERRIDABLE_POLICY_KEYS)
        return _service_failure(
            "WF_POLICY_KEY_UNKNOWN",
            f"unknown policy override key(s) {unknown} — "
            f"the overridable set is {sorted(OVERRIDABLE_POLICY_KEYS)}",
            unknown_keys=unknown,
            overridable=sorted(OVERRIDABLE_POLICY_KEYS),
        )
    if updated is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    return _ok(
        run_id=updated.id,
        status=updated.status.value,
        policy_overrides=updated.policy_overrides,
    )


def output(run_id: str, node_id: str) -> dict[str, Any]:
    """One node's stored output.

    Reads the LAST instance for a node id: a `foreach` body produces many, and returning
    the first would silently hand back item 0's answer for the whole fan-out.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if spec is None:
        return _service_failure(
            "WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec"
        )
    try:
        root = Node.from_dict(spec.get("root") or {})
    except ValueError as exc:
        return _service_failure("WF_RUN_BAD_SPEC", f"unreadable spec: {exc}")
    paths = [p for p, node in walk(root) if node.id == node_id]
    if not paths:
        return _service_failure(
            "WF_NODE_NOT_FOUND", f"no node {node_id!r} in this run's spec"
        )
    instances = store.read_state(run_id)
    matched = [p for p in instances if spec_path(p) in paths]
    if not matched:
        return _service_failure(
            "WF_NODE_NOT_RUN", f"node {node_id!r} has not produced an output yet"
        )
    target = sorted(matched)[-1]
    return _ok(
        run_id=run_id,
        node_id=node_id,
        instance_path=target,
        state=instances[target].state.value,
        output=store.read_output(run_id, target),
    )


def inspect_node(run_id: str, node_id: str) -> dict[str, Any]:
    """The §5 reconstructability set for one terminal node (WF2-A2).

    Read-only forensics over data the controller already persisted: from this payload alone
    a reader can see what a node *saw* (`resolved_prompt` + `resolved_inputs`), what it
    *produced* (`output`, or an `artifact_ref` when the value was offloaded), how many tries
    it took (`attempts`), the ledger slice that records the trajectory (`ledger_events`),
    and whether the output was served from the resume cache rather than a fresh run
    (`cached`). The acceptance bar §5 states is that prompt → tools → output is
    reconstructable from these events alone; this is the surface that exposes it.

    Never raises across the boundary — like every function here it returns
    `{"ok": bool, ...}`. Three distinct failures, because a caller renders them differently:
    an unknown run/node is a 404 (nothing to show), a node that exists but has not reached a
    terminal state is a 409 (`WF_NODE_NOT_TERMINAL` — retry as the run advances), and neither
    is a server fault.

    SECRETS: this returns the persisted values VERBATIM — the resolved prompt is stored raw
    by the controller (`_store_prompt` writes through `store.write_output`, which does NOT
    redact), so this dict is NOT safe to emit as-is. Redaction is the HTTP surface's job
    (WF2-A2 secrets contract); keeping the read un-redacted mirrors `output()`/`status()`,
    which also hand back stored state verbatim to their one in-process caller.
    """
    from gideon.automation.workflows.bindings import node_deps

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    spec = store.read_spec(run_id)
    if spec is None:
        return _service_failure(
            "WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec"
        )
    try:
        root = Node.from_dict(spec.get("root") or {})
    except ValueError as exc:
        return _service_failure("WF_RUN_BAD_SPEC", f"unreadable spec: {exc}")

    node_by_path = dict(walk(root))
    id_paths = [p for p, node in node_by_path.items() if node.id == node_id]
    if not id_paths:
        return _service_failure(
            "WF_NODE_NOT_FOUND", f"no node {node_id!r} in this run's spec"
        )

    instances = store.read_state(run_id)
    matched = [p for p in instances if spec_path(p) in id_paths]
    if not matched:
        return _service_failure(
            "WF_NODE_NOT_RUN", f"node {node_id!r} has not produced an output yet"
        )
    target = sorted(matched)[-1]
    inst = instances[target]
    if inst.state not in TERMINAL_STATES:
        return _service_failure(
            "WF_NODE_NOT_TERMINAL",
            f"node {node_id!r} is {inst.state.value}, not terminal — nothing to reconstruct yet",
        )

    base = spec_path(target)
    node = node_by_path.get(base)

    node_events = [
        e for e in journal_mod.ledger(run_id) if e.get("instance_path") == target
    ]

    prompt_ref = ""
    for e in node_events:
        if e.get("kind") == journal_mod.STEP_COMPLETED and e.get("resolved_prompt_ref"):
            prompt_ref = str(e["resolved_prompt_ref"])
            break
    stored_prompt = store.read_output(run_id, f"{target}::prompt")
    resolved_prompt: Any
    if isinstance(stored_prompt, str) and stored_prompt:
        resolved_prompt = stored_prompt
    elif prompt_ref:
        resolved_prompt = {"ref": prompt_ref}
    else:
        resolved_prompt = ""

    id_to_base: dict[str, str] = {}
    for p, n in node_by_path.items():
        if n.id:
            id_to_base.setdefault(n.id, p)
    resolved_inputs: dict[str, Any] = {}
    for dep in sorted(node_deps(node.config or {}) if node else set()):
        dep_base = id_to_base.get(dep)
        dep_matches = (
            [ip for ip in instances if spec_path(ip) == dep_base] if dep_base else []
        )
        resolved_inputs[dep] = (
            store.read_output(run_id, sorted(dep_matches)[-1]) if dep_matches else None
        )

    output_ref = inst.output_ref or ""
    raw_output = store.read_output(run_id, target)
    if output_ref and not output_ref.startswith("outputs/"):
        output_field: Any = {"artifact_ref": output_ref}
    elif journal_mod.is_binary_payload(raw_output) or _serialized_bytes(raw_output) > (
        journal_mod.MAX_INLINE_OUTPUT_BYTES
    ):
        output_field = {"artifact_ref": output_ref or target}
    else:
        output_field = raw_output

    return _ok(
        run_id=run_id,
        node_id=node_id,
        instance_path=target,
        state=inst.state.value,
        resolved_prompt=resolved_prompt,
        resolved_inputs=resolved_inputs,
        output=output_field,
        attempts=[e for e in node_events if e.get("kind") == journal_mod.STEP_ATTEMPT],
        ledger_events=node_events,
        cached=any(e.get("kind") == journal_mod.STEP_CACHED for e in node_events),
    )


def _serialized_bytes(value: Any) -> int:
    """Byte size of a value's canonical JSON — the same boundary the journal spills at, so
    the inspect view offloads exactly what the journal would have."""
    import json

    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


async def observe(run_id: str, duration_ms: int = DEFAULT_OBSERVE_MS) -> dict[str, Any]:
    """Watch a run for a bounded window and return what changed (WF2-R11).

    Cheaper and safer than a status-polling loop in chat: one call, one clamped wait, a
    timestamped delta. The clamp is the point — an unbounded subscribe in a chat turn is a
    hang where the model waits, the user waits, and nothing explains why.

    Returns as soon as the run goes terminal rather than burning the whole window on a run
    that already finished.
    """
    import asyncio

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    window = max(
        MIN_OBSERVE_MS, min(int(duration_ms or DEFAULT_OBSERVE_MS), MAX_OBSERVE_MS)
    )
    before = {p: i.state.value for p, i in store.read_state(run_id).items()}
    baseline = len(journal_mod.ledger(run_id))
    deadline = time.monotonic() + (window / 1000.0)

    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        current = store.get(run_id)
        if current is not None and current.status in TERMINAL_RUN_STATUSES:
            break

    after = {p: i.state.value for p, i in store.read_state(run_id).items()}
    changed = [
        {"instance_path": p, "from": before.get(p, "pending"), "to": s}
        for p, s in sorted(after.items())
        if before.get(p) != s
    ]
    final = store.get(run_id)
    return _ok(
        run_id=run_id,
        window_ms=window,
        clamped=window != int(duration_ms or DEFAULT_OBSERVE_MS),
        status=final.status.value if final else "unknown",
        changed=changed,
        events=journal_mod.ledger(run_id)[baseline:],
    )


def _live(run_id: str, supervisor: Any) -> Any | None:
    if supervisor is None:
        return None
    getter = getattr(supervisor, "controller", None)
    return getter(run_id) if callable(getter) else None


def _run_not_found(run_id: str) -> dict[str, Any]:
    """The one 404 for a run-control verb.

    Existence is the FIRST question every verb has to ask, and the ones that forgot it did not fail
    quietly — they answered confidently about a run that was not there:

    * `confirm {verb: skip|quit}` returned **200** `{"resumed": false, "still_pending": true}` for a
      nonexistent id, because the non-resuming verbs early-returned before any `store.get`. Only
      approve/reject reached `resume_run`, which is where the 404 lived. A tool firing skip at a
      typo'd or already-deleted run was told it had worked.
    * `rewind` / `run_from` / `edit` returned **409 run_not_live** with "resume the run before
      rewind" — remediation for a run that cannot be resumed because there is nothing to resume.
      They asked `_live()` first, and a nonexistent run has no controller either.
    * `preview_edit` returned `WF_RUN_NO_SPEC`, which `_STATUS_MAP` translates to a **500
      spec_unreadable** — a typo'd id reporting a server fault. Found by
      `tests/test_workflows_run_control_guard.py`, not reported.

    All issue 765. The same missing precheck is behind issue 679, where `resume` answered 200 on a
    terminal run *and wrote to the finished run's `extra` on the way through*.

    This line appeared FOURTEEN times, verbatim, which is why three paths could omit it without
    looking odd — there was no single thing to be missing. A builder rather than a
    resolve-and-return helper so `store.get`'s exact type survives at each call site.
    """
    return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")


def edit_run(
    run_id: str,
    ops: list[dict[str, Any]],
    *,
    supervisor: Any = None,
    expect_version: int | None = None,
    confirm_cascade: bool = False,
    actor: str = "chat",
) -> dict[str, Any]:
    """Queue a mutation batch on a live run.

    Requires a LIVE controller: mutation is only safe at the controller's drain point, and
    editing a run nobody is driving would write state with no one to apply it (WF2-R10).
    """
    if store.get(run_id) is None:
        return _run_not_found(run_id)
    controller = _live(run_id, supervisor)
    if controller is None:
        return _service_failure(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller — only a running workflow can be edited",
        )
    body = controller.submit_mutation(
        ops, actor=actor, confirm=confirm_cascade, expect_version=expect_version
    )
    body.setdefault("run_id", run_id)
    return body


def preview_edit(run_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """The cascade preview WITHOUT queueing anything — a pure what-if.

    Available on a run with no live controller too, so a user can see what an edit would
    cost before deciding to resume the run and apply it.
    """
    if store.get(run_id) is None:
        return _run_not_found(run_id)
    spec = store.read_spec(run_id)
    if spec is None:
        return _service_failure(
            "WF_RUN_NO_SPEC", f"run {run_id!r} has no readable spec"
        )
    from gideon.automation.workflows.effects import effect_history

    result = mutations.prepare_batch(
        ops, spec, store.read_state(run_id), effects=effect_history(run_id)
    )
    return {**result.to_dict(), "run_id": run_id, "queued": False}


def cancel_run(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Record a STICKY cancel intent.

    Written to disk rather than applied in memory, so a cancel issued while the gateway is
    down is still honoured on restart. The controller (or the watchdog) writes the terminal
    status — a handler must never do it (WF2-R10).
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        return _service_failure(
            "WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}"
        )
    store.request_cancel(run_id)
    controller = _live(run_id, supervisor)
    if controller is not None:
        controller.request_cancel()
    return _ok(run_id=run_id, cancel_requested=True)


async def delete_run(
    run_id: str, *, supervisor: Any = None, keep_open: bool = False
) -> dict[str, Any]:
    """Delete a TERMINAL run and its artifacts, tearing its workspace down FIRST.

    Refused while a run can still move. Deleting a live run would leave its controller writing
    journal entries and terminal status to a row that no longer exists — the single-writer
    discipline (WF2-R10) assumes the row outlives the writer. Cancel first, then delete: two
    deliberate steps for two genuinely different intents.

    Removes the run DIRECTORY as well as the row. A row-only delete would leave the journal,
    outputs and continuations on disk forever, invisible to every surface — the run would look
    gone while still costing the disk and still holding a live resume token.

    ASYNC because teardown runs a subprocess (WORK-CONTAINERS §4.1), and it must run BEFORE the
    directory goes away — that is the whole reason teardown exists. A scratch workspace lives
    UNDER the run dir, so the `rmtree` below would take it out; running teardown after that would
    execute `docker compose down` against a path that no longer holds the compose file. The one
    caller (`handlers.api_run_delete`) is already async.

    `keep_open` is the §4.1 override for when the workspace IS the deliverable: the run row and
    directory go, the workspace stays. Teardown still runs — keeping the directory is not keeping
    the processes.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status not in TERMINAL_RUN_STATUSES:
        return _service_failure(
            "WF_RUN_NOT_TERMINAL",
            f"run is {run.status.value}; cancel it before deleting",
            status=run.status.value,
        )
    controller = _live(run_id, supervisor)
    if controller is not None and supervisor is not None:
        try:
            supervisor.forget(run_id)
        except Exception:
            logger.debug(
                "could not unregister the controller for %s", run_id, exc_info=True
            )

    target = store.run_dir(run_id).resolve()
    root = store.runs_root().resolve()
    if root not in target.parents:
        return _service_failure(
            "WF_RUN_DELETE_REFUSED", "refusing to delete a path outside the runs root"
        )
    torn = await teardown_workspace(run, reason="delete", keep_open=keep_open)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    attention.resolve_run_items(getattr(supervisor, "_state", None), run_id)
    deleted = store.delete(run_id)
    return _ok(run_id=run_id, deleted=deleted, teardown=torn.to_dict())


async def teardown_workspace(
    run: Any, *, reason: str, keep_open: bool = False, runner: Any = None
) -> Any:
    """Tear a run's workspace down before its directory is deleted (WORK-CONTAINERS §4.1).

    The shared performer for BOTH deletion paths — the explicit delete here and retention expiry
    in `watchdog.prune_runs`. One function rather than two call sites doing the same thing,
    because the two would eventually disagree about the order, and the order IS the contract.

    Gated by `workflows.workspace_teardown_on_expiry`. That knob has a real reader precisely
    here: when it is off, the workspace is removed without running its command. Off is the
    escape hatch for a teardown command that is itself the problem (one that hangs, or one whose
    author got it wrong), which is a real situation — but it defaults ON, because leaving a
    `docker compose` up after the run that started it is gone is the commoner harm.

    Never raises: both callers are deletion paths, and a run that cannot be deleted because its
    teardown threw would be a row visible forever with no way to remove it.
    """
    from gideon.automation.workflows import provisioning

    try:
        from gideon.core.config.loader import AppConfig

        enabled = bool(AppConfig.load().workflows.workspace_teardown_on_expiry)
    except Exception:
        enabled = True

    state = provisioning.workspace_state(run)
    if not state:
        return provisioning.TornDown()
    if not enabled:
        state = dict(state)
        state.pop("teardown", None)
        extra = getattr(run, "extra", None)
        if isinstance(extra, dict):
            extra[provisioning.WORKSPACE_KEY] = state
    try:
        workspace_dir = _run_workspace_dir(run)
        torn = await provisioning.teardown(
            run, workspace_dir=workspace_dir, keep_open=keep_open, runner=runner
        )
    except Exception:
        logger.warning(
            "workspace teardown failed for %s", getattr(run, "id", "?"), exc_info=True
        )
        return provisioning.TornDown()
    try:
        from gideon.automation.workflows.journal import Journal

        Journal(str(getattr(run, "id", "") or "")).workspace_teardown(
            torn.to_dict(), reason=reason
        )
    except Exception:
        logger.debug("could not journal the workspace teardown", exc_info=True)
    return torn


def _run_workspace_dir(run: Any) -> str:
    """The codebase a run's project binds, for git-side teardown. "" when there is none.

    Read from the PROJECT rather than the run record on purpose: a bound workspace can be
    re-pointed between the run and its deletion, and the git operations (worktree remove, branch
    bookkeeping) must target wherever the repo is NOW, not where it was.
    """
    pid = str(getattr(run, "project_id", "") or "")
    if not pid:
        return ""
    try:
        from gideon.engine.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().get_project(pid)
        return str(getattr(project, "workspace_dir", "") or "") if project else ""
    except Exception:
        logger.debug("project workspace lookup failed for %s", pid, exc_info=True)
        return ""


def drop_status(run_id: str) -> dict[str, Any]:
    """The run's file-drop policy + what has already been dropped (WORK-CONTAINERS §2.5).

    Returns OK with `enabled: false` for a run whose template declared no drop, rather than an
    error:
    "this run does not accept files" is a fact the UI renders as a disabled affordance, and a 4xx
    would make an intentional configuration look like a broken request (§2.5's honest disabled
    status).
    """
    from gideon.automation.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    policy = filedrop.parse_policy(store.read_spec(run_id))
    return _ok(
        enabled=policy.enabled,
        reason=policy.reason,
        auto_accept_mimes=list(policy.auto_accept_mimes),
        max_files=filedrop.MAX_DROPPED_FILES,
        files=filedrop.read_manifest(run_id),
    )


def accept_dropped_file(
    run_id: str, *, filename: str, data: bytes, mime: str = "", confirmed: bool = False
) -> dict[str, Any]:
    """Ingest one dropped file into the run's immutable zone, gated on approval.

    Every refusal is a DISTINCT code, because the four reasons a drop is refused need four different
    reactions from the caller: not accepting files at all (hide the affordance), needing approval
    (show what + size and ask), too many files (offer a clear-out), too large (nothing to do but
    pick a smaller file). One generic rejection would collapse them into a dead end.
    """
    from gideon.automation.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    policy = filedrop.parse_policy(store.read_spec(run_id))
    if not policy.enabled:
        return _service_failure(
            "WF_DROP_DISABLED",
            policy.reason or "this run does not accept dropped files",
        )
    needs, why = filedrop.approval_required(policy, mime, confirmed=confirmed)
    if needs:
        return _service_failure(
            "WF_DROP_APPROVAL_REQUIRED",
            why,
            pending={
                "filename": filedrop.safe_filename(filename),
                "size": len(data),
                "mime": mime,
            },
        )
    existing = filedrop.read_manifest(run_id)
    safe = filedrop.safe_filename(filename)
    if len(existing) >= filedrop.MAX_DROPPED_FILES and not any(
        e.get("filename") == safe for e in existing
    ):
        return _service_failure(
            "WF_DROP_LIMIT",
            f"this run already holds {filedrop.MAX_DROPPED_FILES} dropped files",
        )
    try:
        entry = filedrop.store_dropped_bytes(run_id, filename, data)
    except (OSError, ValueError) as exc:
        return _service_failure(
            "WF_DROP_WRITE_FAILED", f"could not store the dropped file: {exc}"
        )
    entry["mime"] = mime
    entry["approved"] = not needs
    entry["accepted_at"] = _now()
    filedrop.record_drop(run_id, entry)
    return _ok(file=entry, files=filedrop.read_manifest(run_id))


def outbox(run_id: str) -> dict[str, Any]:
    """The run's published-artifact listing — the §2.5 outbox, newest-first.

    A read over what `apply_publish` journalled, so the listing and the publishes cannot disagree.
    Each row carries its artifact `kind`; the FE resolves the preview through the `contentTypes`
    registry rather than this route naming renderers.
    """
    from gideon.automation.workflows import filedrop

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    return _ok(files=filedrop.outbox_entries(run_id))


def introspect(run_id: str) -> dict[str, Any]:
    """The nine-question introspection projection for one run (WORK-CONTAINERS §6.4, R6).

    Everything here is a PROJECTION over the journal this run already wrote —
    `introspection.py` holds the arithmetic and this function holds the reads. No metrics
    store, per the plan's own words: "pass-rate, failure distribution and latency
    percentiles are queries over this".

    The template card aggregates ACROSS runs of the same template, which is why this reads
    the sibling runs' ledgers too: "what is costing money" is a question about the template,
    not about the one run in front of you, and a p95 computed from a single run would just
    restate the run. The sibling read is bounded by `_TEMPLATE_CARD_RUNS` because a personal
    instance accumulates runs forever and the surface that answers "what does this usually
    cost" must not get slower every week.

    `checklist_gaps` runs LAST, over the payload actually assembled, so the response says
    which of the nine questions its own body cannot answer. That is what makes the checklist
    a contract rather than a comment: a surface rendering eight of nine has a named hole, and
    the name arrives with the data instead of in a review.
    """
    from gideon.automation.workflows import filedrop, introspection

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    events = journal_mod.ledger(run_id)
    stats = introspection.run_stats(run_id, events)
    gates = introspection.gate_stats(events)

    evidence = [
        str(entry.get("slug") or "") for entry in filedrop.outbox_entries(run_id)
    ]
    proof = introspection.proof_section(
        stats, evidence_files=[e for e in evidence if e]
    )

    card = introspection.TemplateCard(template=run.workflow_name)
    edges = introspection.EdgeStats()
    if run.workflow_name:
        siblings, _total = store.list_runs(
            workflow_name=run.workflow_name, limit=_TEMPLATE_CARD_RUNS
        )
        sibling_ledgers = [
            (r.id, events if r.id == run_id else journal_mod.ledger(r.id))
            for r in siblings
        ]
        sibling_stats = [
            stats if rid == run_id else introspection.run_stats(rid, evs)
            for rid, evs in sibling_ledgers
        ]
        edges = introspection.edge_stats([evs for _rid, evs in sibling_ledgers])
        warnings = sorted(
            {
                w
                for g in _template_gates(sibling_ledgers, run_id, gates).values()
                if (w := g.fake_check_warning())
            }
            | set(edges.warnings())
        )
        card = introspection.template_card(
            run.workflow_name, sibling_stats, warnings=warnings
        )

    signature = introspection.trajectory_signature(run_id, events)
    trajectory_regression = None
    trajectory_distribution: dict[str, int] = {}
    if run.workflow_name:
        history: list[tuple[str, bool]] = []
        for r in sorted(siblings, key=lambda s: getattr(s, "created_at", "") or ""):
            sib_events = events if r.id == run_id else journal_mod.ledger(r.id)
            sib_sig = introspection.trajectory_signature(r.id, sib_events).signature
            sib_failed = (
                stats if r.id == run_id else introspection.run_stats(r.id, sib_events)
            ).steps_failed > 0
            trajectory_distribution[sib_sig] = (
                trajectory_distribution.get(sib_sig, 0) + 1
            )
            history.append((sib_sig, sib_failed))
        trajectory_regression = introspection.trajectory_regression(
            run.workflow_name, history
        )

    from gideon.automation.workflows.human_input import list_continuations

    nodes = _nodes_of(run_id)
    open_asks = [
        {
            "resume_token": c.token,
            "node_id": c.node_id,
            "ask": journal_mod.redact(c.ask or {}),
        }
        for c in list_continuations(run_id)
        if not c.expired
    ]
    answers: dict[str, Any] = {
        "running": {
            "status": run.status.value,
            "workflow": run.workflow_name,
            "nodes": [
                n for n in nodes if n.get("state") in ("running", "ready", "waiting")
            ],
        },
        "changed": introspection_timeline(events),
        "blocked": [n for n in nodes if n.get("state") == "waiting"],
        "approval": open_asks,
        "failed": [n for n in nodes if n.get("state") in ("failed", "scope_violation")],
        "cost": stats.to_dict(),
        "risky": {
            "degraded": [n for n in nodes if n.get("state") == "degraded"],
            "gates": [g.to_dict() for g in gates.values()],
            "edges": edges.to_dict(),
            "verification_debt": stats.verification_debt,
            "trajectory_regression": (
                trajectory_regression.to_dict() if trajectory_regression else None
            ),
        },
        "next": _next_if_silent(run, nodes, open_asks),
        "proof": proof.to_dict(),
    }
    return _ok(
        run_id=run_id,
        workflow=run.workflow_name,
        stats=stats.to_dict(),
        gates={node_id: g.to_dict() for node_id, g in gates.items()},
        edges=edges.to_dict(),
        template_card=card.to_dict(),
        trajectory=signature.to_dict()
        | {
            "distribution": trajectory_distribution,
            "regression": (
                trajectory_regression.to_dict() if trajectory_regression else None
            ),
        },
        proof=proof.to_dict(),
        timeline=answers["changed"],
        touched=touched_items(run_id),
        answers=answers,
        checklist_gaps=introspection.checklist_gaps(answers),
    )


def ledger_rails(run_id: str) -> dict[str, Any]:
    """The two ledger rails for one run (PP-16 seam 4, the ledger-rails third).

    The run-side answer to the loop cockpit's findings rail and verdict/ROI rail. Both are pure
    PROJECTIONS over the ledger this run already wrote — `introspection.py` holds the arithmetic,
    this holds the read — so nothing new is stored and no kind is minted. It is the same read the
    loop side does through `loop/store.py::get_redacted`, which attaches `findings`
    (`files.get_findings`, over `step_completed`) and `verdicts` (`files.get_verdicts`, over
    `judge_verdict`) to the loop's own detail payload.

    ONE ledger read for both rails and their totals, and the totals are computed from the projected
    ROWS: a second read with a second filter is how a count and the rows beneath it drift apart.

    Per-run and cheap, which is why it is not folded into `introspect`. That payload reads this
    template's SIBLING runs to earn its p50/p95 card and its said-no sample, so it is bounded by
    `_TEMPLATE_CARD_RUNS` and gets slower as a template accumulates history. These two rails are a
    single run's own history, so the cockpit can paint them on connect.

    Redacted through `journal_mod.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift. The findings rail carries `model`,
    `degraded_reason` and `output_ref`, and a degraded reason is exactly where a credential
    surfaces in a screenshot.
    """
    from gideon.automation.workflows import introspection

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    events = journal_mod.ledger(run_id)
    findings = [journal_mod.redact(row) for row in introspection.findings_rail(events)]
    verdicts = [journal_mod.redact(row) for row in introspection.verdict_rail(events)]
    totals = introspection.rail_totals(findings, verdicts)
    return _ok(
        run_id=run_id,
        workflow=run.workflow_name,
        findings=findings,
        verdicts=verdicts,
        totals=totals.to_dict(),
        coverage=introspection.rail_coverage(events),
    )


def run_deliverable(run_id: str) -> dict[str, Any]:
    """The run's DOCUMENT deliverable and working log (PP-16 unit 1).

    The run-side answer to `GET /api/loops/{id}/report`, which serves `store.read_deliverable` +
    `store.read_log` off one route. Same two slots, the same kind-declared filenames and the same
    redaction — a READ over files the run already has, so nothing is stored and no kind is minted.

    **The filename is DERIVED, not configured here.** `deliverable.resolve_name` walks the loop
    alias table forward and asks each kind's own `deliverable_name`, so `goal-pursuit-open-ended`
    resolves to `REPORT.md`, `goal-pursuit-monitor` to `MONITOR_LOG.md` and `design-project` to
    `DESIGN.md` because those kinds say so — not because this module repeats them.

    **Absence is named, five ways** (see `workflows/deliverable.py`), because a blank panel cannot
    tell a user whether the worker has not written yet, whether this kind produces a check rather
    than a document, or whether the template never asked for one. `instructed` is that question,
    measured per run against the run's OWN spec: today no bundled template names its kind's
    document, so an absent REPORT.md is a template gap rather than a slow worker, and the surface
    says which.

    **No money field, deliberately** — issue #2566: `run_totals` reports `cost_usd 0.0` for a loop
    because `LoopJournal.cycle` writes no money keys, and PP-16 sends loop-backed runs through every
    run-side surface. A cost here would read `$0.00` for work that cost real money, on the one page
    a user opens to find out what the document cost.

    404s for an unknown run, so a polled deleted run is distinguishable from one whose worker has
    not written yet — the same rule `api_loop_report` adopted after the same bug.
    """
    from gideon.automation.loop import store as loop_store
    from gideon.automation.workflows import deliverable as deliverable_mod

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    workflow = str(getattr(run, "workflow_name", "") or "")
    resolved = deliverable_mod.resolve_name(workflow)
    roots = deliverable_mod.run_roots(run)
    report = deliverable_mod.read_document(roots, resolved.name, reason=resolved.reason)
    log = deliverable_mod.read_document(roots, loop_store.LOG_NAME)
    return _ok(
        run_id=run_id,
        workflow=workflow,
        report=report.to_dict(),
        log=log.to_dict(),
        derivation=resolved.to_dict(),
        roots=roots.to_dict(),
        instructed=deliverable_mod.instructed_by_spec(
            store.read_spec(run_id), resolved.name
        ),
    )


def template_trajectory(name: str) -> dict[str, Any]:
    """The trajectory-signature distribution and regression signal for one template (PP-7).

    Queryable WITHOUT a run in hand: given a template name, this reads its recent runs' ledgers,
    projects each to its trajectory signature, and reports the distribution of signature classes,
    each run's class, and the sample-gated regression signal. A pure projection over ledgers already
    on disk — no store, no model call. Bounded by `_TEMPLATE_CARD_RUNS` for the same reason the
    introspection card is: a personal instance accumulates runs forever.
    """
    from gideon.automation.workflows import introspection

    runs, _total = store.list_runs(workflow_name=name, limit=_TEMPLATE_CARD_RUNS)
    history: list[tuple[str, bool]] = []
    distribution: dict[str, int] = {}
    signatures: list[dict[str, Any]] = []
    for run in sorted(runs, key=lambda r: getattr(r, "created_at", "") or ""):
        run_id = getattr(run, "id", "")
        if not run_id:
            continue
        events = journal_mod.ledger(run_id)
        sig = introspection.trajectory_signature(run_id, events).signature
        failed = introspection.run_stats(run_id, events).steps_failed > 0
        distribution[sig] = distribution.get(sig, 0) + 1
        history.append((sig, failed))
        signatures.append({"run_id": run_id, "signature": sig, "failed": failed})
    regression = introspection.trajectory_regression(name, history)
    return _ok(
        template=name,
        runs=len(history),
        distribution=distribution,
        signatures=signatures,
        regression=regression.to_dict() if regression else None,
    )


def touched_items(run_id: str) -> list[dict[str, Any]]:
    """What this run TOUCHED, newest-first — the live touched-items feed (§6.5 / R13).

    Unions the two run-attributed mutation records that exist today:

    * ``publishes.jsonl`` — every artifact this run published, versioned or converged (§2.5).
    * the file-drop manifest — every file handed INTO the run.

    Both are already run-scoped, which is the whole reason the feed is buildable: attribution is
    the hard part, not the union. A feed assembled by scanning the artifact registry for things
    that changed recently would attribute another run's work to this one the moment two runs
    overlapped.

    **The knowledge half is absent, not omitted.** Knowledge mutations carry no run attribution
    (S47's lineage covered artifacts only), so a knowledge row here would have to be guessed from
    timing — and a feed that says "this run wrote that memory" on a coincidence is worse than a
    feed that does not mention memory. See the plan's §6.5 note; closing it is a journal-format
    change, not a rendering one.
    """
    from gideon.automation.workflows import filedrop

    rows: list[dict[str, Any]] = []
    for entry in filedrop.outbox_entries(run_id):
        rows.append(
            {
                "kind": "artifact",
                "ref": str(entry.get("slug") or ""),
                "label": str(entry.get("artifact") or entry.get("slug") or ""),
                "action": str(entry.get("action") or ""),
                "detail": str(entry.get("change_note") or ""),
                "node_id": str(entry.get("node_id") or ""),
                "ts": str(entry.get("updated_at") or ""),
            }
        )
    for entry in filedrop.read_manifest(run_id):
        rows.append(
            {
                "kind": "file",
                "ref": str(entry.get("filename") or ""),
                "label": str(entry.get("filename") or ""),
                "action": "dropped",
                "detail": str(entry.get("mime") or ""),
                "node_id": "",
                "ts": str(entry.get("accepted_at") or ""),
            }
        )
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows


_TEMPLATE_CARD_RUNS = 50


def _template_gates(
    sibling_ledgers: list[tuple[str, list[dict[str, Any]]]],
    run_id: str,
    own: dict[str, Any],
) -> dict[str, Any]:
    """Per-gate stats ACROSS the template's runs, over ledgers the caller already read.

    The fake-check badge needs a SAMPLE: `FAKE_CHECK_MIN_RUNS` gate resolutions is a claim
    about the gate's history, and computing it from one run would leave the badge permanently
    unarmed — the exact "declared but can never fire" shape this atom exists to close. Takes the
    pre-read ledgers so the run-economics, said-no and edge-distribution projections share one read
    of each sibling rather than three.
    """
    from gideon.automation.workflows import introspection

    merged: dict[str, Any] = {}
    for rid, events in sibling_ledgers:
        gates = own if rid == run_id else introspection.gate_stats(events)
        for node_id, stats in gates.items():
            into = merged.setdefault(node_id, introspection.GateStats(node_id=node_id))
            into.passes += stats.passes
            into.rejects += stats.rejects
            into.retries_consumed += stats.retries_consumed
    return merged


_TIMELINE_KINDS = (
    "run_started",
    "run_finished",
    "step_started",
    "step_completed",
    "step_failed",
    "step_skipped",
    "step_cached",
    "step_attempt",
    "step_escalated",
    "gate_resolved",
    "gate_revised",
    "handoff",
    "decision",
    "steering",
    "breaker_trip",
)


def introspection_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The journal timeline + attempt ledger, oldest-first, redacted.

    Routed through `journal_mod.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift. The ledger records a node's model and
    failure detail verbatim, and a failure message is exactly where a credential surfaces in a
    screenshot.

    Oldest-first because this reads as a narrative: "what changed" answered newest-first makes a
    reader reconstruct causality backwards.
    """
    out: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("kind") or "") not in _TIMELINE_KINDS:
            continue
        row = {
            "kind": str(event.get("kind") or ""),
            "ts": str(event.get("ts") or ""),
            "node_id": str(event.get("node_id") or ""),
            "instance_path": str(event.get("instance_path") or ""),
            "attempt": event.get("attempt"),
            "state": str(event.get("state") or ""),
            "duration_secs": event.get("duration_secs"),
            "tokens": event.get("tokens"),
            "cost_usd": event.get("cost_usd"),
            "model": str(event.get("model") or ""),
            "approved": event.get("approved"),
            "detail": event.get("detail") or event.get("error") or "",
        }
        out.append(journal_mod.redact(row))
    return out


def _next_if_silent(
    run: Any, nodes: list[dict[str, Any]], open_asks: list[dict[str, Any]]
) -> dict[str, Any]:
    """ "What happens next if I say nothing" — answered, not implied.

    The one checklist question no existing surface answers, and the one that decides whether a
    user can walk away. Three real cases, because they demand different user action: a run
    waiting on an answer will sit there indefinitely (the user IS the blocker), a running run
    proceeds on its own, and a terminal run has already stopped.
    """
    from gideon.automation.workflows.models import TERMINAL_RUN_STATUSES

    if run.status in TERMINAL_RUN_STATUSES:
        return {
            "action": "nothing",
            "detail": f"this run is {run.status.value}",
            "queued": [],
        }
    if open_asks:
        return {
            "action": "waits",
            "detail": (
                f"{len(open_asks)} question(s) are waiting for an answer — this run makes no "
                "further progress until one is given"
            ),
            "queued": [str(c.get("node_id") or "") for c in open_asks],
        }
    queued = [
        str(n.get("node_id") or "")
        for n in nodes
        if n.get("state") in ("pending", "ready")
    ]
    return {
        "action": "proceeds",
        "detail": (
            f"{len(queued)} node(s) are queued and will run without further input"
            if queued
            else "no queued work remains; the run is finishing"
        ),
        "queued": queued,
    }


def workspace_review(run_id: str) -> dict[str, Any]:
    """The code-run cockpit's diff panel + the two reintegration verbs (WORK-CONTAINERS §4.1).

    Pure read: it shells out for `git status --porcelain` and a `merge-tree` conflict probe,
    neither of which mutates either tree. Reintegration is OFFERED, never performed — the plan's
    explicit ruling, and the reason this is a GET rather than a POST.
    """
    from gideon.automation.workflows import provisioning

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    try:
        body = provisioning.reintegration(run, workspace_dir=_run_workspace_dir(run))
    except Exception as exc:
        logger.debug("workspace review failed for %s", run_id, exc_info=True)
        return _service_failure(
            "WF_WORKSPACE_UNREADABLE", f"could not read the run's workspace: {exc}"
        )
    try:
        state = provisioning.inspect_run(run)
        if provisioning.stamp_preserved_path(run, state):
            store.save(run)
    except Exception:
        logger.debug(
            "could not record preserved_workspace_path for %s", run_id, exc_info=True
        )
    from gideon.automation.workflows import web_preview

    body["preview"] = web_preview.preview_scan(run).to_dict()
    return _ok(**body)


def pause_run(run_id: str, *, supervisor: Any = None) -> dict[str, Any]:
    """Stop launching new nodes; in-flight ones finish.

    A pause is a REQUEST recorded on the run, consumed by the tick loop — the same
    single-writer discipline as cancel.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        return _service_failure(
            "WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}"
        )
    run.extra["pause_requested"] = True
    store.save(run)
    return _ok(run_id=run_id, pause_requested=True)


def steer_run(run_id: str, text: str) -> dict[str, Any]:
    """Queue a mid-run steering instruction (LOOPS-EVOLUTION R14).

    Recorded ON THE RUN and consumed at the next iteration boundary, exactly like a pause:
    the tick loop is the single writer, and injecting mid-iteration would race the worker's
    own state. Queued rather than applied, so the user can steer a run that is busy without
    waiting for it — the alternative today is cancel-and-restart, which loses the cycle
    context that made steering worth doing.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    if run.status in TERMINAL_RUN_STATUSES:
        return _service_failure(
            "WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}"
        )
    cleaned = (text or "").strip()
    if not cleaned:
        return _service_failure("WF_STEER_EMPTY", "a steering instruction needs text")

    pending = run.extra.get("steering_queue")
    if not isinstance(pending, list):
        pending = []
    pending.append({"text": cleaned[:4000], "queued_at": _now()})
    run.extra["steering_queue"] = pending
    store.save(run)
    return _ok(run_id=run_id, queued=len(pending))


def pending_steering(run_id: str) -> dict[str, Any]:
    """What is queued but not yet consumed — so the UI can show it as pending.

    A queued instruction the user cannot see is indistinguishable from one that was
    dropped, and they will queue it again.
    """
    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")
    pending = run.extra.get("steering_queue")
    items = pending if isinstance(pending, list) else []
    return _ok(run_id=run_id, pending=items, count=len(items))


def resolve_confirmation(
    run_id: str,
    *,
    supervisor: Any = None,
    verb: str = "",
    token: str = "",
    note: str = "",
    responder: str = "",
) -> dict[str, Any]:
    """Resolve a pending confirmation by VERB — the backend the DagView's Approve/Deny needs.

    Rides `resume_run` rather than reaching into the controller: there is ONE place a resume
    token is consumed (the claim primitive lives with the token, and S57 measured a read-then-
    unlink version letting multiple callers consume one approval in 36 of 40 races). A second
    resolve path would be a second chance to double-approve.

    What this adds over `resume_run` is the VERB vocabulary: `approve | reject | skip | quit`,
    with an unknown verb REFUSED rather than treated as a reject. A typo silently declining an
    approval would reject work the user meant to allow, and they would have no way to know why.

    `skip` and `quit` resolve nothing on purpose — skip leaves the item pending for the next
    pass (different from rejecting it) and quit stops asking without answering. Neither touches
    the run, so neither consumes the token.
    """
    from gideon.automation.workflows.confirmation import resolve as resolve_verb

    resolution, error = resolve_verb(verb, note=note)
    if resolution is None:
        return _service_failure("WF_CONFIRM_VERB_INVALID", error)
    if store.get(run_id) is None:
        return _run_not_found(run_id)
    if not resolution.resumes:
        return _ok(
            run_id=run_id,
            resumed=False,
            still_pending=resolution.still_pending,
            verb=resolution.verb,
        )
    result = resume_run(
        run_id,
        supervisor=supervisor,
        token=token,
        answer=resolution.approved,
        responder=responder,
    )
    result.setdefault("verb", resolution.verb)
    result.setdefault("approved", resolution.approved)
    return result


def resume_run(
    run_id: str,
    *,
    supervisor: Any = None,
    token: str = "",
    answer: Any = None,
    responder: str = "",
    channel: str = "",
    always_allow: bool = False,
) -> dict[str, Any]:
    """Answer a gate, or clear a pause.

    When no token is given the newest pending continuation is used — a chat user says
    "approve it", not a 32-character token. If several gates are pending, the token becomes
    required rather than guessed: approving the wrong gate is worse than asking.

    `answer` also carries the `revise{step_ref, comment}` verb, which `controller.resume`
    recognises: one step is amended and the gate re-asks, instead of the reviewer having to
    reject the whole plan and re-run it to get one sentence changed.
    """
    from gideon.automation.workflows.human_input import list_continuations

    run = store.get(run_id)
    if run is None:
        return _run_not_found(run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return _service_failure(
            "WF_RUN_ALREADY_TERMINAL", f"run is already {run.status.value}"
        )

    if answer is None and not token:
        run.extra.pop("pause_requested", None)
        store.save(run)
        return _ok(run_id=run_id, resumed=True, gate_answered=False)

    controller = _live(run_id, supervisor)
    if controller is None:
        return _service_failure(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller to apply the answer to",
        )
    if not token:
        pending = list_continuations(run_id)
        if not pending:
            return _service_failure(
                "WF_NO_PENDING_GATE", "this run has no gate awaiting an answer"
            )
        if len(pending) > 1:
            named = ", ".join(c.node_id or c.instance_path for c in pending)
            return _service_failure(
                "WF_AMBIGUOUS_GATE",
                f"{len(pending)} gates are awaiting an answer ({named}) — answer one by its "
                "resume token, listed under `pending`",
                pending=[
                    {
                        "node_id": c.node_id,
                        "token": c.token,
                        "prompt": c.ask.get("prompt", ""),
                    }
                    for c in pending
                ],
            )
        token = pending[0].token
    result = controller.resume(
        token, answer, responder=responder, channel=channel, always_allow=always_allow
    )
    result.setdefault("run_id", run_id)
    return result


def _reentry(
    run_id: str,
    node_id: str,
    op: str,
    *,
    supervisor: Any = None,
    redo_effects: bool = False,
    force: bool = False,
    confirm_cascade: bool = False,
) -> dict[str, Any]:
    if store.get(run_id) is None:
        return _run_not_found(run_id)
    controller = _live(run_id, supervisor)
    if controller is None:
        return _service_failure(
            "WF_RUN_NOT_LIVE",
            f"run {run_id!r} has no live controller — resume the run before {op}",
        )
    return controller.submit_mutation(
        [{"op": op, "node_id": node_id, "redo_effects": redo_effects, "force": force}],
        actor="chat",
        confirm=confirm_cascade,
    )


def rewind_run(run_id: str, node_id: str, **kw: Any) -> dict[str, Any]:
    return _reentry(run_id, node_id, "rewind", **kw)


def run_from(run_id: str, node_id: str, **kw: Any) -> dict[str, Any]:
    return _reentry(run_id, node_id, "run_from", **kw)


def skip_nodes(
    run_id: str, node_ids: list[str], *, supervisor: Any = None
) -> dict[str, Any]:
    controller = _live(run_id, supervisor)
    if controller is None:
        return _service_failure(
            "WF_RUN_NOT_LIVE", f"run {run_id!r} has no live controller"
        )
    return controller.submit_mutation(
        [{"op": "skip", "node_id": n} for n in (node_ids or [])],
        actor="chat",
        confirm=True,
    )


def fork_run(
    run_id: str, *, checkpoint_id: str = "", note: str = "", supervisor: Any = None
) -> dict[str, Any]:
    """Branch a new run. Works on a terminal run too — forking a finished result to explore
    an alternative is the main reason to fork at all."""
    from gideon.automation.workflows.checkpoints import fork_run as do_fork

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
    for path in sorted(instances):
        inst = instances[path]
        base = spec_path(path)
        row: dict[str, Any] = {
            "instance_path": path,
            "node_id": ids.get(base, ""),
            "state": inst.state.value,
            "attempt": inst.attempt,
            "degraded_reason": inst.degraded_reason,
            "failure": inst.failure.to_dict() if inst.failure else None,
        }
        markers = list(re.finditer(r"[#@](\d+)(?=\.|$)", path))
        total = inst.item_total or totals.get(sibling_group(path), 0)
        if markers and (inst.item_total > 0 or total > 1):
            row["item_index"] = int(markers[-1].group(1))
            row["item_total"] = total
            if inst.item_label:
                row["item_label"] = inst.item_label
        out.append(row)
    return out


def _completion_summary(run: Any, status: RunStatus) -> str:
    """A one-line completion summary for the launching session's mirror.

    Drawn from the run's own recorded handoff summary — what the run said it produced —
    rather than fabricated, matching `controller._revise_project_overview`. A run that said
    nothing hands over nothing, so the line falls back to name + status, which is honest.
    """
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
    run = store.get(run_id)
    return run.status.value if run else "unknown"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "WorkflowDef",
    "audit",
    "author_def",
    "cancel_run",
    "delete_def",
    "edit_run",
    "fork_run",
    "get_def",
    "list_defs",
    "manifest",
    "observe",
    "output",
    "pause_run",
    "preview_edit",
    "resume_run",
    "rewind_run",
    "run_from",
    "skip_nodes",
    "start_run",
    "status",
]
