from __future__ import annotations

import unittest

from starter.pipeline_contracts import (
    ConstraintTerm,
    IntentRoute,
    ProfileSnapshot,
    RetrievalError,
    RouteDecision,
    SearchRequest,
    StateSnapshot,
)
from starter.retrieval import (
    ACTIVE_CONTEXT_ROUTE,
    CATEGORY_ANCHOR_ROUTE,
    CURRENT_TURN_ROUTE,
    POPULARITY_ROUTE,
    STRUCTURED_CONSTRAINT_ROUTE,
    USE_CASE_ROUTE,
    VECTOR_SIMILARITY_ROUTE,
    HybridRetriever,
    RetrievalRouteError,
    SearchHit,
)


class FakeBackend:
    def __init__(self, responses=None, *, fail_queries=(), popularity=None):
        self.responses = responses or {}
        self.fail_queries = set(fail_queries)
        all_ids = {
            hit.parent_asin for hits in self.responses.values() for hit in hits
        }
        all_ids.update(hit.parent_asin for hit in (popularity or ()))
        self._valid_asins = frozenset(all_ids or {"P1", "P2", "P3"})
        self._popularity = tuple(popularity or (SearchHit("P3", 10.0),))
        self.calls = []

    @property
    def valid_asins(self):
        return self._valid_asins

    def search(self, query, limit):
        self.calls.append(query)
        if query in self.fail_queries:
            raise RetrievalRouteError(query)
        return tuple(self.responses.get(query, ()))[:limit]

    def popularity(self, limit):
        return self._popularity[:limit]

    def category_key(self, parent_asin):
        return "same" if parent_asin in {"P1", "P2"} else "other"


class FakeVectorBackend:
    def __init__(self, hits=(), error=None, categories=None):
        self.hits = tuple(hits)
        self.error = error
        self.categories = categories or {}
        self.calls = []

    def search(self, query, limit):
        self.calls.append(query)
        if self.error:
            raise self.error
        return self.hits[:limit]

    def score_many(self, query, parent_asins):
        return {}

    def category_key(self, parent_asin):
        return self.categories.get(parent_asin, "unknown")


def request(route=IntentRoute.BUYING, *, candidate_limit=200, empty=False):
    message = "the" if empty else "lightweight running shoes"
    state = StateSnapshot(
        schema_version="0.1.0",
        turn=1,
        category=None if empty else "running_shoes",
        hard_constraints=() if empty else (ConstraintTerm("price_max", (120,)),),
        soft_preferences=() if empty else (ConstraintTerm("use_case", ("running",)),),
    )
    return SearchRequest(
        session_id="S1",
        turn=1,
        top_k=min(10, candidate_limit),
        candidate_limit=candidate_limit,
        route_decision=RouteDecision(route, 0.9, "test route"),
        current_message=message,
        raw_context="" if empty else message,
        base_request="" if empty else "running shoes",
        structured_query="" if empty else "running shoes under 120",
        state=state,
        profile=ProfileSnapshot(),
    )


