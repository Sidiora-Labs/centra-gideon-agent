"""Disclosure projection and digest for the exact app bundle under review."""

from __future__ import annotations

from pathlib import Path

from gideon.extensions.apps.disclosure import bundle_digest, changed, describe
from gideon.extensions.apps.manifest import AppManifest


def _manifest(**overrides) -> AppManifest:
    values = {
        "name": "reviewed-app",
        "version": "1.0.0",
        "displayName": "Reviewed app",
        "description": "App review vector",
        "permissions": {"api": ["/api/tasks"], "agent": True, "cron": True},
        "crons": [
            {"name": "daily", "cron_expr": "3 18 * * *", "message": "Review today's tasks"},
            {"name": "hourly", "every": 3600, "agent": "reviewer", "message": "Review updates"},
        ],
        "backend": {"entryPoint": "backend/server.py", "sandbox": "restricted"},
        "setup": {"onInstall": "python setup.py", "onUpdate": "python migrate.py"},
        "providers": [
            {"type": "search", "implementation": "provider:build", "execution": "in-process"}
        ],
        "mcpServers": {
            "local": {"command": "python", "args": ["backend/mcp.py", "--api-key", "fixture-secret"]},
            "remote": {"url": "https://user:fixture-secret@service.example/mcp?token=fixture-secret&mode=brief"},
        },
        "sources": [{"name": "tasks", "script": "sources/tasks.py"}],
        "skills": [{"path": "skills/review/SKILL.md"}],
        "dependencies": {"pythonDependencies": ["review-lib>=1.0"]},
        "hooks": [{"name": "before-review", "event": "task.created", "provider": "hook:build"}],
    }
    values.update(overrides)
    return AppManifest.from_dict(values)


def test_review_names_grants_jobs_packages_and_code_paths() -> None:
    projection = describe(_manifest())

    assert projection["permissions"]["api"] == ["/api/tasks"]
    assert [job["name"] for job in projection["crons"]] == ["daily", "hourly"]
    assert all(job["scheduled"] for job in projection["crons"])
    assert projection["crons"][0]["cadence"] == "3 18 * * *"
    assert projection["crons"][1]["agent"] == "reviewer"
    assert projection["pythonDependencies"] == [{"spec": "review-lib>=1.0", "coreOwned": False}]
    assert projection["hasBackend"] is True
    assert projection["backendSandbox"] == "restricted"
    assert projection["providers"] == [
        {"type": "search", "implementation": "provider:build", "execution": "in-process"}
    ]
    assert projection["onInstall"] == "python setup.py"
    assert projection["mcpServers"] == [
        {"name": "local", "launches": "Command python · 3 arguments · values hidden: ••••••••"},
        {"name": "remote", "launches": "Remote server · https://service.example/mcp · query keys: mode, token"},
    ]
    assert "fixture-secret" not in str(projection["mcpServers"])
    assert projection["sources"] == [{"name": "tasks", "script": "sources/tasks.py"}]
    assert projection["skills"] == ["SKILL.md"]
    assert projection["hooks"] == [
        {"name": "before-review", "event": "task.created", "provider": "hook:build"}
    ]
    assert "loads provider code into the gateway" in projection["runsAsYou"]
    assert "starts or connects to MCP servers" in projection["runsAsYou"]


def test_job_is_disclosed_as_inert_without_the_cron_permission() -> None:
    projection = describe(_manifest(permissions={"api": ["/api/tasks"]}))
    assert [job["scheduled"] for job in projection["crons"]] == [False, False]


def test_bundle_digest_changes_for_file_bytes_names_empty_files_and_link_text(tmp_path: Path) -> None:
    staged = tmp_path / "bundle"
    (staged / "package").mkdir(parents=True)
    (staged / "app.json").write_text("{}", encoding="utf-8")
    (staged / "package" / "provider.py").write_text("VALUE = 1\n", encoding="utf-8")
    first = bundle_digest(staged)
    assert first == bundle_digest(staged)

    (staged / "package" / "provider.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed_bytes = bundle_digest(staged)
    assert changed_bytes != first

    (staged / "package" / "provider.py").rename(staged / "package" / "renamed.py")
    changed_path = bundle_digest(staged)
    assert changed_path != changed_bytes

    (staged / "package" / "empty.txt").write_text("", encoding="utf-8")
    changed_empty = bundle_digest(staged)
    assert changed_empty != changed_path

    (staged / "package" / "inside.py").write_text("VALUE = 3\n", encoding="utf-8")
    (staged / "package" / "alias.py").symlink_to("inside.py")
    link_digest = bundle_digest(staged)
    (staged / "package" / "alias.py").unlink()
    (staged / "package" / "alias.py").symlink_to("renamed.py")
    assert bundle_digest(staged) != link_digest


def test_update_disclosure_changes_only_when_install_behavior_changes() -> None:
    before = describe(_manifest())
    assert changed(before, describe(_manifest(version="1.1.0"))) is False
    assert changed(before, describe(_manifest(permissions={"api": ["/api/tasks"]}))) is True
    assert changed(None, before) is True
