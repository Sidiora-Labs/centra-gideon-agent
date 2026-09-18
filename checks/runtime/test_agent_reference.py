"""Drift guard for the shipped offline agent reference (PLATFORM-LEGIBILITY §3.1).

The reference under ``gideon/reference/*.md`` is GENERATED from the same
``build_manifest`` output the live ``/api/manifest`` walks — one source, two
renderings. This suite is what keeps the checked-in copy honest: it renders fresh
and byte-compares, so a tool/route added without its ``TOOL_META`` / route entry
(or a manual edit to the generated files) reddens the build. When it fires, the
failure message spells out the remedy — see :data:`_STALE_REMEDY`, which pins
``PYTHONPATH`` because a bare ``python -m gideon.extensions.manifest_reference`` run from
a worktree regenerates the MAIN checkout instead, and which says that drift on a
branch touching no route is usually ``main``'s from a merge-train union.

It also asserts the two operator-facing contracts the reference exists to serve:
the `gideon-api` skill points at a reference that actually exists and cross-links
`gideon-features`, and ``doctor --paths`` resolves the reference dir the skill tells
agents to find.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import gideon.extensions.manifest_reference as ref_mod
from gideon.extensions.manifest_reference import reference_dir, render_reference

_STALE_REMEDY = (
    "Offline reference is stale. Regenerate it with the path PINNED to this checkout:\n"
    '    PYTHONPATH="$(git rev-parse --show-toplevel)/src" \\\n'
    "        .venv/bin/python -m gideon.extensions.manifest_reference\n"
    "Then confirm the write landed HERE and not in the main checkout:\n"
    "    git status --porcelain -- runtime/gideon/reference/\n"
    "If that comes back empty, the generator wrote to the main checkout instead "
    "(its output names the absolute paths it wrote) — restore those and re-run with "
    "PYTHONPATH set.\n"
    "If this fired on a branch that touches no route, tool or provider, the drift is "
    "probably already on `origin/main` from a merge-train union rather than yours; "
    "these files are derived, so recompute rather than picking a side of any "
    "conflict.\nStale files:"
)


def _drifted_against(root: Path, rendered: dict[str, str]) -> list[str]:
    """Compare one rendering to the copy shipped under ``root``."""
    mismatches: list[str] = []
    for filename, expected in rendered.items():
        path = root / filename
        if not path.is_file():
            mismatches.append(f"{filename}: missing from the shipped reference dir")
        elif path.read_text(encoding="utf-8") != expected:
            mismatches.append(f"{filename}: differs from a fresh render")
    return mismatches


def _mismatches_once_the_render_is_stable(render, drifted, attempts=5, delay=0.3):
    """Render repeatedly until two consecutive renderings AGREE, then report the drift.

    Two consecutive byte-identical renderings are the evidence that the source tree was
    quiescent for the span of the measurement; only then is a disagreement with the
    shipped copy attributable to the copy rather than to a sibling worker perturbing the
    tree mid-scan. A stable, reproducible drift renders the same bytes every time, so the
    first two renderings already agree and it reds immediately — the absorbed case is
    exactly the non-reproducing one.
    """
    previous = render()
    mismatches = drifted(previous)
    for _ in range(attempts):
        if not mismatches:
            return mismatches
        time.sleep(delay)
        current = render()
        mismatches = drifted(current)
        if current == previous:
            return mismatches
        previous = current
    return mismatches


def test_checked_in_reference_matches_a_fresh_render():
    """Every shipped reference file byte-matches a fresh render — no manual drift.

    This is the whole point: an agent reads exact signatures, so the checked-in
    copy must equal what the generator produces from the current registries.

    Re-rendered until STABLE rather than compared once — and that is determinism,
    not a softened guard. The render is a pure function of the source tree
    (bit-identical across repeated calls in a quiescent process), but it derives
    routes/tools/providers by SCANNING that tree live: an AST walk over every
    ``*.py`` under the package plus a walk of the bundled-apps dir. That tree is
    shared by every ``-n auto`` worker PROCESS, so a sibling on another worker can
    transiently perturb it — a scaffold / install / codegen that creates then
    removes a file mid-scan, a window coverage widens — and the resulting one-off
    byte drift reds an unrelated PR that only a full ~44-min re-run clears (the
    documented flake this test was). A GENUINE drift — a tool or route added without
    its ``TOOL_META`` / route entry, or a hand-edited generated file — is
    deterministic and reproduces on EVERY render, so it still reds every attempt
    below; only a non-reproducing race is absorbed, and the remedy the assertion
    prints when it fires is unchanged.
    """
    root = reference_dir()
    mismatches = _mismatches_once_the_render_is_stable(
        render_reference, lambda rendered: _drifted_against(root, rendered)
    )
    assert not mismatches, _STALE_REMEDY + "\n" + "\n".join(mismatches)


def test_reference_has_the_four_expected_files():
    """index/tools/routes/providers all render (a dropped section would be a regression)."""
    assert set(render_reference()) == {
        "index.md",
        "tools.md",
        "routes.md",
        "providers.md",
    }


def test_route_signature_prefix_is_stripped():
    """A docstring that restates the route signature is de-duplicated in the summary."""
    assert ref_mod._clean_summary("GET /api/foo — does a thing") == "does a thing"
    assert (
        ref_mod._clean_summary("GET/PUT /api/agent/config — read or write")
        == "read or write"
    )
    assert ref_mod._clean_summary("POST /api/x/:id/activate") == ""
    assert (
        ref_mod._clean_summary("List all scheduled jobs.") == "List all scheduled jobs."
    )


def test_tools_reference_lists_every_provider():
    """Each tool provider heading appears — the reference covers the whole surface."""
    tools_md = render_reference()["tools.md"]
    providers = {
        "gideon-core",
        "gideon-automation",
        "gideon-artifacts",
        "gideon-memory",
        "gideon-knowledge-tools",
        "gideon-tasks-tools",
    }
    for p in providers:
        assert f"## {p}" in tools_md, f"{p} missing from tools.md"


def test_reference_examples_carry_no_invented_params():
    """Every ```json example block in tools.md is valid JSON (faithful args).

    The drift test already checks example arg names against the live schema; this
    asserts the RENDERED form stays machine-parseable, since the reference's value
    is that an agent can copy an example verbatim.
    """
    import json

    tools_md = render_reference()["tools.md"]
    blocks = re.findall(r"```json\n(.*?)\n```", tools_md, re.DOTALL)
    assert blocks, "expected at least one example json block"
    for b in blocks:
        json.loads(b)


def _skill_path() -> Path:
    return (
        Path(ref_mod.__file__).parent / "skills" / "bundled" / "gideon-api" / "SKILL.md"
    )


def test_gideon_api_skill_ships_and_cross_references_features():
    """The operator skill exists, points at the reference + doctor --paths, and
    cross-references its prose twin (§3.1)."""
    text = _skill_path().read_text(encoding="utf-8")
    assert "reference/index.md" in text
    assert "doctor --paths" in text
    assert "gideon-features" in text
    assert "verify" in text.lower()
    assert "reference" in text.lower()


def test_doctor_paths_resolves_the_reference_dir():
    """``doctor --paths`` prints the reference dir the skill tells agents to find."""
    from gideon.extensions.skills.loader import skills_dir

    rd = reference_dir()
    assert rd.is_dir()
    assert (rd / "index.md").is_file()
    assert isinstance(skills_dir(), Path)


def test_the_stale_remedy_stays_actionable():
    """The remedy message must keep the two things that make it work.

    Both are load-bearing and both were learned the expensive way, so a reword that
    drops them should red here rather than quietly returning the message to a bare
    ``python -m …`` that sends the next reader into the same two traps.

    This is a floor on the FAILURE PATH, which no passing run ever exercises — the
    guard above is green whenever the reference is current, so without this leg the
    message could rot indefinitely and only be read on the day it is needed most.
    """
    assert "PYTHONPATH=" in _STALE_REMEDY, (
        "the remedy no longer pins PYTHONPATH — a bare `-m` run from a worktree "
        "regenerates the MAIN checkout and leaves this test still failing"
    )
    assert (
        "git status --porcelain" in _STALE_REMEDY
    ), "the remedy no longer tells the reader how to confirm WHICH tree it wrote"
    assert "origin/main" in _STALE_REMEDY, (
        "the remedy no longer says the drift may be main's from a merge-train union, "
        "which is what sends people hunting through their own diff for an hour"
    )


class TestTheDriftGuardStabilizesBeforeItAccuses:
    """§73 — the retry is a quiescence measurement, not a softened assertion.

    Both legs drive the real comparison (:func:`_drifted_against`) against real files on
    disk; only the RENDERING is supplied, because a transient perturbation of the source
    tree by a sibling ``-n auto`` worker cannot be produced on demand.
    """

    @staticmethod
    def _shipped(tmp_path: Path, body: str) -> Path:
        (tmp_path / "index.md").write_text(body, encoding="utf-8")
        return tmp_path

    def test_a_one_off_perturbation_is_absorbed(self, tmp_path):
        """One odd rendering that does not reproduce must not red an unrelated PR."""
        root = self._shipped(tmp_path, "shipped\n")
        renders = iter([{"index.md": "perturbed\n"}, {"index.md": "shipped\n"}])
        mismatches = _mismatches_once_the_render_is_stable(
            lambda: next(renders),
            lambda rendered: _drifted_against(root, rendered),
            delay=0,
        )
        assert mismatches == []

    def test_stable_reproducible_drift_still_fails(self, tmp_path):
        """A genuine drift renders identically every time, so it is never absorbed."""
        root = self._shipped(tmp_path, "shipped\n")
        calls = []

        def render() -> dict[str, str]:
            calls.append(1)
            return {"index.md": "regenerated\n"}

        mismatches = _mismatches_once_the_render_is_stable(
            render, lambda rendered: _drifted_against(root, rendered), delay=0
        )
        assert mismatches == ["index.md: differs from a fresh render"]
        assert len(calls) == 2, "two agreeing renders are enough to accuse"

    def test_a_missing_shipped_file_is_reported_not_absorbed(self, tmp_path):
        mismatches = _mismatches_once_the_render_is_stable(
            lambda: {"index.md": "regenerated\n"},
            lambda rendered: _drifted_against(tmp_path, rendered),
            delay=0,
        )
        assert mismatches == ["index.md: missing from the shipped reference dir"]
