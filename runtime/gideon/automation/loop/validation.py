"""Deterministic pre-flight validation for a unified loop-creation payload.

The composer runs this before launch to show estimated cycles/duration and block an
unstartable config. The SHARED spine checks (task length, cycle budget, workspace
path safety, agent existence) live here; each kind contributes its own checks via an
optional ``validate_config(body) -> (errors, warnings)`` strategy method (goal type/
granularity + verify-command screening; code entry-stage + brownfield workspace). The
union folds the legacy loops + code validators onto the one entity. Free of the agent
registry — the HTTP layer passes ``agent_exists`` — so it stays import-light + testable.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from gideon.core.config.loader import AppConfig
from gideon.security.security import is_sensitive_path, is_system_path

_MIN_TASK_LEN = 12
_MAX_TASK_LEN = 100_000


def _as_int(value) -> int | None:
    """Coerce a JSON value to int, or None if it isn't a whole number. Tolerates a
    clean integer string (JSON clients sometimes send numbers as strings) but rejects
    a non-numeric one — so a malformed value surfaces as a clean validation error
    rather than an unhandled int() ValueError → 500. Ported from the legacy code
    validator, dropped at the unified-validator cutover (which used a raw int())."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


@dataclass
class ValidationResult:
    can_start: bool
    errors: list[str]
    warnings: list[str]
    estimated_cycles: int = 0
    estimated_duration_min: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def workspace_dir_errors(
    workspace_dir: str, *, require_exists: bool = True
) -> list[str]:
    """Path-safety for a bound workspace — must be an absolute, non-sensitive dir.
    Existence is a HARD error only when ``require_exists`` (launch / PUT-bind); at
    CREATE the dir may not exist yet (a draft picks/creates it before launch), so the
    caller defers it to a warning. Shared by every workspace-binding entry point."""
    workspace_dir = (workspace_dir or "").strip()
    if not workspace_dir:
        return []
    user_path = os.path.expanduser(workspace_dir)
    expanded = os.path.realpath(user_path)
    if not os.path.isabs(user_path):
        return ["Workspace directory must be an absolute path."]
    if is_sensitive_path(expanded) or is_system_path(expanded):
        return ["Workspace directory points to a system or sensitive location."]
    if os.path.exists(expanded) and not os.path.isdir(expanded):
        return ["Workspace directory path is a file, not a directory."]
    if require_exists and not os.path.isdir(expanded):
        return ["Workspace directory does not exist."]
    return []


def workspace_write_target_errors(workspace_dir: str) -> list[str]:
    """Path-safety for a bound workspace Gideon will WRITE generated files into.

    One case stricter than :func:`workspace_dir_errors`: it also refuses the user's HOME
    directory itself. The base helper already rejects a relative path, a credential dir
    (``~/.ssh``, ``~/.aws`` …) and an OS/system root (``/``, ``/etc`` …) — but the bare home
    dir is none of those, yet dropping ``CLAUDE.md`` / ``AGENTS.md`` / ``.cursorrules``
    straight into ``$HOME`` is exactly the accident #358 guards against. Composes the base
    helper with ``require_exists=False`` (a project may bind a dir created before the first
    write), then adds the home check on the realpath'd value so a ``..``/symlink form cannot
    slip past. Returns ``[]`` for an empty binding — clearing a workspace is legal. Shared by
    the bind-time guard (``HierarchyStore``) and the regenerate-time guard so the two surfaces
    can never drift on what counts as an unsafe write root.
    """
    workspace_dir = (workspace_dir or "").strip()
    if not workspace_dir:
        return []
    errors = workspace_dir_errors(workspace_dir, require_exists=False)
    user_path = os.path.expanduser(workspace_dir)
    try:
        resolved = os.path.realpath(user_path)
        home = os.path.realpath(os.path.expanduser("~"))
    except (OSError, ValueError):
        resolved, home = user_path, os.path.expanduser("~")
    if resolved == home:
        msg = "Workspace directory cannot be your home directory itself."
        if msg not in errors:
            errors.append(msg)
    return errors


def numeric_and_boolean_field_errors(
    config: dict, *, present_only: bool = False
) -> list[str]:
    """The numeric/boolean spec screens shared by the create gate and the PUT
    spec edit. ``present_only`` (the edit) checks only fields the patch carries;
    the create gate re-runs its own richer max_cycles block in :func:`validate`
    and uses this for the boolean floor."""
    cfg = AppConfig.load().loops
    errors: list[str] = []
    if "max_cycles" in config or not present_only:
        raw = config.get("max_cycles", 0)
        if raw not in (None, ""):
            n = _as_int(raw)
            if n is None:
                errors.append("max_cycles must be a whole number.")
            elif n < 0:
                errors.append("Max cycles cannot be negative (0 means uncapped).")
            elif n > cfg.max_cycles_hard_cap:
                errors.append(
                    f"Max cycles cannot exceed the hard cap of {cfg.max_cycles_hard_cap}."
                )
    if "idle_secs" in config and config.get("idle_secs") not in (None, ""):
        idle = _as_int(config.get("idle_secs"))
        if idle is None or idle < 0:
            errors.append("idle_secs must be a non-negative whole number.")
    for f in ("attended", "autopilot", "auto_teardown_on_complete"):
        if f in config and not isinstance(config.get(f), bool):
            errors.append(
                f"'{f}' must be a boolean (true/false) — a quoted string like "
                f'"false" would silently read as true.'
            )
    return errors


