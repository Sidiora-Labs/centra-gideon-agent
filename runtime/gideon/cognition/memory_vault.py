"""Memory ↔ markdown vault (mem-fs-mirror; MEMORY-GRAPH-AND-VAULT §5).

A projection of the memory store into an Obsidian-compatible markdown vault under
``~/.gideon/memory-vault/`` so a human can browse memory as a linked
knowledge graph (Obsidian graph view + backlinks) instead of squinting at
``memory.db`` — and, in ``two_way`` mode, **edit it back**.

``memory.vault_mode`` (§5.1) picks how far that goes:

``off``
    No vault.
``mirror``
    One direction. The vault is a pure projection of ``MemoryService.get_records()``,
    regenerated from the store and never read back. A hand edit is overwritten — that
    is what "mirror" means, and the config help says so.
``two_way``
    The sync pass reads edited pages back through the normal ``MemoryService`` write
    path before re-projecting (§5.2).

Design — one path, no dual write surface:
  * Reconciliation happens at natural memory-write boundaries (post-consolidation
    seal, the maintenance cadence, and an explicit ``POST /api/memory/vault/sync``),
    NOT by instrumenting every write method or watching the filesystem. The vault
    stays a derived artifact: idempotent, rebuildable from scratch.
  * A content-hash manifest (``.vault-manifest.json``) makes each sync O(changed):
    only pages whose rendered markdown changed are rewritten, and files for records
    that no longer exist are pruned. A full rebuild == delete the manifest.
  * ``[[wikilinks]]`` are derived from **real relations** — ``mem_links`` graph edges,
    supersession chains, shared tags (via tag-hub pages), and session grouping —
    never from scraping the page text. A link the graph does not have cannot appear.

**``source_hash`` is the safety mechanism, and it covers the BODY ONLY.** Every page
carries ``source_hash`` = a hash of everything below the frontmatter fence. The sync
pass rewrites the frontmatter on every pass (counters, ``last_updated``, the hash
itself), so a hash that covered the frontmatter would never match its own page again
and every page would read as hand-edited forever. Body-only also buys the conflict
flag for free: stamping ``sync_conflict`` into the frontmatter of a page we refuse to
touch does not change its body hash, so the page stays flagged until the human
resolves it.

Two-way is *lossy in one direction only*: a page the parser cannot read with
confidence is **left exactly as the human wrote it**, flagged in frontmatter, and
reported by the vault lint. It is never overwritten and never dropped.

Safety: memory text is often *untrusted* (episodic fragments, tool outputs), and a
vault page is worse — a human can paste anything into it. YAML frontmatter values are
JSON-encoded (JSON is a strict subset of YAML), so a value containing ``---`` /
newlines / quotes can never break out of the frontmatter fence or forge extra keys.
Edits read back are written with ``source="vault_edit"``, which is deliberately NOT in
``MemoryService._TRUSTED_WRITE_SOURCES``: the *human's intent* is authoritative, the
*bytes* are not, so they pass the S5 injection scan like any other untrusted write.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import MEMORY_VAULT_MODES

if TYPE_CHECKING:
    from gideon.cognition.memory_graph import Entity
    from gideon.cognition.memory_record import MemoryRecord
    from gideon.cognition.memory_service import MemoryService

logger = logging.getLogger(__name__)

_MANIFEST_NAME = ".vault-manifest.json"
_INDEX_NAME = "MEMORY.md"

_KIND_DIR: dict[str, str] = {
    "semantic": "facts",
    "preference": "facts",
    "note": "facts",
    "lesson": "lessons",
    "episodic": "episodic",
    "procedural": "procedural",
    "commitment": "commitments",
    "self_persona": "persona",
}
_TAGS_DIR = "tags"
_ENTITIES_DIR = "entities"
_RAW_DIR = "raw"
_RAW_DONE_DIR = "raw/.ingested"

#: ``type:`` values this projection actually writes. §5.3's vocabulary also names
#: ``connection`` and ``qa`` pages; nothing generates those yet, so they are not
#: declared here — a type nobody writes reads as a decision and behaves as an
#: omission.
_PAGE_TYPE: dict[str, str] = {
    "semantic": "concept",
    "preference": "concept",
    "note": "concept",
    "lesson": "concept",
    "procedural": "concept",
    "commitment": "concept",
    "self_persona": "concept",
    "approval": "concept",
    "slot": "slot",
    "episodic": "synthesis",
}

_RECORD_PAGE_TYPES = frozenset({"concept", "slot", "synthesis"})

_EDITABLE_KINDS = frozenset({"semantic", "preference", "note", "slot"})

GENERATED_MARKER = "<!-- gideon:generated (replaced on every sync) -->"

HASH_KEY = "source_hash"
CONFLICT_KEY = "sync_conflict"

_FM_ORDER = (
    "id",
    "kind",
    "tier",
    "scope",
    "scope_ref",
    "category",
    "confidence",
    "importance",
    "recall_count",
    "visit_count",
    "source",
    "contributor",
    "conversation_id",
    "tags",
    "created_at",
    "updated_at",
    "superseded_by",
    "invalidated_at",
    "due_window",
    "channel",
    "dismissed_at",
)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_BODY = 20_000
_MAX_BACKLINKS = 1_000
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)")


def slug(raw: str, *, fallback: str = "record") -> str:
    cleaned = _UNSAFE_CHARS.sub("-", raw).strip("-._") or fallback
    suffix = (
        ""
        if cleaned == raw
        else "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    )
    return (cleaned + suffix)[:120]


def _yaml_scalar(value: object) -> str:
    scalar = value if isinstance(value, (bool, int, float)) else str(value)
    return json.dumps(scalar, ensure_ascii=False)


def frontmatter(fields: list[tuple[str, object]]) -> str:
    entries = []
    for name, value in fields:
        if isinstance(value, list):
            if not value:
                continue
            encoded = "[" + ", ".join(map(_yaml_scalar, value)) + "]"
        else:
            encoded = _yaml_scalar(value)
        entries.append(f"{name}: {encoded}")
    return "\n".join(("---", *entries, "---"))


def split_page(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        remainder = text[3:].removeprefix("\n")
        header, fence, body = remainder.partition("\n---")
        if fence:
            return header, body.lstrip("\n")
    return "", text


def parse_frontmatter(block: str) -> dict[str, object]:
    result = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition(":")
        if separator and name.strip() and not name.startswith((" ", "\t")):
            value = value.strip()
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                decoded = value.strip('"')
            result[name.strip()] = decoded
    return result


def body_hash(body: str) -> str:
    normalized = re.sub(r"\r\n?", "\n", body).strip()
    digest = hashlib.sha256(normalized.encode("utf-8"))
    return digest.hexdigest()[:32]


def compose_page(fields: list[tuple[str, object]], body: str) -> str:
    content = body.rstrip() + "\n"
    metadata = list(fields)
    metadata.append((HASH_KEY, body_hash(content)))
    return "\n\n".join((frontmatter(metadata), content))


class RenderedNote:
    __slots__ = ("relpath", "content", "links", "tags", "title")

    def __init__(
        self, relpath: str, content: str, links: set[str], tags: list[str], title: str
    ) -> None:
        self.relpath, self.content, self.links, self.tags, self.title = (
            relpath,
            content,
            links,
            tags,
            title,
        )


def _record_basename(rec: "MemoryRecord") -> str:
    return slug(raw=rec.id, fallback=rec.kind.value)


def _record_title(rec: "MemoryRecord") -> str:
    from gideon.cognition.memory_record import MemoryKind

    if rec.kind != MemoryKind.EPISODIC:
        return rec.id
    title = " ".join((rec.text or "").split())
    if not title:
        return f"episodic {rec.id[:8]}"
    if len(title) > 72:
        title = title[:72]
        boundary = title.rfind(" ")
        if boundary >= 40:
            title = title[:boundary]
        return title.rstrip(" ,.;:") + "…"
    return title


def render_record(
    rec: "MemoryRecord", *, entities: "list[Entity] | None" = None
) -> RenderedNote:
    from gideon.cognition.vault_pages import RecordPage

    return RecordPage(rec, entities).render()


def _entity_basename(entity: "Entity") -> str:
    return slug(raw=entity.name, fallback=entity.id)


def render_tag_hub(tag: str, members: list[tuple[str, str]]) -> RenderedNote:
    from gideon.cognition.vault_pages import membership_page

    return membership_page("tag", tag, members)


def render_session_hub(
    conversation_id: str, members: list[tuple[str, str]]
) -> RenderedNote:
    from gideon.cognition.vault_pages import membership_page

    return membership_page("session", conversation_id, members)


def render_index(
    records: list["MemoryRecord"],
    *,
    entities: "list[Entity] | None" = None,
    mode: str = "mirror",
) -> RenderedNote:
    from gideon.cognition.vault_pages import VaultIndexPage

    return VaultIndexPage(records, entities, mode).render()


_TIMELINE_HEADING = "## Timeline"
_COMPILED_HEADING = "## Compiled"
_BACKLINKS_HEADING = "## Backlinks"


def timeline_lines(body: str) -> list[str]:
    timeline: list[str] = []
    entered = False
    for line in body.splitlines():
        if line.strip() == _TIMELINE_HEADING:
            entered = True
        elif entered and line.startswith("## "):
            return timeline
        elif entered and line.startswith("- "):
            timeline.append(line)
    return timeline


def render_entity_page(
    entity: "Entity",
    *,
    compiled: list[tuple[str, str]],
    backlinks: list[str],
    evidence: list[str],
    existing_body: str = "",
) -> RenderedNote:
    from gideon.cognition.vault_pages import entity_page

    return entity_page(entity, compiled, backlinks, evidence, existing_body)


class MemoryVault:
    def __init__(
        self, service: "MemoryService", vault_dir: Path, *, mode: str = "mirror"
    ) -> None:
        self._svc, self._dir = service, vault_dir
        self._mode = mode if mode in MEMORY_VAULT_MODES else "mirror"

    @property
    def path(self) -> Path:
        return self._dir

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def two_way(self) -> bool:
        return self.mode == "two_way"

    def _manifest_path(self) -> Path:
        return self._dir.joinpath(_MANIFEST_NAME)

    def _load_manifest(self) -> dict[str, str]:
        try:
            content = self._manifest_path().read_text(encoding="utf-8")
            return json.loads(content)
        except (OSError, ValueError):
            return {}

    def status(self) -> dict:
        manifest = self._load_manifest()
        return dict(
            path=str(self._dir),
            files=len(manifest),
            exists=self._dir.exists(),
            mode=self._mode,
        )

    def _graph_view(
        self,
    ) -> tuple[
        list["Entity"], dict[tuple[str, str], list["Entity"]], dict[str, list[dict]]
    ]:
        from gideon.cognition.vault_reconcile import GraphProjection

        return GraphProjection(self._svc).read()

    @staticmethod
    def _link_key(rec: "MemoryRecord") -> tuple[str, str]:
        storage_kind = "episodic" if rec.kind.value == "episodic" else "semantic"
        return storage_kind, rec.id

    def sync(self, *, knowledge: object = None, enqueue: object = None) -> dict:
        from gideon.cognition.vault_reconcile import VaultCycle

        return VaultCycle(self).run(knowledge, enqueue)

    def _render_entity(
        self,
        entity: "Entity",
        inbound: dict[str, list[dict]],
        summary_by_ref: dict[tuple[str, str], tuple[str, str]],
    ) -> RenderedNote:
        from gideon.cognition.vault_reconcile import VaultProjection

        return VaultProjection(self, [], ([], {}, {})).entity(
            entity, inbound, summary_by_ref
        )

    def absorb_edits(self) -> dict:
        from gideon.cognition.vault_reconcile import VaultEdits

        return VaultEdits(self).absorb()

    def _apply_page_edit(self, relpath: str, fm: dict, body: str) -> tuple[bool, str]:
        from gideon.cognition.vault_reconcile import VaultEdits

        return VaultEdits(self).apply(fm, body)

    def _flag_conflict(self, page: Path, block: str, body: str, reason: str) -> None:
        from gideon.cognition.vault_reconcile import VaultEdits

        VaultEdits(self).flag(page, block, body, reason)

    def sweep_raw(self, *, knowledge: object = None, enqueue: object = None) -> dict:
        from gideon.cognition.vault_reconcile import VaultCapture

        return VaultCapture(self).sweep(knowledge, enqueue)

    def seed(self, seeds: dict[str, str]) -> dict:
        from gideon.cognition.vault_reconcile import VaultCapture

        return VaultCapture(self).seed(seeds)

    def lint_flags(self) -> list[tuple[str, str, str]]:
        from gideon.cognition.vault_reconcile import VaultInspection

        return VaultInspection(self).flags()


def starter_seeds(mode: str) -> dict[str, str]:
    """The starter pages a fresh vault gets: how to read it, and how ``raw/`` works.

    Hashed like every other page, so :meth:`MemoryVault.seed` can tell "still the
    shipped text" from "the user rewrote this" and only ever replaces the former.
    """
    two_way = mode == "two_way"
    readme = [
        "# Reading this vault",
        "",
        "Every page here is projected from Gideon's memory store.",
        "",
        "- `facts/` — one page per remembered fact, preference or note.",
        "- `episodic/` — conversation fragments. Evidence: read-only, always.",
        "- `entities/` — one page per person/project/tool memory knows about:",
        "  compiled truth on top, an append-only timeline below.",
        "- `tags/` — hub pages that make the graph view cluster.",
        "- `raw/` — drop a file here and the next sync files it under **Knowledge**,",
        "  never into memory.",
        "",
        "## Frontmatter",
        "",
        "`source_hash` is a hash of everything below the frontmatter fence. It is how",
        "the sync knows whether you edited a page. `sync_conflict` means the sync read",
        "your edit, could not apply it safely, and left your text untouched.",
        "",
        "## Editing",
        "",
        (
            "This vault is in **two_way** mode. Edit a `facts/` page between its H1 and"
            " the `gideon:generated` marker and the next sync writes your version"
            " into memory — your edit wins over the stored value. Everything below the"
            " marker is regenerated, so changes there are lost."
            if two_way
            else "This vault is in **mirror** mode: pages are regenerated from the store"
            " and your edits WILL be overwritten. Set `memory.vault_mode` to `two_way`"
            " in Settings → Memory to edit memory from here."
        ),
    ]
    return {
        "README.md": compose_page(
            [("type", "guide"), ("kind", "guide")], "\n".join(readme)
        ),
    }


def extract_edited_value(body: str, *, stop_at_headings: bool = True) -> str | None:
    lines = iter(body.replace("\r\n", "\n").split("\n"))
    for line in lines:
        if line.startswith("# "):
            break
    else:
        return None
    content = []
    for line in lines:
        if line.strip() == GENERATED_MARKER or (
            stop_at_headings and line.startswith("## ")
        ):
            break
        content.append(line)
    return "\n".join(content).strip()


def vault_mode_from_config() -> str:
    from gideon.core.config.loader import AppConfig

    try:
        configured = getattr(AppConfig.load().memory, "vault_mode", "off")
        mode = str(configured or "off")
    except Exception:
        logger.debug("vault: config unreadable", exc_info=True)
        return "off"
    return mode if mode in MEMORY_VAULT_MODES else "off"


def vault_path_from_config() -> Path:
    from gideon.core.config.loader import AppConfig, config_dir

    configured = getattr(AppConfig.load().memory, "vault_path", "") or "memory-vault"
    raw = configured.strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config_dir() / raw
    return path


def vault_dir_from_config() -> Path | None:
    return vault_path_from_config() if vault_mode_from_config() != "off" else None


def vault_for(service: "MemoryService") -> MemoryVault | None:
    selected = vault_mode_from_config()
    return (
        MemoryVault(service, vault_path_from_config(), mode=selected)
        if selected != "off"
        else None
    )


def mirror_after_consolidation(service: "MemoryService") -> None:
    try:
        selected = vault_for(service)
        if selected is None:
            return
        selected.sync()
    except Exception:
        logger.debug("memory vault: post-consolidation mirror failed", exc_info=True)
