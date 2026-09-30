from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gideon.security.net.git import git_argv, git_child_env


pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("git") is None,
    reason="requires POSIX shell scripts and Git",
)


def _run(args: list[str], *, cwd: Path, neutral: bool) -> subprocess.CompletedProcess[str]:
    argv = git_argv(args) if neutral else ["git", *args]
    return subprocess.run(
        argv,
        cwd=cwd,
        env=git_child_env(site="git-neutral-test"),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_repository_hook_and_fsmonitor_cannot_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    initialized = subprocess.run(
        ["git", "init", "-q", str(repo)],
        env=git_child_env(site="git-neutral-test-init"),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr

    marker = tmp_path / "ran"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f'#!/bin/sh\necho hook >> "{marker}"\n', encoding="utf-8")
    hook.chmod(0o755)
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]

    control = _run([*identity, "commit", "--allow-empty", "-m", "control"], cwd=repo, neutral=False)
    assert control.returncode == 0, control.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == ["hook"]
    marker.unlink()

    neutral = _run([*identity, "commit", "--allow-empty", "-m", "neutral"], cwd=repo, neutral=True)
    assert neutral.returncode == 0, neutral.stderr
    assert not marker.exists(), "repository pre-commit hook ran through Gideon's Git argv"

    fsmonitor = tmp_path / "fsmonitor.sh"
    fsmonitor.write_text(f'#!/bin/sh\necho fsmonitor >> "{marker}"\nexit 1\n', encoding="utf-8")
    fsmonitor.chmod(0o755)
    configured = _run(["config", "core.fsmonitor", str(fsmonitor)], cwd=repo, neutral=False)
    assert configured.returncode == 0, configured.stderr

    control = _run(["status", "--porcelain"], cwd=repo, neutral=False)
    assert control.returncode == 0, control.stderr
    assert set(marker.read_text(encoding="utf-8").splitlines()) == {"fsmonitor"}
    marker.unlink()

    neutral = _run(["status", "--porcelain"], cwd=repo, neutral=True)
    assert neutral.returncode == 0, neutral.stderr
    assert not marker.exists(), "repository fsmonitor ran through Gideon's Git argv"


def test_neutral_diff_and_guarded_transport_policies_are_explicit() -> None:
    args = git_argv(["diff"])
    assert args[args.index("diff") + 1 : args.index("diff") + 3] == [
        "--no-ext-diff",
        "--no-textconv",
    ]

    guarded = git_argv(["clone", "https://example.invalid/repo.git"], https_only=True)
    settings = [guarded[index + 1] for index, item in enumerate(guarded[:-1]) if item == "-c"]
    assert "protocol.allow=never" in settings
    assert "protocol.https.allow=always" in settings
    assert "protocol.ext.allow=never" in settings
