from __future__ import annotations

import unittest

from starter.intent_router import IntentRouter
from starter.pipeline_contracts import ConstraintTerm, IntentRoute, StateSnapshot


def snapshot(*, category="running_shoes", hard=(), excluded=()):
    return StateSnapshot(
        schema_version="0.1.0",
        turn=1,
        category=category,
        hard_constraints=hard,
        excluded=excluded,
    )


class IntentRouterTests(unittest.TestCase):
    def test_hard_constraint_wins_over_browsing_language(self):
        decision = IntentRouter().route(
            "I'm still exploring but it must be under $100",
            snapshot(hard=(ConstraintTerm("price_max", (100,)),)),
        )
        self.assertIs(decision.route, IntentRoute.BUYING)
        self.assertIn("has_budget", decision.signals)

    def test_category_only_and_explicit_exploration_are_browsing(self):
        router = IntentRouter()
        self.assertIs(
            router.route("I'm looking for running shoes", snapshot()).route,
            IntentRoute.BROWSING,
        )
        self.assertIs(
            router.route("I need ideas for a trip", snapshot(category=None)).route,
            IntentRoute.BROWSING,
        )

    def test_exclusion_routes_to_buying_and_override_is_exposed(self):
        decision = IntentRouter().route(
            "Actually, no white shoes",
            snapshot(excluded=(ConstraintTerm("color", ("white",)),)),
            override_detected=True,
        )
        self.assertIs(decision.route, IntentRoute.BUYING)
        self.assertTrue(decision.override_detected)
        self.assertTrue(decision.reason)


if __name__ == "__main__":
    unittest.main()
