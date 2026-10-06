"""Grounded preparation, assembly, decision validation and unconfigured-provider fallback."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from copy import deepcopy

from jsonschema import Draft202012Validator

from gideon.integrations.mcp_artifacts import _list_tools
from gideon.workspace.genui_prepare import (
    apply_genui_decision,
    assemble_genui,
    genui_decision_request,
    prepare_genui,
    prepared_input_schema,
)
from gideon.workspace.genui_v2 import validate_genui_v2
from gideon.workspace.visualize import visualize


def source() -> dict:
    return {
        "id": "deployment-choice",
        "revision": 3,
        "goal": "Compare the supplied deployment options and review the selected action.",
        "candidates": [
            {
                "id": "options",
                "type": "Compare",
                "props": {
                    "title": "Deployment",
                    "items": [
                        {
                            "id": "canary",
                            "label": "Canary",
                            "details": [{"label": "Replicas", "value": "1"}],
                        },
                        {
                            "id": "full",
                            "label": "Full rollout",
                            "description": "All configured replicas",
                        },
                    ],
                },
            },
            {
                "id": "evidence",
                "type": "Sources",
                "props": {
                    "items": [
                        {
                            "id": "deployment-record",
                            "label": "Deployment record",
                            "url": "https://example.org/deployment/31",
                        },
                    ]
                },
            },
            {
                "id": "review",
                "type": "ActionPreview",
                "props": {
                    "title": "Review rollout",
                    "action": "review_rollout",
                    "label": "Submit review",
                    "selectionFrom": "options",
                    "fields": ["note"],
                    "payload": {"record": "deployment-31"},
                },
            },
        ],
        "state": {
            "options": {"selected": "canary", "filter": ""},
            "review": {"fields": {"note": "Review required"}},
        },
    }


def variant_source() -> dict:
    value = source()
    value["candidates"][1:1] = [
        {
            "id": "facts-table",
            "type": "Table",
            "group": "facts-view",
            "props": {
                "columns": ["Environment", "Replicas"],
                "rows": [["Canary", 1], ["Full rollout", 12]],
            },
        },
        {
            "id": "facts-bar",
            "type": "Bar",
            "group": "facts-view",
            "props": {
                "labels": ["Canary", "Full rollout"],
                "data": [1, 12],
            },
        },
    ]
    return value


def decision_answers(body: dict, choices: dict[str, str] | None = None) -> dict:
    selected = choices or {}
    answers = {}
    for name, question in body["questions"].items():
        choice = selected.get(name, next(iter(question["criteria"])))
        answers[name] = {
            "choice": choice,
            "confidence": 1,
            "probabilities": {key: int(key == choice) for key in question["criteria"]},
        }
    return answers


class PreparedGenUiTests(unittest.TestCase):
    def test_each_layout_preserves_every_grounded_candidate_and_state(self):
        raw = source()
        prepared = prepare_genui(raw)
        for layout in ["stack", "cards", "grid"]:
            envelope = assemble_genui(
                prepared, layout, ["evidence", "options", "review"]
            )
            self.assertIsNotNone(validate_genui_v2(envelope))
            self.assertEqual(envelope["state"], raw["state"])
            for candidate in raw["candidates"]:
                self.assertEqual(
                    envelope["elements"][candidate["id"]],
                    {"type": candidate["type"], "props": candidate["props"]},
                )
        self.assertEqual(raw, source())

    def test_decision_request_is_bounded_and_only_offers_grounded_permutations(self):
        prepared = prepare_genui(source())
        body, orders = genui_decision_request(prepared)
        self.assertEqual(set(body["questions"]), {"layout", "ordering"})
        self.assertEqual(body["state"]["candidates"], source()["candidates"])
        for order in orders.values():
            self.assertEqual(set(order), {"options", "evidence", "review"})
            self.assertEqual(len(order), 3)
        self.assertNotIn("model", body)

    def test_valid_choice_assembles_only_supplied_content_and_invalid_choice_refuses(
        self,
    ):
        prepared = prepare_genui(source())
        body, _ = genui_decision_request(prepared)
        answers = decision_answers(body)
        envelope = apply_genui_decision(prepared, {"answers": answers})
        self.assertEqual(
            envelope["elements"]["options"]["props"], source()["candidates"][0]["props"]
        )
        answers["layout"]["choice"] = "execute_code"
        with self.assertRaises(ValueError):
            apply_genui_decision(prepared, {"answers": answers})
        with self.assertRaises(ValueError):
            apply_genui_decision(prepared, {"answers": {}})

    def test_jev_selects_actual_component_variant_and_keeps_mandatory_action_path(self):
        prepared = prepare_genui(variant_source())
        body, _ = genui_decision_request(prepared)
        self.assertEqual(
            set(body["questions"]), {"layout", "ordering", "variant.facts-view"}
        )
        answers = decision_answers(body, {"variant.facts-view": "facts-bar"})
        envelope = apply_genui_decision(prepared, {"answers": answers})
        self.assertIsNotNone(validate_genui_v2(envelope))
        self.assertEqual(envelope["elements"]["facts-bar"]["type"], "Bar")
        self.assertNotIn("facts-table", envelope["elements"])
        self.assertEqual(envelope["elements"]["options"]["type"], "Compare")
        self.assertEqual(envelope["elements"]["review"]["type"], "ActionPreview")
        self.assertTrue(
            all("group" not in element for element in envelope["elements"].values())
        )

    def test_group_selection_filters_state_and_question_count_is_bounded(self):
        value = source()
        value["candidates"].extend(
            [
                {
                    "id": "compact-choice",
                    "type": "Compare",
                    "group": "choice-view",
                    "props": {
                        "items": [{"id": "one", "label": "One"}],
                    },
                },
                {
                    "id": "detailed-choice",
                    "type": "Compare",
                    "group": "choice-view",
                    "props": {
                        "items": [{"id": "two", "label": "Two"}],
                    },
                },
            ]
        )
        value["state"].update(
            {
                "compact-choice": {"selected": "one"},
                "detailed-choice": {"selected": "two"},
            }
        )
        prepared = prepare_genui(value)
        body, _ = genui_decision_request(prepared)
        envelope = apply_genui_decision(
            prepared,
            {
                "answers": decision_answers(
                    body,
                    {"variant.choice-view": "detailed-choice"},
                )
            },
        )
        self.assertNotIn("compact-choice", envelope["state"])
        self.assertEqual(envelope["state"]["detailed-choice"], {"selected": "two"})

        maximum = source()
        maximum["candidates"] = [
            {
                "id": f"badge-{group}-{option}",
                "type": "Badge",
                "group": f"group-{group}",
                "props": {"text": f"Fact {group}"},
            }
            for group in range(6)
            for option in range(2)
        ]
        maximum["state"] = {}
        bounded, _ = genui_decision_request(prepare_genui(maximum))
        self.assertEqual(len(bounded["questions"]), 8)

    def test_group_bounds_and_grouped_action_dependency_are_rejected_before_inference(
        self,
    ):
        singleton = source()
        singleton["candidates"][1]["group"] = "single"
        too_many_options = source()
        too_many_options["candidates"] = [
            {
                "id": f"badge-{option}",
                "type": "Badge",
                "group": "view",
                "props": {"text": "Fact"},
            }
            for option in range(5)
        ]
        too_many_options["state"] = {}
        too_many_groups = source()
        too_many_groups["candidates"] = [
            {
                "id": f"badge-{group}-{option}",
                "type": "Badge",
                "group": f"group-{group}",
                "props": {"text": "Fact"},
            }
            for group in range(7)
            for option in range(2)
        ]
        too_many_groups["state"] = {}
        grouped_reference = source()
        grouped_reference["candidates"][0]["group"] = "compare-view"
        grouped_reference["candidates"].append(
            {
                "id": "options-alt",
                "type": "Compare",
                "group": "compare-view",
                "props": deepcopy(grouped_reference["candidates"][0]["props"]),
            }
        )
        for item in (singleton, too_many_options, too_many_groups, grouped_reference):
            with self.subTest(item=item), self.assertRaises(ValueError):
                prepare_genui(item)

    def test_invalid_prepared_inputs_are_rejected_before_selection(self):
        variants = []
        for key, value in [
            ("revision", True),
            ("goal", ""),
            ("layouts", ["html"]),
            ("unknown", 1),
            ("candidates", []),
        ]:
            item = source()
            item[key] = value
            variants.append(item)
        duplicate = source()
        duplicate["candidates"].append(deepcopy(duplicate["candidates"][0]))
        variants.append(duplicate)
        unsafe = source()
        unsafe["candidates"][1]["props"]["items"][0]["url"] = "javascript:alert(1)"
        variants.append(unsafe)
        invalid_state = source()
        invalid_state["state"]["options"]["selected"] = "invented"
        variants.append(invalid_state)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(ValueError):
                prepare_genui(item)

    def test_tool_schema_exposes_native_props_and_accepts_legacy_data(self):
        schema = prepared_input_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(source())
        Draft202012Validator(schema).validate(variant_source())
        tool = next(tool for tool in _list_tools() if tool["name"] == "visualize")
        validator = Draft202012Validator(tool["inputSchema"])
        validator.validate({"data": {"genui": source()}})
        validator.validate({"data": [1, 2, 3]})
        self.assertTrue(
            list(validator.iter_errors({"data": {"genui": {"id": "missing"}}}))
        )
        self.assertIn(
            "Compare", tool["inputSchema"]["properties"]["data"]["description"]
        )
        self.assertIn(
            "same-fact component alternatives",
            tool["inputSchema"]["properties"]["data"]["description"],
        )

    def test_real_missing_configuration_falls_back_and_escapes_widget_content(self):
        self.assertFalse(
            os.environ.get("OPENROUTER_API_KEY"),
            "Run this focused missing-configuration gate with OPENROUTER_API_KEY empty",
        )
        self.assertNotEqual(os.environ.get("GIDEON_HOSTED"), "1")
        value = source()
        value["candidates"][0]["props"][
            "title"
        ] = "Measured </widget> <script> & values"
        result = asyncio.run(visualize({"genui": value}, title='Title" ><script>'))
        self.assertEqual(result.decision_source, "deterministic_fallback")
        self.assertIn("unavailable or invalid", result.decision_reason)
        self.assertEqual(result.widget.count("</widget>"), 1)
        self.assertNotIn("<script>", result.widget)
        envelope = json.loads(result.dsl)
        self.assertIsNotNone(validate_genui_v2(envelope))
        self.assertEqual(
            envelope["elements"]["options"]["props"]["title"],
            value["candidates"][0]["props"]["title"],
        )

    def test_grouped_missing_configuration_uses_first_supplied_variant(self):
        self.assertFalse(os.environ.get("OPENROUTER_API_KEY"))
        result = asyncio.run(visualize({"genui": variant_source()}))
        self.assertEqual(result.decision_source, "deterministic_fallback")
        self.assertIn("first supplied alternative per group", result.decision_reason)
        envelope = json.loads(result.dsl)
        self.assertIn("facts-table", envelope["elements"])
        self.assertNotIn("facts-bar", envelope["elements"])
        self.assertIn("options", envelope["elements"])
        self.assertIn("review", envelope["elements"])


if __name__ == "__main__":
    unittest.main()
