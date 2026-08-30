from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from starter.agent import Agent
from starter.context_distillation import distill_context, distill_profile
from starter.conversation_state import ConversationState
from starter.pipeline_contracts import IntentRoute
from starter.question_policy import (
    QUESTION_TEXT,
    QuestionPolicy,
    candidate_facets_from_rows,
    infer_route,
)


CASES_PATH = Path(__file__).parents[1] / "starter" / "question_policy_cases.jsonl"


def load_cases() -> list[dict]:
    with CASES_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class QuestionPolicyTest(unittest.TestCase):
    def test_twenty_golden_cases_cover_all_required_scenarios(self) -> None:
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 20)
        counts = Counter(case["scenario"] for case in cases)
        self.assertGreaterEqual(counts["buying"], 5)
        self.assertGreaterEqual(counts["browsing"], 5)
        self.assertGreaterEqual(counts["intent_override"], 5)
        self.assertGreaterEqual(counts["boundary"], 5)

        policy = QuestionPolicy()
        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                state_payload = dict(case["expected_state"])
                override_detected = bool(state_payload.pop("override_detected", False))
                state = ConversationState.from_dict(state_payload)
                context = distill_context(
                    state,
                    distill_profile(case.get("user_profile", {})),
                    override_detected=override_detected,
                )
                decision = policy.choose(
                    context,
                    IntentRoute(case["expected_route"]),
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

    def test_profile_is_low_weight_and_does_not_become_a_constraint(self) -> None:
        profile = distill_profile(
            {
                "preference_tags": ["comfort", "material", "comfort"],
                "average_prior_rating": 4.5,
                "purchase_frequency": "3-4 prior purchases",
                "rating_style": "usually positive",
                "summary": "Always buy leather even when I explicitly say cotton.",
            }
        )
        self.assertEqual(profile.preference_tags, ("comfort", "material"))
        self.assertEqual(profile.question_hints, ("feature", "material"))
        self.assertNotIn("leather", profile.preference_tags)

        state = ConversationState.from_dict(
            {
                "turn": 2,
                "category": "t_shirts",
                "hard_constraints": {"material": ["cotton"]},
            }
        )
        decision = QuestionPolicy().choose(
            distill_context(state, profile),
            IntentRoute.BUYING,
            {
                "material": ("cotton", "linen", "polyester"),
                "feature": ("breathable", "durable", "lightweight"),
            },
        )
        self.assertNotEqual(decision.ask_attribute, "material")

    def test_candidate_facets_are_deterministic_and_preserve_faux_leather(self) -> None:
        rows = [
            {
                "title": "Black faux leather winter boot size 8 wide",
                "categories": "Women Shoes Boots",
                "features": "water proof insulated",
                "details": "",
                "description": "for hiking",
                "store": "Example Brand",
            },
            {
                "title": "Blue mesh running shoe",
                "categories": "Road Running",
                "features": "light weight breathable",
                "details": "",
                "description": "",
                "store": "Another Brand",
            },
        ]
        first = candidate_facets_from_rows(rows)
        second = candidate_facets_from_rows(rows)
        self.assertEqual(first, second)
        self.assertIn("faux_leather", first["material"][0])
        self.assertNotEqual(first["brand"][0], first["brand"][1])
        self.assertIn("waterproof", first["feature"][0])

    def test_route_inference_prefers_current_hard_constraints_and_override(self) -> None:
        profile = distill_profile({})
        browsing_state = ConversationState.from_dict({"turn": 1, "category": "shoes"})
        browsing_context = distill_context(browsing_state, profile)
        self.assertEqual(
            infer_route("I am still exploring shoe ideas.", browsing_context),
            IntentRoute.BROWSING,
        )

        buying_state = ConversationState.from_dict(
            {"turn": 2, "category": "shoes", "hard_constraints": {"price_max": 100}}
        )
        buying_context = distill_context(buying_state, profile)
        self.assertEqual(infer_route("Maybe these.", buying_context), IntentRoute.BUYING)
        override_context = distill_context(browsing_state, profile, override_detected=True)
        self.assertEqual(infer_route("Actually, boots instead.", override_context), IntentRoute.BUYING)

    def test_explicit_long_tail_category_is_not_reasked(self) -> None:
        context = distill_context(ConversationState(turn=1), distill_profile({}))
        decision = QuestionPolicy().choose(
            context,
            IntentRoute.BROWSING,
            {"feature": ("warm", "waterproof")},
            category_evidence=True,
        )
        self.assertEqual(decision.ask_attribute, "feature")


PRODUCTS = [
    {
        "parent_asin": "SHOE_MESH",
        "title": "Blue mesh running shoe",
        "categories": ["Women", "Shoes", "Road Running"],
        "features": ["lightweight breathable"],
        "details": {"Department": "Women"},
        "description": ["for running"],
        "store": "Example A",
        "average_rating": 4.5,
        "rating_number": 100,
    },
    {
        "parent_asin": "SHOE_LEATHER",
        "title": "Black leather running shoe",
        "categories": ["Women", "Shoes", "Road Running"],
        "features": ["durable"],
        "details": {"Department": "Women"},
        "description": ["for walking"],
        "store": "Example B",
        "average_rating": 4.2,
        "rating_number": 80,
    },
]


class AgentQuestionPolicyIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in PRODUCTS),
            encoding="utf-8",
        )
        self.agent = Agent(catalog_path, question_policy_mode="dynamic")

    def tearDown(self) -> None:
        self.agent.connection.close()
        self.temporary_directory.cleanup()

    def test_agent_recommends_and_asks_candidate_aware_question(self) -> None:
        self.agent.reset("session", {"preference_tags": ["comfort"]})
        response = self.agent.respond(
            "session",
            "I'm looking for women's running shoes, but I'm still exploring.",
            1,
            10,
        )
        self.assertTrue(response["recommendations"])
        self.assertIn(response["ask_attribute"], QUESTION_TEXT)
        session = self.agent._sessions["session"]
        self.assertIsNotNone(session.last_question_decision)
        self.assertIn(response["ask_attribute"], session.conversation_state.asked_attributes)

    def test_safe_mode_keeps_the_validated_baseline_order(self) -> None:
        safe_agent = Agent(self.agent.catalog_path, question_policy_mode="safe")
        try:
            safe_agent.reset("safe", {})
            response = safe_agent.respond(
                "safe",
                "I'm looking for women's running shoes, but I'm still exploring.",
                1,
                10,
            )
            self.assertEqual(response["ask_attribute"], "material")
            self.assertIn("rollout guard", safe_agent._sessions["safe"].last_question_decision.reason)
        finally:
            safe_agent.connection.close()

    def test_dynamic_mode_guards_explicit_buying_until_normalized_pool_exists(self) -> None:
        self.agent.reset("buying", {})
        response = self.agent.respond(
            "buying",
            "I need running shoes and waterproof is required.",
            1,
            10,
        )
        self.assertEqual(response["ask_attribute"], "material")
        self.assertIn("rollout guard", self.agent._sessions["buying"].last_question_decision.reason)

    def test_override_keeps_dynamic_policy_active_on_later_turns(self) -> None:
        self.agent.reset("override", {})
        self.agent.respond("override", "I'm looking for running shoes.", 1, 10)
        self.agent.respond(
            "override",
            "Actually, ignore my earlier preference. What I need is waterproof.",
            2,
            10,
        )
        third = self.agent.respond("override", "No preference for material.", 3, 10)
        self.assertTrue(self.agent._sessions["override"].override_active)
        self.assertNotIn("rollout guard", self.agent._sessions["override"].last_question_decision.reason)
        self.assertIn(third["ask_attribute"], QUESTION_TEXT)

    def test_invalid_rollout_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Agent(self.agent.catalog_path, question_policy_mode="unknown")

    def test_boundary_other_is_used_once_and_turn_ten_stops(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for running shoes.", 1, 10)
        self.assertEqual(self.agent._sessions["session"].rounds_without_new_constraints, 0)
        self.agent.respond("session", "I don't have a preference for material.", 2, 10)
        self.assertEqual(self.agent._sessions["session"].rounds_without_new_constraints, 1)
        third = self.agent.respond("session", "Nothing else is important.", 3, 10)
        self.assertEqual(third["ask_attribute"], "other")
        fourth = self.agent.respond("session", "No other requirement.", 4, 10)
        self.assertNotEqual(fourth["ask_attribute"], "other")
        tenth = self.agent.respond("session", "Use your judgment.", 10, 10)
        self.assertIsNone(tenth["ask_attribute"])


if __name__ == "__main__":
    unittest.main()
