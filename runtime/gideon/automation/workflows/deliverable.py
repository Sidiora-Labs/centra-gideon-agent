"""A run's DOCUMENT deliverable — the run-side answer to ``GET /api/loops/{id}/report``.

PP-16 unit 1. The loop cockpit has had this since loops shipped: ``loop/store.read_deliverable``
resolves the kind's declared document (``REPORT.md`` / ``MONITOR_LOG.md`` / ``DESIGN.md``) and
``read_log`` returns the worker's cumulative ``FINDINGS.md``, both redacted, both served off one
route. Measured on ``origin/main`` before this module, the run side answered NONE of it: of the 26
run routes, none served a document, and ``service.outbox`` — the closest thing — lists *published
artifacts*, which is a different noun (a run's document is a file its worker maintains in place; an
artifact is something it deliberately published).

Everything here is a READ over files the run already has. No store, no new event kind, no state
invented and none retired — the same posture the ledger-rails third took, and the reason this unit
was the one that was agent-closable while the other four wait on rulings.

**THE MAPPING IS DRIVEN, NOT ASSERTED.** The filename does not live in a constant here. It is
computed by walking ``loop_aliases`` FORWARD — every ``(kind, variant)`` pair the alias table
declares — and asking that kind's own strategy, through the same ``deliverable_name(loop)`` call
``loop/store.read_deliverable`` and ``loop/watchdog._deliverable_file`` make. So a kind that renames
its document renames it here too, and a kind added to the alias table appears here with no edit.
The direction stays the one ``loop_aliases`` mandates: a kind resolves to a template, never the
reverse. This module never asks "which kind was this template", it asks "which templates do the
kinds produce", and inverts its own forward answer.

**ABSENT IS NOT ZERO, AND ABSENT GETS NAMED.** Five different facts all render as "no document" if
you are careless, and only one of them is a worker that has not written yet:

* ``TEMPLATE_UNKNOWN`` — this run's template is not one a loop kind resolves to at all (26 of the
  33 bundled templates are not loop kinds). Its document name is UNKNOWN, which is not the same as
  it having none.
* ``KIND_HAS_NO_DOCUMENT`` — the template IS a loop kind's, and that kind declares ``""``:
  ``goal-pursuit-verifiable``, ``code-project`` and ``general-project`` produce a passing check or a
  diff, and the code IS the output. A named, declared absence.
* ``NOT_WRITTEN`` — the name is known, a readable root exists, and no such file is in it.
* ``NO_ROOT`` — the run has no directory to read at all (never launched, or swept).
* ``UNREADABLE`` — the file is there and the read failed. Reported, never silently blanked.

**Money is deliberately not here, and that is issue #2566.** ``ledger.reader.run_totals`` reports
``cost_usd 0.0`` / ``tokens 0`` for a LOOP, because ``LoopJournal.cycle`` writes no money keys at
all (loop money lives in ``usage/turns.jsonl`` via ``loop_spend``), and ``introspection.RunStats``
has the same shape. PP-16 retires the loop noun ONTO the run noun, so loop-backed runs will flow
through every run-side surface — and "what did this document cost" answered as ``$0.00`` on the one
page a user opens to find out is the worst place for that bug to land. Three packages consume that
contract and changing it needs an owner ruling, so this payload carries no money field rather than
carrying one that would read zero. The rail (``tests/test_pp16_run_deliverable.py``) asserts the
absence, so a later session cannot add one without meeting the finding.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


TEMPLATE_UNKNOWN = "template_unknown"

KIND_HAS_NO_DOCUMENT = "kind_has_no_document"

NOT_WRITTEN = "not_written"

NO_ROOT = "no_root"

UNREADABLE = "unreadable"

ABSENT_REASONS: frozenset[str] = frozenset(
    {TEMPLATE_UNKNOWN, KIND_HAS_NO_DOCUMENT, NOT_WRITTEN, NO_ROOT, UNREADABLE}
)

ROOT_WORKSPACE = "workspace"
ROOT_RUN_DIR = "run_dir"

MAX_DOC_BYTES = 512 * 1024

STEP_DOCUMENTS_DIR = "step-documents"
STEP_DOCUMENT_MANIFEST = "manifest.json"
MAX_STEP_DOCUMENTS = 40
MAX_STEP_DOCUMENT_TOTAL_BYTES = 4 * 1024 * 1024
MAX_STEP_MANIFEST_BYTES = 32 * 1024
_STEP_DOCUMENT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".adoc"})

MAX_TOKEN_CHARS = 512

_BLOB_RE = re.compile(r"\S{%d,}" % (MAX_TOKEN_CHARS + 1))


@dataclass(frozen=True)
class NameSource:
    """Which loop kind (and variant) declared a template's document name.

    Carried on the wire so the panel can say WHERE the name came from. A filename with no
    provenance is a claim; one that names ``goal``/``monitor`` is a reading of the alias table.
    """

    kind: str
    variant: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "variant": self.variant, "name": self.name}


def template_deliverables() -> dict[str, NameSource]:
    """``{template_name: NameSource}`` for every template a loop kind resolves to.

    Built by walking :mod:`gideon.automation.workflows.loop_aliases` FORWARD and asking each kind's
    strategy for ``deliverable_name`` — the same call the loop side makes. Templates whose kind
    declares no document are INCLUDED with ``name == ""``: "this kind produces no document" is an
    answer, and dropping the row would make it indistinguishable from a template we never saw.

    A variant row wins over the bare-kind row for the same template, because the bare kind resolves
    to the open-ended variant and the variant hint is the more specific statement. Deterministic:
    the bare kinds are walked first, then the variants, so the outcome does not depend on dict order
    across runs.
    """
    from gideon.automation.loop import kinds as kinds_mod
    from gideon.automation.loop.loop import Loop
    from gideon.automation.workflows import loop_aliases

    try:
        kinds_mod.ensure_loaded()
    except (
        Exception
    ):  # pragma: no cover — a kind that fails to import must not 500 a read
        logger.warning(
            "loop kinds unavailable; no template deliverable names", exc_info=True
        )
        return {}

    out: dict[str, NameSource] = {}
    pairs: list[tuple[str, str]] = [
        (k, "") for k in sorted(loop_aliases.KIND_TO_TEMPLATE)
    ]
    pairs += sorted(loop_aliases.VARIANT_HINTS)
    for kind, variant in pairs:
        template = loop_aliases.resolve_kind(kind, variant=variant)
        if not template:
            continue
        name = _declared_name(kinds_mod, Loop, kind, variant)
        if name is None:
            continue
        out[template] = NameSource(kind=kind, variant=variant, name=name)
    return out


def _declared_name(
    kinds_mod: Any, loop_cls: Any, kind: str, variant: str
) -> str | None:
    """What ``kind``'s strategy calls its document for this variant, or None if it cannot answer.

    The variant IS the goal type on the loop side (``kind_config['goal_type']``), which is what
    ``goal.deliverable_name`` reads — so the synthetic loop handed to the strategy carries it there
    rather than anywhere new. None (not ``""``) when the strategy raised or is missing, because "we
    could not ask" and "it answered none" are different facts.
    """
    strategy = kinds_mod.get_or_none(kind)
    if strategy is None:
        return None
    namer = getattr(strategy, "deliverable_name", None)
    if namer is None:
        return ""
    try:
        return str(
            namer(
                loop_cls(id="", name="", kind=kind, task="", kind_config=_cfg(variant))
            )
        )
    except Exception:
        logger.warning("deliverable_name failed for kind %r variant %r", kind, variant)
        return None


def _cfg(variant: str) -> dict[str, Any]:
    return {"goal_type": variant.replace("-", "_")} if variant else {}


@dataclass(frozen=True)
class ResolvedName:
    """The document name a run's template produces, or the named reason there is none."""

    name: str
    reason: str
    source: NameSource | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name or None,
            "reason": self.reason or None,
            "declared_by": self.source.to_dict() if self.source else None,
        }


