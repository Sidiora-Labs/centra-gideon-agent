"""Bounded process-restart intent for the gateway's ordinary shutdown path."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from gideon import shutdown_event


@dataclass(frozen=True, slots=True)
class RestartRequest:
    """Only process launch state crosses the re-exec boundary."""

    executable: str
    arguments: tuple[str, ...]
    auth_mode: str


def relaunch_argv():
    """Preserve runtime options while dropping one-time destructive seed operations."""
    from gideon.operations.self_update import cli_argv

    arguments = []
    skip = False
    for value in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if value == "--seed":
            skip = True
            continue
        if value.startswith("--seed=") or value in {"--seed-replace", "--seed-local-model"}:
            continue
        arguments.append(value)
    return [*cli_argv(), *arguments]


def request_restart(auth_mode: str) -> bool:
    """Queue restart intent and wake the gateway's normal shutdown loop.

    The auth mode and command line are captured from the running process. No
    customer, operator, request, or session identity is retained.
    """
    command = relaunch_argv()
    executable = command[0]
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise RuntimeError("Cannot restart: invalid Python executable path")
    request = RestartRequest(
        executable=executable,
        arguments=tuple(command[1:]),
        auth_mode=str(auth_mode),
    )
    return shutdown_event.request_restart(request)


def take_restart_request() -> RestartRequest | None:
    request = shutdown_event.take_restart_intent()
    return request if isinstance(request, RestartRequest) else None


def reexec(request: RestartRequest) -> None:
    """Replace the process after the runtime's registered shutdown hooks finish."""
    child_env = dict(os.environ)
    if getattr(sys, "frozen", False):
        from gideon.core.frozen_child import restore_environment
        restore_environment(child_env)
    if request.auth_mode:
        child_env["GIDEON_AUTH_MODE"] = request.auth_mode
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execve(request.executable, [request.executable, *request.arguments], child_env)
    except OSError as exc:
        sys.stderr.write(f"Gideon could not restart: {exc}. Reopen the desktop app or start the gateway again.\n")
        sys.stderr.flush()
        raise SystemExit(1) from None
