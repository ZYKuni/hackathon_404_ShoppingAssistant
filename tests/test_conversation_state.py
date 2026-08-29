from __future__ import annotations

import json
import unittest
from pathlib import Path

from starter.attribute_lexicons import canonicalize
from starter.conversation_state import (
    ConversationState,
    PatchOperation,
    StatePatch,
    apply_patch,
)


CASES_PATH = Path(__file__).parents[1] / "starter" / "state_patch_cases.jsonl"


class ConversationStateTest(unittest.TestCase):
    def test_all_thirty_golden_cases(self) -> None:
        with CASES_PATH.open(encoding="utf-8") as handle:
            cases = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({case["case_id"] for case in cases}), 30)
        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                previous = ConversationState.from_dict(case["previous_state"])
                patch = StatePatch.from_dict(case["patch"])
                actual = apply_patch(previous, patch).to_dict(include_empty=False)
                self.assertEqual(actual, case["expected_state"])

    def test_apply_patch_does_not_mutate_previous_state(self) -> None:
        previous = ConversationState.from_dict({
            "category": "running_shoes",
            "soft_preferences": {"color": ["black"]},
            "turn": 1,
        })
        before = previous.to_dict()
        patch = StatePatch.from_dict({
            "source_turn": 2,
            "operations": [{"op": "add", "field": "color", "value": "blue"}],
        })
        updated = apply_patch(previous, patch)
        self.assertEqual(previous.to_dict(), before)
        self.assertEqual(updated.soft_preferences["color"], ["black", "blue"])

    def test_invalid_field_and_invalid_confidence_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PatchOperation.from_dict({"op": "set", "field": "unknown", "value": "x"})
        with self.assertRaises(ValueError):
            PatchOperation.from_dict({
                "op": "set", "field": "color", "value": "blue", "confidence": 1.1
            })

    def test_budget_no_preference_clears_both_bounds(self) -> None:
        previous = ConversationState.from_dict({
            "hard_constraints": {"price_min": 50, "price_max": 100},
            "turn": 1,
        })
        patch = StatePatch.from_dict({
            "source_turn": 2,
            "operations": [{"op": "set_no_preference", "field": "price_max"}],
        })
        updated = apply_patch(previous, patch)
        self.assertEqual(updated.hard_constraints, {})
        self.assertEqual(updated.no_preference, ["budget"])

    def test_shared_aliases_are_canonicalized(self) -> None:
        self.assertEqual(canonicalize("color", "Grey"), "gray")
        self.assertEqual(canonicalize("material", "faux-leather"), "faux_leather")
        self.assertEqual(canonicalize("category", "road running"), "running_shoes")
        self.assertEqual(canonicalize("audience", "Women's"), "women")


if __name__ == "__main__":
    unittest.main()