def resolve_name(workflow_name: str) -> ResolvedName:
    """The document name for a run of ``workflow_name``, with its provenance or its reason."""
    table = template_deliverables()
    source = table.get(workflow_name)
    if source is None:
        return ResolvedName(name="", reason=TEMPLATE_UNKNOWN)
    if not source.name:
        return ResolvedName(name="", reason=KIND_HAS_NO_DOCUMENT, source=source)
    return ResolvedName(name=source.name, reason="", source=source)


@dataclass
class Document:
    """One document slot: present with content, or absent with a NAMED reason. Never both."""

    name: str | None
    present: bool = False
    content: str | None = None
    bytes: int | None = None
    modified_at: float | None = None
    truncated: bool = False
    clipped_blobs: int = 0
    found_in: str | None = None
    absent_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "present": self.present,
            "content": self.content,
            "bytes": self.bytes,
            "modified_at": self.modified_at,
            "truncated": self.truncated,
            "clipped_blobs": self.clipped_blobs,
            "found_in": self.found_in,
            "absent_reason": self.absent_reason,
        }


@dataclass(frozen=True)
class Root:
    """One directory a document may live in, and whether it exists right now."""

    kind: str
    path: str
    exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "exists": self.exists}


@dataclass
class Roots:
    """The ordered roots for one run. Workspace first — see :data:`ROOT_WORKSPACE`."""

    entries: list[Root] = field(default_factory=list)

    @property
    def readable(self) -> list[Root]:
        return [r for r in self.entries if r.exists]

    def to_dict(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.entries]


