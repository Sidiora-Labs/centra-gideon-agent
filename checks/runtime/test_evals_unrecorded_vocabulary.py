"""The rail for ONE vocabulary of "unrecorded" across the eval report (#2540 / #2561 / #2562).

Three fields in one report collapsed *absent* into *zero/none*, and the fix was one word
(``unrecorded``, owned by :mod:`gideon.assurance.evals.provenance` and mirrored in
``apps/console/src/lib/unrecorded.ts``) plus the rule that every consumer of a field that CAN be unrecorded
must consult the fact that says whether it was. This file is what makes the second half stick:

1. :func:`test_the_consumer_set_is_pinned` — the derived {file: guarded fields} map must equal the
   declared one. A NEW consumer, or an existing file starting to read a new guarded field, reds
   here and its author has to come read the rule.
2. :func:`test_every_guarded_read_consults_the_not_recorded_fact` — each consumer in the
   learning-bench chain must carry its required guard token.
3. :func:`test_one_spelling_of_the_word` — Python, TypeScript and the dev harness spell it the
   same, so the "one vocabulary" claim is checked rather than promised.

**Enumerate with git, parse with Python.** ``git ls-files`` lists the tracked files and this module
reads only the paths it returns. Nothing here walks a directory: a broad traversal from a bare root
resolves into credential-holding paths and is refused by host policy, and swapping the reader for
``glob``/``os.walk`` hits the same family.

**Both vacuity floors.** :func:`test_the_detector_reds_on_a_planted_violation` proves the predicate
can fail, and :func:`test_the_detector_is_not_green_from_matching_nothing` proves it is not green by
matching zero sites — with an absolute lower bound and a hand-written list of literal sites, neither
of which comes from the same parse that derives the expected map.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from gideon.assurance.evals import provenance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


THE_WORD = "unrecorded"

WORD_DECLARATIONS: dict[str, str] = {
    "runtime/gideon/assurance/evals/provenance.py": f'UNRECORDED = "{THE_WORD}"',
    "apps/console/src/lib/unrecorded.ts": f"export const UNRECORDED = '{THE_WORD}'",
    "checks/harness/learning_verdict.py": f'UNRECORDED = "{THE_WORD}"',
}


GUARDED: dict[str, dict] = {
    "spend.tokens": {
        "patterns": (
            r'\bget\(\s*"tokens"\s*\)',
            r'\[\s*"tokens"\s*\]',
            r"\bspend\.tokens\b",
        ),
        "dirs": ("runtime/gideon/assurance/evals", "scripts", "harness"),
    },
    "token_ratio": {
        "patterns": (
            r'\bget\(\s*"token_ratio"\s*\)',
            r'\[\s*"token_ratio"\s*\]',
            r"\.token_ratio\b",
        ),
        "dirs": ("runtime/gideon", "scripts", "harness", "apps/console/src"),
    },
    "provider_binding": {
        "patterns": (
            r'\bget\(\s*"provider_binding"\s*\)',
            r'\[\s*"provider_binding"\s*\]',
            r"\.provider_binding\b",
            r"""["']provider_binding["']\s+in\b""",
        ),
        "dirs": ("runtime/gideon", "scripts", "harness", "apps/console/src"),
    },
    "cell_model": {
        "patterns": (
            r'\bget\(\s*"cell_model_f\w*"\s*\)',
            r"\.cell_model_f\w*\b",
            r'\[\s*"cell_model_f\w*"\s*\]',
        ),
        "dirs": ("runtime/gideon", "scripts", "harness", "apps/console/src"),
    },
}

GUARD_TOKENS: dict[str, tuple[str, ...]] = {
    "spend.tokens": ("tokens_recorded",),
    "token_ratio": ("tokens_recorded", "tokensUnrecorded"),
    "provider_binding": ("report_schema", "PROVENANCE_SCHEMA", "provenanceRecorded"),
    "cell_model": ("UNRECORDED", "NO_MODEL", THE_WORD, "cell_model_fingerprint"),
}

