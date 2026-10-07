"""Slack response footers and explicit in-place compaction lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from slack_desk_runtime.client import SlackDeskClientOps
from slack_desk_runtime.format import SLACK_MSG_LIMIT, TRUNCATION_NOTICE, split_message

from gideon.sdk.channel import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    redact_credentials,
    redact_exfiltration_urls,
    sel,
)

if TYPE_CHECKING:
    from .handler import SessionManager

logger = logging.getLogger(__name__)


class ContextUsageProvider(Protocol):

    def context_usage_pct(self) -> float | None: ...


def _filter_options_brackets(
    text: str, bracket_hold: str, stream_buffer: str
) -> tuple[str, str]:
    """Filter ``[OPTIONS: ...]`` tags from streaming text character-by-character.

    Returns the updated *(bracket_hold, stream_buffer)* tuple.
    """
    for ch in text:
        if bracket_hold or ch == "[":
            bracket_hold += ch
            if ch == "]":
                if bracket_hold.startswith("[OPTIONS:"):
                    bracket_hold = ""
                else:
                    stream_buffer += bracket_hold
                    bracket_hold = ""
        else:
            stream_buffer += ch
    return bracket_hold, stream_buffer


def build_timing_footer(
    elapsed: float,
    client: ContextUsageProvider | None = None,
) -> tuple[list[dict], str]:
    """Build the timing/context footer blocks for a Slack response.

    Returns ``(blocks, fallback_text)`` suitable for ``post_blocks``.
    """
    if elapsed < 60:
        duration = f"{int(elapsed)}s"
    else:
        mins, secs = divmod(int(elapsed), 60)
        duration = f"{mins}m {secs}s"
    footer_text = f"Finished in {duration}"
    if client is not None:
        try:
            context_usage = client.context_usage_pct()
            if context_usage is not None:
                ctx_pct = round(context_usage)
                ctx_icon = (
                    "🔴"
                    if ctx_pct >= 70
                    else "🟠" if ctx_pct >= 50 else "🟡" if ctx_pct >= 30 else "🟢"
                )
                footer_text = f"Finished in {duration} · {ctx_icon} ctx {ctx_pct}%"
        except Exception:
            logger.debug("Failed to retrieve context usage", exc_info=True)
    blocks: list[dict] = [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer_text}]}
    ]
    return blocks, footer_text


def _append_footer_actions(
    footer_blocks: list[dict],
    options: list[str] | None,
    thread_ts: str | None,
    linked_session_key: str | None,
    dashboard_state: object | None,
) -> list[dict]:
    """Append OPTIONS checkboxes and/or Link to Dashboard button to footer blocks."""
    if options:
        from slack_desk_runtime.format import build_options_blocks

        footer_blocks.extend(build_options_blocks(options))
    if thread_ts and not linked_session_key and dashboard_state:
        from slack_desk_runtime.format import build_link_dashboard_button

        if footer_blocks and footer_blocks[-1].get("type") == "actions":
            footer_blocks[-1]["elements"].append(build_link_dashboard_button())
        else:
            footer_blocks.append(
                {"type": "actions", "elements": [build_link_dashboard_button()]}
            )
    return footer_blocks


async def _handle_compact_command(
    slack_desk: SlackDeskClientOps,
    sessions: SessionManager,
    channel: str,
    reply_ts: str,
    msg_ts: str,
    session_key: str,
    *,
    add_phase_reaction: Callable[[SlackDeskClientOps, str, str, str], Awaitable[None]],
    timing_footer: Callable[..., tuple[list[dict], str]],
) -> None:
    """Trigger in-place ACP ``/compact`` on the current thread's session."""
    provider = sessions.get_provider(session_key)
    if not provider:
        await slack_desk.post_message(
            channel, "No active session to compact.", reply_ts
        )
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome="no_session",
        )
        return

    _t0 = time.monotonic()

    # --- Phase 1: Pre-compaction UI (cosmetic — log failures, don't abort) ---
    try:
        await slack_desk.add_reaction(channel, msg_ts, "recycle")
        await slack_desk.post_message(channel, "🔄 Compacting context…", reply_ts)
    except Exception:
        logger.debug("Pre-compact UI failed for %s", session_key, exc_info=True)

    # --- Phase 2: Actual compaction (failures warrant error + session teardown) ---
    result_text: str | None = None
    outcome = "unknown"
    try:

        async def _run_compact_stream() -> None:
            nonlocal result_text, outcome
            async for event in provider.stream_command("/compact"):
                if event.kind == EVENT_COMPACTION_STATUS:
                    if event.text == "completed":
                        summary = event.title or ""
                        result_text = (
                            f"✅ Compacted: {summary}"
                            if summary
                            else "✅ Context compacted."
                        )
                        outcome = "completed"
                    elif event.text == "failed":
                        error = event.title or "unknown error"
                        result_text = f"❌ Compaction failed: {error}"
                        outcome = "failed"
                elif event.kind == EVENT_COMPLETE:
                    break

        await asyncio.wait_for(_run_compact_stream(), timeout=120)

        # ACP agent fires compaction asynchronously after EVENT_COMPLETE —
        # wait for the real result, mirroring the dashboard's deferred path.
        if not result_text:
            cr = await provider.wait_for_compaction(timeout=120.0)
            if cr["type"] == "completed":
                summary = cr.get("summary", "")
                result_text = (
                    f"✅ Compacted: {summary}" if summary else "✅ Context compacted."
                )
                outcome = "completed"
            elif cr["type"] == "failed":
                error = cr.get("summary", "")
                result_text = (
                    f"❌ Compaction failed: {error}"
                    if error
                    else "❌ Compaction failed."
                )
                outcome = "failed"
            else:
                result_text = "⚠️ Compaction timed out."
                outcome = "timeout"
    except Exception:
        logger.warning("Compact command failed for %s", session_key, exc_info=True)
        try:
            await slack_desk.post_message(
                channel, "❌ Compaction failed unexpectedly.", reply_ts
            )
        except Exception:
            logger.debug(
                "Failed to post compact error for %s", session_key, exc_info=True
            )
        try:
            await sessions.destroy(session_key)
        except Exception:
            logger.warning(
                "Failed to destroy session %s after compact failure",
                session_key,
                exc_info=True,
            )
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome="failed",
            error="exception",
        )
        try:
            await slack_desk.remove_reaction(channel, msg_ts, "recycle")
            await add_phase_reaction(slack_desk, channel, msg_ts, "done")
        except Exception:
            pass
        return

    # --- Phase 3: Post-compaction reporting (log failures, don't mislead) ---
    try:
        result_text, _ = redact_exfiltration_urls(result_text)
        result_text, _ = redact_credentials(result_text)
        await slack_desk.post_message(channel, result_text, reply_ts)

        elapsed = time.monotonic() - _t0
        footer_blocks, footer_text = timing_footer(elapsed)
        await slack_desk.post_blocks(channel, footer_blocks, footer_text, reply_ts)
    except Exception:
        logger.debug("Post-compact reporting failed for %s", session_key, exc_info=True)

    try:
        sel().log_tool_invocation(
            session_key=session_key,
            source="slack",
            tool_name="compact",
            tool_kind="command",
            outcome=outcome,
        )
    except Exception:
        logger.debug("Failed to log compact outcome for %s", session_key, exc_info=True)
    try:
        await slack_desk.remove_reaction(channel, msg_ts, "recycle")
        await add_phase_reaction(slack_desk, channel, msg_ts, "done")
    except Exception:
        pass


