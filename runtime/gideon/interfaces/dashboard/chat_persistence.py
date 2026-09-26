"""Session persistence — save, restore, history prefix."""

import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import AppConfig
from gideon.engine.agent import AGENTS_DIR
from gideon.engine.task_modes import VALID_TASK_MODES
from gideon.interfaces.dashboard.chat_utils import (
    _normalize_model,
    _sync_dashboard_sessions,
    apply_task_mode,
    candidate_history_keys,
    persisted_history_key,
    resolve_history_key,
)
from gideon.interfaces.dashboard.state import (
    ConsoleState,
    _ChatSession,
    resolve_session_workspace_path,
)
from gideon.security.security import redact_credentials, redact_exfiltration_urls


def _load_providers_raw() -> list[dict]:
    """Load the raw providers array from config.json."""
    try:
        from gideon.core.config.loader import config_path

        path = config_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("providers", [])
    except Exception:
        pass
    return []


def _build_agent_model_map() -> dict[str, str]:
    """Map each agent's name and file-stem to its configured model.

    Lets sessions without a persisted ``model`` resolve the model their agent
    would use. Keyed by both ``name`` and filename stem so either form found in
    session metadata resolves.
    """
    model_map: dict[str, str] = {}
    try:
        for f in AGENTS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                model = data.get("model", "")
                if data.get("name"):
                    model_map[data["name"]] = model
                model_map[f.stem] = model
            except (json.JSONDecodeError, OSError):
                continue
    except Exception:
        logger.debug("Failed to build agent model map", exc_info=True)
    return model_map


def _active_provider_model() -> str:
    """Return the model configured on the active provider, or '' if unavailable."""
    providers = _load_providers_raw()
    if providers:
        return providers[0].get("model", "")
    return ""


def _model_matches_provider(model: str) -> bool:
    """Check if a persisted session model is compatible with the active provider.

    On restore, a session may carry a model pinned by a provider the user has since
    swapped out (e.g. a ``claude-*`` model when the active provider now speaks a
    different family) — that model would fail at call time, so the caller replaces
    it with the active provider's model instead.

    The check is provider-agnostic: it asks whether the active provider TYPE serves
    the model's family, using the shared, data-driven family→type map in
    :func:`gideon.integrations.llm.catalog.model_family_provider_types` (no vendor name is
    hard-coded here). When the family is unknown, or the active provider's type isn't
    in the map, the model is accepted (permissive — never strand a restorable model
    on a guess)."""
    if not model:
        return True
    from gideon.integrations.llm.catalog import model_family_provider_types

    owning_types = model_family_provider_types(model)
    if not owning_types:
        return True
    providers = _load_providers_raw()
    if not providers:
        return True
    active_type = providers[0].get("type", "")
    return active_type in owning_types or active_type == "acp"


def _redact_value(v: object) -> object:
    """Redact a single value recursively (str, dict, list, or passthrough)."""
    if isinstance(v, str):
        v, _ = redact_exfiltration_urls(v)
        v, _ = redact_credentials(v)
        return v
    if isinstance(v, dict):
        return _redact_meta(v)
    if isinstance(v, list):
        return [_redact_value(i) for i in v]
    return v


def _redact_meta(meta: dict) -> dict:
    """Recursively redact string values in meta dict (credentials + URLs)."""
    out: dict = {}
    for k, v in meta.items():
        out[k] = _redact_value(v)
    return out


logger = logging.getLogger(__name__)

_MAX_HISTORY_CHARS = 8000

# Reasoning-effort is no longer a fixed Gideon scale — each backend declares its
_REASONING_EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")


def resolve_tool_result_path(session_id: str, result_id: str) -> Path | None:
    """Resolve a tool-result record without creating its session workspace."""
    if not result_id or "/" in result_id or ".." in result_id:
        return None
    try:
        return (
            resolve_session_workspace_path(session_id)
            / "tool_results"
            / f"{result_id}.json"
        )
    except (TypeError, ValueError):
        return None


