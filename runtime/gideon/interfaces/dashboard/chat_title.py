"""Title generation — auto-title and rename."""

import logging
import re

from aiohttp import web

from gideon.core.http_request import read_json_body, string_field
from gideon.engine.session import BACKGROUND_KEY
from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
)
from gideon.interfaces.dashboard.chat_utils import persisted_history_key
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.security.sel import sel

logger = logging.getLogger(__name__)

_TITLE_MAX_ATTEMPTS = 5

_AUTO_TAG_MAX_TOTAL = 4
_AUTO_TAG_MAX_NEW = 2


def _build_title_prompt(messages: list[dict[str, str]]) -> str | None:
    """Build a title generation prompt from conversation messages.

    Renders the ``title`` use-case prompt (bundled ``task-title``, editable/bindable
    in Settings → Prompts) through the prompt engine, so the instruction text is no
    longer hardcoded here.
    """
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    usable: list[tuple[str, str]] = []
    for m in messages:
        role = m.get("role", "")
        content = str(m.get("content", "") or "").strip()
        if role in ("user", "assistant") and content:
            usable.append((role, content))
    if not usable:
        return None
    first_user = next((item for item in usable if item[0] == "user"), None)
    context = usable[-8:]
    if first_user and first_user not in context:
        context.insert(0, first_user)
    lines = [f"{role}: {content[:280]}" for role, content in context]
    return render_use_case_prompt("title", {"transcript": "\n".join(lines)})


async def _stream_background_prompt(state: ConsoleState, prompt: str) -> str:
    """Stream *prompt* through the shared background session and collect the text."""
    client, _is_new, _resumed = await state.sessions.get_or_create(BACKGROUND_KEY)
    text = ""
    try:
        if hasattr(client, "_history"):
            client._history.clear()
        async for event in client.stream(prompt):
            if event.kind == EVENT_TEXT_CHUNK:
                text += event.text
            elif event.kind == EVENT_PERMISSION_REQUEST:
                await client.reject_tool(event.request_id)
            elif event.kind == EVENT_COMPLETE:
                break
    finally:
        if hasattr(client, "_history"):
            client._history.clear()
        state.sessions.release(BACKGROUND_KEY)
    return text


def _parse_title(text: str) -> str:
    """Extract + sanitize the title from a raw title-generation response."""
    title = text.strip().split("\n")[0].strip()
    title = re.sub(r"^(?:#{1,6}\s*|title\s*:\s*)", "", title, flags=re.IGNORECASE)
    title = title.strip().strip('"').strip("'").strip("`").strip(".").strip()
    if not title or title.upper() == "SKIP":
        logger.info("Title generation returned SKIP/empty — topic not clear yet")
        return ""
    lower = title.lower()
    if lower.startswith("user:") or lower.startswith("assistant:") or len(title) > 60:
        logger.info(
            "Title generation rejected (looks like continuation): %r", title[:80]
        )
        return ""
    title, _ = redact_exfiltration_urls(title)
    title, _ = redact_credentials(title)
    title = re.sub(r"\s+", " ", title).strip()
    logger.info("Title generated: %r", title[:80])
    return title[:60]


