"""Agent pipeline orchestration with deterministic, observable fallbacks.

The orchestrator owns session lifecycle and the Contract v1 boundary, but it
depends only on injected router, retriever, ranker, and question-policy
interfaces.  Catalog normalization, retrieval formulas, filtering, and ranking
features remain outside Ethan's ownership.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Protocol, Sequence

from starter.constraint_parser import parse_message
from starter.conversation_state import ConversationState, Operation, apply_patch
from starter.intent_router import IntentRouter
from starter.pipeline_contracts import (
    Candidate,
    CandidatePool,
    IntentRoute,
    ProfileSnapshot,
    RankedCandidate,
    RankerProtocol,
    RankingError,
    RankingExplanation,
    RankingResult,
    RetrievalError,
    RetrieverProtocol,
    RouteDecision,
    RouteEvidence,
    RoutingError,
    SearchRequest,
    StateSnapshot,
)
from starter.state_adapter import (
    build_structured_query,
    to_profile_snapshot,
    to_state_snapshot,
)


OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead|ignore\s+(?:my\s+)?earlier|changed?\s+my\s+mind|what\s+i\s+need)\b",
    re.IGNORECASE,
)
FULL_OVERRIDE_RE = re.compile(
    r"\b(?:ignore\s+(?:my\s+)?earlier\s+preference|forget\s+(?:my\s+)?earlier|"
    r"changed?\s+my\s+mind)\b",
    re.IGNORECASE,
)
NO_PREFERENCE_RE = re.compile(
    r"\b(?:no|don['’]?t\s+have|without)\b.{0,30}\bpreference\b",
    re.IGNORECASE,
)
NO_MATCH_RE = re.compile(r"\b(?:not quite right|ask me about)\b", re.IGNORECASE)
CATEGORY_RE = re.compile(
    r"\b(shoes?|boots?|sneakers?|sandals?|slippers?|dress(?:es)?|shirts?|tops?|tees?|"
    r"pants?|jeans?|shorts?|skirts?|jackets?|coats?|sweaters?|hoodies?|socks?|underwear|"
    r"bras?|swimwear|jewelry|earrings?|necklaces?|bracelets?|rings?|watches?|bags?|hats?)\b",
    re.IGNORECASE,
)


class RuntimeMode(str, Enum):
    DEVELOPMENT = "development"
    OFFICIAL = "official"


class QuestionPolicyError(Exception):
    """Expected question-selection failure eligible for controlled fallback."""


@dataclass(frozen=True)
class QuestionDecision:
    message: str
    ask_attribute: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("question message must be a non-empty string")
        if self.ask_attribute is not None and (
            not isinstance(self.ask_attribute, str) or not self.ask_attribute.strip()
        ):
            raise ValueError("ask_attribute must be a non-empty string or None")


@dataclass(frozen=True)
class OrchestrationDiagnostics:
    events: tuple[str, ...] = ()
    route_decision: RouteDecision | None = None


@dataclass
class OrchestrationSession:
    user_profile: dict
    base_request: str = ""
    active_messages: list[str] = field(default_factory=list)
    conversation_state: ConversationState = field(default_factory=ConversationState)
    last_asked_attribute: str | None = None


class RouterProtocol(Protocol):
    def route(
        self,
        current_message: str,
        state: StateSnapshot,
        override_detected: bool,
    ) -> RouteDecision:
        ...


class QuestionPolicyProtocol(Protocol):
    def choose(
        self,
        session: OrchestrationSession,
        request: SearchRequest,
        ranking: RankingResult,
    ) -> QuestionDecision:
        ...


def base_request_from_message(message: str) -> str:
    result = re.split(r"\bA key requirement is:\s*|,\s*but\b|\.\s+", message, maxsplit=1, flags=re.I)[0]
    return result.strip()


def _category_terms(text: str) -> set[str]:
    return {match.group(1).lower() for match in CATEGORY_RE.finditer(text)}


def _reset_constraints_for_full_override(session: OrchestrationSession) -> None:
    previous = session.conversation_state
    session.conversation_state = ConversationState(category=previous.category, turn=previous.turn)
    session.last_asked_attribute = None


def update_session_state(
    session: OrchestrationSession,
    user_message: str,
    turn: int,
) -> bool:
    """Update structured and raw state, returning whether this turn is an override."""
    if not isinstance(session, OrchestrationSession):
        raise TypeError("session must be an OrchestrationSession")
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("user_message must be a non-empty string")
    if not 1 <= turn <= 10:
        raise ValueError("turn must be between 1 and 10")

    if turn == 1 or not session.base_request:
        session.base_request = base_request_from_message(user_message)

    full_override = turn > 1 and bool(FULL_OVERRIDE_RE.search(user_message))
    lexical_override = turn > 1 and bool(OVERRIDE_RE.search(user_message))
    if full_override:
        _reset_constraints_for_full_override(session)

    patch = parse_message(user_message, session.conversation_state, turn)
    patch_override = any(
        operation.op
        in {Operation.ALLOW, Operation.REMOVE, Operation.REPLACE, Operation.RESET_SCOPE}
        for operation in patch.operations
    )
    override_detected = turn > 1 and (lexical_override or patch_override)
    session.conversation_state = apply_patch(session.conversation_state, patch)

    no_preference = NO_PREFERENCE_RE.search(user_message) or NO_MATCH_RE.search(user_message)
    if no_preference:
        last_asked = session.last_asked_attribute
        if last_asked and last_asked not in session.conversation_state.no_preference:
            session.conversation_state.no_preference.append(last_asked)
        return override_detected

    if override_detected:
        old_categories = _category_terms(session.base_request)
        new_categories = _category_terms(user_message)
        category_changed = bool(
            new_categories and old_categories and new_categories.isdisjoint(old_categories)
        )
        if category_changed:
            session.base_request = base_request_from_message(user_message)
            session.active_messages = [user_message]
        else:
            # The structured state is authoritative for conflicting fields.  The
            # raw fallback retains only the stable base request and current turn.
            session.active_messages = [session.base_request, user_message]
        session.conversation_state.asked_attributes.clear()
        session.conversation_state.no_preference.clear()
        session.last_asked_attribute = None
    else:
        session.active_messages.append(user_message)
    return override_detected


def build_search_request(
    *,
    session_id: str,
    turn: int,
    top_k: int,
    candidate_limit: int,
    decision: RouteDecision,
    current_message: str,
    session: OrchestrationSession,
    state: StateSnapshot | None = None,
    profile: ProfileSnapshot | None = None,
) -> SearchRequest:
    state_snapshot = state or to_state_snapshot(session.conversation_state)
    profile_snapshot = profile or to_profile_snapshot(session.user_profile)
    return SearchRequest(
        session_id=session_id,
        turn=turn,
        top_k=top_k,
        candidate_limit=candidate_limit,
        route_decision=decision,
        current_message=current_message,
        raw_context=" ".join(session.active_messages).strip(),
        base_request=session.base_request,
        structured_query=build_structured_query(state_snapshot),
        state=state_snapshot,
        profile=profile_snapshot,
    )


def ranking_from_pool(pool: CandidatePool) -> RankingResult:
    """Safe rank fallback that preserves CandidatePool/RRF order exactly."""
    return RankingResult(
        candidates=tuple(
            RankedCandidate(
                parent_asin=candidate.parent_asin,
                final_score=candidate.rrf_score,
                explanation=RankingExplanation(rrf=min(1.0, candidate.rrf_score)),
            )
            for candidate in pool.candidates
        ),
        input_count=len(pool.candidates),
        filtered_count=0,
        unknown_preserved_count=0,
        ranking_latency_ms=0.0,
    )


class LegacyRetrieverAdapter:
    """Expose the current three-route SQLite retrieval as Contract v1.

    ``search`` remains owned by the existing Agent index.  The adapter only
    converts its ranked identifiers into a deterministic CandidatePool.
    """

    ROUTES: tuple[tuple[str, float], ...] = (
        ("active_context", 1.40),
        ("current_message", 0.85),
        ("base_request", 0.25),
    )

    def __init__(
        self,
        search: Callable[[str, int], list[str]],
        fallback_ids: Sequence[str],
        *,
        route_limit: int = 120,
    ) -> None:
        if not callable(search):
            raise TypeError("search must be callable")
        if route_limit < 1:
            raise ValueError("route_limit must be positive")
        self.search = search
        self.fallback_ids = tuple(fallback_ids)
        self.route_limit = route_limit

    def retrieve(self, request: SearchRequest) -> CandidatePool:
        started = time.perf_counter()
        active_context = request.raw_context or request.structured_query
        route_text = {
            "active_context": active_context,
            "current_message": request.current_message,
            "base_request": request.base_request,
        }
        scores: dict[str, float] = {}
        evidence: dict[str, list[RouteEvidence]] = {}
        for route_name, weight in self.ROUTES:
            for rank, parent_asin in enumerate(
                self.search(route_text[route_name], self.route_limit), start=1
            ):
                if not isinstance(parent_asin, str) or not parent_asin.strip():
                    raise RetrievalError("legacy search returned an invalid parent_asin")
                contribution = weight / (60.0 + rank)
                scores[parent_asin] = scores.get(parent_asin, 0.0) + contribution
                evidence.setdefault(parent_asin, []).append(
                    RouteEvidence(route_name=route_name, rank=rank, score=contribution)
                )

        ranked_ids = sorted(scores, key=lambda asin: (-scores[asin], asin))
        for rank, parent_asin in enumerate(self.fallback_ids, start=1):
            if parent_asin in scores or parent_asin in ranked_ids:
                continue
            ranked_ids.append(parent_asin)
            evidence[parent_asin] = (
                [RouteEvidence(route_name="popularity_fallback", rank=rank, score=0.0)]
            )
            if len(ranked_ids) >= request.candidate_limit:
                break
        ranked_ids = ranked_ids[:request.candidate_limit]
        if not ranked_ids:
            raise RetrievalError("legacy retrieval produced no candidates")

        return CandidatePool(
            candidates=tuple(
                Candidate(
                    parent_asin=parent_asin,
                    evidence=tuple(evidence[parent_asin]),
                    rrf_score=scores.get(parent_asin, 0.0),
                )
                for parent_asin in ranked_ids
            ),
            requested_limit=request.candidate_limit,
            route=request.route_decision.route,
            retrieval_latency_ms=(time.perf_counter() - started) * 1000.0,
        )


class LegacyRankerAdapter:
    """Identity ranker preserving the legacy fused RRF order."""

    def rank(self, request: SearchRequest, pool: CandidatePool) -> RankingResult:
        return ranking_from_pool(pool)


class LegacyQuestionPolicyAdapter:
    """Adapter for the existing deterministic attribute-question sequence."""

    def __init__(
        self,
        mentioned_attributes: Callable[[str], set[str]],
        question_order: Sequence[str],
        question_text: Mapping[str, str],
    ) -> None:
        if not callable(mentioned_attributes):
            raise TypeError("mentioned_attributes must be callable")
        self.mentioned_attributes = mentioned_attributes
        self.question_order = tuple(question_order)
        self.question_text = dict(question_text)
        if not self.question_order or any(item not in self.question_text for item in self.question_order):
            raise ValueError("every question_order attribute must have question text")

    def choose(
        self,
        session: OrchestrationSession,
        request: SearchRequest,
        ranking: RankingResult,
    ) -> QuestionDecision:
        if request.turn >= 10:
            session.last_asked_attribute = None
            return QuestionDecision(
                "Here are the best matches based on your current preferences.", None
            )

        known = self.mentioned_attributes(" ".join(session.active_messages))
        for attribute in self.question_order:
            if attribute in session.conversation_state.asked_attributes:
                continue
            if attribute in session.conversation_state.no_preference:
                continue
            if attribute in known and attribute != "feature":
                continue
            session.conversation_state.asked_attributes.append(attribute)
            session.last_asked_attribute = attribute
            return QuestionDecision(self.question_text[attribute], attribute)
        session.last_asked_attribute = None
        return QuestionDecision(
            "Here are the best matches based on your current preferences.", None
        )


class AgentOrchestrator:
    """Stateful coordinator for one or more isolated shopping sessions."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        ranker: RankerProtocol,
        question_policy: QuestionPolicyProtocol,
        *,
        router: RouterProtocol | None = None,
        fallback_retriever: RetrieverProtocol | None = None,
        fallback_question_policy: QuestionPolicyProtocol | None = None,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
        candidate_limit: int = 200,
    ) -> None:
        if not isinstance(runtime_mode, RuntimeMode):
            raise TypeError("runtime_mode must be a RuntimeMode")
        if not 1 <= candidate_limit <= 200:
            raise ValueError("candidate_limit must be between 1 and 200")
        self.retriever = retriever
        self.ranker = ranker
        self.question_policy = question_policy
        self.router = router or IntentRouter()
        self.fallback_retriever = fallback_retriever
        self.fallback_question_policy = fallback_question_policy
        self.runtime_mode = runtime_mode
        self.candidate_limit = candidate_limit
        self.sessions: dict[str, OrchestrationSession] = {}
        self._diagnostics: dict[str, OrchestrationDiagnostics] = {}

    def reset(self, session_id: str, user_profile: dict) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if user_profile is not None and not isinstance(user_profile, dict):
            raise TypeError("user_profile must be a dictionary")
        self.sessions[session_id] = OrchestrationSession(user_profile=dict(user_profile or {}))
        self._diagnostics.pop(session_id, None)

    def diagnostics(self, session_id: str) -> OrchestrationDiagnostics:
        return self._diagnostics.get(session_id, OrchestrationDiagnostics())

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        if session_id not in self.sessions:
            raise RuntimeError("reset must be called before respond")
        if not 1 <= turn <= 10:
            raise ValueError("turn must be between 1 and 10")
        top_k = max(1, min(int(top_k), 10))
        candidate_limit = max(top_k, self.candidate_limit)
        session = self.sessions[session_id]
        events: list[str] = []

        override_detected = update_session_state(session, user_message, turn)
        state = to_state_snapshot(session.conversation_state)
        profile = to_profile_snapshot(session.user_profile)
        decision = self._route(user_message, state, override_detected, events)
        request = build_search_request(
            session_id=session_id,
            turn=turn,
            top_k=top_k,
            candidate_limit=candidate_limit,
            decision=decision,
            current_message=user_message,
            session=session,
            state=state,
            profile=profile,
        )
        pool = self._retrieve(request, events)
        ranking = self._rank(request, pool, events)
        question = self._choose_question(session, request, ranking, events)
        recommendations = self._recommendations(ranking, top_k)
        if not recommendations:
            raise RetrievalError("pipeline completed without any recommendations")

        self._diagnostics[session_id] = OrchestrationDiagnostics(
            events=tuple(events), route_decision=decision
        )
        return {
            "message": question.message,
            "ask_attribute": question.ask_attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    def _route(
        self,
        message: str,
        state: StateSnapshot,
        override_detected: bool,
        events: list[str],
    ) -> RouteDecision:
        try:
            return self.router.route(message, state, override_detected)
        except RoutingError:
            if self.runtime_mode == RuntimeMode.DEVELOPMENT:
                raise
            events.append("routing_fallback")
            route = (
                IntentRoute.BUYING
                if state.hard_constraints or state.excluded
                else IntentRoute.BROWSING
            )
            return RouteDecision(
                route=route,
                confidence=0.50,
                reason="The router failed, so a deterministic state-based default was used.",
                signals=("routing_fallback",),
                override_detected=override_detected,
            )

    def _retrieve(self, request: SearchRequest, events: list[str]) -> CandidatePool:
        try:
            pool = self.retriever.retrieve(request)
            if not pool.candidates:
                raise RetrievalError("retriever returned an empty candidate pool")
            return pool
        except RetrievalError:
            if self.runtime_mode == RuntimeMode.DEVELOPMENT or self.fallback_retriever is None:
                raise
            events.append("retrieval_fallback")
            pool = self.fallback_retriever.retrieve(request)
            if not pool.candidates:
                raise RetrievalError("legacy fallback returned an empty candidate pool")
            return pool

    def _rank(
        self,
        request: SearchRequest,
        pool: CandidatePool,
        events: list[str],
    ) -> RankingResult:
        try:
            ranking = self.ranker.rank(request, pool)
            if not ranking.candidates:
                raise RankingError("ranker returned no candidates")
            return ranking
        except RankingError:
            if self.runtime_mode == RuntimeMode.DEVELOPMENT:
                raise
            events.append("ranking_fallback")
            return ranking_from_pool(pool)

    def _choose_question(
        self,
        session: OrchestrationSession,
        request: SearchRequest,
        ranking: RankingResult,
        events: list[str],
    ) -> QuestionDecision:
        try:
            return self.question_policy.choose(session, request, ranking)
        except QuestionPolicyError:
            if self.runtime_mode == RuntimeMode.DEVELOPMENT or self.fallback_question_policy is None:
                raise
            events.append("question_fallback")
            return self.fallback_question_policy.choose(session, request, ranking)

    @staticmethod
    def _recommendations(ranking: RankingResult, top_k: int) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for candidate in ranking.candidates:
            if candidate.parent_asin in seen:
                continue
            seen.add(candidate.parent_asin)
            result.append(
                {"parent_asin": candidate.parent_asin, "score": candidate.final_score}
            )
            if len(result) >= top_k:
                break
        return result


__all__ = [
    "AgentOrchestrator",
    "LegacyQuestionPolicyAdapter",
    "LegacyRankerAdapter",
    "LegacyRetrieverAdapter",
    "OrchestrationDiagnostics",
    "OrchestrationSession",
    "QuestionDecision",
    "QuestionPolicyError",
    "QuestionPolicyProtocol",
    "RouterProtocol",
    "RuntimeMode",
    "base_request_from_message",
    "build_search_request",
    "ranking_from_pool",
    "update_session_state",
]
