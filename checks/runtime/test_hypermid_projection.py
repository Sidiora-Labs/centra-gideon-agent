import copy
import hashlib

from gideon.hypermid.projection import project


def _part(part_id: str, text: str) -> dict[str, object]:
    return {
        "part_id": part_id,
        "kind": "text",
        "content_digest": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def test_projection_is_deterministic_source_bound_and_non_mutating() -> None:
    request = {
        "scope": {"owner_id": "owner-1", "project_id": "project-1"},
        "session_id": "session-1",
        "source_cursor": {"epoch": 1, "sequence": 2},
        "source_digest": hashlib.sha256(b"journal-range").hexdigest(),
        "generation": 4,
        "policy_revision": 2,
        "mode": "primary",
        "provider_profile_digest": hashlib.sha256(b"provider").hexdigest(),
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
                "role": "system",
                "parts": [_part("part-1", "fixed system prefix")],
                "region": "baseline",
                "token_mass": 4,
            },
            {
                "item_id": "item-2",
                "cursor": {"epoch": 1, "sequence": 2},
                "role": "user",
                "parts": [_part("part-2", "latest request")],
                "region": "tail",
                "token_mass": 3,
            },
        ],
    }
    original = copy.deepcopy(request)

    first = project(request)
    second = project(request)

    assert first.serialized == second.serialized
    assert first.value["selected_item_ids"] == ["item-1", "item-2"]
    assert first.region_bytes["baseline"] == second.region_bytes["baseline"]
    assert request == original