def run_roots(run: Any) -> Roots:
    """Where a run's documents can be: its provisioned workspace, then its own run dir.

    Workspace-first mirrors ``loop/watchdog._deliverable_file`` exactly, and for the same measured
    reason: the brief directs a worker to write the document into the bound workspace when there is
    one, so resolving the run dir first finds nothing for every isolated run.
    """
    from gideon.automation.workflows import provisioning, store

    entries: list[Root] = []
    run_id = str(getattr(run, "id", "") or "")
    if run_id:
        run_path = store.run_dir(run_id)
        try:
            run_root = run_path.resolve(strict=True)
            if (
                not run_path.is_symlink()
                and run_root.parent == store.runs_root().resolve()
            ):
                path = str(run_root / STEP_DOCUMENTS_DIR)
                entries.append(Root(STEP_DOCUMENTS_DIR, path, _is_dir(path)))
        except (OSError, RuntimeError):
            pass
    try:
        workspace = str((provisioning.workspace_state(run) or {}).get("path", "") or "")
    except Exception:  # pragma: no cover — a malformed record must not 500 a read
        logger.debug("workspace state unreadable", exc_info=True)
        workspace = ""
    if workspace:
        entries.append(Root(ROOT_WORKSPACE, workspace, _is_dir(workspace)))
    if run_id:
        path = str(store.run_dir(run_id))
        entries.append(Root(ROOT_RUN_DIR, path, _is_dir(path)))
    return Roots(entries)


