"""Vault reconciliation phases with store writes and file ownership kept explicit."""

import hashlib
import json
from pathlib import Path
from typing import Any


def contract():
    from gideon.cognition import memory_vault

    return memory_vault


class GraphProjection:
    def __init__(self, service):
        self.service, self.api = service, contract()

    def read(self):
        if not getattr(self.service, "has_graph", False):
            return [], {}, {}
        try:
            raw = self.service.graph_entities()
        except Exception:
            self.api.logger.debug("vault: entity roster unavailable", exc_info=True)
            return [], {}, {}
        from gideon.cognition.memory_graph import Entity

        entities = []
        for item in raw:
            if item.get("id") and item.get("name"):
                entities.append(
                    Entity(
                        id=str(item.get("id") or ""),
                        name=str(item.get("name") or ""),
                        entity_type=str(item.get("entity_type") or "topic"),
                        aliases=tuple(item.get("aliases") or ()),
                        source=str(item.get("source") or "user"),
                    )
                )
        by_record: dict[tuple[str, str], list[Any]] = {}
        inbound: dict[str, list[dict[str, Any]]] = {}
        for entity in entities:
            try:
                edges = self.service.graph_backlinks(
                    entity.id, limit=self.api._MAX_BACKLINKS
                )
            except Exception:
                self.api.logger.debug(
                    "vault: backlinks failed for %s", entity.id, exc_info=True
                )
                edges = []
            edges.sort(
                key=lambda edge: (
                    str(edge.get("created_at") or ""),
                    int(edge.get("id") or 0),
                )
            )
            inbound[entity.id] = edges
            for edge in edges:
                reference = (
                    str(edge.get("from_kind") or ""),
                    str(edge.get("from_ref") or ""),
                )
                by_record.setdefault(reference, []).append(entity)
        return entities, by_record, inbound


class VaultProjection:
    def __init__(self, vault, records, graph):
        self.vault, self.records, self.graph = vault, records, graph
        self.api, self.notes, self.summaries = contract(), {}, {}
        self.tags, self.sessions = {}, {}

    def add(self, note):
        self.notes[note.relpath] = note

    def build(self):
        entities, by_record, inbound = self.graph
        for record in self.records:
            try:
                note = self.api.render_record(
                    record, entities=by_record.get(self.vault._link_key(record))
                )
            except Exception:
                self.api.logger.debug(
                    "vault: failed to render record %s",
                    getattr(record, "id", "?"),
                    exc_info=True,
                )
                continue
            self.add(note)
            base = Path(note.relpath).stem
            self.summaries[self.vault._link_key(record)] = (
                base,
                " ".join((record.text or str(record.value or "")).split())[:100],
            )
            for tag in note.tags:
                self.tags.setdefault(tag, []).append((base, note.title))
            if record.kind.value == "episodic" and record.conversation_id:
                self.sessions.setdefault(record.conversation_id, []).append(
                    (base, note.title)
                )
        for entity in entities:
            try:
                note = self.vault._render_entity(entity, inbound, self.summaries)
            except Exception:
                self.api.logger.debug(
                    "vault: entity page failed for %s", entity.id, exc_info=True
                )
                continue
            self.add(note)
        for groups, renderer in (
            (self.tags, self.api.render_tag_hub),
            (self.sessions, self.api.render_session_hub),
        ):
            for identity, members in groups.items():
                self.add(renderer(identity, members))
        try:
            self.notes[self.api._INDEX_NAME] = self.api.render_index(
                self.records, entities=entities, mode=self.vault._mode
            )
        except Exception:
            self.api.logger.debug("vault: index render failed", exc_info=True)
        return self.notes

    def entity(self, entity, inbound, summaries):
        compiled, backlinks, evidence, seen = [], [], [], set()
        for edge in inbound.get(entity.id, []):
            reference = str(edge.get("from_kind") or ""), str(
                edge.get("from_ref") or ""
            )
            summary = summaries.get(reference)
            if summary is not None and summary[0] not in seen:
                seen.add(summary[0])
                backlinks.append(summary[0])
                if reference[0] == "semantic":
                    compiled.append(summary)
            stamp = str(edge.get("created_at") or "")[:19]
            context = " ".join(str(edge.get("context") or "").split())[:160]
            evidence.append(
                f"- {stamp} — `{reference[1]}`" + (f" — {context}" if context else "")
            )
        path = (
            self.vault._dir
            / f"{self.api._ENTITIES_DIR}/{self.api._entity_basename(entity)}.md"
        )
        try:
            previous = self.api.split_page(path.read_text(encoding="utf-8"))[1]
        except OSError:
            previous = ""
        return self.api.render_entity_page(
            entity,
            compiled=compiled,
            backlinks=backlinks,
            evidence=evidence,
            existing_body=previous,
        )


