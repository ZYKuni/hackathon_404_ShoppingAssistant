from __future__ import annotations

import unittest

from analysis.analyze_agent_failures import analyze, classify_miss


def make_turn(turn: int, route_rank=None, pool_rank=None, recommendation_rank=None, message="request"):
    return {
        "turn": turn,
        "user_message": message,
        "state": {},
        "ranking": {
            "routes": [{"name": "active", "target_rank": route_rank}],
            "candidate_pool_target_rank": pool_rank,
            "recommendation_target_rank": recommendation_rank,
        },
        "response": {"ask_attribute": None},
        "fallback": {"used": False},
    }


def make_trace(sample_id: str, turns, scenario="buying"):
    return {
        "diagnostics_available": True,
        "evaluation_context": {
            "sample_id": sample_id,
            "scenario_type": scenario,
            "target_parent_asin": "TARGET",
        },
        "turns": turns,
    }


class FailureAnalysisTests(unittest.TestCase):
    def test_recall_failure_when_target_never_retrieved(self):
        session = {"sample_id": "s1", "scenario_type": "buying", "hit": False}
        failure = classify_miss(session, make_trace("s1", [make_turn(1)]))
        self.assertEqual(failure["primary_failure"], "Recall failure")
        self.assertEqual(failure["confidence"], "high")

    def test_rerank_failure_when_target_is_below_final_cutoff(self):
        session = {"sample_id": "s2", "scenario_type": "browsing", "hit": False}
        failure = classify_miss(
            session, make_trace("s2", [make_turn(1, route_rank=22, pool_rank=18)])
        )
        self.assertEqual(failure["primary_failure"], "Rerank failure")
        self.assertEqual(failure["best_candidate_pool_rank"], 18)

    def test_override_failure_requires_before_after_evidence_loss(self):
        session = {"sample_id": "s3", "scenario_type": "intent_override", "hit": False}
        trace = make_trace("s3", [
            make_turn(1, route_rank=8, pool_rank=7),
            make_turn(2, message="Actually, I need a different size instead"),
        ], scenario="intent_override")
        failure = classify_miss(session, trace)
        self.assertEqual(failure["primary_failure"], "Override failure")
        self.assertEqual(failure["override_turn"], 2)

    def test_analyze_reconciles_all_misses_and_adds_boundary_context(self):
        result = {
            "hit_rate_at_10": 0.5,
            "sessions": [
                {"sample_id": "hit", "scenario_type": "buying", "hit": True},
                {"sample_id": "miss", "scenario_type": "boundary", "hit": False},
            ],
        }
        report = analyze(result, [make_trace("miss", [make_turn(1)], scenario="boundary")])
        self.assertEqual(report["miss_count"], 1)
        self.assertEqual(report["primary_failure_counts"], {"Recall failure": 1})
        self.assertEqual(report["failures"][0]["secondary_labels"], ["Boundary context"])


if __name__ == "__main__":
    unittest.main()
