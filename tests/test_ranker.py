from __future__ import annotations

import unittest

from starter.catalog_normalizer import CatalogNormalizer, normalize_product
from starter.pipeline_contracts import (
    Candidate,
    CandidatePool,
    ConstraintTerm,
    IntentRoute,
    ProfileSnapshot,
    RankingError,
    RouteDecision,
    RouteEvidence,
    SearchRequest,
    StateSnapshot,
)
from starter.ranker import LocalConstraintRanker, RankerWeights


def raw(asin, *, color=None, price=100, features=None, category="Running Shoes", ratings=10):
    details = {"Department": "Womens"}
    if color is not None:
        details["Color"] = color
    return {
        "parent_asin": asin,
        "title": f"{color or ''} {category}",
        "features": features or [],
        "description": [],
        "price": price,
        "categories": ["Clothing, Shoes & Jewelry", "Women", category],
        "details": details,
        "average_rating": 4.5,
        "rating_number": ratings,
        "store": "Acme",
    }


def request(*, hard=(), soft=(), excluded=(), category="running_shoes", profile=()):
    state = StateSnapshot(
        schema_version="0.1.0", turn=1, category=category,
        hard_constraints=hard, soft_preferences=soft, excluded=excluded,
    )
    return SearchRequest(
        session_id="S1", turn=1, top_k=10, candidate_limit=10,
        route_decision=RouteDecision(IntentRoute.BUYING, 0.9, "test"),
        current_message="lightweight black running shoes",
        raw_context="lightweight black running shoes",
        base_request="running shoes",
        structured_query="running shoes lightweight black",
        state=state,
        profile=ProfileSnapshot(preference_tags=profile),
    )


def pool(*items, route=IntentRoute.BUYING):
    candidates = tuple(
        Candidate(asin, (RouteEvidence("active_context_bm25", rank, -float(rank)),), score)
        for rank, (asin, score) in enumerate(items, 1)
    )
    return CandidatePool(candidates, 10, route, 1.0)


class LocalConstraintRankerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = CatalogNormalizer([
            raw("A_BLACK", color="Black", features=["Lightweight breathable"], ratings=100),
            raw("B_WHITE", color="White", features=["Heavy duty"], ratings=10),
            raw("C_UNKNOWN", color=None, price=None, features=[], ratings=0),
            raw("D_BLACK", color="Black", features=["Lightweight breathable"], ratings=100),
        ])
        self.ranker = LocalConstraintRanker(catalog=self.catalog)

    def test_explanation_features_are_normalized(self):
        result = self.ranker.rank(request(
            hard=(ConstraintTerm("color", ("black",)),),
            soft=(ConstraintTerm("feature", ("lightweight",)),),
            profile=("breathable",),
        ), pool(("A_BLACK", 0.4)))
        explanation = result.candidates[0].explanation
        self.assertTrue(all(0.0 <= value <= 1.0 for value in explanation.__dict__.values()))
        self.assertGreater(explanation.feature_overlap, 0)
        self.assertEqual(explanation.profile_alignment, 1.0)

    def test_unknown_is_preserved_and_counted(self):
        result = self.ranker.rank(request(
            hard=(ConstraintTerm("price_max", (120,)), ConstraintTerm("color", ("black",))),
        ), pool(("C_UNKNOWN", 0.3)))
        self.assertEqual([item.parent_asin for item in result.candidates], ["C_UNKNOWN"])
        self.assertEqual(result.unknown_preserved_count, 1)
        self.assertEqual(result.filtered_count, 0)

    def test_hard_mismatch_is_filtered(self):
        result = self.ranker.rank(request(
            hard=(ConstraintTerm("color", ("black",)),),
        ), pool(("A_BLACK", 0.4), ("B_WHITE", 0.5)))
        self.assertEqual([item.parent_asin for item in result.candidates], ["A_BLACK"])
        self.assertEqual(result.input_count, 2)
        self.assertEqual(result.filtered_count, 1)

    def test_soft_mismatch_is_not_filtered_and_is_penalized(self):
        result = self.ranker.rank(request(
            soft=(ConstraintTerm("color", ("black",)),),
        ), pool(("A_BLACK", 0.4), ("B_WHITE", 0.4)))
        self.assertEqual(result.filtered_count, 0)
        by_id = {item.parent_asin: item for item in result.candidates}
        self.assertGreater(by_id["B_WHITE"].explanation.violation_penalty, 0)
        self.assertGreater(by_id["A_BLACK"].final_score, by_id["B_WHITE"].final_score)

    def test_excluded_match_is_filtered(self):
        result = self.ranker.rank(request(
            excluded=(ConstraintTerm("color", ("white",)),),
        ), pool(("A_BLACK", 0.4), ("B_WHITE", 0.5)))
        self.assertEqual([item.parent_asin for item in result.candidates], ["A_BLACK"])
        self.assertEqual(result.filtered_count, 1)

    def test_final_score_order_can_uplift_better_match(self):
        result = self.ranker.rank(request(
            soft=(ConstraintTerm("feature", ("lightweight",)),),
        ), pool(("B_WHITE", 0.5), ("A_BLACK", 0.4)))
        self.assertEqual(result.candidates[0].parent_asin, "A_BLACK")

    def test_tie_break_is_parent_asin(self):
        result = self.ranker.rank(
            request(category=None), pool(("D_BLACK", 0.4), ("A_BLACK", 0.4))
        )
        self.assertEqual(
            [item.parent_asin for item in result.candidates], ["A_BLACK", "D_BLACK"]
        )

    def test_counts_are_consistent(self):
        result = self.ranker.rank(request(
            hard=(ConstraintTerm("color", ("black",)),),
        ), pool(("A_BLACK", 0.4), ("B_WHITE", 0.3), ("C_UNKNOWN", 0.2)))
        self.assertEqual(result.input_count, 3)
        self.assertEqual(result.filtered_count, 1)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.unknown_preserved_count, 1)

    def test_profile_weight_is_capped(self):
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            RankerWeights(profile_alignment=0.031)

    def test_missing_catalog_candidate_raises_ranking_error(self):
        with self.assertRaisesRegex(RankingError, "absent"):
            self.ranker.rank(request(), pool(("MISSING", 0.1)))

    def test_route_mismatch_raises_ranking_error(self):
        with self.assertRaisesRegex(RankingError, "route"):
            self.ranker.rank(request(), pool(("A_BLACK", 0.1), route=IntentRoute.BROWSING))

    def test_semantic_scorer_reranks_only_configured_top_n(self):
        class Scorer:
            def __init__(self):
                self.parent_asins = ()

            def score_many(self, query, parent_asins):
                self.parent_asins = parent_asins
                return {"B_WHITE": 1.0, "A_BLACK": 0.0}

        scorer = Scorer()
        ranker = LocalConstraintRanker(
            catalog=self.catalog,
            semantic_scorer=scorer,
            semantic_top_n=2,
            weights=RankerWeights(semantic_similarity=2.0),
        )
        result = ranker.rank(
            request(category=None),
            pool(("A_BLACK", 0.5), ("B_WHITE", 0.49), ("C_UNKNOWN", 0.1)),
        )
        self.assertEqual(set(scorer.parent_asins), {"A_BLACK", "B_WHITE"})
        self.assertEqual(result.candidates[0].parent_asin, "B_WHITE")
        self.assertEqual(result.candidates[-1].parent_asin, "C_UNKNOWN")
        self.assertEqual(result.candidates[0].explanation.semantic_similarity, 1.0)

    def test_semantic_failure_uses_local_ranker_order(self):
        class BrokenScorer:
            def score_many(self, query, parent_asins):
                raise ValueError("model unavailable")

        ranker = LocalConstraintRanker(catalog=self.catalog, semantic_scorer=BrokenScorer())
        result = ranker.rank(
            request(category=None), pool(("A_BLACK", 0.5), ("B_WHITE", 0.4))
        )
        self.assertEqual(result.candidates[0].parent_asin, "A_BLACK")
        self.assertTrue(ranker.last_semantic_fallback)

    def test_semantic_top_n_is_capped_at_thirty(self):
        with self.assertRaises(ValueError):
            LocalConstraintRanker(catalog=self.catalog, semantic_top_n=31)


if __name__ == "__main__":
    unittest.main()
