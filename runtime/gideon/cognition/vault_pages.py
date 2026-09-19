"""Pure page projections sharing a single note assembly model."""

import json


def contract():
    from gideon.cognition import memory_vault

    return memory_vault


class NoteDraft:
    def __init__(self, path, title, fields, tags=()):
        self.path, self.title, self.fields = path, title, fields
        self.tags, self.links, self.lines = list(tags), set(), []
        self.api = contract()

    def referenced(self, target, summary=None):
        self.links.add(target)
        if summary is None:
            return f"- [[{target}]]"
        safe = summary.replace("]", " ").replace("[", " ").strip() or target
        return f"- [[{target}]] — {safe}"

    def finish(self):
        return self.api.RenderedNote(
            self.path,
            self.api.compose_page(self.fields, "\n".join(self.lines)),
            self.links,
            self.tags,
            self.title,
        )


class RecordPage:
    def __init__(self, record, entities):
        self.record, self.entities, self.api = record, entities, contract()

    def metadata(self):
        rec, api = self.record, self.api
        fields = [("type", api._PAGE_TYPE.get(rec.kind.value, "concept"))]
        for name in api._FM_ORDER:
            if name in ("kind", "scope"):
                fields.append((name, getattr(rec, name).value))
            elif name == "tier":
                if rec.tier is not None:
                    fields.append((name, rec.tier.value))
            elif name == "tags":
                if rec.tags:
                    fields.append((name, list(rec.tags)))
            else:
                value = getattr(rec, name, None)
                if value not in (None, "", 0, 0.0):
                    fields.append((name, value))
        return fields

    def text(self):
        rec = self.record
        content = rec.text or ""
        if not content and rec.value is not None:
            content = (
                json.dumps(rec.value, indent=2, ensure_ascii=False)
                if isinstance(rec.value, (dict, list))
                else str(rec.value)
            )
        return content[: self.api._MAX_BODY]

    def render(self):
        from gideon.cognition.memory_record import MemoryKind

        rec, api = self.record, self.api
        draft = NoteDraft(
            f"{api._KIND_DIR.get(rec.kind.value, 'other')}/{api._record_basename(rec)}.md",
            api._record_title(rec),
            self.metadata(),
            rec.tags,
        )
        body = self.text()
        draft.lines = [f"# {draft.title}", ""]
        if body.strip():
            draft.lines.extend((body.rstrip(), ""))
        relations = []
        if rec.superseded_by:
            relations.append(("Superseded by", [api.slug(rec.superseded_by)]))
        if rec.kind == MemoryKind.EPISODIC and rec.conversation_id:
            relations.append(("Session", [api.slug(f"session-{rec.conversation_id}")]))
        entities = [
            api._entity_basename(entity)
            for entity in sorted(self.entities or [], key=lambda item: item.name)
        ]
        tags = [api.slug(f"tag-{tag}") for tag in rec.tags]
        if entities:
            relations.append(("Entities", entities))
        if tags:
            relations.append(("Tags", tags))
        if relations:
            draft.lines.extend((api.GENERATED_MARKER, ""))
            for label, targets in relations:
                draft.links.update(targets)
                draft.lines.extend(
                    (
                        f"**{label}:** "
                        + " ".join(f"[[{target}]]" for target in targets),
                        "",
                    )
                )
        return draft.finish()


def membership_page(kind, identity, members):
    api = contract()
    count = len(members)
    if kind == "tag":
        draft = NoteDraft(
            f"{api._TAGS_DIR}/{api.slug(f'tag-{identity}')}.md",
            f"#{identity}",
            [("type", "tag"), ("kind", "tag"), ("tag", identity), ("count", count)],
            [identity],
        )
        description = (
            f"{count} memor" + ("y" if count == 1 else "ies") + " with this tag:"
        )
    else:
        draft = NoteDraft(
            f"sessions/{api.slug(f'session-{identity}')}.md",
            f"Session {identity}",
            [
                ("type", "session"),
                ("kind", "session"),
                ("conversation_id", identity),
                ("count", count),
            ],
        )
        description = f"{count} episodic fragment" + ("" if count == 1 else "s") + ":"
    draft.lines = [f"# {draft.title}", "", description, "", api.GENERATED_MARKER, ""]
    draft.lines.extend(draft.referenced(base, title) for base, title in sorted(members))
    return draft.finish()


