"""Compact native transcripts while retaining anchors, tool pairs and resume facts."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable

logger = logging.getLogger(__name__)
_TOOL_RESULT_PRUNE_OVER = 600
_KEEP_RECENT_TOOL_RESULTS = 4
_PROTECT_HEAD = 3
_PROTECT_TAIL = 8
_MIN_SAVE_FRACTION = 0.10
_FILE_ARG_KEYS = ("path", "file_path", "workdir", "output_path", "cwd")
_FILE_RE = re.compile(r"(?:[\w./~-]+/)?[\w.-]+\.[A-Za-z0-9]{1,8}")
_RESULT_ID_RE = re.compile(r'tool_result_get\(result_id="(r_[^"]+)"\)')


def _msg_len(m: dict) -> int:
    arguments = (
        str(call.get("function", {}).get("arguments", ""))
        for call in m.get("tool_calls", []) or []
    )
    return len(str(m.get("content", ""))) + sum(map(len, arguments))


def total_chars(messages: list[dict]) -> int:
    return sum(map(_msg_len, messages))


class _ToolDigestPass:
    def __init__(self, messages):
        positions = [
            position
            for position, message in enumerate(messages)
            if message.get("role") == "tool"
        ]
        self.protected = frozenset(positions[-_KEEP_RECENT_TOOL_RESULTS:])
        self.previous = None

    def project(self, position, message):
        if message.get("role") != "tool" or position in self.protected:
            self.previous = None
            return message
        body = str(message.get("content", ""))
        if len(body) <= _TOOL_RESULT_PRUNE_OVER:
            self.previous = None
            return message
        line_count, char_count = body.count("\n") + 1, len(body)
        digest = f"[pruned tool result — {line_count} lines, {char_count} chars]"
        if digest == self.previous:
            digest = "[pruned tool result — identical to previous]"
        handle = _RESULT_ID_RE.search(body)
        if handle:
            digest += f' full result: tool_result_get(result_id="{handle.group(1)}")'
        self.previous = digest
        return dict(message, content=digest)


def prune_tool_outputs(messages: list[dict]) -> list[dict]:
    projection = _ToolDigestPass(messages)
    return [
        projection.project(index, message) for index, message in enumerate(messages)
    ]


class _FileMentions:
    def __init__(self, limit):
        self.limit, self.names = limit, {}

    def consume(self, message):
        for call in message.get("tool_calls", []) or []:
            arguments = call.get("function", {}).get("arguments", "")
            if isinstance(arguments, str):
                try:
                    fields = json.loads(arguments)
                except (ValueError, TypeError):
                    fields = {}
            else:
                fields = arguments if isinstance(arguments, dict) else {}
            for name in _FILE_ARG_KEYS:
                value = fields.get(name)
                if isinstance(value, str) and value.strip():
                    self.names.setdefault(value.strip(), None)
        for match in _FILE_RE.findall(str(message.get("content", ""))):
            self.names.setdefault(match, None)
            if len(self.names) >= self.limit * 2:
                break


def extract_file_refs(messages: list[dict], limit: int = 25) -> list[str]:
    mentions = _FileMentions(limit)
    for message in messages:
        mentions.consume(message)
    return list(mentions.names)[:limit]


def _structured_digest(middle: list[dict], files: list[str]) -> str:
    requests, tools = [], Counter()
    for message in middle:
        if message.get("role") == "user":
            requests.append(str(message.get("content", "")))
        for call in message.get("tool_calls", []) or []:
            name = call.get("function", {}).get("name", "")
            if name:
                tools[name] += 1
    sections = ["## Earlier conversation (compacted)"]
    if requests:
        sections.append(
            "### Requests made\n"
            + "\n".join("- " + text[:200] for text in requests[:10])
        )
    if tools:
        sections.append(
            "### Tools used\n"
            + ", ".join(f"{name}×{count}" for name, count in tools.most_common(12))
        )
    if files:
        sections.append(
            "### Relevant Files\n" + "\n".join("- " + name for name in files)
        )
    return "\n\n".join(sections)


def should_compact(saves: list[float]) -> bool:
    recent = saves[-2:]
    return len(recent) < 2 or not all(value < _MIN_SAVE_FRACTION for value in recent)


def _drop_orphan_tool_results(messages: list[dict]) -> list[dict]:
    announced = {
        str(call["id"])
        for message in messages
        for call in message.get("tool_calls", []) or []
        if call.get("id")
    }
    return [
        message
        for message in messages
        if message.get("role") != "tool"
        or str(message.get("tool_call_id", "")) in announced
    ]


def is_resume_account(msg: dict) -> bool:
    from gideon.cognition.resume_account import FENCE_START

    body = str(msg.get("content", ""))
    return body.find(FENCE_START) >= 0


def _account_message(body: str) -> dict:
    return dict(role="user", content=body)


def _derive_account_for(folded: list[dict]) -> str:
    try:
        from gideon.cognition.resume_account import (
            NOT_CONSULTED,
            derive_account,
            render_account,
        )

        facts = derive_account(
            ledger_events=NOT_CONSULTED,
            tool_messages=folded,
            checkpoint_entries=NOT_CONSULTED,
        )
        return render_account(facts)
    except Exception:
        logger.warning(
            "resume account derivation skipped during compaction", exc_info=True
        )
        return ""


class _CompactionLayout:
    def __init__(self, original, pruned, head, tail):
        self.original, self.pruned = original, pruned
        self.head, self.tail = head, tail
        self.middle = slice(head, len(pruned) - tail)

    def accounts(self):
        carried, unaccounted = None, []
        for message in self.original[self.middle]:
            if is_resume_account(message):
                carried = message
            else:
                unaccounted.append(message)
        blocks = [] if carried is None else [carried]
        fresh = _derive_account_for(unaccounted)
        if fresh:
            blocks.append(_account_message(fresh))
        return blocks

    def assemble(self, summary):
        envelope = {
            "role": "user",
            "content": (
                "[CONTEXT COMPACTION — REFERENCE ONLY. The earlier conversation was "
                "compacted to save context. This is a record of what happened, NOT a "
                "task to resume verbatim — act on the latest message below.]\n\n"
                + summary
                + "\n[END CONTEXT COMPACTION]"
            ),
        }
        output = self.pruned[: self.head] + [envelope]
        output.extend(self.accounts())
        output.extend(self.pruned[-self.tail :])
        return _drop_orphan_tool_results(output)


def compact(
    messages: list[dict],
    *,
    summarize_fn: Callable[[list[dict]], str] | None = None,
    protect_head: int = _PROTECT_HEAD,
    protect_tail: int = _PROTECT_TAIL,
) -> list[dict]:
    pruned = prune_tool_outputs(messages)
    if len(pruned) <= protect_head + protect_tail:
        return pruned
    layout = _CompactionLayout(messages, pruned, protect_head, protect_tail)
    middle = pruned[layout.middle]
    if not middle:
        return pruned
    files = extract_file_refs(pruned)
    summary = (
        summarize_fn(middle) if summarize_fn else _structured_digest(middle, files)
    )
    return layout.assemble(summary)
