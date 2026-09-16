"""Integration tests for the harness reference-resolution + CLI.

These exercise the live pytest ``--collect-only`` round-trip against the REAL repo spec
set, so they double as a guard that the shipped seed specs stay valid. They are heavier
than test_harness_specs.py (they shell out to pytest once), but there are only a few.
"""

from __future__ import annotations

from pathlib import Path

from checks.harness import cli
from checks.harness.specs import load_specs, validate_all
from checks.harness.validate_refs import (
    _file_defines,
    _node_id_matches,
    _split_node_id,
    validate_refs,
)


def test_the_interpreter_is_absolute_and_launchable_from_any_cwd() -> None:
    """The whole-suite collection must launch from a worktree, not just the checkout.

    ``VENV_PY`` was the cwd-relative ``".venv/bin/python"``. A git worktree has no
    ``.venv``, so ``collect_test_ids`` failed with ``[Errno 2]``, ``validate_refs``
    reported "could not collect the test suite", and the three tests below failed in
    every worktree — indistinguishable from a genuinely dangling spec reference.
    """
    from checks.harness.profiles import HARNESS_PY

    assert Path(HARNESS_PY).is_absolute()
    assert Path(HARNESS_PY).is_file()


def test_the_interpreter_prefers_this_trees_venv_then_the_running_process(
    tmp_path: Path,
) -> None:
    """Both branches of the resolution, so neither can rot unnoticed."""
    import sys

    from checks.harness.profiles import resolve_python

    assert resolve_python(tmp_path) == sys.executable

    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n")
    assert resolve_python(tmp_path) == str(venv_py)


def test_shipped_specs_validate_shape() -> None:
    """Every seed spec that ships in checks/harness/specs/ is shape-valid."""
    specs = load_specs()
    assert specs, "no seed specs found — the harness ships a starter set"
    errors = [i for i in validate_all(specs) if i.level == "error"]
    assert not errors, "shipped specs have shape errors:\n" + "\n".join(
        f"  {i.path}: {i.message}" for i in errors
    )


def test_shipped_specs_test_references_resolve() -> None:
    """Every requiredTests node-id in the shipped specs resolves (collects OR is defined).

    This is the spec-rot guard from Success Criterion #1: if a test is renamed and a spec
    still points at the old node-id, this fails.
    """
    specs = load_specs()
    errors = [i for i in validate_refs(specs, check_tests=True) if i.level == "error"]
    assert not errors, "shipped specs have dangling references:\n" + "\n".join(
        f"  {i.path}: {i.message}" for i in errors
    )


def test_split_node_id_strips_class_and_params() -> None:
    assert _split_node_id("checks/runtime/x.py::Klass::test_fn[a-b]") == (
        "checks/runtime/x.py",
        "test_fn",
    )
    assert _split_node_id("checks/runtime/x.py::test_fn") == (
        "checks/runtime/x.py",
        "test_fn",
    )
    assert _split_node_id("checks/runtime/x.py") == ("checks/runtime/x.py", "")


def test_file_defines_finds_real_function() -> None:
    assert _file_defines(
        "checks/runtime/test_apps_import_boundary.py", "test_apps_only_import_sdk"
    )
    assert not _file_defines(
        "checks/runtime/test_apps_import_boundary.py", "test_not_a_real_name"
    )


def test_node_id_matches_ast_fallback_for_skipped_module() -> None:
    node = "checks/runtime/test_apps_import_boundary.py::test_apps_only_import_sdk"
    assert _node_id_matches(node, collected=set())
    assert not _node_id_matches(
        "checks/runtime/test_apps_import_boundary.py::test_ghost", collected=set()
    )


def test_dangling_node_id_is_flagged(tmp_path: Path) -> None:
    """A spec pointing at a non-existent test node-id fails reference resolution."""
    from checks.harness.specs import KIND_RULE, Spec

    spec = Spec(
        path=tmp_path / "r.md",
        kind=KIND_RULE,
        meta={
            "id": "r",
            "type": KIND_RULE,
            "statement": "s",
            "appliesTo": ["src/x.py"],
            "source": "x",
            "requiredTests": [
                "checks/runtime/test_config_roundtrip.py::test_THIS_DOES_NOT_EXIST"
            ],
        },
        body="why",
    )
    errors = [i for i in validate_refs([spec], check_tests=True) if i.level == "error"]
    assert any("does not resolve" in i.message for i in errors)


def test_cli_validate_returns_zero_on_shipped_specs() -> None:
    assert cli.main(["validate"]) == 0


def test_cli_explain_known_task_succeeds() -> None:
    assert cli.main(["explain", "T1.example-config-field"]) == 0


def test_cli_explain_unknown_task_errors() -> None:
    assert cli.main(["explain", "no-such-task"]) == 2


def test_cli_scan_whole_tree_returns_zero() -> None:
    assert cli.main(["scan"]) == 0


def test_cli_validate_has_no_scanner_warnings_now() -> None:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["validate", "--fast"])
    assert rc == 0
    assert "0 warning(s)" in buf.getvalue()