def purge_session_workspace(session_id: str) -> bool:
    """Remove an existing session workspace without materializing an absent one."""
    try:
        path = resolve_session_workspace_path(session_id)
        if not path.is_dir():
            return False
        shutil.rmtree(path)
    except (OSError, TypeError, ValueError):
        logger.debug("session workspace purge failed for %s", session_id, exc_info=True)
        return False
    return not path.exists()


def _validate_reasoning_effort(raw: object) -> str:
    """Return *raw* if it's a safe reasoning_effort token, else "".

    Enforces a format (not a fixed value set) so any backend-declared effort is
    accepted while a tampered/corrupted metadata file cannot smuggle spaces or
    shell metacharacters into a subprocess ``--effort`` arg / config value.
    """
    if raw == "" or raw is None:
        return ""
    if isinstance(raw, str) and _REASONING_EFFORT_RE.match(raw):
        return raw
    if raw:
        logger.warning("Discarding invalid persisted reasoning_effort: %r", raw)
    return ""


_NON_TRANSCRIPT_ROLES = frozenset(
    {"chunk", "done", "streaming", "queued", "permission"}
)


def _persistable(msgs: list[dict]) -> list[dict]:
    """The subset of *msgs* that :func:`save_session_to_history` will actually write."""
    return [m for m in msgs if m.get("role", "assistant") not in _NON_TRANSCRIPT_ROLES]


def _persisted_message_count(state: ConsoleState, history_key: str) -> int:
    """How many transcript messages the PERSISTED side already holds for *history_key*.

    The disk-side half of "does this buffer hold more than the file does?" — the
    question :func:`save_session_to_history` has to answer before overwriting a
    transcript, and which it used to answer from ``session._resumed_count``, an
    IN-MEMORY count. Reads the single file the write is about to replace (not
    ``read_messages_chained``, which spans rotated siblings and would over-count into a
    permanent refusal). ``ConversationLog`` mtime-caches the parse, so on the hot path
    this is a ``stat``.

    An unreadable file answers ``0`` — fail OPEN, never refuse a live write because the
    disk misbehaved. Same posture as :func:`session_key_exists`, for the same reason.
    """
    log = state.conversation_log
    if log is None:
        return 0
    try:
        return len(log.read_messages(history_key))
    except Exception:  # noqa: BLE001 — an unreadable log must not block a live write
        logger.warning(
            "persisted message count failed for %s", history_key, exc_info=True
        )
        return 0


def save_all_sessions_to_history(state: ConsoleState) -> None:
    """Save all active sessions to history. Called on gateway shutdown."""
    for session in list(state._sessions.values()):
        if not _persistable(session.messages):
            continue
        try:
            save_session_to_history(state, session, force=True)
        except Exception:
            logger.error(
                "Shutdown: failed to save session %s", session.key, exc_info=True
            )


def _attach_variants(session: _ChatSession, m: dict) -> None:
    """Copy variant history from a persisted message onto the session's last message, with redaction."""  # noqa: E501
    if m.get("variants"):
        session.messages[-1]["variants"] = [  # type: ignore[assignment]
            {
                **v,
                "content": redact_credentials(
                    redact_exfiltration_urls(v.get("content", ""))[0]
                )[0],
            }
            for v in m["variants"]
            if isinstance(v, dict)
        ]
        session.messages[-1]["variant_idx"] = m.get("variant_idx", 0)


def _redact_snapshot_message(m: dict) -> dict:
    """Redact one message dict inside a rewind tail snapshot.

    Keeps only the fields the read-only tail viewer renders (role, content, ts,
    cls, meta) and applies the same credential/URL passes as the main transcript
    for non-user roles.
    """
    role = m.get("role", "assistant")
    content = m.get("content", "")
    if role not in ("user", "system"):
        content, _ = redact_exfiltration_urls(content)
        content, _ = redact_credentials(content)
    out: dict = {"role": role, "content": content, "ts": m.get("ts", "")}
    if m.get("cls"):
        out["cls"] = m["cls"]
    if m.get("meta"):
        out["meta"] = _redact_meta(m["meta"])
    return out


