from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from starter.conversation_state import ConversationState
from starter.state_adapter import build_structured_query, to_profile_snapshot, to_state_snapshot


class StateAdapterTests(unittest.TestCase):
    def test_snapshot_is_stable_immutable_and_separates_negative_state(self):
        state = ConversationState(
            turn=2,
            category="running_shoes",
            hard_constraints={"size": ["wide"], "price_max": 120},
            soft_preferences={"feature": ["lightweight"]},
            excluded={"color": ["white"]},
            no_preference=["brand"],
            asked_attributes=["material", "brand"],
        )
        snapshot = to_state_snapshot(state)

        self.assertEqual([item.field for item in snapshot.hard_constraints], ["price_max", "size"])
        self.assertEqual(snapshot.excluded[0].values, ("white",))
        query = build_structured_query(snapshot)
        self.assertIn("running shoes", query)
        self.assertIn("lightweight", query)
        self.assertNotIn("white", query)
        self.assertNotIn("brand", query)
        state.hard_constraints["size"].append("narrow")
        self.assertEqual(snapshot.hard_constraints[1].values, ("wide",))
        with self.assertRaises(FrozenInstanceError):
            snapshot.turn = 3  # type: ignore[misc]

    def test_profile_has_only_safe_aggregate_fields(self):
        profile = to_profile_snapshot({
            "preference_tags": ["comfort"],
            "average_prior_rating": "4.5",
            "purchase_frequency": "frequent",
            "rating_style": "positive",
            "target_asin": "MUST_NOT_LEAK",
        })
        self.assertEqual(profile.preference_tags, ("comfort",))
        self.assertFalse(hasattr(profile, "target_asin"))


if __name__ == "__main__":
    unittest.main()