class ManifestProjection:
    def __init__(self, vault):
        self.vault, self.api = vault, contract()

    def write(self, notes, conflicts):
        previous = self.vault._load_manifest()
        current, writes, pruned = {}, 0, 0
        for relative, note in notes.items():
            if relative in conflicts:
                if relative in previous:
                    current[relative] = previous[relative]
                continue
            current[relative] = hashlib.sha256(note.content.encode("utf-8")).hexdigest()
            target = self.vault._dir / relative
            try:
                same = target.read_text(encoding="utf-8") == note.content
            except OSError:
                same = False
            if same:
                continue
            try:
                self.api.atomic_write(target, note.content, fsync=False)
                writes += 1
            except OSError:
                self.api.logger.debug(
                    "vault: write failed for %s", relative, exc_info=True
                )
                del current[relative]
        for relative in previous:
            if relative in current:
                continue
            try:
                (self.vault._dir / relative).unlink()
                pruned += 1
            except OSError:
                pass
        try:
            self.api.atomic_write(
                self.vault._manifest_path(),
                json.dumps(current, indent=0, sort_keys=True),
                fsync=False,
            )
        except OSError:
            self.api.logger.debug("vault: manifest write failed", exc_info=True)
        return len(current), writes, pruned


class VaultCycle:
    def __init__(self, vault):
        self.vault, self.api = vault, contract()

    def run(self, knowledge, enqueue):
        edits: dict[str, Any] = (
            self.vault.absorb_edits()
            if self.vault.two_way
            else dict(absorbed=0, conflicts={}, rejected=0)
        )
        conflicts = dict(edits["conflicts"])
        records = self.vault._svc.get_records()
        projection = VaultProjection(self.vault, records, self.vault._graph_view())
        files, written, pruned = ManifestProjection(self.vault).write(
            projection.build(), conflicts
        )
        try:
            (self.vault._dir / self.api._RAW_DIR).mkdir(parents=True, exist_ok=True)
        except OSError:
            self.api.logger.debug("vault: could not create raw/", exc_info=True)
        swept = self.vault.sweep_raw(knowledge=knowledge, enqueue=enqueue)
        seeded = self.vault.seed(self.api.starter_seeds(self.vault._mode))
        result = dict(
            records=len(records),
            files=files,
            written=written,
            pruned=pruned,
            mode=self.vault._mode,
            absorbed=edits["absorbed"],
            rejected=edits["rejected"],
            conflicts=len(conflicts),
            raw_ingested=swept["ingested"],
            seeded=seeded["written"],
        )
        self.api.logger.info("memory vault synced: %s", result)
        return result


class VaultEdits:
    def __init__(self, vault):
        self.vault, self.api = vault, contract()

    def absorb(self):
        result: dict[str, Any] = dict(absorbed=0, rejected=0, conflicts={})
        if not self.vault.two_way:
            return result
        for relative in sorted(self.vault._load_manifest()):
            path = self.vault._dir / relative
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            header, body = self.api.split_page(content)
            metadata = self.api.parse_frontmatter(header)
            claimed = str(metadata.get(self.api.HASH_KEY) or "")
            if not claimed or claimed == self.api.body_hash(body):
                continue
            accepted, reason = self.vault._apply_page_edit(relative, metadata, body)
            result["absorbed" if accepted else "rejected"] += 1
            if not accepted:
                result["conflicts"][relative] = reason
                self.vault._flag_conflict(path, header, body, reason)
        if result["absorbed"] or result["conflicts"]:
            self.api.logger.info(
                "memory vault absorbed %d edit(s), %d conflict(s): %s",
                result["absorbed"],
                len(result["conflicts"]),
                sorted(result["conflicts"]),
            )
        return result

    def apply(self, metadata, body):
        identity, kind = str(metadata.get("id") or ""), str(metadata.get("kind") or "")
        if not identity:
            return False, "no id in frontmatter"
        if kind not in self.api._EDITABLE_KINDS:
            return False, f"{kind or 'unknown'} pages are read-only"
        value = self.api.extract_edited_value(body)
        if value is None:
            return False, "cannot locate the edited value (H1 heading missing?)"
        if not value.strip():
            return False, "edited value is empty — delete the page to propose removal"
        projected = str(metadata.get("updated_at") or "")
        stale = False
        if projected:
            current = self.vault._svc.get_semantic(identity) or {}
            version = str(current.get("updated_at") or "")
            stale = bool(version) and version != projected
        accepted, detail = self.vault._svc.apply_vault_edit(identity, value)
        if accepted and stale:
            self.api.logger.info(
                "vault edit to %s won over a concurrent store write (undo via the memory event log)",
                identity,
            )
        return bool(accepted), detail

    def flag(self, page, header, body, reason):
        metadata = self.api.parse_frontmatter(header)
        if str(metadata.get(self.api.CONFLICT_KEY) or "") == reason:
            return
        fields = [
            (name, value)
            for name, value in metadata.items()
            if name not in (self.api.CONFLICT_KEY, self.api.HASH_KEY)
        ]
        fields.extend(
            (
                (self.api.CONFLICT_KEY, reason),
                (self.api.HASH_KEY, str(metadata.get(self.api.HASH_KEY) or "")),
            )
        )
        content = self.api.frontmatter(fields) + "\n\n" + body.rstrip() + "\n"
        try:
            self.api.atomic_write(page, content, fsync=False)
        except OSError:
            self.api.logger.debug(
                "vault: could not flag conflict on %s", page, exc_info=True
            )


