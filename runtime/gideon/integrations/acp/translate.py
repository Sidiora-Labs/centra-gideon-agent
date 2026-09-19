"""Decode ACP payloads into correlated, display-ready event records."""

from __future__ import annotations

import base64
import difflib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.integrations.acp.types import (
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
    UPDATE_AGENT_MESSAGE_CHUNK,
    UPDATE_TOOL_CALL,
    UPDATE_TOOL_CALL_UPDATE,
    AcpEvent,
    JsonRpcMessage,
)
from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)
_EXIT_STATUS_KEYS = ("exit_code", "exitCode", "exit_status", "exitStatus")
_ERROR_FLAG_KEYS = ("isError", "is_error")
_FAILURE_SCAN_MAX_DEPTH = 6
TOOL_INTERRUPTED_MARKER = "Tool uses were interrupted, waiting for the next user prompt"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}
_IMAGE_PATH_RE = re.compile(
    r"(/[\w./@~\s()\-]+\.(?:png|jpg|jpeg|gif|webp|bmp))", re.IGNORECASE
)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _update(message: JsonRpcMessage) -> dict:
    return _mapping(_mapping(message.params).get("update"))


def _safe_display(value):
    if not value:
        return value
    return redact_credentials(redact_exfiltration_urls(value)[0])[0]


def _formatted(value) -> str:
    return (
        json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)
    )


def make_unified_diff(old: str, new: str, path: str, max_len: int = 6000) -> str:
    def preserved_lines(text):
        return (
            (text + ("" if text.endswith("\n") else "\n")).splitlines(keepends=True)
            if text
            else []
        )

    delta = difflib.unified_diff(
        preserved_lines(old), preserved_lines(new), fromfile=path, tofile=path, n=3
    )
    return "".join(delta).rstrip()[:max_len]


def _declared_file_change(
    path: object, old: object, new: object
) -> dict[str, str] | None:
    if not path:
        return None
    return dict(path=str(path), before=str(old or ""), after=str(new or ""))


def _first_diff(update: dict) -> dict | None:
    blocks = update.get("content")
    if not isinstance(blocks, list):
        return None
    return next(
        (
            block
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "diff"
        ),
        None,
    )


def _change_from(block: dict | None) -> dict[str, str] | None:
    if block is None:
        return None
    return _declared_file_change(
        block.get("path", ""), block.get("oldText"), block.get("newText")
    )


def coerce_tool_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for block in content:
        if isinstance(block, str):
            text = block
        elif isinstance(block, dict):
            raw_text = _mapping(block.get("content")).get("text") or block.get("text")
            text = str(raw_text) if raw_text is not None else ""
        else:
            continue
        if text:
            chunks.append(str(text))
    return "\n".join(chunks)


def extract_text_chunk(msg: JsonRpcMessage) -> tuple[str | None, bool]:
    update = _update(msg)
    if update.get("sessionUpdate") != UPDATE_AGENT_MESSAGE_CHUNK:
        return None, False
    content = _mapping(update.get("content"))
    return content.get("text"), content.get("type", "text") in {"thinking", "reasoning"}


@dataclass(frozen=True)
class SeenToolCall:
    kind: str = ""
    title: str = ""


@dataclass(frozen=True)
class _ToolOpening:
    frame: dict

    @property
    def raw_input(self):
        return (
            self.frame.get("rawInput")
            or self.frame.get("input")
            or self.frame.get("params")
        )

    def display_input(self, call_id: str, raw) -> str:
        rendered = _formatted(raw) if call_id and raw else ""
        block = _first_diff(self.frame)
        difference = ""
        if block is not None:
            difference = make_unified_diff(
                block.get("oldText") or "",
                block.get("newText") or "",
                block.get("path", ""),
            )
        if difference:
            return difference
        if isinstance(raw, dict) and raw.get("command") == "strReplace":
            before, after = raw.get("oldStr") or "", raw.get("newStr") or ""
            if before or after:
                rendered = (
                    make_unified_diff(before, after, raw.get("path") or "") or rendered
                )
        return rendered


