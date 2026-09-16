"""Run outputs as Artifacts — the `publish:` declaration and its integrity rules (S47).

The adversarial review caught the original WORK-CONTAINERS draft inventing a second Artifact noun.
Gideon already has a first-class one (`artifacts/`: named, versioned to 50 snapshots,
project-scoped, event-logged, with REST routes and chat tools), so this module adds a
**declaration**
and its rules — never an entity.

What lands:

* **`publish: {artifact, kind}`** on a node, translated into a registry upsert at the same dispatch
  seam `required_artifacts` already uses. A refinement run UPDATES the same artifact by name, which
  is where the artifact's native versioning gives "stable name across revisions" for free.
* **Material-change gating.** The upsert refuses to create a new version when the content has not
  materially changed. Without it, a five-iteration refinement loop burns five of the fifty snapshots
  on identical bodies, and the window that exists to hold real history holds noise instead.
* **Typed lineage.** `SOURCE` → the run/node that produced it, `INFORMED_BY` → evidence and
knowledge
  items it read, `RELATED` → siblings. Typed rather than a flat list because "what made this" and
  "what this resembles" are different questions, and a reader following an untyped edge cannot tell
  which one they are answering.
* **Evidence bundles and the terminal handoff report** — both Artifact compositions, not new
nouns. A
  bundle is one manifest artifact listing what a run produced with a sha256 per file; the handoff
  report is the standardized contract every template's final node emits so the board can render
  "what happened while I slept" without per-template code.

Pure functions over dicts and content strings. The registry call is the caller's — `upsert_plan`
returns the decision (create / new version / no-op) and the payload, so the gating rules are
testable
without a filesystem.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PUBLISHABLE_KINDS = frozenset(
    {"markdown", "text", "json", "html", "csv", "document", "widget", "svg"}
)

MATERIAL_CHANGE_RATIO = 0.02

_NORMALIZE_RE = re.compile(r"\s+")


class Lineage(str, Enum):
    """Typed lineage edges.

    Three kinds because they answer three different questions. "What made this" (SOURCE) is
    provenance; "what did it read" (INFORMED_BY) is evidence; "what is like it" (RELATED) is
    navigation. A flat link list would make a reader unable to tell which they were following, and
    the provenance edge is the one an audit needs to trust.
    """

    SOURCE = "source"
    INFORMED_BY = "informed_by"
    RELATED = "related"


class PublishAction(str, Enum):
    """What an upsert decided.

    `NOOP` is a first-class outcome, not a failure: "nothing material changed" is the correct answer
    for a refinement round that converged, and reporting it as an error would make a converged loop
    look broken.
    """

    CREATE = "create"
    VERSION = "version"
    NOOP = "noop"


@dataclass
class PublishSpec:
    """A node's `publish:` declaration, validated.

    `artifact` is a NAME, not a slug: the whole point of publishing by name is that a refinement run
    lands on the same artifact, and a slug the model invented would fork a second one on the first
    typo.
    """

    artifact: str
    kind: str = "markdown"
    description: str = ""
    lineage: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "kind": self.kind,
            "description": self.description,
            "lineage": {k: list(v) for k, v in self.lineage.items()},
        }


def parse_publish(config: dict[str, Any]) -> tuple[PublishSpec | None, str]:
    """Read a node's `publish:` block. Returns `(spec, error)`.

    An error is RETURNED rather than raised, and an invalid declaration must not silently degrade to
    "no publish": a node whose author declared an output and got nothing would report success while
    producing no artifact, which is the completion-lie class `required_artifacts` exists to catch.
    """
    raw = (config or {}).get("publish")
    if raw is None:
        return None, ""
    if isinstance(raw, str):
        raw = {"artifact": raw}
    if not isinstance(raw, dict):
        return None, "publish must be a name or a {artifact, kind} object"
    name = str(raw.get("artifact", "") or "").strip()
    if not name:
        return None, "publish declares no artifact name"
    kind = str(raw.get("kind", "markdown") or "markdown").strip().lower()
    if kind not in PUBLISHABLE_KINDS:
        return (
            None,
            f"kind {kind!r} is not publishable; expected one of {sorted(PUBLISHABLE_KINDS)}",
        )
    lineage: dict[str, list[str]] = {}
    for edge, targets in (raw.get("lineage") or {}).items():
        if edge not in {e.value for e in Lineage}:
            return (
                None,
                f"unknown lineage edge {edge!r}; expected {[e.value for e in Lineage]}",
            )
        lineage[edge] = [str(t) for t in (targets or []) if str(t).strip()]
    return (
        PublishSpec(
            artifact=name,
            kind=kind,
            description=str(raw.get("description", "") or "").strip(),
            lineage=lineage,
        ),
        "",
    )


def _normalized(content: str) -> str:
    return _NORMALIZE_RE.sub(" ", (content or "").strip()).strip(" .,;:!?-")


def content_hash(content: str) -> str:
    """A stable hash of the NORMALIZED content.

    Normalized, so a body re-emitted with different whitespace hashes the same. An exact-bytes hash
    would report every re-emission as a change, which is precisely the noise the material-
    change gate
    exists to filter.
    """
    return hashlib.sha256(_normalized(content).encode("utf-8")).hexdigest()


def materially_changed(previous: str, current: str) -> tuple[bool, str]:
    """Whether a re-publish earns a new version, and why not when it does not.

    The reason is returned because a silent no-op looks identical to a failed write. A user who
    refined a document and saw no new version needs to know the system judged it unchanged rather
    than that the publish broke.
    """
    if not (current or "").strip():
        return (
            False,
            "new content is empty — refusing to publish an empty body over a real one",
        )
    if not (previous or "").strip():
        return True, "no previous content"
    if content_hash(previous) == content_hash(current):
        return False, "content is identical after normalization"
    prev_norm, cur_norm = _normalized(previous), _normalized(current)
    span = max(len(prev_norm), len(cur_norm)) or 1
    shared = 0
    for a, b in zip(prev_norm, cur_norm):
        if a != b:
            break
        shared += 1
    changed = span - shared
    ratio = changed / span
    if ratio < MATERIAL_CHANGE_RATIO:
        return (
            False,
            f"only {ratio:.1%} of the content changed (below {MATERIAL_CHANGE_RATIO:.0%})",
        )
    return True, f"{ratio:.0%} of the content changed"


@dataclass
class UpsertPlan:
    """What the caller should do, and what to record with it.

    `change_note` rides ALONG with the version rather than replacing the diff: the note is why, the
    version is what. A version with no note is a revision nobody can explain later; a note with no
    version is a claim about a change that did not happen.
    """

    action: PublishAction
    artifact: str
    kind: str
    content: str = ""
    change_note: str = ""
    reason: str = ""
    lineage: dict[str, list[str]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "artifact": self.artifact,
            "kind": self.kind,
            "change_note": self.change_note,
            "reason": self.reason,
            "lineage": {k: list(v) for k, v in self.lineage.items()},
            "meta": dict(self.meta),
        }


def upsert_plan(
    spec: PublishSpec,
    content: str,
    *,
    existing_content: str | None = None,
    run_id: str = "",
    node_id: str = "",
    change_note: str = "",
) -> UpsertPlan:
    """Decide create / new-version / no-op for one publish.

    The provenance `meta` is attached on EVERY action including the no-op, because a reader asking
    "which run produced this" needs an answer even when the latest run changed nothing — otherwise a
    converged refinement loop makes the artifact look abandoned by its own producer.
    """
    meta = {"run_id": run_id, "node_id": node_id}
    lineage = dict(spec.lineage)
    if run_id:
        source = list(lineage.get(Lineage.SOURCE.value) or [])
        marker = f"run:{run_id}" + (f"#{node_id}" if node_id else "")
        if marker not in source:
            source.append(marker)
        lineage[Lineage.SOURCE.value] = source
    if existing_content is None:
        return UpsertPlan(
            action=PublishAction.CREATE,
            artifact=spec.artifact,
            kind=spec.kind,
            content=content,
            change_note=change_note or "first publish",
            reason="no existing artifact by this name",
            lineage=lineage,
            meta=meta,
        )
    changed, why = materially_changed(existing_content, content)
    if not changed:
        return UpsertPlan(
            action=PublishAction.NOOP,
            artifact=spec.artifact,
            kind=spec.kind,
            reason=why,
            lineage=lineage,
            meta=meta,
        )
    return UpsertPlan(
        action=PublishAction.VERSION,
        artifact=spec.artifact,
        kind=spec.kind,
        content=content,
        change_note=change_note or why,
        reason=why,
        lineage=lineage,
        meta=meta,
    )


_MEDIA_REF_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")

MEDIA_HASH_WIDTH = 12

MEDIA_PASSTHROUGH_PREFIX = "@"


def media_filename(reference: str, digest: str) -> str:
    """The content-hash name a copied local file lands under: ``<stem>@<hash>.<ext>``.

    The stem survives so a human reading the versions directory can still tell a chart from a
    screenshot; the digest is what makes the name immutable. Two versions referencing the same
    unchanged file therefore resolve to the SAME copy rather than to two byte-identical ones.
    """
    from pathlib import PurePosixPath

    p = PurePosixPath(reference)
    stem = re.sub(r"[^\w.\-]", "_", p.stem) or "file"
    ext = p.suffix.lower()
    return f"{stem}@{digest[:MEDIA_HASH_WIDTH]}{ext}"


def is_local_media_ref(reference: str) -> bool:
    """Whether a reference names a LOCAL file this publish should copy.

    Remote URLs are already self-contained (the point of the copy is that a workspace file moves and
    the version breaks; a URL does not move because the artifact was versioned). Absolute paths are
    excluded too: a body pointing at `/etc/…` or a user's home is not describing run output, and
    copying it would pull host files into an artifact the UI serves.
    """
    ref = (reference or "").strip()
    if not ref or ref.startswith(MEDIA_PASSTHROUGH_PREFIX):
        return False
    if ref.startswith(("http://", "https://", "data:", "mailto:", "//", "#", "/")):
        return False
    if "://" in ref:
        return False
    return not ref.startswith("~")


@dataclass
class MediaCopy:
    """One local file a publish must copy into the version dir, and its rewritten reference.

    The BYTES ride along rather than being re-read by the writer. Re-reading would open a window in
    which the file changed between the hash and the copy, so the artifact would carry a name
    asserting a digest its content does not have — and a content-addressed name that lies is worse
    than no copy at all.
    """

    reference: str
    filename: str
    sha256: str
    size: int
    data: bytes = b""


def rewrite_media_refs(
    content: str, resolve: Any
) -> tuple[str, list[MediaCopy], list[tuple[str, str]]]:
    """Rewrite local file references to content-hash names, returning the copies to make.

    `resolve(reference)` is the caller's — it returns `(bytes, sha256)` for a readable local file or
    None — so the rules stay testable without a filesystem, the same split the rest of this module
    uses. Returns `(rewritten_content, copies, unresolved)`.

    An UNRESOLVED reference is left EXACTLY as written and reported, never dropped or replaced with
    a placeholder. A body whose broken image silently became `[missing]` would read as though the
    run produced something it did not; leaving the original keeps the break diagnosable and keeps
    the `unresolved` list as the honest record of what could not be self-contained.
    """
    copies: dict[str, MediaCopy] = {}
    unresolved: list[tuple[str, str]] = []

    def _sub(match: re.Match[str]) -> str:
        bang, label, ref = match.group(1), match.group(2), match.group(3)
        if not is_local_media_ref(ref):
            return match.group(0)
        if ref in copies:
            return f"{bang}[{label}]({copies[ref].filename})"
        resolved = resolve(ref)
        if not resolved:
            unresolved.append((ref, "could not read the referenced file"))
            return match.group(0)
        data, digest = resolved
        copy = MediaCopy(
            reference=ref,
            filename=media_filename(ref, digest),
            sha256=digest,
            size=len(data),
            data=data,
        )
        copies[ref] = copy
        return f"{bang}[{label}]({copy.filename})"

    rewritten = _MEDIA_REF_RE.sub(_sub, content or "")
    return rewritten, list(copies.values()), unresolved


@dataclass
class EvidenceFile:
    """One file in an evidence bundle.

    `sha256` is not decoration: an evidence bundle exists so "what did my machine do while I slept"
    has PROOF, and a manifest listing a screenshot without a digest cannot tell you the
    screenshot is
    still the one the run took.
    """

    name: str
    kind: str
    size: int
    sha256: str
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }
        if self.expires_at:
            payload["expires_at"] = self.expires_at
        return payload


BUNDLE_SCHEMA = 1


LINEAGE_KEY_PREFIX = "lineage_"


def flatten_lineage(lineage: dict[str, list[str]]) -> dict[str, str]:
    """Lineage edges as scalar `lineage_<edge>` keys, comma-joined.

    Empty edges are omitted rather than written as "": a key whose value is empty reads as
    "this edge
    was considered and found nothing", which is a claim the publish never made.
    """
    out: dict[str, str] = {}
    for edge, targets in (lineage or {}).items():
        joined = ",".join(str(t) for t in (targets or []) if str(t).strip())
        if joined:
            out[f"{LINEAGE_KEY_PREFIX}{edge}"] = joined
    return out


def parse_lineage(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """The inverse of `flatten_lineage` — read the edges back off an event.

    Shipped WITH the flattener so the round trip is one decision in one place. A writer whose reader
    lives elsewhere is a format that drifts.
    """
    out: dict[str, list[str]] = {}
    for key, value in (metadata or {}).items():
        if not str(key).startswith(LINEAGE_KEY_PREFIX):
            continue
        edge = str(key)[len(LINEAGE_KEY_PREFIX) :]
        targets = [t.strip() for t in str(value).split(",") if t.strip()]
        if targets:
            out[edge] = targets
    return out


def evidence_bundle(
    files: list[EvidenceFile], *, run_id: str = "", summary: str = ""
) -> dict:
    """One evidence bundle manifest — an Artifact composition, not a new entity.

    Files are sorted by name so two runs producing the same evidence produce byte-identical
    manifests. An unstable order would make every bundle look changed to the material-change gate,
    defeating it for exactly the artifact kind that is re-published most.
    """
    return {
        "schema": BUNDLE_SCHEMA,
        "run_id": run_id,
        "summary": summary,
        "files": [f.to_dict() for f in sorted(files, key=lambda f: f.name)],
        "count": len(files),
    }


HANDOFF_SECTIONS = {
    "did": "what was actually done — commands run, files written, actions taken",
    "skipped": "what was NOT done, each with its reason",
    "side_effects": "confirmations about the world: what was committed, pushed, sent, deleted",
    "risks": "known risks the run is aware of and did not resolve",
    "follow_ups": "what a person should do next",
}


def handoff_report(**sections: Any) -> dict[str, Any]:
    """A standardized terminal handoff report.

    Missing sections are filled with an explicit "nothing recorded" rather than omitted. An absent
    `side_effects` section reads as "nothing was committed or sent", which is a claim — and it is
    the claim a user most wants to be true and least wants to be guessed.
    """
    report: dict[str, Any] = {}
    for key, purpose in HANDOFF_SECTIONS.items():
        value = sections.get(key)
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value if str(v).strip()]
        elif value is None or not str(value).strip():
            items = []
        else:
            items = [str(value).strip()]
        report[key] = items or ["nothing recorded"]
        report[f"{key}_purpose"] = purpose
    return report


def skipped_without_reason(report: dict[str, Any]) -> list[str]:
    """Skipped items that do not say WHY.

    A skip with no reason is the least useful line in a handoff report and the most misleading: the
    reader cannot tell a deliberate omission from a silent failure, so they must re-do the work to
    find out.
    """
    out: list[str] = []
    for item in report.get("skipped") or []:
        text = str(item)
        if text == "nothing recorded":
            continue
        if not re.search(
            r"\b(because|since|as|no |missing|unavailable|blocked|failed)\b", text, re.I
        ):
            out.append(text)
    return out


LEDGER_SCHEMA = 1


def ledger_row(
    attempt: int,
    *,
    outcome: str,
    note: str = "",
    reverted: bool = False,
    metrics: dict | None = None,
) -> dict[str, Any]:
    """One results-ledger row. Reverted attempts are recorded, not deleted."""
    return {
        "attempt": int(attempt),
        "outcome": str(outcome),
        "note": str(note),
        "reverted": bool(reverted),
        "metrics": dict(metrics or {}),
    }


def append_ledger_rows(existing: list[dict], rows: list[dict]) -> list[dict]:
    """Append-only, and duplicate attempt numbers are KEPT.

    Two rows for attempt 3 means the attempt was re-run — collapsing them by attempt number would
    hide a retry, which is the single most useful thing a results ledger records.
    """
    return [*(existing or []), *rows]
