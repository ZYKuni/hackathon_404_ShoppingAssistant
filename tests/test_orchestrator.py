from __future__ import annotations

import unittest

from starter.orchestrator import (
    AgentOrchestrator,
    LegacyQuestionPolicyAdapter,
    LegacyRankerAdapter,
    LegacyRetrieverAdapter,
    OrchestrationSession,
    QuestionDecision,
    QuestionPolicyError,
    RuntimeMode,
    update_session_state,
)
from starter.pipeline_contracts import (
    Candidate,
    CandidatePool,
    IntentRoute,
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
    RouteDecision,
    RouteEvidence,
    RoutingError,
)


def candidate(parent_asin: str, rank: int) -> Candidate:
    return Candidate(
        parent_asin=parent_asin,
        evidence=(RouteEvidence("fake", rank),),
        rrf_score=1.0 / (60 + rank),
    )


class FakeRouter:
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail
        self.last_state = None

    def route(self, current_message, state, override_detected):
        self.calls.append("route")
        self.last_state = state
        if self.fail:
            raise RoutingError("injected router failure")
        return RouteDecision(
            IntentRoute.BUYING,
            0.9,
            "A fake hard requirement is known.",
            ("fake_signal",),
            override_detected,
        )


class FakeRetriever:
    def __init__(self, calls: list[str], *, fail: bool = False, prefix: str = "A") -> None:
        self.calls = calls
        self.fail = fail
        self.prefix = prefix
        self.last_request = None

    def retrieve(self, request):
        self.calls.append(f"retrieve:{self.prefix}")
        self.last_request = request
        if self.fail:
            raise RetrievalError("injected retrieval failure")
        return CandidatePool(
            candidates=tuple(candidate(f"{self.prefix}{index}", index) for index in range(1, 13)),
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
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail
        self.last_request = None
        self.last_pool = None

    def rank(self, request, pool):
        self.calls.append("rank")
        self.last_request = request
        self.last_pool = pool
        if self.fail:
            raise RankingError("injected ranking failure")
        ranked = tuple(
            RankedCandidate(item.parent_asin, 1.0 - index / 100, RankingExplanation())
            for index, item in enumerate(reversed(pool.candidates), start=1)
        )
        return RankingResult(ranked, len(pool.candidates), 0, 0, 1.0)


class FakeQuestionPolicy:
    def __init__(self, calls: list[str], *, fail: bool = False, attribute: str = "material") -> None:
        self.calls = calls
        self.fail = fail
        self.attribute = attribute

    def choose(self, session, request, ranking):
        self.calls.append(f"question:{self.attribute}")
        if self.fail:
            raise QuestionPolicyError("injected question failure")
        session.conversation_state.asked_attributes.append(self.attribute)
        session.last_asked_attribute = self.attribute
        return QuestionDecision("Do you have a preference?", self.attribute)


class UnexpectedFailureRetriever:
    def retrieve(self, request):
        raise RuntimeError("unexpected bug")


class AgentOrchestratorTest(unittest.TestCase):
    def build(self, *, mode=RuntimeMode.OFFICIAL, router_fail=False, retrieve_fail=False,
              rank_fail=False, question_fail=False):
        calls: list[str] = []
        router = FakeRouter(calls, fail=router_fail)
        retriever = FakeRetriever(calls, fail=retrieve_fail)
        fallback = FakeRetriever(calls, prefix="F")
        ranker = FakeRanker(calls, fail=rank_fail)
        question = FakeQuestionPolicy(calls, fail=question_fail)
        fallback_question = FakeQuestionPolicy(calls, attribute="other")
        orchestrator = AgentOrchestrator(
            retriever,
            ranker,
            question,
            router=router,
            fallback_retriever=fallback,
            fallback_question_policy=fallback_question,
            runtime_mode=mode,
        )
        return orchestrator, calls, router, retriever, ranker

    def test_complete_call_order_and_search_request(self) -> None:
        orchestrator, calls, router, retriever, ranker = self.build()
        orchestrator.reset("s1", {"preference_tags": ["comfort"]})
        response = orchestrator.respond("s1", "I need running shoes under $90.", 1, 7)

        self.assertEqual(calls, ["route", "retrieve:A", "rank", "question:material"])
        request = retriever.last_request
        self.assertIs(request, ranker.last_request)
        self.assertEqual(request.session_id, "s1")
        self.assertEqual(request.turn, request.state.turn)
        self.assertEqual(request.top_k, 7)
        self.assertEqual(request.candidate_limit, 200)
        self.assertEqual(request.current_message, "I need running shoes under $90.")
        self.assertIn("shoes", request.raw_context)
        self.assertIn("shoes", request.structured_query)
        self.assertEqual(request.profile.preference_tags, ("comfort",))
        self.assertEqual(router.last_state.turn, 1)
        self.assertEqual(len(response["recommendations"]), 7)
        self.assertEqual(response["recommendations"][0]["parent_asin"], "A12")

    def test_retrieval_failure_uses_legacy_fallback_in_official_mode(self) -> None:
        orchestrator, calls, *_ = self.build(retrieve_fail=True)
        orchestrator.reset("s", {})
        response = orchestrator.respond("s", "Show me shoes.", 1, 10)
        self.assertEqual(response["recommendations"][0]["parent_asin"], "F12")
        self.assertEqual(calls[:4], ["route", "retrieve:A", "retrieve:F", "rank"])
        self.assertIn("retrieval_fallback", orchestrator.diagnostics("s").events)

    def test_ranking_failure_preserves_candidate_pool_order(self) -> None:
        orchestrator, _, *_ = self.build(rank_fail=True)
        orchestrator.reset("s", {})
        response = orchestrator.respond("s", "Show me shoes.", 1, 10)
        self.assertEqual(
            [item["parent_asin"] for item in response["recommendations"]],
            [f"A{index}" for index in range(1, 11)],
        )
        self.assertIn("ranking_fallback", orchestrator.diagnostics("s").events)

    def test_router_and_question_fallbacks_are_observable(self) -> None:
        orchestrator, _, *_ = self.build(router_fail=True, question_fail=True)
        orchestrator.reset("s", {})
        response = orchestrator.respond("s", "Show me shoes.", 1, 10)
        self.assertEqual(response["ask_attribute"], "other")
        diagnostics = orchestrator.diagnostics("s")
        self.assertIn("routing_fallback", diagnostics.events)
        self.assertIn("question_fallback", diagnostics.events)

    def test_development_mode_raises_each_expected_failure(self) -> None:
        configurations = (
            {"router_fail": True},
            {"retrieve_fail": True},
            {"rank_fail": True},
            {"question_fail": True},
        )
        expected = (RoutingError, RetrievalError, RankingError, QuestionPolicyError)
        for config, error in zip(configurations, expected):
            with self.subTest(config=config):
                orchestrator, *_ = self.build(mode=RuntimeMode.DEVELOPMENT, **config)
                orchestrator.reset("s", {})
                with self.assertRaises(error):
                    orchestrator.respond("s", "Show me shoes.", 1, 10)

    def test_unexpected_exception_is_never_hidden(self) -> None:
        orchestrator, *_ = self.build()
        orchestrator.retriever = UnexpectedFailureRetriever()
        orchestrator.reset("s", {})
        with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
            orchestrator.respond("s", "Show me shoes.", 1, 10)

    def test_top_k_is_capped_and_recommendations_are_unique(self) -> None:
        orchestrator, *_ = self.build()
        orchestrator.reset("s", {})
        response = orchestrator.respond("s", "Show me shoes.", 1, 99)
        identifiers = [item["parent_asin"] for item in response["recommendations"]]
        self.assertEqual(len(identifiers), 10)
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_reset_isolates_sessions_and_replaces_existing_state(self) -> None:
        orchestrator, *_ = self.build()
        orchestrator.reset("one", {"preference_tags": ["comfort"]})
        orchestrator.reset("two", {"preference_tags": ["style"]})
        orchestrator.respond("one", "I need shoes under $80.", 1, 10)
        self.assertEqual(orchestrator.sessions["two"].conversation_state.turn, 0)
        self.assertEqual(orchestrator.sessions["two"].active_messages, [])
        orchestrator.reset("one", {})
        self.assertEqual(orchestrator.sessions["one"].conversation_state.turn, 0)
        self.assertEqual(orchestrator.sessions["one"].user_profile, {})

    def test_legacy_retriever_fuses_three_routes_and_adds_popularity_fallback(self) -> None:
        calls = []

        def search(text, limit):
            calls.append((text, limit))
            return {
                "raw evidence": ["X", "Y"],
                "current": ["Y", "Z"],
                "base": ["X"],
            }.get(text, [])

        request_orchestrator, *_ = self.build()
        request_orchestrator.reset("s", {})
        session = request_orchestrator.sessions["s"]
        session.base_request = "base"
        session.active_messages = ["raw evidence"]
        session.conversation_state.turn = 1
        decision = RouteDecision(IntentRoute.BUYING, 0.8, "Focused request.")
        from starter.orchestrator import build_search_request
        request = build_search_request(
            session_id="s", turn=1, top_k=4, candidate_limit=4,
            decision=decision, current_message="current", session=session,
        )

        pool = LegacyRetrieverAdapter(search, ["P", "X"]).retrieve(request)
        self.assertEqual(calls, [("raw evidence", 120), ("current", 120), ("base", 120)])
        self.assertEqual([item.parent_asin for item in pool.candidates], ["Y", "X", "Z", "P"])
        self.assertEqual([item.route_name for item in pool.candidates[0].evidence],
                         ["active_context", "current_message"])
        ranking = LegacyRankerAdapter().rank(request, pool)
        self.assertEqual([item.parent_asin for item in ranking.candidates], ["Y", "X", "Z", "P"])

    def test_legacy_question_policy_preserves_fixed_question_behavior(self) -> None:
        policy = LegacyQuestionPolicyAdapter(
            lambda text: {"material"} if "cotton" in text else set(),
            ("material", "feature"),
            {"material": "Material?", "feature": "Feature?"},
        )
        orchestrator, *_ = self.build()
        orchestrator.reset("s", {})
        session = orchestrator.sessions["s"]
        session.active_messages = ["cotton shoes"]
        session.conversation_state.turn = 1
        decision = RouteDecision(IntentRoute.BUYING, 0.8, "Focused request.")
        from starter.orchestrator import build_search_request, ranking_from_pool
        request = build_search_request(
            session_id="s", turn=1, top_k=1, candidate_limit=1,
            decision=decision, current_message="cotton shoes", session=session,
        )
        pool = CandidatePool((candidate("A", 1),), 1, IntentRoute.BUYING, 0.0)
        result = policy.choose(session, request, ranking_from_pool(pool))
        self.assertEqual(result, QuestionDecision("Feature?", "feature"))


class OverrideLifecycleTest(unittest.TestCase):
    def test_same_category_color_replacement_preserves_category(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I'm looking for running shoes.", 1)
        update_session_state(session, "I prefer black.", 2)
        detected = update_session_state(session, "Actually, blue instead of black.", 3)

        self.assertTrue(detected)
        self.assertEqual(session.conversation_state.category, "running_shoes")
        self.assertEqual(session.conversation_state.soft_preferences["color"], ["blue"])
        self.assertNotIn("I prefer black.", session.active_messages)

    def test_budget_replacement_keeps_unrelated_constraints(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I need cotton running shoes under $100.", 1)
        detected = update_session_state(session, "Actually, change my budget to $150.", 2)

        self.assertTrue(detected)
        self.assertEqual(session.conversation_state.hard_constraints["price_max"], 150)
        self.assertEqual(session.conversation_state.hard_constraints["material"], ["cotton"])
        self.assertEqual(session.conversation_state.category, "running_shoes")

    def test_complete_category_switch_discards_old_product_evidence(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I need a red formal dress.", 1)
        detected = update_session_state(
            session,
            "Actually, ignore my earlier preference. What I need is a black winter boot.",
            2,
        )

        self.assertTrue(detected)
        self.assertEqual(session.conversation_state.category, "winter_boots")
        self.assertEqual(session.active_messages, [
            "Actually, ignore my earlier preference. What I need is a black winter boot."
        ])
        self.assertNotIn("red", session.conversation_state.soft_preferences.get("color", []))

    def test_open_vocabulary_override_keeps_new_raw_evidence(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I'm looking for running shoes.", 1)
        update_session_state(session, "A key requirement is: Ethylene Vinyl Acetate sole.", 2)
        detected = update_session_state(
            session, "Actually, replace that with a Button closure.", 3
        )

        self.assertTrue(detected)
        self.assertIn("Actually, replace that with a Button closure.", session.active_messages)
        self.assertNotIn(
            "A key requirement is: Ethylene Vinyl Acetate sole.", session.active_messages
        )

    def test_excluded_value_can_be_allowed_without_becoming_positive(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I'm looking for running shoes.", 1)
        update_session_state(session, "Definitely not black.", 2)
        self.assertEqual(session.conversation_state.excluded["color"], ["black"])

        detected = update_session_state(session, "Black is okay now.", 3)
        self.assertTrue(detected)
        self.assertNotIn("color", session.conversation_state.excluded)
        self.assertNotIn("black", session.conversation_state.hard_constraints.get("color", []))
        self.assertNotIn("black", session.conversation_state.soft_preferences.get("color", []))

    def test_full_override_clears_asked_and_no_preference_state(self) -> None:
        session = OrchestrationSession({})
        update_session_state(session, "I'm looking for running shoes.", 1)
        session.conversation_state.asked_attributes.extend(["material", "color"])
        session.conversation_state.no_preference.extend(["brand", "size"])

        update_session_state(
            session,
            "Actually, ignore my earlier preference. What I need is a winter boot.",
            2,
        )
        self.assertEqual(session.conversation_state.asked_attributes, [])
        self.assertEqual(session.conversation_state.no_preference, [])

    def test_full_override_preserves_profile_but_clears_exclusions(self) -> None:
        profile = {"preference_tags": ["comfort"]}
        session = OrchestrationSession(profile)
        update_session_state(session, "I need running shoes, definitely not white.", 1)
        self.assertEqual(session.conversation_state.excluded["color"], ["white"])

        update_session_state(
            session,
            "Actually, ignore my earlier preference. What I need is a winter boot.",
            2,
        )
        self.assertEqual(session.user_profile, profile)
        self.assertEqual(session.conversation_state.excluded, {})

    def test_override_reexecutes_router_with_updated_state(self) -> None:
        calls: list[str] = []
        orchestrator = AgentOrchestrator(
            FakeRetriever(calls),
            FakeRanker(calls),
            FakeQuestionPolicy(calls),
        )
        orchestrator.reset("s", {})
        orchestrator.respond("s", "I'm still exploring running shoes.", 1, 10)
        self.assertEqual(
            orchestrator.diagnostics("s").route_decision.route, IntentRoute.BROWSING
        )

        orchestrator.respond("s", "Actually, change my budget to $80.", 2, 10)
        decision = orchestrator.diagnostics("s").route_decision
        self.assertEqual(decision.route, IntentRoute.BUYING)
        self.assertTrue(decision.override_detected)
        self.assertEqual(orchestrator.sessions["s"].conversation_state.turn, 2)
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
