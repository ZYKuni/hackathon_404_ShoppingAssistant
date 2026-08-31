from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.diagnostics import validate_diagnostic_trace


PRODUCTS = [
    {
        "parent_asin": "SHOE_BLUE",
        "title": "Blue cotton running shoe",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Running"],
        "features": ["lightweight breathable cotton trainer"],
        "details": {"Department": "Women"},
        "description": ["comfortable shoe for road running"],
        "store": "Example",
        "average_rating": 4.5,
        "rating_number": 100,
    },
    {
        "parent_asin": "BOOT_BLACK",
        "title": "Black leather winter boot",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Boots"],
        "features": ["waterproof insulated leather"],
        "details": {"Department": "Women"},
        "description": ["warm boot for winter hiking"],
        "store": "Example",
        "average_rating": 4.4,
        "rating_number": 80,
    },
    {
        "parent_asin": "DRESS_RED",
        "title": "Red silk formal dress",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Dresses"],
        "features": ["formal silk evening dress"],
        "details": {"Department": "Women"},
        "description": ["red dress for a wedding"],
        "store": "Example",
        "average_rating": 4.0,
        "rating_number": 50,
    },
]


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in PRODUCTS), encoding="utf-8"
        )
        self.agent = Agent(catalog_path)

    def tearDown(self) -> None:
        self.agent.connection.close()
        self.temporary_directory.cleanup()

    def test_reset_is_required(self) -> None:
        with self.assertRaises(RuntimeError):
            self.agent.respond("missing", "running shoe", 1, 10)

    def test_recommends_relevant_product_and_returns_valid_contract(self) -> None:
        self.agent.reset("session", {"preference_tags": ["comfort"]})
        response = self.agent.respond("session", "I need a blue cotton running shoe", 1, 10)
        self.assertEqual(response["recommendations"][0]["parent_asin"], "SHOE_BLUE")
        self.assertIn(response["ask_attribute"], {"feature", "style", "size", "use_case", "budget", "brand", "other"})
        self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

        structured = self.agent._sessions["session"].conversation_state
        self.assertEqual(structured.category, "running_shoes")
        self.assertEqual(structured.hard_constraints["color"], ["blue"])
        self.assertEqual(structured.hard_constraints["material"], ["cotton"])
        self.assertEqual(structured.turn, 1)

    def test_accumulates_useful_multi_turn_constraints(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for women's shoes, but I'm still exploring.", 1, 10)
        response = self.agent.respond(
            "session", "For that, what matters is: lightweight cotton; road running.", 2, 10
        )
        self.assertEqual(response["recommendations"][0]["parent_asin"], "SHOE_BLUE")

    def test_intent_override_discards_superseded_constraints(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for a red formal dress.", 1, 10)
        response = self.agent.respond(
            "session", "Actually, ignore my earlier preference. What I need is a black winter boot.", 2, 10
        )
        self.assertEqual(response["recommendations"][0]["parent_asin"], "BOOT_BLACK")
        state = self.agent._sessions["session"]
        self.assertNotIn("red formal dress", " ".join(state.active_messages).lower())
        self.assertEqual(state.conversation_state.category, "winter_boots")
        self.assertEqual(state.conversation_state.hard_constraints["color"], ["black"])
        self.assertNotIn("red", state.conversation_state.soft_preferences.get("color", []))

    def test_override_preserves_only_compatible_same_slot_evidence(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for a cotton and rayon shirt.", 1, 10)
        self.agent.respond("session", "Imported with a button closure.", 2, 10)

        self.agent.respond(
            "session",
            "Actually, ignore my earlier preference. What I need is: cotton.",
            3,
            10,
        )

        state = self.agent._sessions["session"]
        self.assertEqual(state.conversation_state.hard_constraints["material"], ["cotton"])
        self.assertEqual(state.conversation_state.soft_preferences["material"], ["rayon"])
        context = " ".join(state.active_messages).lower()
        self.assertIn("cotton and rayon", context)
        self.assertNotIn("button closure", context)

    def test_no_preference_reply_does_not_pollute_search_context(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for running shoes.", 1, 10)
        before = list(self.agent._sessions["session"].active_messages)
        self.agent.respond("session", "I don't have a preference for material.", 2, 10)
        state = self.agent._sessions["session"]
        self.assertEqual(state.active_messages, before)
        self.assertIn("material", state.conversation_state.no_preference)

    def test_legacy_search_uses_conversation_stopwords(self) -> None:
        class CapturingBackend:
            def __init__(self) -> None:
                self.stopwords = None

            def search_legacy(self, query, limit, *, stopwords):
                self.stopwords = stopwords
                return ()

        backend = CapturingBackend()
        self.agent._search_backend = backend
        self.agent._search("preferred matches for running shoes")
        self.assertIn("matches", backend.stopwords)
        self.assertIn("preference", backend.stopwords)

    def test_open_vocabulary_text_remains_available_as_raw_evidence(self) -> None:
        self.agent.reset("session", {})
        message = "I need running shoes with an Ethylene Vinyl Acetate sole."
        self.agent.respond("session", message, 1, 10)

        state = self.agent._sessions["session"]
        self.assertIn(message, state.active_messages)
        self.assertIn("running shoes", self.agent._structured_query(state.conversation_state))

    def test_diagnostic_trace_is_valid_and_detached(self) -> None:
        self.agent.reset("session", {})
        response = self.agent.respond("session", "I need a blue running shoe", 1, 10)
        self.assertEqual(
            set(response), {"message", "ask_attribute", "recommendations", "usage"}
        )
        trace = self.agent.get_diagnostic_trace("session")
        validate_diagnostic_trace(trace)
        turn = trace["turns"][0]
        self.assertEqual([route["name"] for route in turn["ranking"]["routes"]], [
            "active_context", "current_turn", "category_anchor"
        ])
        self.assertEqual(
            turn["response"]["recommendations"][0],
            response["recommendations"][0]["parent_asin"],
        )
        trace["turns"].clear()
        self.assertEqual(len(self.agent.get_diagnostic_trace("session")["turns"]), 1)


if __name__ == "__main__":
    unittest.main()