class HybridRetrieverTests(unittest.TestCase):
    def test_buying_and_browsing_use_distinct_stable_routes(self):
        buying = HybridRetriever._route_specs(request(IntentRoute.BUYING))
        browsing = HybridRetriever._route_specs(request(IntentRoute.BROWSING))
        self.assertEqual(
            {item.name for item in buying},
            {ACTIVE_CONTEXT_ROUTE, CURRENT_TURN_ROUTE, CATEGORY_ANCHOR_ROUTE, STRUCTURED_CONSTRAINT_ROUTE},
        )
        self.assertEqual(
            {item.name for item in browsing},
            {
                ACTIVE_CONTEXT_ROUTE, CURRENT_TURN_ROUTE, CATEGORY_ANCHOR_ROUTE,
                USE_CASE_ROUTE, VECTOR_SIMILARITY_ROUTE,
            },
        )

    def test_browsing_uses_vector_route_and_buying_does_not(self):
        vector = FakeVectorBackend((SearchHit("P3", 0.9),))
        backend = FakeBackend({"lightweight running shoes": (SearchHit("P1"),)})
        backend._valid_asins = frozenset({"P1", "P3"})
        browsing = HybridRetriever(backend=backend, vector_backend=vector).retrieve(
            request(IntentRoute.BROWSING)
        )
        p3 = next(item for item in browsing.candidates if item.parent_asin == "P3")
        self.assertIn(VECTOR_SIMILARITY_ROUTE, {item.route_name for item in p3.evidence})
        before = len(vector.calls)
        HybridRetriever(backend=backend, vector_backend=vector).retrieve(
            request(IntentRoute.BUYING)
        )
        self.assertEqual(len(vector.calls), before)

    def test_vector_failure_falls_back_to_lexical_routes(self):
        backend = FakeBackend({"lightweight running shoes": (SearchHit("P1"),)})
        vector = FakeVectorBackend(error=ValueError("unavailable"))
        result = HybridRetriever(backend=backend, vector_backend=vector).retrieve(
            request(IntentRoute.BROWSING)
        )
        self.assertIn("P1", {item.parent_asin for item in result.candidates})

    def test_browsing_diversity_reorders_without_changing_membership(self):
        hits = tuple(SearchHit(f"P{i}") for i in range(1, 7))
        backend = FakeBackend({"lightweight running shoes": hits})
        backend._valid_asins = frozenset(item.parent_asin for item in hits)
        vector = FakeVectorBackend(categories={
            "P1": "shoe", "P2": "shoe", "P3": "shoe",
            "P4": "boot", "P5": "dress", "P6": "hat",
        })
        result = HybridRetriever(
            backend=backend,
            vector_backend=vector,
            diversity_window=6,
            diversity_category_cap=1,
        ).retrieve(request(IntentRoute.BROWSING, candidate_limit=6))
        identifiers = [item.parent_asin for item in result.candidates]
        self.assertEqual(set(identifiers), {item.parent_asin for item in hits})
        self.assertLess(identifiers.index("P4"), identifiers.index("P2"))

    def test_rrf_evidence_merge_deduplication_and_rank(self):
        backend = FakeBackend({
            "lightweight running shoes": (SearchHit("P1", -3.0), SearchHit("P2", -2.0)),
            "running shoes": (SearchHit("P2", -4.0), SearchHit("P1", -1.0)),
            "running shoes under 120": (SearchHit("P1", -5.0),),
        })
        pool = HybridRetriever(backend=backend).retrieve(request())
        self.assertEqual(pool.candidates[0].parent_asin, "P1")
        evidence = pool.candidates[0].evidence
        self.assertEqual(len({item.route_name for item in evidence}), len(evidence))
        self.assertTrue(all(item.rank >= 1 for item in evidence))
        expected = 1.40 / 61 + 0.85 / 61 + 0.25 / 62 + 0.75 / 61
        self.assertAlmostEqual(pool.candidates[0].rrf_score, expected)

    def test_duplicate_within_route_uses_first_rank(self):
        backend = FakeBackend({
            "lightweight running shoes": (SearchHit("P1"), SearchHit("P1"), SearchHit("P2")),
        })
        pool = HybridRetriever(backend=backend).retrieve(request(candidate_limit=2))
        active = next(
            item for item in pool.candidates[0].evidence
            if item.route_name == ACTIVE_CONTEXT_ROUTE
        )
        self.assertEqual(active.rank, 1)

    def test_candidate_pool_is_capped_unique_prefilter_and_valid(self):
        hits = tuple(SearchHit(f"P{i:03}") for i in range(250))
        backend = FakeBackend({"lightweight running shoes": hits})
        pool = HybridRetriever(backend=backend, per_route_limit=250).retrieve(
            request(candidate_limit=200)
        )
        self.assertEqual(len(pool.candidates), 200)
        self.assertEqual(len({item.parent_asin for item in pool.candidates}), 200)
        self.assertTrue(all(item.parent_asin in backend.valid_asins for item in pool.candidates))

    def test_empty_query_uses_popularity_fallback(self):
        backend = FakeBackend(popularity=(SearchHit("P2", 20.0), SearchHit("P1", 10.0)))
        pool = HybridRetriever(backend=backend).retrieve(request(empty=True))
        self.assertEqual([item.parent_asin for item in pool.candidates], ["P2", "P1"])
        self.assertEqual(pool.candidates[0].evidence[0].route_name, POPULARITY_ROUTE)
        self.assertEqual(backend.calls, [])

    def test_one_route_failure_keeps_other_routes(self):
        backend = FakeBackend(
            {"running shoes": (SearchHit("P2"),)},
            fail_queries={"lightweight running shoes"},
        )
        pool = HybridRetriever(backend=backend).retrieve(request())
        self.assertIn("P2", {item.parent_asin for item in pool.candidates})

    def test_all_routes_failure_raises_retrieval_error(self):
        req = request()
        queries = {item.query for item in HybridRetriever._route_specs(req)}
        backend = FakeBackend(fail_queries=queries)
        with self.assertRaisesRegex(RetrievalError, "all usable"):
            HybridRetriever(backend=backend).retrieve(req)

    def test_no_results_falls_back_to_popularity(self):
        backend = FakeBackend(popularity=(SearchHit("P3", 9.0),))
        pool = HybridRetriever(backend=backend).retrieve(request())
        self.assertEqual(pool.candidates[0].parent_asin, "P3")
        self.assertEqual(pool.candidates[0].evidence[0].route_name, POPULARITY_ROUTE)

    def test_invalid_asin_is_not_emitted(self):
        backend = FakeBackend({"lightweight running shoes": (SearchHit("P1"),)})
        backend._valid_asins = frozenset({"P2", "P3"})
        pool = HybridRetriever(backend=backend).retrieve(request())
        self.assertNotIn("P1", {item.parent_asin for item in pool.candidates})

    def test_output_order_is_deterministic_by_asin_on_tie(self):
        backend = FakeBackend({
            "lightweight running shoes": (SearchHit("P2"), SearchHit("P1")),
        })
        first = HybridRetriever(backend=backend).retrieve(request())
        second = HybridRetriever(backend=backend).retrieve(request())
        self.assertEqual(first.candidates, second.candidates)


if __name__ == "__main__":
    unittest.main()
