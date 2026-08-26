from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent


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

    def test_no_preference_reply_does_not_pollute_search_context(self) -> None:
        self.agent.reset("session", {})
        self.agent.respond("session", "I'm looking for running shoes.", 1, 10)
        before = list(self.agent._sessions["session"].active_messages)
        self.agent.respond("session", "I don't have a preference for material.", 2, 10)
        self.assertEqual(self.agent._sessions["session"].active_messages, before)


if __name__ == "__main__":
    unittest.main()
