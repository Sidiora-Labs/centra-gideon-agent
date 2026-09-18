"""Shorter parameterized ids for the unsharded coverage job, and for nothing else.

**Why this exists (req 86).** The monolithic leg — ``.github/workflows/full.yml``'s
``coverage`` job, ``pytest --cov=gideon --cov-report=xml`` with no ``--splits`` — prints every
collected id in one stream, and GitHub truncates a step's log. Sharding shortens that log too,
by printing a quarter of it per leg, which means the two remedies are confounded: nobody could
say whether short ids help, because the only run where they would show is also the run that
was sharded. So the shortening is scoped to the ONE unsharded job and switched on by an
environment variable, and these tests pin both halves of that: the rules, and the scoping.

**The scoping is the load-bearing half.** ``pytest-split`` distributes by recorded duration
per node-id. Rewriting ids in a sharded job invalidates every recorded duration at once — the
shards silently re-balance and the suite gets slower for a reason that looks like nothing.
:func:`test_the_hook_declines_when_the_switch_is_off` and
:func:`test_only_the_unsharded_job_opts_in` are what stop that.

The rules are asserted through the real hook (``conftest.pytest_make_parametrize_id`` →
``short_ids.shorten``) and, at the end, end-to-end: a real pytest subprocess collecting a real
parametrized file, twice, and the ids it prints.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import short_ids

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FULL_YML = REPO_ROOT / ".github" / "workflows" / "full.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

LONG = "checks/runtime/fixtures/deeply/nested/case-seventeen-with-a-long-name.json"


class TestTheSwitch:
    def test_off_by_default(self) -> None:
        assert short_ids.enabled({}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_true_values_turn_it_on(self, raw: str) -> None:
        assert short_ids.enabled({short_ids.ENV_VAR: raw}) is True

    @pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "maybe"])
    def test_everything_else_leaves_it_off(self, raw: str) -> None:
        assert short_ids.enabled({short_ids.ENV_VAR: raw}) is False

    def test_the_hook_declines_when_the_switch_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🪤 The whole containment: with the switch off the hook returns ``None``, which is
        how a pytest hook says "not mine" — so the sharded jobs keep the ids their recorded
        durations are keyed on."""
        import conftest

        monkeypatch.delenv(short_ids.ENV_VAR, raising=False)
        assert conftest.pytest_make_parametrize_id(None, LONG, "path") is None

        monkeypatch.setenv(short_ids.ENV_VAR, "1")
        assert conftest.pytest_make_parametrize_id(None, LONG, "path") is not None


class TestTheShorteningRules:
    def test_short_strings_are_left_exactly_as_they_were(self) -> None:
        """An id a reader already recognises must be byte-identical between this job and
        the sharded ones, or two logs of the same failure stop matching."""
        for val in ("", "a", "loopback", "x" * short_ids.LIMIT):
            assert short_ids.shorten(val) is None, val

    def test_a_long_string_is_shortened_below_the_limit(self) -> None:
        got = short_ids.shorten(LONG)
        assert got is not None
        assert len(got) < len(LONG)
        assert len(got) <= short_ids.KEEP + 1 + short_ids.DIGEST
        assert len(got) <= short_ids.LIMIT

    def test_the_last_path_segment_is_what_survives(self) -> None:
        """The meaning of a parametrized path is in the leaf; the prefix is shared by every
        case and identifies none of them."""
        got = short_ids.shorten(LONG)
        assert got is not None
        assert got.startswith("case-seventeen"), got

    def test_non_alphanumerics_collapse_to_single_dashes(self) -> None:
        got = short_ids.shorten("a  b__c///d!!!!e  " + "z" * 40)
        assert got is not None
        assert re.fullmatch(r"[A-Za-z0-9-]+~[0-9a-f]+", got), got
        assert "--" not in got

    def test_two_values_sharing_a_prefix_stay_distinguishable(self) -> None:
        """🔴 The reason there is a digest at all. Truncation collides, pytest resolves a
        collision with a positional suffix, and a failure reported as ``…-0`` versus ``…-1``
        cannot be traced back to a parameter."""
        prefix = "a-very-long-shared-prefix-that-truncates-identically-"
        first = short_ids.shorten(prefix + "one")
        second = short_ids.shorten(prefix + "two")
        assert first is not None and second is not None
        assert first[: short_ids.KEEP] == second[: short_ids.KEEP]
        assert first != second

    def test_the_same_value_always_gets_the_same_id(self) -> None:
        """A failing id copied out of a CI log has to select the same case locally."""
        assert short_ids.shorten(LONG) == short_ids.shorten(LONG)

    def test_undecodable_bytes_do_not_raise(self) -> None:
        raw = b"/tmp/\xff\xfe/" + b"payload-" * 8
        got = short_ids.shorten(raw)
        assert got is not None
        assert re.fullmatch(r"[A-Za-z0-9-]+~[0-9a-f]+", got), got
        assert short_ids.shorten(raw) == got

    @pytest.mark.parametrize("val", [1, 3.5, None, True, ("a", "b"), {"k": "v"}])
    def test_non_strings_keep_pytests_own_id(self, val: object) -> None:
        """pytest names these ``<argname><index>`` already — short, and not this hook's
        business to re-invent."""
        assert short_ids.shorten(val) is None


