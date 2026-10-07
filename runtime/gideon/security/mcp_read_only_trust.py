"""Owner-reviewed MCP read-only labels, bound to configuration and exact tool definitions.

Only an unchanged definition in an unchanged allowed server configuration can establish read
    authority. New or changed tools ask until reviewed. Records live in protected owner grant
books; damaged records fail closed and strict writes preserve them. Listing changes also produce
    quiet description notices for servers whose labels are untrusted.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.security.owner_grants import GrantBook, GrantBookError

logger = logging.getLogger(__name__)

#: The record of the owner's trust, by name under ``grants/``.
TRUST_RECORD = "mcp_read_only"
#: The descriptions of each server's tools as last seen, by name under ``grants/``.
SEEN_RECORD = "mcp_tool_descriptions"

#: The parts of a definition digested alone, so a review can say what changed.
PARTS: tuple[str, ...] = ("description", "inputSchema", "annotations")

#: The input schema the client gives a tool listed with none (`mcp_client._refresh_tools`).
_NO_INPUTS: dict[str, Any] = {"type": "object", "properties": {}}

_HEX = frozenset("0123456789abcdef")


# ── a tool's definition, and its digest ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Definition:
    """One tool as its server listed it: the four things the digest is taken of."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    annotations: Mapping[str, Any]

    def as_listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "annotations": dict(self.annotations),
        }


def definition(tool: Any) -> Definition:
    """*tool* as a :class:`Definition`: a listed spec (``name``, ``description``, ``input_schema``,
    ``annotations``), or a probe's row (``inputSchema`` for the schema)."""
    if isinstance(tool, Mapping):
        name, description = tool.get("name"), tool.get("description")
        schema, labels = tool.get("inputSchema"), tool.get("annotations")
    else:
        name, description = getattr(tool, "name", ""), getattr(tool, "description", "")
        schema, labels = getattr(tool, "input_schema", None), getattr(
            tool, "annotations", None
        )
    return Definition(
        name=name if isinstance(name, str) else "",
        description=description if isinstance(description, str) else "",
        input_schema=schema if isinstance(schema, Mapping) else _NO_INPUTS,
        annotations=labels if isinstance(labels, Mapping) else {},
    )


def _canonical(value: Any) -> bytes:
    """*value* serialized the one way a digest is taken of it (see the module docstring)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(tool: Any) -> str:
    """The digest of *tool*'s definition: what the owner's trust in it is sealed to."""
    return _sha256(_canonical(definition(tool).as_listed()))


def part_digests(tool: Any) -> dict[str, str]:
    """The digest of each of *tool*'s description, input schema and labels, alone."""
    listed = definition(tool).as_listed()
    return {part: _sha256(_canonical(listed[part])) for part in PARTS}


def is_digest(value: object) -> bool:
    """Whether *value* reads as a digest: 64 lowercase hex characters."""
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


# ── the record ──────────────────────────────────────────────────────────────────────────────────


def _book(record: str) -> GrantBook:
    return GrantBook(record)


def _path(record: str) -> Path:
    return _book(record).path


