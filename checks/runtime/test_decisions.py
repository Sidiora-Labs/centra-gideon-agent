"""Real decision validation and request construction without provider calls."""

import asyncio
import math
import unittest

from gideon.integrations.decisions import (
    ENDPOINT,
    MODEL,
    decision_target,
    request_decision,
    validate_choice,
)


class DecisionTests(unittest.TestCase):
    def test_accepts_maximum_and_ties(self):
        for probabilities in (
            {"a": 0.8, "b": 0.2},
            {"a": 0.5, "b": 0.5},
            {"a": 0.8, "b": 0.19},
        ):
            with self.subTest(probabilities=probabilities):
                self.assertEqual(
                    validate_choice(
                        {
                            "choice": "a",
                            "confidence": 0.9,
                            "probabilities": probabilities,
                        },
                        {"a": "First", "b": "Second"},
                    ),
                    "a",
                )

    def test_rejects_malformed_answers(self):
        invalid = (
            None,
            [],
            {},
            {"choice": "missing", "confidence": 1, "probabilities": {"a": 1}},
            {"choice": [], "confidence": 1, "probabilities": {"a": 1}},
            {"choice": "a", "confidence": 1, "probabilities": []},
            {"choice": "a", "confidence": 1, "probabilities": {"a": 1, "extra": 0}},
            {"choice": "a", "confidence": 1, "probabilities": {}},
        )
        for answer in invalid:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_choice(answer, {"a": "First"})

    def test_rejects_nonfinite_or_out_of_range_scores(self):
        for invalid in (
            math.nan,
            math.inf,
            -math.inf,
            -0.01,
            1.01,
            True,
            "1",
            None,
            10**1000,
        ):
            for field in ("confidence", "probability"):
                answer = {"choice": "a", "confidence": 1, "probabilities": {"a": 1}}
                if field == "confidence":
                    answer[field] = invalid
                else:
                    answer["probabilities"]["a"] = invalid
                with (
                    self.subTest(field=field, value=invalid),
                    self.assertRaises(ValueError),
                ):
                    validate_choice(answer, {"a": "First"})

    def test_rejects_missing_keys_bad_sum_and_nonmaximum(self):
        for probabilities in (
            {"a": 1},
            {"a": 0.3, "b": 0.3},
            {"a": 0.6, "b": 0.6},
            {"a": 0.2, "b": 0.8},
            {"a": 0.4999999, "b": 0.5000001},
        ):
            with (
                self.subTest(probabilities=probabilities),
                self.assertRaises(ValueError),
            ):
                validate_choice(
                    {"choice": "a", "confidence": 1, "probabilities": probabilities},
                    {"a": "First", "b": "Second"},
                )

    def test_direct_target_pins_model_and_preserves_input(self):
        body = {
            "state": {"goal": "Choose a surface"},
            "questions": {},
            "model": "caller-model",
        }
        for purpose in ("genui", "browser"):
            with self.subTest(purpose=purpose):
                url, key, payload = decision_target(
                    body, purpose=purpose, api_key="test-credential"
                )
                self.assertEqual(url, ENDPOINT)
                self.assertEqual(key, "test-credential")
                self.assertEqual(payload, {**body, "model": MODEL})
                self.assertEqual(body["model"], "caller-model")
                self.assertIsNot(payload, body)

    def test_unconfigured_direct_target_fails_closed(self):
        for purpose in ("genui", "browser"):
            with (
                self.subTest(purpose=purpose),
                self.assertRaisesRegex(RuntimeError, "not configured"),
            ):
                decision_target({}, purpose=purpose)

    def test_unknown_purpose_is_rejected_before_transport(self):
        with self.assertRaisesRegex(ValueError, "Unsupported decision purpose"):
            decision_target({}, purpose="../other", api_key="test-credential")
        with self.assertRaisesRegex(ValueError, "Unsupported decision purpose"):
            asyncio.run(request_decision({}, purpose="../other"))


if __name__ == "__main__":
    unittest.main()