EXPECTED_CONSUMERS: dict[str, dict[str, str | None]] = {
    "runtime/gideon/assurance/evals/gate.py": {
        "spend.tokens": "cell_spend/_accumulate sum the cells' token counts",
    },
    "tooling/scripts/learning_benchmark.py": {
        "spend.tokens": "_verdict_for_task builds the §4 token denominator",
        "provider_binding": "writes the report's provenance and its schema",
        "cell_model": "writes the pin's cells-reach fields into the report",
    },
    "checks/harness/learning_verdict.py": {
        "spend.tokens": "the token gate this design refuses when the count is unrecorded",
        "token_ratio": "produces the published ratio",
    },
    "checks/harness/fanout_measure.py": {"spend.tokens": None, "token_ratio": None},
    "checks/harness/cli.py": {"token_ratio": None},
    "runtime/gideon/assurance/evals/child.py": {
        "provider_binding": None,
    },
    "runtime/gideon/assurance/evals/runner.py": {
        "provider_binding": None,
    },
    "runtime/gideon/assurance/evals/learning_bench.py": {
        "provider_binding": "report_schema / provenance_recorded are declared here",
    },
    "runtime/gideon/assurance/evals/pinning.py": {
        "cell_model": "owns cell_model_fp's three states"
    },
    "runtime/gideon/assurance/evals/store.py": {
        "cell_model": "the appended results.tsv column"
    },
    "apps/console/src/lib/unrecorded.ts": {
        "provider_binding": "declares PROVENANCE_SCHEMA — this file IS the guard",
    },
    "apps/console/src/lib/api.ts": {
        "provider_binding": "the BenchmarkReport type documents the three states",
    },
    "apps/console/src/pages/learning/BenchmarkPanel.tsx": {
        "provider_binding": "renders which of the three states the report is in",
        "token_ratio": "renders the ratio, or 'not recorded' in its place",
        "cell_model": "renders what the cells could reach beside the home's binding",
    },
}


def _tracked(directory: str) -> list[str]:
    """Tracked source paths under ``directory``. ``git ls-files`` recurses on its own."""
    listed = subprocess.run(
        ["git", "ls-files", directory],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        rel
        for rel in listed
        if rel.endswith((".py", ".ts", ".tsx"))
        and ".test." not in rel
        and "/tests/" not in rel
    ]


def _reads(text: str, field: str) -> bool:
    return any(re.search(p, text) for p in GUARDED[field]["patterns"])


def _derive() -> dict[str, set[str]]:
    """{path: {guarded field}} over the tracked sources, by the scoped patterns above."""
    found: dict[str, set[str]] = {}
    for field, spec in GUARDED.items():
        seen: set[str] = set()
        for directory in spec["dirs"]:
            seen.update(_tracked(directory))
        for rel in sorted(seen):
            if _reads((REPO_ROOT / rel).read_text(encoding="utf-8"), field):
                found.setdefault(rel, set()).add(field)
    return found


def test_the_consumer_set_is_pinned():
    """A NEW consumer of a field that can be UNRECORDED reds here.

    That is the whole rail: the failure message sends the author to `EXPECTED_CONSUMERS`, where the
    rule and every existing exemption's REASON are written down, rather than letting a fourth site
    quietly reinvent `.get("tokens") or 0`.
    """
    derived = _derive()
    expected = {path: set(fields) for path, fields in EXPECTED_CONSUMERS.items()}
    new_files = sorted(set(derived) - set(expected))
    gone = sorted(set(expected) - set(derived))
    assert not new_files, (
        f"new consumer(s) of an unrecordable field: {new_files}. Add each to "
        "EXPECTED_CONSUMERS with the guard it consults, or the reason it needs none — and read "
        "gideon.assurance.evals.provenance first."
    )
    assert (
        not gone
    ), f"EXPECTED_CONSUMERS names file(s) that no longer read the field: {gone}"
    for path in sorted(expected):
        assert derived[path] == expected[path], (
            f"{path} reads {sorted(derived[path])} but EXPECTED_CONSUMERS declares "
            f"{sorted(expected[path])}"
        )


def test_every_guarded_read_consults_the_not_recorded_fact():
    """Reading the value without consulting the fact is the defect, at whatever site it happens."""
    offenders: list[str] = []
    for path, fields in EXPECTED_CONSUMERS.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        for field, reason in fields.items():
            if reason is None:
                continue
            if not any(token in text for token in GUARD_TOKENS[field]):
                offenders.append(
                    f"{path} reads {field} without any of {GUARD_TOKENS[field]}"
                )
    assert not offenders, "; ".join(offenders)


def _code_lines(text: str) -> str:
    """``text`` with whole-line comments dropped, so a rule about CODE is not tripped by prose.

    The panel's own docstring names the workaround it replaced, which is worth keeping — a reader
    who finds the key-presence check in an old branch needs to know it was deliberate to remove.
    """
    kept = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "#"))
    ]
    return "\n".join(kept)