def _redact_rewound(raw: object) -> list[dict]:
    """Redact a rewind-tail chain for persistence/rehydration.

    Tolerant of the pre-rewind shape (non-list / missing key → empty). Each entry
    is ``{"messages": [<redacted msg dicts>], "ts": <ISO>}``.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for snap in raw:
        if not isinstance(snap, dict):
            continue
        snap_msgs = snap.get("messages")
        if not isinstance(snap_msgs, list):
            continue
        out.append(
            {
                "messages": [
                    _redact_snapshot_message(sm)
                    for sm in snap_msgs
                    if isinstance(sm, dict)
                ],
                "ts": snap.get("ts", ""),
            }
        )
    return out


def _attach_rewound(session: _ChatSession, m: dict) -> None:
    """Copy a rewind tail chain onto the session's last message, tolerantly.

    Missing/old-shape key = today's behaviour (pre-rewind sessions load
    unchanged — the plan's one clean-break field under the pre-1.0 banner).
    """
    out = _redact_rewound(m.get("rewound"))
    if out:
        session.messages[-1]["rewound"] = out


def _restore_runtime_binding(
    state: ConsoleState, session: _ChatSession, meta: dict
) -> None:
    """Restore a session's RUNTIME binding from its persisted metadata line.

    One helper for both restore paths — the bulk startup restore
    (``restore_recent_sessions``) and the targeted single-session rehydrate — because
    they had silently drifted: only the targeted path read ``acp_provider``, so a
    gateway restart, which goes through the BULK path, always brought the session back
    on the native axis. Two independent readers of one contract is how a restore ends
    up half-implemented; there is now one.

    ``_acp_meta_binding`` records what the meta line ASKED the runtime to be, whether
    or not the binding was honoured, so the first turn after a restore can say so
    rather than resolving on a different axis in silence. The task mode goes through
    ``apply_task_mode`` (never a bare attribute write) because the mode is TWO writes —
    the session's posture and the runtime's tool gate — and restoring only the first
    would bring back a "plan" session whose tools still run.
    """
    _acp_prov = meta.get("acp_provider")
    if isinstance(_acp_prov, str) and _acp_prov:
        session._acp_meta_binding = _acp_prov
        if _acp_prov.startswith("acp:"):
            session.acp_provider = _acp_prov
            _acp_pa = meta.get("acp_provider_agent")
            session.acp_provider_agent = _acp_pa if isinstance(_acp_pa, str) else ""
    if meta.get("workspace_dir"):
        session.workspace_dir = meta["workspace_dir"]
    _pid = meta.get("project_id")
    if isinstance(_pid, str) and _pid:
        session.project_id = _pid
    if meta.get("mode"):
        session.mode = meta["mode"]
    _tm = meta.get("task_mode")
    if isinstance(_tm, str) and _tm in VALID_TASK_MODES:
        apply_task_mode(state, session, _tm)


def _rehydrate_session_from_history(
    state: ConsoleState, session_name: str, *, include_archived: bool = False
) -> _ChatSession | None:
    """Rehydrate a single dashboard session from persisted history.

    Unlike ``state.get_or_create_session`` (which creates a fresh, empty session with
    default ``memory_mode='persistent'``), this helper reads the session's
    metadata and messages from ``conversation_log`` so the restored session has
    the original title/agent/model/memory_mode and its message history
    populated. Returns ``None`` if the session does not exist on disk (so
    callers can fall through to other delivery paths without creating a
    phantom empty tab).

    Intended for targeted resume paths (e.g. cron→origin injection after
    gateway restart). Bulk startup restore still uses ``restore_recent_sessions``.

    *include_archived* loads a session whose metadata carries ``closed``. It exists for
    exactly one caller — the ``POST /api/chat`` SEED, which has already asked
    :func:`session_key_exists` "may I write this key?" and been told yes, because
    "archival is not deletion" and an archived session stays WRITABLE. Writable but not
    SEEDABLE is incoherent, and it is the incoherence that caused the data loss: the
    seed returned ``None``, ``get_or_create_session`` minted a BLANK session for a key
    holding a real transcript, and the save then wrote that blank buffer over it.
    Readers keep the default (``False``) — for them ``closed`` still means "not
    resident", which is what makes an archived chat absent from the UI.
    """
    if not state.conversation_log:
        return None
    if session_name in state._sessions:
        return state._sessions[session_name]
    history_key = resolve_history_key(state.conversation_log, session_name)
    if not history_key:
        return None
    meta = state.conversation_log.get_metadata(history_key)
    if not meta:
        return None
    if meta.get("closed") and not include_archived:
        return None
    try:
        _restore_cfg = AppConfig.load()
    except Exception:
        _restore_cfg = None
    provider_model_map = _build_agent_model_map()
    session = state.get_or_create_session(session_name)
    sessions = state.conversation_log.list_sessions()
    session_info = next(
        (s for s in sessions if s.get("key") == history_key),
        {},
    )
    raw_title = session_info.get("title") or meta.get("title") or session_name
    raw_title, _ = redact_exfiltration_urls(raw_title)
    raw_title, _ = redact_credentials(raw_title)
    session.title = raw_title
    session._titled = bool(session_info.get("title") or meta.get("title"))
    if meta.get("created_at"):
        session.created_at = meta["created_at"]
    if meta.get("agent"):
        session.agent = meta["agent"]
    if meta.get("model"):
        normalized = _normalize_model(meta["model"])
        if _model_matches_provider(normalized):
            session.model = normalized
        else:
            session.model = _active_provider_model()
    elif session.agent:
        try:
            pc = _restore_cfg.agents.get(session.agent) if _restore_cfg else None
            provider_name = (
                pc.provider_agent if pc and pc.provider_agent else session.agent
            )
            session.model = provider_model_map.get(provider_name, "")
        except Exception:
            logger.debug(
                "Failed to resolve model for rehydrated session %s",
                session_name,
                exc_info=True,
            )
    if meta.get("reasoning_effort"):
        session.reasoning_effort = _validate_reasoning_effort(meta["reasoning_effort"])
    _restore_runtime_binding(state, session, meta)
    if meta.get("folder_id"):
        session.folder_id = meta["folder_id"]
    if meta.get("pinned"):
        session.pinned = True
    if meta.get("color_index") is not None:
        session.color_index = meta["color_index"]
    if meta.get("natural_voice"):
        from gideon.integrations.natural_voice import normalize_conversation_choice

        session.natural_voice = normalize_conversation_choice(meta["natural_voice"])
    raw_tags = meta.get("tags")
    if isinstance(raw_tags, list):
        session.tags = [str(t) for t in raw_tags if isinstance(t, str) and t]
    _lc = meta.get("lifecycle")
    if isinstance(_lc, str) and _lc in ("active", "archived"):
        session.lifecycle = _lc
    _la = meta.get("last_activity_at")
    if isinstance(_la, (int, float)):
        session.last_activity_at = float(_la)
    if meta.get("never_archive"):
        session.never_archive = True
    mm = meta.get("memory_mode", "persistent")
    session.memory_mode = mm
    if mm != "persistent":
        state._restricted_keys.add(f"dashboard:{session_name}")
    if meta.get("forked_from") is not None:
        session.forked_from = meta["forked_from"]
    _side_meta = meta.get("side")
    if isinstance(_side_meta, dict) and _side_meta.get("messages"):
        from gideon.interfaces.dashboard.side_state import SideState

        session._side = SideState.from_dict(_side_meta)
    messages = state.conversation_log.read_messages_chained(history_key)
    for m in messages:
        role = m.get("role", "assistant")
        cls = m.get("cls") or ("msg msg-u" if role == "user" else "msg msg-a")
        content = m.get("content", "")
        if role != "user":
            content, _ = redact_exfiltration_urls(content)
            content, _ = redact_credentials(content)
        session.append(
            role,
            content,
            cls,
            ts=m.get("ts", ""),
            meta=_redact_meta(m["meta"]) if m.get("meta") else None,
        )
        _attach_variants(session, m)
        _attach_rewound(session, m)
    session.drain()
    session._resumed_count = len(session.messages)
    session._dirty = False
    logger.info("Rehydrated session %s (%s) from history", session_name, session.title)
    return session


def resolve_session(state: ConsoleState, name: str):
    """Return the in-memory session for *name*, rehydrating from disk on miss.

    Org actions (tag/folder/pin/color) target sessions chosen from the chat
    history list, which now surfaces disk-only sessions (not in memory after a
    restart). A bare ``state._sessions.get`` 404s on those, so editing tags on
    an older chat silently fails. Returns None only if never persisted.
    """
    return state._sessions.get(name) or _rehydrate_session_from_history(state, name)


def session_key_exists(state: ConsoleState, name: str) -> bool:
    """THE owner of "does this session key exist, and may it be written?".

    ``True`` when *name* addresses a real chat session — resident in
    ``state._sessions``, or holding a conversation log FILE on disk. ``False`` when the
    key names nothing at all: never created, or hard-deleted by
    ``api_chat_session_delete`` (which unlinks that file).

    **Why a writer needs this and cannot just call get_or_create_session.**
    ``state.get_or_create_session`` mints a blank session on a name MISS — which is
    correct for the paths that own creation (``POST /api/chat/sessions``, a fork, a
    channel thread, a loop) and wrong for every path where the name arrived from a
    CLIENT. There, a miss means the client is addressing something that is not there,
    and creating it turns "delete" into "close": a stale tab, a retried request or a
    queued send re-materialises a conversation the user destroyed, minus everything
    the original carried. Measured before this helper existed: ``POST /api/chat``
    naming a hard-deleted key answered **200** and put the key back in
    ``GET /api/chat/sessions`` with an empty transcript.

    **The predicate is FILE PRESENCE, and deliberately neither of its two neighbours.**
    Not :func:`resolve_session` — that answers ``None`` for an ARCHIVED (``closed``)
    session, because a soft-closed chat is not resident; but archival is not deletion,
    ``api_chat_session_resume`` exists to reopen one, and refusing a write there would
    be a brand-new refusal on live user data. And not "has readable metadata" either:
    :func:`resolve_history_key` collapses "no metadata" and "I could not READ the
    metadata" into the same ``None``, so asking it would 404 a session whose file is
    right there the moment a read fails — measured with a ``get_metadata`` that raises
    ``OSError``. So the question is put to :meth:`ConversationLog.has_log`, a bare
    ``Path.exists()`` that no unreadable byte and no corrupt first line can defeat.
    A broken disk degrades to the old permissive behaviour; a hard-deleted key, whose
    file is gone, is still refused.

    Provider-agnostic without assuming a key SHAPE: both candidate keys are tried — the
    bare one (a channel-provider thread persists under its own key, exactly as the
    channel app wrote it) and the ``dashboard:`` form. The pair now comes from
    :func:`~gideon.interfaces.dashboard.chat_utils.candidate_history_keys`, which
    :func:`resolve_history_key` reads too, so the two cannot disagree about which files
    belong to a key — it used to be built inline in both places.
    """
    if name in state._sessions:
        return True
    try:
        log = state.conversation_log
        if log is None:
            return True
        return any(log.has_log(k) for k in candidate_history_keys(name))
    except Exception:  # noqa: BLE001 — an unreadable log must not refuse a live send
        logger.warning("session existence check failed for %s", name, exc_info=True)
        return True


def restore_recent_sessions(
    state: ConsoleState, window_minutes: int = 30, *, folders_only: bool = False
) -> int:
    """Restore sessions as chat sessions."""
    if not state.conversation_log:
        return 0
    cutoff = time.time() - (window_minutes * 60) if window_minutes > 0 else None
    restored = 0

    provider_model_map = _build_agent_model_map()
    try:
        _restore_cfg = AppConfig.load()
    except Exception:
        _restore_cfg = None
    for s in state.conversation_log.list_sessions():
        key = s.get("key", "")
        if key.startswith("dashboard:"):
            session_name = key.removeprefix("dashboard:")
        elif key.startswith("dashboard_"):
            session_name = key.removeprefix("dashboard_")
        else:
            continue
        if session_name in state._sessions:
            continue
        meta = state.conversation_log.get_metadata(key)
        has_folder = bool(meta.get("folder_id"))
        has_pin = bool(meta.get("pinned"))
        if folders_only and not has_folder and not has_pin:
            continue
        if meta.get("closed"):
            continue
        if not has_folder and not has_pin:
            if cutoff is not None and s.get("modified", 0) < cutoff:
                continue
        session = state.get_or_create_session(session_name)
        raw_title = s.get("title", session_name)
        raw_title, _ = redact_exfiltration_urls(raw_title)
        raw_title, _ = redact_credentials(raw_title)
        session.title = raw_title
        session._titled = bool(s.get("title"))
        if meta.get("created_at"):
            session.created_at = meta["created_at"]
        if meta.get("agent"):
            session.agent = meta["agent"]
        if meta.get("model"):
            normalized = _normalize_model(meta["model"])
            if _model_matches_provider(normalized):
                session.model = normalized
            else:
                session.model = _active_provider_model()
        elif session.agent:
            try:
                pc = _restore_cfg.agents.get(session.agent) if _restore_cfg else None
                provider_name = (
                    pc.provider_agent if pc and pc.provider_agent else session.agent
                )
                session.model = provider_model_map.get(provider_name, "")
            except Exception:
                logger.debug(
                    "Failed to resolve model for restored session %s",
                    session_name,
                    exc_info=True,
                )
        if meta.get("reasoning_effort"):
            session.reasoning_effort = _validate_reasoning_effort(
                meta["reasoning_effort"]
            )
        _restore_runtime_binding(state, session, meta)
        if meta.get("folder_id"):
            session.folder_id = meta["folder_id"]
        if meta.get("pinned"):
            session.pinned = True
        if meta.get("color_index") is not None:
            session.color_index = meta["color_index"]
        if meta.get("color_theme"):
            session.color_theme = meta["color_theme"]
        if meta.get("natural_voice"):
            from gideon.integrations.natural_voice import normalize_conversation_choice

            session.natural_voice = normalize_conversation_choice(meta["natural_voice"])
        raw_tags = meta.get("tags")
        if isinstance(raw_tags, list):
            session.tags = [str(t) for t in raw_tags if isinstance(t, str) and t]
        _lc = meta.get("lifecycle")
        if isinstance(_lc, str) and _lc in ("active", "archived"):
            session.lifecycle = _lc
        _la = meta.get("last_activity_at")
        if isinstance(_la, (int, float)):
            session.last_activity_at = float(_la)
        if meta.get("never_archive"):
            session.never_archive = True
        mm = meta.get("memory_mode", "persistent")
        session.memory_mode = mm
        if mm != "persistent":
            state._restricted_keys.add(f"dashboard:{session_name}")
        if meta.get("forked_from") is not None:
            session.forked_from = meta["forked_from"]
        _side_meta = meta.get("side")
        if isinstance(_side_meta, dict) and _side_meta.get("messages"):
            from gideon.interfaces.dashboard.side_state import SideState

            session._side = SideState.from_dict(_side_meta)
        tab_id = meta.get("tab_id")
        if not tab_id:
            tab_id = uuid.uuid4().hex[:12]
            state.conversation_log.update_metadata(key, {"tab_id": tab_id})
        session._tab_id = tab_id
        messages = state.conversation_log.read_messages_chained(key)
        for m in messages:
            role = m.get("role", "assistant")
            cls = m.get("cls") or ("msg msg-u" if role == "user" else "msg msg-a")
            content = m.get("content", "")
            if role != "user":
                content, _ = redact_exfiltration_urls(content)
                content, _ = redact_credentials(content)
            session.append(
                role,
                content,
                cls,
                ts=m.get("ts", ""),
                meta=_redact_meta(m["meta"]) if m.get("meta") else None,
            )
            _attach_variants(session, m)
            _attach_rewound(session, m)
        session.drain()
        session._resumed_count = len(session.messages)
        session._dirty = False
        restored += 1
        logger.info("Restored session %s (%s)", session_name, session.title)
    _sync_dashboard_sessions(state)
    return restored


def save_session_to_history(
    state: ConsoleState,
    session: _ChatSession,
    messages: list[dict] | None = None,
    *,
    closed: bool = False,
    force: bool = False,
    metadata_only: bool = False,
) -> None:
    """Persist session messages to JSONL history.

    Public because it is re-exported as `gideon.sdk.channel.save_session_to_history`:
    a channel app that mutates a linked session out-of-band (an interactive option pick,
    a link/unlink) has to flush it, or the thread it just changed is lost on restart.

    ``metadata_only`` preserves an existing transcript byte-for-byte while replacing
    its metadata header. If no history exists yet, the current transcript seeds it.
    """
    msgs = messages if messages is not None else session.messages
    if not state.conversation_log:
        return
    history_key = persisted_history_key(state.conversation_log, session.key)
    outgoing = _persistable(msgs)
    if not outgoing and not (force or metadata_only):
        return
    if not force and not metadata_only:
        _persisted = _persisted_message_count(state, history_key)
        if _persisted and len(outgoing) <= _persisted:
            if closed:
                try:
                    state.conversation_log.update_metadata(
                        history_key, {"closed": True}
                    )
                except Exception:
                    logger.warning(
                        "archive flag write failed for %s", history_key, exc_info=True
                    )
            else:
                logger.debug(
                    "save skipped for %s: buffer holds %d transcript message(s), disk holds %d",
                    history_key,
                    len(outgoing),
                    _persisted,
                )
            return
    try:
        existing_meta = state.conversation_log.get_metadata(history_key)

        path = state.conversation_log._path(history_key)
        preserved_lines: list[str] | None = None
        if path.exists() and (metadata_only or not outgoing):
            persisted_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if persisted_lines:
                try:
                    persisted_header = json.loads(persisted_lines[0])
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"cannot safely update metadata for {history_key}: invalid header"
                    ) from exc
                if persisted_header.get("_type") != "metadata":
                    raise ValueError(
                        f"cannot safely update metadata for {history_key}: missing header"
                    )
                preserved_lines = persisted_lines[1:]
            else:
                preserved_lines = []
        path.parent.mkdir(parents=True, exist_ok=True)
        meta_line: dict = {
            "_type": "metadata",
            "created_at": existing_meta.get("created_at") or session.created_at,
            "last_consolidated": existing_meta.get("last_consolidated", 0),
        }
        if closed or existing_meta.get("closed"):
            meta_line["closed"] = True
        meta_line["memory_mode"] = session.memory_mode
        if session.title and session.title != session.key:
            meta_line["title"] = session.title
        if session.agent:
            meta_line["agent"] = session.agent
        meta_line["model"] = session.model
        if session.reasoning_effort:
            meta_line["reasoning_effort"] = session.reasoning_effort
        if session.mode:
            meta_line["mode"] = session.mode
        if session.workspace_dir:
            meta_line["workspace_dir"] = session.workspace_dir
        if session.project_id:
            meta_line["project_id"] = session.project_id
        if session.acp_provider:
            meta_line["acp_provider"] = session.acp_provider
            if session.acp_provider_agent:
                meta_line["acp_provider_agent"] = session.acp_provider_agent
        _task_mode = getattr(session, "_task_mode", "agent") or "agent"
        if _task_mode != "agent":
            meta_line["task_mode"] = _task_mode
        if session.folder_id:
            meta_line["folder_id"] = session.folder_id
        if session.pinned:
            meta_line["pinned"] = True
        if session.color_index is not None:
            meta_line["color_index"] = session.color_index
        if session.color_theme:
            meta_line["color_theme"] = session.color_theme
        if session.natural_voice:
            meta_line["natural_voice"] = session.natural_voice
        if session.tags:
            meta_line["tags"] = list(session.tags)
        if session.lifecycle and session.lifecycle != "active":
            meta_line["lifecycle"] = session.lifecycle
        if session.last_activity_at:
            meta_line["last_activity_at"] = session.last_activity_at
        if session.never_archive:
            meta_line["never_archive"] = True
        if session.forked_from is not None:
            meta_line["forked_from"] = session.forked_from
        _side = getattr(session, "_side", None)
        if _side is not None and _side.messages:
            meta_line["side"] = _side.to_dict()
        elif existing_meta.get("side"):
            meta_line["side"] = existing_meta["side"]
        tab_id = getattr(session, "_tab_id", None) or existing_meta.get("tab_id")
        if tab_id:
            meta_line["tab_id"] = tab_id
        _app = getattr(session, "_app", "") or existing_meta.get("app", "")
        if _app:
            meta_line["app"] = _app
        lines = [json.dumps(meta_line) + "\n"]
        messages_to_write = outgoing if preserved_lines is None else []
        for m in messages_to_write:
            role = m.get("role", "assistant")
            content = m.get("content", "")
            if role not in ("user", "system"):
                content, _ = redact_exfiltration_urls(content)
                content, _ = redact_credentials(content)
            entry: dict = {
                "role": role,
                "content": content,
                "ts": m.get("ts", ""),
                "source_thread": "dashboard",
                "source_user": "dashboard",
            }
            if m.get("variants"):
                redacted_variants: list[dict] = []
                for v in m["variants"]:
                    if not isinstance(v, dict):
                        continue
                    vc = v.get("content", "")
                    vc, _ = redact_exfiltration_urls(vc)
                    vc, _ = redact_credentials(vc)
                    redacted_variants.append({**v, "content": vc})
                entry["variants"] = redacted_variants
                entry["variant_idx"] = m.get("variant_idx", 0)
            if m.get("rewound"):
                rewound = _redact_rewound(m["rewound"])
                if rewound:
                    entry["rewound"] = rewound
            cls_val = m.get("cls", "")
            if role == "system" and cls_val:
                entry["cls"] = cls_val
            if m.get("meta"):
                entry["meta"] = _redact_meta(m["meta"])
            lines.append(json.dumps(entry) + "\n")
        if preserved_lines is not None:
            lines.extend(preserved_lines)

        atomic_write(path, "".join(lines), fsync=True)
        state.conversation_log._invalidate_cache(history_key)
        state.conversation_log.invalidate_tab_id_cache()
        try:
            from gideon.engine import session_search

            session_search.index_turn(
                history_key, "", "", memory_mode=getattr(session, "memory_mode", "")
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "session search index skipped for %s", history_key, exc_info=True
            )
    except Exception:
        logger.error("Failed to save session %s to history", session.key, exc_info=True)
        raise


def _build_history_prefix(session: _ChatSession) -> str:
    """Build a condensed history prefix from session messages for session re-injection."""
    lines: list[str] = []
    total = 0
    for m in session.messages:
        role = m.get("role", "")
        if role in (
            "chunk",
            "done",
            "streaming",
            "queued",
            "permission",
            "error",
            "tool",
        ):
            continue
        label = "User" if role == "user" else "Assistant"
        text = m.get("content", "")[:500]
        line = f"{label}: {text}"
        if total + len(line) > _MAX_HISTORY_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (
        "[Previous chat history for this tab — session was reset after stop]\n"
        + "\n".join(lines)
        + "\n[End of history]\n\n"
    )
