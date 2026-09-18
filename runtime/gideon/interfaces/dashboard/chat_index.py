"""Durable chat index — one navigation marker per turn and significant sub-event.

The coordinates are the ones :mod:`gideon.interfaces.dashboard.chat_fork` takes
(``at_message_index``: the position in the visible user/assistant list), so a marker
restored from disk jumps exactly where forking the same turn would branch. The turn
grouping is a port of the console's ``hydrateTurns`` + ``branchIndexOf`` so both sides
derive the same markers from the same transcript.
"""

from __future__ import annotations

import re
import unicodedata

LABEL_MAX = 80
INDEX_CAP = 200

_SUBAGENT_TOOL = re.compile(r"agent|task|subagent|delegate|dispatch", re.IGNORECASE)
_WS = re.compile(r"\s+")
_ENTRY_TYPES = {
    "id": str,
    "kind": str,
    "label": str,
    "role": str,
    "turn_index": int,
    "jump_index": int,
    "failed_tool": bool,
}


def _tidy(text: str) -> str:
    return _WS.sub(" ", str(text)).strip()[:LABEL_MAX]


def _is_pictograph(ch: str) -> bool:
    cp = ord(ch)
    return (
        unicodedata.category(ch) == "So"
        or 0x1F000 <= cp <= 0x1FAFF
        or cp in (0x200D, 0xFE0F)
    )


def _tool_name(meta: dict, content: str) -> str:
    raw = str(meta.get("tool") or content or "tool")
    i = 0
    while i < len(raw) and _is_pictograph(raw[i]):
        i += 1
    return raw[i:].strip() or "tool"


def _recollapse_pastes(content: str, pastes: list) -> str:
    out = content
    ordered = sorted(
        (p for p in pastes if isinstance(p, dict)),
        key=lambda p: len(str(p.get("content", ""))),
        reverse=True,
    )
    for p in ordered:
        body = str(p.get("content", ""))
        if body:
            out = out.replace(body, f"[Paste #{p.get('seq')}]")
    return out


def _user_display(content: str, meta: dict) -> str:
    primary = content
    if meta.get("original") is not None:
        primary = str(meta["original"])
    if meta.get("ui_label") is not None:
        primary = str(meta["ui_label"])
    pastes = meta.get("pastes")
    if isinstance(pastes, list) and pastes:
        primary = _recollapse_pastes(primary, pastes)
    return primary


def _failed(meta: dict) -> bool:
    return meta.get("ok") is False or bool(meta.get("agent_error"))


def _hydrate_turns(messages: list[dict]) -> list[dict]:
    turns: list[dict] = []
    tool_index: dict[str, dict] = {}
    last_user_text = ""
    assistant_text_since_user = False
    visible = -1

    def last_assistant() -> dict:
        if turns and turns[-1]["role"] == "assistant":
            return turns[-1]
        turn: dict = {"role": "assistant", "visible_index": None, "segments": []}
        turns.append(turn)
        return turn

    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "assistant")
        content = str(m.get("content", "") or "")
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        if role == "user":
            visible += 1
            text = content.strip()
            if text == last_user_text and not assistant_text_since_user:
                continue
            turns.append(
                {
                    "role": "user",
                    "visible_index": visible,
                    "segments": [
                        {"kind": "text", "text": _user_display(content, meta)}
                    ],
                }
            )
            last_user_text = text
            assistant_text_since_user = False
        elif role == "assistant":
            visible += 1
            turn = last_assistant()
            turn["visible_index"] = visible
            turn["segments"].append({"kind": "text", "text": content})
            assistant_text_since_user = True
        elif role == "tool":
            call_id = str(meta.get("tool_call_id") or "")
            if call_id:
                existing = tool_index.get(call_id)
                if existing is not None:
                    existing["failed"] = existing["failed"] or _failed(meta)
                    continue
                turn = last_assistant()
            else:
                position = len(turns)
                turn = last_assistant()
                call_id = f"auto-{position}-{len(turn['segments'])}"
                existing = tool_index.get(call_id)
                if existing is not None:
                    existing["failed"] = existing["failed"] or _failed(meta)
                    continue
            seg = {
                "kind": "tool",
                "tool": _tool_name(meta, content),
                "failed": _failed(meta),
            }
            tool_index[call_id] = seg
            turn["segments"].append(seg)
        elif role == "permission":
            last_assistant()["segments"].append(
                {"kind": "approval", "tool": _tool_name(meta, content)}
            )
        elif role == "error":
            last_assistant()["segments"].append({"kind": "error", "text": content})
    return turns


def _holds_message(turn: dict) -> bool:
    if turn["role"] == "user":
        return True
    return any(s["kind"] == "text" for s in turn["segments"])


def _jump_index(turns: list[dict], i: int) -> int:
    if i < 0 or i >= len(turns):
        return i
    if turns[i]["visible_index"] is not None:
        return int(turns[i]["visible_index"])
    for j in range(i - 1, -1, -1):
        anchor = turns[j]["visible_index"]
        if anchor is None:
            continue
        steps = sum(1 for k in range(j + 1, i + 1) if _holds_message(turns[k]))
        return int(anchor) + steps
    return i


def _turn_label(turn: dict) -> str:
    text = "\n".join(s["text"] for s in turn["segments"] if s["kind"] == "text").strip()
    return _tidy(text) or ("You" if turn["role"] == "user" else "Assistant")


def _event_marker(seg: dict) -> tuple[str, str] | None:
    if seg["kind"] == "tool":
        name = _tidy(seg["tool"]) or "tool"
        return ("subagent" if _SUBAGENT_TOOL.search(name) else "tool", name)
    if seg["kind"] == "approval":
        return ("approval", _tidy(seg["tool"]) or "approval")
    if seg["kind"] == "error":
        return ("error", _tidy(seg["text"]) or "error")
    return None


def build_chat_index(messages: list[dict]) -> list[dict]:
    """Derive the navigation markers for *messages*.

    Bounded to the newest ``INDEX_CAP`` markers: this rides in the session's metadata
    line, which every history listing parses.
    """
    turns = _hydrate_turns(messages)
    entries: list[dict] = []
    for i, turn in enumerate(turns):
        jump = _jump_index(turns, i)
        failed = any(s["kind"] == "tool" and s["failed"] for s in turn["segments"])
        entries.append(
            {
                "id": f"t{i}",
                "kind": "turn",
                "label": _turn_label(turn),
                "role": turn["role"],
                "turn_index": i,
                "jump_index": jump,
                "failed_tool": failed,
            }
        )
        for s, seg in enumerate(turn["segments"]):
            marker = _event_marker(seg)
            if marker is None:
                continue
            kind, label = marker
            entries.append(
                {
                    "id": f"t{i}s{s}",
                    "kind": kind,
                    "label": label,
                    "role": turn["role"],
                    "turn_index": i,
                    "jump_index": jump,
                    "failed_tool": failed,
                }
            )
    return entries[-INDEX_CAP:]


def load_chat_index(raw: object) -> list[dict]:
    """Read a persisted index back, dropping anything a hand edit made unusable."""
    if not isinstance(raw, list):
        return []
    entries: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if any(
            not isinstance(item.get(field), expected)
            or (expected is int and isinstance(item.get(field), bool))
            for field, expected in _ENTRY_TYPES.items()
        ):
            continue
        entries.append({field: item[field] for field in _ENTRY_TYPES})
    return entries
