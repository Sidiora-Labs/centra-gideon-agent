from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from gideon.hypermid.foundation import Id, Scope, Trace
from gideon.hypermid.roles import (
    RUNNER_ROLE_V1,
    TOOL_ROLE_V1,
    RoleProcess,
    RoleRequest,
    structural_schema_digest,
)


def _request(
    role: str,
    method: str,
    params: dict,
    request_id: str,
    project: str = "project-a",
) -> RoleRequest:
    return RoleRequest(
        role,
        method,
        params,
        Trace(Id("roles-live"), Id(request_id)),
        Scope(Id("owner-1"), Id(project), Id("workspace-1")),
    )


def test_real_role_process_pins_tools_and_recovers_runner_state(tmp_path: Path) -> None:
    state_root = tmp_path / "role-state"
    payload = tmp_path / "payload.txt"
    payload.write_bytes(b"real role payload")
    process = RoleProcess(
        [sys.executable, "-m", "gideon.hypermid.roles", "serve"],
        state_root,
    )
    process.start()

    catalog_params = {
        "preset": "default",
        "parameters": {"mode": "local"},
        "composition": {"modules": ["files"]},
        "system_text": "include",
        "digest_only": False,
    }
    first = process.request(_request(TOOL_ROLE_V1, "catalog", catalog_params, "catalog-1"))["result"]
    second = process.request(
        _request(TOOL_ROLE_V1, "catalog", catalog_params, "catalog-2", "project-b")
    )["result"]
    assert first == second
    tool = first["tools"][0]
    changed_description = dict(tool["input_schema"])
    changed_description["description"] = "different prose"
    assert structural_schema_digest(changed_description) == tool["schema_digest"]

    call = process.request(
        _request(
            TOOL_ROLE_V1,
            "call",
            {
                "schema_pin": {
                    "tool_name": tool["name"],
                    "schema_digest": tool["schema_digest"],
                    "semantics": tool["semantics"],
                },
                "arguments": {"path": str(payload)},
            },
            "call-1",
        )
    )
    assert call["result"] == {
        "kind": "final",
        "result": {
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "bytes": len(payload.read_bytes()),
        },
    }

    accepted = process.request(
        _request(
            RUNNER_ROLE_V1,
            "send",
            {"send_id": "send-1", "mode": "queue", "content": "persist me"},
            "send-1-request",
        )
    )
    assert accepted["result"]["outcome"] == "accepted"
    assert (state_root / "points" / "owner-send-recorded").read_text() == "send-1"
    assert process.kill() != 0
    process.restart()

    duplicate = process.request(
        _request(
            RUNNER_ROLE_V1,
            "send",
            {"send_id": "send-1", "mode": "queue", "content": "persist me"},
            "send-1-repeat",
        )
    )
    transcript = process.request(
        _request(RUNNER_ROLE_V1, "transcript", {}, "transcript-1")
    )["result"]
    process.close()

    assert duplicate["result"]["outcome"] == "duplicate"
    assert transcript["messages"] == [
        {"message_id": "message-1", "ordinal": 0, "role": "user", "content": "persist me"}
    ]
    assert transcript["lineage_id"] == transcript["head"]["lineage_id"]
    assert transcript["cursor"] == {"epoch": 1, "sequence": 1}

