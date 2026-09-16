"""Compare observed model quality with the current routing decision."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.engine.routing.proposals import RoutingProposal

logger = logging.getLogger(__name__)
_AUDIT_TAIL = 2000


def _cell_opinions(rows: dict[str, Any], *, min_samples: int) -> dict[str, float]:
    from gideon.engine.routing.learned import _opinion

    measured = (
        (str(ref), _opinion(row, min_samples=min_samples)) for ref, row in rows.items()
    )
    return {ref: score for ref, score in measured if score is not None}


def _sample_ids(
    audit_rows: list[dict[str, Any]],
    use_case: str,
    query_class: str,
    refs: tuple[str, ...],
) -> list[str]:
    selected = (
        row
        for row in reversed(audit_rows)
        if (row.get("use_case"), row.get("query_class")) == (use_case, query_class)
        and f"{row.get('provider', '')}:{row.get('model', '')}" in refs
    )
    return [
        identifier
        for row in selected
        if (identifier := str(row.get("audit_id", "") or ""))
    ]


@dataclass(frozen=True)
class ComparisonEvidence:
    rows: dict
    audit: list
    use_case: str
    query_class: str

    @classmethod
    def read(cls, stats, use_case, query_class):
        from gideon.engine.routing.telemetry import telemetry_rows

        audit = []
        try:
            from gideon.security.guardrails.audit import read_recent

            audit = read_recent(limit=_AUDIT_TAIL)
        except Exception:
            logger.debug(
                "routing proposal evidence: audit tail unreadable", exc_info=True
            )
        rows = {
            str(row["ref"]): row
            for row in telemetry_rows(stats, audit, use_case, query_class)
        }
        return cls(rows, audit, use_case, query_class)

    def delta(self, field, promoted, demoted, precision):
        values = [
            float(self.rows.get(ref, {}).get(field, 0.0) or 0.0)
            for ref in (promoted, demoted)
        ]
        return round(values[0] - values[1], precision)

    def document(self, opinions, knobs, current, proposed):
        promoted, demoted = proposed[0], current[0]
        values = {
            "n": {
                ref: int(self.rows.get(ref, {}).get("n", 0)) for ref in sorted(opinions)
            },
            "scores": {ref: round(opinions[ref], 4) for ref in sorted(opinions)},
            "min_samples": int(knobs["min_samples"]),
            "hysteresis": float(knobs["hysteresis"]),
            "cloud_quality_margin": float(knobs["cloud_quality_margin"]),
            "p50_delta_ms": self.delta("p50_ms", promoted, demoted, 1),
            "cost_delta_usd": self.delta("avg_cost_usd", promoted, demoted, 6),
            "sample_audit_ids": _sample_ids(
                self.audit, self.use_case, self.query_class, (promoted, demoted)
            ),
        }
        return values


def _evidence(
    stats: dict[str, Any],
    use_case: str,
    query_class: str,
    *,
    opinions: dict[str, float],
    knobs: dict[str, Any],
    current: list[str],
    proposed: list[str],
) -> dict[str, Any]:
    return ComparisonEvidence.read(stats, use_case, query_class).document(
        opinions, knobs, current, proposed
    )


@dataclass(frozen=True)
class RoutingComparison:
    stats: dict
    use_case: str
    query_class: str
    home: Path

    def propose(self):
        from gideon.engine.routing import learned, policy, proposals

        rows = learned._rows_for(self.stats, self.use_case, self.query_class)
        if len(rows) < 2:
            return None
        knobs = policy._routing_knobs()
        opinions = _cell_opinions(rows, min_samples=int(knobs["min_samples"]))
        scores = sorted(opinions.values())
        if len(scores) < 2 or scores[-1] - scores[0] <= float(knobs["hysteresis"]):
            return None
        waiting = (
            (item.use_case, item.query_class)
            for item in proposals.pending(home=self.home)
        )
        if (self.use_case, self.query_class) in waiting:
            return None
        if not policy.routing_active(self.use_case, home=self.home) or policy.pin_for(
            self.use_case, home=self.home
        ):
            return None
        current = policy.route_refs(
            self.use_case, self.query_class, sorted(rows), home=self.home
        )
        proposed = policy._learned_order(
            current,
            self.use_case,
            self.query_class,
            policy._local_provider_keys(),
            home=self.home,
        )
        if current == proposed:
            return None
        evidence = _evidence(
            self.stats,
            self.use_case,
            self.query_class,
            opinions=opinions,
            knobs=knobs,
            current=current,
            proposed=proposed,
        )
        return proposals.propose(
            use_case=self.use_case,
            query_class=self.query_class,
            current=current,
            proposed=proposed,
            evidence=evidence,
            home=self.home,
        )


def detect_gap(
    stats: dict[str, Any], use_case: str, query_class: str, *, home: Path
) -> "RoutingProposal | None":
    return RoutingComparison(stats, use_case, query_class, home).propose()
