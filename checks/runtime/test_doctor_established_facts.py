from __future__ import annotations

import subprocess
from pathlib import Path

from gideon.core.config import loader
from gideon.core.config.credentials import CREDENTIAL_BACKEND_ENV
from gideon.interfaces.cli import doctor
from gideon.operations.resilience.doctor import credential_store_state


def test_git_is_authority_for_worktree_pointer_and_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "initial"],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_AUTHOR_NAME": "Doctor Test",
            "GIT_AUTHOR_EMAIL": "doctor@example.invalid",
            "GIT_COMMITTER_NAME": "Doctor Test",
            "GIT_COMMITTER_EMAIL": "doctor@example.invalid",
        },
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree)],
        check=True,
        capture_output=True,
    )
    assert (worktree / ".git").is_file()
    assert doctor._git_repo_state(worktree, "git") is True

    monkeypatch.setattr(
        doctor.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    assert doctor._git_repo_state(worktree, "git") is None


def test_node_rows_use_the_minimum_and_exceptions_are_unknown(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(doctor, "_MIN_NODE_VERSION", 73)
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    doctor._doctor_node("/bin/node")
    assert capsys.readouterr().out == "  node:        ⚠️  /bin/node (version unknown)\n"

    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="v72.1.0\n"),
    )
    doctor._doctor_node("/bin/node")
    out = capsys.readouterr().out
    assert "v72 < 73" in out
    assert "Node 73+" in out
    assert "Node.js >= 73" in out


def test_credentials_never_invent_mode_and_report_independent_facts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setenv(CREDENTIAL_BACKEND_ENV, "dotenv")

    state = credential_store_state()
    assert state["env_exists"] is False
    assert state["env_readable"] is False
    assert state["env_mode"] == ""
    doctor._doctor_credentials()
    assert "0600" not in capsys.readouterr().out

    env = home / ".env"
    env.write_text("TOKEN=value\n")
    env.chmod(0o640)
    state = credential_store_state()
    assert state["env_exists"] is True
    assert state["env_readable"] is True
    assert state["env_mode"] == "0640"
