"""Skill proposals — propose-only auto-skill evolution (skill-evolution-proposal-only).

Auto-skill synthesis used to write straight into the live ``auto/`` skill namespace.
After the documented malicious-skill-drift risk (OpenForge B2), the stance is
**propose, never install**: synthesized skills land in a review QUEUE, and a human
accepts (moves to live) or rejects them. Nothing the system authored autonomously
runs until a person approves it.

A proposal is a JSON record under ``~/.gideon/skills/.proposals/<id>.json``
carrying the synthesized skill (slug/description/triggers/procedure) + provenance +
a **fenced** excerpt of the source trace (so the reviewer sees what drove it without
that text being executable if it's ever re-fed to a model). Accept installs the
proposal — a ``kind="new"`` proposal via the auto-skill writer, a ``kind="refine"``
proposal as a SIDECAR OVERLAY on its named target skill (``overlays.py``; the base
``SKILL.md`` is never rewritten, so a locked skill stays verifiable and revert is a
one-file delete) — and reject drops the record.
The queue is the single sink for autonomous synthesis — there is no auto-install
path (by design).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.record_ids import is_safe_record_id, record_path

logger = logging.getLogger(__name__)

_PROPOSALS_DIRNAME = ".proposals"
_SOURCE_EXCERPT_MAX = 4_000
_MAX_PENDING = 100
_COALESCE_WINDOW_SECONDS = 86_400
_ID_LABEL_MAX = 48
_OLD_ID = re.compile(r"(?P<slug>.+)-(?P<digest>[0-9a-f]{12})", re.DOTALL)


def _proposals_dir() -> Path:
    from gideon.extensions.skills import loader as _loader

    return _loader.config_dir() / "skills" / _PROPOSALS_DIRNAME


def _path(proposal_id: object) -> Path:
    """The ONE expression turning a proposal id into a file in this store.

    ``proposal_id`` reaches here from ``/api/skills/proposals/{id}`` unvalidated, which
    gave a traversal a read and an ``unlink`` outside the home (#459). ``UnsafeRecordId``
    is not an ``OSError``, so ``reject()``'s ``except OSError`` reports the refusal
    instead of swallowing it into ``False``.
    """
    return record_path(_proposals_dir(), proposal_id, kind="proposal_id")


def _last_review_path() -> Path:
    """Where the ladder's most recent pass records itself.

    Deliberately a SIBLING of ``.proposals/`` rather than a file inside it:
    :func:`list_pending` globs ``*.json`` in that directory and coerces every hit
    to a :class:`SkillProposal`, so a marker living there would be a sentinel
    sharing a namespace with real records — silently skipped today by the
    ``except (OSError, ValueError, TypeError)``, and a latent mis-parse the first
    time that constructor grows a default.
    """
    from gideon.extensions.skills import loader as _loader

    return _loader.config_dir() / "skills" / ".ladder_last_review.json"


def record_review(
    *, verdict: str, elapsed_ms: float, session_key: str, detail: str = ""
) -> None:
    """Record that a skill-ladder pass RAN, and how it ended.

    This exists because an empty proposals list is two different facts wearing one
    face: "the ladder ran and had nothing to propose" and "the ladder never ran"
    are the same observation from outside (`G44`). The per-pass log line added by
    `G47` does not separate them on a shipped install either — the verdicts that
    mean the pass worked, ``no_action`` chief among them, log at INFO while the
    default ``log_level`` is WARNING, so the common success is invisible.

    Overwrites: one marker, always the latest pass. A history would be a second
    unbounded store to cap and prune, and the question this answers ("did it run
    at all, and what did it decide?") is answered by the most recent pass.

    Best-effort by construction. This is called from the ``finally`` of the pass,
    so a raising write here would replace the pass's real verdict with an
    unrelated failure — instrumentation must never be the thing that breaks the
    mechanism it observes.
    """
    rec = {
        "verdict": str(verdict),
        "elapsed_ms": int(elapsed_ms),
        "session_key": str(session_key or ""),
        "detail": str(detail or ""),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        p = _last_review_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(rec, indent=2))
    except OSError:
        logger.debug(
            "skill-ladder review: could not record the last-run marker", exc_info=True
        )


def last_review() -> dict | None:
    """The most recent ladder pass, or ``None`` if no pass has ever run.

    ``None`` is the load-bearing value: it is what distinguishes a home where the
    ladder has never fired from one where it fired and proposed nothing.
    """
    try:
        data = json.loads(_last_review_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class SkillProposal:
    """One pending, human-reviewable auto-skill."""

    id: str
    slug: str
    description: str
    triggers: str
    procedure_md: str
    session_key: str
    created_at: str
    kind: str = "new"
    refine_target: str = ""
    trigger: str = ""
    source_excerpt: str = ""
    status: str = "pending"

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The compact view for the inbox list (no full procedure body)."""
        return {
            "id": self.id,
            "slug": self.slug,
            "description": self.description,
            "triggers": self.triggers,
            "kind": self.kind,
            "refine_target": self.refine_target,
            "trigger": self.trigger,
            "session_key": self.session_key,
            "created_at": self.created_at,
            "status": self.status,
            "procedure_preview": self.procedure_md[:280],
        }


def _id_label(slug: str) -> str:
    """The readable half of a proposal id: *slug* in lowercase letters, digits and hyphens.

    Built from that allowlist, never by removing what is unsafe, so no spelling of a name can
    bring a separator, a parent-folder segment or a drive into the id.
    """
    label = (
        re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:_ID_LABEL_MAX].rstrip("-")
    )
    return label or "proposal"


def _make_id(slug: str, session_key: str, created_at: str) -> str:
    """The id of a proposal for *slug*: the name of one file, directly in the proposals folder.

    *slug* is a skill's name, or a model's choice of one, and a skill is named by its folder in
    the library: ``imported/<source>/<name>`` for an imported skill, ``auto/<name>``, any folder
    a user made, in any characters. The id was the slug itself with the digest on the end, so a
    refinement of most skills got an id with slashes in it: the record went into a subfolder the
    listing never reads, the routes refused the id, and the anti-flood rail, which reads the same
    listing, filed the next stumble beside it. The name stays on the record (``slug``,
    ``refine_target``); the id only has to be unique, and to name a file.

    The digest covers the whole slug, so two names that read the same once flattened (``a/b`` and
    ``a-b``) still get two ids.
    """
    h = hashlib.sha1(f"{slug}|{session_key}|{created_at}".encode("utf-8")).hexdigest()[
        :12
    ]
    return f"{_id_label(slug)}-{h}"


def _successor(old_id: object) -> str:
    """The id a proposal the store filed as *old_id* has now, or ``""`` when *old_id* is an id
    of today's shape, or not one the store ever minted.

    Only an id :func:`_make_id` used to mint and the store can no longer address qualifies: the
    slug, a hyphen and the twelve-digit digest, unsafe as a record id because of what the slug
    held. The successor keeps that digest, so it is :func:`_make_id`'s value for the same
    proposal: a re-filing of it after the move is the same record, not a second one. A function
    of the old id alone, because that is all an Inbox row holds of its proposal.
    """
    if not isinstance(old_id, str) or is_safe_record_id(old_id):
        return ""
    old = _OLD_ID.fullmatch(old_id)
    return f"{_id_label(old['slug'])}-{old['digest']}" if old else ""


def _own_slug(prop: SkillProposal) -> str:
    """The slug of the ``auto/`` skill *prop* names as the user's own, or ``""`` when its slug
    holds nothing the auto namespace can spell.

    It is what :func:`accept_target` overlays when no installed skill takes *prop* as a
    refinement, and what :func:`accept` creates when that skill does not exist either. A
    refinement's slug is its skill's whole name (``imported/<source>/<name>``, ``auto/<name>``)
    and a new skill's is a model's choice, while ``create_auto_skill`` takes one lowercase
    segment, so a refinement whose skill had since been removed — which the review surface says
    accepting "would add as a new skill instead" — could only fail. This is that new skill's
    name: the slug in the namespace's alphabet, without the ``auto/`` it may already carry, so a
    deleted ``auto/`` skill comes back under its own name.
    """
    from gideon.extensions.skills.loader import (
        AUTO_SKILL_NAMESPACE,
        _auto_name_from_title,
    )

    return _auto_name_from_title(prop.slug.removeprefix(f"{AUTO_SKILL_NAMESPACE}/"))


def accept_target(*, slug: str, kind: str = "new", refine_target: str = "") -> str:
    from gideon.extensions.skills.loader import _auto_name_from_title

    own = _auto_name_from_title(slug.removeprefix("auto/")) or slug
    return refine_target if kind == "refine" and refine_target else f"auto/{own}"


def _parse_created_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def coalesce_reason(
    *, slug: str, kind: str = "new", refine_target: str = "", created_at: str
) -> str:
    """Explain why a proposal for this accepted subject must not be enqueued."""
    subject = accept_target(slug=slug, kind=kind, refine_target=refine_target)
    for pending in list_pending(_surface=False):
        if (
            accept_target(
                slug=pending.slug,
                kind=pending.kind,
                refine_target=pending.refine_target,
            )
            == subject
        ):
            return f"a proposal for {subject} is already pending"

    incoming = _parse_created_at(created_at) or datetime.now(timezone.utc)
    cutoff = incoming.timestamp() - _COALESCE_WINDOW_SECONDS
    if kind == "refine":
        from gideon.extensions.skills import overlays
        from gideon.extensions.skills.loader import ProcedureLibrary, overlay_identity

        path = ProcedureLibrary(install_builtins=False).skill_file(subject)
        identity = subject
        if path is not None:
            root = path.parent
            for _ in subject.split("/"):
                root = root.parent
            identity = overlay_identity(root, subject)
        accepted = overlays.last_refinement(identity)
        stamp = _parse_created_at(str((accepted or {}).get("created_at", "")))
    else:
        from gideon.extensions.skills.loader import ProcedureLibrary

        path = ProcedureLibrary(install_builtins=False).skill_file(subject)
        meta = ProcedureLibrary._parse_frontmatter(path) if path else {}
        stamp = _parse_created_at(meta.get("created_at", ""))
    if stamp is not None and stamp.timestamp() >= cutoff:
        return f"{subject} accepted a proposal in the last 24h"
    return ""


def enqueue(
    *,
    slug: str,
    description: str,
    triggers: str,
    procedure_md: str,
    session_key: str,
    created_at: str,
    kind: str = "new",
    refine_target: str = "",
    trigger: str = "",
    source_excerpt: str = "",
) -> SkillProposal | None:
    """Add a synthesized skill to the review queue. Returns the proposal, or None
    if the queue is full or inputs are empty. The source excerpt is FENCED so a
    poisoned trace can't direct any model that later renders it."""
    if not (slug and description and procedure_md):
        return None
    reason = coalesce_reason(
        slug=slug,
        kind=kind,
        refine_target=refine_target,
        created_at=created_at,
    )
    if reason:
        logger.info("skill proposal coalesced: %s", reason)
        return None
    d = _proposals_dir()
    if d.is_dir() and len(list(d.glob("*.json"))) >= _MAX_PENDING:
        logger.info("skill-proposal queue full (%d); dropping %r", _MAX_PENDING, slug)
        return None
    fenced = ""
    if source_excerpt:
        try:
            from gideon.security.security import fence_untrusted

            fenced = fence_untrusted(
                source_excerpt[:_SOURCE_EXCERPT_MAX], source="skill-synthesis-trace"
            )
        except Exception:
            fenced = ""
    pid = _make_id(slug, session_key, created_at)
    prop = SkillProposal(
        id=pid,
        slug=slug,
        description=description,
        triggers=triggers,
        procedure_md=procedure_md,
        session_key=session_key,
        created_at=created_at,
        kind=kind,
        refine_target=refine_target,
        trigger=trigger,
        source_excerpt=fenced,
    )
    try:
        atomic_write(_path(pid), json.dumps(prop.to_dict(), indent=2))
    except OSError:
        logger.debug("skill proposal write failed", exc_info=True)
        return None
    logger.info("Queued skill proposal %s (session %s)", pid, session_key)
    _surface_in_inbox(prop)
    return prop


def _surface_in_inbox(prop: SkillProposal) -> None:
    """Raise the proposal as a durable inbox item (plan 42 S4).

    A proposal is a standing request: it waits until the user decides. Before this it lived
    only in the skills page's approval tab, so a proposal synthesized while the user was
    away was invisible unless they went looking. Deduped by proposal id so a re-enqueue of
    the same synthesis can't stack rows.

    Best-effort: if the inbox is unreachable the proposal still exists in its own store and
    the skills page still shows it — surfacing must never be able to fail the enqueue.
    """
    try:
        from gideon.integrations.inbox import ItemKind, emit_attention_item

        state = None
        try:
            from gideon.integrations.inbox_providers.native_source import (
                get_dashboard_state,
            )

            state = get_dashboard_state()
        except Exception:
            logger.debug("proposal inbox surface: no dashboard state", exc_info=True)

        label = "Refine a skill" if prop.kind == "refine" else "New skill proposed"
        emit_attention_item(
            state,
            source="skills",
            kind="proposal",
            item_kind=ItemKind.PROPOSAL.value,
            title=label,
            body=f"{prop.slug} — {prop.description}",
            refs={"skill_proposal": prop.id, "session": prop.session_key},
            dedup_key=f"skill_proposal:{prop.id}",
        )
    except Exception:
        logger.debug("proposal inbox surface failed", exc_info=True)


def _inbox_store_for_write() -> Any:
    """The inbox store a writer in this module must use, or ``None``.

    The RUNNING service's store when one is up, else a fresh file-backed `InboxStore` (headless: a
    CLI accept, a test, a background pass with no gateway — there the file IS the truth).

    🔴 This exists because both writers here constructed `InboxStore()` unconditionally, against
    `inbox.live_store`'s own warning that a writer doing so "writes a row the API cannot see … and
    that the service's next save silently overwrites". Measured on a live instance: three orphan
    rows, each referencing a proposal already accepted, still open and un-clearable, because the
    resolve wrote to a detached copy the service then overwrote (#336).

    The right accessor was already in this file — `_surface_in_inbox`, the WRITE path, goes through
    `get_dashboard_state()` + `emit_attention_item`. Only the RESOLVE path was left behind, which is
    the one-sided shape: whoever fixed the writer did not fix the reader of the same rows.
    """
    from gideon.integrations.inbox import InboxStore, live_store

    state = None
    try:
        from gideon.integrations.inbox_providers.native_source import (
            get_dashboard_state,
        )

        state = get_dashboard_state()
    except Exception:  # noqa: BLE001 — headless is normal, not an error
        logger.debug("proposal inbox write: no dashboard state", exc_info=True)
    live = live_store(state) if state is not None else None
    if live is not None:
        return live
    store = InboxStore()
    store.load()
    return store


def _resolve_inbox_item(pid: str, status: str) -> None:
    """Move the inbox item for *pid* to a terminal status once the user decides.

    Without this the row would sit unresolved forever after the user accepted or rejected
    the proposal on either surface — the inbox would keep claiming attention for work
    already done, which is precisely the "second attention store" problem this plan exists
    to end.

    Accepts a transition FROM a terminal status, because ``accept()`` runs after
    ``reject()`` has already marked the item dismissed and needs to correct it to handled.
    Never moves an item backwards into an open state, which would resurrect it.
    """
    open_or_resolved = ("pending", "seen", "dismissed", "handled")
    try:
        store = _inbox_store_for_write()
        changed = False
        for item in store.items.values():
            if (
                item.refs.get("skill_proposal") == pid
                and item.status in open_or_resolved
            ):
                if item.status != status:
                    item.status = status
                    changed = True
        if changed:
            store.save()
    except Exception:
        logger.debug("proposal inbox resolve failed", exc_info=True)


def _load(pid: str) -> SkillProposal | None:
    try:
        data = json.loads(_path(pid).read_text(encoding="utf-8"))
        return SkillProposal(**data)
    except (OSError, ValueError, TypeError):
        return None


def list_pending(*, _surface: bool = True) -> list[SkillProposal]:
    """All pending proposals, newest-first by created_at."""
    d = _proposals_dir()
    if not d.is_dir():
        return []
    _move_old_records(d)
    out: list[SkillProposal] = []
    for p in d.glob("*.json"):
        try:
            rec = SkillProposal(**json.loads(p.read_text(encoding="utf-8")))
            if rec.status == "pending":
                out.append(rec)
        except (OSError, ValueError, TypeError):
            continue
    out.sort(key=lambda r: r.created_at, reverse=True)
    if _surface:
        backfill_inbox_items(out)
    return out


def _move_old_records(d: Path) -> int:
    """Move every record filed under an id the store cannot address to its :func:`_successor`,
    directly in the folder. Returns how many moved.

    An idempotent backfill keyed on what is on disk, run by the read path. Before ids were built
    for the folder, a proposal about a namespaced skill was written at
    ``.proposals/<namespace>/…/<name>-<digest>.json``, where no listing looks, and a slug with a
    backslash or an over-long one gave a top-level record no route would open. Those are
    proposals still waiting on the user, and nothing expires them, so they are moved rather than
    left behind: each is rewritten under its successor id, the old file goes, and so do the
    folders it leaves empty. A record is moved only from where the old store wrote it (the file's
    path is its id), and a file that does not read as a proposal is left alone.

    The successor is a function of the old id, so two readers moving the same record write the
    same file, and a record whose old file cannot be removed is not left beside its twin: the
    twin would outlive the user's decision on it and bring the proposal back.
    """
    moved = 0
    emptied: set[Path] = set()
    try:
        found = sorted(d.rglob("*.json"))
    except OSError:
        logger.debug("skill proposals: could not walk %s", d, exc_info=True)
        return 0
    for old_file in found:
        if old_file.parent == d and is_safe_record_id(old_file.stem):
            continue
        if (
            any(
                part.is_symlink()
                for part in (old_file, *old_file.parents)
                if part != d.parent
            )
            or not old_file.resolve().is_relative_to(d.resolve())
            or not old_file.is_file()
        ):
            continue
        try:
            rec = SkillProposal(**json.loads(old_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        new_id = _successor(rec.id)
        if (
            not new_id
            or _make_id(rec.slug, rec.session_key, rec.created_at) != new_id
            or old_file.relative_to(d).as_posix() != f"{rec.id}.json"
        ):
            continue
        old_id, rec.id = rec.id, new_id
        target = _path(new_id)
        if target.exists():
            try:
                if json.loads(target.read_text(encoding="utf-8")) != rec.to_dict():
                    continue
            except (OSError, ValueError):
                continue
        try:
            atomic_write(target, json.dumps(rec.to_dict(), indent=2))
        except OSError:
            logger.debug("skill proposal %r: could not write it as %s", old_id, new_id)
            continue
        try:
            old_file.unlink()
        except FileNotFoundError:
            pass  # another reader moved it first
        except OSError:
            logger.debug("skill proposal %r: could not remove its old file", old_id)
            with contextlib.suppress(OSError):
                target.unlink()
            continue
        emptied.update(p for p in old_file.parents if d in p.parents)
        moved += 1
    for folder in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass  # still holds something, which is not this backfill's to remove
    if moved:
        logger.info("moved %d skill proposal(s) to ids the store can address", moved)
    return moved


def _point_rows_at_moved_records(store: Any) -> list[Any]:
    """Point every Inbox row that names its proposal by an old id at the id the record was moved
    to (:func:`_successor`), its dedup key with it. Returns the rows it changed.

    The row is the same request, so it keeps its place, its status and its one notification;
    pointed at the moved record, it opens, accepts and rejects it like any other. Keyed on the
    rows themselves, not on what :func:`_move_old_records` moved in this pass, so a row whose
    record an earlier read moved is still found.
    """
    changed: list[Any] = []
    for row in store.items.values():
        old_id = row.refs.get("skill_proposal")
        new_id = _successor(old_id)
        if not new_id:
            continue
        record = _load(new_id)
        match = _OLD_ID.fullmatch(old_id)
        if (
            record is None
            or not match
            or record.slug != match["slug"]
            or _make_id(record.slug, record.session_key, record.created_at) != new_id
        ):
            continue
        row.refs["skill_proposal"] = new_id
        if row.refs.get("dedup_key") == f"skill_proposal:{old_id}":
            row.refs["dedup_key"] = f"skill_proposal:{new_id}"
        changed.append(row)
    return changed


def _announce_rows(rows: list[Any]) -> None:
    """Send each changed row to every open surface, as every other writer of a row does."""
    from gideon.integrations.inbox_providers.native_source import get_dashboard_state

    state = get_dashboard_state()
    if state is None:
        return
    from gideon.integrations.inbox import redact_item

    for row in rows:
        try:
            state.broadcast_ws("inbox_item_updated", redact_item(row.to_dict()))
        except (
            Exception
        ):  # noqa: BLE001 — a surface missing a frame re-reads; the row is saved
            logger.debug(
                "proposal row %s: could not announce it", row.id, exc_info=True
            )


def backfill_inbox_items(pending: "list[SkillProposal] | None" = None) -> int:
    """Give every pending proposal an inbox item if it doesn't have one. Returns how many.

    T4.2, as an **idempotent backfill keyed on data inspection** rather than a
    `lifecycle/migrations/m_*.py` file (see the plan's *Change discipline*). Proposals
    enqueued before S4 have no item; without this they'd stay invisible in the inbox forever
    while `enqueue` only covers new ones.

    Idempotent **by pid**: `emit_attention_item`'s dedup key is the proposal id, so an
    existing OPEN item is reused and no second notification fires. A proposal the user
    already resolved is deliberately skipped — re-creating an item for it would resurrect a
    decision they'd made.

    Runs from `list_pending()` (the read path both the skills page and the API use), so the
    first look at either surface after an upgrade is already correct.
    """
    props = pending if pending is not None else list_pending(_surface=False)
    if not props:
        return 0

    try:
        store = _inbox_store_for_write()
        changed_rows = _point_rows_at_moved_records(store)
        if changed_rows:
            store.save()
            _announce_rows(changed_rows)
        seen = {
            i.refs.get("skill_proposal")
            for i in store.items.values()
            if i.refs.get("skill_proposal")
        }
    except Exception:
        logger.debug("proposal backfill: inbox read failed", exc_info=True)
        return 0

    made = 0
    for prop in props:
        if prop.id in seen:
            continue
        _surface_in_inbox(prop)
        made += 1
    if made:
        logger.info("surfaced %d pre-existing skill proposal(s) in the inbox", made)
    return made


def get(pid: str) -> SkillProposal | None:
    return _load(pid)


def reject(pid: str, *, accepted: bool = False) -> bool:
    """Drop a proposal (never installed). Returns True if it existed.

    ``accept()`` also calls this to clear the queue entry, so the inbox resolution here is
    deliberately DISMISSED and `accept()` overwrites it with HANDLED afterwards — the
    distinction matters because "I said no" and "I installed it" are different answers, and
    the item's terminal status is the only record of which one the user gave.
    """
    prop = _load(pid)
    try:
        _path(pid).unlink()
        logger.info("Rejected skill proposal %s", pid)
        _resolve_inbox_item(pid, "dismissed")
        if prop is not None:
            from gideon.cognition.feedback import record_feedback

            record_feedback(
                target_kind="proposal_content",
                target_id=pid,
                verdict="up" if accepted else "down",
                producer_kind="skill_synthesis",
                producer_id=accept_target(
                    slug=prop.slug, kind=prop.kind, refine_target=prop.refine_target
                ),
                session_key=prop.session_key,
            )
        return True
    except OSError:
        return False


class AcceptError(Exception):
    """Raised when a proposal can't be accepted (invalid / write failed)."""


@dataclass(frozen=True)
class AcceptResult:
    """What an accept DID — the skill it touched and, for a refine, which version it wrote.

    ``accept`` used to return the bare name, so the one question a refinement raises — "which
    version of this skill did I just approve?" — had no answer anywhere on the accept path.
    ``version`` is the 1-based overlay refinement version (see ``overlays.Refinement``), and
    ``0`` for a ``kind="new"`` accept, which creates a skill rather than versioning one.
    """

    name: str
    version: int = 0


def accept(
    pid: str, *, description: str | None = None, procedure_md: str | None = None
) -> AcceptResult:
    """Accept a pending proposal and clear it from the queue.

    A ``kind="refine"`` proposal that names a resolvable ``refine_target`` applies as a SIDECAR
    OVERLAY on that skill (``skills/overlays.py``) — a single file merged onto the base body at
    load time, never a rewrite of ``SKILL.md``. This is WF2LEA-6's clean break over the old
    in-body append: the base bytes (and a marketplace skill's ``.gideon-lock.json`` hashes) stay
    intact, and reverting the refinement is the deletion of exactly one file. Everything else —
    ``kind="new"``, or a refine whose target no longer exists — CREATES a new ``auto/`` skill.

    Optional ``description``/``procedure_md`` apply reviewer edits. Returns an
    :class:`AcceptResult` naming the written/updated skill AND, for a refine, the refinement
    version it wrote. Raises ``AcceptError`` on failure."""
    prop = _load(pid)
    if prop is None:
        raise AcceptError(f"no proposal {pid!r}")
    from gideon.extensions.skills import overlays
    from gideon.extensions.skills.loader import (
        AUTO_SKILL_NAMESPACE,
        AutoSkillProvenance,
        ProcedureLibrary,
    )

    loader = ProcedureLibrary(install_builtins=False)
    eff_description = description or prop.description
    eff_procedure = procedure_md or prop.procedure_md

    target = ""
    if prop.kind == "refine" and prop.refine_target:
        if loader.load_skill(prop.refine_target) is not None:
            target = prop.refine_target
        else:
            logger.info(
                "refine target %r for proposal %s no longer exists; creating new skill",
                prop.refine_target,
                pid,
            )
    if not target:
        implied = f"{AUTO_SKILL_NAMESPACE}/{_own_slug(prop)}"
        if loader.load_skill(implied) is not None:
            logger.info(
                "proposal %s is labelled %r but %s already exists; overlaying it",
                pid,
                prop.kind,
                implied,
            )
            target = implied

    if target:
        try:
            from gideon.extensions.skills.loader import hold_library, overlay_identity

            with hold_library():
                if _load(pid) is None:
                    raise ValueError("This proposal was already resolved")
                path = loader.skill_file(target)
                if path is None:
                    raise ValueError("The reviewed skill copy no longer exists")
                root = path.parent
                for _ in target.split("/"):
                    root = root.parent
                identity = overlay_identity(root, target)
                records = overlays._records(identity)
                records.append(
                    overlays.Refinement(
                        eff_description, eff_procedure, prop.created_at, prop.trigger
                    ).to_dict()
                )
                overlay_path = overlays.overlay_path(identity)
                if overlay_path is None:
                    raise ValueError("Invalid refinement identity")
                overlays._store(identity, overlay_path, records)
                version = len(records)
                reject(pid, accepted=True)
        except (OSError, ValueError) as exc:
            raise AcceptError(f"could not overlay skill {target!r}: {exc}") from exc
        _resolve_inbox_item(pid, "handled")
        logger.info("Accepted proposal %s → overlaid %s v%d", pid, target, version)
        return AcceptResult(target, version)

    prov = AutoSkillProvenance(session_key=prop.session_key, created_at=prop.created_at)
    created = loader.create_auto_skill(
        _own_slug(prop),
        description=eff_description,
        triggers=prop.triggers,
        procedure_md=eff_procedure,
        provenance=prov,
    )
    if not created:
        raise AcceptError(
            f"could not write skill {prop.slug!r} (invalid, oversized, or exists)"
        )
    reject(pid, accepted=True)
    _resolve_inbox_item(pid, "handled")
    logger.info("Accepted skill proposal %s → %s", pid, created)
    return AcceptResult(created)
