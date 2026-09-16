"""Closed-vocabulary organization proposals and explicit user acceptance."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

RESTRICTED_MODES = frozenset({"temporary", "incognito"})

MAX_TAGS = 2

_MIN_KEYWORD = 3

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "what",
        "when",
        "how",
        "why",
        "can",
        "you",
        "are",
        "was",
        "new",
        "add",
        "fix",
        "get",
        "set",
        "run",
        "use",
        "does",
        "did",
        "about",
        "into",
        "some",
        "help",
        "please",
        "chat",
        "session",
    }
)

_GENERIC_DIRS = frozenset(
    {
        "src",
        "lib",
        "tmp",
        "temp",
        "home",
        "users",
        "documents",
        "downloads",
        "desktop",
        "projects",
        "code",
        "repos",
        "workspace",
        "work",
        "dev",
    }
)

_STORE = "session_organize"


@dataclass
class OrganizeProposal:
    session_key: str
    folder_id: str = ""
    folder_name: str = ""
    tag_names: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    session_title: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.folder_id or self.tag_names)

    def to_dict(self) -> dict[str, Any]:
        values = (
            self.session_key,
            self.folder_id,
            self.folder_name,
            list(self.tag_names),
            self.source,
            self.reason,
            dedup_key_for(self),
        )
        return dict(
            zip(
                (
                    "session",
                    "folder_id",
                    "folder_name",
                    "tags",
                    "source",
                    "reason",
                    "dedup_key",
                ),
                values,
            )
        )


def dedup_key_for(proposal: OrganizeProposal) -> str:
    return ":".join(
        (
            "session_organize",
            str(proposal.session_key),
            str(proposal.folder_id),
            ",".join(sorted(proposal.tag_names)),
        )
    )


def _load_store() -> dict:
    from gideon.extensions.providers.entity_routes import _load_entity_settings

    try:
        document = _load_entity_settings(_STORE)
        if isinstance(document, dict):
            return document
    except Exception:
        pass
    return {}


def _save_store(store: dict) -> None:
    from gideon.extensions.providers.entity_routes import _save_entity_settings

    try:
        _save_entity_settings(_STORE, store)
    except Exception:
        logger.debug("session-organize store save failed", exc_info=True)


class _DeclinedProposals:
    def __init__(self):
        self.document = _load_store()
        declined = self.document.get("declined")
        self.values = declined if isinstance(declined, dict) else {}

    def remember(self, proposal, timestamp):
        self.values[dedup_key_for(proposal)] = float(timestamp)
        self.document["declined"] = self.values
        _save_store(self.document)

    def contains(self, proposal):
        return dedup_key_for(proposal) in self.values


def record_decline(proposal: OrganizeProposal, *, now: float | None = None) -> None:
    ledger = _DeclinedProposals()
    ledger.remember(proposal, time.time() if now is None else now)


def is_declined(proposal: OrganizeProposal) -> bool:
    return _DeclinedProposals().contains(proposal)


def is_unorganized(session: Any) -> bool:
    if getattr(session, "is_restricted", False):
        return False
    if str(getattr(session, "memory_mode", "") or "") in RESTRICTED_MODES:
        return False
    return not str(getattr(session, "folder_id", "") or "") and not list(
        getattr(session, "tags", None) or []
    )


def _keywords(text: str) -> list[str]:
    unique = dict.fromkeys(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return sorted(
        (
            word
            for word in unique
            if len(word) >= _MIN_KEYWORD and word not in _STOPWORDS
        ),
        key=len,
        reverse=True,
    )


class _Vocabulary:
    def __init__(self, entries):
        self.entries = entries
        self.positions: dict[str, set[int]] = {}
        for position, entry in enumerate(entries):
            for word in re.findall(r"[a-z0-9]+", str(entry.get("name") or "").lower()):
                self.positions.setdefault(word, set()).add(position)

    def matches(self, words):
        positions = set()
        for word in words:
            positions.update(self.positions.get(word, ()))
        return [self.entries[position] for position in sorted(positions)]

    def names(self, *, topics=False):
        return {
            str(entry.get("name") or "").lower(): entry
            for entry in self.entries
            if not topics or not entry.get("status")
        }

    def display(self):
        return ", ".join(
            str(entry.get("name") or "") for entry in self.entries if entry.get("name")
        )


def _match_vocabulary(words: list[str], vocab: list[dict]) -> list[dict]:
    return _Vocabulary(vocab).matches(words)


class _ProposalSignals:
    def __init__(self, session, folders=(), tags=()):
        self.session = session
        self.folders = folders
        self.tags = tags

    def proposal(self, source, reason, folder=None, tags=()):
        chosen = folder or {}
        return OrganizeProposal(
            session_key=str(getattr(self.session, "key", "")),
            session_title=str(getattr(self.session, "title", "") or ""),
            folder_id=str(chosen.get("id") or ""),
            folder_name=str(chosen.get("name") or ""),
            tag_names=list(tags),
            source=source,
            reason=reason,
        )

    def title(self):
        words = _keywords(str(getattr(self.session, "title", "") or ""))
        if not words:
            return None
        folders = _match_vocabulary(words, self.folders)
        tags = [
            entry
            for entry in _match_vocabulary(words, self.tags)
            if not entry.get("status")
        ]
        if not (folders or tags):
            return None
        return self.proposal(
            "title",
            "the title matches this folder/tag",
            folders[0] if folders else None,
            [
                str(entry.get("name") or "")
                for entry in tags[:MAX_TAGS]
                if entry.get("name")
            ],
        )

    def workspace(self):
        directory = str(getattr(self.session, "workspace_dir", "") or "")
        base = directory.rstrip("/").rsplit("/", 1)[-1]
        if len(base) < _MIN_KEYWORD or base.lower() in _GENERIC_DIRS:
            return None
        matches = _match_vocabulary(_keywords(base), self.folders)
        return (
            self.proposal("workspace", f"this chat works in {base}", matches[0])
            if matches
            else None
        )

    def channel(self):
        if not bool(getattr(self.session, "_channel_linked", False)):
            return None
        channel = str(getattr(self.session, "_channel_id", "") or "")
        if not channel:
            return None
        matches = [
            entry
            for entry in _match_vocabulary(_keywords(channel), self.tags)
            if not entry.get("status")
        ]
        return (
            self.proposal(
                "channel",
                f"this chat came from {channel}",
                tags=[str(matches[0].get("name") or "")],
            )
            if matches
            else None
        )


def _from_title(
    session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    return _ProposalSignals(session, folders, tags).title()


def _from_workspace(session: Any, folders: list[dict]) -> OrganizeProposal | None:
    return _ProposalSignals(session, folders=folders).workspace()


def _from_channel(session: Any, tags: list[dict]) -> OrganizeProposal | None:
    return _ProposalSignals(session, tags=tags).channel()


def deterministic_proposal(
    session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    proposals = (
        _from_title(session, folders, tags),
        _from_workspace(session, folders),
        _from_channel(session, tags),
    )
    return next(
        (
            proposal
            for proposal in proposals
            if proposal is not None and not proposal.is_empty
        ),
        None,
    )


def is_ambiguous(session: Any, folders: list[dict], tags: list[dict]) -> bool:
    matched = deterministic_proposal(session, folders, tags)
    return (
        matched is None
        and bool(folders or tags)
        and bool(_keywords(str(getattr(session, "title", "") or "")))
    )


def build_llm_prompt(session: Any, folders: list[dict], tags: list[dict]) -> str:
    title = str(getattr(session, "title", "") or "")
    return "\n".join(
        (
            "Classify one chat into an existing organization scheme.",
            "",
            f"Chat title: {title}",
            f"Available folders: {_Vocabulary(folders).display() or '(none)'}",
            f"Available tags: {_Vocabulary(tags).display() or '(none)'}",
            "",
            "Reply with exactly one line:",
            "FOLDER: <folder name or ->  TAGS: <comma-separated tag names or ->",
            "Use ONLY names from the lists above. Never invent a folder or tag. If nothing listed fits, reply exactly NONE.",
        )
    )


@dataclass(frozen=True)
class _ClassificationReply:
    folder: str
    tags: str

    @classmethod
    def read(cls, text):
        lines = (text or "").strip().splitlines()
        first = lines[0].strip() if lines else ""
        if not first or first.upper().startswith("NONE"):
            return None
        match = re.search(r"FOLDER:\s*(.*?)\s*(?:TAGS:\s*(.*))?$", first, re.IGNORECASE)
        return (
            cls((match.group(1) or "").strip(), (match.group(2) or "").strip())
            if match
            else cls("", "")
        )

    def resolve(self, session, folders, tags):
        folder = (
            _Vocabulary(folders).names().get(self.folder.lower(), {})
            if self.folder not in ("", "-")
            else {}
        )
        allowed = _Vocabulary(tags).names(topics=True)
        selected = []
        for candidate in map(str.strip, self.tags.split(",")):
            entry = allowed.get(candidate.lower())
            if entry is not None and len(selected) < MAX_TAGS:
                name = str(entry.get("name") or "")
                if name and name not in selected:
                    selected.append(name)
        proposal = _ProposalSignals(session).proposal(
            "llm", "suggested from the chat's topic", folder, selected
        )
        return None if proposal.is_empty else proposal


def parse_llm_reply(
    text: str, session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    reply = _ClassificationReply.read(text)
    return reply.resolve(session, folders, tags) if reply is not None else None


def _state_vocabulary(state, attribute):
    return [
        entry
        for entry in (getattr(state, attribute, None) or [])
        if isinstance(entry, dict)
    ]


async def propose_for_session(
    state: Any, session: Any, *, allow_llm: bool = True
) -> OrganizeProposal | None:
    if not is_unorganized(session):
        return None
    folders, tags = _state_vocabulary(state, "_folders"), _state_vocabulary(
        state, "_tags"
    )
    candidate = deterministic_proposal(session, folders, tags)
    if candidate is None and allow_llm and is_ambiguous(session, folders, tags):
        candidate = await _llm_proposal(state, session, folders, tags)
    if candidate is None or candidate.is_empty:
        return None
    if not is_declined(candidate):
        return candidate
    logger.debug("session-organize: %s already declined", dedup_key_for(candidate))
    return None


async def _llm_proposal(
    state: Any, session: Any, folders: list[dict], tags: list[dict]
) -> OrganizeProposal | None:
    try:
        from gideon.interfaces.dashboard.chat_title import _stream_background_prompt

        reply = await _stream_background_prompt(
            state, build_llm_prompt(session, folders, tags)
        )
    except Exception:
        logger.debug("session-organize: LLM classification failed", exc_info=True)
        return None
    return parse_llm_reply(reply, session, folders, tags)


@dataclass(frozen=True)
class _ProposalAttention:
    proposal: OrganizeProposal

    def body(self):
        proposal = self.proposal
        labels = []
        if proposal.folder_name:
            labels.append(f"folder “{proposal.folder_name}”")
        if proposal.tag_names:
            labels.append(
                "tags " + ", ".join(f"“{name}”" for name in proposal.tag_names)
            )
        subject = (
            f"“{proposal.session_title}”"
            if proposal.session_title
            else proposal.session_key
        )
        return f"{subject}: {' + '.join(labels)} — {proposal.reason}"

    def refs(self):
        return {
            "session": self.proposal.session_key,
            "session_organize": dedup_key_for(self.proposal),
            "folder_id": self.proposal.folder_id,
            "tags": ",".join(self.proposal.tag_names),
        }

    def resolve(self, state, status):
        from gideon.integrations.inbox import InboxStore, live_store

        store = live_store(state)
        if store is None:
            store = InboxStore()
            store.load()
        key = dedup_key_for(self.proposal)
        matching = [
            item
            for item in store.items.values()
            if item.refs.get("session_organize") == key and item.status != status
        ]
        for item in matching:
            item.status = status
        if matching:
            store.save()


def surface_proposal(state: Any, proposal: OrganizeProposal) -> str:
    from gideon.integrations.inbox import ItemKind, emit_attention_item

    attention = _ProposalAttention(proposal)
    return emit_attention_item(
        state,
        source="skills",
        kind="proposal",
        item_kind=ItemKind.PROPOSAL.value,
        title="Organize an untagged chat",
        body=attention.body(),
        refs=attention.refs(),
        dedup_key=dedup_key_for(proposal),
    )


def resolve_inbox_item(state: Any, proposal: OrganizeProposal, status: str) -> None:
    try:
        _ProposalAttention(proposal).resolve(state, status)
    except Exception:
        logger.debug("session-organize: inbox resolution failed", exc_info=True)


class _AcceptedOrganization:
    def __init__(self, state, proposal):
        self.state = state
        self.proposal = proposal

    def folder(self):
        identifier = self.proposal.folder_id
        return (
            identifier
            if identifier
            and any(
                entry.get("id") == identifier
                for entry in _state_vocabulary(self.state, "_folders")
            )
            else ""
        )

    def tags(self, current):
        from gideon.interfaces.dashboard.chat_tags import (
            _auto_color,
            create_tag,
            find_tag_by_name,
        )

        assigned = list(current)
        for name in self.proposal.tag_names[:MAX_TAGS]:
            entry = find_tag_by_name(self.state, name)
            if entry is None:
                entry = create_tag(self.state, name, color=_auto_color(name))
            if entry is not None:
                identifier = str(entry.get("id") or "")
                if identifier and identifier not in assigned:
                    assigned.append(identifier)
        return assigned


def apply_proposal(
    state: Any, session: Any, proposal: OrganizeProposal
) -> dict[str, Any]:
    from gideon.interfaces.dashboard.chat_persistence import save_session_to_history

    accepted = _AcceptedOrganization(state, proposal)
    folder = accepted.folder()
    if folder:
        session.folder_id = folder
    tags = accepted.tags(list(getattr(session, "tags", None) or []))
    if tags != list(getattr(session, "tags", None) or []):
        session.tags = tags
    applied = {"folder_id": folder, "tags": list(tags)}
    save_session_to_history(state, session, force=True)
    if hasattr(state, "push_sessions_update"):
        state.push_sessions_update()
    logger.info(
        "session-organize: applied folder=%r tags=%s to %s",
        folder,
        applied["tags"],
        proposal.session_key,
    )
    return applied
