"""Record storage, admission and human decisions for learning proposals."""

from __future__ import annotations

from dataclasses import asdict
from itertools import islice


class QueueBindings:
    def __init__(self):
        from . import proposals

        self.api = proposals


class ProposalFiles(QueueBindings):
    def decode(self, path, record):
        return record(**self.api.json.loads(path.read_text(encoding="utf-8")))

    def encode(self, path, document):
        self.api.atomic_write(path, self.api.json.dumps(document, indent=2))

    def load_decisions(self):
        api = self.api
        try:
            document = api.json.loads(api._decisions_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        records = {}
        for identity, payload in (document or {}).items():
            try:
                record = api.Decision(**payload)
            except (TypeError, ValueError):
                pass
            else:
                records[str(identity)] = record
        return records

    def save_decisions(self, records):
        try:
            self.encode(
                self.api._decisions_path(),
                {key: record.to_dict() for key, record in records.items()},
            )
        except OSError:
            self.api.logger.debug("decision store write failed", exc_info=True)

    def load(self, identifier):
        try:
            return self.decode(self.api._path(identifier), self.api.Proposal)
        except (OSError, ValueError, TypeError):
            return None

    def save(self, proposal):
        try:
            self.encode(self.api._path(proposal.id), proposal.to_dict())
        except OSError:
            self.api.logger.debug("proposal write failed", exc_info=True)
            return False
        return True

    def all(self):
        directory = self.api._dir()
        records = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                if path.name == self.api._DECISIONS_FILE:
                    continue
                try:
                    record = self.decode(path, self.api.Proposal)
                except (OSError, ValueError, TypeError):
                    pass
                else:
                    records.append(record)
        return records

    def attach(self, identifier, field, report):
        proposal = self.api._load(identifier)
        if proposal is None:
            return False
        setattr(proposal, field, dict(report or {}))
        return self.api._save(proposal)

    def unlink(self, identifier, message, *arguments):
        try:
            self.api._path(identifier).unlink()
        except OSError:
            self.api.logger.debug(message, *arguments, exc_info=True)
            return False
        return True

    def prune(self, keep):
        api = self.api
        lineage = [
            record
            for record in api._all()
            if record.status == api.Status.SUPERSEDED.value
        ]
        lineage.sort(key=lambda record: record.updated_at or record.created_at)
        excess = max(0, len(lineage) - max(0, keep))
        deleted = sum(
            self.unlink(record.id, "superseded prune failed for %s", record.id)
            for record in lineage[:excess]
        )
        if deleted:
            api.logger.info("pruned %d superseded proposal record(s)", deleted)
        return deleted

    def expire(self, records):
        api = self.api
        waiting = (
            record for record in records if record.status == api.Status.PENDING.value
        )
        oldest = min(waiting, key=lambda record: record.created_at, default=None)
        if oldest is not None and self.unlink(oldest.id, "proposal expiry failed"):
            api.logger.info("proposal queue full; expired oldest %s", oldest.id)


class ProposalText(QueueBindings):
    NEGATIONS = (
        " never ",
        " don't ",
        " do not ",
        " avoid ",
        " stop ",
        " no longer ",
        " not ",
        " without ",
    )

    @classmethod
    def polarity(cls, text):
        padded = " " + text.lower() + " "
        for token in cls.NEGATIONS:
            if token in padded:
                return -1
        return 1

    @staticmethod
    def subject(text, ignored):
        words = [word.strip(".,;:!?\"'") for word in (text or "").lower().split()]
        meaningful = filter(lambda word: bool(word) and word not in ignored, words)
        return " ".join(islice(meaningful, 2))

    @staticmethod
    def numbers(text):
        found = set()
        for word in (text or "").replace(",", " ").split():
            if word.replace(".", "").isdigit():
                found.add(word)
        return found

    @staticmethod
    def similarity(left, right):
        words_left, words_right = set((left or "").lower().split()), set(
            (right or "").lower().split()
        )
        if not words_left or not words_right:
            return 0.0
        overlap = words_left.intersection(words_right)
        total = len(words_left) + len(words_right) - len(overlap)
        return len(overlap) / total

    def contradicts(self, left, right):
        api = self.api
        subject_pair = (api._subject_span(left), api._subject_span(right))
        if subject_pair[0] != subject_pair[1]:
            return False
        if api._polarity(left) != api._polarity(right):
            return True
        first, second = api._numbers(left), api._numbers(right)
        numeric_conflict = bool(first and second and first != second)
        return (
            numeric_conflict
            and api._similarity(left, right) >= api._NUMBER_CONFLICT_MIN_SIM
        )

    def resolve(self, candidate, records):
        api = self.api
        selected, score = None, 0.0
        for record in records:
            if (record.kind, record.status, record.target) != (
                candidate.kind,
                api.Status.PENDING.value,
                candidate.target,
            ):
                continue
            immediate = None
            if record.fingerprint == candidate.fingerprint:
                immediate = api.Verdict.REINFORCE
            elif api.contradicts(candidate.body, record.body):
                immediate = api.Verdict.REPLACE
            if immediate is not None:
                return immediate, record
            similarity = api._similarity(candidate.body, record.body)
            if similarity > score:
                selected, score = record, similarity
        if selected is not None and not score < api.SIM_NEW:
            same_subject = api._subject_span(candidate.body) == api._subject_span(
                selected.body
            )
            if same_subject:
                label = (
                    api.Verdict.REINFORCE
                    if score >= api.SIM_REINFORCE
                    else api.Verdict.MERGE
                )
                return label, selected
        return api.Verdict.NEW, None


class DecisionMemory(QueueBindings):
    def remember(self, proposal, verdict):
        api = self.api
        records = api.load_decisions()
        previous = records.get(proposal.fingerprint)
        count = (previous.rejections if previous else 0) + (
            1 if verdict == "rejected" else 0
        )
        until = 0.0
        if verdict == "rejected":
            tier = min(count, len(api._COOLDOWN_DAYS)) - 1
            until = api.time.time() + api._COOLDOWN_DAYS[tier] * 86400
        replacement = api.Decision(
            fingerprint=proposal.fingerprint,
            verdict=verdict,
            kind=proposal.kind,
            title=proposal.title,
            decided_at=api._now(),
            rejections=count,
            cooldown_until=until,
        )
        records[proposal.fingerprint] = replacement
        api.save_decisions(records)

    def refusal(self, fingerprint, decisions):
        record = decisions.get(fingerprint)
        if record is None:
            return ""
        if record.verdict != "accepted":
            if record.cooldown_until and self.api.time.time() < record.cooldown_until:
                return f"rejected {record.rejections}x, cooling down"
            return "previously rejected"
        return "already accepted"


class ProposalIntake(QueueBindings):
    def admitted(self, request):
        api = self.api
        if not all((request["kind"], request["title"], request["body"])):
            return False
        try:
            api.Kind(request["kind"])
        except ValueError:
            api.logger.debug("unknown proposal kind %r", request["kind"])
            return False
        observations = request["occurrences"]
        if request["provenance"] != "human" and observations:
            if observations < max(1, request["min_evidence"]):
                api.logger.debug(
                    "proposal below evidence floor (%d < %d): %s",
                    observations,
                    request["min_evidence"],
                    request["title"],
                )
                return False
        return True

    def manifest(self, source, kind):
        api = self.api
        if isinstance(source, api.ChangeManifest):
            issues = source.issues()
            return asdict(source), issues
        if isinstance(source, dict) and source:
            try:
                issues = api.ChangeManifest(**source).issues()
            except (TypeError, ValueError):
                issues = ["malformed"]
            return source, issues
        missing = kind in (api.Kind.TEMPLATE_DIFF.value, api.Kind.SKILL.value)
        return {}, ["missing"] if missing else []

    def excerpt(self, raw, kind):
        if not raw:
            return ""
        try:
            from gideon.security.security import fence_untrusted

            return fence_untrusted(
                raw[: self.api._EXCERPT_MAX], source=f"{kind}-evidence"
            )
        except Exception:
            return ""

    def candidate(self, request, fingerprint):
        manifest, issues = self.manifest(request["change_manifest"], request["kind"])
        excerpt = self.excerpt(request["source_excerpt"], request["kind"])
        timestamp = self.api._now()
        values = {
            name: request[name]
            for name in (
                "kind",
                "title",
                "body",
                "target",
                "provenance",
                "source_cadence",
                "session_key",
                "run_id",
            )
        }
        values.update(
            id="",
            fingerprint=fingerprint,
            created_at=timestamp,
            updated_at=timestamp,
            source_excerpt=excerpt,
            evidence_refs=list(request["evidence_refs"] or []),
            staging_refs=list(request["staging_refs"] or []),
            change_manifest=manifest,
            manifest_valid=not issues,
            manifest_issues=issues,
            evidence_strength=request["evidence_strength"],
            confidence=float(request["confidence"]),
            reinforcements=max(1, int(request["occurrences"] or 1)),
            tags=list(request["tags"] or []),
        )
        return self.api.Proposal(**values)

    def reinforce(self, current, proposed):
        current.reinforcements += 1
        current.updated_at = proposed.updated_at
        current.tags = sorted(set(current.tags).union(proposed.tags))
        if proposed.provenance == "human":
            current.provenance = proposed.provenance
        self.api._save(current)
        return self.api.Verdict.REINFORCE, current

    def link(self, verdict, current, proposed):
        api = self.api
        if current is None:
            return
        if verdict is api.Verdict.MERGE:
            proposed.specializes = current.id
        elif verdict is api.Verdict.REPLACE:
            proposed.supersedes = current.id
            current.status, current.updated_at = (
                api.Status.SUPERSEDED.value,
                proposed.updated_at,
            )
            api._save(current)
            api._resolve_inbox_item(current.id, "dismissed")

    def enqueue(self, request):
        api = self.api
        skipped = (api.Verdict.SKIP, None)
        if not self.admitted(request):
            return skipped
        fingerprint = api.content_fingerprint(
            request["kind"], request["target"], request["body"]
        )
        decisions = api.load_decisions()
        reason = api._prior_decision_blocks(fingerprint, decisions)
        if reason:
            api.logger.info("skipping re-file of %r (%s)", request["title"], reason)
            return skipped
        proposed = self.candidate(request, fingerprint)
        records = api._all()
        verdict, current = api.resolve(proposed, records)
        if verdict is api.Verdict.REINFORCE and current is not None:
            return self.reinforce(current, proposed)
        proposed.id = f"{request['kind']}-{fingerprint[:12]}"
        self.link(verdict, current, proposed)
        if len(records) >= api.MAX_PENDING:
            api._expire_oldest(records)
        if not api._save(proposed):
            return skipped
        api.logger.info(
            "Queued %s proposal %s (%s)", request["kind"], proposed.id, verdict.value
        )
        api._surface_in_inbox(proposed)
        if verdict is api.Verdict.REPLACE:
            api.prune_superseded()
        return verdict, proposed


class HumanDecision(QueueBindings):
    def finalize(self, proposal, identifier, verdict):
        api = self.api
        proposal.status = {
            "accepted": api.Status.ACCEPTED.value,
            "rejected": api.Status.REJECTED.value,
        }[verdict]
        proposal.updated_at = api._now()
        api.record_decision(proposal, verdict)
        if verdict == "accepted":
            try:
                from gideon.cognition.learning import attribution

                attribution.record_accepted_change(proposal)
            except Exception:
                api.logger.debug(
                    "attribution record failed for %s", identifier, exc_info=True
                )
        ProposalFiles().unlink(identifier, "proposal delete failed")
        inbox_status, operation, outcome, message = {
            "accepted": (
                "handled",
                "learning_proposal_accept",
                "completed",
                "Accepted %s proposal %s",
            ),
            "rejected": (
                "dismissed",
                "learning_proposal_reject",
                "rejected",
                "Rejected %s proposal %s",
            ),
        }[verdict]
        api._resolve_inbox_item(identifier, inbox_status)
        api._audit(operation, proposal, outcome)
        api.logger.info(message, proposal.kind, identifier)

    def reject(self, identifier, actor):
        from gideon.cognition.learning.inbox import require_human

        api = self.api
        proposal = api._load(identifier)
        if proposal is None:
            return False
        permission = require_human(action="reject", actor=actor, status=proposal.status)
        if permission.allowed:
            self.finalize(proposal, identifier, "rejected")
            return True
        api._audit("learning_proposal_reject", proposal, "blocked")
        api.logger.warning(
            "Blocked %s reject of %s: %s", actor, identifier, permission.reason
        )
        return False

    def accept(self, identifier, installer, actor):
        from gideon.cognition.learning.inbox import audit_denial, require_human

        api = self.api
        proposal = api._load(identifier)
        if proposal is None:
            raise api.AcceptError(f"no proposal {identifier!r}")
        permission = require_human(action="accept", actor=actor, status=proposal.status)
        if not permission.allowed:
            row = audit_denial(
                action="accept", actor=actor, pid=identifier, gate=permission
            )
            api._audit("learning_proposal_accept", proposal, "blocked")
            api.logger.warning(
                "Blocked %s accept of %s: %s", actor, identifier, row["reason"]
            )
            raise api.AcceptError(permission.reason)
        if installer is not None:
            try:
                installer(proposal)
            except Exception as failure:
                api._audit("learning_proposal_accept", proposal, "failed")
                raise api.AcceptError(
                    f"install failed for {identifier!r}: {failure}"
                ) from failure
        self.finalize(proposal, identifier, "accepted")
        return proposal

    def defer(self, identifier):
        proposal = self.api._load(identifier)
        if proposal is None:
            return False
        proposal.status = self.api.Status.DRAFT.value
        setattr(proposal, "updated_at", self.api._now())
        return self.api._save(proposal)


def available_quota(filed, quota, fallback):
    ceiling = quota
    if ceiling is None:
        try:
            from gideon.core.config.loader import AppConfig

            learning = AppConfig.load().learning
            configured = int(getattr(learning, "propose_quota_per_run", 0) or 0)
        except Exception:
            configured = 0
        ceiling = configured if configured > 0 else fallback
    return max(0, ceiling - max(0, filed))