async def _safe_update(
    slack_desk: SlackDeskClientOps, channel: str, ts: str, text: str
) -> None:
    """Update a Slack message, truncating if too long.

    Used for progressive streaming edits — truncation is fine here since
    the final message uses _safe_final_update which splits instead.
    """
    text, _ = redact_exfiltration_urls(text)
    if len(text) > SLACK_MSG_LIMIT:
        text = text[:SLACK_MSG_LIMIT] + TRUNCATION_NOTICE
    try:
        await slack_desk.update_message(channel, ts, text)
    except Exception:
        logger.debug("Failed to update message %s", ts, exc_info=True)


async def _safe_final_update(
    slack_desk: SlackDeskClientOps,
    channel: str,
    ts: str,
    text: str,
    thread_ts: str | None = None,
) -> None:
    """Final message update — splits into multiple messages if too long."""
    text, _ = redact_exfiltration_urls(text)
    parts = split_message(text)
    # First part updates the existing streaming message
    try:
        await slack_desk.update_message(channel, ts, parts[0])
    except Exception:
        logger.debug("Failed to update message %s", ts, exc_info=True)
    # Overflow parts posted as follow-up messages in the same thread
    for part in parts[1:]:
        try:
            await slack_desk.post_message(channel, part, thread_ts)
        except Exception:
            logger.debug("Failed to post continuation message", exc_info=True)
