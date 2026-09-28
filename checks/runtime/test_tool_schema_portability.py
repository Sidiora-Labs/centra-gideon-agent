from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_NATIVE = _REPO / "runtime/gideon/engine/agents/native"


def _load_native_module(name):
    spec = importlib.util.spec_from_file_location(
        f"_portable_test_{name}", _NATIVE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project_run_tool_definitions = _load_native_module("project_run_tool_defs").project_run_tool_definitions
task_tool_definitions = _load_native_module("task_tool_defs").task_tool_definitions
tool_definitions_to_openai_schema = _load_native_module("tools").tool_definitions_to_openai_schema
from gideon.integrations.tool_providers.base import ToolDefinition
from gideon.integrations.tool_providers.portable_schema import (
    ToolSchemaRejected,
    conform_parameters,
    offered_tool_definitions,
    offered_tool_payload,
    schema_rejection_can_turn_off,
    tools_named_in_rejection,
)
from gideon.engine.agents.native.runtime import build_provider_tool_name_index


def _object(**properties):
    return {"type": "object", "properties": properties}


def _assert_portable(node, path="parameters"):
    assert isinstance(node, dict), path
    assert node.get("type") in {"object", "array", "string", "number", "integer", "boolean"}, path
    if node["type"] == "array":
        assert "items" in node, path
        _assert_portable(node["items"], f"{path}.items")
    if node["type"] == "object":
        assert path == "parameters" or node.get("properties"), path
        for key, child in node.get("properties", {}).items():
            _assert_portable(child, f"{path}.{key}")


def test_profile_repairs_only_safe_schema_metadata_and_keeps_required_fields():
    params = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["read", "write"], "examples": ["read"]},
            "labels": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "required": ["mode", "absent"],
        "additionalProperties": False,
    }
    verdict = conform_parameters(params)
    assert verdict.parameters == {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["read", "write"]},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["mode"],
    }
    assert params["properties"]["labels"]["uniqueItems"] is True
    assert any(issue.rule == "required_invalid" for issue in verdict.issues)


def test_free_form_maps_are_not_offered_but_one_bad_tool_does_not_drop_its_siblings():
    safe = ToolDefinition("safe_tool", "safe", parameters=_object(q={"type": "string"}))
    unportable = ToolDefinition(
        "map_tool",
        "map",
        provider="remote-mcp",
        parameters=_object(options={"type": "object", "additionalProperties": {"type": "string"}}),
    )
    offered = offered_tool_definitions([safe, unportable], provider="mcp")
    assert offered == [safe]
    assert offered[0] is safe


def test_malformed_nested_type_excludes_only_its_tool_and_keeps_safe_sibling():
    malformed = ToolDefinition(
        "bad_nested_type",
        "malformed nested type",
        provider="remote-mcp",
        parameters=_object(payload={"type": {"malformed": "array"}}),
    )
    safe = ToolDefinition("safe_sibling", "safe", parameters=_object(q={"type": "string"}))
    assert offered_tool_definitions([malformed, safe], provider="openai-compatible") == [safe]


def test_schema_rejection_switchability_requires_explicit_unlocked_owner():
    known = {"optional_tool", "read_file"}
    owners = {"optional_tool": "optional-provider", "read_file": "gideon-filesystem"}
    assert schema_rejection_can_turn_off(
        ["optional_tool"], provider_by_name=owners, known_tool_names=known
    )
    assert not schema_rejection_can_turn_off(
        ["read_file"], provider_by_name=owners, known_tool_names=known
    )
    assert not schema_rejection_can_turn_off(
        ["unknown_tool"], provider_by_name=owners, known_tool_names=known
    )
    assert not schema_rejection_can_turn_off(
        ["optional_tool"], provider_by_name={}, known_tool_names=known
    )


def test_unknown_mcp_array_shapes_are_not_repaired_by_builtin_name_rules():
    mcp_tool = ToolDefinition(
        "mcp__remote__project_run_create",
        "remote tool",
        provider="remote-mcp",
        parameters=_object(deliverables={"type": "array"}),
    )
    assert offered_tool_definitions([mcp_tool], provider="remote-mcp") == []


