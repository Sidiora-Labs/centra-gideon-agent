"""Durable routing proposals, evidence and operator decisions."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.engine.routing import policy

logger = logging.getLogger(__name__)
_QUEUE_FILE = "routing_proposals.json"
QUEUE_VERSION = 1
ROUTING_PROPOSAL_KIND = "routing"
_MAX_PENDING = 50
_DEFAULT_COOLDOWN_DAYS = 14
_EVIDENCE_TEXT_MAX = 1000
_MAX_SAMPLE_IDS = 20
_ID_LIST_KEYS = frozenset({"sample_audit_ids"})


@dataclass
class RoutingProposal:
    id: str
    use_case: str
    query_class: str
    current: list[str]
    proposed: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    kind: str = ROUTING_PROPOSAL_KIND
    status: str = "pending"
    decided_at: str = ""
    refusal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        keys = (
            "id",
            "use_case",
            "query_class",
            "current",
            "proposed",
            "created_at",
            "status",
        )
        return {
            key: list(value) if key in ("current", "proposed") else value
            for key in keys
            for value in (getattr(self, key),)
        }


def _queue_path(home: Path) -> Path:
    return Path(home).joinpath(_QUEUE_FILE)


def _empty_queue() -> dict[str, Any]:
    return dict(version=QUEUE_VERSION, proposals=[], rejections={})


def _default_home() -> Path | None:
    try:
        from gideon.core.config import config_dir

        return Path(config_dir())
    except Exception:
        return None


def _resolve_home(home: Path | None) -> Path | None:
    return _default_home() if home is None else Path(home)


def load_queue(home: Path | None = None) -> dict[str, Any]:
    directory = _resolve_home(home)
    try:
        data = (
            json.loads(_queue_path(directory).read_text(encoding="utf-8"))
            if directory is not None
            else None
        )
    except (OSError, ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        return _empty_queue()
    data.setdefault("version", QUEUE_VERSION)
    for key, kind in (("proposals", list), ("rejections", dict)):
        if not isinstance(data.get(key), kind):
            data[key] = kind()
    return data


def _save_queue(home: Path, queue: dict[str, Any]) -> bool:
    try:
        text = json.dumps(queue, indent=2, sort_keys=True)
        atomic_write(_queue_path(home), text + "\n")
    except OSError:
        logger.debug("routing proposal queue write failed", exc_info=True)
        return False
    return True


def _record(raw: object) -> RoutingProposal | None:
    if isinstance(raw, dict):
        try:
            record = RoutingProposal(**raw)
        except TypeError:
            logger.debug("unreadable routing proposal record dropped", exc_info=True)
            return None
        if all(isinstance(order, list) for order in (record.current, record.proposed)):
            if not isinstance(record.evidence, dict):
                record.evidence = {}
            return record
    return None


def _records(queue: dict[str, Any]) -> list[RoutingProposal]:
    return [
        record
        for raw in queue.get("proposals", [])
        if (record := _record(raw)) is not None
    ]


def suppression_key(use_case: str, query_class: str, proposed: list[str]) -> str:
    return "|".join(
        (str(use_case), str(query_class), str(proposed[0]) if proposed else "")
    )


def _cooldown_days() -> int:
    try:
        from gideon.core.config import AppConfig

        value = AppConfig.load().routing.reproposal_cooldown_days
        return max(0, int(value))
    except Exception:
        return _DEFAULT_COOLDOWN_DAYS


def _parse_ts(value: object) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if not parsed.tzinfo else parsed
    return None


def _in_cooldown(queue: dict[str, Any], key: str, now: datetime) -> bool:
    days = _cooldown_days()
    if days > 0:
        stamp = _parse_ts(queue.get("rejections", {}).get(key))
        return stamp is not None and now < stamp + timedelta(days=days)
    return False


def _fence(text: str) -> str:
    try:
        from gideon.security.security import fence_untrusted

        bounded = text[:_EVIDENCE_TEXT_MAX]
        return fence_untrusted(bounded, source="routing-telemetry")
    except Exception:
        return ""


@dataclass(frozen=True)
class EvidenceValue:
    key: str
    depth: int = 0

    def clean(self, value):
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return (
                value[:_EVIDENCE_TEXT_MAX]
                if self.key in _ID_LIST_KEYS
                else _fence(value)
            )
        if isinstance(value, (list, tuple)):
            items = list(value)
            if self.key in _ID_LIST_KEYS:
                items = items[:_MAX_SAMPLE_IDS]
            child = EvidenceValue(self.key, self.depth + 1)
            return [child.clean(item) for item in items]
        if isinstance(value, dict) and self.depth < 3:
            return {
                str(key)[:200]: EvidenceValue(str(key), self.depth + 1).clean(item)
                for key, item in value.items()
            }
        return None


def _clean_value(key: str, value: object, *, depth: int = 0) -> object:
    return EvidenceValue(key, depth).clean(value)


def _clean_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(evidence, dict):
        return {
            str(key)[:200]: _clean_value(str(key), value)
            for key, value in evidence.items()
        }
    return {}


def _make_id(
    use_case: str, query_class: str, proposed: list[str], created_at: str
) -> str:
    body = "|".join(
        (str(use_case), str(query_class), ",".join(proposed), str(created_at))
    )
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
    return f"rp-{digest[:12]}"


def _now(now: str = "") -> str:
    return now if now else datetime.now(timezone.utc).isoformat()


@dataclass
class ProposalQueue:
    home: Path
    document: dict

    def admits(self, key, stamp):
        moment = _parse_ts(stamp) or datetime.now(timezone.utc)
        if _in_cooldown(self.document, key, moment):
            logger.debug("routing proposal suppressed by cooldown: %s", key)
            return False
        waiting = [
            record for record in _records(self.document) if record.status == "pending"
        ]
        if len(waiting) >= _MAX_PENDING:
            logger.info(
                "routing proposal queue full (%d); dropping %s", _MAX_PENDING, key
            )
            return False
        duplicate = any(
            suppression_key(record.use_case, record.query_class, record.proposed) == key
            for record in waiting
        )
        if duplicate:
            logger.debug("routing proposal already pending: %s", key)
        return not duplicate

    def append(self, record):
        self.document.setdefault("proposals", []).append(record.to_dict())
        return _save_queue(self.home, self.document)


def propose(
    *,
    use_case: str,
    query_class: str,
    current: list[str],
    proposed: list[str],
    evidence: dict[str, Any],
    home: Path | None = None,
    now: str = "",
) -> RoutingProposal | None:
    directory = _resolve_home(home)
    if directory is None:
        return None
    proposed, current = list(map(str, proposed or [])), list(map(str, current or []))
    if not (use_case and query_class and proposed) or proposed == current:
        return None
    queue = ProposalQueue(directory, load_queue(directory))
    key = suppression_key(use_case, query_class, proposed)
    stamp = _now(now)
    if not queue.admits(key, stamp):
        return None
    record = RoutingProposal(
        _make_id(use_case, query_class, proposed, stamp),
        use_case,
        query_class,
        current,
        proposed,
        evidence=_clean_evidence(evidence),
        created_at=stamp,
    )
    if not queue.append(record):
        return None
    logger.info("Queued routing proposal %s (%s)", record.id, key)
    _notify(record)
    return record


def pending(*, home: Path | None = None) -> list[RoutingProposal]:
    waiting = (
        record for record in _records(load_queue(home)) if record.status == "pending"
    )
    return sorted(waiting, key=lambda record: (record.created_at, record.id))


def find(proposal_id: str, *, home: Path | None = None) -> RoutingProposal | None:
    return next(
        (record for record in _records(load_queue(home)) if record.id == proposal_id),
        None,
    )


def _find_pending(
    queue: dict[str, Any], proposal_id: str
) -> tuple[int, RoutingProposal] | None:
    parsed = (
        (index, _record(raw)) for index, raw in enumerate(queue.get("proposals", []))
    )
    return next(
        (
            (index, record)
            for index, record in parsed
            if record is not None
            and record.id == proposal_id
            and record.status == "pending"
        ),
        None,
    )


@dataclass
class ProposalDecision:
    queue: ProposalQueue
    index: int
    proposal: RoutingProposal
    stamp: str

    @classmethod
    def open(cls, identifier, home):
        directory = _resolve_home(home)
        if directory is None:
            return None
        queue = ProposalQueue(directory, load_queue(directory))
        found = _find_pending(queue.document, identifier)
        return cls(queue, *found, _now()) if found is not None else None

    def settle(self, status):
        self.proposal.status = status
        self.proposal.decided_at = self.stamp
        self.queue.document["proposals"][self.index] = self.proposal.to_dict()

    def save(self):
        return _save_queue(self.queue.home, self.queue.document)

    def accept(self):
        record = self.proposal
        basis = policy.order_basis(
            record.use_case, record.query_class, home=self.queue.home
        )
        if basis.get("source") == "user":
            record.refusal_reason = (
                "a hand-set order owns this cell; set it by hand to change it"
            )
            self.settle("refused")
            self.save()
            logger.info("routing proposal %s refused: user-set basis", record.id)
            return False
        policy.set_order(
            record.use_case,
            record.query_class,
            list(record.proposed),
            home=self.queue.home,
            basis={
                "source": "proposal",
                "proposal_id": record.id,
                "accepted_at": self.stamp,
            },
        )
        self.settle("accepted")
        self.save()
        _sel_decision(record, "accept")
        logger.info("Accepted routing proposal %s", record.id)
        return True

    def reject(self):
        record = self.proposal
        self.settle("rejected")
        if not isinstance(self.queue.document.get("rejections"), dict):
            self.queue.document["rejections"] = {}
        key = suppression_key(record.use_case, record.query_class, record.proposed)
        self.queue.document["rejections"][key] = self.stamp
        if not self.save():
            return False
        _sel_decision(record, "reject")
        logger.info("Rejected routing proposal %s", record.id)
        return True


def accept(proposal_id: str, *, home: Path | None = None) -> bool:
    decision = ProposalDecision.open(proposal_id, home)
    return decision.accept() if decision is not None else False


def reject(proposal_id: str, *, home: Path | None = None) -> bool:
    decision = ProposalDecision.open(proposal_id, home)
    return decision.reject() if decision is not None else False


def _sel_decision(prop: RoutingProposal, decision: str) -> None:
    try:
        from gideon.security.sel import sel

        resources = (
            f"{prop.id}:{prop.use_case}:{prop.query_class}:{','.join(prop.proposed)}"
        )
        fields = dict(
            caller="user",
            operation=f"routing.proposal.{decision}",
            outcome="success",
            source="routing_proposals",
            resources=resources,
        )
        sel().log_api_access(**fields)
    except Exception:
        logger.debug("routing proposal SEL record failed", exc_info=True)


def _notify(prop: RoutingProposal) -> None:
    try:
        from gideon.integrations.action_providers.services import get_action_services
        from gideon.workspace import notification_kinds

        state = getattr(get_action_services(), "state", None)
        if state is not None:
            message = f"{prop.use_case} / {prop.query_class}: try {prop.proposed[0]} first. Nothing changed — review the evidence and decide."
            metadata = dict(
                kind_detail=ROUTING_PROPOSAL_KIND,
                routing_proposal=prop.id,
                use_case=prop.use_case,
                query_class=prop.query_class,
            )
            state.notify(
                notification_kinds.INFO,
                "A routing change is proposed",
                message,
                meta=metadata,
            )
    except Exception:
        logger.debug("routing proposal notify failed", exc_info=True)
