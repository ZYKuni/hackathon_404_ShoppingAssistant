from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.vector_index import InMemoryTfidfIndex


PRODUCTS = (
    {
        "parent_asin": "TRAIL",
        "title": "Waterproof trail running shoe",
        "categories": ["Women", "Shoes", "Trail Running"],
        "features": ["rock plate grippy outsole mountain terrain"],
    },
    {
        "parent_asin": "DRESS",
        "title": "Silk evening dress",
        "categories": ["Women", "Clothing", "Dresses"],
        "features": ["formal wedding cocktail"],
    },
    {
        "parent_asin": "ROAD",
        "title": "Lightweight road running shoe",
        "categories": ["Women", "Shoes", "Road Running"],
        "features": ["breathable pavement trainer"],
    },
)


class InMemoryTfidfIndexTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "catalog.jsonl"
        self.path.write_text(
            "".join(json.dumps(item) + "\n" for item in PRODUCTS), encoding="utf-8"
        )
        self.index = InMemoryTfidfIndex(self.path, min_document_frequency=1)

    def tearDown(self):
        self.directory.cleanup()

    def test_semantic_search_is_deterministic_and_relevant(self):
        first = self.index.search("mountain trail shoe with grip", 3)
        second = self.index.search("mountain trail shoe with grip", 3)
        self.assertEqual(first, second)
        self.assertEqual(first[0].parent_asin, "TRAIL")
        self.assertGreater(first[0].score, 0.0)

    def test_score_many_is_bounded_and_scoped(self):
        scores = self.index.score_many("formal silk wedding", ("TRAIL", "DRESS"))
        self.assertEqual(max(scores, key=scores.get), "DRESS")
        self.assertTrue(all(0.0 <= score <= 1.0 for score in scores.values()))

    def test_stats_and_category_are_available(self):
        self.assertEqual(self.index.stats.document_count, 3)
        self.assertGreater(self.index.stats.posting_count, 0)
        self.assertEqual(self.index.category_key("TRAIL"), "trail running")


if __name__ == "__main__":
    unittest.main()
