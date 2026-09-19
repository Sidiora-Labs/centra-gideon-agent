"""Compress idle journals in attention tiers and archive displaced transcript rows."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from gideon.cognition.context_segmentation import Segment, segment_messages
from gideon.cognition.history import ConversationLog

logger = logging.getLogger(__name__)
_MIN_TRANSCRIPT_CHARS = 8_000
_KEEP_RECENT_SEGMENTS = 1
_MIDDLE_MSG_CAP = 600
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')


def _idle_seconds(idle_days: float) -> float:
    days = max(0.0, idle_days)
    return days * 86400.0


def _transcript_chars(messages: list[dict]) -> int:
    sizes = (len(str(message.get("content", ""))) for message in messages)
    return sum(sizes)


def _collect_raw_refs(messages: list[dict]) -> list[str]:
    occurrences = (
        handle
        for message in messages
        for handle in _RESULT_ID_RE.findall(str(message.get("content", "")))
    )
    return list(dict.fromkeys(occurrences))


def _reduce_middle(seg: Segment) -> list[dict]:
    dialogue = []
    for message in seg.messages:
        if message.get("role", "") in ("user", "assistant"):
            body = str(message.get("content", ""))
            excerpt = (
                body[:_MIDDLE_MSG_CAP] + " …" if len(body) > _MIDDLE_MSG_CAP else body
            )
            dialogue.append(dict(message, content=excerpt))
    return dialogue


class _EarlierConversation:
    def __init__(self, segments):
        self.messages = [
            message for segment in segments for message in segment.messages
        ]

    async def summarize(self):
        if not self.messages:
            return None
        lines = []
        for message in self.messages:
            role = message.get("role", "")
            if role in ("user", "assistant"):
                lines.append(f"{role}: {str(message.get('content', '')).strip()}")
        body = "\n".join(lines)
        handles = _collect_raw_refs(self.messages)
        from gideon.integrations.tool_providers.prose_compress import compress_prose

        summary = await compress_prose(body, raw_ref="")
        appendix = ""
        if handles:
            calls = [
                f'tool_result_get(result_id="{handle}")' for handle in handles[:20]
            ]
            appendix = "\nRecoverable raw outputs: " + ", ".join(calls)
        return {
            "role": "system",
            "content": (
                "[CONTEXT ECONOMY — background-compressed earlier conversation, reference only. "
                "Older turns were archived (recoverable) and summarized below.]\n"
                f"{summary}{appendix}"
            ),
            "cls": "bg_compress_summary",
        }


async def _summarize_oldest(segments: list[Segment]) -> dict | None:
    return await _EarlierConversation(segments).summarize()


class _CompressionTiers:
    def __init__(self, segments):
        self.segments = segments
        self.recent = segments[-_KEEP_RECENT_SEGMENTS:]
        earlier = segments[:-_KEEP_RECENT_SEGMENTS]
        boundary = max(1, len(earlier) // 2)
        self.oldest, self.middle = earlier[:boundary], earlier[boundary:]

    async def rebuild(self):
        summary = await _summarize_oldest(self.oldest)
        rebuilt = [] if summary is None else [summary]
        for segment in self.middle:
            rebuilt.extend(_reduce_middle(segment))
        for segment in self.recent:
            rebuilt.extend(segment.messages)
        return rebuilt


class _JournalCompression:
    def __init__(self, log, key):
        self.log, self.key = log, key

    async def run(self, embed):
        messages = self.log._read_messages(self.key)
        if not messages:
            return None
        before = _transcript_chars(messages)
        if before < _MIN_TRANSCRIPT_CHARS:
            return None
        segments = segment_messages(messages, embed_fn=embed)
        if len(segments) <= _KEEP_RECENT_SEGMENTS + 1:
            return None
        replacement = await _CompressionTiers(segments).rebuild()
        after = _transcript_chars(replacement)
        if not replacement or after >= before:
            return None
        self.log.rewrite_session(self.key, replacement, reason="bg_compress")
        _record_savings(before, after)
        logger.info(
            "bg-compress: session %s %d→%d chars (%d segments)",
            self.key,
            before,
            after,
            len(segments),
        )
        return {
            "key": self.key,
            "chars_in": before,
            "chars_out": after,
            "segments": len(segments),
        }


async def compress_session(
    log: ConversationLog, key: str, *, embed_fn=None
) -> dict | None:
    try:
        return await _JournalCompression(log, key).run(embed_fn)
    except Exception:
        logger.warning("bg-compress: failed for session %s", key, exc_info=True)
        return None


def _eligible_keys(log: ConversationLog, idle_days: float, now: float) -> list[str]:
    cutoff = now - _idle_seconds(idle_days)
    candidates = []
    for session in log.list_sessions():
        key = session.get("key")
        if not key or session.get("memory_mode", "persistent") != "persistent":
            continue
        modified = float(session.get("modified", 0.0) or 0.0)
        if modified <= 0 or modified > cutoff:
            continue
        candidates.append((modified, key))
    return [key for _, key in sorted(candidates, key=lambda candidate: candidate[0])]


async def run_bg_compression_pass(
    log: ConversationLog | None, *, embed_fn=None, max_sessions: int = 3
) -> list[dict]:
    if log is None:
        return []
    try:
        from gideon.core.config.loader import AppConfig

        configuration = AppConfig.load().tools
        if not configuration.bg_compress_enabled:
            return []
        age = float(configuration.bg_compress_idle_days)
    except Exception:
        logger.debug("bg-compress: config load failed — skipping pass", exc_info=True)
        return []
    eligible = _eligible_keys(log, age, time.time())
    if not eligible:
        return []
    results = []
    for key in eligible[: max(1, max_sessions)]:
        outcome = await compress_session(log, key, embed_fn=embed_fn)
        if outcome is not None:
            results.append(outcome)
    return results


def _record_savings(chars_in: int, chars_out: int) -> None:
    try:
        from gideon.integrations.tool_providers import savings

        entry: dict = {
            "month": datetime.now().strftime("%Y-%m"),
            "model": "unknown",
            "compressor": "bg_topic",
            "chars_in": chars_in,
            "chars_out": chars_out,
        }
        savings.record_saving(**entry)
    except Exception:
        logger.debug("bg-compress savings accounting failed", exc_info=True)
