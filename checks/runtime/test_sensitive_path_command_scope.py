from __future__ import annotations

import asyncio
from pathlib import Path

from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.bash_provider import BashActionProvider
from gideon.security.security import is_sensitive_bash_command


def test_shell_refuses_sensitive_paths_across_home_and_cwd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    gideon_home = project / ".gideon"
    home.mkdir()
    project.mkdir()
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("fixture key", encoding="utf-8")
    (gideon_home / "credentials").mkdir(parents=True)
    (gideon_home / "credentials" / "token").write_text(
        "fixture token", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(gideon_home))

    cli_files = (
        ("CODEX_HOME", "auth.json"),
        ("CLAUDE_CONFIG_DIR", ".credentials.json"),
        ("GEMINI_CONFIG_DIR", "oauth_creds.json"),
        ("GH_CONFIG_DIR", "hosts.yml"),
        ("GLAB_CONFIG_DIR", "config.yml"),
    )
    for variable, name in cli_files:
        config = tmp_path / variable.lower()
        config.mkdir()
        (config / name).write_text("fixture sign-in", encoding="utf-8")
        monkeypatch.setenv(variable, str(config))
        assert is_sensitive_bash_command(f"cat {config / name}", cwd=project)

    link = project / "key-link"
    link.symlink_to(home / ".ssh" / "id_rsa")
    assert is_sensitive_bash_command("cat key-link", cwd=project)
    assert is_sensitive_bash_command(
        f"cat {gideon_home}/credentials/{{token,absent}}", cwd=project
    )
    assert is_sensitive_bash_command(f"cat {gideon_home}/credentials/*", cwd=project)
    assert is_sensitive_bash_command(
        "node -e \"read(process.env.HOME+'/.ssh/id_rsa')\""
    )
    assert is_sensitive_bash_command(
        "node -e \"read(process.env.GIDEON_HOME+'/credentials/token')\""
    )
    assert is_sensitive_bash_command(
        "node -e \"read(process.env.GIDEON_HOME + '/.env')\""
    )
    assert is_sensitive_bash_command("cat .gideon/credentials/token", cwd=project)

    (project / "README.md").write_text("ordinary", encoding="utf-8")
    assert is_sensitive_bash_command("cat README.md", cwd=project) is None
    assert (
        is_sensitive_bash_command("git@github.com:owner/repo.git", cwd=project) is None
    )

    absent_home = tmp_path / "must-not-be-created"
    monkeypatch.setenv("GIDEON_HOME", str(absent_home))
    assert is_sensitive_bash_command("cat $GIDEON_HOME/.env", cwd=project)
    assert not absent_home.exists()

    monkeypatch.setenv("GIDEON_HOME", str(gideon_home))
    marker = project / "spawned"
    context = ActionContext(event="test", execution_cwd=str(project))
    result = asyncio.run(
        BashActionProvider().execute(
            {"command": "cat .gideon/credentials/token > spawned"}, context
        )
    )
    assert not result.success
    assert not marker.exists(), "the sensitive shell command spawned before refusal"