class TestTheScoping:
    """Which jobs opt in, read from the shipped workflows."""

    def test_only_the_unsharded_job_opts_in(self) -> None:
        """🔴 req 86.1. The coverage leg opts in; no sharded leg does.

        Read out of the workflow files rather than asserted in prose: switching the sharded
        matrix on would invalidate every ``pytest-split`` duration, and that must red here.
        """
        full = FULL_YML.read_text(encoding="utf-8")
        ci = CI_YML.read_text(encoding="utf-8")
        assert short_ids.ENV_VAR not in ci, (
            "a ci.yml job opted into short ids; every test job there is sharded and "
            "pytest-split keys its durations on the id string"
        )
        assert full.count(short_ids.ENV_VAR) == 1, (
            "short ids are declared in more than one full.yml job — only the unsharded "
            "coverage leg may opt in"
        )
        coverage = full.split("\n  coverage:\n", 1)[1].split("\n  install-smoke:", 1)[0]
        assert short_ids.ENV_VAR in coverage, "the coverage job does not opt in"

    def test_the_coverage_job_keeps_its_topology(self) -> None:
        """🪤 "Without changing its execution topology" is half the requirement: one
        process, no splits, coverage still produced."""
        full = FULL_YML.read_text(encoding="utf-8")
        coverage = full.split("\n  coverage:\n", 1)[1].split("\n  install-smoke:", 1)[0]
        assert "--cov=gideon" in coverage and "--cov-report=xml" in coverage
        assert "--splits" not in coverage and "--group" not in coverage
        assert "strategy:" not in coverage and "matrix:" not in coverage


LONG_ID_SUITE = "checks/runtime/test_discover_dismiss_validates_the_tip_id.py"


def test_collection_renames_ids_without_changing_the_set() -> None:
    """End to end, through a real pytest process, on a real file with real long ids.

    ``test_discover_dismiss_validates_the_tip_id.py`` parametrizes over a junk-id corpus that
    includes a 200 KB string — one collected id in this suite is larger than most source
    files, and it is the single biggest contributor to the unsharded job's log. It is
    therefore the honest subject: a synthetic fixture would prove the rules (they are pinned
    above) but not that the hook reaches the ids that actually make the log long.

    The set of collected FUNCTIONS must be identical — the hook renames a parameter, it does
    not add, drop or reorder a case — while the id text gets shorter. Run in subprocesses
    because the hook is read per collection, and this test is itself inside a collection that
    must not change under it.
    """
    target = REPO_ROOT / LONG_ID_SUITE
    assert target.is_file(), f"the long-id suite moved: {LONG_ID_SUITE}"

    def collect(short: bool) -> list[str]:
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "runtime"))
        env.pop(short_ids.ENV_VAR, None)
        if short:
            env[short_ids.ENV_VAR] = "1"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                LONG_ID_SUITE,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=600,
            env=env,
        )
        assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
        return [ln.strip() for ln in proc.stdout.splitlines() if "::" in ln]

    before = collect(short=False)
    after = collect(short=True)

    assert before, "nothing was collected; this leg would be vacuous"
    assert len(after) == len(before)
    assert {i.split("[", 1)[0] for i in after} == {
        i.split("[", 1)[0] for i in before
    }, "the hook changed WHICH tests were collected, not just their ids"
    assert len(set(after)) == len(after), "the short ids collided"
    assert max(len(i) for i in after) < max(len(i) for i in before)
    assert sum(len(i) for i in after) < sum(len(i) for i in before)
