from __future__ import annotations

import asyncio

import pytest

from gideon.assurance.validation import (
    FieldSpec,
    ToolSchema,
    ValidationError,
    normalize_tool_booleans,
    validate_tool_args,
)
from gideon.automation.triggers import tools
from gideon.automation.triggers.action_edit import edited_action
from gideon.automation.triggers.models import parse_trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.http_request import RequestValidationError, bool_field
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider


def saved_trigger(tmp_path, *, flat=False):
    store = TriggerStore(base_dir=tmp_path)
    action = {
        "provider": "invoke-agent",
        "config": {
            "task_template": "Review files",
            "agent": "reviewer",
            "capability": "research",
            "may_change": ["src/**"],
            "max_turns": 7,
            "timeout": 180,
        },
    }
    trigger, issues = parse_trigger(
        {
            "id": "clock:review",
            "name": "Review",
            "kind": "clock",
            "enabled": True,
            "spec": {"kind": "interval", "interval_secs": 3600},
            "workflow": action if flat else {"inline": action},
        }
    )
    assert not [item for item in issues if item.severity == "error"]
    store.upsert(trigger)
    return store


@pytest.mark.parametrize("flat", [False, True])
def test_store_action_patch_retains_hidden_settings_and_removes_explicit_null(
    tmp_path, flat
):
    store = saved_trigger(tmp_path, flat=flat)
    original = store.get("clock:review").trigger
    result = tools.update(
        store,
        trigger_id=original.id,
        patch={
            "workflow": {
                "inline": {"config": {"task_template": "Review later", "agent": None}}
            }
        },
    )
    assert result.ok, result.text
    reread = TriggerStore(base_dir=tmp_path).get(original.id).trigger
    action = reread.workflow.get("inline", reread.workflow)
    assert action["provider"] == "invoke-agent"
    assert action["config"] == {
        "task_template": "Review later",
        "capability": "research",
        "may_change": ["src/**"],
        "max_turns": 7,
        "timeout": 180,
    }
    assert reread.spec == original.spec


def test_provider_switch_drops_previous_provider_settings():
    assert edited_action(
        {"provider": "invoke-agent", "config": {"max_turns": 8}},
        {"provider": "notify", "config": {"message": "Done"}},
    ) == {"provider": "notify", "config": {"message": "Done"}}


def test_bad_action_patch_changes_nothing(tmp_path):
    store = saved_trigger(tmp_path)
    before = store.get("clock:review").trigger.to_dict()
    answer = tools.update(
        store,
        trigger_id="clock:review",
        patch={"workflow": {"config": []}, "name": "Broken"},
    )
    assert not answer.ok
    assert store.get("clock:review").trigger.to_dict() == before


def test_model_schema_spelled_false_is_false_but_http_boolean_stays_strict():
    schema = ToolSchema("edit", [FieldSpec("replace_all", bool)])
    args = normalize_tool_booleans({"replace_all": "false"}, schema)
    assert validate_tool_args(args, schema) == {"replace_all": False}
    with pytest.raises(ValidationError):
        normalize_tool_booleans({"replace_all": "perhaps"}, schema)
    with pytest.raises(RequestValidationError):
        bool_field({"replace_all": "false"}, "replace_all", default=False)


def test_automation_false_patch_disables_and_invalid_boolean_is_atomic(tmp_path):
    store = saved_trigger(tmp_path)
    result = tools.update(store, trigger_id="clock:review", patch={"enabled": "false"})
    assert result.ok and store.get("clock:review").trigger.enabled is False
    before = store.get("clock:review").trigger.to_dict()
    result = tools.update(
        store, trigger_id="clock:review", patch={"enabled": "perhaps", "name": "Lost"}
    )
    assert not result.ok and store.get("clock:review").trigger.to_dict() == before


def test_real_file_edit_spelled_false_cannot_replace_all(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("same same")
    provider = NativeBuiltinToolProvider(
        cwd=tmp_path, session_key="dashboard:boolean-edit"
    )

    async def run():
        read = await provider.invoke("read_file", {"path": "sample.txt"})
        assert read.success, read.error
        edit = await provider.invoke(
            "edit_file",
            {
                "path": "sample.txt",
                "old_str": "same",
                "new_str": "changed",
                "replace_all": "false",
            },
        )
        assert not edit.success
        assert "not unique" in edit.error

    asyncio.run(run())
    assert path.read_text() == "same same"


def test_workflow_boolean_safety_defaults():
    from gideon.automation.workflows.effects import redo_blocked
    from gideon.automation.workflows.verify import requires_fresh_judge

    assert requires_fresh_judge({"self_judge": "false"}) is True
    assert requires_fresh_judge({"self_judge": "perhaps"}) is True
    assert requires_fresh_judge({"self_judge": "true"}) is False
    from types import SimpleNamespace

    committed = SimpleNamespace(epoch=1)
    assert redo_blocked({"redo_effects": "false"}, committed, 2) is True
    assert redo_blocked({"redo_effects": "true"}, committed, 2) is False


def test_workflow_tool_unknown_boolean_refused_before_dispatch():
    from gideon.integrations.mcp_workflows import _call_tool

    response = _call_tool("workflow_audit", {"dry_run": "perhaps"})
    assert "field_not_a_boolean" in response


def test_workflow_http_boolean_body_rejects_string_on_real_request():
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    from gideon.automation.workflows.handlers import _json_body

    async def run():
        async def route(request):
            body = await _json_body(request)
            return body if isinstance(body, web.Response) else web.json_response(body)

        app = web.Application()
        app.router.add_post("/flags", route)
        async with TestServer(app) as server:
            async with ClientSession() as client:
                async with client.post(
                    server.make_url("/flags"), json={"force": "false"}
                ) as response:
                    assert response.status == 400
                    assert (await response.json())["error"][
                        "code"
                    ] == "field_not_a_boolean"
                async with client.post(
                    server.make_url("/flags"), json={"force": False}
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["force"] is False

    asyncio.run(run())
