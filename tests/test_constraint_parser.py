from __future__ import annotations

import json
import unittest
from pathlib import Path

from starter.constraint_parser import parse_message
from starter.conversation_state import ConversationState, apply_patch


CASES_PATH = Path(__file__).parents[1] / "starter" / "state_patch_cases.jsonl"


class ConstraintParserTest(unittest.TestCase):
    def test_all_golden_messages_reach_expected_state(self) -> None:
        with CASES_PATH.open(encoding="utf-8") as handle:
            cases = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(cases), 30)

        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                previous = ConversationState.from_dict(case["previous_state"])
                turn = int(case["patch"]["source_turn"])
                patch = parse_message(case["user_message"], previous, turn)
                actual = apply_patch(previous, patch).to_dict(include_empty=False)
                self.assertEqual(actual, case["expected_state"], patch.to_dict())

    def test_parser_is_deterministic_and_does_not_mutate_state(self) -> None:
        state = ConversationState.from_dict({
            "category": "running_shoes",
            "soft_preferences": {"color": ["black"]},
            "turn": 1,
        })
        before = state.to_dict()
        first = parse_message("Blue is also fine.", state, 2)
        second = parse_message("Blue is also fine.", state, 2)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(state.to_dict(), before)

    def test_negation_is_not_emitted_as_positive_preference(self) -> None:
        state = ConversationState()
        patch = parse_message("No leather and definitely not white.", state, 1)
        updated = apply_patch(state, patch)
        self.assertEqual(updated.excluded, {"material": ["leather"], "color": ["white"]})
        self.assertNotIn("material", updated.soft_preferences)
        self.assertNotIn("color", updated.soft_preferences)

    def test_invalid_arguments_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            parse_message(None, ConversationState(), 1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            parse_message("shoes", object(), 1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            parse_message("shoes", ConversationState(), 11)


if __name__ == "__main__":
    unittest.main()
