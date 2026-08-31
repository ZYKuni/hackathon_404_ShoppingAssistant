from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from starter.agent import Agent
from starter.catalog_normalizer import CatalogNormalizer
from starter.conversation_state import ConversationState
from starter.pipeline_contracts import IntentRoute, ProfileSnapshot, RouteDecision
from starter.question_policy import (
    QUESTION_TEXT,
    QuestionPolicy,
    QuestionPolicyMode,
    candidate_facets_from_products,
)
from starter.state_adapter import to_state_snapshot


CASES_PATH = Path(__file__).parents[1] / "starter" / "question_policy_cases.jsonl"


def load_cases() -> list[dict]:
    with CASES_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def route_decision(route: str, *, override: bool = False) -> RouteDecision:
    return RouteDecision(
        route=IntentRoute(route),
        confidence=0.9,
        reason="Golden-case route.",
        override_detected=override,
    )


class QuestionPolicyUnitTests(unittest.TestCase):
    def test_twenty_golden_cases_cover_all_required_scenarios(self) -> None:
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 20)
        counts = Counter(case["scenario"] for case in cases)
        self.assertEqual(
            {name: counts[name] for name in ("buying", "browsing", "intent_override", "boundary")},
            {"buying": 5, "browsing": 5, "intent_override": 5, "boundary": 5},
        )

        policy = QuestionPolicy()
        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                payload = dict(case["expected_state"])
                override = bool(payload.pop("override_detected", False))
                state = to_state_snapshot(ConversationState.from_dict(payload))
                profile_data = case.get("user_profile", {})
                profile = ProfileSnapshot(
                    preference_tags=tuple(dict.fromkeys(profile_data.get("preference_tags", [])))
                )
                decision = policy.choose(
                    state,
                    profile,
                    route_decision(case["expected_route"], override=override),
                    case.get("candidate_facets", {}),
                    rounds_without_new_constraints=case.get("rounds_without_new_constraints", 0),
                    other_used=case.get("other_used", False),
                )
                expected = case["expected_ask_attributes"]
                if expected:
                    self.assertIn(decision.ask_attribute, expected)
                    self.assertEqual(decision.message, QUESTION_TEXT[decision.ask_attribute])
                else:
                    self.assertIsNone(decision.ask_attribute)
                self.assertNotIn(decision.ask_attribute, case.get("must_not_ask", []))

    def test_normalized_candidate_facets_are_aligned_and_deterministic(self) -> None:
        catalog = CatalogNormalizer(PRODUCTS)
        products = tuple(catalog.get(asin) for asin in ("SHOE_MESH", "BOOT_LEATHER"))
        self.assertTrue(all(product is not None for product in products))
        first = candidate_facets_from_products(products)  # type: ignore[arg-type]
        second = candidate_facets_from_products(products)  # type: ignore[arg-type]
        self.assertEqual(first, second)
        self.assertEqual(len(first["material"]), 2)
        self.assertIn("mesh", first["material"][0])
        self.assertIn("leather", first["material"][1])
        self.assertIn("waterproof", first["feature"][1])

    def test_profile_hint_remains_low_weight_and_current_state_wins(self) -> None:
        state = to_state_snapshot(
            ConversationState.from_dict(
                {
                    "turn": 2,
                    "category": "t_shirts",
                    "hard_constraints": {"material": ["cotton"]},
                }
            )
        )
        decision = QuestionPolicy().choose(
            state,
            ProfileSnapshot(preference_tags=("material", "comfort")),
            route_decision("buying"),
            {
                "material": ("cotton", "linen", "polyester"),
                "feature": ("breathable", "durable", "lightweight"),
            },
        )
        self.assertNotEqual(decision.ask_attribute, "material")
        self.assertTrue(all(score.profile_multiplier <= 1.08 for score in decision.scores))

    def test_long_tail_category_evidence_prevents_semantic_reask(self) -> None:
        state = to_state_snapshot(ConversationState(turn=1))
        decision = QuestionPolicy().choose(
            state,
            ProfileSnapshot(),
            route_decision("browsing"),
            {"feature": ("warm", "waterproof")},
            category_evidence=True,
        )
        self.assertEqual(decision.ask_attribute, "feature")


