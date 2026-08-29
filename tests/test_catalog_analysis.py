from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.catalog_analysis import analyze_catalog, build_audit_sample, render_markdown


PRODUCTS = [
    {
        "parent_asin": "A",
        "title": "Women's blue cotton running shoe",
        "features": ["lightweight and comfortable"],
        "description": ["for road running"],
        "price": 49.0,
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Road Running"],
        "details": {"Department": "Women", "Color": "Blue", "Material": "Cotton"},
        "average_rating": 4.5,
        "rating_number": 100,
        "store": "Example",
    },
    {
        "parent_asin": "B",
        "title": "Black leather winter boot",
        "features": [],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Boots"],
        "details": {"Department": "Women", "Material": "Leather"},
        "average_rating": 4.2,
        "rating_number": 50,
        "store": "Example",
    },
    {
        "parent_asin": "C",
        "title": "Red silk evening dress",
        "features": ["formal"],
        "description": None,
        "price": "from 20.00",
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Dresses"],
        "details": {},
        "average_rating": 4.0,
        "rating_number": 10,
        "store": None,
    },
    {
        "parent_asin": "D",
        "title": "Second running shoe",
        "features": ["breathable"],
        "description": ["walking and gym"],
        "price": 60.0,
        "categories": ["Clothing, Shoes & Jewelry", "Men", "Shoes", "Road Running"],
        "details": {"Department": "Men"},
        "average_rating": 3.9,
        "rating_number": 5,
        "store": "Other",
    },
]


class CatalogAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.directory.name) / "catalog.jsonl"
        self.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in PRODUCTS), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_profile_reports_completeness_price_and_lexicon_coverage(self) -> None:
        profile = analyze_catalog(self.catalog_path, top_n=5)
        self.assertEqual(profile["row_count"], 4)
        self.assertEqual(profile["unique_parent_asin_count"], 4)
        self.assertEqual(profile["field_stats"]["price"]["missing_or_empty"], 1)
        self.assertEqual(profile["price_stats"]["numeric_count"], 2)
        self.assertEqual(profile["price_stats"]["text_count"], 1)
        self.assertEqual(profile["top_leaf_categories"][0], {"value": "Road Running", "count": 2})
        self.assertGreaterEqual(profile["lexicon_coverage"]["material"]["matched_products"], 3)

    def test_markdown_contains_decision_relevant_sections(self) -> None:
        markdown = render_markdown(analyze_catalog(self.catalog_path))
        self.assertIn("Field completeness", markdown)
        self.assertIn("Price is missing", markdown)
        self.assertIn("PASS / FAIL / UNKNOWN", markdown)

    def test_audit_sample_is_balanced_unique_and_reproducible(self) -> None:
        profile = analyze_catalog(self.catalog_path, top_n=5)
        first = build_audit_sample(self.catalog_path, profile, sample_size=3, category_count=2, seed=7)
        second = build_audit_sample(self.catalog_path, profile, sample_size=3, category_count=2, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual(len({row["parent_asin"] for row in first}), 3)
        self.assertEqual({row["raw_leaf_category"] for row in first}, {"Road Running", "Boots"})


if __name__ == "__main__":
    unittest.main()
