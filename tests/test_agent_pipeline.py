from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.orchestrator import RuntimeMode


PRODUCTS = (
    {
        "parent_asin": "SHOE_BLUE",
        "title": "Blue cotton running shoe",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Running"],
        "features": ["lightweight breathable cotton trainer"],
        "details": {"Department": "Women", "Color": "Blue"},
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
        "details": {"Department": "Women", "Color": "Black"},
        "description": ["warm boot for winter hiking"],
        "store": "Example",
        "average_rating": 4.4,
        "rating_number": 80,
    },
)


class AgentPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in PRODUCTS), encoding="utf-8"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_local_pipeline_preserves_public_api_and_session_reset(self):
        agent = Agent(self.catalog_path)
        try:
            agent.reset("session", {"preference_tags": ["comfort"]})
            response = agent.respond(
                "session", "I need a blue cotton running shoe", 1, 2
            )
            self.assertEqual(response["recommendations"][0]["parent_asin"], "SHOE_BLUE")
            self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
            self.assertEqual(agent.pipeline_fallbacks("session"), ())
            self.assertIs(agent._orchestrator.retriever.backend, agent._search_backend)

            agent.reset("broad", {})
            message, attribute = agent._next_question(
                agent._sessions["broad"], "show me options", 1, over_general=True
            )
            self.assertEqual(attribute, "material")
            self.assertIn("broad set", message)

            agent.reset("session", {})
            self.assertEqual(agent._sessions["session"].active_messages, [])
            self.assertEqual(agent.pipeline_fallbacks("session"), ())
        finally:
            agent.connection.close()

    def test_compatibility_constructor_and_explicit_legacy_mode(self):
        formal = Agent.with_local_pipeline(self.catalog_path)
        legacy = Agent.legacy(self.catalog_path)
        try:
            self.assertIsNotNone(formal._orchestrator)
            self.assertIsNone(legacy._orchestrator)
        finally:
            formal.connection.close()
            legacy.connection.close()

    def test_dependency_pair_is_atomic(self):
        class Retriever:
            def retrieve(self, request):  # pragma: no cover - constructor rejects first
                raise AssertionError

        with self.assertRaises(ValueError):
            Agent(self.catalog_path, retriever=Retriever(), runtime_mode=RuntimeMode.OFFICIAL)


if __name__ == "__main__":
    unittest.main()
