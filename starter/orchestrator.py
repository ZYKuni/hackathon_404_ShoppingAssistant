"""Protocol-only orchestration boundary for the formal retrieval/ranking path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .conversation_state import ConversationState
from .intent_router import IntentRouter
from .pipeline_contracts import (
    RankerProtocol,
    RankingError,
    RetrievalError,
    RetrieverProtocol,
    RoutingError,
    SearchRequest,
)
from .state_adapter import build_structured_query, to_profile_snapshot, to_state_snapshot


class RuntimeMode(str, Enum):
    DEVELOPMENT = "development"
    OFFICIAL = "official"


@dataclass(frozen=True)
class OrchestrationResult:
    recommendations: tuple[tuple[str, float], ...]
    fallbacks: tuple[str, ...]
    request: SearchRequest


class AgentOrchestrator:
    def __init__(
        self,
        retriever: RetrieverProtocol,
        ranker: RankerProtocol,
        *,
        router: IntentRouter | None = None,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
        candidate_limit: int = 200,
        guarded_rerank_weight: float = 0.4,
    ) -> None:
        self.retriever = retriever
        self.ranker = ranker
        self.router = router or IntentRouter()
        self.runtime_mode = RuntimeMode(runtime_mode)
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
            raise TypeError("candidate_limit must be an integer")
        if not 1 <= candidate_limit <= 200:
            raise ValueError("candidate_limit must be between 1 and 200")
        self.candidate_limit = candidate_limit
        if isinstance(guarded_rerank_weight, bool) or not isinstance(
            guarded_rerank_weight, (int, float)
        ):
            raise TypeError("guarded_rerank_weight must be numeric")
        if not 0.0 <= float(guarded_rerank_weight) <= 1.0:
            raise ValueError("guarded_rerank_weight must be between 0 and 1")
        self.guarded_rerank_weight = float(guarded_rerank_weight)

    def execute(
        self,
        *,
        session_id: str,
        turn: int,
        top_k: int,
        current_message: str,
        raw_context: str,
        base_request: str,
        state: ConversationState,
        profile: dict,
        override_detected: bool,
        legacy_fallback: Callable[[], list[dict]],
    ) -> OrchestrationResult:
        snapshot = to_state_snapshot(state)
        profile_snapshot = to_profile_snapshot(profile)
        fallbacks: list[str] = []
        try:
            decision = self.router.route(current_message, snapshot, override_detected)
        except RoutingError:
            if self.runtime_mode is RuntimeMode.DEVELOPMENT:
                raise
            fallbacks.append("routing_default")
            decision = IntentRouter().route(current_message, snapshot, override_detected)
        request = SearchRequest(
            session_id=session_id,
            turn=turn,
            top_k=top_k,
            candidate_limit=max(top_k, min(self.candidate_limit, 200)),
            route_decision=decision,
            current_message=current_message,
            raw_context=raw_context,
            base_request=base_request,
            structured_query=build_structured_query(snapshot),
            state=snapshot,
            profile=profile_snapshot,
        )
        try:
            pool = self.retriever.retrieve(request)
        except RetrievalError:
            if self.runtime_mode is RuntimeMode.DEVELOPMENT:
                raise
            fallbacks.append("legacy_retrieval")
            return OrchestrationResult(
                recommendations=self._legacy(legacy_fallback(), top_k),
                fallbacks=tuple(fallbacks),
                request=request,
            )
        try:
            ranking = self.ranker.rank(request, pool)
            recommendations = tuple(
                (item.parent_asin, item.final_score) for item in ranking.candidates[:top_k]
            )
            if not recommendations:
                raise RankingError("ranker returned no surviving recommendation")
        except RankingError:
            if self.runtime_mode is RuntimeMode.DEVELOPMENT:
                raise
            fallbacks.append("rrf_ranking")
            recommendations = tuple(
                (item.parent_asin, item.rrf_score) for item in pool.candidates[:top_k]
            )
        if not recommendations:
            fallbacks.append("legacy_empty_guard")
            recommendations = self._legacy(legacy_fallback(), top_k)
        elif self.runtime_mode is RuntimeMode.OFFICIAL:
            # Public-set ablation showed that replacing the proven Legacy Top-K
            # before dialog constraints have converged loses recall.  Official
            # mode therefore preserves that candidate set and uses the formal
            # ranker only as a bounded, deterministic reordering signal.
            legacy = self._legacy(legacy_fallback(), top_k)
            recommendations = self._guarded_rerank(
                legacy, recommendations, self.guarded_rerank_weight
            )
        return OrchestrationResult(recommendations, tuple(fallbacks), request)

    @staticmethod
    def _guarded_rerank(
        legacy: tuple[tuple[str, float], ...],
        formal: tuple[tuple[str, float], ...],
        formal_weight: float,
    ) -> tuple[tuple[str, float], ...]:
        formal_rank = {asin: rank for rank, (asin, _) in enumerate(formal, 1)}
        legacy_rank = {asin: rank for rank, (asin, _) in enumerate(legacy, 1)}
        scored = []
        for asin, _ in legacy:
            score = (
                (1.0 - formal_weight) / legacy_rank[asin]
                + formal_weight / formal_rank.get(asin, 201)
            )
            scored.append((asin, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return tuple(scored)

    @staticmethod
    def _legacy(values: list[dict], top_k: int) -> tuple[tuple[str, float], ...]:
        result: list[tuple[str, float]] = []
        seen: set[str] = set()
        for item in values:
            asin = str(item.get("parent_asin") or "")
            if not asin or asin in seen:
                continue
            seen.add(asin)
            result.append((asin, float(item.get("score") or 0.0)))
            if len(result) >= top_k:
                break
        if not result:
            raise RetrievalError("legacy fallback returned no recommendations")
        return tuple(result)


__all__ = ["AgentOrchestrator", "OrchestrationResult", "RuntimeMode"]
