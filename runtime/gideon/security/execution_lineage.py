"""Model restrictions inherited from authenticated private execution origins."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.security.session_credentials import BoundWork

_HOST_ADMISSION: ContextVar[BoundWork | None] = ContextVar(
    "private_host_runtime_admission", default=None
)


@contextmanager
def host_runtime_admission(credential):
    """Bootstrap the host's first runtime from a live turn credential only."""
    from gideon.security.session_credentials import verify

    work = getattr(credential, "work", None)
    if work is None or verify(credential.bearer, work.session_key) is not work:
        raise PrivateModelRefused(
            "Runtime admission requires the active host turn credential."
        )
    token = _HOST_ADMISSION.set(work)
    try:
        yield
    finally:
        _HOST_ADMISSION.reset(token)


def admitting_host_runtime() -> bool:
    work = origin()
    return (
        work is not None
        and _HOST_ADMISSION.get() is work
        and not getattr(work, "execution_model", "")
    )


class PrivateModelRefused(PermissionError):
    pass


def origin():
    from gideon.security.session_credentials import current_work

    work = current_work()
    return (
        work
        if work is not None and work.memory_mode in {"temporary", "incognito"}
        else None
    )


def requested_model(named: str = "", *, model_only: bool = False) -> tuple[str, str]:
    """Return the admitted model/runtime; a private origin never borrows a global chain."""
    work = origin()
    if work is None:
        return named, ""
    model = getattr(work, "execution_model", "")
    runtime = getattr(work, "execution_runtime", "")
    allowed = getattr(work, "allowed_models", ())
    if not model or model not in allowed:
        raise PrivateModelRefused(
            "The private origin's actual model has not been authenticated; no model call was made."
        )
    if named and named != model:
        raise PrivateModelRefused(
            "Private work may reach only the original chat's model; the requested model was refused."
        )
    if runtime.startswith("acp:"):
        if model_only:
            raise PrivateModelRefused(
                "This private chat runs through an agent CLI; a model-only call cannot use that runtime."
            )
        return "", runtime
    return model, runtime


def admits(model: str) -> bool:
    work = origin()
    if work is None:
        return True
    return bool(
        model
        and model == getattr(work, "execution_model", "")
        and model in getattr(work, "allowed_models", ())
    )


def run_model(run) -> str:
    """Read the signed run envelope, never an unsigned model label in run.extra."""
    from gideon.automation.workflows import ownership

    if ownership.run_mode(run) is ownership.MemoryMode.NORMAL:
        return ""
    from gideon.security.durable_work import verified_run_origin

    values = verified_run_origin(run)
    model = values.get("execution_model", "") if values else ""
    if not model or model not in (values or {}).get("allowed_models", ()):
        return ""
    return model
