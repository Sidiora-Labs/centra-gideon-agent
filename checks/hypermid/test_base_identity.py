from __future__ import annotations

import json
from pathlib import Path

import pytest

from gideon.hypermid.foundation import (
    Admission,
    ContractViolation,
    Cursor,
    Digest,
    EffectState,
    Error,
    Id,
    Scope,
    Trace,
    normalize_windows_spelling,
    resolve_home,
    resolve_project_root,
)


ROOT = Path(__file__).resolve().parents[2]
COMMON_SCHEMA = ROOT / "spec/hypermid/schemas/common.schema.json"
FOUNDATION_SCHEMA = ROOT / "spec/hypermid/schemas/foundation.schema.json"


def _scope(project: str = "project-a") -> Scope:
    return Scope(Id("owner-1"), Id(project), Id("workspace-1"))


def test_common_wire_types_enforce_the_shared_schema_bounds() -> None:
    scope = Scope.from_wire(_scope().to_wire())
    trace = Trace.from_wire(Trace(Id("trace-1"), Id("request-1")).to_wire())
    cursor = Cursor.from_wire(Cursor(1, 0).to_wire())
    error = Error.from_wire(
        Error("STORE_BUSY", "try later", True, 250, EffectState.NOT_STARTED).to_wire()
    )

    assert scope.project_id == "project-a"
    assert trace.request_id == "request-1"
    assert cursor.next() == Cursor(1, 1)
    assert error.effect_state is EffectState.NOT_STARTED
    assert len(Digest.sha256(b"hypermid")) == 64
    with pytest.raises(ContractViolation):
        Id("bad id")
    with pytest.raises(ContractViolation):
        Cursor(0, 0)
    with pytest.raises(ContractViolation):
        Error.from_wire({"code": "BAD", "message": "bad", "retryable": False, "extra": 1})


def test_live_aliases_converge_and_separate_roots_remain_distinct(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(first, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    direct = resolve_project_root(_scope(), first / ".", Admission.LIVE)
    through_alias = resolve_project_root(_scope(), alias, Admission.LIVE)
    worktree = resolve_project_root(_scope("project-b"), second, Admission.LIVE)

    assert direct.canonical_path == through_alias.canonical_path
    assert direct.canonical_path != worktree.canonical_path
    assert direct.scope.project_id != worktree.scope.project_id


def test_windows_verbatim_prefix_drive_case_and_trailing_separator_converge() -> None:
    expected = r"C:\work\project"
    assert normalize_windows_spelling(r"\\?\c:\work\project\\") == expected
    assert normalize_windows_spelling(r"c:/work/project/") == expected
    assert (
        normalize_windows_spelling(r"\\?\UNC\server\share\project\\")
        == r"\\server\share\project"
    )


def test_recovery_only_resolves_missing_tail_but_cannot_admit_state(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "project"
    with pytest.raises(FileNotFoundError):
        resolve_project_root(_scope(), missing, Admission.LIVE)

    recovered = resolve_project_root(_scope(), missing, Admission.RECOVERY_ONLY)
    assert recovered.canonical_path == tmp_path.resolve() / "missing" / "project"
    assert recovered.missing_components == 2
    assert not recovered.permits_new_state
    with pytest.raises(ContractViolation, match="cannot create durable state"):
        recovered.require_live()


def test_home_resolution_reports_relative_values_and_empty_values_use_fallback() -> None:
    relative = resolve_home("state", "/fallback")
    fallback = resolve_home("", "/fallback")
    assert relative.path == Path("state")
    assert relative.is_relative
    assert fallback.path == Path("/fallback")
    assert not fallback.is_relative


def test_foundation_schema_references_all_common_types_without_redefining_them() -> None:
    common = json.loads(COMMON_SCHEMA.read_text(encoding="utf-8"))
    foundation_text = FOUNDATION_SCHEMA.read_text(encoding="utf-8")
    foundation = json.loads(foundation_text)
    common_names = {"Id", "Scope", "Digest", "Cursor", "Error", "Trace"}
    referenced = {
        name
        for name in common_names
        if f'common.schema.json#/$defs/{name}' in foundation_text
    }

    assert common_names <= set(common["$defs"])
    assert referenced == common_names
    assert common_names.isdisjoint(foundation["$defs"])
    assert foundation["$schema"] == "https://json-schema.org/draft/2020-12/schema"
