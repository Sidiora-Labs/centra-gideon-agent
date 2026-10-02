from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gideon.interfaces.cli.hypermid import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_OUTCOME_UNKNOWN,
    _emit,
    _result_exit,
    add_parser,
)
_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="command", required=True))
    return parser


def test_hypermid_cli_registers_complete_noninteractive_operator_surface(tmp_path) -> None:
    parser = _parser()
    prefix = ["hypermid", "--owner", "owner-1", "--project", "project-1"]
    cases = [
        ["status"],
        ["inspect", "sessions"],
        ["config", "get"],
        ["maintenance", "plan", "integrity_check"],
        ["lifecycle", "status", "job-1"],
        ["conditions", '{"kind":"path_exists","path":"x","exists":true}'],
        ["export", "plan", str(tmp_path / "export.jsonl")],
    ]
    for suffix in cases:
        parsed = parser.parse_args(prefix + suffix)
        assert parsed.command == "hypermid"

    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    parsed = parser.parse_args(
        prefix
        + [
            "lifecycle",
            "apply",
            "restore",
            "--plan",
            str(plan),
            "--plan-digest",
            "a" * 64,
            "--confirm-destructive",
        ]
    )
    assert parsed.plan_digest == "a" * 64
    assert parsed.confirm_destructive is True

    with pytest.raises(SystemExit) as missing_review:
        parser.parse_args(prefix + ["maintenance", "apply", "--plan", str(plan)])
    assert missing_review.value.code == 2


def test_hypermid_cli_human_and_json_modes_share_results_and_stable_exits(
    capsys,
) -> None:
    result = {
        "state": "committed",
        "scope": {"owner_id": "owner-1", "project_id": "project-1"},
        "cursor": {"epoch": 5, "sequence": 8},
        "digest": "a" * 64,
    }
    _emit(SimpleNamespace(json=True), result)
    json_output = capsys.readouterr().out
    assert json.loads(json_output) == result

    _emit(SimpleNamespace(json=False), result)
    human_output = capsys.readouterr().out
    for value in ("committed", "owner-1", "project-1", "5", "8", "a" * 64):
        assert value in human_output

    assert _result_exit({"state": "committed"}) == EXIT_OK
    assert _result_exit({"state": "outcome_unknown"}) == EXIT_OUTCOME_UNKNOWN
    assert _result_exit({"state": "failed"}) == EXIT_FAILED
    assert _result_exit({"state": "cancelled"}) == EXIT_CANCELLED


def test_existing_gideon_entrypoint_exposes_hypermid_without_starting_a_daemon() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "runtime"
    completed = subprocess.run(
        [sys.executable, "-m", "gideon", "hypermid", "--help"],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for command in (
        "status",
        "inspect",
        "config",
        "maintenance",
        "lifecycle",
        "conditions",
        "export",
    ):
        assert command in completed.stdout


def test_native_consumers_bind_the_registered_hypermid_connectors() -> None:
    handler_source = (
        _ROOT / "runtime/gideon/interfaces/dashboard/handlers/hypermid.py"
    ).read_text(encoding="utf-8")
    console_api = (_ROOT / "apps/console/src/shared/data/api.ts").read_text(
        encoding="utf-8"
    )
    for connector, route in (
        ("hypermidOverview:", "/api/hypermid/overview"),
        ("hypermidSessions:", "/api/hypermid/sessions"),
        ("hypermidMemory:", "/api/hypermid/memory"),
        ("hypermidRuntimeConfig:", "/api/hypermid/config/runtime"),
        ("planHypermidMaintenance:", "/api/hypermid/operations/maintenance/plan"),
        ("applyHypermidMaintenance:", "/api/hypermid/operations/maintenance/apply"),
        ("planHypermidLifecycle:", "/api/hypermid/operations/lifecycle/${action}/plan"),
        ("applyHypermidLifecycle:", "/api/hypermid/operations/lifecycle/${action}/apply"),
        ("hypermidLifecycleStatus:", "/api/hypermid/operations/lifecycle/jobs/${encodeURIComponent(jobId)}"),
        ("recoverHypermidLifecycle:", "/api/hypermid/operations/lifecycle/jobs/${encodeURIComponent(jobId)}/recover"),
    ):
        assert connector in console_api
        assert route in console_api
        handler_route = route.replace("${action}", "{action}").replace(
            "${encodeURIComponent(jobId)}", "{job_id}"
        )
        assert f'"{handler_route}"' in handler_source

    desktop_check = r"""
const assert = require("node:assert/strict");
const path = require("node:path");
const { HypermidDesktop, OVERVIEW_ROUTE } = require("./apps/desktop/src/application/hypermid");
const { LocalGateway } = require("./apps/desktop/src/application/local-gateway");
const home = path.join(process.cwd(), ".native-connector-home");
const gateway = new LocalGateway({ app: { isPackaged: false }, home, status() {} });
assert.ok(gateway.hypermid instanceof HypermidDesktop);
assert.equal(OVERVIEW_ROUTE, "/api/hypermid/overview");
assert.equal(gateway.hypermid.gatewayEnvironment().GIDEON_HOME, home);
"""
    completed = subprocess.run(
        ["node", "-e", desktop_check],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