def extract_tool_event(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
    tool_calls_sink: list[tuple[str, str]],
) -> AcpEvent | None:
    update = _update(msg)
    if update.get("sessionUpdate") != UPDATE_TOOL_CALL:
        return None
    opening = _ToolOpening(update)
    raw = opening.raw_input
    call_id = update.get("toolCallId", "")
    title = _safe_display(update.get("title", "unknown"))
    kind = _safe_display(update.get("kind", "unknown"))
    purpose = _safe_display(_mapping(raw).get("__tool_use_purpose", ""))
    display = _safe_display(opening.display_input(call_id, raw))
    if call_id:
        tool_call_seen[call_id] = SeenToolCall(kind, title)
        if display:
            tool_call_inputs[call_id] = display
    tool_calls_sink.append((kind, title))
    return AcpEvent(
        kind=EVENT_TOOL_CALL,
        title=title,
        tool_kind=kind,
        tool_purpose=purpose,
        tool_input=display,
        tool_input_obj=raw if isinstance(raw, dict) else None,
        file_change=_change_from(_first_diff(update)),
        tool_call_id=call_id,
    )


def _declares_nonzero_exit(value: object) -> bool:
    if type(value) is bool:
        return False
    if isinstance(value, int):
        return bool(value)
    digits = re.search(r"-?\d+", value) if isinstance(value, str) else None
    return bool(digits and int(digits[0]) != 0)


def _declares_failure(node: object, depth: int = 0) -> bool:
    pending = [(node, depth)]
    while pending:
        value, level = pending.pop()
        if level > _FAILURE_SCAN_MAX_DEPTH:
            continue
        if isinstance(value, dict):
            for key, item in value.items():
                if (key in _ERROR_FLAG_KEYS and item is True) or (
                    key in _EXIT_STATUS_KEYS and _declares_nonzero_exit(item)
                ):
                    return True
            children: Iterable[Any] = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        pending.extend((child, level + 1) for child in children)
    return False


def terminal_result_failed(update: dict) -> bool:
    payloads = (update.get("rawOutput"), update.get("content"))
    return update.get("status") == "failed" or any(map(_declares_failure, payloads))


def _refine_call(
    update: dict, call_id: str, inputs: dict, seen: dict
) -> AcpEvent | None:
    raw = update.get("rawInput")
    display = (
        _formatted(raw)
        if isinstance(raw, (dict, list)) and raw
        else raw if isinstance(raw, str) else ""
    )
    display = _safe_display(display)
    title = _safe_display(update.get("title") or "")
    kind = _safe_display(str(update.get("kind") or ""))
    if display:
        inputs[call_id] = display
    if kind or title:
        previous = seen.get(call_id, SeenToolCall())
        seen[call_id] = SeenToolCall(kind or previous.kind, title or previous.title)
    change = _change_from(_first_diff(update))
    if not (display or title or change):
        return None
    return AcpEvent(
        kind=EVENT_TOOL_CALL_UPDATE,
        tool_call_id=call_id,
        title=title,
        tool_input=display,
        tool_input_obj=raw if isinstance(raw, dict) else None,
        file_change=change,
    )


def _terminal_tool_event(update: dict, call_id: str) -> AcpEvent:
    output = coerce_tool_content(update.get("content"))
    raw = update.get("rawOutput")
    if not output and raw is not None:
        output = _formatted(raw)
    return AcpEvent(
        kind=EVENT_TOOL_RESULT,
        tool_call_id=call_id,
        tool_output=_safe_display((output or "")[:8000]),
        tool_meta={"ok": False} if terminal_result_failed(update) else {},
    )


def extract_tool_update_events(
    msg: JsonRpcMessage,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
) -> list[AcpEvent]:
    update = _update(msg)
    call_id = update.get("toolCallId", "")
    if update.get("sessionUpdate") != UPDATE_TOOL_CALL_UPDATE or not call_id:
        return []
    refinement = _refine_call(update, call_id, tool_call_inputs, tool_call_seen)
    events = [refinement] if refinement is not None else []
    if update.get("status") in {"completed", "failed"}:
        events.append(_terminal_tool_event(update, call_id))
    return events


