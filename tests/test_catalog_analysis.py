from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis.catalog_analysis import PROJECT_ROOT, analyze, load_jsonl, percentile, portable_source_path


class CatalogAnalysisTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertIsNone(percentile([], 0.5))

    def test_report_source_paths_do_not_expose_local_directories(self) -> None:
        self.assertEqual(
            portable_source_path(PROJECT_ROOT / "data" / "catalog.jsonl"),
            "data/catalog.jsonl",
        )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                portable_source_path(Path(directory) / "external.jsonl"),
                "<external>/external.jsonl",
            )

    def test_profiles_keys_missingness_and_session_coverage(self) -> None:
        products = [
            {
                "parent_asin": "B000000001",
                "title": "Blue Shirt",
                "features": ["Cotton"],
                "description": [],
                "price": 20.0,
                "categories": ["Clothing", "Shirts"],
                "details": {"Department": "Men"},
                "average_rating": 4.5,
                "rating_number": 10,
                "store": "Example",
            },
            {
                "parent_asin": "B000000002",
                "title": "Blue Shirt",
                "features": [],
                "description": ["Casual shirt"],
                "price": None,
                "categories": ["Clothing", "Shirts"],
                "details": {},
                "average_rating": 4.0,
                "rating_number": 0,
                "store": "Example",
            },
        ]
        sessions = [
            {
                "sample_id": "sample_1",
                "scenario_type": "buying",
                "difficulty_bucket": "easy",
                "ground_truth": {"parent_asin": "B000000001"},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.jsonl"
            sessions_path = root / "sessions.jsonl"
            catalog_path.write_text("\n".join(json.dumps(row) for row in products), encoding="utf-8")
            sessions_path.write_text("\n".join(json.dumps(row) for row in sessions), encoding="utf-8")

            result = analyze(catalog_path, sessions_path)

        self.assertEqual(result["catalog"]["row_count"], 2)
        self.assertEqual(result["catalog"]["primary_key"]["duplicate_key_count"], 0)
        self.assertEqual(result["catalog"]["fields"]["price"]["missing_rate"], 0.5)
        self.assertEqual(result["catalog"]["price"]["numeric_coverage_rate"], 0.5)
        self.assertEqual(result["catalog"]["titles"]["repeated_normalized_title_rows"], 2)
        self.assertEqual(result["sessions"]["missing_target_count"], 0)
        price_coverage = next(
            item
            for item in result["target_coverage"]["overall_targets"]
            if item["field"] == "price"
        )
        self.assertEqual(price_coverage["difference_percentage_points"], 50.0)
        self.assertEqual(result["hidden_constraints"]["evaluator_reproduction_mismatch_count"], 0)
        self.assertEqual(result["bm25"]["oracle_all_fields"]["overall"]["top_100_rate"], 1.0)

    def test_load_jsonl_reports_bad_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                load_jsonl(path)


if __name__ == "__main__":
    unittest.main()
