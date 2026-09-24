import json
from pathlib import Path

import pytest

from gideon.extensions.apps.quality import (
    token_lint_bundle,
    token_lint_file,
    token_lint_source,
    verify_app,
)

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/token_lint_cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_token_lint_corpus(tmp_path, case):
    source = tmp_path / "panel.tsx"
    source.write_text(case["source"], encoding="utf-8")
    result = token_lint_source(case["source"])
    assert result.end_state == case.get("end_state", "code")
    hits = token_lint_file(source)
    assert hits == result.violations
    actual = [
        [int(hit.split(": ")[0]), hit.split(": ")[1].split(" — ")[0]] for hit in hits
    ]
    assert actual == case["expected"]
    if case["name"].startswith("gap_"):
        assert actual != case["intended"]
    assert token_lint_bundle(tmp_path) == ({"panel.tsx": hits} if hits else {})


def test_comment_state_is_local_to_file(tmp_path):
    (tmp_path / "a.ts").write_text("/* unfinished", encoding="utf-8")
    (tmp_path / "b.ts").write_text("const color = '#fff'", encoding="utf-8")
    assert "b.ts" in token_lint_bundle(tmp_path)


def test_unreadable_eof_refuses_design_system_claim(tmp_path):
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "lexical-app",
                "version": "1.0.0",
                "displayName": "Lexical App",
                "description": "Token source validation",
                "quality": {"designSystem": "v2"},
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "panel.tsx"
    source.write_text("/* unfinished\n#fff", encoding="utf-8")
    violations = verify_app(tmp_path)
    assert len(violations) == 1
    assert violations[0].axis == "designSystem"
    assert "unreadable EOF: block_comment" in violations[0].reason
    source.write_text("/* closed\n#fff */", encoding="utf-8")
    assert verify_app(tmp_path) == []


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.get("end_state", "code") != "code"],
    ids=lambda case: case["name"],
)
def test_unreadable_corpus_refuses_bundle_claim(tmp_path, case):
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "eof-app",
                "version": "1.0.0",
                "displayName": "EOF App",
                "description": "Lexical EOF validation",
                "quality": {"designSystem": "v2"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "panel.tsx").write_text(case["source"], encoding="utf-8")
    violations = verify_app(tmp_path)
    assert len(violations) == 1
    assert violations[0].axis == "designSystem"
    assert "token-lint fails" in violations[0].reason
    assert any(
        "unreadable EOF" in hit for hit in token_lint_file(tmp_path / "panel.tsx")
    )


def test_non_gap_corpus_has_real_clean_and_rejected_bundle_floors(tmp_path):
    normal_cases = [case for case in CASES if not case["name"].startswith("gap_")]
    assert len(CASES) - len(normal_cases) >= 2
    clean = rejected = 0
    for index, case in enumerate(normal_cases):
        bundle = tmp_path / str(index)
        bundle.mkdir()
        (bundle / "app.json").write_text(
            json.dumps(
                {
                    "name": "floor-app",
                    "version": "1.0.0",
                    "displayName": "Floor App",
                    "description": "Token lint corpus",
                    "quality": {"designSystem": "v2"},
                }
            ),
            encoding="utf-8",
        )
        (bundle / "panel.tsx").write_text(case["source"], encoding="utf-8")
        violations = verify_app(bundle)
        assert bool(violations) == bool(case["expected"]), case["name"]
        if violations:
            assert all(violation.axis == "designSystem" for violation in violations)
            rejected += 1
        else:
            clean += 1
    assert clean >= 10
    assert rejected >= 15
