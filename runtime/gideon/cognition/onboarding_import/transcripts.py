"""Read foreign conversation exports into reviewable, separate Gideon journals."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from gideon.cognition.onboarding_import.floors import refuses, safe_text
from gideon.cognition.onboarding_import.model import ImportCategory, ImportItem
from gideon.core.atomic_write import atomic_write

_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_MESSAGES = 20000
_MAX_MESSAGE_CHARS = 100000
_ROLES = {"user", "assistant", "tool"}


def _clean(value: object) -> tuple[str, int]:
    text = str(value) if isinstance(value, str) else ""
    return safe_text(text[:_MAX_MESSAGE_CHARS])


def _parts(value: object) -> tuple[str, list[dict], list[dict], int]:
    if isinstance(value, str):
        text, count = _clean(value)
        return text, [], [], count
    if not isinstance(value, list):
        return "", [], [], 0
    texts: list[str] = []
    calls: list[dict] = []
    results: list[dict] = []
    redactions = 0
    for part in value:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text"):
            text, count = _clean(part.get("text") or part.get("content"))
            if text:
                texts.append(text)
            redactions += count
        elif kind == "tool_use":
            arguments, count = _clean(json.dumps(part.get("input", {}), ensure_ascii=False))
            calls.append({"id": str(part.get("id") or ""), "name": str(part.get("name") or "tool"), "arguments": arguments})
            redactions += count
        elif kind == "tool_result":
            content = part.get("content")
            if isinstance(content, list):
                content = "\n".join(str(piece.get("text") or "") for piece in content if isinstance(piece, dict) and piece.get("type") == "text")
            text, count = _clean(content)
            results.append({"role": "tool", "content": text, "tool_call_id": str(part.get("tool_use_id") or "")})
            redactions += count
    return "\n\n".join(texts), calls, results, redactions


def _timestamp(value: object) -> str:
    if isinstance(value, (float, int)) and value > 0:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    return ""


def _claude_event(event: dict) -> tuple[list[dict], int]:
    if event.get("isMeta") or event.get("isCompactSummary") or event.get("isApiErrorMessage"):
        return [], 0
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
        return [], 0
    stamp = _timestamp(event.get("timestamp"))
    text, calls, results, redactions = _parts(message.get("content"))
    rows = []
    for result in results:
        if result["tool_call_id"]:
            rows.append(dict(result, ts=stamp))
    if text or calls:
        row = {"role": message["role"], "content": text, "ts": stamp}
        if calls:
            row["tool_calls"] = calls
        rows.append(row)
    return rows, redactions


def _hermes_event(event: dict) -> tuple[list[dict], int]:
    rows = []
    redactions = 0
    messages = event.get("messages")
    if not isinstance(messages, list):
        messages = [event] if event.get("role") in _ROLES else []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in _ROLES:
            continue
        text, _, _, count = _parts(message.get("content"))
        redactions += count
        calls = message.get("tool_calls")
        clean_calls = []
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                rendered, count = _clean(json.dumps(call, ensure_ascii=False))
                redactions += count
                try:
                    clean_calls.append(json.loads(rendered))
                except ValueError:
                    clean_calls.append({"redacted": True})
        if text or clean_calls:
            row = {"role": message["role"], "content": text, "ts": _timestamp(message.get("timestamp"))}
            if clean_calls:
                row["tool_calls"] = clean_calls
            if message.get("tool_call_id"):
                row["tool_call_id"] = str(message["tool_call_id"])
            rows.append(row)
    return rows, redactions


def _read_rows(lines: list[str], format: str) -> tuple[list[dict], int]:
    rows: list[dict] = []
    redactions = 0
    for line in lines:
        if len(rows) >= _MAX_MESSAGES:
            break
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        converted, count = _claude_event(event) if format == "claude" else _hermes_event(event)
        rows.extend(converted[: _MAX_MESSAGES - len(rows)])
        redactions += count
    return rows, redactions


def _add(result, source: str, key: str, rows: list[dict], redactions: int) -> None:
    if not rows:
        return
    result.redactions += redactions
    result.items.append(ImportItem(source=source, category=ImportCategory.CONVERSATIONS, key=key,
                                   title=f"Conversation {Path(key).stem}", payload={"messages": rows},
                                   redactions=redactions))


def scan_transcripts(base: Path, result, *, source: str, pattern: str, format: str) -> None:
    for path in sorted(base.glob(pattern)):
        if not path.is_file():
            continue
        if refuses(path):
            result.secrets_skipped += 1
            continue
        try:
            if path.stat().st_size > _MAX_SOURCE_BYTES:
                result.notes.append(f"A {source} conversation exceeded the import size limit and was skipped.")
                continue
            rows, redactions = _read_rows(path.read_text(encoding="utf-8", errors="replace").splitlines(), format)
        except OSError:
            result.notes.append(f"A {source} conversation could not be read.")
            continue
        _add(result, source, path.relative_to(base).as_posix(), rows, redactions)


def scan_hermes_cli(result) -> None:
    try:
        listing = subprocess.run(["hermes", "sessions", "export", "--format", "jsonl", "--dry-run", "-"],
                                 capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        result.notes.append("Hermes conversation export is unavailable; export sessions to the Hermes exports folder to import them.")
        return
    header = re.search(r"Would export (\d+) session", listing.stdout)
    if not header:
        result.notes.append("Hermes returned an unrecognized session listing.")
        return
    identifiers = [line.strip().split()[0] for line in listing.stdout.splitlines()
                   if line.startswith("  ") and line.strip() and line.strip() != "..."]
    if len(identifiers) < int(header.group(1)):
        result.notes.append(f"Hermes listed {len(identifiers)} of {header.group(1)} sessions; export the remaining sessions to files for import.")
    for identifier in dict.fromkeys(identifiers):
        if not re.fullmatch(r"[\w.:-]{1,160}", identifier):
            continue
        try:
            exported = subprocess.run(["hermes", "sessions", "export", "--format", "jsonl", "--session-id", identifier, "-"],
                                      capture_output=True, text=True, timeout=15, check=True)
        except (OSError, subprocess.SubprocessError):
            result.notes.append("A Hermes session could not be exported.")
            continue
        if len(exported.stdout.encode("utf-8")) > _MAX_SOURCE_BYTES:
            result.notes.append("A Hermes session exceeded the import size limit and was skipped.")
            continue
        rows, redactions = _read_rows(exported.stdout.splitlines(), "hermes")
        _add(result, "hermes", identifier, rows, redactions)


def write_transcript(item: ImportItem, api):
    from gideon.cognition.history import ConversationLog

    rows = item.payload.get("messages") if isinstance(item.payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return api._result(item, api.WriteOutcome.REJECTED, detail="conversation has no readable messages")
    key = f"imported_{item.source}_{item.fingerprint}"
    log = ConversationLog()
    path = log._path(key)
    header = {"_type": "metadata", "created_at": rows[0].get("ts") or datetime.now(timezone.utc).isoformat(),
              "last_consolidated": 0, "title": item.title, "import_source": item.source, "import_key": item.key}
    content = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in [header, *rows]) + "\n"
    destination = api._rel_to_home(path)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as existing_file:
                existing_header = json.loads(existing_file.readline())
            same = (isinstance(existing_header, dict)
                    and existing_header.get("import_source") == item.source
                    and existing_header.get("import_key") == item.key)
        except (OSError, ValueError):
            same = False
        return api._result(item, api.WriteOutcome.EXISTING if same else api.WriteOutcome.CONFLICT,
                           destination, "already imported" if same else "this imported conversation has changed; the existing session was kept")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, content)
    api._record(item, destination)
    return api._result(item, api.WriteOutcome.IMPORTED, destination)
