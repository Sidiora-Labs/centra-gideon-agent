"""Canonical persisted conversation key resolution shared by runtime consumers."""

from __future__ import annotations


def _history_key_for(session_name: str) -> str:
    """Canonical history key for a DASHBOARD chat session.

    Dashboard sessions live under the ``dashboard:`` namespace; a ``dashboard_``
    filename form normalizes to it. This helper is for dashboard-native session
    ids only — it does NOT know about channel-provider threads (those persist +
    resolve under their own bare provider key; see ``resolve_history_key``)."""
    from gideon.core.constants import DASHBOARD_SESSION_PREFIX, dashboard_session_key

    if session_name.startswith(DASHBOARD_SESSION_PREFIX):
        return session_name
    while session_name.startswith("dashboard_"):
        session_name = session_name[len("dashboard_") :]
    return dashboard_session_key(session_name)


def candidate_history_keys(session_name: str) -> tuple[str, ...]:
    """Every key *session_name*'s conversation log could live under, in resolution order.

    The one place that knows a session's possible key SHAPES: the bare key (a
    channel-provider thread persists under its own key, exactly as the channel app wrote
    it) and the ``dashboard:`` form. Both :func:`resolve_history_key` and
    ``chat_persistence.session_key_exists`` used to build this pair inline, which is how
    "which files belong to this key" came to have two implementations that had to be kept
    in agreement by hand. Callers that need ONE key want
    :func:`persisted_history_key`; this is for the probes that must try all of them.
    """
    dash = _history_key_for(session_name)
    return (session_name,) if dash == session_name else (session_name, dash)


def resolve_history_key(conversation_log, session_name: str) -> str | None:
    """Provider-agnostically resolve the canonical persisted key for *session_name*.

    A chat session is either a dashboard-native session (persisted under the
    ``dashboard:`` namespace) or a CHANNEL-PROVIDER thread (Slack/Discord/…),
    which persists under its OWN bare key exactly as the channel app wrote it.
    Core must not assume a key SHAPE (no provider-specific pattern) — it just asks
    the conversation log which key actually has metadata:

      1. the key as given (a channel thread key is canonical as-is), then
      2. the dashboard-namespaced form (a dashboard session).

    Returns the key that has persisted metadata, or ``None`` if neither does."""
    if conversation_log is None:
        return None
    for candidate in candidate_history_keys(session_name):
        try:
            if conversation_log.get_metadata(candidate):
                return candidate
        except Exception:
            continue
    return None


def persisted_history_key(conversation_log, session_name: str) -> str:
    """THE owner of a chat session's ON-DISK identity: the key its file lives under.

    :func:`_history_key_for` answers a different, weaker question — "what does the
    dashboard namespace look like for this name?" It PREFIXES; it never looks at the
    disk. Passing its answer to a ``conversation_log`` read or write asserts a key
    SHAPE, and a channel-provider thread (which persists under its own bare key,
    exactly as the channel app wrote it) does not have that shape. The read then finds
    nothing and the write lands in a second, empty file beside the real transcript.

    :func:`resolve_history_key` is the part that actually knows: it asks the log which
    candidate key HAS metadata. But it answers ``None`` for a session with nothing
    persisted yet, which a reader wants (``None`` = "never persisted, do not
    materialise a phantom") and a writer does not (a brand-new session must still get
    a file). So the write side needs the resolution AND a fallback, and it had grown a
    copy-pasted three-line idiom in two places —
    ``resolve_history_key(log, k) or _history_key_for(k)`` in
    ``save_session_to_history`` and again in ``api_chat_session_detail`` — while eight
    other call sites simply skipped it and hand-formed the prefix. A duplicated idiom
    is not an owner; this function is, and every keyed ``conversation_log`` access in
    ``dashboard/`` routes through it or through ``resolve_history_key`` directly
    (``tests/test_session_key_one_owner_audit.py`` reds on a new bypass).

    Returns the resolved persisted key, falling back to the dashboard-namespaced form
    when nothing is persisted under either candidate — so for a never-yet-saved
    session the answer is byte-identical to the old hand-formed one, and the behaviour
    changes ONLY where a file already exists under a key the prefix would have missed.
    """
    return resolve_history_key(conversation_log, session_name) or _history_key_for(
        session_name
    )