def _identity(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return info.st_mtime_ns, info.st_size, info.st_ino


def _servers(record: str, *, strict: bool = False) -> dict[str, Any]:
    return _book(record)._read(strict=strict)


@contextmanager
def _locked(record: str) -> Iterator[None]:
    with _book(record)._locked():
        yield


def _write(record: str, servers: dict[str, Any]) -> None:
    # Every owner record remains compatible with the protected grant-book envelope.
    _book(record)._write(
        {name: {"seal": "observed", **entry} for name, entry in servers.items()}
    )


def configuration_revision(server: str) -> str:
    from gideon.integrations.mcp_discovery import list_servers
    from gideon.security import mcp_grants

    configured = next((item for item in list_servers() if item.name == server), None)
    if configured is None or not mcp_grants.allowed(configured):
        return ""
    try:
        return mcp_grants.revision(configured)
    except (mcp_grants.McpGrantDefinitionError, ValueError, TypeError):
        return ""


def _sealed_tools(server: str) -> dict[str, dict[str, str]] | None:
    """The tools the owner's trust in *server* is sealed to, by name, or ``None`` when the owner
    does not trust its labels. An entry that is not one the Tools page writes seals nothing.
    """
    entry = _servers(TRUST_RECORD).get(server)
    if (
        not isinstance(entry, dict)
        or not entry.get("configuration")
        or entry["configuration"] != configuration_revision(server)
    ):
        return None
    tools = entry.get("tools")
    if not isinstance(tools, dict):
        return None
    return {
        str(name): seal
        for name, seal in tools.items()
        if isinstance(seal, dict) and is_digest(seal.get("digest"))
    }


# ── the reads ───────────────────────────────────────────────────────────────────────────────────


def believes(server: str, tool: Any) -> bool:
    """Whether *tool*'s read-only label is believed: the owner trusts *server*'s labels, and the
    trust is sealed to *tool* exactly as it is defined now. THE read the approval gate takes a label
    through (`ConfiguredMcpToolProvider.list_tools`). A tool added or changed since the owner trusted the
    server, or last reviewed it, is not believed, and asks like any untrusted server's tool.
    """
    sealed = _sealed_tools(server)
    if not sealed:
        return False
    found = sealed.get(definition(tool).name)
    return found is not None and found.get("digest") == digest(tool)


def holds_trust(server: str) -> bool:
    """Whether the owner trusts *server*'s read-only labels at all. For the words that say why a
    tool is not believed, and for the Tools page's switch: never a tool's answer, which is
    :func:`believes`."""
    return _sealed_tools(server) is not None


@dataclass(frozen=True)
class Change:
    """A tool whose definition changed since the trust, and which of its parts did."""

    name: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    """What a server's listing is, against the owner's trust in its labels."""

    trusted: bool
    #: When the owner trusted the labels, or last reviewed them ("" without trust).
    at: str = ""
    #: Listed now, and not when the trust was given.
    added: tuple[str, ...] = ()
    #: Listed both times, defined differently now.
    changed: tuple[Change, ...] = ()
    #: Gone from the listing since.
    removed: tuple[str, ...] = ()
    #: Every tool listed now, with its digest: what a Trust or a Review seals. ``None`` while no
    #: listing of the server as it is defined now is known, which says nothing of what changed.
    listed: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "trusted": self.trusted,
            "listed": None if self.listed is None else dict(self.listed),
        }
        if self.trusted:
            out["at"] = self.at
        if self.trusted and self.listed is not None:
            out.update(
                added=list(self.added),
                changed=[
                    {"name": c.name, "parts": list(c.parts)} for c in self.changed
                ],
                removed=list(self.removed),
            )
        return out


def review(server: str, tools: Iterable[Any] | None) -> Review:
    """*tools* (the server's listing now, or ``None`` while none is known) against the owner's
    trust in *server*'s labels: which were added, changed or removed since it was given, and the
    digest of each listed now."""
    sealed = _sealed_tools(server)
    entry = _servers(TRUST_RECORD).get(server)
    at = (
        str(entry.get("at") or "")
        if sealed is not None and isinstance(entry, dict)
        else ""
    )
    if tools is None:
        return Review(trusted=sealed is not None, at=at)
    now = {d.name: d for d in map(definition, tools) if d.name}
    listed = {name: digest(d) for name, d in sorted(now.items())}
    if sealed is None:
        return Review(trusted=False, listed=listed)
    changed = []
    for name, d in sorted(now.items()):
        seal = sealed.get(name)
        if seal is None or seal.get("digest") == listed[name]:
            continue
        parts = part_digests(d)
        changed.append(Change(name, tuple(p for p in PARTS if seal.get(p) != parts[p])))
    return Review(
        trusted=True,
        at=at,
        added=tuple(sorted(set(now) - set(sealed))),
        changed=tuple(changed),
        removed=tuple(sorted(set(sealed) - set(now))),
        listed=listed,
    )


# ── the owner's writes ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Sealed:
    """What a Trust or a Review recorded."""

    #: The tools the trust now covers.
    sealed: tuple[str, ...]
    #: Reviewed, and defined differently by the time the review landed: they still ask.
    changed_since: tuple[str, ...]


def seal(
    server: str, tools: Iterable[Any], seen: Mapping[str, str], *, configuration: str
) -> Sealed:
    """Record the owner's trust in *server*'s labels, sealed to the tools they reviewed. *seen* is
    each tool the page showed them, with the digest it showed; *tools* is the server's listing now.
    A tool is sealed when the listing still defines it as shown. One defined differently by now, or
    no longer listed, is left out: it asks until the owner reviews it again. Replaces the record
    the server had, so a tool reviewed before and not shown this time is no longer covered.

    Only an owner surface calls this, after its question."""
    now = {d.name: d for d in map(definition, tools) if d.name}
    kept: dict[str, dict[str, str]] = {}
    changed_since: list[str] = []
    for name, shown in sorted(seen.items()):
        d = now.get(name)
        if d is None:
            continue
        if digest(d) != shown:
            changed_since.append(name)
            continue
        kept[name] = {"digest": shown, **part_digests(d)}
    from datetime import datetime, timezone

    def utc_now_iso():
        return datetime.now(timezone.utc).isoformat()

    with _locked(TRUST_RECORD):
        servers = dict(_servers(TRUST_RECORD, strict=True))
        if not configuration or configuration_revision(server) != configuration:
            raise ValueError("The reviewed server configuration changed")
        servers[server] = {
            "at": utc_now_iso(),
            "configuration": configuration,
            "tools": kept,
        }
        _write(TRUST_RECORD, servers)
    return Sealed(sealed=tuple(sorted(kept)), changed_since=tuple(changed_since))


