"""Verification ladder outcomes, confined artifact evidence and command adaptation."""

from __future__ import annotations

import fnmatch
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.automation.workflows.judge_contract import Verdict

logger = logging.getLogger(__name__)

LADDER_ORDER = ("static", "runtime", "system")


def parse_verdict(value: Any) -> Verdict | None:
    raw = value
    if isinstance(value, dict):
        raw = value.get("verdict") or value.get("result") or value.get("status")
    if raw is not None:
        text = str(raw).strip().upper()
        exact = next((verdict for verdict in Verdict if verdict.value == text), None)
        if exact is not None:
            return exact
        candidates = tuple(verdict for verdict in Verdict if verdict.value in text)
        if len(candidates) == 1:
            return candidates[0]
    return None


@dataclass
class CriterionResult:
    """One ladder criterion's outcome. `hard_fail` is what makes the no-averaging rule
    real — a hard failure ends the gate regardless of every other score."""

    criterion: str
    rung: str = "static"
    passed: bool = False
    score: float = 0.0
    hard_fail: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in ("criterion", "rung", "passed", "score", "hard_fail", "detail")
        }
        result["score"] = round(self.score, 4)
        return result


@dataclass
class LadderResult:
    results: list[CriterionResult] = field(default_factory=list)
    verdict: Verdict = Verdict.PASS
    stopped_at: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return dict(
            verdict=self.verdict.value,
            passed=self.passed,
            stopped_at=self.stopped_at,
            criteria=[result.to_dict() for result in self.results],
        )


def run_ladder(
    criteria: list[dict[str, Any]], evaluated: dict[str, Any]
) -> LadderResult:
    return _LadderPass(criteria).evaluate(evaluated)


def _score(raw: Any, threshold: Any) -> tuple[bool, float]:
    if raw is None:
        return False, 0.0
    if isinstance(raw, bool):
        return raw, float(raw)
    if isinstance(raw, (int, float)):
        value = float(raw)
        thresholded = isinstance(threshold, (int, float))
        return (value >= float(threshold) if thresholded else value > 0), value
    accepted = parse_verdict(raw) == Verdict.PASS
    return accepted, float(accepted)


@dataclass
class ArtifactCheck:
    satisfied: bool = True
    missing: list[str] = field(default_factory=list)
    digests: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            satisfied=self.satisfied,
            **{name: list(getattr(self, name)) for name in ("missing", "digests")},
        )


def check_required_artifacts(patterns: list[str], workspace: Path) -> ArtifactCheck:
    check = ArtifactCheck()
    if patterns:
        try:
            root = workspace.resolve()
        except OSError:
            return ArtifactCheck(satisfied=False, missing=list(patterns))
        for pattern in patterns:
            matches = _safe_glob(root, str(pattern))
            if matches:
                check.digests.extend(_digest(path, root) for path in matches)
            else:
                check.missing.append(str(pattern))
        check.satisfied = not check.missing
    return check


def _safe_glob(root: Path, pattern: str) -> list[Path]:
    if pattern.startswith("/") or ".." in pattern:
        logger.warning("ignoring artifact pattern outside the workspace: %s", pattern)
        return []
    accepted = []
    try:
        accepted.extend(_ArtifactSelection(root, pattern).paths())
    except OSError:
        logger.debug("artifact glob failed for %s", pattern, exc_info=True)
    return accepted


def _digest(path: Path, root: Path) -> dict[str, Any]:
    try:
        content = path.read_bytes()
        relative = path.relative_to(root).as_posix()
    except OSError:
        return dict(path=str(path), size=0, sha256="")
    return dict(
        path=relative,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest()[:16],
    )


def judge_session_key(run_id: str, node_path: str, *, epoch: int = 0) -> str:
    """A session key distinct from any producing node's."""
    return f"judge:{run_id}:{node_path}:{epoch}"


def requires_fresh_judge(node_config: dict[str, Any]) -> bool:
    """True unless the author explicitly opted into self-judging."""
    return not bool((node_config or {}).get("self_judge", False))


async def run_verify_block(
    block: dict[str, Any], *, default_cwd: str = ""
) -> bool | None:
    from gideon.automation.loop.gates import run_verify_command

    command = str(block.get("command") or block.get("script") or "").strip()
    if command:
        parameters = {
            name: str(block.get(name) or fallback or "").strip()
            for name, fallback in (("cwd", default_cwd), ("label", "verify"))
        }
        return await run_verify_command(
            command, parameters["cwd"] or None, label=parameters["label"] or "verify"
        )
    return None


class _LadderPass:
    def __init__(self, criteria: list[dict[str, Any]]):
        self.rungs: dict[str, list[dict[str, Any]]] = {
            rung: [] for rung in LADDER_ORDER
        }
        for criterion in criteria:
            declared = str(criterion.get("rung", "static") or "static")
            destination = declared if declared in self.rungs else "static"
            self.rungs.setdefault(destination, []).append(criterion)

    def evaluate(self, outcomes: dict[str, Any]) -> LadderResult:
        result = LadderResult()
        for rung in LADDER_ORDER:
            for criterion in self.rungs.get(rung, []):
                name = str(criterion.get("name", "") or "criterion")
                observed = outcomes.get(name)
                threshold = criterion.get("threshold")
                hard = bool(criterion.get("hard", True))
                passed, score = _score(observed, threshold)
                item = CriterionResult(
                    name,
                    rung,
                    passed,
                    score,
                    hard and not passed,
                    "" if passed else f"{name} did not meet its threshold",
                )
                result.results.append(item)
                if item.hard_fail:
                    result.verdict, result.stopped_at = Verdict.REJECT, rung
                    return result
        return result


@dataclass(frozen=True)
class _ArtifactSelection:
    root: Path
    pattern: str

    def paths(self):
        for candidate in self.root.rglob("*"):
            if candidate.is_file():
                names = (candidate.relative_to(self.root).as_posix(), candidate.name)
                if any(fnmatch.fnmatch(name, self.pattern) for name in names):
                    resolved = candidate.resolve()
                    if self.root in resolved.parents:
                        yield resolved
