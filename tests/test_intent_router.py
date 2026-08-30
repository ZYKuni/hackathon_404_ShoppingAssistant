from __future__ import annotations

import unittest

from starter.intent_router import IntentRouter, route
from starter.pipeline_contracts import (
    ConstraintTerm,
    IntentRoute,
    RoutingError,
    StateSnapshot,
)


def snapshot(
    *,
    category: str | None = None,
    hard: tuple[ConstraintTerm, ...] = (),
    soft: tuple[ConstraintTerm, ...] = (),
    excluded: tuple[ConstraintTerm, ...] = (),
) -> StateSnapshot:
    return StateSnapshot(
        schema_version="0.1.0",
        turn=2,
        category=category,
        hard_constraints=hard,
        soft_preferences=soft,
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


class IntentRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter()

    def assert_explainable(self, decision) -> None:
        self.assertTrue(decision.reason.strip())
        self.assertGreaterEqual(decision.confidence, 0.0)
        self.assertLessEqual(decision.confidence, 1.0)
        self.assertTrue(decision.signals)

    def test_explicit_budget_routes_to_buying(self) -> None:
        decision = self.router.route(
            "I need shoes under $120.",
            snapshot(
                category="shoes",
                hard=(ConstraintTerm("price_max", (120,)),),
            ),
            False,
        )
        self.assertEqual(decision.route, IntentRoute.BUYING)
        self.assertIn("has_budget", decision.signals)
        self.assert_explainable(decision)

    def test_explicit_size_routes_to_buying(self) -> None:
        decision = self.router.route(
            "Size 8 wide, please.",
            snapshot(hard=(ConstraintTerm("size", ("8 WIDE",)),)),
            False,
        )
        self.assertEqual(decision.route, IntentRoute.BUYING)
        self.assertIn("has_size", decision.signals)

    def test_exclusion_routes_to_buying(self) -> None:
        decision = self.router.route(
            "Anything but white.",
            snapshot(excluded=(ConstraintTerm("color", ("white",)),)),
            False,
        )
        self.assertEqual(decision.route, IntentRoute.BUYING)
        self.assertIn("has_exclusion", decision.signals)

    def test_still_exploring_routes_to_browsing(self) -> None:
        decision = self.router.route(
            "I'm still exploring women's shoes.", snapshot(category="shoes"), False
        )
        self.assertEqual(decision.route, IntentRoute.BROWSING)
        self.assertIn("still_exploring", decision.signals)

    def test_need_ideas_is_not_misclassified_by_need(self) -> None:
        decision = self.router.route(
            "I need some ideas for a wedding.",
            snapshot(soft=(ConstraintTerm("use_case", ("wedding",)),)),
            False,
        )
        self.assertEqual(decision.route, IntentRoute.BROWSING)
        self.assertIn("needs_ideas", decision.signals)

    def test_category_only_routes_to_browsing(self) -> None:
        decision = route("Show me dresses.", snapshot(category="dresses"))
        self.assertEqual(decision.route, IntentRoute.BROWSING)
        self.assertIn("category_only", decision.signals)

    def test_hard_constraint_wins_over_exploring_language(self) -> None:
        decision = self.router.route(
            "I'm still exploring, but it must be under $80.",
            snapshot(hard=(ConstraintTerm("price_max", (80,)),)),
            False,
        )
        self.assertEqual(decision.route, IntentRoute.BUYING)
        self.assertIn("has_hard_constraint", decision.signals)

    def test_override_is_event_and_route_uses_updated_state(self) -> None:
        previous = self.router.route("I'm still exploring.", snapshot(category="boots"), False)
        current = self.router.route(
            "Actually, make the budget $150.",
            snapshot(
                category="boots",
                hard=(ConstraintTerm("price_max", (150,)),),
            ),
            True,
        )
        self.assertEqual(previous.route, IntentRoute.BROWSING)
        self.assertEqual(current.route, IntentRoute.BUYING)
        self.assertTrue(current.override_detected)
        self.assertIn("override_detected", current.signals)

    def test_soft_single_attribute_defaults_to_browsing(self) -> None:
        decision = self.router.route(
            "Maybe cotton.", snapshot(soft=(ConstraintTerm("material", ("cotton",)),)), False
        )
        self.assertEqual(decision.route, IntentRoute.BROWSING)
        self.assert_explainable(decision)

    def test_invalid_inputs_raise_expected_routing_error(self) -> None:
        with self.assertRaises(RoutingError):
            self.router.route("", snapshot(), False)
        with self.assertRaises(RoutingError):
            self.router.route("hello", snapshot(), "yes")  # type: ignore[arg-type]
        with self.assertRaises(RoutingError):
            self.router.route("hello", object(), False)  # type: ignore[arg-type]
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