def _drop(record: str, server: str) -> bool:
    """Take *server* out of *record*. True when it was there."""
    if server not in _servers(record):
        return False
    with _locked(record):
        servers = dict(_servers(record, strict=True))
        if servers.pop(server, None) is None:
            return False
        _write(record, servers)
    return True


def revoke(server: str) -> bool:
    """Stop trusting *server*'s labels. True when the owner had trusted them."""
    return _drop(TRUST_RECORD, server)


def forget(server: str) -> None:
    """*server* is gone: the trust in its labels and what was seen of its tools go with it, so a
    server added again under its name starts from neither."""
    _drop(TRUST_RECORD, server)
    _drop(SEEN_RECORD, server)
    _last_listing.pop(server, None)


# ── what a listing tells the owner ──────────────────────────────────────────────────────────────

#: Each server's last listing in this process: every tool's name and digest.
_last_listing: dict[str, tuple[tuple[str, str], ...]] = {}
#: Counts the listings that differed from the one before them (:func:`stamp`).
_listings_changed = 0
#: ``(server, tools whose description changed) -> None``, told of a description that changed on a
#: server whose labels the owner does not trust (:func:`observe`).
_listeners: list[Callable[[str, tuple[str, ...]], None]] = []


def subscribe(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    """Be told, with the server and the tools, of each description that changed on a server whose
    labels the owner does not trust (idempotent). The dashboard subscribes at start and raises the
    quiet notice."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Callable[[str, tuple[str, ...]], None]) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def stamp() -> tuple[object, ...]:
    """A value that changes whenever a tool's verdict can: the owner's trust was written, or a
    server listed its tools differently. A session's catalog holds each tool's verdict, so the
    runtime reads this at each turn (`NativeAgentRuntime._refresh_mcp_inventory`) and judges its tools
    again when it differs."""
    try:
        written: object = _identity(_path(TRUST_RECORD))
    except FileNotFoundError:
        written = "absent"
    except OSError:
        written = "unreadable"
    return (_listings_changed, written)


def description_notice(server: str, tools: tuple[str, ...]) -> tuple[str, str]:
    """The quiet notice's title and body: *server* changed the description of *tools*. Product
    copy."""
    if len(tools) == 1:
        title = f"{server} changed the description of its tool {tools[0]}"
    else:
        title = f"{server} changed the descriptions of {len(tools)} of its tools"
        title += f": {', '.join(tools)}" if len(tools) <= 5 else ""
    body = (
        "A tool's description is text the model reads when it decides what to call, so look at "
        f"what {'it says' if len(tools) == 1 else 'they say'} now on the Tools page."
    )
    return title, body


def observe(server: str, tools: Iterable[Any]) -> None:
    """A start of *server* listed *tools* (`mcp_discovery.note_start`, for the server as it is
    defined now). Never raises: what it found has already happened."""
    global _listings_changed

    defs = [d for d in map(definition, tools) if d.name]
    listing = tuple(sorted((d.name, digest(d)) for d in defs))
    before = _last_listing.get(server)
    _last_listing[server] = listing
    if before is not None and before != listing:
        _listings_changed += 1
    try:
        changed = _note_descriptions(server, defs)
    except (
        Exception
    ):  # noqa: BLE001 - a record that cannot be written must not fail the start
        logger.warning(
            "MCP tool descriptions for %s could not be recorded", server, exc_info=True
        )
        return
    if not changed or holds_trust(server):
        return
    for listener in list(_listeners):
        try:
            listener(server, changed)
        except (
            Exception
        ):  # noqa: BLE001 - the owner is told elsewhere if this one fails
            logger.warning(
                "MCP description notice for %s failed", server, exc_info=True
            )


def _note_descriptions(server: str, defs: list[Definition]) -> tuple[str, ...]:
    """Keep each listed tool's description digest as the one last seen of *server*, and return the
    tools whose description differs from the one seen before. A tool seen for the first time has
    nothing to differ from; one no longer listed keeps what was seen of it."""
    current = {d.name: _sha256(_canonical(d.description)) for d in defs}
    try:
        held = _servers(SEEN_RECORD, strict=True).get(server)
    except GrantBookError:
        # Nothing seen before can be compared, and the record is never written over: no notice.
        return ()
    before = held if isinstance(held, dict) else {}
    changed = tuple(
        sorted(n for n, h in current.items() if n in before and before[n] != h)
    )
    if all(before.get(n) == h for n, h in current.items()):
        return changed
    with _locked(SEEN_RECORD):
        seen = dict(_servers(SEEN_RECORD, strict=True))
        entry = seen.get(server)
        merged = dict(entry) if isinstance(entry, dict) else {}
        merged.update(current)
        seen[server] = merged
        _write(SEEN_RECORD, seen)
    return changed
