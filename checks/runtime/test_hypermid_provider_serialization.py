import hashlib

import pytest

from gideon.hypermid.projection import project
from gideon.hypermid.provider import (
    ProviderSerializationViolation,
    profile_digest,
    serialize_for_host,
)


def _tool_part(part_id: str, kind: str, call_id: str, body: str) -> dict[str, object]:
    part = {
        "part_id": part_id,
        "kind": kind,
        "call_id": call_id,
        "content_digest": hashlib.sha256(body.encode()).hexdigest(),
    }
    part["tool_name" if kind == "tool_call" else "result_json"] = (
        "lookup" if kind == "tool_call" else body
    )
    if kind == "tool_call":
        part["arguments_json"] = body
    return part


def test_provider_serialization_validates_tool_pair_and_generation() -> None:
    capabilities = {
        "profile_id": "provider-1",
        "context_window_tokens": 100,
        "reserved_output_tokens": 20,
        "roles": ["assistant", "tool"],
        "part_kinds": ["tool_call", "tool_result"],
        "requires_tool_adjacency": True,
        "supports_reasoning": False,
        "supports_cache_boundaries": False,
        "max_cache_boundaries": 0,
        "max_images": 0,
        "image_accounting": "tokens",
    }
    digest = profile_digest(capabilities)
    profile = {**capabilities, "profile_digest": digest}
    request = {
        "scope": {"owner_id": "owner-1", "project_id": "project-1"},
        "session_id": "session-1",
        "source_cursor": {"epoch": 1, "sequence": 2},
        "source_digest": hashlib.sha256(b"journal-range").hexdigest(),
        "generation": 2,
        "policy_revision": 1,
        "mode": "primary",
        "provider_profile_digest": digest,
        "budget_inputs": {
            "context_window_tokens": 100,
            "reserved_output_tokens": 20,
            "max_input_tokens": 80,
            "max_items": 10,
            "max_images": 0,
        },
        "created_at": "2026-10-02T12:00:00Z",
        "items": [
            {
                "item_id": "item-1",
                "cursor": {"epoch": 1, "sequence": 1},
                "role": "assistant",
                "parts": [_tool_part("part-1", "tool_call", "call-1", "{}")],
                "region": "tail",
                "token_mass": 2,
            },
            {
                "item_id": "item-2",
                "cursor": {"epoch": 1, "sequence": 2},
                "role": "tool",
                "parts": [_tool_part("part-2", "tool_result", "call-1", '{"ok":true}')],
                "region": "tail",
                "token_mass": 2,
            },
        ],
    }
    projection = project(request).value
    result = serialize_for_host(
        projection, profile, {"generation": 1, "profile_digest": "0" * 64}
    )
    assert (
        result.blocks[0]["parts"][0]["call_id"]
        == result.blocks[1]["parts"][0]["call_id"]
    )

    orphan = {**projection, "blocks": projection["blocks"][1:]}
    with pytest.raises(ProviderSerializationViolation, match="no call"):
        serialize_for_host(orphan, profile)
