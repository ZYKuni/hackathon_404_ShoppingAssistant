from __future__ import annotations

import unittest
from collections import Counter

from analysis.create_stratified_folds import assignment_sha256, build_folds


class StratifiedFoldsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = []
        scenario_counts = {"buying": 80, "browsing": 80, "intent_override": 30, "boundary": 10}
        index = 0
        for scenario, count in scenario_counts.items():
            for offset in range(count):
                self.rows.append({
                    "sample_id": f"sample_{index:04d}",
                    "scenario_type": scenario,
                    "difficulty_bucket": "easy" if offset % 3 else "hard",
                })
                index += 1

    def test_five_folds_have_exact_scenario_counts(self) -> None:
        folds = build_folds(self.rows, n_splits=5, seed=404)
        for fold in folds:
            counts = Counter(
                row["scenario_type"]
                for row in self.rows
                if row["sample_id"] in set(fold["sample_ids"])
            )
            self.assertEqual(counts, {
                "buying": 16,
                "browsing": 16,
                "intent_override": 6,
                "boundary": 2,
            })
            self.assertEqual(len(fold["sample_ids"]), 40)

    def test_assignment_is_deterministic_and_seeded(self) -> None:
        first = build_folds(self.rows, n_splits=5, seed=404)
        second = build_folds(self.rows, n_splits=5, seed=404)
        different = build_folds(self.rows, n_splits=5, seed=405)
        self.assertEqual(assignment_sha256(first), assignment_sha256(second))
        self.assertNotEqual(assignment_sha256(first), assignment_sha256(different))


if __name__ == "__main__":
    unittest.main()
