"""Transport admission for models executed on this machine, across event loops."""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass, field
import threading
import time
from urllib.parse import urlsplit
import uuid
import ipaddress


class LocalInferenceBusy(RuntimeError):
    """The request was never sent; the finite chain may try its next entry."""
    unsent = True


@dataclass(frozen=True)
class Attended:
    step: str
    session: str = ""


_ATTENDED = contextvars.ContextVar("gideon_local_attended", default=None)
_NEXT = contextvars.ContextVar("gideon_local_next", default="")


@contextlib.contextmanager
def attending(who: Attended | None):
    token = _ATTENDED.set(who)
    try:
        yield
    finally:
        _ATTENDED.reset(token)


@contextlib.contextmanager
def next_entry(ref: str):
    from gideon.security.execution_lineage import admits
    token = _NEXT.set(ref if not ref or admits(ref) else "")
    try:
        yield
    finally:
        _NEXT.reset(token)


def resource_key(provider: str, model: str) -> str:
    """A declared local execution resource, never a provider-name or suffix guess."""
    try:
        from gideon.integrations.llm.registry import get_default_registry, serving_entry, serving_endpoint, served_on_this_machine
        entry = serving_entry(provider)
        if entry is None or not served_on_this_machine(entry, model):
            return ""
        if entry.type in get_default_registry()._in_process_types:
            return f"in-process:{entry.type}|{model}"
        url = urlsplit(serving_endpoint(entry))
        host = str(url.hostname or "").lower().rstrip(".")
        if host == "localhost":
            host = "127.0.0.1"
        else:
            try:
                host = ipaddress.ip_address(host).compressed
            except ValueError:
                pass
        port = url.port or (443 if url.scheme == "https" else 80)
        return f"loopback:{url.scheme}:{host}:{port}{url.path.rstrip('/')}|{model}"
    except Exception:
        return ""  # ordering failure never grants or denies a model permission


@dataclass
class _Request:
    key: str
    provider: str
    model: str
    attended: Attended | None
    next_ref: str
    caller: str
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    deadline: float | None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    moved: bool = False
    granted: bool = False


@dataclass
class _Resource:
    active: _Request | None = None
    attended: list[_Request] = field(default_factory=list)
    background: list[_Request] = field(default_factory=list)


_LOCK = threading.RLock()
_RESOURCES: dict[str, _Resource] = {}
_LISTENERS = set()


def subscribe(listener):
    with _LOCK:
        _LISTENERS.add(listener)


def unsubscribe(listener):
    with _LOCK:
        _LISTENERS.discard(listener)


def _notify():
    with _LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener()
        except Exception:
            pass


def _grant_locked(resource: _Resource):
    if resource.active is not None and resource.active.loop.is_closed():
        resource.active = None
    while resource.active is None and (resource.attended or resource.background):
        request = (resource.attended or resource.background).pop(0)
        if request.future.cancelled() or request.loop.is_closed() or request.moved:
            continue
        request.granted = True
        resource.active = request
        return request
    return None


def _wake(request):
    if request is None:
        return
    def resolve():
        if request.future.done():
            _release(request)
        else:
            request.future.set_result(None)
    try:
        request.loop.call_soon_threadsafe(resolve)
    except RuntimeError:
        _release(request)


def _release(request):
    granted = None
    with _LOCK:
        resource = _RESOURCES.get(request.key)
        if resource is None:
            return
        for lane in (resource.attended, resource.background):
            if request in lane:
                lane.remove(request)
        if resource.active is request:
            resource.active = None
        granted = _grant_locked(resource)
        if resource.active is None and not resource.attended and not resource.background:
            _RESOURCES.pop(request.key, None)
    _wake(granted)
    _notify()


async def acquire(provider: str, model: str, *, within: float | None = None):
    key = resource_key(provider, model)
    if not key:
        return None
    loop = asyncio.get_running_loop()
    who = _ATTENDED.get()
    next_ref = _NEXT.get()
    limit = within
    if who is not None and next_ref:
        limit = min(limit, 15.0) if limit is not None else 15.0
    from gideon.security.guardrails.audit import current_caller
    request = _Request(key, provider, model, who, next_ref, current_caller(), loop,
                       loop.create_future(), time.monotonic() + max(0, limit) if limit is not None else None)
    with _LOCK:
        resource = _RESOURCES.setdefault(key, _Resource())
        (resource.attended if who is not None else resource.background).append(request)
        grant = _grant_locked(resource)
    _wake(grant)
    _notify()
    try:
        if limit is None:
            await request.future
        else:
            await asyncio.wait_for(request.future, max(0.001, limit))
        if request.moved:
            raise LocalInferenceBusy("The queued request was not sent; moving to the next model.")
        return request
    except TimeoutError as error:
        _release(request)
        raise LocalInferenceBusy("The local model is busy; this request was not sent.") from error
    except BaseException:
        _release(request)
        raise


@contextlib.asynccontextmanager
async def turn(provider: str, model: str, *, within: float | None = None):
    request = await acquire(provider, model, within=within)
    try:
        yield
    finally:
        if request is not None:
            _release(request)


def waits() -> list[dict]:
    rows = []
    with _LOCK:
        for resource in _RESOURCES.values():
            holder = resource.active
            for position, request in enumerate(resource.attended + resource.background, 1):
                if request.attended is None:
                    continue
                rows.append(dict(id=request.id, step=request.attended.step,
                                 provider=request.provider, model=request.model,
                                 holder="another request you are waiting for" if holder and holder.attended else "background work",
                                 position=position, next_ref=request.next_ref,
                                 seconds_left=max(0, request.deadline - time.monotonic()) if request.deadline else None))
    return rows


def move_on(wait_id: str) -> bool:
    request = None
    with _LOCK:
        for resource in _RESOURCES.values():
            for candidate in resource.attended:
                if candidate.id == wait_id and candidate.next_ref and not candidate.granted:
                    resource.attended.remove(candidate)
                    candidate.moved = True
                    request = candidate
                    break
            if request is not None:
                break
    if request is None:
        return False
    def resolve():
        if not request.future.done():
            request.future.set_result(None)
    try:
        request.loop.call_soon_threadsafe(resolve)
    except RuntimeError:
        _release(request)
    _notify()
    return True


@contextlib.asynccontextmanager
async def native_turn(runtime):
    """Give native chat inference the same transport turn as guarded helpers."""
    model = runtime._model
    ref = str(getattr(model, "served_model_ref", "") or runtime._preferred_model_ref or "")
    provider, _, selected = ref.partition(":")
    override = runtime._definition.model if runtime._active_fallback is None else ""
    if override:
        selected = str(override).partition(":")[2] if str(override).startswith(provider + ":") else str(override)
    from gideon.security.session_credentials import current_work
    from gideon.security.approval_answer import OWNER
    work = current_work()
    owner_turn = work is not None and work.initiator.kind == OWNER and not work.created_by_app
    who = Attended("Answering your chat", runtime._session_key) if owner_turn and not runtime._unattended else None
    next_ref = ""
    if runtime._announce_failover:
        from gideon.extensions.providers.use_cases import resolution_chain
        chain = resolution_chain("chat")
        current = runtime._active_fallback or runtime._preferred_model_ref
        if current in chain and chain.index(current) + 1 < len(chain):
            next_ref = chain[chain.index(current) + 1]
    with attending(who), next_entry(next_ref):
        if getattr(model, "takes_local_turns", False):
            yield
        else:
            async with turn(provider, selected, within=model.first_token_timeout_secs or None):
                yield
