"""Tests for the git worktree manager backing parallel task execution.

Exercises real git against a temp repo: capability detection, base-commit
bootstrap on a fresh init, worktree add/merge/remove, and the no-git fallback.
Skipped entirely if git isn't installed.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from gideon.automation.loop import worktree as wt

pytestmark = pytest.mark.skipif(not wt.git_available(), reason="git not installed")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    """Root worktrees under a temp config dir (Gideon's working dir), NOT inside the
    test repo — mirrors production where worktrees live outside the user's checkout."""
    monkeypatch.setattr(
        "gideon.core.config.loader.config_dir", lambda: tmp_path / "gideon"
    )
    return tmp_path


def _init_repo(path: str, *, commit: bool = True) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if commit:
        (open(os.path.join(path, "README.md"), "w")).write("# repo\n")
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "init",
            ],
            cwd=path,
            check=True,
        )


class TestCapability:
    def test_non_repo_dir_is_not_parallelizable(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        assert wt.is_git_repo(str(d)) is False
        assert wt.can_parallelize(str(d)) is False

    def test_repo_dir_is_parallelizable(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        assert wt.is_git_repo(str(d)) is True
        assert wt.can_parallelize(str(d)) is True

    def test_empty_path_not_parallelizable(self):
        assert wt.can_parallelize("") is False


class TestWorktreeLifecycle:
    def test_ensure_base_commit_on_fresh_init(self, tmp_path):
        d = tmp_path / "fresh"
        d.mkdir()
        _init_repo(str(d), commit=False)
        assert wt.ensure_base_commit(str(d)) is True
        rc = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=str(d)
        ).returncode
        assert rc == 0

    def test_add_and_remove_worktree(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-abc")
        assert path and os.path.isdir(path)
        assert os.path.basename(path) == "t-abc"
        # worktrees must live OUTSIDE the user's workspace (under Gideon's dir), so a
        assert not os.path.abspath(path).startswith(os.path.abspath(str(d)) + os.sep)
        assert not os.path.isdir(os.path.join(str(d), ".gideon-worktrees"))
        listing = subprocess.run(
            ["git", "worktree", "list"], cwd=str(d), capture_output=True, text=True
        ).stdout
        assert "t-abc" in listing
        wt.remove_worktree(str(d), "t-abc")
        assert not os.path.isdir(path)

    def test_unsafe_task_id_is_refused(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        for bad in ["../escape", "a/b", "x*", "..", "with space", ""]:
            with pytest.raises(ValueError):
                wt.worktree_path(str(d), bad)
            assert wt.add_worktree(str(d), bad) is None
            assert wt.branch_exists(str(d), bad) is False
            wt.remove_worktree(str(d), bad)
            assert wt.merge_worktree(str(d), bad).ok is False
        assert not os.path.isdir(tmp_path / "gideon" / "escape")

    def test_per_project_root_isolates_shared_workspace(self, tmp_path):
        d = tmp_path / "shared"
        d.mkdir()
        _init_repo(str(d))
        pa = wt.add_worktree(str(d), "t-1", "p-aaaa1111")
        pb = wt.add_worktree(str(d), "t-2", "p-bbbb2222")
        assert pa and pb
        assert "/projects/p-aaaa1111/worktrees/" in pa.replace(os.sep, "/")
        assert "/projects/p-bbbb2222/worktrees/" in pb.replace(os.sep, "/")
        wt.cleanup_all(str(d), "p-aaaa1111")
        assert not os.path.isdir(pa)
        assert os.path.isdir(pb)

    def test_legacy_root_when_no_project(self, tmp_path):
        d = tmp_path / "repo2"
        d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-x")
        assert path and "/code/worktrees/" in path.replace(os.sep, "/")

    def test_merge_worktree_brings_changes_back(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        path = wt.add_worktree(str(d), "t-feat")
        open(os.path.join(path, "feature.txt"), "w").write("hello from the task\n")
        assert wt.merge_worktree(str(d), "t-feat").ok is True
        assert os.path.isfile(os.path.join(str(d), "feature.txt"))
        assert not os.path.isdir(path)

    def test_branch_exists_tracks_add_merge_remove(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        assert wt.branch_exists(str(d), "t-x") is False  # no branch yet
        path = wt.add_worktree(str(d), "t-x")
        open(os.path.join(path, "x.txt"), "w").write("work\n")
        assert wt.branch_exists(str(d), "t-x") is True
        assert wt.merge_worktree(str(d), "t-x").ok is True
        assert wt.branch_exists(str(d), "t-x") is False

    def test_branch_survives_a_conflicted_merge(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        open(os.path.join(str(d), "f.txt"), "w").write("base\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
            cwd=str(d),
            check=True,
        )
        path = wt.add_worktree(str(d), "t-conf")
        open(os.path.join(path, "f.txt"), "w").write("task edit\n")
        open(os.path.join(str(d), "f.txt"), "w").write("base edit\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "div",
            ],
            cwd=str(d),
            check=True,
        )
        result = wt.merge_worktree(str(d), "t-conf")
        assert result.ok is False
        assert "f.txt" in result.conflicts
        assert wt.branch_exists(str(d), "t-conf") is True

    def test_merge_commit_succeeds_without_git_identity(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for var in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            monkeypatch.delenv(var, raising=False)
        d = tmp_path / "repo"
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(d), check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Gideon",
                "-c",
                "user.email=code@gideon.local",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            cwd=str(d),
            check=True,
        )
        path = wt.add_worktree(str(d), "t-feat")
        assert path
        open(os.path.join(path, "feature.txt"), "w").write("task work\n")
        open(os.path.join(str(d), "base.txt"), "w").write("base change\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Gideon",
                "-c",
                "user.email=code@gideon.local",
                "commit",
                "-q",
                "-m",
                "base diverge",
            ],
            cwd=str(d),
            check=True,
        )
        assert wt.merge_worktree(str(d), "t-feat").ok is True
        assert os.path.isfile(os.path.join(str(d), "feature.txt"))

    def test_add_worktree_idempotent(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        p1 = wt.add_worktree(str(d), "t-x")
        p2 = wt.add_worktree(str(d), "t-x")
        assert p1 == p2
        wt.remove_worktree(str(d), "t-x")

    def test_cleanup_all_removes_worktrees_dir(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        p1 = wt.add_worktree(str(d), "t-1")
        wt.add_worktree(str(d), "t-2")
        # the worktrees root is Gideon-owned (outside the workspace) and is removed
        root = os.path.dirname(p1)
        assert os.path.isdir(root)
        wt.cleanup_all(str(d))
        assert not os.path.isdir(root)
        assert not os.path.isdir(os.path.join(str(d), ".gideon-worktrees"))

    def test_cleanup_all_sweeps_orphan_task_branch(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        wt.add_worktree(str(d), "t-orphan")
        subprocess.run(
            [
                "git",
                "worktree",
                "remove",
                "--force",
                wt.worktree_path(str(d), "t-orphan"),
            ],
            cwd=str(d),
            check=True,
        )
        branch = wt.branch_name("t-orphan")
        rc, out = wt._git(
            str(d), "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        )
        assert branch in out
        wt.cleanup_all(str(d))
        rc2, out2 = wt._git(
            str(d), "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        )
        assert branch not in out2


class TestConflictDetection:
    def test_clean_repo_has_no_conflict_paths(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        assert wt.conflict_paths(str(d)) == []

    def test_real_conflict_is_detected_and_merge_returns_false(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        _init_repo(str(d))
        f = os.path.join(str(d), "shared.txt")
        open(f, "w").write("line one\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "seed",
            ],
            cwd=str(d),
            check=True,
        )
        path = wt.add_worktree(str(d), "t-conf")
        open(os.path.join(path, "shared.txt"), "w").write("task version\n")
        open(f, "w").write("base version\n")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "base edit",
            ],
            cwd=str(d),
            check=True,
        )
        assert wt.merge_worktree(str(d), "t-conf").ok is False
        assert wt.conflict_paths(str(d)) == []
