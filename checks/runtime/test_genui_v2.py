from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

from gideon.workspace.genui_v2 import (
    genui_v2_prompt,
    genui_v2_schema,
    validate_candidate,
    validate_genui_v2,
)


COMPONENTS = [
    "Stack",
    "Card",
    "StatTile",
    "Table",
    "List",
    "Bar",
    "Callout",
    "Badge",
    "ProgressBar",
    "Button",
    "Form",
    "Compare",
    "Timeline",
    "Sources",
    "ActionPreview",
]


def valid_surface() -> dict:
    return {
        "schemaVersion": 2,
        "id": "surface.main",
        "revision": 3,
        "root": "root",
        "elements": {
            "root": {
                "type": "Stack",
                "props": {"direction": "grid", "gap": "m"},
                "children": ["compare", "form", "preview", "sources"],
            },
            "compare": {
                "type": "Compare",
                "props": {
                    "title": "Plans",
                    "items": [
                        {
                            "id": "basic",
                            "label": "Basic",
                            "details": [{"label": "Price", "value": "$10"}],
                        },
                        {"id": "pro", "label": "Pro", "description": "More capacity"},
                    ],
                },
            },
            "form": {
                "type": "Form",
                "props": {"fields": ["email"], "action": "contact", "title": "Contact"},
            },
            "preview": {
                "type": "ActionPreview",
                "props": {
                    "title": "Confirm plan",
                    "action": "choose_plan",
                    "label": "Choose",
                    "fields": ["note"],
                    "selectionFrom": "compare",
                    "selectionField": "plan",
                    "payload": {"source": "pricing"},
                },
            },
            "sources": {
                "type": "Sources",
                "props": {
                    "items": [
                        {"id": "pricing", "label": "Pricing", "url": "https://example.com/plans"}
                    ]
                },
            },
        },
        "state": {
            "compare": {"selected": "pro", "filter": "capacity"},
            "form": {"fields": {"email": "owner@example.com"}},
            "preview": {"fields": {"note": "Annual billing"}},
        },
    }


def test_catalog_is_the_canonical_strict_draft_2020_contract() -> None:
    schema = genui_v2_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert [entry["name"] for entry in schema["x-components"]] == COMPONENTS
    assert set(schema["required"]) == {
        "schemaVersion",
        "id",
        "revision",
        "root",
        "elements",
        "state",
    }
    assert schema["additionalProperties"] is False
    for name in COMPONENTS:
        assert schema["$defs"][f"{name}Props"]["additionalProperties"] is False
    assert set(schema["$defs"]["StackProps"]["properties"]) == {"direction", "gap"}
    assert set(schema["$defs"]["CardProps"]["properties"]) == {"title"}


def test_valid_complete_surface_and_schema_copy() -> None:
    surface = valid_surface()
    assert validate_genui_v2(surface) is surface
    schema = genui_v2_schema()
    schema["title"] = "changed"
    assert genui_v2_schema()["title"] == "Gideon GenUI v2"


def test_tree_must_be_connected_acyclic_and_single_parented() -> None:
    unreachable = valid_surface()
    unreachable["elements"]["orphan"] = {
        "type": "Badge",
        "props": {"text": "Orphan"},
    }
    assert validate_genui_v2(unreachable) is None

    cycle = valid_surface()
    cycle["elements"]["loop"] = {
        "type": "Card",
        "props": {},
        "children": ["root"],
    }
    cycle["elements"]["root"]["children"].append("loop")
    assert validate_genui_v2(cycle) is None

    duplicate_parent = valid_surface()
    duplicate_parent["elements"]["card-a"] = {
        "type": "Card",
        "props": {},
        "children": ["sources"],
    }
    duplicate_parent["elements"]["root"]["children"].append("card-a")
    assert validate_genui_v2(duplicate_parent) is None


def test_layout_and_leaf_children_are_exact() -> None:
    missing_children = valid_surface()
    del missing_children["elements"]["root"]["children"]
    assert validate_genui_v2(missing_children) is None

    leaf_children = valid_surface()
    leaf_children["elements"]["sources"]["children"] = []
    assert validate_genui_v2(leaf_children) is None

    body_prop = valid_surface()
    body_prop["elements"]["root"]["props"]["body"] = ["compare"]
    assert validate_genui_v2(body_prop) is None


def test_compare_state_fields_and_selection_references_are_bound() -> None:
    duplicate_items = valid_surface()
    duplicate_items["elements"]["compare"]["props"]["items"][1]["id"] = "basic"
    assert validate_genui_v2(duplicate_items) is None

    unknown_selection = valid_surface()
    unknown_selection["state"]["compare"]["selected"] = "enterprise"
    assert validate_genui_v2(unknown_selection) is None

    unknown_field = valid_surface()
    unknown_field["state"]["form"]["fields"]["phone"] = "123"
    assert validate_genui_v2(unknown_field) is None

    wrong_source = valid_surface()
    wrong_source["elements"]["preview"]["props"]["selectionFrom"] = "sources"
    assert validate_genui_v2(wrong_source) is None

    state_on_leaf = valid_surface()
    state_on_leaf["state"]["sources"] = {}
    assert validate_genui_v2(state_on_leaf) is None


def test_rejects_unsafe_unbounded_and_non_finite_json() -> None:
    dangerous = valid_surface()
    dangerous["elements"]["preview"]["props"]["payload"] = {
        "safe": {"constructor": "blocked"}
    }
    assert validate_genui_v2(dangerous) is None

    non_finite = valid_surface()
    non_finite["elements"]["preview"]["props"]["payload"] = {"score": float("nan")}
    assert validate_genui_v2(non_finite) is None

    too_long = valid_surface()
    too_long["elements"]["sources"]["props"]["title"] = "x" * 4097
    assert validate_genui_v2(too_long) is None

    nested: dict = {}
    cursor = nested
    for _ in range(20):
        cursor["next"] = {}
        cursor = cursor["next"]
    too_deep = valid_surface()
    too_deep["elements"]["preview"]["props"]["payload"] = nested
    assert validate_genui_v2(too_deep) is None


def test_candidates_are_exact_bounded_registered_leaves() -> None:
    candidate = {
        "id": "choice",
        "type": "Compare",
        "props": {"items": [{"id": "one", "label": "One"}]},
    }
    assert validate_candidate(candidate) is candidate

    duplicate = deepcopy(candidate)
    duplicate["props"]["items"].append({"id": "one", "label": "Again"})
    assert validate_candidate(duplicate) is None
    assert validate_candidate({"id": "layout", "type": "Stack", "props": {}}) is None
    assert validate_candidate({**candidate, "children": []}) is None

    oversized = {
        "id": "preview",
        "type": "ActionPreview",
        "props": {
            "title": "Preview",
            "action": "save",
            "label": "Save",
            "payload": {"values": ["é" * 4096 for _ in range(9)]},
        },
    }
    assert validate_candidate(oversized) is None


def test_prompt_is_derived_from_the_full_catalog() -> None:
    prompt = genui_v2_prompt()
    for name in COMPONENTS:
        assert f"{name}(" in prompt
    assert "schemaVersion=2" in prompt
    assert "Only Stack and Card own children" in prompt
    assert "Stack(body" not in prompt
    assert "Card(body" not in prompt
    assert "items:[{id:id,label:string" in prompt
    assert "url:http(s)-url" in prompt
    assert "stateKey" not in prompt
    assert "selectionKey" not in prompt
    assert "filterKey" not in prompt