class VaultIndexPage:
    descriptions = {
        True: "A **two-way** projection of Gideon's memory. Open this folder in "
        "Obsidian for the graph view. Edit a fact page above its "
        "`gideon:generated` marker and the next sync reads your change back "
        "into memory — your edit wins. Anything the sync cannot read confidently is "
        "left exactly as you wrote it and reported in Settings → Memory → Health.",
        False: "A read-only mirror of Gideon's memory. Open this folder in "
        "Obsidian for the graph view. Do not edit — files are regenerated from the "
        "memory store. Switch `memory.vault_mode` to `two_way` to edit them back.",
    }

    def __init__(self, records, entities, mode):
        self.records, self.entities, self.mode = records, entities, mode
        self.api = contract()

    def render(self):
        from gideon.cognition.memory_record import MemoryKind, MemoryScope

        counts: dict = {}
        for record in self.records:
            counts[record.kind.value] = counts.get(record.kind.value, 0) + 1
        draft = NoteDraft(
            self.api._INDEX_NAME,
            "Memory Vault",
            [
                ("type", "index"),
                ("kind", "index"),
                ("total", len(self.records)),
                ("vault_mode", self.mode),
            ],
        )
        draft.lines = [
            "# Memory Vault",
            "",
            self.descriptions[self.mode == "two_way"],
            "",
            "## Counts",
            "",
        ]
        draft.lines.extend(f"- **{kind}**: {counts[kind]}" for kind in sorted(counts))
        draft.lines.extend(("", self.api.GENERATED_MARKER, ""))
        roster = sorted(self.entities or [], key=lambda item: item.name)
        if roster:
            draft.lines.extend(("## Entities", ""))
            for entity in roster:
                base = self.api._entity_basename(entity)
                draft.links.add(base)
                draft.lines.append(f"- [[{base}]] — {entity.entity_type}")
            draft.lines.append("")
        kinds = (
            MemoryKind.SEMANTIC,
            MemoryKind.PREFERENCE,
            MemoryKind.LESSON,
            MemoryKind.PROCEDURAL,
        )
        facts = [
            record
            for record in self.records
            if record.scope == MemoryScope.GLOBAL and record.kind in kinds
        ]
        facts.sort(key=lambda record: record.heat(), reverse=True)
        if facts:
            draft.lines.extend(("## Most-recalled facts", ""))
            for record in facts[:25]:
                target = self.api._record_basename(record)
                draft.links.add(target)
                summary = " ".join((record.text or str(record.value or "")).split())[
                    :100
                ]
                draft.lines.append(f"- [[{target}]] — {summary}")
            draft.lines.append("")
        return draft.finish()


def entity_page(entity, compiled, backlinks, evidence, existing):
    api = contract()
    metadata = [
        ("type", "entity"),
        ("kind", "entity"),
        ("id", entity.id),
        ("title", entity.name),
        ("entity_type", entity.entity_type),
    ]
    if entity.aliases:
        metadata.append(("aliases", list(entity.aliases)))
    metadata.extend(
        (
            ("source", entity.source),
            ("sources", ["memory.db:mem_entities", "memory.db:mem_links"]),
        )
    )
    draft = NoteDraft(
        f"{api._ENTITIES_DIR}/{api._entity_basename(entity)}.md", entity.name, metadata
    )
    draft.lines = [
        f"# {entity.name}",
        "",
        api.GENERATED_MARKER,
        "",
        api._COMPILED_HEADING,
        "",
    ]
    draft.lines.extend(
        draft.referenced(target, summary) for target, summary in compiled
    )
    if not compiled:
        draft.lines.append("_Nothing linked to this entity yet._")
    draft.lines.extend(("", api._BACKLINKS_HEADING, ""))
    draft.lines.extend(draft.referenced(target) for target in backlinks)
    if not backlinks:
        draft.lines.append("_No records link here._")
    timeline = api.timeline_lines(existing)
    seen = set(timeline)
    for item in evidence:
        if item in seen:
            continue
        timeline.append(item)
        seen.add(item)
    draft.lines.extend(("", api._TIMELINE_HEADING, "", *timeline))
    return draft.finish()
