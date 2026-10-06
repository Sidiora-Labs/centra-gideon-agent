"""Exercise the real projections and inverse used by editable content surfaces."""

import json

import pytest

from gideon.interfaces.dashboard.handlers.mcp import _redact_mcp_projection
from gideon.interfaces.dashboard.handlers.memory import _redact_memory_field
from gideon.security.security import MaskConflict, redact_values_for_display
from gideon.workspace.artifacts.handlers import _restore_artifact_fields, _serialize
from gideon.workspace.artifacts.models import Artifact

SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"


def test_artifact_list_and_detail_mask_nested_content_and_inverse_edits():
    artifact = Artifact(
        slug="release-notes",
        name=f"Release {SECRET}",
        description=f"Use {SECRET}",
        tags=[f"tag-{SECRET}"],
        content=f"key: {SECRET}\nold note",
    )
    listed = _serialize(artifact)
    detail = _serialize(artifact, include_content=True)
    assert listed["slug"] == detail["slug"] == "release-notes"
    assert MASK in json.dumps(detail)
    assert SECRET not in json.dumps(listed)
    assert SECRET not in json.dumps(detail)

    submitted = {
        "content": detail["content"].replace("old note", "new note"),
        "description": detail["description"],
    }
    restored = _restore_artifact_fields(submitted, artifact)
    assert restored["content"] == f"key: {SECRET}\nnew note"
    assert restored["description"] == artifact.description


def test_ambiguous_moved_masked_list_entry_fails_closed():
    artifact = Artifact(
        slug="tags", name="Tags", tags=[SECRET, SECRET.replace("A", "D")]
    )
    with pytest.raises(MaskConflict):
        _restore_artifact_fields({"tags": [MASK]}, artifact)


def test_nested_memory_and_mcp_projections_mask_values_and_preserve_references():
    memory = _redact_memory_field({"value": [f"customer note {SECRET}"]})
    mcp = _redact_mcp_projection(
        {
            "name": "tenant-service",
            "headers": {"Authorization": f"Bearer {SECRET}"},
            "header_credentials": {
                "Authorization": {
                    "credential": "MCP_owner__API_TOKEN",
                    "prefix": "Bearer ",
                }
            },
        }
    )
    assert SECRET not in json.dumps(memory)
    assert MASK in json.dumps(memory)
    assert SECRET not in json.dumps(mcp)
    assert mcp["name"] == "tenant-service"
    assert (
        mcp["header_credentials"]["Authorization"]["credential"]
        == "MCP_owner__API_TOKEN"
    )


def test_recursive_projection_uses_the_shared_display_mask():
    assert redact_values_for_display({"nested": [SECRET]}) == {"nested": [MASK]}


def test_schedule_final_failure_policy_stays_masked(tmp_path, monkeypatch):
    import time

    from gideon.automation.triggers.models import Trigger
    from gideon.core.config import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.interfaces.dashboard.handlers.triggers import _schedule_row_for
    from gideon.interfaces.dashboard.state import ConsoleState

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    trigger = Trigger(
        id="clock:mask-policy",
        name="Mask policy",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 900},
        failure_policy={"nested": {"api_key": SECRET}, "dedupe_hash": True},
    )
    state = ConsoleState(ConversationDirectory(AppConfig()), time.time())
    projected = _schedule_row_for(state, trigger)
    assert SECRET not in json.dumps(projected)
    assert projected["failure_policy"] == {
        "nested": {"api_key": MASK},
        "dedupe_hash": True,
    }
    assert trigger.failure_policy["nested"]["api_key"] == SECRET
