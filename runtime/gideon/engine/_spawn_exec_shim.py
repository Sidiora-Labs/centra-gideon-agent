"""Apply resource policy in a fresh child before replacing its process image."""

from __future__ import annotations

import json
import os
import sys
from types import ModuleType

_resource: ModuleType | None
try:
    import resource
except ImportError:
    _resource = None
else:
    _resource = resource


def _resolve_limit(value: object, cur_hard: int, cap: int) -> int:
    if not isinstance(value, int):
        return cur_hard
    return min(value, cap) if value >= 0 and cap >= 0 else value


class ProcessCeilings:
    def __init__(self, policy: dict):
        self.policy = policy

    def limits(self) -> None:
        limits = self.policy.get("limits") or {}
        if _resource is None or not isinstance(limits, dict):
            return
        for name, requested in limits.items():
            resource_id = getattr(_resource, name, None)
            if (
                resource_id is None
                or not isinstance(requested, (list, tuple))
                or len(requested) != 2
            ):
                continue
            try:
                inherited_hard = _resource.getrlimit(resource_id)[1]
                hard = _resolve_limit(requested[1], inherited_hard, inherited_hard)
                soft = _resolve_limit(requested[0], inherited_hard, hard)
                if hard >= 0 and soft >= 0:
                    soft = min(soft, hard)
                _resource.setrlimit(resource_id, (soft, hard))
            except (ValueError, OSError):
                continue

    def oom_bias(self) -> None:
        value = self.policy.get("oom_score_adj")
        if value is None:
            return
        try:
            with open("/proc/self/oom_score_adj", "w", encoding="ascii") as destination:
                destination.write(str(int(value)))
        except (OSError, ValueError):
            pass


def _apply_limits(policy: dict) -> None:
    ProcessCeilings(policy).limits()


def _apply_oom_bias(policy: dict) -> None:
    ProcessCeilings(policy).oom_bias()


def _split_argv(raw: list[str]) -> tuple[str, list[str]]:
    try:
        boundary = raw.index("--")
    except ValueError:
        raise SystemExit("_spawn_exec_shim: missing '--' argv separator") from None
    if boundary == 0:
        raise SystemExit("_spawn_exec_shim: missing policy argument before '--'")
    command = raw[boundary + 1 :]
    if not command:
        raise SystemExit("_spawn_exec_shim: no target command after '--'")
    return raw[0], command


def _read_policy(encoded: str) -> dict:
    try:
        value = json.loads(encoded)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> None:
    encoded, command = _split_argv(list(sys.argv[1:] if argv is None else argv))
    policy = _read_policy(encoded)
    _apply_limits(policy)
    _apply_oom_bias(policy)
    try:
        os.execvp(command[0], command)
    except OSError as error:
        raise SystemExit(
            f"_spawn_exec_shim: cannot exec {command[0]!r}: {error}"
        ) from error


if __name__ == "__main__":
    main()
