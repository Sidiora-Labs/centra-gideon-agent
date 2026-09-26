"""Real visualize authoring path for explicit, source-bound UISpec records."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from gideon.integrations.mcp_artifacts import _call_tool
from gideon.workspace.genui import uispec_catalog, uispec_library_prompt, validate_uispec_envelope
from gideon.workspace.visualize import visualize


def real_record(template: str) -> dict:
    entry = next(item for item in uispec_catalog() if item["template"] == template)
    bindings = {}
    for key, kind in entry["bindings"].items():
        bindings[key] = (
            [] if kind == "array" else 7 if kind == "number" else True if kind == "boolean"
            else "https://example.test/image.png" if key.endswith(".src") else f"Record {key}"
        )
    return {"schemaVersion": 1, "template": template, "recordId": f"record-{template}", "bindings": bindings}


class UISpecVisualizeTests(unittest.TestCase):
    def test_catalog_has_all_source_structures_and_exact_required_fields(self) -> None:
        catalog = uispec_catalog()
        self.assertEqual(len(catalog), 22)
        self.assertEqual(len({entry["template"] for entry in catalog}), 22)
        self.assertEqual(catalog[0]["title"], "Riverside Loft")
        self.assertEqual(catalog[-1]["title"], "Support tickets")
        prompt = uispec_library_prompt()
        for entry in catalog:
            self.assertIn(entry["template"], prompt)
            self.assertTrue(entry["bindings"])
            self.assertIsNotNone(validate_uispec_envelope(real_record(entry["template"])))

    def test_incomplete_or_changed_records_are_refused(self) -> None:
        record = real_record("create-task")
        record["bindings"].pop(next(iter(record["bindings"])))
        self.assertIsNone(validate_uispec_envelope(record))
        record = real_record("stays")
        record["bindings"]["children.0.src"] = "javascript:alert(1)"
        self.assertIsNone(validate_uispec_envelope(record))

    def test_visualize_emits_distinct_widget_only_for_exact_supplied_record(self) -> None:
        record = real_record("chart-bars")
        seen = {}

        async def completion(prompt: str, *, use_case: str) -> str:
            seen["prompt"] = prompt
            seen["axis"] = use_case
            return json.dumps(record)

        result = asyncio.run(visualize({"generative_ui": record}, "show support counts", completion=completion))
        self.assertEqual(seen["axis"], "reasoning")
        self.assertIn("chart-bars (Support tickets)", seen["prompt"])
        self.assertTrue(result.widget.startswith('<widget kind="uispec"'))
        self.assertEqual(json.loads(result.dsl), record)

        async def changed(prompt: str, *, use_case: str) -> str:
            return json.dumps({**record, "recordId": "invented"})

        with self.assertRaisesRegex(ValueError, "changed the supplied"):
            asyncio.run(visualize({"generative_ui": record}, completion=changed))

    def test_tool_response_contains_block_and_refuses_incomplete_input(self) -> None:
        record = real_record("create-task")

        async def completion(prompt: str, *, use_case: str = "background", **kwargs) -> str:
            return json.dumps(record)

        with patch("gideon.integrations.llm_helpers.one_shot_completion", completion):
            result = _call_tool("visualize", {"data": {"generative_ui": record}})
        self.assertIn('<widget kind="uispec"', result)
        self.assertIn('"template":"create-task"', result)
        record["bindings"].clear()
        refused = _call_tool("visualize", {"data": {"generative_ui": record}})
        self.assertTrue(refused.startswith("Error:"))
        self.assertNotIn('<widget kind="uispec"', refused)

    def test_record_text_cannot_end_the_widget_block(self) -> None:
        record = real_record("chart-bars")
        record["bindings"]["title"] = "Measured </widget> & <script>"

        async def completion(prompt: str, *, use_case: str) -> str:
            return json.dumps(record)

        result = asyncio.run(visualize({"generative_ui": record}, completion=completion))
        self.assertEqual(result.widget.count("</widget>"), 1)
        self.assertIn("\\u003c/widget\\u003e", result.widget)
        self.assertEqual(json.loads(result.dsl)["bindings"]["title"], record["bindings"]["title"])


if __name__ == "__main__":
    unittest.main()