class VaultCapture:
    def __init__(self, vault):
        self.vault, self.api = vault, contract()

    def sweep(self, knowledge, enqueue):
        result = dict(ingested=0, failed=0)
        raw = self.vault._dir / self.api._RAW_DIR
        if not raw.is_dir():
            return result
        candidates = [
            path
            for path in sorted(raw.iterdir())
            if path.is_file()
            and not path.is_symlink()
            and not path.name.startswith(".")
        ]
        if not candidates:
            return result
        if knowledge is None:
            from gideon.cognition.knowledge import get_knowledge_store

            knowledge = get_knowledge_store()
        destination = self.vault._dir / self.api._RAW_DONE_DIR
        for path in candidates:
            try:
                body = ""
                if path.suffix.lower() in (".md", ".markdown", ".txt", ".text", ""):
                    body = path.read_text(encoding="utf-8", errors="replace")[
                        : self.api._MAX_BODY
                    ]
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / path.name
                path.replace(target)
                identity = knowledge.create_typed_item(
                    item_type="note",
                    title=path.stem or path.name,
                    content=body,
                    provider="native",
                    tags=["vault-raw"],
                    extra={"file_path": str(target)},
                )
                if not identity:
                    result["failed"] += 1
                    continue
                knowledge.update_item(identity, processing_status="queued", touch=False)
                if callable(enqueue):
                    enqueue(identity)
                result["ingested"] += 1
            except Exception:
                self.api.logger.debug(
                    "vault: raw sweep failed for %s", path, exc_info=True
                )
                result["failed"] += 1
        if result["ingested"]:
            self.api.logger.info(
                "memory vault raw sweep: %d file(s) → knowledge", result["ingested"]
            )
        return result

    def seed(self, seeds):
        result = dict(written=0, kept=0)
        for relative, content in sorted(seeds.items()):
            target = self.vault._dir / relative
            try:
                existing = target.read_text(encoding="utf-8")
            except OSError:
                existing = ""
            if existing:
                header, body = self.api.split_page(existing)
                claimed = str(
                    self.api.parse_frontmatter(header).get(self.api.HASH_KEY) or ""
                )
                if (
                    not claimed
                    or claimed != self.api.body_hash(body)
                    or existing == content
                ):
                    result["kept"] += 1
                    continue
            try:
                self.api.atomic_write(target, content, fsync=False)
                result["written"] += 1
            except OSError:
                self.api.logger.debug(
                    "vault: seed write failed for %s", relative, exc_info=True
                )
        return result


class VaultInspection:
    def __init__(self, vault):
        self.vault, self.api = vault, contract()

    def pages(self):
        found = {}
        for path in sorted(self.vault._dir.rglob("*.md")):
            relative = path.relative_to(self.vault._dir).as_posix()
            if relative.startswith((self.api._RAW_DIR + "/", ".")):
                continue
            try:
                header, body = self.api.split_page(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            found[relative] = self.api.parse_frontmatter(header), body
        return found

    def flags(self):
        if not self.vault._dir.is_dir():
            return []
        pages = self.pages()
        names = {Path(relative).stem for relative in pages}
        owned, seeds = set(self.vault._load_manifest()), set(
            self.api.starter_seeds(self.vault._mode)
        )
        flags: list[tuple[str, str, str]] = []
        incoming: dict[str, set[str]] = {}
        for relative, (metadata, body) in pages.items():
            conflict = str(metadata.get(self.api.CONFLICT_KEY) or "")
            if conflict:
                flags.append(
                    ("vault_conflict", relative, f"{conflict} — edit not applied")
                )
            claimed = str(metadata.get(self.api.HASH_KEY) or "")
            if claimed and claimed != self.api.body_hash(body) and not conflict:
                flags.append(
                    (
                        "vault_stale_hash",
                        relative,
                        "edited since the last sync — not yet read back",
                    )
                )
            for match in self.api.WIKILINK_RE.findall(body):
                target = match.strip()
                if not target:
                    continue
                if target not in names:
                    flags.append(
                        ("vault_broken_link", relative, f"[[{target}]] has no page")
                    )
                elif str(metadata.get("type") or "") in self.api._RECORD_PAGE_TYPES:
                    incoming.setdefault(target, set()).add(Path(relative).stem)
            if relative not in owned and relative not in seeds:
                flags.append(
                    (
                        "vault_orphan_page",
                        relative,
                        "not produced by the projection — yours to keep",
                    )
                )
        for relative, (metadata, body) in pages.items():
            if str(metadata.get("type") or "") == "entity":
                listed = {
                    target.strip() for target in self.api.WIKILINK_RE.findall(body)
                }
                for source in sorted(incoming.get(Path(relative).stem, set())):
                    if source not in listed:
                        flags.append(
                            (
                                "vault_backlink_asymmetry",
                                relative,
                                f"[[{source}]] links here but is not listed back",
                            )
                        )
        return flags
