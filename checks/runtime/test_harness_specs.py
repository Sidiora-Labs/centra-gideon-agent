"""Unit tests for the self-development harness spec layer (checks/harness/specs.py, profiles.py).

These lock the spec-schema contract and the profile resolution the CLI relies on. They are
pure/in-memory (no pytest subprocess) — the live reference-resolution round-trip
(``requiredTests`` collection) is exercised separately in test_harness_validate.py so this
module stays fast and hermetic.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from checks.harness import profiles
from checks.harness.specs import (
    KIND_RULE,
    KIND_TASK,
    Spec,
    SpecError,
    load_specs,
    parse_spec,
    validate_all,
    validate_spec,
)


def _write(tmp_path: Path, subdir: str, name: str, text: str) -> Path:
    d = tmp_path / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_parse_spec_reads_frontmatter_and_body(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: my-rule
        type: ai-coding-rule
        statement: a thing must hold
        appliesTo:
          - src/x.py
        source: because it broke once
        ---
        # Body
        why + how
        """,
    )
    spec = parse_spec(p)
    assert spec.kind == KIND_RULE
    assert spec.id == "my-rule"
    assert spec.get_list("appliesTo") == ["src/x.py"]
    assert "why + how" in spec.body


def test_parse_spec_missing_frontmatter_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "rules", "r.md", "# just a body, no frontmatter\n")
    with pytest.raises(SpecError, match="missing YAML frontmatter"):
        parse_spec(p)


def test_parse_spec_unknown_type_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: r
        type: not-a-real-kind
        ---
        body
        """,
    )
    with pytest.raises(SpecError, match="unknown or missing type"):
        parse_spec(p)


def test_get_list_tolerates_scalar(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: r
        type: ai-coding-rule
        statement: s
        appliesTo: src/single.py
        source: x
        ---
        body
        """,
    )
    spec = parse_spec(p)
    assert spec.get_list("appliesTo") == ["src/single.py"]


def _rule(**meta: object) -> Spec:
    base = {
        "id": "r",
        "type": KIND_RULE,
        "statement": "s",
        "appliesTo": ["src/x.py"],
        "source": "src",
    }
    base.update(meta)
    return Spec(
        path=Path("checks/harness/specs/rules/r.md"),
        kind=KIND_RULE,
        meta=base,
        body="why",
    )


def test_valid_rule_has_no_issues() -> None:
    assert validate_spec(_rule()) == []


def test_rule_missing_required_field_flagged() -> None:
    spec = _rule()
    del spec.meta["statement"]
    issues = validate_spec(spec)
    assert any("statement" in i.message and i.level == "error" for i in issues)


def test_rule_without_body_flagged() -> None:
    spec = _rule()
    spec.body = "   \n"
    issues = validate_spec(spec)
    assert any("body" in i.message for i in issues)


def test_malformed_id_flagged() -> None:
    spec = _rule(id="has spaces")
    issues = validate_spec(spec)
    assert any("malformed id" in i.message for i in issues)


def test_type_directory_mismatch_flagged() -> None:
    spec = Spec(
        path=Path("checks/harness/specs/rules/mis.md"),
        kind=KIND_TASK,
        meta={
            "id": "t",
            "type": KIND_TASK,
            "title": "t",
            "intent": "i",
            "touchedAreas": ["src/x.py"],
            "acceptance": {"negative": ["x"]},
        },
        body="",
    )
    issues = validate_spec(spec)
    assert any("filed under" in i.message for i in issues)


def test_task_requires_negative_acceptance() -> None:
    spec = Spec(
        path=Path("checks/harness/specs/tasks/t.md"),
        kind=KIND_TASK,
        meta={
            "id": "T9.1",
            "type": KIND_TASK,
            "title": "t",
            "intent": "i",
            "touchedAreas": ["src/x.py"],
            "acceptance": {"positive": ["did the thing"]},
        },
        body="",
    )
    issues = validate_spec(spec)
    assert any("negative" in i.message for i in issues)


def test_unknown_requiredrules_reference_flagged() -> None:
    spec = _rule(requiredRules=["no-such-rule"])
    issues = validate_spec(spec, known_ids={"r"})
    assert any("unknown spec id" in i.message for i in issues)


def test_duplicate_ids_flagged() -> None:
    a = _rule(id="dup")
    b = _rule(id="dup")
    b.path = Path("checks/harness/specs/rules/b.md")
    issues = validate_all([a, b])
    assert any("duplicate id" in i.message for i in issues)


def test_the_commit_watcher_retirement_rule_ships() -> None:
    """The harness must CARRY the retirement rule, not merely be able to enforce one.

    A scanner check with no rule spec behind it is a check nobody can find: `explain` lists
    rules, and the rule is where the reason and the expiry condition live.
    """
    rules = {s.id: s for s in load_specs() if s.kind == KIND_RULE}
    rule = rules.get("no-periodic-commit-watcher")
    assert rule is not None, f"the retirement rule is not shipped: {sorted(rules)}"
    assert validate_spec(rule) == []
    assert rule.meta["scanner"] == "no-periodic-commit-watcher"
    assert str(rule.meta.get("expiry_condition", "")).strip(), (
        "a prohibition with no stated expiry condition cannot be retired on evidence "
        "later — every shipped rule here carries one"
    )
    applies = rule.get_list("appliesTo")
    assert any("selfqa" in path for path in applies), applies


def test_profile_registry_has_core_profiles() -> None:
    names = profiles.profile_names()
    assert {"fast", "web", "replay", "full", "scan"} <= names


def test_resolve_commands_substitutes_tests() -> None:
    cmds = profiles.resolve_commands(["fast"], tests=["checks/runtime/test_a.py::t1"])
    assert len(cmds) == 1
    assert cmds[0].command.endswith("checks/runtime/test_a.py::t1")
    assert cmds[0].profile == "fast"


def test_resolve_commands_skips_needs_tests_when_none() -> None:
    assert profiles.resolve_commands(["fast"], tests=[]) == []


def test_resolve_commands_dedups_and_ignores_unknown() -> None:
    cmds = profiles.resolve_commands(["web", "web", "does-not-exist"])
    commands = [c.command for c in cmds]
    assert commands == ["npm run typecheck:web", "npm run test:web"]