def _document_signature(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_mtime_ns, info.st_size, info.st_ino


def _safe_step_document_name(name: str) -> bool:
    candidate = Path(name)
    return (
        bool(name)
        and candidate.name == name
        and name not in {".", "..", STEP_DOCUMENT_MANIFEST}
        and not name.startswith(".")
        and candidate.suffix.lower() in _STEP_DOCUMENT_SUFFIXES
    )


def step_document_snapshot(workspace: str | Path) -> dict[str, tuple[int, int, int]]:
    """Take a bounded metadata snapshot of direct-child text documents before a step."""
    try:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            return {}
        from gideon.security.security import is_sensitive_path

        if is_sensitive_path(str(root)):
            return {}
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except (OSError, RuntimeError, ValueError):
        return {}
    snapshot: dict[str, tuple[int, int, int]] = {}
    for path in entries:
        if not _safe_step_document_name(path.name) or path.is_symlink():
            continue
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOC_BYTES:
                continue
            resolved = path.resolve(strict=True)
            if (
                resolved.parent != root
                or is_sensitive_path(str(path))
                or is_sensitive_path(str(resolved))
            ):
                continue
            snapshot[path.name] = _document_signature(info)
            if len(snapshot) >= MAX_STEP_DOCUMENTS:
                break
        except (OSError, RuntimeError, ValueError):
            continue
    return snapshot


def retain_step_documents(
    run_id: str,
    workspace: str | Path,
    before: dict[str, tuple[int, int, int]],
    step: str,
) -> list[dict[str, Any]]:
    """Copy only changed direct-child documents into this run's bounded private artifact area."""
    if not run_id or not workspace:
        return []
    try:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            return []
        from gideon.security.security import is_sensitive_path, redact_for_display

        if is_sensitive_path(str(root)):
            return []
        candidates = step_document_snapshot(root)
        if not candidates:
            return []
        from gideon.automation.workflows import store
        from gideon.core.atomic_write import atomic_write

        run_path = store.run_dir(run_id)
        if run_path.is_symlink():
            return []
        run_root = run_path.resolve(strict=True)
        if run_root.parent != store.runs_root().resolve():
            return []
        target_root = run_root / STEP_DOCUMENTS_DIR
        target_root.mkdir(parents=True, exist_ok=True)
        if target_root.is_symlink() or target_root.resolve().parent != run_root:
            return []
        manifest_path = target_root / STEP_DOCUMENT_MANIFEST
        try:
            info = manifest_path.lstat()
            if (
                manifest_path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_STEP_MANIFEST_BYTES
            ):
                rows: object = []
            else:
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
                rows = current.get("documents", []) if isinstance(current, dict) else []
        except (OSError, ValueError, TypeError):
            rows = []
        manifest: dict[str, dict[str, Any]] = {}
        for row in rows[:MAX_STEP_DOCUMENTS] if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "") or "")
            try:
                size = int(row.get("bytes", 0))
                modified_at = float(row.get("modified_at", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if _safe_step_document_name(name) and 0 <= size <= MAX_DOC_BYTES:
                manifest[name] = {
                    "name": name,
                    "bytes": size,
                    "sha256": str(row.get("sha256", ""))[:64],
                    "modified_at": modified_at,
                    "step": str(row.get("step", ""))[:200],
                }
        changed: list[dict[str, Any]] = []
        for name, signature in sorted(candidates.items()):
            if before.get(name) == signature:
                continue
            source = root / name
            if source.is_symlink() or is_sensitive_path(str(source)):
                continue
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(source, flags)
                with os.fdopen(descriptor, "rb") as stream:
                    first = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(first.st_mode)
                        or _document_signature(first) != signature
                    ):
                        continue
                    raw = stream.read(MAX_DOC_BYTES + 1)
                    last = os.fstat(stream.fileno())
            except (OSError, RuntimeError):
                continue
            if len(raw) > MAX_DOC_BYTES or _document_signature(last) != signature:
                continue
            clipped, _count = _clip_blobs(raw.decode("utf-8", errors="replace"))
            content = redact_for_display(clipped)
            data = content.encode("utf-8")
            if len(data) > MAX_DOC_BYTES:
                continue
            atomic_write(target_root / name, content)
            row = {
                "name": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "modified_at": last.st_mtime,
                "step": str(step or "")[:200],
            }
            manifest[name] = row
            changed.append(row)

        ordered = sorted(
            manifest.values(),
            key=lambda row: (
                float(row.get("modified_at", 0) or 0),
                str(row.get("name", "")),
            ),
        )[-MAX_STEP_DOCUMENTS:]
        kept: list[dict[str, Any]] = []
        total = 0
        for row in reversed(ordered):
            size = int(row.get("bytes", 0) or 0)
            if (
                size < 0
                or size > MAX_DOC_BYTES
                or total + size > MAX_STEP_DOCUMENT_TOTAL_BYTES
            ):
                continue
            kept.append(row)
            total += size
        kept.reverse()
        kept_names = {row["name"] for row in kept}
        for name in set(manifest).difference(kept_names):
            path = target_root / name
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        atomic_write(
            manifest_path,
            json.dumps({"version": 1, "documents": kept}, ensure_ascii=False),
        )
        return [row for row in kept if row in changed]
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "run %s: step documents could not be retained", run_id, exc_info=True
        )
        return []


def read_step_documents(run_id: str) -> list[dict[str, Any]]:
    """Read only manifest-listed documents under the selected run's own artifact root."""
    if not run_id:
        return []
    from gideon.automation.workflows import store

    run_path = store.run_dir(run_id)
    if run_path.is_symlink():
        return []
    try:
        run_root = run_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    if run_root.parent != store.runs_root().resolve():
        return []
    directory = run_root / STEP_DOCUMENTS_DIR
    if directory.is_symlink() or not _is_dir(str(directory)):
        return []
    try:
        manifest_path = directory / STEP_DOCUMENT_MANIFEST
        info = manifest_path.lstat()
        if (
            manifest_path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_STEP_MANIFEST_BYTES
        ):
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = manifest.get("documents", []) if isinstance(manifest, dict) else []
    except (OSError, ValueError, TypeError):
        return []
    root = Roots([Root(STEP_DOCUMENTS_DIR, str(directory), True)])
    documents = []
    total = 0
    for row in rows[:MAX_STEP_DOCUMENTS] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "")
        if not _safe_step_document_name(name):
            continue
        document = read_document(root, name)
        if document.present:
            size = int(document.bytes or 0)
            if size < 0 or total + size > MAX_STEP_DOCUMENT_TOTAL_BYTES:
                continue
            total += size
            documents.append(document.to_dict())
    return documents


def _is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:  # pragma: no cover — an unstattable path is simply not a root
        return False


