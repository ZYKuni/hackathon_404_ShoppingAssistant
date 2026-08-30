from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from starter.conversation_state import ConversationState
from starter.pipeline_contracts import ConstraintTerm, StateSnapshot
from starter.state_adapter import (
    build_structured_query,
    to_profile_snapshot,
    to_state_snapshot,
)


class StateAdapterTest(unittest.TestCase):
    def test_conversation_state_becomes_deterministic_immutable_snapshot(self) -> None:
        state = ConversationState.from_dict({
            "schema_version": "0.1.0",
            "category": "running shoes",
            "hard_constraints": {
                "price_max": 120,
                "audience": ["women"],
            },
            "soft_preferences": {
                "material": ["cotton", "mesh"],
                "feature": ["lightweight"],
            },
            "excluded": {"color": ["white"]},
            "no_preference": ["brand"],
            "asked_attributes": ["material", "brand"],
            "turn": 2,
        })

        snapshot = to_state_snapshot(state)

        self.assertEqual(snapshot.schema_version, "0.1.0")
        self.assertEqual(snapshot.turn, 2)
        self.assertEqual(snapshot.category, "running_shoes")
        self.assertEqual(
            snapshot.hard_constraints,
            (
                ConstraintTerm("audience", ("women",)),
                ConstraintTerm("price_max", (120,)),
            ),
        )
        self.assertEqual(
            snapshot.soft_preferences,
            (
                ConstraintTerm("feature", ("lightweight",)),
                ConstraintTerm("material", ("cotton", "mesh")),
            ),
        )
        self.assertEqual(snapshot.excluded, (ConstraintTerm("color", ("white",)),))
        self.assertEqual(snapshot.no_preference, ("brand",))
        self.assertEqual(snapshot.asked_attributes, ("material", "brand"))
        with self.assertRaises(FrozenInstanceError):
            snapshot.turn = 3  # type: ignore[misc]

    def test_snapshot_is_detached_and_does_not_mutate_source(self) -> None:
        state = ConversationState.from_dict({
            "soft_preferences": {"color": ["black"]},
            "turn": 1,
        })
        before = state.to_dict()

        snapshot = to_state_snapshot(state)
        state.soft_preferences["color"].append("blue")

        self.assertEqual(snapshot.soft_preferences, (ConstraintTerm("color", ("black",)),))
        self.assertEqual(before["soft_preferences"], {"color": ["black"]})

    def test_structured_query_contains_only_positive_state(self) -> None:
        snapshot = StateSnapshot(
            schema_version="0.1.0",
            turn=3,
            category="running_shoes",
            hard_constraints=(
                ConstraintTerm("audience", ("women",)),
                ConstraintTerm("price_max", (120,)),
            ),
            soft_preferences=(
                ConstraintTerm("feature", ("water_resistant", "running shoes")),
                ConstraintTerm("material", ("mesh",)),
            ),
            excluded=(ConstraintTerm("color", ("white",)),),
            no_preference=("brand",),
            asked_attributes=("material", "color"),
        )

        query = build_structured_query(snapshot)

        self.assertEqual(query, "running shoes women 120 water resistant mesh")
        self.assertNotIn("white", query)
        self.assertNotIn("brand", query)
        self.assertNotIn("color", query)

    def test_empty_state_builds_empty_query(self) -> None:
        snapshot = to_state_snapshot(ConversationState())
        self.assertEqual(build_structured_query(snapshot), "")

    def test_profile_snapshot_is_normalized_deduplicated_and_separate(self) -> None:
        profile = {
            "preference_tags": [" Comfort ", "comfort", "Weather Resistant"],
            "average_prior_rating": 4,
            "purchase_frequency": "3-4 prior purchases",
            "rating_style": "usually positive",
            "summary": "This field is intentionally outside the shared contract.",
        }

        snapshot = to_profile_snapshot(profile)

        self.assertEqual(snapshot.preference_tags, ("comfort", "weather resistant"))
        self.assertEqual(snapshot.average_prior_rating, 4.0)
        self.assertEqual(snapshot.purchase_frequency, "3-4 prior purchases")
        self.assertEqual(snapshot.rating_style, "usually positive")
        self.assertFalse(hasattr(snapshot, "summary"))
        profile["preference_tags"].append("style")
        self.assertEqual(snapshot.preference_tags, ("comfort", "weather resistant"))

    def test_empty_profile_uses_contract_defaults(self) -> None:
        snapshot = to_profile_snapshot({})
        self.assertEqual(snapshot.preference_tags, ())
        self.assertIsNone(snapshot.average_prior_rating)
        self.assertIsNone(snapshot.purchase_frequency)
        self.assertIsNone(snapshot.rating_style)

    def test_invalid_adapter_inputs_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            to_state_snapshot({})  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            to_profile_snapshot(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            to_profile_snapshot({"preference_tags": "comfort"})
        with self.assertRaises(TypeError):
            to_profile_snapshot({"preference_tags": [1]})
        with self.assertRaises(TypeError):
            build_structured_query({})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
