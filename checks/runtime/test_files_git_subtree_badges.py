from __future__ import annotations

import asyncio
import os
import subprocess

from gideon.interfaces.dashboard.handlers import files as F


def test_collapsed_untracked_subtrees_expand_to_real_badge_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("ignored/\n")
    (repo / "new" / "deep").mkdir(parents=True)
    (repo / "new" / "deep" / "shown.txt").write_text("shown\n")
    (repo / "new" / "ignored").mkdir()
    (repo / "new" / "ignored" / "hidden.txt").write_text("hidden\n")
    (repo / ".gitignore").write_text("new/ignored/\n")

    calls: list[list[str]] = []
    real_git = F._git

    async def counted_git(args, cwd, *pos, **kwargs):
        calls.append(args)
        return await real_git(args, cwd, *pos, **kwargs)

    monkeypatch.setattr(F, "_git", counted_git)
    statuses = asyncio.run(F._git_statuses(str(repo), "?? new/\0"))

    assert statuses == {
        str(repo / "new" / "deep" / "shown.txt"): "??",
        str(repo / "new" / "deep"): "??",
        str(repo / "new"): "??",
    }
    assert str(repo) not in statuses
    assert calls == [["ls-files", "--others", "--exclude-standard", "--", "new"]]


def test_changed_file_badges_each_intermediate_directory_but_not_repo_root(tmp_path):
    repo = os.fspath(tmp_path / "repo")
    statuses = asyncio.run(F._git_statuses(repo, " M src/pkg/file.py\0"))

    assert statuses == {
        os.path.join(repo, "src", "pkg", "file.py"): "M",
        os.path.join(repo, "src", "pkg"): "M",
        os.path.join(repo, "src"): "M",
    }
