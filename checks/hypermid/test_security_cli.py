from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from gideon.hypermid.contracts import MemoryOperation, RecordKind
from gideon.hypermid.credential_authority import local_backup_credential_name
from gideon.hypermid.foundation import Id, Scope
from gideon.integrations.llm.credentials import CredentialStore
from gideon.interfaces.cli.hypermid import _run, add_parser

from checks.hypermid.test_memory_release import _draft, _mutation, _open_memory
from checks.hypermid.test_security_operations import _start_security_daemon


def _arguments(values: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser(commands)
    return parser.parse_args(values)


async def _invoke(
    capsys: pytest.CaptureFixture[str], common: list[str], command: list[str]
) -> dict[str, object]:
    result = await _run(_arguments(["hypermid", "--json", *common, *command]))
    assert result == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert isinstance(value, dict)
    return value


@pytest.mark.asyncio
async def test_cli_persists_reviewed_backup_plan_and_receipt_across_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    scope = Scope(Id("cli-security-owner"), Id("cli-security-project"), Id("cli-security-workspace"))
    capability_id = Id("cli-security-capability")
    record_id = Id("cli-security-record")
    resources = {record_id, Id("memory-portability")}
    daemon = _start_security_daemon(
        tmp_path, "cli-security", scope, capability_id, resources
    )
    client = None
    try:
        client, memory = await _open_memory(daemon, scope, capability_id)
        content = "encrypted recovery snapshot from the native CLI"
        created = await memory.create(
            _mutation(scope, MemoryOperation.CREATE, record_id, "cli-security-create"),
            _draft(scope, record_id, RecordKind.FACT, content),
            now_ms=1,
        )
        assert created.record is not None
        await client.close()
        client = None

        enrollment_path = home / "hypermid" / "enrollment.json"
        enrollment_path.parent.mkdir(parents=True, mode=0o700)
        enrollment_path.write_text(
            json.dumps(
                {
                    "scope": scope.to_wire(),
                    "credential_id": "cli-security-credential",
                    "capability_id": str(capability_id),
                    "operations": ["read", "append", "export"],
                    "resources": [str(item) for item in sorted(resources)],
                    "expires_ms": time.time_ns() // 1_000_000 + 600_000,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        enrollment_path.chmod(0o600)
        key = hashlib.sha256(b"native CLI recovery key").digest()
        CredentialStore(home).put(
            local_backup_credential_name(scope),
            {
                "type": "static_token",
                "value": base64.b64encode(key).decode("ascii"),
            },
        )

        common = [
            "--connection-record",
            str(daemon.record),
            "--owner",
            str(scope.owner_id),
            "--project",
            str(scope.project_id),
            "--workspace",
            str(scope.workspace_id),
        ]
        credentials = await _invoke(capsys, common, ["security", "credentials"])
        assert credentials == {
            "credentials": [
                {
                    "configured": True,
                    "credential_ref": "local-backup",
                    "label": "Local backup key",
                    "purposes": ["backup", "restore"],
                }
            ]
        }

        artifact = tmp_path / "native-cli-recovery.hmbk"
        plan = await _invoke(
            capsys,
            common,
            [
                "security",
                "plan",
                "backup",
                "--destination",
                str(artifact),
                "--export-id",
                "native-cli-recovery",
            ],
        )
        assert plan["operation"] == "security.backup.apply"
        assert plan["record_count"] == 1
        assert "credential_handle" not in json.dumps(plan)
        reviewed = tmp_path / "reviewed-security-plan.json"
        reviewed.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")

        receipt = await _invoke(
            capsys,
            common,
            [
                "security",
                "apply",
                "backup",
                "--plan",
                str(reviewed),
                "--plan-digest",
                str(plan["plan_digest"]),
            ],
        )
        assert receipt["state"] == "committed"
        assert receipt["artifact_path"] == str(artifact)
        assert artifact.is_file()
        assert content.encode("utf-8") not in artifact.read_bytes()

        status = await _invoke(
            capsys, common, ["security", "status", str(receipt["job_id"])]
        )
        recovered = await _invoke(
            capsys, common, ["security", "recover", str(receipt["job_id"])]
        )
        assert status == receipt
        assert recovered == receipt
        assert "credential_handle" not in json.dumps(receipt)
    finally:
        if client is not None:
            await client.close()
        daemon.stop()
