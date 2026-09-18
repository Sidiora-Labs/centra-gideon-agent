"""``tooling/scripts/run_prepush.sh``: it must gate the tree it is pushing, and scope by what
that branch actually adds.

Two concerns, one file, because they are the same two lines of the script reading the
same stdin: the guard decides *whether* this worktree may be gated at all, and the range
right after it decides *which* halves run. Both are cheap to get subtly wrong and
expensive to notice — one goes green while proving nothing, the other burns ten minutes
proving something nobody asked about.

── 1. The tree/ref guard ──────────────────────────────────────────────────────

**Measured gap (2026-08-27).** ``tooling/scripts/run_prepush.sh`` read the ref ranges
githooks(5) puts on stdin and used ``local_sha`` for exactly one purpose: computing
``range="$remote_sha..$local_sha"`` to decide *whether* the frontend half
(``needs_gate``) and the Python half (``needs_lint``) should run at all. It then ran
black/isort/flake8 and ``npm ci`` → build → ``tooling/scripts/render_smoke.mjs`` against
``git rev-parse --show-toplevel`` — the **working tree**.
``git grep -c "rev-parse HEAD" tooling/scripts/run_prepush.sh`` returned **0**: nothing
asserted the tree was the thing being pushed.

That is a live hazard, not a theoretical one, because this repo is worked with many
``git worktree``s at once. ``git push origin some-branch`` from a checkout sitting on
``main`` scoped the gate by ``some-branch``'s diff and then validated ``main``'s tree
— green, and proof of nothing about what shipped. Batching (``git push origin br1 br2
br3``) to pay the ~20-minute ``npm ci`` + render-smoke cost once instead of three
times is exactly the shape that produces it: one hook run, three unvalidated
branches, ``main``'s tree gated three times.

**These tests drive the real script**, with synthesized stdin ref lines. Re-deriving
the ref-line parsing in Python would assert nothing about the shipped file — the file
is the gate.

**Why a sandbox repo.** The script resolves everything through
``git rev-parse --show-toplevel``, so the only way to control both ``HEAD`` and the
diff ranges is to point it at a repo built for the purpose:
:func:`sandbox` makes one with two commits and a byte-identical copy of the shipped
script, asserted identical. That is also what makes these legs portable — an earlier
version read *this* repo's history and errored in CI, because ``actions/checkout``
clones shallow (``fetch-depth: 1``) and ``HEAD~1`` does not exist there.

**Cheap by construction, not by mocking.** The guard sits inside the ref loop, before
the range is computed, so every refusing leg exits before either expensive half is
reached. The non-refusing legs feed a ref line whose range is empty, so both halves
are correctly skipped. The sandbox holds no ``apps/console/`` or ``runtime/gideon`` path
either, so nothing there can pull in ``npm ci``. Nothing is stubbed.

**Vacuity.** :func:`test_the_refusal_is_attributable_to_the_guard` reruns the exact
stdin of the negative leg against a copy of the script whose *own* condition is
neutered to ``if false``, and asserts that copy exits 0. So the negative leg's red is
attributable to the guard rather than to any other non-zero exit the script can
produce, and the substitution is asserted to have applied — a rename of that line
reds this test instead of silently making it vacuous.

── 2. Scope, after a rebase (issue 2269) ──────────────────────────────────────

**Measured gap (2026-09-01).** The range was ``remote_sha..local_sha``, which answers
"what changed between these two commits" — the same question as "what does this branch
add" only while the branch is a fast-forward of its remote. A rebase strands the old
remote tip, and the range then spans every ``main`` commit in between. Measured on a
real three-file backend-only branch: **94 frontend files in that range, versus 0 the
branch touches.** The gate ran ``npm ci`` → typecheck → vitest → build →
``playwright install`` → render-smoke for ten minutes and then failed on a web-suite
timeout in a file the diff could not reach.

Rebasing before a push is routine, and branches here are rebased for you, so this was
the common case. The fix scopes by ``merge-base(local_sha, origin/main)`` and keeps the
old range only as the answer for a clone that has no ``origin/main``.

**Where the falsification lives.** The behavioural leg is
:func:`test_a_rebased_backend_only_branch_skips_the_frontend_chain`; its floor is
:func:`test_a_branch_that_really_changes_the_frontend_still_gates`, because "scope it
more narrowly" is one step from "scope it to nothing" and a gate that never fires is
indistinguishable from a fast one. :func:`test_the_merge_base_is_preferred_over_the_remote_tip`
pins the ORDER rather than the presence of either range — swapping the two rungs leaves
both lines in the file and every sandbox leg green, since the sandboxes that exercise
the fallback have no ``origin/main`` for the first rung to win with.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "tooling/scripts" / "run_prepush.sh"

ZERO = "0" * 40

GUARD_CONDITION = 'if [ "$pushed_commit" != "$head_commit" ]; then'

REFUSAL = "refusing to gate a tree that is not what you are pushing"
REACHED_THE_END = "no frontend changes outgoing"

_IDENT = (
    "-c",
    "user.name=Gate Test",
    "-c",
    "user.email=gate@test.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class Sandbox:
    """A throwaway repo carrying the shipped script, plus its two commit shas."""

    def __init__(self, root: Path, head: str, parent: str) -> None:
        self.root = root
        self.script = root / "tooling/scripts" / "run_prepush.sh"
        self.head = head
        self.parent = parent


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    (root / "tooling/scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "tooling/scripts" / "run_prepush.sh")
    assert (
        root / "tooling/scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes(), "the sandbox copy is not the shipped script"

    _git("init", "-q", "-b", "main", cwd=root)
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "first", cwd=root)
    (root / "notes.txt").write_text("two\n", encoding="utf-8")
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-am", "second", cwd=root)

    head = _git("rev-parse", "HEAD", cwd=root)
    parent = _git("rev-parse", "HEAD~1", cwd=root)
    assert head != parent, "the sandbox has no second commit"
    return Sandbox(root, head, parent)


def _run(
    stdin: str, *, cwd: Path, script: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real gate with `stdin` as its githooks(5) ref lines."""
    return subprocess.run(
        ["sh", str(script or (cwd / "tooling/scripts" / "run_prepush.sh"))],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _assert_refused(result: subprocess.CompletedProcess[str]) -> None:
    """A refusal is a non-zero exit *taken by the guard*, before anything else ran.

    The empty stdout is the load-bearing half. Found while falsifying: with the guard
    softened to print-and-continue, a ref line whose ``remote_sha`` is all-zeroes fell
    through to the merge-base branch, gated unconditionally, and then exited non-zero
    because black had no ``runtime/gideon`` to check in the sandbox. Asserting only
    ``returncode != 0`` and the message therefore read as green against a guard that had
    been turned into a warning — exactly the false green this file exists to close. The
    guard writes to stderr and exits before either half announces itself on stdout, so an
    empty stdout is what separates "refused" from "warned, then failed for another
    reason".
    """
    assert result.returncode != 0, f"the guard did not fire: stdout={result.stdout!r}"
    assert REFUSAL in result.stderr
    assert result.stdout == "", (
        "the script kept going past the guard, so this non-zero exit is not the refusal: "
        f"stdout={result.stdout!r}"
    )


def test_the_shipped_script_still_carries_the_guard():
    """A floor for every leg below: the thing under test must be present."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert GUARD_CONDITION in source, (
        "the tree/ref guard's condition is gone from tooling/scripts/run_prepush.sh — the legs "
        "below would be measuring some other exit path. Restore it, do not weaken them."
    )


def test_pushing_this_worktrees_head_is_not_refused(sandbox: Sandbox):
    """The positive leg: the tree IS the ref, so the guard stands aside.

    ``remote_sha == local_sha == HEAD`` is an empty range, so both halves are owed
    nothing and the script takes its cheap exit — which is also how this leg proves it
    ran past the guard instead of never reaching it.
    """
    result = _run(
        f"refs/heads/some-branch {sandbox.head} refs/heads/some-branch {sandbox.head}\n",
        cwd=sandbox.root,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr
    assert (
        REACHED_THE_END in result.stdout
    ), f"the script did not reach the end of the ref loop: stdout={result.stdout!r}"


def test_the_real_repo_does_not_refuse_its_own_head():
    """The same positive leg, in place, against the checkout this file ships in.

    The sandbox proves the logic; this proves the shipped file behaves that way where it
    actually runs.

    🪤 THE OLD PREMISE WAS FALSE, AND ONLY ON `main` DID IT LOOK TRUE. This test used to
    claim "``HEAD..HEAD`` is an empty range, so this stays cheap" and assert a clean exit.
    But the script does not use the stdin range: it PREFERS
    ``git merge-base "$local_sha" origin/main`` (see its own "preference order" comment), so
    the range it actually scopes by is this branch's whole diff versus ``origin/main``. On
    ``main`` that is empty and the test costs 11 seconds; on any branch touching a frontend
    path it sets ``needs_gate=1`` and the script correctly runs ``npm ci`` → build → render
    smoke, which the script's own header prices at ~20 minutes. Measured: this test timed out
    at 120s on a branch whose only frontend change was one documentation URL.

    The two things this test wants are in TENSION and cannot both be cheap here. The guard
    requires ``local_sha == HEAD`` — that is the property under test — and that is precisely
    what makes the scoping expensive. So the assertion is narrowed to the guard alone, which
    is all this leg was ever for: the guard refuses FAST and non-zero, before any gating
    work. Reaching the gates at all therefore proves the guard passed, and a timeout waiting
    for `npm ci` is a PASS for this property rather than a failure to be silenced.
    """
    head = _git("rev-parse", "HEAD", cwd=REPO_ROOT)
    stdin = f"refs/heads/some-branch {head} refs/heads/some-branch {head}\n"
    try:
        result = subprocess.run(
            ["sh", str(SCRIPT)],
            cwd=REPO_ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        err = (exc.stderr or b"").decode(errors="replace") if exc.stderr else ""
        assert REFUSAL not in err, f"refused before the gates began: {err!r}"
        return
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr


def test_pushing_a_ref_this_worktree_does_not_have_checked_out_is_refused(
    sandbox: Sandbox,
):
    """The negative leg: non-zero exit, and the message names BOTH shas.

    Naming both is the whole remedy — "wrong tree" without the two commits leaves the
    reader guessing which worktree to go to. ``HEAD`` is read off the checkout by the
    script itself, never from stdin, which is why the ref line can carry the same sha on
    both sides: that keeps the range empty, so the guard-free copy the vacuity leg runs
    on this *identical* stdin also takes the cheap exit.
    """
    result = _run(
        f"refs/heads/other-branch {sandbox.parent} refs/heads/other-branch {sandbox.parent}\n",
        cwd=sandbox.root,
    )
    _assert_refused(result)
    assert (
        sandbox.parent in result.stderr
    ), "the refusal does not name the pushed commit"
    assert (
        sandbox.head in result.stderr
    ), "the refusal does not name the checkout's HEAD"
    assert "git worktree list" in result.stderr, "the refusal does not name the fix"


def test_a_new_remote_branch_is_guarded_too(sandbox: Sandbox):
    """The most common first push — ``remote_sha`` all-zeroes — is not a hole.

    The guard runs before the range is computed at all, so a brand-new branch pushed from
    the wrong worktree is refused on the same terms. (``remote_sha`` used to select a
    separate merge-base branch here; issue 2269 made the merge-base the only path, which
    leaves this leg testing the same thing through one fewer case.)
    """
    result = _run(
        f"refs/heads/brand-new {sandbox.parent} refs/heads/brand-new {ZERO}\n",
        cwd=sandbox.root,
    )
    _assert_refused(result)


def test_a_branch_deletion_is_not_refused(sandbox: Sandbox):
    """The deletion leg: an all-zeroes ``local_sha`` names no tree to check.

    ``git push origin --delete some-branch`` ships no commits, so there is nothing for
    the guard to compare and a refusal here would be a false one.
    """
    result = _run(
        f"(delete) {ZERO} refs/heads/some-branch {sandbox.head}\n", cwd=sandbox.root
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr


def test_the_refusal_is_attributable_to_the_guard(sandbox: Sandbox):
    """Vacuity: the same stdin passes once the guard's own condition is neutered.

    Without this, the negative leg could be reading any of the script's other non-zero
    exits. A guard whose test cannot fail is not a guard.
    """
    source = sandbox.script.read_text(encoding="utf-8")
    assert (
        source.count(GUARD_CONDITION) == 1
    ), "the guard condition is not a unique line"
    neutered = sandbox.root / "tooling/scripts" / "run_prepush_without_the_guard.sh"
    neutered.write_text(
        source.replace(GUARD_CONDITION, "if false; then"), encoding="utf-8"
    )
    assert "if false; then" in neutered.read_text(
        encoding="utf-8"
    ), "the neuter did not apply"

    stdin = f"refs/heads/other-branch {sandbox.parent} refs/heads/other-branch {sandbox.parent}\n"
    _assert_refused(_run(stdin, cwd=sandbox.root))

    result = _run(stdin, cwd=sandbox.root, script=neutered)
    assert result.returncode == 0, (
        "the guard-free copy still failed, so the negative leg above is not measuring "
        f"the guard: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert REFUSAL not in result.stderr
    assert REACHED_THE_END in result.stdout, (
        "the guard-free copy exited 0 without reaching the end of the ref loop, so this "
        f"leg is not comparing the same path: stdout={result.stdout!r}"
    )


MERGE_BASE_SCOPE = 'git merge-base "$local_sha" origin/main'
REMOTE_TIP_SCOPE = 'range="$remote_sha..$local_sha"'

GATING = "frontend changes outgoing — running the render-smoke gate"


class Rebased:
    """A sandbox where a branch was rebased and its remote tip left behind.

    Three shas matter: `old_tip` (what the remote still points at), `new_main` (what the
    branch is now based on, and what `main` gained a `apps/console/` file in), and `head` (the
    rebased tip, checked out). `old_tip..head` therefore spans `main`'s frontend commit;
    `merge-base(head, origin/main)..head` does not.
    """

    def __init__(self, root: Path, head: str, old_tip: str, new_main: str) -> None:
        self.root = root
        self.head = head
        self.old_tip = old_tip
        self.new_main = new_main


def _sandbox_repo(tmp_path: Path, name: str) -> Path:
    """A repo carrying a byte-identical copy of the shipped script, on a first commit.

    The script lives in the FIRST commit for the reason the other fixture states: it is
    itself one of `FRONTEND_PATHS`, so any range that named it would drag `npm ci` into
    a test. Nothing here touches `PYTHON_PATHS` either, so the lint half stays unowed.
    """
    root = tmp_path / name
    (root / "tooling/scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "tooling/scripts" / "run_prepush.sh")
    assert (
        root / "tooling/scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes()
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "first", cwd=root)
    return root


@pytest.fixture
def rebased(tmp_path: Path) -> Rebased:
    root = _sandbox_repo(tmp_path, "rebased")

    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=root)
    (root / "notes.txt").write_text("branch work\n", encoding="utf-8")
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-am", "backend-only work", cwd=root)
    old_tip = _git("rev-parse", "HEAD", cwd=root)

    _git(*_IDENT, "checkout", "-q", "main", cwd=root)
    (root / "apps/console").mkdir(parents=True)
    (root / "apps/console" / "Thing.tsx").write_text(
        "export const Thing = () => null\n", encoding="utf-8"
    )
    _git("add", "-A", cwd=root)
    _git(
        *_IDENT,
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "somebody else's frontend work",
        cwd=root,
    )
    new_main = _git("rev-parse", "HEAD", cwd=root)
    _git("update-ref", "refs/remotes/origin/main", new_main, cwd=root)

    _git(*_IDENT, "checkout", "-q", "feature", cwd=root)
    _git(*_IDENT, "rebase", "-q", "main", cwd=root)
    head = _git("rev-parse", "HEAD", cwd=root)
    assert head != old_tip, "the rebase did not rewrite the branch"
    assert (
        _git("merge-base", head, "refs/remotes/origin/main", cwd=root) == new_main
    ), "the branch is not based on the new main; the fixture is not modelling a rebase"
    return Rebased(root, head, old_tip, new_main)


def test_the_merge_base_is_preferred_over_the_remote_tip():
    """A floor for the legs below: the fix must still be in the file, in the right order.

    Reordering these two rungs is the whole regression — both lines would still be present
    and every other test in this file would still pass, because the sandboxes that exercise
    the fallback have no `origin/main` for the first rung to win with.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert MERGE_BASE_SCOPE in source, (
        "the merge-base scoping is gone from tooling/scripts/run_prepush.sh; the legs below would "
        "be measuring some other path (issue 2269)"
    )
    assert (
        REMOTE_TIP_SCOPE in source
    ), "the fork fallback is gone; see its own leg below"
    assert source.index(MERGE_BASE_SCOPE) < source.index(REMOTE_TIP_SCOPE), (
        "tooling/scripts/run_prepush.sh reaches `remote_sha..local_sha` before the merge-base. On "
        "a rebased branch that range spans every main commit in between, so a backend-only "
        "push runs the whole ten-minute frontend chain and can fail on web tests the diff "
        "could not affect (issue 2269). The merge-base must be tried first."
    )


def test_a_rebased_backend_only_branch_skips_the_frontend_chain(rebased: Rebased):
    """🔴 issue 2269, the whole point. The stranded remote tip must not decide the scope.

    Measured before the fix on a real three-file backend branch: `old_tip..head` named 94
    frontend files, all of them `main`'s, and the gate spent ten minutes on a chain the
    diff could not affect — then failed on a web-suite timeout.
    """
    result = _run(
        f"refs/heads/feature {rebased.head} refs/heads/feature {rebased.old_tip}\n",
        cwd=rebased.root,
    )
    assert result.returncode == 0, (
        "the gate did not take its cheap exit on a branch that changes no frontend file: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert REACHED_THE_END in result.stdout, f"stdout={result.stdout!r}"
    assert GATING not in result.stdout


def test_a_branch_that_really_changes_the_frontend_still_gates(rebased: Rebased):
    """🪤 The vacuity floor, and it is the one that matters here.

    "Scope it more narrowly" is one step from "scope it to nothing", and a gate that
    never fires costs nothing and protects nothing. So: commit a `apps/console/` change ON the
    branch and assert the expensive half is owed. The assertion is on the announcement
    rather than the exit code, because `npm ci` in a repo with no `package.json` fails
    for its own reasons and that failure would look like a gate either way.
    """
    (rebased.root / "apps/console" / "Extra.tsx").write_text(
        "export const Extra = () => null\n", "utf-8"
    )
    _git("add", "-A", cwd=rebased.root)
    _git(
        *_IDENT,
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "my own frontend change",
        cwd=rebased.root,
    )
    head = _git("rev-parse", "HEAD", cwd=rebased.root)

    result = _run(
        f"refs/heads/feature {head} refs/heads/feature {rebased.old_tip}\n",
        cwd=rebased.root,
    )
    assert GATING in result.stdout, (
        "the gate skipped the frontend chain for a branch that changes a `apps/console/` file — "
        f"the scoping is now too narrow: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert REACHED_THE_END not in result.stdout


def test_an_unrelated_upstream_frontend_commit_after_the_rebase_does_not_gate(
    rebased: Rebased,
):
    """🔴 req 9.2, the case the merge-base exists to answer: main moves ON without you.

    The leg above rebases the branch ONTO main's frontend commit, so the stranded remote
    tip is what a naive range would drag in. This is the other half of the same week: the
    branch is rebased, and then somebody else lands more frontend work upstream while it
    sits in review. ``origin/main`` is now AHEAD of the branch, and nothing about the branch
    changed — it must still take the cheap exit.

    That is a different rung of the scoping, not a restatement: ``merge-base(HEAD,
    origin/main)`` here is the commit the branch is based on, NOT ``origin/main`` itself, so
    a scoping that reached for the remote ref directly (``git diff origin/main HEAD``, the
    obvious spelling) would see every upstream file as a difference and gate. The floor
    below measures exactly that, so this leg cannot be satisfied by the wrong fix.
    """
    _git(*_IDENT, "checkout", "-q", "main", cwd=rebased.root)
    (rebased.root / "apps/console" / "Later.tsx").write_text(
        "export const Later = () => null\n", encoding="utf-8"
    )
    _git("add", "-A", cwd=rebased.root)
    _git(
        *_IDENT,
        "commit",
        "-q",
        "--no-gpg-sign",
        "-m",
        "more of somebody else's frontend work",
        cwd=rebased.root,
    )
    upstream = _git("rev-parse", "HEAD", cwd=rebased.root)
    _git("update-ref", "refs/remotes/origin/main", upstream, cwd=rebased.root)
    _git(*_IDENT, "checkout", "-q", "feature", cwd=rebased.root)

    assert (
        _git("rev-parse", "HEAD", cwd=rebased.root) == rebased.head
    ), "the branch moved; this leg is no longer about an upstream-only change"
    assert (
        _git("merge-base", rebased.head, "refs/remotes/origin/main", cwd=rebased.root)
        == rebased.new_main
    ), "origin/main is not ahead of the branch — the fixture is not modelling this case"
    assert _git(
        "diff",
        "--name-only",
        upstream,
        rebased.head,
        "--",
        "apps/console",
        cwd=rebased.root,
    ), (
        "the upstream commit is invisible even to a naive two-endpoint diff, so this leg "
        "would pass against any scoping at all"
    )

    result = _run(
        f"refs/heads/feature {rebased.head} refs/heads/feature {rebased.old_tip}\n",
        cwd=rebased.root,
    )
    assert result.returncode == 0, (
        "upstream frontend work the branch does not contain forced the render-smoke gate: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert REACHED_THE_END in result.stdout, f"stdout={result.stdout!r}"
    assert GATING not in result.stdout


LINT_TREES = ("runtime/gideon", "checks/runtime", "checks/harness")

NO_JS = "node/npm not found"
LINT_GREEN = "pre-push: python lint green."


class Degraded:
    """A sandbox branch that owes BOTH halves, plus a PATH that carries neither node nor npm."""

    def __init__(self, root: Path, head: str, base: str, path: str) -> None:
        self.root = root
        self.head = head
        self.base = base
        self.path = path


@pytest.fixture
def degraded(tmp_path: Path) -> Degraded:
    """A branch touching a Python path AND a frontend path, with the real linters lent to it.

    Both halves have to be genuinely owed, or "the Python half still ran" is unfalsifiable:
    a branch that owes no lint prints nothing either way. So the commit adds a file under
    each of the three trees ``run_prepush.sh`` lints and one under ``apps/console/``.

    The linters are the SHIPPED ones, symlinked into the sandbox's ``.venv/bin`` — the first
    rung of the script's own tool detection, and the same binaries a developer's checkout
    resolves. They are lent rather than reproduced so "lint green" means black, isort and
    flake8 actually read these files and agreed.

    ``PATH`` is rebuilt from nothing but ``git``: that is the condition under test, and
    emptying it is what a machine without Node looks like to ``command -v``. The linters are
    unaffected because their shebangs name an interpreter by absolute path.
    """
    root = _sandbox_repo(tmp_path, "degraded")
    base = _git("rev-parse", "HEAD", cwd=root)

    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=root)
    for tree in LINT_TREES:
        d = root / tree
        d.mkdir(parents=True, exist_ok=True)
        (d / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "apps/console").mkdir(parents=True)
    (root / "apps/console" / "Thing.tsx").write_text(
        "export const Thing = () => null\n", encoding="utf-8"
    )
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "both halves", cwd=root)
    head = _git("rev-parse", "HEAD", cwd=root)

    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    for tool in ("black", "isort", "flake8"):
        shipped = REPO_ROOT / ".venv" / "bin" / tool
        if not shipped.is_file():
            pytest.skip(f"{tool} is not installed in .venv; the lint half cannot run")
        (venv_bin / tool).symlink_to(shipped)

    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("git", "sh"):
        found = shutil.which(tool)
        assert found, f"{tool} is not on PATH"
        (bare / tool).symlink_to(found)
    return Degraded(root, head, base, str(bare))


def _run_with_path(stdin: str, *, sandbox: Degraded, path: str):
    env = dict(os.environ, PATH=path)
    return subprocess.run(
        ["sh", str(sandbox.root / "tooling/scripts" / "run_prepush.sh")],
        cwd=sandbox.root,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_the_bare_path_really_has_no_js_toolchain(degraded: Degraded):
    """Vacuity floor for the leg below: the PATH under test must actually lack node/npm,
    or "it skipped" is just the ambient machine being measured."""
    assert shutil.which("node", path=degraded.path) is None
    assert shutil.which("npm", path=degraded.path) is None


def test_a_missing_js_toolchain_skips_only_the_frontend_half(degraded: Degraded):
    """🔴 req 33.1. No node, no npm — the render-smoke gate is skipped with a message that
    says so, and the Python half still runs to a verdict.

    The exit is 0 on purpose: a contributor with no Node installed is not pushing something
    broken, and blocking their backend push on a toolchain their change cannot reach is the
    defect. What they must not get is silence — hence the message, and hence `LINT_GREEN`,
    which is black/isort/flake8 having really read the committed files.
    """
    result = _run_with_path(
        f"refs/heads/feature {degraded.head} refs/heads/feature {degraded.base}\n",
        sandbox=degraded,
        path=degraded.path,
    )
    assert result.returncode == 0, (
        "a missing JavaScript toolchain blocked the push: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert NO_JS in result.stdout, f"the skip was silent: stdout={result.stdout!r}"
    assert "CI" in result.stdout, "the message does not say CI still runs the check"
    assert LINT_GREEN in result.stdout, (
        "the Python half did not run to a verdict — the skip took more than the frontend "
        f"check with it: stdout={result.stdout!r}"
    )
    assert GATING not in result.stdout
    assert "render-smoke gate green" not in result.stdout


def test_with_the_toolchain_present_the_same_branch_still_gates(degraded: Degraded):
    """🪤 The falsification. The degradation must be about the toolchain, not about this
    branch quietly having stopped owing the gate.

    Asserted on the announcement rather than the exit code, for the reason its sibling leg
    states: `npm ci` in a sandbox with no `package.json` fails for its own reasons, and that
    failure looks like a gate either way.
    """
    result = _run_with_path(
        f"refs/heads/feature {degraded.head} refs/heads/feature {degraded.base}\n",
        sandbox=degraded,
        path=os.environ.get("PATH", ""),
    )
    assert GATING in result.stdout, (
        "the branch does not owe the frontend gate even with node present, so the skip "
        f"leg proves nothing: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert NO_JS not in result.stdout


def test_ci_runs_the_frontend_chain_unconditionally(degraded: Degraded):
    """🔴 req 33.2. The local gate degrades; CI does not.

    The workflow's web job has no `command -v node` anywhere and no `if:` on its test steps
    — it installs Node with `actions/setup-node` and then runs the chain, so the complete
    frontend check stays mandatory exactly where it is the merge criterion. Read from the
    shipped workflow rather than asserted in prose, so relaxing CI reds this.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    web = workflow.split("\n  web:\n", 1)[1].split("\n  e2e-pwa:", 1)[0]
    assert "actions/setup-node" in web, "the web job no longer installs Node"
    for step in (
        "npm ci",
        "npm run typecheck:web",
        "npm run test:web",
        "npm run build",
    ):
        assert step in web, f"the web job no longer runs `{step}`"
    assert (
        "command -v node" not in web and "command -v npm" not in web
    ), "the local skip leaked into CI; the frontend check must stay unconditional there"


def test_no_shared_history_with_main_gates_unconditionally(tmp_path: Path):
    """The fallback. With no `origin/main` there is nothing to measure against, so the
    honest answer is to run both halves rather than to skip blind — the same call the
    new-branch path made before this change, now the only call."""
    root = _sandbox_repo(tmp_path, "orphan")
    head = _git("rev-parse", "HEAD", cwd=root)
    assert not (root / ".git" / "refs" / "remotes" / "origin").exists()

    result = _run(f"refs/heads/feature {head} refs/heads/feature {ZERO}\n", cwd=root)
    assert REACHED_THE_END not in result.stdout, (
        "the gate skipped both halves with no origin/main to scope against: "
        f"stdout={result.stdout!r}"
    )


def test_without_origin_main_the_old_range_is_still_used(sandbox: Sandbox):
    """The middle rung of the preference order, and why it is kept.

    A fork that tracks `upstream` has no `origin/main`, and for it `remote_sha..local_sha`
    is still the best answer available: exact while the branch is a fast-forward, too wide
    after a rewrite. Gating unconditionally instead would make every push on such a clone
    pay the full chain forever — a real cost, to buy nothing, since the wide range already
    errs toward gating. This sandbox has no `origin/main`, so an empty `remote..local`
    range must still reach the cheap exit.
    """
    assert not (sandbox.root / ".git" / "refs" / "remotes" / "origin").exists()
    result = _run(
        f"refs/heads/some-branch {sandbox.head} refs/heads/some-branch {sandbox.head}\n",
        cwd=sandbox.root,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REACHED_THE_END in result.stdout
