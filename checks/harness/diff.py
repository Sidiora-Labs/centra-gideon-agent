"""Git diff introspection for diff-aware required-check selection (§1.4).

``run --diff`` computes which files a change touches (against the merge-base with the
default branch) and which profiles that forces, independent of what a task spec claims.
The spec author can ADD requirements; the diff can only add more, never remove — so a
change touching a sensitive area can't skip the profile that guards it by omitting it from
the task spec.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_FIX_SHAPED_RE = re.compile(r"\b(fix|bug|bugfix|regression|hotfix)\b", re.IGNORECASE)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def run_git(args: list[str], root: Path) -> tuple[int, str, str]:
    """One read-only git command, decoded so undecodable bytes cannot abort a check.

    ``errors="replace"`` because the two things git hands back here — a patch body and a
    path list — are both attacker/author-controlled byte strings: a source file holding one
    stray latin-1 byte makes ``text=True``'s strict decode raise ``UnicodeDecodeError``
    inside ``subprocess.run``, and the whole scan dies on a file it was only going to skim.
    A U+FFFD in a diff body costs nothing (the parser reads the leading ``+``/``@@`` markers,
    which are ASCII) and the line numbering is unaffected, since only the undecodable bytes
    are replaced and never the newlines around them.

    ``core.quotePath=false`` for the other half of the same promise: with git's default
    quoting a non-ASCII path comes back as the C-escaped literal ``"caf\\303\\251.py"``,
    which is not a path any caller can open or relate to the repo root. Off, the path
    arrives as its real (decoded) bytes and stays usable.
    """
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except OSError as exc:
        return -1, "", str(exc)


def merge_base(root: Path, base_ref: str = "origin/main") -> str:
    """The merge-base sha between HEAD and ``base_ref`` (falls back to ``base_ref`` itself,
    then to ``HEAD~1``) so ``--diff`` works whether or not the remote ref is present."""
    for ref in (base_ref, "main", "HEAD~1"):
        rc, out, _ = run_git(["merge-base", "HEAD", ref], root)
        if rc == 0 and out.strip():
            return out.strip()
    return "HEAD"


@dataclass
class Diff:
    """A parsed diff: the set of changed files (repo-relative) and, per file, the set of
    line numbers added/changed on the NEW side (for line-scoped heuristic checks)."""

    base: str
    files: list[str] = field(default_factory=list)
    changed_lines: dict[str, set[int]] = field(default_factory=dict)

    def abs_files(self, root: Path) -> list[Path]:
        return [root / f for f in self.files]

    def abs_changed_lines(self, root: Path) -> dict[Path, set[int]]:
        return {root / f: lines for f, lines in self.changed_lines.items()}


def compute_diff(root: Path | None = None, base_ref: str = "origin/main") -> Diff:
    """Compute the diff of HEAD (plus uncommitted changes) against the merge-base.

    Uses ``git diff <base>`` (not ``<base>..HEAD``) so uncommitted working-tree edits are
    included — the executor typically runs ``--diff`` before committing.
    """
    r = root if root is not None else _repo_root()
    base = merge_base(r, base_ref)

    rc, name_out, _ = run_git(["diff", "--name-only", base], r)
    files = (
        [ln.strip() for ln in name_out.splitlines() if ln.strip()] if rc == 0 else []
    )

    changed_lines: dict[str, set[int]] = {}
    rc, patch, _ = run_git(["diff", "--unified=0", base], r)
    if rc == 0:
        changed_lines = _parse_added_lines(patch)

    return Diff(base=base, files=files, changed_lines=changed_lines)


def commit_subjects_since(root: Path, base: str) -> list[str]:
    """Commit subjects from ``base`` to HEAD (exclusive of base). Empty when base==HEAD."""
    if base == "HEAD":
        return []
    rc, out, _ = run_git(["log", "--format=%s", f"{base}..HEAD"], root)
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def has_fix_shaped_commit(subjects: list[str]) -> bool:
    """True if any subject looks like a bug fix (fix/bug/regression/hotfix)."""
    return any(_FIX_SHAPED_RE.search(s) for s in subjects)


def touches_specs(files: list[str]) -> bool:
    """True if any changed file is a harness spec (so the same-PR rule is satisfied)."""
    return any(f.startswith("checks/harness/specs/") for f in files)


def _parse_added_lines(patch: str) -> dict[str, set[int]]:
    """Parse a unified=0 diff into {file: {new-side line numbers of added lines}}.

    Reads the ``+++ b/<file>`` headers and the ``@@ -a,b +c,d @@`` hunk headers; counts
    each ``+`` body line against the running new-side line number. Deletions don't advance
    the new-side counter (unified=0 has no context lines to worry about).
    """
    out: dict[str, set[int]] = {}
    cur_file: str | None = None
    new_line = 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            cur_file = (
                path[2:]
                if path.startswith("b/")
                else (None if path == "/dev/null" else path)
            )
            continue
        if line.startswith("@@"):
            # @@ -old,cnt +new,cnt @@
            try:
                plus = line.split("+", 1)[1]
                new_start = int(plus.split(",", 1)[0].split(" ", 1)[0])
            except (IndexError, ValueError):
                new_start = 0
            new_line = new_start
            continue
        if cur_file is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur_file, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            pass
    return out
