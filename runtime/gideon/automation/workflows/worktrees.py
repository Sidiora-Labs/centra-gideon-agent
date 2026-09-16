"""Code-kind run worktrees: preserve, setup, resume, teardown, reintegration (§4.1 — S52).

S49 declared the `workspace` block and returned an ordered plan; this module performs the code-kind
half of it on the machinery that already exists. `loop/worktree.py` is proven —
`.worktrees/<id>` under
the project's own dir, `gideon/task-*` branches, idempotent `add_worktree`, `merge_worktree` with a
typed result — so nothing here re-implements git.

Measured against a real repo before writing any of it:

* `add_worktree` on an existing id returns the SAME path rather than failing, which is what makes
  resume free.
* An untracked `.env` is genuinely ABSENT from a fresh worktree. That is why `preserve_patterns` is
  the adoption-critical detail and not a nicety: a worktree where every build fails reads
  to a user as
  "isolation is broken".

Four asymmetries, each chosen:

* **Setup failure does not block the run; teardown failure does not block deletion.** Setup is
  convenience — refusing to run because `npm install` failed makes the block a liability. Teardown
  runs BEFORE deletion because its job is to stop services and sync work out while the
  directory still
  exists.
* **Work is committed before the worktree is removed.** An ephemeral workspace whose run
record points
  at a deleted directory has lost the work; a per-run branch survives, so the record references git.
* **Preserve copies IN, never OUT.** A pattern that copied a worktree file back over the user's real
  tree would make an isolated run able to modify the thing it was isolated from.
* **Reintegration is offered, never performed.** `Apply Locally` and `Checkout Branch` are the two
  verbs a user picks between; a run that auto-merged would decide for them, and the decision is the
  whole reason the work was isolated.

Pure planning + explicit performers. Every function that touches the filesystem takes the paths
explicitly, so the ordering rules are testable in a scratch repo.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from gideon.automation.workflows.workspace import setup_marker  # noqa: E402
from gideon.automation.workflows.workspace import SETUP_MARKER_DIR

RUN_BRANCH_PREFIX = "gideon/run-"

PRESERVE_DENYLIST = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}
)

MAX_PRESERVE_BYTES = 2 * 1024 * 1024


class Reintegration(str, Enum):
    """The two verbs a user picks between after a code run.

    `APPLY_LOCALLY` puts the diff in the working tree so the user reviews and commits it themselves;
    `CHECKOUT_BRANCH` switches to the run's branch so they own the history. Both are offered because
    they suit different situations, and picking for the user is what "review before it lands" exists
    to prevent.
    """

    APPLY_LOCALLY = "apply_locally"
    CHECKOUT_BRANCH = "checkout_branch"


@dataclass
class PreserveResult:
    """What a preserve pass actually copied, and what it refused.

    `skipped` is returned rather than logged because a user whose build fails needs to know their
    `.env` was skipped for being 4MB — a silent skip makes the isolation look broken for a reason
    nothing reports.
    """

    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"copied": list(self.copied), "skipped": list(self.skipped)}


def preserve(
    source: str | Path, target: str | Path, patterns: list[str]
) -> PreserveResult:
    return _PreserveTransfer(Path(source), Path(target)).copy(patterns)


@dataclass
class SetupResult:
    """The outcome of a setup pass. `blocked_run` is always False, by contract.

    Setup is best-effort convenience: refusing to run the workflow because `npm install`
    failed would
    make declaring setup a liability, and a user would stop declaring it. The failures are
    RECORDED so
    a run whose first stage fails for a missing dependency has an explanation in reach.
    """

    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def blocked_run(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": list(self.ran),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "blocked_run": self.blocked_run,
        }


def setup_steps(setup: str) -> list[str]:
    """Split a setup block into steps.

    Newlines only. Splitting on `&&` or `;` would shred a single shell command that legitimately
    chains, and each step is marker-guarded individually — so getting the boundaries wrong means
    either re-running a chain or marking half of it done.
    """
    return [line.strip() for line in (setup or "").splitlines() if line.strip()]


def pending_setup(worktree: str | Path, setup: str) -> tuple[list[str], list[str]]:
    return _SetupJournal(worktree).partition(setup)


def mark_setup_done(worktree: str | Path, step: str) -> bool:
    return _SetupJournal(worktree).record(step)


@dataclass
class TeardownPlan:
    """The ordered teardown, with deletion LAST.

    The order is the contract, not a preference: teardown stops services and syncs work
    out, and both
    need the directory to still exist. A plan that deleted first would run its own teardown against
    nothing and report success.
    """

    steps: list[str] = field(default_factory=list)
    commits_first: bool = True
    deletes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "commits_first": self.commits_first,
            "deletes": self.deletes,
        }


def plan_teardown(
    *, teardown: str = "", ephemeral: bool = True, keep_open: bool = False
) -> TeardownPlan:
    commands = [f"teardown: {step}" for step in setup_steps(teardown)]
    commands.extend(
        ["commit outstanding work to the per-run branch"] if ephemeral else []
    )
    commands.append(
        "KEEP the workspace (keep_open: the workspace is the deliverable)"
        if keep_open
        else "remove the worktree"
    )
    return TeardownPlan(steps=commands, commits_first=ephemeral, deletes=not keep_open)


def run_branch(run_id: str) -> str:
    characters = list(str(run_id or ""))
    for index, character in enumerate(characters):
        if not character.isalnum() and character not in "-_.":
            characters[index] = "-"
    return RUN_BRANCH_PREFIX + ("".join(characters) or "unknown")


@dataclass
class DiffEntry:
    """One changed file in the run's worktree.

    `staged` and `status` are separate because the cockpit's stage/discard affordances need both: a
    file can be modified-and-staged, modified-and-not, or untracked, and collapsing them would make
    "discard" ambiguous about what it discards.
    """

    path: str
    status: str
    staged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "status": self.status, "staged": self.staged}


_STATUS_WORDS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "?": "untracked",
    "U": "conflicted",
    "!": "ignored",
}


INFRASTRUCTURE_PATHS = (SETUP_MARKER_DIR,)


def is_infrastructure(path: str, preserved: list[str] | None = None) -> bool:
    return _ReviewPaths(preserved).excluded(path)


def parse_status(porcelain: str) -> list[DiffEntry]:
    rows = (_PorcelainRecord.read(line) for line in (porcelain or "").splitlines())
    return [row.entry() for row in rows if row is not None]


def reintegration_offer(
    run_id: str,
    *,
    branch: str = "",
    changed: int = 0,
    conflicts: list[str] | None = None,
) -> dict[str, Any]:
    collisions = list(conflicts or [])
    selected_branch = branch or run_branch(run_id)
    offer = _ReintegrationMenu(selected_branch, collisions)
    return {
        "run_id": run_id,
        "branch": selected_branch,
        "changed_files": changed,
        "conflicts": collisions,
        "verbs": offer.verbs(),
        "note": offer.note(),
    }


@dataclass
class WorktreeState:
    """Everything the cockpit and the boot sweep need about one run's worktree.

    `alive` feeds S46's substrate check directly: an isolated substrate that survived a
    restart makes
    the run SUSPENDED and resumable rather than a zombie, and getting that backwards destroys
    recoverable work while reporting success.
    """

    run_id: str
    path: str = ""
    branch: str = ""
    alive: bool = False
    dirty: bool = False
    changed: list[DiffEntry] = field(default_factory=list)

    @property
    def preserved_workspace_path(self) -> str:
        """The path to surface on the run record when a dirty workspace is kept.

        Empty for a clean workspace: pointing a user at a directory with nothing in it is a
        false lead, and the run record would carry a path that means nothing.
        """
        return self.path if (self.alive and self.dirty) else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": self.path,
            "branch": self.branch,
            "alive": self.alive,
            "dirty": self.dirty,
            "changed": [c.to_dict() for c in self.changed],
            "preserved_workspace_path": self.preserved_workspace_path,
        }


def inspect_worktree(
    run_id: str,
    path: str | Path,
    porcelain: str = "",
    preserved: list[str] | None = None,
) -> WorktreeState:
    root = Path(path)
    alive = bool(str(path).strip()) and root.is_dir()
    state = WorktreeState(
        run_id=run_id,
        path=str(root) if alive else "",
        branch=run_branch(run_id),
        alive=alive,
    )
    if alive:
        paths = _ReviewPaths(preserved)
        state.changed = list(
            filter(
                lambda change: not paths.excluded(change.path), parse_status(porcelain)
            )
        )
        state.dirty = bool(state.changed)
    return state


def substrate_for(state: WorktreeState) -> Any:
    """The S46 `Substrate` for this worktree, so the boot sweep has ONE source of truth.

    Built here rather than re-derived in the sweep: the sweep's whole decision turns on whether an
    isolated substrate is alive, and two places computing that would eventually disagree —
    with the
    disagreement showing up as a run aborted despite having recoverable work.
    """
    from gideon.automation.workflows.containers import Substrate

    return Substrate(
        kind="worktree",
        alive=state.alive,
        detail=state.path or "worktree is gone",
    )


def resume_safe(worktree: str | Path, setup: str) -> tuple[bool, str]:
    root = Path(worktree)
    if not root.is_dir():
        return False, "the worktree is gone — nothing to resume into"
    pending, completed = pending_setup(root, setup)
    if not pending:
        return True, "resumable; setup already complete"
    progress = f"{len(completed)} setup step(s) already done, " if completed else ""
    remaining = (
        f"{len(pending)} to run"
        if completed
        else f"{len(pending)} setup step(s) to run"
    )
    return True, f"resumable; {progress}{remaining}"


def worktree_env(worktree: str | Path) -> dict[str, str]:
    """Env additions for a stage running in a worktree.

    `PWD` is set alongside `cwd` because some tools read it rather than calling `getcwd`,
    and a stale
    `PWD` makes a build resolve relative paths against the user's real tree — which is
    precisely the
    isolation failure the worktree exists to prevent.
    """
    root = str(Path(worktree))
    return {"PWD": root, "GIDEON_RUN_WORKTREE": root}


def cleanup_markers(worktree: str | Path) -> int:
    return _SetupJournal(worktree).clear()


class _PreserveTransfer:
    def __init__(self, source: Path, destination: Path):
        self.source, self.destination = source, destination
        self.result = PreserveResult()

    def copy(self, patterns: list[str]) -> PreserveResult:
        if self.source.is_dir() and self.destination.is_dir():
            for pattern in patterns or []:
                for candidate in sorted(self.source.glob(pattern)):
                    self.transfer(candidate)
        return self.result

    @staticmethod
    def exclusion(candidate: Path, relative: Path) -> str:
        if not PRESERVE_DENYLIST.isdisjoint(relative.parts):
            return "denylisted"
        if candidate.is_dir():
            return "directories are not preserved"
        try:
            size = candidate.stat().st_size
        except OSError:
            return "unreadable"
        return (
            f"{size} bytes exceeds the preserve cap"
            if size > MAX_PRESERVE_BYTES
            else ""
        )

    def transfer(self, candidate: Path) -> None:
        relative = candidate.relative_to(self.source)
        refusal = self.exclusion(candidate, relative)
        if refusal:
            self.result.skipped.append(f"{relative}: {refusal}")
            return
        destination = self.destination / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, destination)
        except OSError as error:
            self.result.skipped.append(f"{relative}: {error}")
        else:
            self.result.copied.append(str(relative))


class _SetupJournal:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def partition(self, declaration: str) -> tuple[list[str], list[str]]:
        buckets: tuple[list[str], list[str]] = ([], [])
        for command in setup_steps(declaration):
            complete = (self.root / setup_marker(command)).exists()
            buckets[int(complete)].append(command)
        return buckets

    def record(self, command: str) -> bool:
        marker = self.root / setup_marker(command)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("done\n", encoding="utf-8")
        except OSError:
            logger.debug(
                "could not write setup marker for %r", command[:60], exc_info=True
            )
            return False
        return True

    def clear(self) -> int:
        directory = self.root / SETUP_MARKER_DIR
        if not directory.is_dir():
            return 0
        return sum(self.remove(marker) for marker in directory.glob("*.done"))

    @staticmethod
    def remove(marker: Path) -> bool:
        try:
            os.unlink(marker)
        except OSError:
            logger.debug("could not remove setup marker %s", marker, exc_info=True)
            return False
        return True


class _ReviewPaths:
    def __init__(self, preserved: list[str] | None):
        self.preserved = preserved

    @staticmethod
    def normalized(path: str) -> str:
        return path.strip().lstrip("./").rstrip("/")

    def excluded(self, path: str) -> bool:
        selected = self.normalized(path or "")
        if not selected:
            return False
        infrastructure = (self.normalized(marker) for marker in INFRASTRUCTURE_PATHS)
        if any(
            selected == prefix or selected.startswith(prefix + "/")
            for prefix in infrastructure
        ):
            return True
        return selected in {
            self.normalized(str(path)) for path in (self.preserved or [])
        }


@dataclass
class _PorcelainRecord:
    index: str
    tree: str
    path: str

    @classmethod
    def read(cls, line: str) -> _PorcelainRecord | None:
        if len(line) < 4:
            return None
        filename = line[3:].strip()
        if not filename:
            return None
        source, arrow, destination = filename.partition(" -> ")
        return cls(line[0], line[1], destination.strip() if arrow else source)

    def entry(self) -> DiffEntry:
        staged = self.index not in (" ", "?")
        status = self.index if staged else self.tree
        return DiffEntry(
            self.path, _STATUS_WORDS.get(status, status.strip() or "unknown"), staged
        )


class _ReintegrationMenu:
    def __init__(self, branch: str, conflicts: list[str]):
        self.branch, self.conflicts = branch, conflicts

    def verbs(self) -> list[dict[str, Any]]:
        choices = (
            (
                Reintegration.APPLY_LOCALLY,
                "Apply Locally",
                "put the diff in your working tree — you review and commit it",
                not self.conflicts,
            ),
            (
                Reintegration.CHECKOUT_BRANCH,
                "Checkout Branch Locally",
                f"switch to {self.branch} — you own the history",
                True,
            ),
        )
        return [
            dict(verb=verb.value, label=label, detail=detail, safe=safe)
            for verb, label, detail, safe in choices
        ]

    def note(self) -> str:
        if self.conflicts:
            return f"{len(self.conflicts)} file(s) conflict with your working tree — checkout is the safer verb."
        return "Nothing is applied automatically. Reviewing before it lands is why the run was isolated."
