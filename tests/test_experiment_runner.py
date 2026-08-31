from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis.run_agent_experiments import (
    DEFAULT_CONFIG,
    aggregate_cross_validation,
    annotate_diagnostic_trace,
    append_registry,
    compare_sessions,
    load_fold_sample_ids,
    percentile,
)


class ExperimentRunnerTest(unittest.TestCase):
    def test_frozen_configs_select_distinct_agent_paths(self) -> None:
        config_dir = Path(__file__).resolve().parents[1] / "analysis" / "configs"
        legacy = json.loads(
            (config_dir / "legacy_bm25_rrf.json").read_text(encoding="utf-8")
        )
        integrated = json.loads(
            (config_dir / "integrated_guarded_rerank.json").read_text(encoding="utf-8")
        )

        self.assertFalse(legacy["agent"]["kwargs"]["use_local_pipeline"])
        self.assertTrue(integrated["agent"]["kwargs"]["use_local_pipeline"])
        self.assertEqual(legacy["system_variant"], "frozen_legacy_reference")
        self.assertEqual(integrated["system_variant"], "formal_integrated_offline")
        self.assertEqual(integrated["agent"]["kwargs"]["dense_mode"], "off")
        self.assertEqual(integrated["agent"]["kwargs"]["question_policy_mode"], "safe")
        self.assertEqual(DEFAULT_CONFIG, config_dir / "integrated_guarded_rerank.json")

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

    def test_cross_validation_uses_sample_standard_deviation(self) -> None:
        entries = []
        for fold, hit_rate in enumerate([0.5, 0.7]):
            entries.append({
                "experiment_id": f"fold_{fold}",
                "commit": "abc",
                "dirty": False,
                "metrics": {
                    "hit_rate_at_10": hit_rate,
                    "mrr": hit_rate,
                    "mttc": 5.0,
                    "efficiency": 0.6,
                    "recommended_technical_score": hit_rate,
                    "scenario_metrics": {
                        "buying": {
                            "hit_rate_at_10": hit_rate,
                            "mrr": hit_rate,
                            "mttc": 5.0,
                        }
                    },
                },
            })
        summary = aggregate_cross_validation(entries)
        self.assertEqual(summary["overall"]["hit_rate_at_10"]["mean"], 0.6)
        self.assertAlmostEqual(summary["overall"]["hit_rate_at_10"]["std"], 0.141421)

    def test_diagnostic_annotation_adds_target_ranks(self) -> None:
        trace = {
            "schema_version": "1.0.0",
            "session_id": "runtime",
            "turns": [{
                "turn": 1,
                "user_message": "shoe",
                "state": {},
                "ranking": {
                    "routes": [{"name": "bm25", "candidate_ids": ["A", "TARGET"]}],
                    "candidate_pool": ["A", "TARGET"],
                    "recommendations": [{"parent_asin": "TARGET"}],
                },
                "response": {},
            }],
        }
        annotated = annotate_diagnostic_trace(trace, {
            "sample_id": "public_1",
            "scenario_type": "buying",
            "target_parent_asin": "TARGET",
        })
        ranking = annotated["turns"][0]["ranking"]
        self.assertEqual(ranking["routes"][0]["target_rank"], 2)
        self.assertEqual(ranking["candidate_pool_target_rank"], 2)
        self.assertEqual(ranking["recommendation_target_rank"], 1)
        self.assertNotIn("evaluation_context", trace)


if __name__ == "__main__":
    unittest.main()
