"""Deterministic local filtering and explainable ranking for CandidatePool."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Protocol

from .attribute_lexicons import normalize_phrase
from .catalog_normalizer import CatalogNormalizer, ExtractedValue, NormalizedProduct
from .constraint_matcher import ConstraintMatcher, MatchState, ProductConstraintEvaluation
from .pipeline_contracts import (
    Candidate,
    CandidatePool,
    RankedCandidate,
    RankingError,
    RankingExplanation,
    RankingResult,
    SearchRequest,
)


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
RANK_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
    "from", "have", "i", "in", "is", "it", "me", "my", "need", "of",
    "on", "or", "please", "some", "that", "the", "this", "to", "under",
    "want", "with", "would", "you", "your",
}


@dataclass(frozen=True)
class RankerWeights:
    rrf: float = 1.00
    exact_phrase: float = 0.35
    feature_overlap: float = 0.25
    category_match: float = 0.25
    hard_match: float = 0.25
    soft_match: float = 0.15
    popularity: float = 0.03
    profile_alignment: float = 0.03
    violation_penalty: float = 0.80

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{item.name} weight must be finite and non-negative")
        if self.profile_alignment > 0.03:
            raise ValueError("profile_alignment weight must not exceed 0.03")


class ProductLookup(Protocol):
    def get(self, parent_asin: str) -> NormalizedProduct | None: ...


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in RANK_STOPWORDS
    ))


def _attribute_values(product: NormalizedProduct) -> tuple[str, ...]:
    values: list[str] = []
    for name in (
        "leaf_categories", "audiences", "materials", "colors", "brands", "sizes", "styles"
    ):
        values.extend(str(item.value).replace("_", " ") for item in getattr(product, name))
    return tuple(values)


def _document(product: NormalizedProduct) -> str:
    return " ".join((
        *(value.replace("_", " ") for value in product.category_path),
        *_attribute_values(product),
        *product.features,
    )).strip()


def _constraint_phrases(request: SearchRequest) -> tuple[str, ...]:
    phrases: list[str] = []
    if request.state.category:
        phrases.append(request.state.category.replace("_", " "))
    for group in (
        request.state.hard_constraints,
        request.state.soft_preferences,
    ):
        for term in group:
            if term.field not in {"price_min", "price_max"}:
                phrases.extend(str(value).replace("_", " ") for value in term.values)
    return tuple(dict.fromkeys(
        phrase for phrase in (normalize_phrase(value) for value in phrases) if phrase
    ))


def _phrase_present(document: str, phrase: str) -> bool:
    words = [re.escape(word) for word in normalize_phrase(phrase).split()]
    if not words:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + r"\s+".join(words) + r"(?![a-z0-9])", document))


def _match_ratio(matches: tuple, *, unknown_value: float = 0.0) -> float:
    decided = [item for item in matches if item.state is not MatchState.UNKNOWN]
    if not decided:
        return unknown_value
    return sum(item.state is MatchState.MATCH for item in decided) / len(decided)


class LocalConstraintRanker:
    """Apply A2 matching, high-confidence filtering, and local feature ranking."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        catalog: ProductLookup | None = None,
        matcher: ConstraintMatcher | None = None,
        weights: RankerWeights | None = None,
    ) -> None:
        self.catalog = catalog or CatalogNormalizer.from_jsonl(catalog_path)
        self.matcher = matcher or ConstraintMatcher()
        self.weights = weights or RankerWeights()

    def rank(self, request: SearchRequest, pool: CandidatePool) -> RankingResult:
        if not isinstance(request, SearchRequest):
            raise TypeError("request must be a SearchRequest")
        if not isinstance(pool, CandidatePool):
            raise TypeError("pool must be a CandidatePool")
        if pool.route is not request.route_decision.route:
            raise RankingError("CandidatePool route does not match SearchRequest route")

        started = time.perf_counter()
        max_rrf = max((candidate.rrf_score for candidate in pool.candidates), default=0.0)
        products: dict[str, NormalizedProduct] = {}
        evaluations: dict[str, ProductConstraintEvaluation] = {}
        filtered: set[str] = set()
        try:
            for candidate in pool.candidates:
                product = self.catalog.get(candidate.parent_asin)
                if product is None:
                    raise RankingError(
                        f"candidate is absent from normalized catalog: {candidate.parent_asin}"
                    )
                products[candidate.parent_asin] = product
                evaluation = self.matcher.evaluate(request.state, product)
                evaluations[candidate.parent_asin] = evaluation
                if evaluation.should_filter:
                    filtered.add(candidate.parent_asin)
        except RankingError:
            raise
        except Exception as error:
            raise RankingError("constraint evaluation failed") from error

        survivors = [
            candidate for candidate in pool.candidates
            if candidate.parent_asin not in filtered
        ]
        max_popularity = max(
            (math.log1p(products[item.parent_asin].rating_number) for item in survivors),
            default=0.0,
        )
        ranked: list[RankedCandidate] = []
        try:
            for candidate in survivors:
                product = products[candidate.parent_asin]
                evaluation = evaluations[candidate.parent_asin]
                explanation = self._explain(
                    request, candidate, product, evaluation, max_rrf, max_popularity
                )
                score = self._score(explanation)
                ranked.append(RankedCandidate(candidate.parent_asin, score, explanation))
        except Exception as error:
            raise RankingError("ranking feature computation failed") from error

        ranked.sort(key=lambda item: (-item.final_score, item.parent_asin))
        unknown_preserved = sum(
            evaluations[item.parent_asin].unknown_count > 0 for item in survivors
        )
        return RankingResult(
            candidates=tuple(ranked),
            input_count=len(pool.candidates),
            filtered_count=len(filtered),
            unknown_preserved_count=unknown_preserved,
            ranking_latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _explain(
        self,
        request: SearchRequest,
        candidate: Candidate,
        product: NormalizedProduct,
        evaluation: ProductConstraintEvaluation,
        max_rrf: float,
        max_popularity: float,
    ) -> RankingExplanation:
        document = normalize_phrase(_document(product))
        phrases = _constraint_phrases(request)
        exact_phrase = (
            sum(_phrase_present(document, phrase) for phrase in phrases) / len(phrases)
            if phrases else 0.0
        )
        query = request.structured_query.strip() or request.current_message
        query_tokens = set(_tokens(query))
        document_tokens = set(_tokens(document))
        feature_overlap = (
            len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0
        )
        category_match = self._category_feature(request, product)
        mismatches = [
            *[item.confidence for item in evaluation.hard if item.state is MatchState.MISMATCH],
            *[item.confidence for item in evaluation.excluded if item.state is MatchState.MISMATCH],
            *[
                item.confidence * 0.5
                for item in evaluation.soft if item.state is MatchState.MISMATCH
            ],
        ]
        profile_tokens = set(
            token
            for tag in request.profile.preference_tags
            for token in _tokens(tag)
        )
        profile_alignment = (
            len(profile_tokens & document_tokens) / len(profile_tokens)
            if profile_tokens else 0.0
        )
        return RankingExplanation(
            rrf=candidate.rrf_score / max_rrf if max_rrf > 0 else 0.0,
            exact_phrase=exact_phrase,
            feature_overlap=feature_overlap,
            category_match=category_match,
            hard_match=_match_ratio(evaluation.hard),
            soft_match=_match_ratio(evaluation.soft),
            violation_penalty=max(mismatches, default=0.0),
            popularity=(
                math.log1p(product.rating_number) / max_popularity
                if max_popularity > 0 else 0.0
            ),
            profile_alignment=profile_alignment,
        )

    @staticmethod
    def _category_feature(request: SearchRequest, product: NormalizedProduct) -> float:
        if not request.state.category:
            return 0.0
        expected = str(request.state.category)
        leaves = {str(item.value) for item in product.leaf_categories}
        if expected in leaves:
            return 1.0
        if expected in set(product.category_path):
            return 0.75
        return 0.0

    def _score(self, value: RankingExplanation) -> float:
        w = self.weights
        return (
            w.rrf * value.rrf
            + w.exact_phrase * value.exact_phrase
            + w.feature_overlap * value.feature_overlap
            + w.category_match * value.category_match
            + w.hard_match * value.hard_match
            + w.soft_match * value.soft_match
            + w.popularity * value.popularity
            + w.profile_alignment * value.profile_alignment
            - w.violation_penalty * value.violation_penalty
        )


__all__ = ["LocalConstraintRanker", "RankerWeights"]
