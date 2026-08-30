from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis.run_agent_experiments import (
    append_registry,
    compare_sessions,
    load_fold_sample_ids,
    percentile,
)


class ExperimentRunnerTest(unittest.TestCase):
    def test_percentile_interpolates_and_handles_empty_values(self) -> None:
        self.assertIsNone(percentile([], 0.95))
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)

    def test_compare_sessions_reports_gains_and_losses(self) -> None:
        baseline = [
            {"sample_id": "gain", "hit": False},
            {"sample_id": "loss", "hit": True},
            {"sample_id": "same", "hit": True},
        ]
        current = [
            {"sample_id": "gain", "hit": True},
            {"sample_id": "loss", "hit": False},
            {"sample_id": "same", "hit": True},
        ]
        result = compare_sessions(current, baseline)
        self.assertEqual(result["gained_sessions"], ["gain"])
        self.assertEqual(result["lost_sessions"], ["loss"])
        self.assertEqual(result["shared_session_count"], 3)

    def test_registry_is_valid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.jsonl"
            append_registry(path, {"experiment_id": "first", "metric": 1})
            append_registry(path, {"experiment_id": "second", "metric": 2})
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["experiment_id"] for row in rows], ["first", "second"])

    def test_fold_loader_rejects_a_different_dataset_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "folds.json"
            path.write_text(json.dumps({
                "dataset_sha256": "expected",
                "folds": [{"fold": 0, "sample_ids": ["sample_1"]}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dataset hash"):
                load_fold_sample_ids(path, 0, expected_dataset_sha256="different")


if __name__ == "__main__":
    unittest.main()
