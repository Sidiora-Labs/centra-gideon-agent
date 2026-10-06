"""End private run trees with the native chat that owns their durable origin."""
from __future__ import annotations
import math
from datetime import datetime
from gideon.automation.workflows import ownership, store
from gideon.automation.workflows.chat_runs import whose


def ended(run, state) -> bool:
    mode = ownership.run_mode(run)
    if mode not in {ownership.MemoryMode.TEMPORARY, ownership.MemoryMode.INCOGNITO}:
        return False
    chat = whose(run)
    key = str((chat or {}).get('session') or '')
    # A missing/unverifiable owner is retained and inaccessible, never guessed to have ended.
    if not key or state is None:
        return False
    boot = getattr(state, 'start_time', None)
    try:
        born = datetime.fromisoformat(run.created_at.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return False
    if mode is ownership.MemoryMode.TEMPORARY and isinstance(boot, (int, float)) and born < math.floor(boot):
        return True
    sessions = getattr(state, '_sessions', None)
    if not isinstance(sessions, dict):
        return False
    name = key.removeprefix('dashboard:')
    live = sessions.get(key) or sessions.get(name)
    if live is not None:
        return getattr(live, 'lifecycle', 'active') != 'active'
    if key.startswith('subagent:'):
        from gideon.security.durable_work import recorded_run_origin
        from gideon.security.approval_answer import principal_from_record, APP
        source = recorded_run_origin(run)
        if source is None or source.get('origin_session_key') != key:
            return False
        actor = principal_from_record(source.get('work_actor'))
        if actor.kind != APP or source.get('created_by_app') != actor.name:
            return False
        manager = getattr(state, 'subagents', None)
        getter = getattr(manager, 'get', None)
        try:
            identity = key.removeprefix('subagent:')
            info = getter(identity) if callable(getter) else None
            return info is not None and info.id == identity and bool(info.done)
        except Exception:
            return False
    if not key.startswith('dashboard:'):
        return False
    if mode is ownership.MemoryMode.TEMPORARY:
        return True
    log = getattr(state, 'conversation_log', None)
    if log is None:
        return False
    try:
        return not any(log.has_log(k) for k in (key, name))
    except Exception:
        return False


def trees_ended(state):
    runs, _ = store.list_runs(limit=1_000_000)
    trees = {}
    for run in runs:
        if ownership.run_mode(run) in {ownership.MemoryMode.TEMPORARY, ownership.MemoryMode.INCOGNITO}:
            trees.setdefault(run.root_run_id or run.id, []).append(run)
    return [runs for root, runs in trees.items() if any(run.id == root and ended(run, state) for run in runs)]


async def reconcile(supervisor):
    from gideon.automation.workflows import service, private_work
    from gideon.automation.workflows.controller import RunController
    from gideon.automation.workflows.models import RunStatus
    trees = trees_ended(supervisor._state)
    runs = [run for tree in trees for run in tree]
    for run in runs:
        if run.is_terminal:
            continue
        store.request_cancel(run.id)
        controller = supervisor.controller(run.id)
        if controller is None:
            old = supervisor._controllers.get(run.id)
            if old is not None:
                supervisor._take_over_from(old)
            spec = store.read_spec(run.id)
            if not isinstance(spec, dict):
                continue
            controller = RunController(run, spec, services=supervisor._services_for(run))
            await controller._cancel_inflight()
            await controller._finish(RunStatus.CANCELLED, error='private chat ended')
        else:
            controller.wake()
    current = [store.get(run.id) for run in runs]
    origins = {str(run.extra.get(private_work.ORIGIN_KEY)) for run in current if run and run.extra.get(private_work.ORIGIN_KEY)}
    all_runs, _ = store.list_runs(limit=1_000_000)
    retired = set()
    for origin in origins:
        # One scope may own several root trees; every writer must end before revocation.
        if any(run.extra.get(private_work.ORIGIN_KEY) == origin and not run.is_terminal for run in all_runs):
            continue
        await private_work.retire(supervisor, origin)
        retired.add(origin)
    for tree in trees:
        fresh = [store.get(run.id) for run in tree]
        if not all(run is None or run.is_terminal for run in fresh):
            continue
        for run in sorted((run for run in fresh if run), key=lambda run: run.id == (run.root_run_id or run.id)):
            origin = str(run.extra.get(private_work.ORIGIN_KEY) or '')
            if origin and origin not in retired:
                continue
            await service.delete_run(run.id, supervisor=supervisor)