def read_document(roots: Roots, name: str, *, reason: str = "") -> Document:
    """Read ``name`` out of the first root that holds it, redacted, or report a NAMED absence.

    ``reason`` short-circuits: pass the resolver's reason when there is no name to look for, and it
    is returned verbatim rather than re-derived — the resolver knows *why* better than a stat does.

    Redacted through ``ledger.redaction.redact``, the same recursive redactor the run journal
    writer uses and the same screens ``loop/files._redact_str`` applies to the loop side's copy.
    Reused rather than re-derived: a worker-authored document is prose about whatever it was working
    on, and a pasted token in a REPORT.md is exactly how a credential reaches a screenshot.

    Confined: the resolved file must sit inside the root it was reached through. ``name`` comes off
    a strategy declaration rather than a request today, so this is a floor rather than a fix — but a
    document name is one refactor away from being user-settable (``kind_config
    ['primary_deliverable']`` already is, on the loop side), and a traversal that becomes reachable
    later is a traversal.
    """
    if not name:
        return Document(name=None, absent_reason=reason or NOT_WRITTEN)
    readable = roots.readable
    if not readable:
        return Document(name=name, absent_reason=NO_ROOT)
    for root in readable:
        candidate = Path(root.path) / name
        try:
            if candidate.is_symlink() or not stat.S_ISREG(candidate.lstat().st_mode):
                continue
        except OSError:
            continue
        target = _confined(root.path, name)
        if target is None or not target.is_file():
            continue
        try:
            raw = target.read_bytes()
            file_stat = target.stat()
        except OSError:
            logger.warning(
                "run document %s unreadable under %s", name, root.kind, exc_info=True
            )
            return Document(name=name, absent_reason=UNREADABLE, found_in=root.kind)
        body, clipped = _clip_blobs(
            raw[:MAX_DOC_BYTES].decode("utf-8", errors="replace")
        )
        return Document(
            name=name,
            present=True,
            content=_redact(body),
            bytes=len(raw),
            modified_at=file_stat.st_mtime,
            truncated=len(raw) > MAX_DOC_BYTES,
            clipped_blobs=clipped,
            found_in=root.kind,
        )
    return Document(name=name, absent_reason=NOT_WRITTEN)


def _confined(root: str, name: str) -> Path | None:
    """``root/name`` when it resolves INSIDE ``root``, else None."""
    try:
        base = Path(root).resolve()
        target = (base / name).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    return target


def _clip_blobs(text: str) -> tuple[str, int]:
    """Replace every unbroken run past :data:`MAX_TOKEN_CHARS` with a marker. Returns the count.

    Runs BEFORE redaction, and that order is the whole point: the redactor is quadratic in
    unbroken-token length, so clipping afterwards would already have paid the cost this exists to
    avoid. Replacing the whole run rather than truncating it also means a clipped blob cannot leave
    a credential's first 512 characters sitting on the page.
    """
    clipped = 0

    def _mark(match: re.Match[str]) -> str:
        nonlocal clipped
        clipped += 1
        return f"[clipped: {len(match.group(0))}-character run with no whitespace]"

    return _BLOB_RE.sub(_mark, text), clipped


def _redact(text: str) -> str:
    from gideon.security.security import redact_for_display

    return redact_for_display(text)


def instructed_by_spec(spec: Any, name: str) -> bool | None:
    """Does this run's OWN spec name the document its kind declares?

    The reason this exists: measured across the seven bundled templates the five loop kinds resolve
    to, NONE of them mentions ``REPORT.md``, ``MONITOR_LOG.md``, ``DESIGN.md`` or ``RESEARCH.md``
    anywhere. The loop side's brief does (``goal.build_brief`` writes the deliverable name into the
    DoD and the cycle nudge); the template side's prompts do not. So on the run side today, a
    document that is absent is usually absent because nothing ever asked for it — and reporting
    that as "the worker has not written it yet" would send a user to wait for something that is
    never coming.

    ``None`` when there is no name to look for, so "we did not check" stays distinct from "we
    checked and it is not there". A substring scan over the serialized spec rather than a walk of
    prompt fields: the name can legitimately appear in a node prompt, an action argument, a
    workspace setup step or a judge rubric, and a field-by-field walk would answer "no" for the
    ones it had not learned about yet.
    """
    if not name:
        return None
    try:
        import json

        return name in json.dumps(spec, ensure_ascii=False, default=str)
    except (
        TypeError,
        ValueError,
    ):  # pragma: no cover — an unserializable spec answers "unknown"
        logger.debug("spec not serializable; cannot check for %s", name)
        return None
