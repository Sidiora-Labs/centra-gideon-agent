from __future__ import annotations

import os
from pathlib import Path

import pytest

from gideon.security.security import is_sensitive_path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("GIDEON_HOME", str(root / ".gideon"))
    return root


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sentinel", encoding="utf-8")
    return path


def test_direct_and_dot_segment_paths_are_protected(home: Path) -> None:
    key = _file(home / ".ssh" / "id_ed25519")
    assert is_sensitive_path(str(key))
    assert is_sensitive_path(str(home / "notes" / ".." / ".ssh" / "id_ed25519"))


def test_symlinked_protected_root_and_resolved_alias_are_protected(home: Path) -> None:
    target = _file(home / "dotfiles" / "aws" / "credentials").parent
    (home / ".aws").symlink_to(target, target_is_directory=True)
    alias = target / "credentials"
    assert is_sensitive_path(str(home / ".aws" / "credentials"))
    assert is_sensitive_path(str(alias))
    assert is_sensitive_path(str(home / ".aws" / "missing" / ".." / "credentials"))


def test_symlinked_entry_inside_protected_root_is_protected(home: Path) -> None:
    key = _file(home / "dotfiles" / "ssh" / "id_rsa")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").symlink_to(key)
    assert is_sensitive_path(str(home / ".ssh" / "id_rsa"))
    assert is_sensitive_path(os.path.realpath(home / ".ssh" / "id_rsa"))


def test_case_variants_and_invalid_paths_fail_closed(home: Path) -> None:
    _file(home / ".ssh" / "id_rsa")
    assert is_sensitive_path(str(home / ".SSH" / "ID_RSA"))
    assert is_sensitive_path("bad\x00path")


def test_unrelated_dotfiles_remain_readable(home: Path) -> None:
    readme = _file(home / "dotfiles" / "README.md")
    assert not is_sensitive_path(str(readme))