def _fallback_title(messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        raw = str(message.get("content", "") or "")
        cleaned = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
        cleaned = re.sub(r"https?://\S+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" #>*-\n\t")
        cleaned, _ = redact_exfiltration_urls(cleaned)
        cleaned, _ = redact_credentials(cleaned)
        words = cleaned.split()
        if words:
            return " ".join(words[:9])[:60].rstrip(" ,.;:")
    return "Untitled chat"


async def _generate_title_via_provider(
    state: ConsoleState, messages: list[dict[str, str]]
) -> str:
    """Generate a title using the shared background agent session."""

    prompt = _build_title_prompt(messages)
    if not prompt:
        logger.debug("Title generation skipped — no usable messages")
        return ""

    logger.debug("Title generation prompt (%d chars): %s", len(prompt), prompt[:120])
    text = await _stream_background_prompt(state, prompt)
    return _parse_title(text)


def _build_tags_suffix(state: ConsoleState) -> str:
    """The tag-proposal instructions appended to the title prompt.

    Asks the model for ONE extra line so the title stays line 1 (the title
    parser already only reads the first line).
    """
    existing = ", ".join(
        str(t.get("name", ""))
        for t in sorted(state._tags, key=lambda t: t.get("order", 0))
        if t.get("name")
    )
    return (
        "\n\nThen, on a SECOND line, propose tags for this conversation in the form:\n"
        "TAGS: tag1, tag2\n"
        f"Existing tags: {existing or '(none)'}\n"
        "Rules: strongly prefer existing tags that genuinely fit. Propose at most "
        f"{_AUTO_TAG_MAX_NEW} NEW tags (short, 1-2 words) only when no existing tag fits. "
        f"At most {_AUTO_TAG_MAX_TOTAL} tags total. If nothing fits, reply exactly: TAGS: none"
    )


def _parse_tags_line(text: str) -> list[str]:
    """Extract proposed tag names from a ``TAGS:`` line in the response."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("tags:"):
            continue
        raw = stripped[5:].strip()
        if not raw or raw.lower() in ("none", "n/a", "-"):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            name = part.strip().strip('"').strip("'").strip(".")
            if name.lower().startswith("new:"):
                name = name[4:].strip()
            if not name or len(name) > 40:
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)
        return names[:_AUTO_TAG_MAX_TOTAL]
    return []


def _apply_auto_tags(
    state: ConsoleState, session: _ChatSession, names: list[str]
) -> list[str]:
    """Resolve proposed tag names to ids (creating up to 2 new tags) and assign.

    New tags are created via the SAME helper the UI's create endpoint uses
    (:func:`gideon.interfaces.dashboard.chat_tags.create_tag`) so they get proper
    ids/colors/order. Returns the assigned tag ids (empty = nothing applied).
    """
    from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
    from gideon.interfaces.dashboard.chat_tags import (
        _auto_color,
        create_tag,
        find_tag_by_name,
    )

    if not names:
        return []
    if session.is_restricted or session.tags:
        return []
    assigned: list[str] = []
    created = 0
    for name in names:
        if len(assigned) >= _AUTO_TAG_MAX_TOTAL:
            break
        tag = find_tag_by_name(state, name)
        if tag is None:
            if created >= _AUTO_TAG_MAX_NEW:
                continue
            tag = create_tag(state, name, color=_auto_color(name))
            if tag is None:
                continue
            created += 1
        if tag["id"] not in assigned:
            assigned.append(tag["id"])
    if not assigned:
        return []
    session.tags = assigned
    save_session_to_history(state, session, force=True)
    state.push_sessions_update()
    logger.info(
        "Auto-tagged session %s with %d tag(s) (%d new)",
        session.key,
        len(assigned),
        created,
    )
    return assigned


def _auto_tag_enabled() -> bool:
    """Read the chat auto-tag config flag (default on)."""
    try:
        from gideon.core.config.loader import AppConfig

        return bool(AppConfig.load().dashboard.auto_tag_sessions)
    except Exception:
        return True


def _persist_title(state: ConsoleState, session: _ChatSession) -> None:
    """Save the session title to the conversation history file."""

    if state.conversation_log:
        history_key = persisted_history_key(state.conversation_log, session.key)
        try:
            state.conversation_log.set_title(history_key, session.title)
            logger.debug(
                "Persisted title %r for session %s", session.title, session.key
            )
        except Exception:
            logger.debug("Failed to persist title for session %s", session.key)


def _apply_title(state: ConsoleState, session: _ChatSession, title: str) -> None:
    """Commit a resolved title to the session: mark it titled, persist, broadcast."""
    session.title = title
    session._titled = True
    _persist_title(state, session)
    state.push_session_title(session.key, title)


async def _maybe_auto_title(state: ConsoleState, session: _ChatSession) -> None:
    """Background task: attempt to auto-title a session after a response completes.

    When auto-tagging is enabled the SAME LLM call also proposes tags for the
    session (the tag instructions are appended to the title prompt — no second
    roundtrip). Tags are only applied when the user hasn't tagged the session
    themselves and the session isn't restricted (incognito/temporary).
    """
    if session._titled:
        return
    if session.blocks_reads:
        return
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    if user_count < 1 or user_count > _TITLE_MAX_ATTEMPTS:
        if user_count > _TITLE_MAX_ATTEMPTS and not session._titled:
            _apply_title(state, session, _fallback_title(session.messages))
        return
    logger.info(
        "Auto-title: attempting for session %s (turn %d)", session.key, user_count
    )
    want_tags = not session.is_restricted and not session.tags and _auto_tag_enabled()
    try:
        prompt = _build_title_prompt(session.messages)
        if not prompt:
            logger.debug("Title generation skipped — no usable messages")
            return
        if want_tags:
            prompt += _build_tags_suffix(state)
        text = await _stream_background_prompt(state, prompt)
        title = _parse_title(text)
        logger.info("Auto-title: agent returned %r for session %s", title, session.key)
        if title:
            _apply_title(state, session, title)
            if want_tags:
                _apply_auto_tags(state, session, _parse_tags_line(text))
    except Exception:
        logger.warning("Auto-title failed for session %s", session.key, exc_info=True)


async def api_chat_session_generate_title(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/generate-title — manually trigger title generation."""
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)

    logger.info("Manual title generation requested for session %s", name)
    try:
        title = await _generate_title_via_provider(state, session.messages)
    except Exception:
        logger.debug("Title generation failed for session %s", name, exc_info=True)
        title = _fallback_title(session.messages)

    if title:
        _apply_title(state, session, title)

    return web.json_response({"ok": True, "title": title})


async def api_chat_session_rename(request: web.Request) -> web.Response:
    """PATCH /api/chat/sessions/{session}/title — rename a chat session."""
    state: ConsoleState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    if not session:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    title = string_field(body, "title")[:200]
    if not title:
        return web.json_response({"error": "title required"}, status=400)
    _apply_title(state, session, title)
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_rename",
        outcome="allowed",
        source="dashboard",
        resources=session.key,
    )
    return web.json_response({"ok": True, "title": title})
