"""Immutable workflow definitions shown in an owner execution question."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from typing import Any

BOUNDS_KEY = "owner_workflow_versions"


class VersionConsentError(ValueError):
    pass


def digest(spec: dict[str, Any]) -> str:
    from gideon.automation.workflows.models import WorkflowDef
    spec = WorkflowDef.from_dict(spec).to_dict()
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@contextmanager
def definitions_lock():
    from gideon.automation.workflows.native_defs import defs_root
    root = defs_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".definitions.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _references(value: Any):
    if isinstance(value, dict):
        config = value.get("config")
        config = config if isinstance(config, dict) else {}
        if value.get("kind") == "subworkflow":
            ref = config.get("ref")
            if isinstance(ref, str) and ref.strip():
                yield ref.strip()
        if value.get("kind") == "action" and config.get("provider") == "run-workflow":
            action = config.get("with") or config.get("config") or {}
            ref = action.get("workflow") if isinstance(action, dict) else None
            if isinstance(ref, str) and ref.strip():
                yield ref.strip()
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _blocking(make):
    import asyncio
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make())
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(context.run, lambda: asyncio.run(make())).result()


def _current(name: str):
    from gideon.automation.workflows import defs, native_defs, versions
    from gideon.automation.workflows.bundled_defs import BundledWorkflowDefProvider
    async def find():
        for provider_name in defs.list_providers():
            provider = defs.get_provider(provider_name)
            if provider is None:
                continue
            try:
                definition = await provider.get_def(name)
            except Exception:
                continue
            if definition is not None:
                return definition, provider
        definition = native_defs._read(name)
        return (definition, native_defs.NativeWorkflowDefProvider()) if definition else None
    found = _blocking(find)
    if found is None:
        return None
    definition, provider = found
    spec = definition if isinstance(definition, dict) else definition.to_dict()
    if not isinstance(spec, dict) or not isinstance(spec.get("root"), dict):
        return None
    from gideon.automation.workflows.models import WorkflowDef
    try:
        spec = WorkflowDef.from_dict(spec).to_dict()
    except (ValueError, TypeError, KeyError):
        return None
    provider_name = str(provider.name)
    if type(provider) is native_defs.NativeWorkflowDefProvider:
        record = versions.get_version(name, int(spec.get("version", 1)))
        matches = record is not None and digest(record.spec) == digest(spec)
        saved_by = "owner" if matches and owner_saved(name, record) else "agent" if matches and record.source == versions.SOURCE_REFINER else "imported"
    elif type(provider) is BundledWorkflowDefProvider:
        saved_by = "shipped"
    else:
        saved_by = "provider:" + provider_name
    return spec, provider_name, saved_by


def _spec_path(checksum: str):
    from gideon.automation.workflows import store
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise VersionConsentError("invalid workflow snapshot checksum")
    return store.workflows_dir() / "consent_specs" / (checksum + ".json")


def keep_spec(spec: dict[str, Any]) -> None:
    from gideon.core.atomic_write import atomic_write
    from gideon.automation.workflows.models import WorkflowDef
    document = WorkflowDef.from_dict(spec).to_dict()
    path = _spec_path(digest(document))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if digest(json.loads(path.read_text(encoding="utf-8"))) != path.stem:
                raise VersionConsentError("stored workflow snapshot checksum differs")
        except (OSError, ValueError, TypeError) as error:
            raise VersionConsentError("stored workflow snapshot is unreadable") from error
        return
    atomic_write(path, json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False))


def snapshot(name: str, *, root_spec: dict[str, Any] | None = None, keeping: bool = False) -> dict[str, Any]:
    """Capture all readable static descendants; unavailable children gain no permission."""
    from gideon.automation.workflows import versions
    pending = [(name, 0)]
    selected: dict[str, Any] = {}
    from gideon.automation.workflows.engine import MAX_SUBWORKFLOW_DEPTH
    while pending:
        ref, depth = pending.pop()
        workflow, _, requested = ref.partition("@")
        if depth:
            requested = ""
        if "{{" in workflow:
            continue
        if root_spec is not None and workflow == name.partition("@")[0]:
            current = (root_spec, "native", "owner")
        else:
            current = _current(workflow)
        if current is None:
            if depth == 0:
                raise VersionConsentError(f"workflow {workflow!r} has no readable definition")
            continue
        spec, provider_name, saved_by = current
        version = int(spec.get("version", 1))
        if requested:
            try:
                version = int(requested)
            except ValueError:
                if depth == 0:
                    raise VersionConsentError("invalid workflow version")
                continue
            record = versions.get_version(workflow, version)
            if record is None:
                if depth == 0:
                    raise VersionConsentError("the requested workflow version is unavailable")
                continue
            spec = record.spec
        entry = {"version": version, "digest": digest(spec), "provider": provider_name, "saved_by": saved_by}
        if workflow in selected:
            continue
        selected[workflow] = entry
        if keeping:
            keep_spec(spec)
        if depth < MAX_SUBWORKFLOW_DEPTH:
            pending.extend((child, depth + 1) for child in _references(spec.get("root")))
    return {"root": name.partition("@")[0], "workflows": dict(sorted(selected.items()))}


def pinned_spec(name: str, bounds: Any) -> dict[str, Any]:
    from gideon.automation.workflows import versions
    if not isinstance(bounds, dict) or not isinstance(bounds.get("workflows"), dict):
        raise VersionConsentError("the owner workflow version record is unavailable")
    workflow, _, requested = name.partition("@")
    entry = bounds["workflows"].get(workflow)
    if not isinstance(entry, dict) or type(entry.get("version")) is not int:
        raise VersionConsentError(f"workflow {workflow!r} was not included in owner consent")
    if requested and requested != str(entry["version"]):
        raise VersionConsentError("the referenced version differs from owner consent")
    checksum = str(entry.get("digest") or "")
    path = _spec_path(checksum)
    if path.exists():
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise VersionConsentError("the workflow consent snapshot is unreadable") from error
    else:
        record = versions.get_version(workflow, entry["version"])
        spec = record.spec if record else None
    try:
        valid = isinstance(spec, dict) and digest(spec) == checksum
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise VersionConsentError(f"workflow {workflow!r} consent snapshot is unavailable or changed")
    return dict(spec)


def grant_content(revision: str, bounds: Any) -> str:
    if not isinstance(bounds, dict):
        return ""
    return json.dumps({"revision": revision, "workflows": bounds}, sort_keys=True, separators=(",", ":"), allow_nan=False)


def trigger_bounds(trigger_id: str, name: str) -> dict[str, Any]:
    from gideon.automation.triggers import grants
    from gideon.automation.triggers.store import TriggerStore
    row = grants._loaded_trigger(trigger_id)
    if row is None or not row.ok or not grants.is_granted(row.trigger):
        raise VersionConsentError("the workflow trigger has no current owner execution grant")
    seal = row.trigger.capabilities.get(grants.SEAL_KEY, {})
    bounds = seal.get("workflows")
    if not isinstance(bounds, dict) or bounds.get("root") != name.partition("@")[0]:
        raise VersionConsentError("the workflow differs from the owner execution question")
    pinned_spec(name, bounds)
    return bounds


def run_bounds(run_id: str, *, _seen: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    from gideon.automation.workflows import store
    if run_id in _seen or len(_seen) >= 64:
        raise VersionConsentError("the workflow ancestry is cyclic or too deep")
    run = store.get(run_id)
    if run is None:
        raise VersionConsentError("the ancestor workflow run is unavailable")
    extra = run.extra if isinstance(run.extra, dict) else {}
    bounds = extra.get(BOUNDS_KEY)
    if bounds is not None:
        if not isinstance(bounds, dict):
            raise VersionConsentError("the ancestor workflow version record is invalid")
        return bounds
    if run.parent_run_id:
        return run_bounds(run.parent_run_id, _seen=_seen | {run_id})
    if str(getattr(run.origin.kind, "value", run.origin.kind)) == "hook":
        raise VersionConsentError("the automated ancestor has no owner workflow version record")
    return None


def session_bounds(session_key: str, *, _seen: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    from gideon.automation.workflows import ownership
    owned = ownership.parse_owned(session_key)
    if owned:
        return run_bounds(owned[0])
    if session_key.startswith("subagent:"):
        if session_key in _seen or len(_seen) > 64:
            raise VersionConsentError("the subagent ancestry is cyclic or too deep")
        from gideon.integrations.action_providers.services import get_action_services
        services = get_action_services()
        manager = getattr(services, "subagents", None)
        info = manager.get(session_key.removeprefix("subagent:")) if manager else None
        if info is None:
            raise VersionConsentError("the subagent ancestor record is unavailable")
        parent_run = str(getattr(info, "parent_run", "") or "")
        if parent_run.startswith(ownership.OWNED_PREFIX):
            return run_bounds(parent_run.removeprefix(ownership.OWNED_PREFIX).partition(":")[0])
        return session_bounds(str(info.parent_session_key or ""), _seen=_seen | {session_key})
    return None


def selection(name: str, bounds: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A later owner save carries the descendant versions captured at that save."""
    from gideon.automation.workflows import versions
    workflow, _, requested = name.partition("@")
    entries = bounds.get("workflows")
    allowed = entries.get(workflow) if isinstance(entries, dict) else None
    if not isinstance(allowed, dict) or type(allowed.get("version")) is not int:
        raise VersionConsentError(f"workflow {workflow!r} was not included in owner consent")
    for record in reversed(versions.list_versions(workflow)):
        if record.version <= allowed["version"]:
            break
        if owner_saved(workflow, record):
            if requested not in ("", str(record.version)):
                continue
            merged = {"root": bounds.get("root"), "workflows": {**record.owner_calls.get("workflows", {}), **entries}}
            return dict(record.spec), merged
    return pinned_spec(name, bounds), bounds


def selected_spec(name: str, bounds: dict[str, Any]) -> dict[str, Any]:
    return selection(name, bounds)[0]


def owner_content(spec: dict[str, Any], calls: Any) -> str:
    return json.dumps({"digest": digest(spec), "calls": calls}, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record_owner_save(name: str, spec: dict[str, Any], calls: dict[str, Any]) -> None:
    from gideon.security.owner_grants import GrantBook
    GrantBook("workflow_owner_versions").give(f"{name}@{spec['version']}", owner_content(spec, calls), principal="owner")


def owner_saved(name: str, record: Any) -> bool:
    from gideon.security.owner_grants import GrantBook
    if not isinstance(getattr(record, "owner_calls", None), dict):
        return False
    try:
        return GrantBook("workflow_owner_versions").holds(f"{name}@{record.version}", owner_content(record.spec, record.owner_calls))
    except (OSError, ValueError, TypeError, KeyError):
        return False
