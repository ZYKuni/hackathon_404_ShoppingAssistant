from __future__ import annotations

import unittest

from starter.conversation_state import ConversationState
from starter.orchestrator import AgentOrchestrator, RuntimeMode
from starter.pipeline_contracts import (
    Candidate,
    CandidatePool,
    RankedCandidate,
    RankingError,
    RankingExplanation,
    RankingResult,
    RetrievalError,
    RouteEvidence,
)


class FakeRetriever:
    def __init__(self, error=None):
        self.error = error
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return CandidatePool(
            candidates=(
                Candidate("P1", (RouteEvidence("test", 1),), 0.4),
                Candidate("P2", (RouteEvidence("test", 2),), 0.3),
            ),
            requested_limit=request.candidate_limit,
            route=request.route_decision.route,
            retrieval_latency_ms=1.0,
        )


class FakeRanker:
    def __init__(self, error=None):
        self.error = error

    def rank(self, request, pool):
        if self.error:
            raise self.error
        return RankingResult(
            candidates=(
                RankedCandidate("P2", 0.9, RankingExplanation(rrf=1.0)),
                RankedCandidate("P1", 0.8, RankingExplanation(rrf=0.8)),
            ),
            input_count=2,
            filtered_count=0,
            unknown_preserved_count=0,
            ranking_latency_ms=1.0,
        )


def execute(orchestrator):
    return orchestrator.execute(
        session_id="S1",
        turn=1,
        top_k=1,
        current_message="I need running shoes under $100",
        raw_context="I need running shoes under $100",
        base_request="I need running shoes",
        state=ConversationState(
            turn=1, category="running_shoes", hard_constraints={"price_max": 100}
        ),
        profile={"preference_tags": ["comfort"]},
        override_detected=False,
        legacy_fallback=lambda: [{"parent_asin": "LEGACY", "score": 0.1}],
    )


class OrchestratorTests(unittest.TestCase):
    def test_passes_complete_request_and_uses_ranker_order(self):
        retriever = FakeRetriever()
        result = execute(AgentOrchestrator(
            retriever, FakeRanker(), runtime_mode=RuntimeMode.DEVELOPMENT
        ))
        self.assertEqual(result.recommendations, (("P2", 0.9),))
        self.assertEqual(result.fallbacks, ())
        self.assertEqual(retriever.requests[0].structured_query, "running shoes 100")
        self.assertEqual(retriever.requests[0].profile.preference_tags, ("comfort",))

    def test_official_retrieval_error_falls_back_to_legacy(self):
        result = execute(AgentOrchestrator(FakeRetriever(RetrievalError("failed")), FakeRanker()))
        self.assertEqual(result.recommendations[0][0], "LEGACY")
        self.assertEqual(result.fallbacks, ("legacy_retrieval",))

    def test_official_ranking_error_uses_rrf_order(self):
        result = execute(AgentOrchestrator(FakeRetriever(), FakeRanker(RankingError("failed"))))
        self.assertEqual(result.recommendations[0][0], "LEGACY")
        self.assertEqual(result.fallbacks, ("rrf_ranking",))

    def test_official_mode_preserves_legacy_candidate_set(self):
        orchestrator = AgentOrchestrator(FakeRetriever(), FakeRanker())
        result = orchestrator.execute(
            session_id="S1", turn=1, top_k=2,
            current_message="I need running shoes", raw_context="I need running shoes",
            base_request="running shoes",
            state=ConversationState(turn=1, category="running_shoes"), profile={},
            override_detected=False,
            legacy_fallback=lambda: [
                {"parent_asin": "P1", "score": 0.2},
                {"parent_asin": "LEGACY", "score": 0.1},
            ],
        )
        self.assertEqual({item[0] for item in result.recommendations}, {"P1", "LEGACY"})

    def test_development_mode_surfaces_expected_failure(self):
        orchestrator = AgentOrchestrator(
            FakeRetriever(RetrievalError("failed")), FakeRanker(),
            runtime_mode=RuntimeMode.DEVELOPMENT,
        )
        with self.assertRaises(RetrievalError):
            execute(orchestrator)

    def test_candidate_limit_validation(self):
        with self.assertRaises(ValueError):
            AgentOrchestrator(FakeRetriever(), FakeRanker(), candidate_limit=201)


if __name__ == "__main__":
    unittest.main()