def spec_edit_errors(
    body: dict, *, kind: str, existing_kind_config: dict | None = None
) -> list[str]:
    """Security-relevant checks for a PUT spec edit — mirrors the create gate so an
    edit can't smuggle in what create rejects (a sensitive/relative workspace_dir, or
    a destructive verify/test command). Only fields present in ``body`` are checked.
    A flat ``verify_command``/``test_command`` or a whole ``kind_config`` patch both
    route through the kind's ``validate_config`` (errors only — warnings don't block
    an edit). ``existing_kind_config`` lets the kind see the merged config."""
    from gideon.automation.loop import kinds

    kinds.ensure_loaded()
    errors: list[str] = []
    errors.extend(numeric_and_boolean_field_errors(body, present_only=True))
    if "workspace_dir" in body:
        errors.extend(
            workspace_dir_errors(
                str(body.get("workspace_dir") or ""), require_exists=False
            )
        )
    touches_cfg = (
        "kind_config" in body or "verify_command" in body or "test_command" in body
    )
    if touches_cfg:
        merged = dict(existing_kind_config or {})
        if isinstance(body.get("kind_config"), dict):
            merged.update(body["kind_config"])
        for f in ("verify_command", "test_command"):
            if f in body:
                merged[f] = body[f]
        strat = kinds.get_or_none(kind)
        hook = getattr(strat, "validate_config", None) if strat else None
        if hook is not None:
            try:
                k_errors, _warnings = hook({"kind_config": merged})
                errors.extend(k_errors)
            except Exception:
                import logging

                logging.getLogger(__name__).debug(
                    "kind %s validate_config (edit) errored", kind, exc_info=True
                )
    return errors


def validate(config: dict, *, agent_exists: bool = True) -> ValidationResult:
    """Deterministic pre-flight on a unified loop-creation payload. ``agent_exists``
    is supplied by the HTTP layer (validation stays free of the agent registry)."""
    from gideon.automation.loop import kinds

    kinds.ensure_loaded()
    cfg = AppConfig.load().loops
    errors: list[str] = []
    warnings: list[str] = []

    task = str(config.get("task") or config.get("goal") or "").strip()
    if len(task) < _MIN_TASK_LEN:
        errors.append(
            f"Task is too vague — describe it in more detail (min {_MIN_TASK_LEN} characters)."
        )
    elif len(task) > _MAX_TASK_LEN:
        errors.append(
            f"Task is too large ({len(task):,} characters) — trim it to under "
            f"{_MAX_TASK_LEN:,} (link or summarize a big document instead of pasting it whole)."
        )

    max_cycles_raw = config.get("max_cycles", 0)
    max_cycles = _as_int(max_cycles_raw) if max_cycles_raw not in (None, "") else 0
    if max_cycles is None:
        errors.append("Cycle budget must be a whole number.")
        max_cycles = 0
    elif max_cycles < 0:
        errors.append("Cycle budget cannot be negative (use 0 for an ongoing loop).")
    elif max_cycles > cfg.max_cycles_hard_cap:
        errors.append(
            f"Max cycles cannot exceed the hard cap of {cfg.max_cycles_hard_cap}."
        )
    elif max_cycles > 50:
        low, high = max_cycles * 0.10, max_cycles * 0.30
        warnings.append(
            f"High cycle count ({max_cycles}). Estimated cost: ~${low:.2f}–${high:.2f}."
        )

    if (
        "idle_secs" in config
        and config.get("idle_secs") not in (None, "")
        and _as_int(config.get("idle_secs")) is None
    ):
        errors.append("Idle timeout must be a whole number of seconds.")

    errors.extend(
        e
        for e in numeric_and_boolean_field_errors(config, present_only=True)
        if "must be a boolean" in e
    )

    ws = str(config.get("workspace_dir") or "")
    errors.extend(workspace_dir_errors(ws, require_exists=False))
    if (
        ws.strip()
        and not workspace_dir_errors(ws, require_exists=False)
        and workspace_dir_errors(ws, require_exists=True)
    ):
        warnings.append(
            "Workspace directory does not exist yet — create or pick it before launching."
        )

    if not agent_exists:
        errors.append("Selected worker agent does not exist.")

    kind = str(config.get("kind", "goal")).strip().lower() or "goal"
    strat = kinds.get_or_none(kind)
    hook = getattr(strat, "validate_config", None) if strat else None
    if hook is not None:
        try:
            k_errors, k_warnings = hook(config)
            errors.extend(k_errors)
            warnings.extend(k_warnings)
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "kind %s validate_config errored", kind, exc_info=True
            )

    effective_cycles = max_cycles or cfg.max_cycles_hard_cap
    return ValidationResult(
        can_start=not errors,
        errors=errors,
        warnings=warnings,
        estimated_cycles=effective_cycles,
        estimated_duration_min=effective_cycles * 2,
    )
