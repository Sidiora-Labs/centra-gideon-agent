"""Mutation journal access and bounded, reversible curation passes."""

from __future__ import annotations


class CuratorBindings:
    def __init__(self):
        from . import curator

        self.api = curator


class CuratorJournal(CuratorBindings):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def ensure(self):
        if not self.owner._bootstrapped:
            with self.owner._staging._cursor() as cursor:
                cursor.execute(MUTATION_SCHEMA)
            self.owner._bootstrapped = True

    def append(self, mutation):
        self.owner._ensure()
        with self.owner._staging._cursor() as cursor:
            values = dict(
                operation=mutation.operation,
                kind=mutation.kind,
                entity=mutation.entity,
                before=self.api.json.dumps(mutation.before),
                after=self.api.json.dumps(mutation.after),
                at=mutation.at or self.api._now(),
            )
            columns = ", ".join(values)
            cursor.execute(
                f"INSERT INTO curator_mutations ({columns}) VALUES (?, ?, ?, ?, ?, ?);",
                tuple(values.values()),
            )
            return int(cursor.lastrowid or 0)

    def rows(self, limit, *, pending):
        self.owner._ensure()
        filters = " WHERE undone_at = ''" if pending else ""
        with self.owner._staging._cursor() as cursor:
            return cursor.execute(
                "SELECT * FROM curator_mutations"
                + filters
                + " ORDER BY id DESC LIMIT ?;",
                (int(limit),),
            ).fetchall()

    def pending(self, limit):
        return [
            (int(row["id"]), self.api._row_to_mutation(row))
            for row in self.rows(limit, pending=True)
        ]

    def mark(self, identifier):
        self.owner._ensure()
        with self.owner._staging._cursor() as cursor:
            stamp = self.api._now()
            identity = int(identifier)
            updated = cursor.execute(
                "UPDATE curator_mutations SET undone_at = ? WHERE id = ? AND undone_at = '';",
                (stamp, identity),
            )
            return bool(updated.rowcount)

    def changelog(self, limit):
        return [
            self.api._row_to_mutation(row).to_dict()
            for row in self.rows(limit, pending=False)
        ]


class AgingSweep(CuratorBindings):
    def __init__(
        self, candidates, *, active_dates, now, dry_run, mode, batch_size, log
    ):
        super().__init__()
        api = self.api
        self.now = now or api.datetime.now(api.timezone.utc)
        self.report = api.CuratorReport(mode=mode, dry_run=dry_run)
        self.population = (
            [candidate for candidate in candidates if candidate.kind == mode]
            if mode
            else candidates
        )
        ordered = sorted(
            self.population,
            key=lambda candidate: (candidate.audited_at or "", candidate.entity),
        )
        self.batch = ordered[: max(1, batch_size)]
        self.cuts = []
        self.verdicts = {}
        self.active_dates = active_dates
        self.log = log
        self.dry_run = dry_run

    def consider(self, candidate):
        api, report = self.api, self.report
        report.scanned += 1
        if candidate.pinned:
            report.skipped_pinned.append(candidate.entity)
            return
        if candidate.source_type == "user":
            report.skipped_user.append(candidate.entity)
            return
        idle = api.active_days_between(
            self.active_dates or [],
            candidate.last_used_at or candidate.created_at,
            self.now.isoformat(),
        )
        parameters = {
            name: getattr(candidate, name)
            for name in (
                "kind",
                "importance",
                "stability",
                "pinned",
                "source_type",
                "linked_neighbors",
            )
        }
        parameters["active_days_since_use"] = idle
        decision = api.evaluate(**parameters)
        self.verdicts[candidate.entity] = decision
        if decision.review:
            report.review_proposals.append(candidate.entity)
            return
        next_state = api.target_state(decision, candidate.state)
        if next_state == candidate.state:
            return
        if next_state == api.STATE_ARCHIVED:
            self.cuts.append(candidate)
            return
        collections = {
            api.STATE_STALE: report.to_stale,
            api.STATE_ACTIVE: report.reactivated,
        }
        destination = collections.get(next_state)
        if destination is not None:
            destination.append(candidate.entity)

    def refused(self):
        api = self.api
        eligible = sum(
            1
            for candidate in self.population
            if not candidate.pinned and candidate.source_type != "user"
        )
        if (
            eligible < api.MIN_SET_FOR_REFUSAL
            or not len(self.cuts) > eligible * api.MAX_CUT_FRACTION
        ):
            return False
        self.report.refused = (
            f"would archive {len(self.cuts)} of {eligible} eligible entities "
            f"(>{api.MAX_CUT_FRACTION:.0%}) — refusing; the likely cause is a bug in the pass"
        )
        api.logger.warning(self.report.summary())
        return True

    def journal(self):
        api, report = self.api, self.report
        journal = self.log or api.MutationLog()
        try:
            groups = (
                ("age", report.to_stale, api.STATE_STALE),
                ("archive", report.to_archived, api.STATE_ARCHIVED),
                ("reactivate", report.reactivated, api.STATE_ACTIVE),
            )
            for operation, entities, state in groups:
                for entity in entities:
                    journal.append(
                        api._mutation(
                            operation, entity, self.batch, state, self.verdicts
                        )
                    )
        finally:
            if self.log is None:
                journal.close()

    def execute(self):
        for candidate in self.batch:
            self.consider(candidate)
        if self.refused():
            return self.report
        self.report.to_archived = [candidate.entity for candidate in self.cuts]
        if not self.dry_run:
            self.journal()
        if self.report.changed or self.report.review_proposals:
            self.api.logger.info(self.report.summary())
        return self.report


