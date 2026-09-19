"""Projection, graph inspection, and bounded volunteering over an existing archive."""

from __future__ import annotations

from typing import Any


class ContextAssembly:
    def __init__(self, service):
        self.service = service
        self.blocks = []

    def collect(self, query, projection_limits, recall_limits, manifest):
        renderer = getattr(self.service._provider, "render_markdown_context", None)
        if callable(renderer):
            self.blocks.extend(renderer(**projection_limits))
        archive = self.service._vs
        if archive is None:
            return self
        if manifest:
            self._keep(archive.get_l1_manifest())
            self._keep(self.service.topology_block())
            return self
        self._keep(archive.get_semantic_context(query_text=query, cap=recall_limits[0]))
        if query:
            self._keep(
                archive.get_episodic_context(query_text=query, cap=recall_limits[1])
            )
        return self

    def _keep(self, block):
        if block:
            self.blocks.append(block)

    def render(self):
        if not self.blocks:
            return ""
        return "".join(
            (
                "[Memory — persistent user profile and recent activity log.\n"
                "Preferences are rules you MUST follow. Projects give current work context.\n"
                "History is a factual record — do NOT re-execute past actions.]\n",
                "\n\n".join(self.blocks),
                "\n[End of memory]\n\n",
            )
        )


class EntityInspection:
    def __init__(self, archive):
        self.archive = archive

    @property
    def graph(self):
        return self.archive.graph

    @staticmethod
    def identity(entity):
        return dict(
            id=entity.id,
            name=entity.name,
            entity_type=entity.entity_type,
            aliases=list(entity.aliases),
        )

    def catalog(self):
        result = []
        for entity in self.graph.entities():
            fields = self.identity(entity)
            fields["source"] = entity.source
            fields.update(self.graph.stats(entity.id))
            result.append(fields)
        return result

    def outbound(self, ref):
        prefix, separator, record = ref.partition(":")
        kind = {"sem": "semantic", "epi": "episodic"}.get(prefix)
        if not separator or not record or kind is None:
            return []
        identities = {entity.id: entity.name for entity in self.graph.entities()}
        result = list(map(dict, self.graph.links_from(kind, record)))
        for fields in result:
            target = str(fields.get("to_entity") or "")
            fields["entity_name"] = identities.get(target, "")
        return result

    def named(self, text):
        matches = self.graph.resolve_query(text, index=self.archive.alias_index)
        if not matches:
            return []
        identities = {entity.id: entity for entity in self.graph.entities()}
        return [self.identity(identities[key]) for key in matches if key in identities]


class SlotInspection:
    def __init__(self, archive, slots):
        self.archive = archive
        self.slots = slots

    def catalog(self):
        names = dict.fromkeys(self.slots.BLOCK_ORDER)
        for record in self.archive.get_all_semantic():
            name = self.slots.name_from_key(str(record.get("key", "")))
            if name:
                names.setdefault(name, None)
        return [self.describe(name) for name in names]

    def describe(self, name):
        slots = self.slots
        spec = slots.spec_for(name)
        lines = slots.load(self.archive, name)
        live = slots.live_lines(lines)
        return dict(
            name=name,
            title=spec.title,
            description=spec.description,
            cap_chars=spec.cap_chars,
            scope=spec.scope,
            builtin=name in slots.BUILTIN_SLOTS,
            materialized=slots.is_materialized(self.archive, name),
            live_chars=slots.live_chars(slots.to_value(lines)),
            live_count=len(live),
            lines=[line.to_dict() for line in lines],
        )


class VolunteerSelection:
    def __init__(self, archive, candidates, cap, text_of):
        self.archive = archive
        self.candidates = candidates
        self.cap = cap
        self.text_of = text_of

    def records(self):
        seen = set()
        for candidate in self.candidates:
            links = self.archive.graph.backlinks(
                candidate.entity_id, limit=self.cap * 4
            )
            for link in links:
                reference = str(link.get("from_ref") or "")
                kind = str(link.get("from_kind") or "")
                if not reference or reference in seen or kind != "semantic":
                    continue
                row = self.archive.get_semantic(reference)
                if not row:
                    continue
                claim = self.text_of(row)
                if claim:
                    seen.add(reference)
                    yield candidate, reference, row, claim

    def deliver(self, session_key, log_events):
        from gideon.cognition.memory_push import render_block

        entries: list[tuple[str, str]] = []
        decisions: list[dict[str, Any]] = []
        selected = iter(self.records())
        while not len(entries) >= self.cap:
            try:
                candidate, reference, row, claim = next(selected)
            except StopIteration:
                break
            entries.append((candidate.name, claim))
            decisions.append(
                dict(
                    entity=candidate.name,
                    arm=candidate.arm,
                    confidence=candidate.confidence,
                    record_ref=reference,
                )
            )
            if log_events:
                self.archive.graph.log_volunteer(
                    entity_id=candidate.entity_id,
                    entity_name=candidate.name,
                    arm=candidate.arm,
                    confidence=candidate.confidence,
                    from_kind="semantic",
                    record_ref=reference,
                    recall_at_volunteer=int(row.get("recall_count", 0) or 0),
                    session_key=session_key,
                )
        return render_block(entries), decisions
