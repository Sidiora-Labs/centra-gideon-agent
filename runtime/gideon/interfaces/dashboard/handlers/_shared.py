"""Shared helpers used across handler submodules."""

import logging
from pathlib import Path
from typing import Any

from gideon.core.cancellation import run_with_timeout
from gideon.interfaces.dashboard.state import ConsoleState

logger = logging.getLogger(__name__)


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _get_memory(state: ConsoleState):
    """Get MemoryJournal from context_builder, or create standalone.

    Ensures the vector store's embed_fn is wired from the active embedding
    model on first access — the same deferred resolution used by knowledge.
    Without this, memory writes skip embedding when the gateway boots before
    the model provider entry is registered."""
    if state.context_builder:
        mem = state.context_builder.memory
    else:
        # Fallback: create standalone MemoryJournal with an attached record store.
        if not hasattr(state, "_standalone_memory"):
            from gideon.cognition.memory import MemoryJournal
            from gideon.cognition.vector_memory import SemanticArchive

            mem = MemoryJournal()
            mem.init()
            vs = SemanticArchive()
            vs.init()
            mem.vector_store = vs
            state._standalone_memory = mem  # type: ignore[attr-defined]
        mem = state._standalone_memory  # type: ignore[attr-defined]
    if (
        hasattr(mem, "vector_store")
        and mem.vector_store
        and not mem.vector_store.embed_fn
    ):
        try:
            from gideon.integrations.embedding_providers.registry import (
                get_active_embed_fn,
            )

            embed_fn = get_active_embed_fn()
            if embed_fn:
                mem.vector_store.embed_fn = embed_fn
        except Exception:
            pass
    return mem


def _get_skills(state: ConsoleState):
    """Get ProcedureLibrary from context_builder, or create standalone."""
    if state.context_builder:
        return state.context_builder.skills
    if not hasattr(state, "_standalone_skills"):
        from gideon.extensions.skills import ProcedureLibrary

        skills = ProcedureLibrary(install_builtins=False)
        state._standalone_skills = skills  # type: ignore[attr-defined]
    return state._standalone_skills  # type: ignore[attr-defined]


def _resolve_skill_path(name: str) -> Path | None:
    """Find SKILL.md for a marketplace skill by name (supports nested paths)."""
    skills_dir = _path_home_gideon() / "skills"
    for pattern in (f"*/{name}/SKILL.md", f"packages/*/skills/*/{name}/SKILL.md"):
        for p in skills_dir.glob(pattern):
            return p
    return None


async def _list_marketplace_skills() -> list[dict[str, Any]]:
    """List skills from the marketplace using ``gideon skills list`` CLI."""
    import asyncio
    import re

    try:
        proc = await asyncio.create_subprocess_exec(
            "gideon",
            "skills",
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await run_with_timeout(proc, 15)
        if proc.returncode != 0:
            return []
    except FileNotFoundError:
        return []
    except asyncio.TimeoutError:
        return []

    result: list[dict[str, Any]] = []
    pkg = ""
    for line in stdout.decode(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith(" ") and not line.startswith("Installed"):
            pkg = stripped
            continue
        dm = re.match(r"^\s+Description:\s+(.+)", line)
        if dm and result and result[-1]["package"] == pkg:
            result[-1]["description"] = dm.group(1)
            continue
        m = re.match(r"^\s+(\S+)(?:\s+\[v[\d.]+\])?:\s+(.+)", line)
        if m:
            name = m.group(1)
            resolved = _resolve_skill_path(name)
            result.append(
                {
                    "key": f"marketplace/{name}",
                    "name": name,
                    "description": "",
                    "path": str(resolved) if resolved else "",
                    "dir": str(resolved.parent) if resolved else "",
                    "always": False,
                    "source": "marketplace",
                    "package": pkg,
                }
            )

    return result


def _resolved_memory_mode(state: ConsoleState, request: Any) -> str | None:
    """Resolve established live or durable state; absence is not persistent scope."""
    from gideon.engine import session_restrictions
    from gideon.security.approval_answer import (
        OWNER,
        UNKNOWN,
        principal_from_record,
        work_principal_of_request,
    )

    actor = work_principal_of_request(request)
    if actor.kind == UNKNOWN:
        return None
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == "dashboard:ui":
        return "persistent" if actor.kind == OWNER else None
    if session_restrictions.is_temporary(sk):
        return "temporary"
    from gideon.security.session_credentials import work_of_request

    proof = work_of_request(request)
    from gideon.security.session_credentials import admitted_memory_tool

    if proof is not None and admitted_memory_tool(proof):
        return proof.memory_mode
    lookup_key = proof.origin_session_key if proof is not None else sk
    name = lookup_key.removeprefix("dashboard:")
    session = state.get_session(name)
    if session is not None:
        if session.lifecycle != "active":
            return None
        initiator = principal_from_record(session._initiator)
        mode = session.memory_mode
    else:
        if state.conversation_log is None:
            return None
        try:
            from gideon.interfaces.dashboard.chat_utils import resolve_history_key

            key = resolve_history_key(state.conversation_log, name)
            if not key:
                return None
            meta = state.conversation_log.get_metadata(key)
            if meta.get("closed") or meta.get("lifecycle", "active") != "active":
                return None
            initiator = principal_from_record(meta.get("initiator"))
            mode = str(meta.get("memory_mode") or "")
        except Exception:
            logger.warning("session memory scope lookup failed", exc_info=True)
            return None
    # Provenance is only an extra constraint; it does not mint native scope authority.
    source_actor = proof.initiator if proof is not None else actor
    if initiator.kind == UNKNOWN or initiator != source_actor:
        return None
    if proof is not None:
        if proof.memory_mode == "temporary":
            mode = "temporary"
        elif proof.memory_mode == "incognito" and mode == "persistent":
            mode = "incognito"
    if session_restrictions.is_incognito(sk) and mode == "persistent":
        mode = "incognito"
    return mode if mode in {"persistent", "incognito", "temporary"} else None


def _is_restricted_session(state: ConsoleState, request: Any) -> bool:
    return _resolved_memory_mode(state, request) != "persistent"


def _blocks_reads_session(state: ConsoleState, request: Any) -> bool:
    return _resolved_memory_mode(state, request) not in {"persistent", "incognito"}


def _path_home_gideon():
    """Resolve Gideon home dir, honoring GIDEON_HOME."""
    try:
        from gideon.core.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".gideon"


def _session_has_persisted_history(session_name: str) -> bool:
    """Validate a readable active persistent metadata header; existence proves nothing."""
    if (
        not session_name
        or "/" in session_name
        or "\\" in session_name
        or "\x00" in session_name
        or session_name.startswith(".")
    ):
        return False
    sess_dir = _path_home_gideon() / "sessions"
    if not sess_dir.exists():
        return False

    def established(path: Path) -> bool:
        try:
            import json

            from gideon.security.approval_answer import UNKNOWN, principal_from_record

            with path.open(encoding="utf-8") as stream:
                meta = json.loads(stream.readline())
            return (
                meta.get("_type") == "metadata"
                and not meta.get("closed")
                and meta.get("memory_mode") == "persistent"
                and principal_from_record(meta.get("initiator")).kind != UNKNOWN
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    if established(sess_dir / f"{session_name}.jsonl"):
        return True
    if not session_name.startswith("dashboard_") and established(
        sess_dir / f"dashboard_{session_name}.jsonl"
    ):
        return True
    return False