class MutationProjection(CuratorBindings):
    def decode(self, row):
        def document(raw):
            try:
                decoded = self.api.json.loads(raw or "{}")
            except ValueError:
                return {}
            return decoded if isinstance(decoded, dict) else {}

        values = {name: str(row[name]) for name in ("operation", "kind", "entity")}
        values.update(
            before=document(row["before"]),
            after=document(row["after"]),
            at=str(row["at"]),
            undone_at=str(row["undone_at"] or ""),
        )
        return self.api.Mutation(**values)

    def create(self, operation, entity, batch, target, verdicts):
        original = None
        for candidate in batch:
            if candidate.entity == entity:
                original = candidate
                break
        verdict = verdicts.get(entity)
        evidence = {"state": target, "strength": None, "reason": ""}
        if verdict is not None:
            evidence.update(strength=round(verdict.strength, 4), reason=verdict.reason)
        return self.api.Mutation(
            operation=operation,
            kind=original.kind if original else "",
            entity=entity,
            before={"state": original.state if original else ""},
            after=evidence,
            at=self.api._now(),
        )


class CurationFindings(CuratorBindings):
    def promotions(self, records, active_dates, now):
        from gideon.cognition.learning.usage import promotion_ready

        api = self.api
        clock = now or api.datetime.now(api.timezone.utc)
        suggestions = []
        for record in records:
            if getattr(record, "pinned", False):
                continue
            if getattr(record, "source_type", "agent") == "user":
                continue
            idle = api.active_days_between(
                active_dates or [],
                getattr(record, "last_used_at", "")
                or getattr(record, "first_seen_at", ""),
                clock.isoformat(),
            )
            ready, reason = promotion_ready(record, active_days_idle=idle)
            if ready:
                values = dict(
                    kind=str(getattr(record, "kind", "")),
                    entity=str(getattr(record, "entity", "")),
                    why=reason,
                    uses=int(getattr(record, "used", 0) or 0),
                    contexts=int(getattr(record, "context_diversity", 0) or 0),
                    idle_days=idle,
                )
                suggestions.append(api.PromotionSuggestion(**values))
        return suggestions

    def file(self, findings, *, dry_run, promotion):
        if dry_run or not findings:
            return 0
        from gideon.cognition.learning.proposals import Kind, enqueue

        filed = 0
        for finding in findings:
            payload = (
                self.promotion(finding, Kind)
                if promotion
                else self.review(finding, Kind)
            )
            _, proposal = enqueue(**payload)
            filed += int(proposal is not None)
        return filed

    @staticmethod
    def review(entity, kinds):
        return dict(
            kind=kinds.RETIREMENT.value,
            title=f"Review {entity} — confident but unused",
            body=(
                f"{entity} has decayed to a low strength while remaining highly stable: "
                "the system is confident about it, but nothing has used it. Keep it, pin "
                "it, or retire it."
            ),
            target=entity,
            provenance="inferred",
            source_cadence="curator",
            occurrences=1,
            min_evidence=1,
        )

    @staticmethod
    def promotion(suggestion, kinds):
        return dict(
            kind=kinds.TIER_MIGRATION.value,
            title=f"Widen {suggestion.entity}'s scope — it earned it",
            body=suggestion.body(),
            target=suggestion.entity,
            provenance="inferred",
            source_cadence="curator",
            tags=["promotion", suggestion.kind],
            occurrences=max(1, suggestion.uses),
            min_evidence=1,
        )

    def detect(self, candidates, sizes):
        measured = sizes or {}
        result = []
        for candidate in candidates:
            tokens = measured.get(candidate.entity, 0)
            if not tokens > 500:
                continue
            result.append(
                self.api.Detection(
                    "compress_summary",
                    candidate.kind,
                    candidate.entity,
                    f"{tokens} tokens — summarizable",
                    int(tokens * 0.6),
                )
            )
        return result


MUTATION_SCHEMA = """
                CREATE TABLE IF NOT EXISTS curator_mutations (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    kind      TEXT NOT NULL,
                    entity    TEXT NOT NULL,
                    before    TEXT NOT NULL DEFAULT '{}',
                    after     TEXT NOT NULL DEFAULT '{}',
                    at        TEXT NOT NULL,
                    undone_at TEXT NOT NULL DEFAULT ''
                );
                """
