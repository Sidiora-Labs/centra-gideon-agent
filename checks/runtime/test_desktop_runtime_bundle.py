import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.core.config import validation
from gideon.core.config.loader import AppConfig
from gideon.operations import self_update
from gideon.operations.desktop_smoke import inspect_bundle
from tooling.packaging.backend_bundle_manifest import data_files, manifest
from tooling.packaging.runtime_bundle import (
    ENTRYPOINT_IMPORTS,
    MODULE_COLLECTIONS,
    RuntimeBundlePlan,
)

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def local_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "active_models.json").write_text("{}")
    validation._VALIDATED_CONTENT.clear()
    yield tmp_path
    validation._VALIDATED_CONTENT.clear()


def test_real_source_inventory_hashes_sdk_imports_and_core_tools():
    inventory = manifest(REPOSITORY, include_console=False)
    result = inspect_bundle(REPOSITORY / "runtime", inventory, require_console=False)
    assert result["files"] > 500
    assert result["sdk_modules"] >= 10
    assert result["mcp_tools"] > 10
    sdk_files = {
        f"gideon.sdk.{path.stem}"
        for path in (REPOSITORY / "runtime/gideon/sdk").glob("*.py")
        if path.stem != "__init__"
    }
    assert set(inventory["sdk_modules"]) == sdk_files
    assert "gideon.sdk" in MODULE_COLLECTIONS
    assert "gideon.integrations.mcp_core" in ENTRYPOINT_IMPORTS
    assert "gideon.operations.desktop_smoke" in ENTRYPOINT_IMPORTS
    assert inventory == manifest(REPOSITORY, include_console=False)


def test_manifest_is_not_vacuous_and_bad_hashes_are_refused():
    with pytest.raises(ValueError, match="missing runtime"):
        inspect_bundle(
            REPOSITORY / "runtime", {"version": 1, "files": []}, require_console=False
        )
    inventory = manifest(REPOSITORY, include_console=False)
    inventory["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        inspect_bundle(REPOSITORY / "runtime", inventory, require_console=False)


def test_data_list_uses_existing_console_or_refuses_missing_output():
    if (REPOSITORY / "apps/console/dist/index.html").is_file():
        plan = RuntimeBundlePlan(REPOSITORY)
        assert plan.runtime_resources() == data_files(REPOSITORY)
        assert (
            str(REPOSITORY / "apps/console/dist"),
            "gideon/static/dist",
        ) in plan.runtime_resources()
    else:
        with pytest.raises(SystemExit, match="Build the console"):
            data_files(REPOSITORY)


@pytest.mark.asyncio
async def test_frozen_and_app_bundle_update_checks_do_not_spawn_git(
    monkeypatch, tmp_path
):
    calls = []

    def audit(event, arguments):
        if event == "subprocess.Popen":
            calls.append(arguments)

    sys.addaudithook(audit)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
    assert self_update.detect_install_kind() == "desktop"
    assert await self_update.commits_behind_upstream(str(tmp_path)) is None
    with pytest.raises(RuntimeError, match="desktop bundle"):
        self_update.git_fetch(str(tmp_path), "main")
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.delenv("GIDEON_INSTALL_KIND")
    assert (
        await self_update.commits_behind_upstream(
            str(tmp_path / "Gideon.app/Contents/Resources")
        )
        is None
    )
    assert calls == []


def test_config_validation_runs_once_per_content_and_returns_fresh_values(
    local_home, caplog
):
    path = local_home / "config.json"
    original = {"agent": {"max_subagents": "invalid-a"}}
    path.write_text(json.dumps(original))
    with caplog.at_level(logging.WARNING):
        first = AppConfig.load()
        first.agent.max_subagents = 97
        second = AppConfig.load()
        assert second.agent.max_subagents != 97
        path.touch()
        AppConfig.load()
        assert (
            len(
                [
                    record
                    for record in caplog.records
                    if "type mismatch" in record.message
                ]
            )
            == 1
        )
        original["agent"]["max_subagents"] = "invalid-b"
        path.write_text(json.dumps(original))
        AppConfig.load()
        assert (
            len(
                [
                    record
                    for record in caplog.records
                    if "type mismatch" in record.message
                ]
            )
            == 2
        )
        original["agent"]["max_subagents"] = "invalid-a"
        path.write_text(json.dumps(original))
        AppConfig.load()
        assert (
            len(
                [
                    record
                    for record in caplog.records
                    if "type mismatch" in record.message
                ]
            )
            == 2
        )


def test_retired_keys_stay_consumed_after_load_save_merge(local_home):
    path = local_home / "config.json"
    original = {
        "default_memory_store": "old",
        "agent": {"streaming": True, "model": "retired"},
        "inbox": {"quick_reactions": True, "message_provider": "retired"},
        "providers": [{"name": "retained"}],
    }
    path.write_text(json.dumps(original))
    config = AppConfig.load()
    assert json.loads(path.read_text()) == original
    config.save()
    saved = json.loads(path.read_text())
    assert "default_memory_store" not in saved
    assert "streaming" not in saved["agent"] and "model" not in saved["agent"]
    assert (
        "quick_reactions" not in saved["inbox"]
        and "message_provider" not in saved["inbox"]
    )
    assert saved["providers"] == original["providers"]


def test_real_source_mcp_stdio_initializes_and_lists_tools(local_home):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    result = subprocess.run(
        [sys.executable, "-m", "gideon", "mcp-core"],
        input="".join(json.dumps(message) + "\n" for message in requests),
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    replies = {
        reply["id"]: reply
        for line in result.stdout.splitlines()
        if (reply := json.loads(line)).get("id") in (1, 2)
    }
    assert "result" in replies[1]
    assert any(tool["name"] == "skill_invoke" for tool in replies[2]["result"]["tools"])


def test_release_smoke_consumer_requires_frozen_backend():
    from gideon.operations.desktop_smoke import main

    with pytest.raises(SystemExit, match="packaged backend"):
        main()
    script = (REPOSITORY / "tooling/scripts/smoke_packaged_desktop.py").read_text()
    assert "'--desktop-smoke'" in script and "'mcp-core'" in script
    assert (
        "smoke_packaged_desktop.py"
        in (REPOSITORY / "tooling/scripts/smoke_desktop_mac.sh").read_text()
    )
    assert (
        "smoke_packaged_desktop.py"
        in (REPOSITORY / ".github/workflows/release.yml").read_text()
    )
