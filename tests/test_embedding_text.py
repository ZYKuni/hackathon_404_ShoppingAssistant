from __future__ import annotations

import unittest

from starter.embedding_text import (
    TEXT_SCHEMA_VERSION,
    build_product_embedding_text,
    build_query_embedding_text,
)
from starter.pipeline_contracts import (
    ConstraintTerm,
    IntentRoute,
    ProfileSnapshot,
    RouteDecision,
    SearchRequest,
    StateSnapshot,
)


class EmbeddingTextTests(unittest.TestCase):
    def test_product_text_is_prioritized_compact_and_safe(self):
        product = {
            "parent_asin": "SECRET_ID",
            "title": "<b>Waterproof Hiking Boot</b>",
            "categories": ["Clothing, Shoes & Jewelry", "Women, Boots, Hiking"],
            "features": ["Waterproof", "Non-slip sole"],
            "details": {
                "Material": "Leather",
                "Color": "Black",
                "Random Internal Field": "do not embed",
            },
            "description": ["Warm &amp; comfortable for wet trails."],
            "price": 89.99,
            "average_rating": 4.8,
            "rating_number": 1234,
        }

        text = build_product_embedding_text(product)

        self.assertTrue(text.startswith("Product: Waterproof Hiking Boot."))
        self.assertIn("Category: Shoes & Jewelry > Women > Boots > Hiking.", text)
        self.assertIn("Features: Waterproof; Non-slip sole.", text)
        self.assertIn("Details: material Leather; color Black.", text)
        self.assertIn("Description: Warm & comfortable for wet trails.", text)
        self.assertNotIn("SECRET_ID", text)
        self.assertNotIn("89.99", text)
        self.assertNotIn("1234", text)
        self.assertNotIn("Random Internal Field", text)
        self.assertEqual(TEXT_SCHEMA_VERSION, "product-query-text-v1")

    def test_product_text_is_deterministic_and_does_not_mutate_input(self):
        product = {
            "title": "Cotton Shirt",
            "categories": ["Men", "Shirts"],
            "features": ["Lightweight"],
            "details": {"Fabric Type": ["Cotton", "Cotton"]},
        }
        before = repr(product)

        first = build_product_embedding_text(product)
        second = build_product_embedding_text(product)

        self.assertEqual(first, second)
        self.assertEqual(repr(product), before)
        self.assertIn("fabric Cotton", first)

    def test_query_uses_soft_positive_state_and_redacts_exclusions(self):
        request = SearchRequest(
            session_id="S1",
            turn=2,
            top_k=10,
            candidate_limit=200,
            route_decision=RouteDecision(IntentRoute.BROWSING, 0.9, "test"),
            current_message="I want a white or blue hiking shoe, but not red.",
            raw_context="I want a white or blue hiking shoe, but not red.",
            base_request="hiking shoe",
            structured_query="hiking shoes blue waterproof",
            state=StateSnapshot(
                schema_version="0.1.0",
                turn=2,
                category="hiking_shoes",
                hard_constraints=(ConstraintTerm("size", (8,)),),
                soft_preferences=(
                    ConstraintTerm("use_case", ("hiking",)),
                    ConstraintTerm("feature", ("waterproof",)),
                    ConstraintTerm("color", ("blue",)),
                ),
                excluded=(
                    ConstraintTerm("color", ("white", "red")),
                ),
            ),
            profile=ProfileSnapshot(),
        )

        text = build_query_embedding_text(request)

        self.assertIn("Looking for hiking shoes.", text)
        self.assertIn("Use: hiking.", text)
        self.assertIn("waterproof", text)
        self.assertIn("blue", text)
        self.assertNotIn("white", text.lower())
        self.assertNotIn("red", text.lower())
        self.assertNotIn("8", text)


if __name__ == "__main__":
    unittest.main()