def test_the_panel_no_longer_infers_the_schema_from_key_presence():
    """#2562's own words: the `'provider_binding' in report` workaround "leaves every other future
    consumer to rediscover the same trick", which made the panel a second owner of the fact. It is
    gone from the CODE, and the schema-reading helper is what replaced it."""
    panel = (
        REPO_ROOT / "apps/console/src/pages/learning/BenchmarkPanel.tsx"
    ).read_text(encoding="utf-8")
    code = _code_lines(panel)
    assert "'provider_binding' in report" not in code
    assert '"provider_binding" in report' not in code
    assert "provenanceRecorded(report)" in code
    assert "recorded" in code and "binding" in code


@pytest.mark.parametrize("path,declaration", sorted(WORD_DECLARATIONS.items()))
def test_one_spelling_of_the_word(path: str, declaration: str):
    """Three language surfaces, one spelling. A synonym here would be a second vocabulary."""
    assert declaration in (REPO_ROOT / path).read_text(encoding="utf-8")


def test_the_python_and_typescript_halves_agree_on_the_provenance_schema():
    """One number, declared twice because one is TypeScript, and checked so it stays one number."""
    from gideon.assurance.evals import learning_bench

    ts = (REPO_ROOT / "apps/console/src/lib/unrecorded.ts").read_text(encoding="utf-8")
    match = re.search(r"export const PROVENANCE_SCHEMA = (\d+)", ts)
    assert match, "apps/console/src/lib/unrecorded.ts must declare PROVENANCE_SCHEMA"
    assert int(match.group(1)) == learning_bench.PROVENANCE_SCHEMA
    assert learning_bench.REPORT_SCHEMA >= learning_bench.PROVENANCE_SCHEMA


def test_the_word_is_not_priced():
    """#2630's ruling, asserted rather than remembered.

    ``priced`` already exists in ``loop_spend``, ``usage_ledger`` and ``run_totals`` and means "a
    cost is unknown". Widening it to cover token counts would force a row with a known cost and an
    unknown token count to pick one value and lie to one of its two readers, which is exactly the
    shape a cell whose provider omitted ``usage`` is in — its ``dollars_est`` is real and only its
    token count is absent.
    """
    for path in (
        "runtime/gideon/assurance/evals/provenance.py",
        "apps/console/src/lib/unrecorded.ts",
    ):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert THE_WORD in text
    used = re.compile(r"""(["']priced["']|\.priced\b|\bpriced\s*[:=])""")
    for path in (
        "runtime/gideon/assurance/evals/child.py",
        "runtime/gideon/assurance/evals/gate.py",
        "runtime/gideon/assurance/evals/pinning.py",
        "runtime/gideon/assurance/evals/provenance.py",
        "checks/harness/learning_verdict.py",
        "tooling/scripts/learning_benchmark.py",
    ):
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert not used.search(text), f"{path} must not reach for `priced` (see #2630)"


def test_the_eval_spend_dict_cannot_leave_the_scoped_dirs():
    """The scope `spend.tokens` is detected in, justified rather than assumed.

    The dict has exactly two producers — ``child.spend_from_home`` and ``gate.cell_spend`` — and a
    reader outside the scoped directories would have to name one of them. Nothing does, so scoping
    the pattern is not a hole; if that changes, this reds and the scope has to widen with it.
    """
    producers = ("spend_from_home", "cell_spend")
    scoped = {"runtime/gideon/assurance/evals", "scripts", "harness"}
    strays: list[str] = []
    for rel in _tracked("runtime/gideon") + _tracked("apps/console/src"):
        if any(rel.startswith(prefix) for prefix in scoped):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if any(name in text for name in producers):
            strays.append(rel)
    assert not strays, (
        f"{strays} reach an eval-spend producer from outside {sorted(scoped)} — widen GUARDED"
        "['spend.tokens']['dirs'] to cover them"
    )


def test_the_detector_reds_on_a_planted_violation():
    """FLOOR ONE: the predicate can fail. A source that reads the value and never mentions the
    fact is detected as a read and rejected as unguarded."""
    planted = 'total += int(spend.get("tokens") or 0)  # no mention of the fact\n'
    assert _reads(
        planted, "spend.tokens"
    ), "the read pattern must match the planted violation"
    assert not any(
        token in planted for token in GUARD_TOKENS["spend.tokens"]
    ), "the planted violation must carry no guard token, or this floor proves nothing"
    guarded = (
        'if not spend.get("tokens_recorded"):\n'
        "    recorded = False\n"
        "else:\n"
        '    total += int(spend.get("tokens") or 0)\n'
    )
    assert _reads(guarded, "spend.tokens")
    assert any(token in guarded for token in GUARD_TOKENS["spend.tokens"])


