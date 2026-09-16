"""Preview-bound structural edits, provenance transfer and reversible journal receipts."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
VERBS: tuple[str, ...] = ("split", "extract", "merge", "retitle", "move", "change_kind")
LINEAGE_RELATION = "part_of"


class RestructureError(Exception):
    def __init__(self, code: str, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


class PreviewStale(RestructureError):
    def __init__(self, message: str, *, plan: "Plan") -> None:
        self.plan = plan
        super().__init__("preview_stale", message, detail={"plan": plan.to_dict()})


@dataclass(frozen=True)
class Break:
    kind: str
    message: str
    relinkable: bool
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return dict(
            kind=self.kind,
            message=self.message,
            relinkable=self.relinkable,
            refs=list(self.refs),
        )


@dataclass(frozen=True)
class Plan:
    verb: str
    item_id: str
    summary: str
    token: str
    affected: tuple[str, ...]
    breaks: tuple[Break, ...]
    detail: dict

    @property
    def relinkable(self) -> bool:
        return any(entry.relinkable for entry in self.breaks)

    def to_dict(self) -> dict:
        fields = {
            key: getattr(self, key)
            for key in ("verb", "item_id", "summary", "token", "detail")
        }
        fields.update(
            affected=list(self.affected),
            breaks=[entry.to_dict() for entry in self.breaks],
            relink_offered=self.relinkable,
        )
        return fields


def sections(content: str) -> list[dict]:
    from gideon.cognition.knowledge import chunking

    boundaries = chunking.section_boundaries(content or "")
    ends = [entry.offset for entry in boundaries[1:]] + [len(content or "")]
    return [
        dict(
            offset=entry.offset,
            line=entry.line,
            title=entry.title,
            level=entry.level,
            chars=end - entry.offset,
        )
        for entry, end in zip(boundaries, ends)
    ]


def _row(store: Any, item_id: str) -> dict:
    found = store.get_item(item_id)
    if found:
        return dict(found)
    raise RestructureError("item_not_found", f"no knowledge item {item_id!r}")


def _digest(
    verb: str, item_id: str, params: dict, states: list[dict], breaks: list[Break]
) -> str:
    payload = dict(
        verb=verb,
        item_id=item_id,
        params=params,
        states=states,
        breaks=[entry.to_dict() for entry in breaks],
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(serialized).hexdigest()[:32]


def _states(store: Any, item_ids: list[str]) -> list[dict]:
    fingerprints = []
    for identifier in sorted(set(item_ids)):
        row = store.db.execute(
            "SELECT id, title, updated_at, content, kind FROM items WHERE id = ?",
            (identifier,),
        ).fetchone()
        if row is not None:
            state = {
                key: str(row[key] or "") for key in ("updated_at", "title", "kind")
            }
            state.update(
                id=row["id"],
                content_sha=hashlib.sha256(
                    str(row["content"] or "").encode("utf-8")
                ).hexdigest()[:16],
            )
            fingerprints.append(state)
    return fingerprints


@dataclass(frozen=True)
class _References:
    store: Any

    def wikilinks(self, title: str, destination: str) -> Break | None:
        rows = self.store.items_linking_to_title(title)
        if not rows:
            return None
        count = sum(int(row.get("links") or 0) for row in rows)
        suffix = "" if count == 1 else "s"
        location = "item" if len(rows) == 1 else "items"
        message = (
            f"{count} [[{title}]] link{suffix} in {len(rows)} other {location} would stop "
            f"resolving; relinking repoints them at “{destination}”"
        )
        return Break("wikilink", message, True, tuple(str(row["id"]) for row in rows))

    def chunk_citations(self, identifier: str) -> Break | None:
        rows = self.store.db.execute(
            "SELECT item_id, marker FROM item_citations "
            "WHERE source_item_id = ? AND chunk_index >= 0 ORDER BY item_id, marker",
            (identifier,),
        ).fetchall()
        if not rows:
            return None
        suffix = "" if len(rows) == 1 else "s"
        message = (
            f"{len(rows)} citation{suffix} name a specific passage of this item by chunk "
            "number; rewriting its body renumbers the chunks, so relinking widens them to "
            "cite the item as a whole rather than the wrong passage"
        )
        return Break(
            "citation_chunk", message, True, tuple(str(row["item_id"]) for row in rows)
        )

    def annotations(self, identifier: str, pieces: list[str]) -> tuple[dict, list[str]]:
        placements, stranded = {}, []
        for row in self.store.list_annotations(identifier):
            quote = str(row.get("quote") or "")
            if not quote:
                continue
            matches = [
                position for position, piece in enumerate(pieces) if quote in piece
            ]
            annotation = str(row["id"])
            if matches:
                placements[annotation] = matches[0] if len(matches) == 1 else 0
            else:
                stranded.append(annotation)
        return placements, stranded


def _wikilink_break(store: Any, title: str, *, becoming: str) -> Break | None:
    return _References(store).wikilinks(title, becoming)


def _chunk_citation_break(store: Any, item_id: str) -> Break | None:
    return _References(store).chunk_citations(item_id)


def _annotation_spans(
    store: Any, item_id: str, parts: list[str]
) -> tuple[dict, list[str]]:
    return _References(store).annotations(item_id, parts)


def _stranded_break(stranded: list[str]) -> Break | None:
    if stranded:
        plural = "" if len(stranded) == 1 else "s"
        return Break(
            "annotation",
            f"{len(stranded)} highlight{plural} sit across the cut, so no single piece "
            "contains the whole passage; they stay listed on the original but stop marking it",
            False,
            tuple(stranded),
        )
    return None


def _plan_pieces(content: str, offsets: list[int]) -> list[str]:
    boundaries = (0, *offsets, len(content))
    return [content[left:right] for left, right in zip(boundaries, boundaries[1:])]


@dataclass(frozen=True)
class _EditAnalysis:
    store: Any
    row: dict
    parameters: dict

    @property
    def identifier(self) -> str:
        return str(self.row["id"])

    @property
    def content(self) -> str:
        return str(self.row.get("content") or "")

    def rewrite_breaks(self, pieces: list[str]) -> tuple[dict, list[Break]]:
        placements, stranded = _annotation_spans(self.store, self.identifier, pieces)
        possible = (
            _stranded_break(stranded),
            _chunk_citation_break(self.store, self.row["id"]),
        )
        return placements, [entry for entry in possible if entry]

    def split(self):
        available = {int(entry["offset"]): entry for entry in sections(self.content)}
        raw = self.parameters.get("offsets")
        if not isinstance(raw, list) or not raw:
            raise RestructureError(
                "split_needs_boundary",
                "supply offsets: a list of section-boundary offsets",
            )
        try:
            offsets = sorted(set(map(int, raw)))
        except (TypeError, ValueError) as exc:
            raise RestructureError(
                "split_needs_boundary", "offsets must be integers"
            ) from exc
        for offset in offsets:
            if offset not in available:
                raise RestructureError(
                    "not_a_section_boundary",
                    f"offset {offset} is not a section boundary of this item",
                    detail={"sections": list(available.values())},
                )
            if offset <= 0:
                raise RestructureError(
                    "boundary_at_start",
                    "that boundary is the start of the document, so splitting there would leave "
                    "the original empty — pick a later section",
                )
        pieces = _plan_pieces(self.content, offsets)
        placements, breaks = self.rewrite_breaks(pieces)
        titles = [
            str(available[offset]["title"]) or "Untitled section" for offset in offsets
        ]
        moving = sum(len(piece.split()) for piece in pieces[1:])
        summary = (
            f"Split into {len(pieces)} items: “{self.row.get('title') or 'Untitled'}” keeps "
            f"{len(pieces[0].split())} words, and {moving} words move to "
            + ", ".join(f"“{title}”" for title in titles)
        )
        detail = dict(
            pieces=[
                dict(
                    title=self.row.get("title") if index == 0 else titles[index - 1],
                    words=len(piece.split()),
                )
                for index, piece in enumerate(pieces)
            ],
            annotations_following=sum(part > 0 for part in placements.values()),
            sections=list(available.values()),
            offsets=offsets,
        )
        return summary, [self.identifier], breaks, detail

    def extract(self):
        start, end = self.parameters.get("start"), self.parameters.get("end")
        if start is None or end is None:
            raise RestructureError("extract_needs_span", "supply integer start and end")
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError) as exc:
            raise RestructureError(
                "extract_needs_span", "supply integer start and end"
            ) from exc
        if not 0 <= start < end <= len(self.content):
            raise RestructureError(
                "extract_needs_span",
                f"start/end must satisfy 0 <= start < end <= {len(self.content)} (the body length)",
            )
        passage = self.content[start:end]
        if not passage.strip():
            raise RestructureError("extract_needs_span", "that span is only whitespace")
        title = " ".join(str(self.parameters.get("title") or "").split())
        if not title:
            raise RestructureError("title_required", "the extracted item needs a title")
        keep = bool(self.parameters.get("keep_in_source"))
        remainder = self.content if keep else self.content[:start] + self.content[end:]
        if not keep and not remainder.strip():
            raise RestructureError(
                "extract_empties_source",
                "extracting that span would leave the original empty — split the item instead, "
                "or extract a copy with keep_in_source",
            )
        breaks = [] if keep else self.rewrite_breaks([remainder, passage])[1]
        summary = (
            f"{'Copy' if keep else 'Move'} {len(passage.split())} words into a new item “{title}”, linked to "
            f"“{self.row.get('title') or 'Untitled'}”"
            + ("" if keep else f", leaving {len(remainder.split())} words behind")
        )
        return (
            summary,
            [self.identifier],
            breaks,
            dict(
                title=title,
                words=len(passage.split()),
                keep_in_source=keep,
                excerpt=passage[:280],
                start=start,
                end=end,
            ),
        )

    def merge(self):
        identifier = str(self.parameters.get("merge_id") or "").strip()
        if not identifier:
            raise RestructureError(
                "merge_id_required", "supply merge_id: the item to fold in"
            )
        if identifier == self.identifier:
            raise RestructureError(
                "merge_into_self",
                "an item cannot be merged into itself — that would delete the survivor",
            )
        losing = _row(self.store, identifier)
        inbound = self.store.inbound_references(identifier)
        breaks = []
        citations = list(inbound["citations"])
        if citations:
            suffix = "" if len(citations) == 1 else "s"
            breaks.append(
                Break(
                    "citation",
                    f"{len(citations)} citation{suffix} name the folded-in copy as their source; "
                    "merging deletes it, so relinking repoints them at the survivor whose body now holds that text",
                    True,
                    tuple(str(row["item_id"]) for row in citations),
                )
            )
        link = _wikilink_break(
            self.store, str(losing.get("title") or ""), becoming=str(self.row["title"])
        )
        if link is not None:
            breaks.append(link)
        summary = (
            f"Fold “{losing.get('title') or 'Untitled'}” into “{self.row.get('title') or 'Untitled'}”. "
            f"The survivor inherits its tags, shelves, entity mentions, {inbound['annotations']} highlight(s) "
            f"and {len(inbound['relations'])} relation(s); the folded-in copy is deleted"
        )
        detail = dict(
            merge_id=identifier,
            merge_title=losing.get("title") or "",
            inherits=dict(
                annotations=inbound["annotations"],
                relations=len(inbound["relations"]),
                collections=len(inbound["collections"]),
            ),
        )
        return summary, [self.identifier, identifier], breaks, detail

    def retitle(self):
        title = " ".join(str(self.parameters.get("title") or "").split())
        if not title:
            raise RestructureError("title_required", "supply a non-empty title")
        previous = str(self.row.get("title") or "")
        if title == previous:
            raise RestructureError("title_unchanged", "that is already the title")
        link = _wikilink_break(self.store, previous, becoming=title)
        affected = [self.identifier, *(link.refs if link else ())]
        return (
            f"Rename “{previous}” to “{title}”",
            affected,
            [link] if link else [],
            dict(title=title, previous_title=previous),
        )

    def move(self):
        collections, tags = self.parameters.get("collections"), self.parameters.get(
            "tags"
        )
        if collections is None and tags is None:
            raise RestructureError("nothing_to_move", "supply collections and/or tags")
        if collections is not None:
            if not isinstance(collections, list):
                raise RestructureError(
                    "bad_collections", "collections must be a list of ids"
                )
            collections = [
                str(value) for value in collections if str(value or "").strip()
            ]
            for identifier in collections:
                row = self.store.db.execute(
                    "SELECT id, name, kind FROM collections WHERE id = ?", (identifier,)
                ).fetchone()
                if row is None:
                    raise RestructureError(
                        "unknown_collection", f"no collection {identifier!r}"
                    )
                if str(row["kind"]) != "manual":
                    raise RestructureError(
                        "smart_collection",
                        f"“{row['name']}” is a smart shelf — its contents come from its query, "
                        "so an item cannot be moved into it",
                    )
        if tags is not None:
            if not isinstance(tags, list):
                raise RestructureError("bad_tags", "tags must be a list of names")
            tags = [
                " ".join(str(value).split())
                for value in tags
                if str(value or "").strip()
            ]
        current = [
            str(row["id"]) for row in self.store.collections_for_item(self.identifier)
        ]
        phrases = []
        if collections is not None:
            phrases.append(
                f"{len(set(collections) - set(current))} shelf addition(s) and {len(set(current) - set(collections))} removal(s)"
            )
        if tags is not None:
            phrases.append(f"tags become {', '.join(tags) if tags else 'none'}")
        summary = f"Reshelve “{self.row.get('title') or 'Untitled'}”: " + "; ".join(
            phrases
        )
        return summary, [self.identifier], [], dict(collections=collections, tags=tags)

    def change_kind(self):
        from gideon.cognition.knowledge import semantics

        kind = str(self.parameters.get("kind") or "").strip().lower()
        if not kind:
            raise RestructureError("kind_required", "supply kind")
        if kind not in semantics.KINDS:
            raise RestructureError(
                "unknown_kind",
                f"unknown kind {kind!r} — one of: {', '.join(semantics.KINDS)}",
            )
        old = str(self.row.get("kind") or "")
        if kind == old:
            raise RestructureError("kind_unchanged", "that is already the item's kind")
        breaks = []
        if kind in semantics.SYNTHESIZED_KINDS and not self.store.item_citations(
            self.identifier
        ):
            breaks.append(
                Break(
                    "kind_contract",
                    f"“{kind}” is a synthesized kind, which is expected to carry citations, "
                    "and this item has none — it will read as unsourced",
                    False,
                )
            )
        identity = semantics.logical_key(kind, str(self.row.get("title") or ""))
        summary = (
            f"Change kind from “{old or 'unset'}” to “{kind}”, which re-derives the item's "
            f"logical identity as {identity or 'nothing'}"
        )
        return summary, [self.identifier], breaks, dict(kind=kind, previous_kind=old)


def _plan_split(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).split()


def _plan_extract(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).extract()


def _plan_merge(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).merge()


def _plan_retitle(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).retitle()


def _plan_move(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).move()


def _plan_change_kind(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    return _EditAnalysis(store, row, params).change_kind()


_PLANNERS = dict(
    split=_plan_split,
    extract=_plan_extract,
    merge=_plan_merge,
    retitle=_plan_retitle,
    move=_plan_move,
    change_kind=_plan_change_kind,
)


def plan(store: Any, verb: str, item_id: str, params: dict | None = None) -> Plan:
    if verb not in _PLANNERS:
        raise RestructureError(
            "unknown_verb", f"verb must be one of: {', '.join(VERBS)}"
        )
    row = _row(store, item_id)
    summary, affected, breaks, detail = _PLANNERS[verb](store, row, dict(params or {}))
    fingerprint = _digest(verb, item_id, detail, _states(store, affected), breaks)
    return Plan(
        verb, item_id, summary, fingerprint, tuple(affected), tuple(breaks), detail
    )


def refresh_derived(store: Any, item_ids: list[str], *, reason: str) -> list[str]:
    from gideon.cognition.knowledge import maintenance

    touched = []
    for identifier in item_ids:
        exists = store.db.execute(
            "SELECT 1 FROM items WHERE id = ?", (identifier,)
        ).fetchone()
        if exists is not None:
            store.clear_chunks(identifier)
            store.db.execute(
                "UPDATE items SET embedding = NULL WHERE id = ?", (identifier,)
            )
            store.db.execute(
                "DELETE FROM mention_sweeps WHERE item_id = ?", (identifier,)
            )
            store.release_similarity_claims(identifier, set())
            touched.append(identifier)
    store.db.commit()
    maintenance.mark_dirty(reason=f"restructure {reason}")
    return touched


def _inherit(store: Any, parent: dict, *, title: str, content: str) -> str:
    fields = dict(
        item_type=str(parent.get("item_type") or parent.get("type") or "note"),
        title=title,
        content=content,
        tags=list(parent.get("tags") or []),
        url=str(parent.get("url") or ""),
        provider=str(parent.get("provider") or "native"),
    )
    identifier = store.create_typed_item(**fields)
    if not identifier:
        raise RestructureError("create_failed", "the new item could not be created")
    shelves = store.collections_for_item(str(parent["id"]))
    for shelf in shelves:
        if str(shelf.get("kind")) == "manual":
            store.add_to_collection(str(shelf["id"]), identifier)
    if parent.get("kind"):
        store.set_item_identity(identifier, kind=str(parent["kind"]))
    store.add_item_relation(
        identifier, str(parent["id"]), LINEAGE_RELATION, provenance="extracted"
    )
    return identifier


def _widen_chunk_citations(store: Any, item_id: str) -> int:
    query = "UPDATE item_citations SET chunk_index = -1 WHERE source_item_id = ? AND chunk_index >= 0"
    result = store.db.execute(query, (item_id,))
    store.db.commit()
    return result.rowcount or 0


@dataclass(frozen=True)
class _EditMutation:
    store: Any
    current: Plan
    relink: bool

    def move_annotations(self, placements: dict, destinations: dict[int, str]) -> int:
        count = 0
        for annotation, position in placements.items():
            if position in destinations:
                self.store.db.execute(
                    "UPDATE annotations SET item_id = ? WHERE id = ?",
                    (destinations[position], annotation),
                )
                count += 1
        self.store.db.commit()
        return count

    def split(self) -> dict:
        parent = _row(self.store, self.current.item_id)
        pieces = _plan_pieces(
            str(parent.get("content") or ""), list(self.current.detail["offsets"])
        )
        placements, _ = _annotation_spans(self.store, self.current.item_id, pieces)
        titles = [
            str(entry["title"] or "Untitled section")
            for entry in self.current.detail["pieces"][1:]
        ]
        children = [
            _inherit(self.store, parent, title=title, content=piece)
            for title, piece in zip(titles, pieces[1:])
        ]
        self.store.update_item(self.current.item_id, content=pieces[0])
        moved = self.move_annotations(placements, dict(enumerate(children, start=1)))
        widened = (
            _widen_chunk_citations(self.store, self.current.item_id)
            if self.relink
            else 0
        )
        return dict(
            created=children,
            kept=self.current.item_id,
            annotations_moved=moved,
            citations_widened=widened,
        )

    def extract(self) -> dict:
        parent = _row(self.store, self.current.item_id)
        body = str(parent.get("content") or "")
        start, end = int(self.current.detail["start"]), int(self.current.detail["end"])
        keep = bool(self.current.detail["keep_in_source"])
        passage = body[start:end]
        child = _inherit(
            self.store, parent, title=str(self.current.detail["title"]), content=passage
        )
        moved = 0
        if not keep:
            remainder = body[:start] + body[end:]
            placements, _ = _annotation_spans(
                self.store, self.current.item_id, [remainder, passage]
            )
            self.store.update_item(self.current.item_id, content=remainder)
            moved = self.move_annotations(placements, {1: child})
        widened = (
            _widen_chunk_citations(self.store, self.current.item_id)
            if self.relink and not keep
            else 0
        )
        return dict(
            created=[child],
            kept=self.current.item_id,
            annotations_moved=moved,
            citations_widened=widened,
        )

    def merge(self) -> dict:
        loser = str(self.current.detail["merge_id"])
        old = str(self.current.detail.get("merge_title") or "")
        new = str(_row(self.store, self.current.item_id).get("title") or "")
        links = dict(items=0, links=0, item_ids=[])
        if self.relink and old and old != new:
            links = self.store.rewrite_wikilinks(old, new)
        moved = self.store.merge_items(
            self.current.item_id, loser, relink_citations=self.relink
        )
        return dict(
            created=[],
            kept=self.current.item_id,
            merged=loser,
            moved=moved,
            wikilinks_relinked=links,
        )

    def retitle(self) -> dict:
        old, new = str(self.current.detail["previous_title"]), str(
            self.current.detail["title"]
        )
        self.store.update_item(self.current.item_id, title=new)
        identity = self.store.set_item_identity(self.current.item_id)
        links = (
            self.store.rewrite_wikilinks(old, new)
            if self.relink
            else dict(items=0, links=0)
        )
        return dict(
            created=[],
            kept=self.current.item_id,
            title=new,
            logical_key=identity["logical_key"],
            wikilinks_relinked=links,
        )

    def move(self) -> dict:
        requested, tags = self.current.detail.get(
            "collections"
        ), self.current.detail.get("tags")
        added = removed = 0
        if requested is not None:
            have = {
                str(row["id"])
                for row in self.store.collections_for_item(self.current.item_id)
            }
            want = set(requested)
            added = sum(
                bool(self.store.add_to_collection(identifier, self.current.item_id))
                for identifier in sorted(want - have)
            )
            removed = sum(
                bool(
                    self.store.remove_from_collection(identifier, self.current.item_id)
                )
                for identifier in sorted(have - want)
            )
        if tags is not None:
            self.store.update_item(self.current.item_id, tags=tags, tag_source="user")
        return dict(
            created=[],
            kept=self.current.item_id,
            collections_added=added,
            collections_removed=removed,
            tags=tags,
        )

    def change_kind(self) -> dict:
        identity = self.store.set_item_identity(
            self.current.item_id, kind=str(self.current.detail["kind"])
        )
        return {"created": [], "kept": self.current.item_id, **identity}


def _apply_split(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).split()


def _apply_extract(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).extract()


def _apply_merge(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).merge()


def _apply_retitle(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).retitle()


def _apply_move(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).move()


def _apply_change_kind(store: Any, current: Plan, *, relink: bool) -> dict:
    return _EditMutation(store, current, relink).change_kind()


_APPLIERS = dict(
    split=_apply_split,
    extract=_apply_extract,
    merge=_apply_merge,
    retitle=_apply_retitle,
    move=_apply_move,
    change_kind=_apply_change_kind,
)


@dataclass(frozen=True)
class _EditJournal:
    store: Any
    token: str

    def apply(self, verb: str, item_id: str, params: dict | None, relink: bool) -> dict:
        prior = self.store.load_undo(self.token)
        if prior is not None and str(prior.get("verb")) == verb:
            receipt = dict(prior.get("snapshot") or {}).get("result") or {}
            return {**receipt, "undo_token": self.token, "idempotent": True}
        current = plan(self.store, verb, item_id, params)
        if current.token != self.token:
            raise PreviewStale(
                "this item changed since that preview was taken; review the new preview before confirming",
                plan=current,
            )
        snapshot = self.store.snapshot_items(list(current.affected))
        receipt = _APPLIERS[verb](self.store, current, relink=relink)
        created = [str(identifier) for identifier in receipt.get("created") or []]
        snapshot["ids"] = [*snapshot["ids"], *created]
        self.store.save_undo(
            self.token,
            verb=verb,
            item_id=item_id,
            summary=current.summary,
            snapshot={"state": snapshot, "result": receipt},
        )
        refresh_derived(self.store, [*current.affected, *created], reason=verb)
        logger.info("knowledge restructure %s on %s: %s", verb, item_id, receipt)
        return {
            **receipt,
            "undo_token": self.token,
            "summary": current.summary,
            "idempotent": False,
        }

    def undo(self) -> dict:
        record = self.store.load_undo(self.token)
        if record is None:
            raise RestructureError(
                "unknown_undo_token",
                "that restructure is no longer undoable — it was already undone, or the journal "
                "has since rolled past it",
            )
        snapshot = dict(record.get("snapshot") or {}).get("state") or {}
        restored = self.store.restore_items_snapshot(snapshot)
        self.store.delete_undo(self.token)
        refresh_derived(
            self.store,
            list(snapshot.get("existing") or []),
            reason=f"undo {record['verb']}",
        )
        logger.info("undid knowledge restructure %s (%s)", record["verb"], self.token)
        return dict(
            ok=True,
            verb=str(record["verb"]),
            item_id=str(record["item_id"]),
            summary=str(record.get("summary") or ""),
            restored=restored,
        )


def apply(
    store: Any,
    verb: str,
    item_id: str,
    params: dict | None = None,
    *,
    token: str,
    relink: bool = True,
) -> dict:
    if verb not in _APPLIERS:
        raise RestructureError(
            "unknown_verb", f"verb must be one of: {', '.join(VERBS)}"
        )
    if not token:
        raise RestructureError(
            "token_required",
            "confirming a restructure requires the token from its preview",
        )
    return _EditJournal(store, token).apply(verb, item_id, params, relink)


def undo(store: Any, token: str) -> dict:
    return _EditJournal(store, token).undo()