def test_local_refs_and_nullable_unions_are_conformed_without_mutating_input():
    params = {
        "type": "object",
        "properties": {
            "payload": {"$ref": "#/$defs/Payload"},
            "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "$defs": {"Payload": {"type": "object", "properties": {"id": {"type": "string"}}}},
    }
    verdict = conform_parameters(params)
    assert verdict.parameters["properties"]["payload"]["properties"] == {"id": {"type": "string"}}
    assert verdict.parameters["properties"]["name"] == {"type": "string"}
    assert "$defs" in params
    recursive = conform_parameters({
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/Node"}},
        "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
    })
    assert recursive.parameters is None
    assert any(not issue.repair for issue in recursive.issues)


def test_rejection_index_is_accepted_only_when_its_property_matches_the_definition():
    payload = [
        {"type": "function", "function": {"name": "first", "parameters": _object(a={"type": "string"})}},
        {"type": "function", "function": {"name": "second", "parameters": _object(b={"type": "string"})}},
    ]
    assert tools_named_in_rejection(
        "function_declarations[1].parameters.properties[b].format: unsupported", payload
    ) == ["second"]
    assert tools_named_in_rejection(
        "function_declarations[1].parameters.properties[a].format: unsupported", payload
    ) == []
    assert tools_named_in_rejection("tools[0].choice: first", payload) == []


def test_schema_rejection_is_a_deterministic_domain_error():
    error = ToolSchemaRejected(["project_run_create"], can_turn_off=True)
    assert error.tools == ("project_run_create",)
    assert '"project_run_create" tool' in str(error)
    assert "Gideon" in str(error)


def test_built_in_tool_catalog_serializes_with_array_items_and_declared_map_values():
    builtins = [
        *project_run_tool_definitions(
            "native",
            {"type": "object", "properties": {}},
        ),
        *task_tool_definitions("native", {"type": "object", "properties": {}}),
    ]
    declared_project = builtins[0].parameters["properties"]
    assert declared_project["stage_plan"]["items"]["properties"]["tasks"]["items"][
        "properties"
    ]["depends_on"]["items"] == {"type": "integer"}
    assert "required" not in declared_project["stage_plan"]["items"]["properties"]["tasks"][
        "items"
    ]
    schemas = offered_tool_payload(
        tool_definitions_to_openai_schema(builtins), provider="openai-compatible"
    )
    assert schemas
    for declaration in schemas:
        _assert_portable(declaration["function"]["parameters"])
    by_name = {entry["function"]["name"]: entry["function"]["parameters"] for entry in schemas}
    project = by_name["project_run_create"]["properties"]
    assert project["stage_plan"]["items"]["properties"]["objective"]["type"] == "string"
    assert project["deliverables"]["items"] == {"type": "string"}
    tasks = by_name["task_create"]["properties"]
    assert tasks["exit_criteria"]["items"]["properties"]["description"]["type"] == "string"
    assert tasks["action_plan"]["items"]["properties"]["content"]["type"] == "string"

    # The same final serializer covers full-catalog, reduced retrieval, and grouped offerings.
    for selected in (builtins, builtins[:4], [builtins[0], builtins[-1]]):
        declarations = offered_tool_payload(
            tool_definitions_to_openai_schema(selected), provider="openai-compatible"
        )
        for declaration in declarations:
            _assert_portable(declaration["function"]["parameters"])


def test_provider_wire_aliases_are_safe_collision_free_and_dispatch_reversibly():
    long_name = "mcp/provider/" + ("unicode_λ_" * 8)
    already_aliased = "g_sMZXW6YTB"
    definitions = [
        ToolDefinition("foo/bar", "remote slash", provider="mcp:one", parameters=_object()),
        ToolDefinition("foo_bar", "safe underscore", provider="builtin", parameters=_object()),
        ToolDefinition("mcp/qual_mcp/sha256_text", "digest", provider="mcp:qual_mcp", parameters=_object(text={"type": "string"})),
        ToolDefinition(long_name, "long unicode", provider="mcp:long", parameters=_object()),
        ToolDefinition(already_aliased, "canonical g name", provider="mcp:legacy", parameters=_object()),
        ToolDefinition("same_name", "earlier owner", provider="mcp:first", parameters=_object()),
        ToolDefinition("same_name", "last owner", provider="mcp:last", parameters=_object()),
    ]
    payload = tool_definitions_to_openai_schema(definitions)
    wire_names = [item["function"]["name"] for item in payload]
    assert len(wire_names) == len(set(wire_names))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in wire_names)
    by_description = {item["function"]["description"]: item["function"]["name"] for item in payload}
    assert by_description["safe underscore"] == "foo_bar"
    assert by_description["last owner"] == "same_name"
    assert "earlier owner" not in by_description
    assert by_description["remote slash"] != "foo_bar"
    assert by_description["canonical g name"] != already_aliased
    assert by_description["digest"].startswith("g_s")
    assert len(by_description["long unicode"]) <= 64

    finalized = offered_tool_payload(payload, provider="openai-compatible")
    assert [item["function"]["name"] for item in finalized] == wire_names
    canonical = [definition.name for definition in definitions]
    dispatch = build_provider_tool_name_index(canonical)
    assert dispatch[by_description["remote slash"]] == "foo/bar"
    assert dispatch[by_description["digest"]] == "mcp/qual_mcp/sha256_text"
    assert dispatch[by_description["canonical g name"]] == already_aliased
    assert dispatch[by_description["long unicode"]] == long_name
    rejected_wire_names = tools_named_in_rejection(
        f"Invalid schema for function '{by_description['digest']}'", finalized
    )
    rejection = ToolSchemaRejected(
        [dispatch[name] for name in rejected_wire_names], can_turn_off=True
    )
    assert rejection.tools == ("mcp/qual_mcp/sha256_text",)