def test_the_detector_is_not_green_from_matching_nothing():
    """FLOOR TWO, and the direction that actually goes wrong: a rail that matches zero sites passes
    every assertion above.

    The bound and the site list are HAND-WRITTEN, not produced by ``_derive`` — a floor computed
    from the same parse it is checking would be satisfied by a parse that matched nothing.
    """
    derived = _derive()
    assert (
        len(derived) >= 13
    ), f"the detector found only {len(derived)} consumer file(s)"
    total_reads = sum(len(fields) for fields in derived.values())
    assert total_reads >= 17, f"the detector found only {total_reads} guarded read(s)"
    assert len({f for fields in derived.values() for f in fields}) == 4

    for path, field in (
        ("runtime/gideon/assurance/evals/gate.py", "spend.tokens"),
        ("tooling/scripts/learning_benchmark.py", "spend.tokens"),
        ("tooling/scripts/learning_benchmark.py", "provider_binding"),
        ("tooling/scripts/learning_benchmark.py", "cell_model"),
        ("checks/harness/learning_verdict.py", "spend.tokens"),
        ("checks/harness/learning_verdict.py", "token_ratio"),
        ("checks/harness/fanout_measure.py", "token_ratio"),
        ("runtime/gideon/assurance/evals/pinning.py", "cell_model"),
        ("runtime/gideon/assurance/evals/store.py", "cell_model"),
        ("runtime/gideon/assurance/evals/learning_bench.py", "provider_binding"),
        ("apps/console/src/pages/learning/BenchmarkPanel.tsx", "token_ratio"),
        ("apps/console/src/pages/learning/BenchmarkPanel.tsx", "cell_model"),
        ("apps/console/src/lib/api.ts", "provider_binding"),
    ):
        assert field in derived.get(
            path, set()
        ), f"the detector missed {field} in {path}"


def test_git_ls_files_is_what_enumerates():
    """The enumeration itself, asserted: a traversal from a bare root is refused by host policy, and
    a rail that silently stopped listing files would pass every assertion above."""
    listed = _tracked("runtime/gideon/assurance/evals")
    assert "runtime/gideon/assurance/evals/provenance.py" in listed
    assert "runtime/gideon/assurance/evals/child.py" in listed
    assert len(listed) >= 20, f"git ls-files returned only {len(listed)} eval source(s)"
    assert not any(".test." in rel for rel in listed)


def test_state_of_separates_absent_from_recorded_nothing():
    assert provenance.state_of({}, "x") == provenance.UNRECORDED
    assert provenance.state_of(None, "x") == provenance.UNRECORDED
    assert provenance.state_of({"x": None}, "x") == provenance.UNRECORDED
    assert provenance.state_of({"x": {}}, "x") == provenance.RECORDED_NONE
    assert provenance.state_of({"x": []}, "x") == provenance.RECORDED_NONE
    assert provenance.state_of({"x": {"a": "b"}}, "x") == provenance.RECORDED
    assert provenance.state_of({"x": 0}, "x") == provenance.RECORDED
    assert provenance.state_of({"x": False}, "x") == provenance.RECORDED
    assert provenance.is_unrecorded({}, "x") is True
    assert provenance.is_unrecorded({"x": 0}, "x") is False
    assert set(provenance.STATES) == {
        provenance.RECORDED,
        provenance.RECORDED_NONE,
        provenance.UNRECORDED,
    }


def test_digest_cell_never_renders_an_absence_as_an_empty_string():
    """A TSV cell has three possible meanings and an empty one is already taken by "the column did
    not exist when this row was written"."""
    assert provenance.digest_cell("abc123abc123", recorded=True) == "abc123abc123"
    assert provenance.digest_cell("", recorded=True) == provenance.NO_MODEL
    assert provenance.digest_cell(None, recorded=True) == provenance.NO_MODEL
    assert (
        provenance.digest_cell("abc123abc123", recorded=False) == provenance.UNRECORDED
    )
    assert provenance.NO_MODEL != provenance.UNRECORDED
    for word in (provenance.NO_MODEL, provenance.UNRECORDED):
        assert not re.fullmatch(r"[0-9a-f]{12}", word)
