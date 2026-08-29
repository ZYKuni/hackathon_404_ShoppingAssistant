from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from starter.pipeline_contracts import (
    PIPELINE_CONTRACT_VERSION,
    Candidate,
    CandidatePool,
    ConstraintTerm,
    IntentRoute,
    ProfileSnapshot,
    RankedCandidate,
    RankerProtocol,
    RankingExplanation,
    RankingResult,
    RetrieverProtocol,
    RouteDecision,
    RouteEvidence,
    SearchRequest,
    StateSnapshot,
    contract_to_dict,
    contract_to_json,
)


def request_fixture() -> SearchRequest:
    state = StateSnapshot(
        schema_version="0.1.0",
        turn=2,
        category="running_shoes",
        hard_constraints=(ConstraintTerm("price_max", (120,)),),
        soft_preferences=(ConstraintTerm("feature", ("lightweight",)),),
        excluded=(ConstraintTerm("color", ("white",)),),
        no_preference=("brand",),
        asked_attributes=("material", "brand"),
    )
    return SearchRequest(
        session_id="session-1",
        turn=2,
        top_k=10,
        candidate_limit=200,
        route_decision=RouteDecision(
            route=IntentRoute.BUYING,
            confidence=0.9,
            reason="A price ceiling and excluded color are known.",
            signals=("has_budget", "has_exclusion"),
        ),
        current_message="It must be lightweight and under $120.",
        raw_context="I need running shoes. It must be lightweight and under $120.",
        base_request="I need running shoes.",
        structured_query="running shoes lightweight",
        state=state,
        profile=ProfileSnapshot(
            preference_tags=("comfort",),
            average_prior_rating=4.5,
            purchase_frequency="3-4 prior purchases",
            rating_style="usually positive",
        ),
    )


def candidate_fixture(parent_asin: str = "ASIN_1", rank: int = 1) -> Candidate:
    return Candidate(
        parent_asin=parent_asin,
        evidence=(RouteEvidence("active_context_bm25", rank, -8.2),),
        rrf_score=1.0 / (60 + rank),
    )


class FakeRetriever:
    def retrieve(self, request: SearchRequest) -> CandidatePool:
        return CandidatePool(
            candidates=(candidate_fixture(),),
            requested_limit=request.candidate_limit,
            route=request.route_decision.route,
            retrieval_latency_ms=1.0,
        )


class FakeRanker:
    def rank(self, request: SearchRequest, pool: CandidatePool) -> RankingResult:
        candidate = pool.candidates[0]
        return RankingResult(
            candidates=(
                RankedCandidate(
                    parent_asin=candidate.parent_asin,
                    final_score=candidate.rrf_score,
                    explanation=RankingExplanation(rrf=1.0),
                ),
            ),
            input_count=len(pool.candidates),
            filtered_count=0,
            unknown_preserved_count=0,
            ranking_latency_ms=1.0,
        )


class PipelineContractsTest(unittest.TestCase):
    def test_contract_version_and_json_diagnostics(self) -> None:
        self.assertEqual(PIPELINE_CONTRACT_VERSION, "1.0")
        request = request_fixture()
        payload = contract_to_dict(request)
        self.assertEqual(payload["route_decision"]["route"], "buying")
        self.assertEqual(payload["state"]["hard_constraints"][0]["field"], "price_max")
        self.assertEqual(json.loads(contract_to_json(request))["candidate_limit"], 200)

    def test_contract_values_are_immutable(self) -> None:
        request = request_fixture()
        with self.assertRaises(FrozenInstanceError):
            request.turn = 3  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            request.state.hard_constraints.append(ConstraintTerm("size", ("M",)))  # type: ignore[attr-defined]

    def test_runtime_protocols_accept_independent_fake_implementations(self) -> None:
        self.assertIsInstance(FakeRetriever(), RetrieverProtocol)
        self.assertIsInstance(FakeRanker(), RankerProtocol)
        request = request_fixture()
        pool = FakeRetriever().retrieve(request)
        result = FakeRanker().rank(request, pool)
        self.assertEqual(result.candidates[0].parent_asin, "ASIN_1")

    def test_route_decision_validates_confidence_and_reason(self) -> None:
        with self.assertRaises(ValueError):
            RouteDecision(IntentRoute.BUYING, 1.1, "invalid")
        with self.assertRaises(ValueError):
            RouteDecision(IntentRoute.BROWSING, 0.5, "")
        with self.assertRaises(ValueError):
            RouteDecision(IntentRoute.BROWSING, 0.5, "broad request", ("broad", "broad"))

    def test_constraint_terms_require_unique_immutable_values(self) -> None:
        with self.assertRaises(TypeError):
            ConstraintTerm("color", ["black"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ConstraintTerm("color", ("black", "black"))
        with self.assertRaises(ValueError):
            StateSnapshot(
                schema_version="0.1.0",
                turn=1,
                hard_constraints=(
                    ConstraintTerm("color", ("black",)),
                    ConstraintTerm("color", ("blue",)),
                ),
            )

    def test_search_request_enforces_turn_and_candidate_limits(self) -> None:
        valid = request_fixture()
        payload = {**valid.__dict__, "top_k": 11}
        with self.assertRaises(ValueError):
            SearchRequest(**payload)
        payload = {**valid.__dict__, "candidate_limit": 5}
        with self.assertRaises(ValueError):
            SearchRequest(**payload)
        payload = {**valid.__dict__, "candidate_limit": 201}
        with self.assertRaises(ValueError):
            SearchRequest(**payload)
        payload = {**valid.__dict__, "turn": 3}
        with self.assertRaises(ValueError):
            SearchRequest(**payload)

    def test_candidate_pool_is_unique_and_capped(self) -> None:
        first = candidate_fixture("ASIN_1", 1)
        duplicate = candidate_fixture("ASIN_1", 2)
        with self.assertRaises(ValueError):
            CandidatePool((first, duplicate), 200, IntentRoute.BUYING, 1.0)
        with self.assertRaises(ValueError):
            CandidatePool((first,), 0, IntentRoute.BUYING, 1.0)
        with self.assertRaises(ValueError):
            CandidatePool((first,), 1, IntentRoute.BUYING, -1.0)

    def test_candidate_requires_valid_route_evidence(self) -> None:
        with self.assertRaises(ValueError):
            RouteEvidence("bm25", 0)
        evidence = RouteEvidence("bm25", 1)
        with self.assertRaises(ValueError):
            Candidate("ASIN_1", (evidence, evidence), 0.1)
        with self.assertRaises(ValueError):
            Candidate("ASIN_1", (evidence,), -0.1)

    def test_ranking_features_are_normalized(self) -> None:
        with self.assertRaises(ValueError):
            RankingExplanation(feature_overlap=1.01)
        explanation = RankingExplanation(
            rrf=1.0,
            exact_phrase=0.5,
            violation_penalty=0.25,
        )
        self.assertEqual(explanation.rrf, 1.0)

    def test_ranking_result_validates_counts_and_identifiers(self) -> None:
        ranked = RankedCandidate("ASIN_1", 0.8, RankingExplanation(rrf=1.0))
        with self.assertRaises(ValueError):
            RankingResult((ranked,), 1, 1, 0, 1.0)
        with self.assertRaises(ValueError):
            RankingResult((ranked, ranked), 2, 0, 0, 1.0)
        with self.assertRaises(ValueError):
            RankingResult((), 1, 0, 2, 1.0)


if __name__ == "__main__":
    unittest.main()