def build_permission_event(
    msg: JsonRpcMessage,
    dialect,
    tool_call_inputs: dict[str, str],
    tool_call_seen: dict[str, SeenToolCall],
    offered_options: dict[str, list[dict[str, str]]],
) -> AcpEvent:
    params = _mapping(msg.params)
    declaration = _mapping(params.get("toolCall"))
    call_id = declaration.get("toolCallId", "")
    previous = (
        tool_call_seen.get(call_id, SeenToolCall()) if call_id else SeenToolCall()
    )
    title = str(declaration.get("title") or "") or previous.title or "unknown"
    kind = _safe_display(str(declaration.get("kind") or "")) or previous.kind
    options = (
        dialect.parse_permission_options(params.get("options", []))
        or dialect.default_permission_options()
    )
    request_id = "" if msg.id is None else msg.id
    if request_id != "":
        offered_options[str(request_id)] = options
    display = tool_call_inputs.get(call_id, "") if call_id else ""
    if not display:
        raw = _ToolOpening(declaration).raw_input
        display = _formatted(raw) if raw else ""
    logger.info("Permission requested for tool: %s (req=%s)", title, request_id)
    return AcpEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id=request_id,
        title=title,
        tool_kind=kind,
        options=options,
        tool_input=display,
        tool_call_id=call_id,
    )


def is_tool_interrupted_marker(chunk: str) -> bool:
    return TOOL_INTERRUPTED_MARKER == chunk.strip()


def extract_context_pct(msg: JsonRpcMessage) -> float | None:
    reported = _mapping(msg.params).get("contextUsagePercentage")
    return None if reported is None else float(reported)


def encode_prompt_content(message: str) -> list[dict]:
    images = []

    def attachment(match):
        file = Path(match[1].strip())
        suffix = file.suffix.lower()
        if not file.is_file() or suffix not in IMAGE_EXTENSIONS:
            return match[0]
        try:
            encoded = base64.b64encode(file.read_bytes()).decode()
        except Exception:
            return match[0]
        images.append(
            dict(
                type="image",
                data=encoded,
                mimeType=IMAGE_MEDIA_TYPES.get(suffix, "image/png"),
            )
        )
        return f"[image: {file.name}]"

    text = _IMAGE_PATH_RE.sub(attachment, message)
    return [dict(type="text", text=text), *images]


def _jsonl_outputs(entry: dict):
    if entry.get("kind") != "ToolResults":
        return
    for block in _mapping(entry.get("data")).get("content", []):
        if not isinstance(block, dict) or block.get("kind") != "toolResult":
            continue
        record = block.get("data")
        if not isinstance(record, dict):
            continue
        parts = []
        for item in record.get("content", []):
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "text":
                parts.append(str(item.get("data", ""))[:4000])
            elif item.get("kind") == "json":
                data = item.get("data", {})
                if isinstance(data, dict) and "stdout" in data:
                    if data.get("stdout"):
                        parts.append(data["stdout"][:4000])
                else:
                    parts.append(json.dumps(data, indent=2)[:4000])
        if parts:
            yield AcpEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=record.get("toolUseId", ""),
                tool_output="\n".join(parts)[:8000],
            )


def read_new_tool_results(jsonl_path: Path, pos: int) -> tuple[list[AcpEvent], int]:
    events = []
    try:
        with jsonl_path.open("rb") as stream:
            stream.seek(pos)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                pos = stream.tell()
                try:
                    payload = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(payload, dict):
                    events.extend(_jsonl_outputs(payload))
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("ACP tool-result journal could not be read", exc_info=True)
    return events, pos


def format_command_result(result: dict) -> str:
    message = result.get("message") or ""
    payload = _mapping(result.get("data"))
    visible = {
        key: value for key, value in payload.items() if key not in {"agent", "model"}
    }
    if not visible:
        return message
    block = "```json\n" + json.dumps(visible, indent=2) + "\n```"
    return str(message) + "\n" + block if message else block