PRODUCTS = (
    {
        "parent_asin": "SHOE_MESH",
        "title": "Blue mesh running shoe",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Running"],
        "features": ["lightweight breathable mesh trainer"],
        "details": {"Department": "Women", "Color": "Blue", "Material": "Mesh"},
        "description": ["comfortable shoe for road running"],
        "store": "Example Shoe",
        "price": 79.0,
        "average_rating": 4.5,
        "rating_number": 100,
    },
    {
        "parent_asin": "BOOT_LEATHER",
        "title": "Black leather winter boot",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Boots"],
        "features": ["waterproof insulated leather"],
        "details": {"Department": "Women", "Color": "Black", "Material": "Leather"},
        "description": ["warm boot for winter hiking"],
        "store": "Example Boot",
        "price": 149.0,
        "average_rating": 4.4,
        "rating_number": 80,
    },
)


class QuestionPolicyAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in PRODUCTS),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_safe_and_shadow_outputs_are_identical(self) -> None:
        safe = Agent(self.catalog_path, question_policy_mode="safe")
        shadow = Agent(self.catalog_path, question_policy_mode="shadow")
        try:
            for agent in (safe, shadow):
                agent.reset("session", {"preference_tags": ["weather"]})
            messages = (
                "I'm looking for shoes, but I'm still exploring.",
                "I don't have an additional preference for material.",
                "Waterproof matters most.",
            )
            for turn, message in enumerate(messages, 1):
                self.assertEqual(
                    shadow.respond("session", message, turn, 2),
                    safe.respond("session", message, turn, 2),
                )
            diagnostics = shadow.question_policy_diagnostics("session")
            self.assertIsNotNone(diagnostics)
            self.assertIs(diagnostics.mode, QuestionPolicyMode.SHADOW)
            self.assertGreater(diagnostics.candidate_count, 0)
        finally:
            safe.connection.close()
            shadow.connection.close()

    def test_dynamic_mode_uses_formal_route_and_candidate_pool(self) -> None:
        agent = Agent(self.catalog_path, question_policy_mode="dynamic")
        try:
            agent.reset("session", {})
            response = agent.respond(
                "session", "I'm looking for shoes, but I'm still exploring.", 1, 2
            )
            diagnostics = agent.question_policy_diagnostics("session")
            self.assertIsNotNone(diagnostics)
            self.assertIs(diagnostics.route, IntentRoute.BROWSING)
            self.assertEqual(diagnostics.candidate_count, 2)
            self.assertEqual(response["ask_attribute"], diagnostics.selected_attribute)
            self.assertIn(response["ask_attribute"], QUESTION_TEXT)
        finally:
            agent.connection.close()

    def test_other_is_used_once_and_turn_ten_stops(self) -> None:
        agent = Agent(self.catalog_path, question_policy_mode="dynamic")
        try:
            agent.reset("boundary", {})
            agent.respond("boundary", "I'm looking for shoes.", 1, 2)
            agent.respond("boundary", "I don't have a preference for material.", 2, 2)
            third = agent.respond("boundary", "Nothing else is important.", 3, 2)
            self.assertEqual(third["ask_attribute"], "other")
            fourth = agent.respond("boundary", "No other requirement.", 4, 2)
            self.assertNotEqual(fourth["ask_attribute"], "other")
            tenth = agent.respond("boundary", "Use your judgment.", 10, 2)
            self.assertIsNone(tenth["ask_attribute"])
        finally:
            agent.connection.close()

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Agent(self.catalog_path, question_policy_mode="unknown")


if __name__ == "__main__":
    unittest.main()
