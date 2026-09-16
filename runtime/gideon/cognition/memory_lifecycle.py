"""Lifecycle policies for typed memory records held by an existing service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from gideon.cognition.memory_record import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
)


def record_key(namespace, payload):
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return f"user.{namespace}.{digest[:12]}"


def tag_values(row):
    tags = row.get("tags") or []
    if isinstance(tags, str):
        try:
            return json.loads(tags)
        except ValueError:
            return []
    return tags


def bullet_block(header, lines, footer):
    return "\n".join((header, *(f"- {line}" for line in lines), footer))


class SessionLifecycle:
    def __init__(self, service, summary_cap):
        self.service, self.summary_cap = service, summary_cap

    def seal(self, session_key):
        service = self.service
        note = service.get_record(service._working_key(session_key))
        if note is not None and note.text.strip():
            service.write_episodic(
                note.text[: self.summary_cap],
                conversation_id=session_key,
                tags=["sealed", "session"],
                importance=0.6,
                source="seal",
            )
            service._vs.delete(note.id, source="seal")
        rows = service._vs.query(scope=MemoryScope.SESSION.value, scope_ref=session_key)
        removable = (row for row in rows if row.source != "seal")
        return sum(
            1 for row in removable if service._vs.delete(row.id, source="session_sweep")
        )

    def promote(self, threshold, now):
        service = self.service
        total = 0
        for record in service._vs.iter_records():
            if (
                record.scope == MemoryScope.GLOBAL
                or record.kind == MemoryKind.COMMITMENT
            ):
                continue
            if not (record.heat(now=now) >= threshold and record.recall_count >= 2):
                continue
            table, identity = (
                ("episodic_memories", "id")
                if record.kind == MemoryKind.EPISODIC
                else ("semantic_memory", "key")
            )
            service._vs.db.execute(
                f"UPDATE {table} SET scope = ? WHERE {identity} = ?",
                (MemoryScope.GLOBAL.value, record.id),
            )
            service._vs.db.commit()
            service._vs.append_event(
                event_type="promote_scope",
                memory_type=record.kind.value,
                memory_key=record.id,
                old_value=record.scope.value,
                new_value=MemoryScope.GLOBAL.value,
                source="heat_promote",
            )
            total += 1
        return total

    @staticmethod
    def expire(archive, now, lifetimes):
        reference = now or datetime.now(tz=timezone.utc)
        count = 0
        for record in archive.iter_records():
            lifetime = lifetimes.get(record.category or "")
            if lifetime is None or (
                record.scope == MemoryScope.GLOBAL and record.source == "user_explicit"
            ):
                continue
            stamp = record.last_accessed_at or record.updated_at or record.created_at
            if not stamp:
                continue
            try:
                last_seen = datetime.fromisoformat(stamp)
            except (TypeError, ValueError):
                continue
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            elapsed = (reference - last_seen).total_seconds() / 86400.0
            if elapsed > lifetime and archive.delete(record.id, source="category_ttl"):
                count += 1
        return count


class ObservationLedger:
    def __init__(self, service):
        self.service = service

    def reinforce(self, identity, kind, value, source, scope, reference, category=None):
        previous = self.service.get_record(identity)
        visits = 1 + (previous.recall_count if previous else 0)
        record = MemoryRecord(
            id=identity,
            kind=kind,
            value=value,
            confidence=0.85,
            source=source,
            tier=MemoryTier.SEMANTIC,
            scope=scope,
            scope_ref=reference,
            category=category,
            recall_count=visits,
        )
        self.service.put([record])
        return identity

    def priors(self, limit):
        from gideon.cognition.after_turn_review import is_environment_failure_claim

        candidates = []
        for record in self.service.get_records(kinds={MemoryKind.PROCEDURAL.value}):
            if record.scope != MemoryScope.GLOBAL:
                continue
            if self.service._is_surfaceable_prior(
                record
            ) and not is_environment_failure_claim(record.text):
                candidates.append(record)
        ordered = sorted(candidates, key=lambda record: record.heat(), reverse=True)
        return [
            dict(key=record.id, text=record.text, heat=round(record.heat(), 3))
            for record in ordered[:limit]
        ]

    def collapse_failures(self, threshold):
        groups = {}
        for record in self.service.get_records(kinds={MemoryKind.PROCEDURAL.value}):
            text = record.text
            if any(marker in text for marker in ("→ failed", "→ denied")):
                owner = text.split(" on ", 1)[0].strip()
                groups.setdefault(owner, []).append(record)
        count = 0
        for tool, records in groups.items():
            if len(records) < threshold:
                continue
            replacement = MemoryRecord(
                id=record_key("procedural.synth", tool),
                kind=MemoryKind.PROCEDURAL,
                value=f"{tool} is unreliable for these task shapes — prefer an alternative "
                f"(synthesized from {len(records)} failures)",
                confidence=0.8,
                source="failure_synthesis",
                tier=MemoryTier.SEMANTIC,
                scope=MemoryScope.GLOBAL,
                category="decision",
                recall_count=sum(record.recall_count for record in records),
            )
            self.service.put([replacement])
            for record in records:
                self.service._vs.delete(record.id, source="failure_synthesis")
            count += 1
        return count

    def persona_lines(self, agent, limit):
        records = self.service.get_records(kinds={MemoryKind.SELF_PERSONA.value})
        matching = (record for record in records if record.scope_ref == agent)
        ranked = sorted(matching, key=lambda record: record.heat(), reverse=True)
        return [record.text for record in ranked[:limit] if record.text.strip()]


class CommitmentAgenda:
    def __init__(self, service):
        self.service = service

    def available(self, agent, cap):
        records = self.service.get_records(kinds={MemoryKind.COMMITMENT.value})
        active = sum(
            1
            for record in records
            if record.scope_ref == agent
            and not (record.extra or {}).get("dismissed_at")
        )
        return not active >= cap

    def capture(self, identity, agent, channel, text, due_window, confidence):
        envelope = dict(text=text.strip(), due_window=due_window, channel=channel)
        self.service.put(
            [
                MemoryRecord(
                    id=identity,
                    kind=MemoryKind.COMMITMENT,
                    value=envelope,
                    confidence=confidence,
                    source="commitment",
                    tier=MemoryTier.EPISODIC,
                    scope=MemoryScope.AGENT,
                    scope_ref=agent,
                    category="event",
                )
            ]
        )
        return identity

    def due(self, now_iso, *, agent=None, all_agents=False):
        output = []
        records = self.service.get_records(kinds={MemoryKind.COMMITMENT.value})
        for record in records:
            if not all_agents and record.scope_ref != agent:
                continue
            payload = record.value if isinstance(record.value, dict) else {}
            window = payload.get("due_window")
            if not window or not window <= now_iso:
                continue
            row = dict(
                key=record.id,
                text=payload.get("text", ""),
                channel=payload.get("channel"),
                due_window=window,
            )
            if all_agents:
                row["agent"] = record.scope_ref or ""
            output.append(row)
        return output


class DailyMemoryRollup:
    def __init__(self, service, archive, tag, logger):
        self.service, self.archive, self.tag, self.logger = (
            service,
            archive,
            tag,
            logger,
        )

    def calendar(self, now):
        today = (now or datetime.now(tz=timezone.utc)).date().isoformat()
        calendar = {}
        for record in self.archive.iter_records(kinds={MemoryKind.EPISODIC.value}):
            if self.tag in (record.tags or []) or not record.created_at:
                continue
            day = record.created_at[:10]
            if len(day) == 10 and day < today:
                calendar.setdefault(day, []).append(record)
        return calendar

    @staticmethod
    def extract(day, texts):
        shown = texts[:20]
        lines = [f"Daily digest for {day} — {len(texts)} memory event(s):"]
        lines.extend(f"- {text[:200]}" for text in shown)
        if len(texts) > len(shown):
            lines.append(f"- …and {len(texts) - len(shown)} more.")
        return "\n".join(lines)

    def build(self, now, max_days, summarizer):
        calendar = self.calendar(now)
        written = 0
        for day in sorted(calendar, reverse=True)[:max_days]:
            if self.service._digest_exists(day):
                continue
            records = sorted(calendar[day], key=lambda record: record.created_at)
            texts = [
                " ".join((record.text or "").split())
                for record in records
                if record.text.strip()
            ]
            if not texts:
                continue
            body = None
            if summarizer is not None:
                try:
                    body = summarizer(day, texts)
                except Exception:
                    self.logger.debug(
                        "daily-digest summarizer failed for %s", day, exc_info=True
                    )
            if not body:
                body = self.extract(day, texts)
            self.service.write_episodic(
                body,
                conversation_id=f"daily-digest:{day}",
                tags=[self.tag, day],
                importance=0.9,
                source="daily_digest",
            )
            written += 1
        return written

    @staticmethod
    def listing(records, tag, limit):
        result = []
        for record in records:
            day = next((value for value in tag_values(record) if value != tag), "")
            result.append(
                dict(
                    day=day,
                    text=record.get("text", ""),
                    created_at=record.get("created_at", ""),
                )
            )
        return sorted(result, key=lambda row: row["day"], reverse=True)[:limit]


class RecallOrder:
    @staticmethod
    def rank(hits, now, limit, current_owner):
        for hit in hits:
            score = float(hit.get("score", hit.get("cosine_sim", 0.0)) or 0.0)
            record = (
                MemoryRecord.from_episodic_row(hit) if "created_at" in hit else None
            )
            heat = record.heat(now=now) if record is not None else 0.0
            hit["ranked_score"] = score * (1.0 + min(0.5, 0.33 * heat))
        from gideon.cognition.vector_memory import _owner_rank_bonus

        owner = current_owner()

        def ordering(hit):
            score = float(hit.get("ranked_score", 0.0) or 0.0)
            return score + _owner_rank_bonus(hit.get("contributor"), owner)

        hits.sort(key=ordering, reverse=True)
        return hits[:limit]

    @staticmethod
    def provenance(hit):
        return dict(
            text=hit.get("text", ""),
            source=hit.get("source") or "",
            session=hit.get("conversation_id") or "",
            created_at=hit.get("created_at") or "",
            score=round(
                float(hit.get("ranked_score", hit.get("score", 0.0)) or 0.0), 4
            ),
            contributor=str(hit.get("contributor") or ""),
        )


class MemoryWriteScreen:
    def __init__(self, logger):
        self.logger = logger

    def blocked(self, text, source):
        try:
            from gideon.security.supply_chain import Verdict, default_scanner

            report = default_scanner.scan_text(text, surface="manifest")
            if report.verdict is not Verdict.DANGEROUS:
                return False
            rules = ", ".join(sorted({finding.rule for finding in report.findings}))
            self.logger.warning(
                "memory write BLOCKED (source=%s): injection/steering payload (%s)",
                source,
                rules,
            )
            self.record_block(source)
            return True
        except Exception:
            self.logger.debug("memory-write scan errored (fail-open)", exc_info=True)
            return False

    @staticmethod
    def record_block(source):
        try:
            from gideon.security.sel import sel

            sel().log_api_access(
                caller=f"memory_service.write:{source}",
                operation="memory_write",
                outcome="blocked",
                source="memory",
                resources="",
                error="injection/bidi payload in untrusted memory write",
            )
        except Exception:
            pass
