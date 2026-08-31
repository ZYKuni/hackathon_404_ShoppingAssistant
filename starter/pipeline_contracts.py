"""Versioned interfaces shared by Agent orchestration and search/ranking modules.

This module intentionally contains contracts only.  It does not implement intent
routing, catalog normalization, retrieval, filtering, ranking, fallback behavior,
or the public Agent API.  Ethan's orchestration code and Aaron's search code can
therefore depend on the same immutable boundary without importing each other's
concrete implementations.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Protocol, Union, runtime_checkable


PIPELINE_CONTRACT_VERSION = "1.0"

ScalarValue = Union[str, int, float]


def _require_tuple(name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")


def _require_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_finite(name: str, value: int | float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _require_ratio(name: str, value: int | float) -> None:
    _require_finite(name, value)
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _require_unique_strings(name: str, values: tuple[str, ...]) -> None:
    _require_tuple(name, values)
    for value in values:
        _require_non_empty_string(f"{name} item", value)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")


class IntentRoute(str, Enum):
    """Runtime search route.  Override is an event, not a third route."""

    BUYING = "buying"
    BROWSING = "browsing"


@dataclass(frozen=True)
class RouteDecision:
    route: IntentRoute
    confidence: float
    reason: str
    signals: tuple[str, ...] = ()
    override_detected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.route, IntentRoute):
            raise TypeError("route must be an IntentRoute")
        _require_ratio("confidence", self.confidence)
        _require_non_empty_string("reason", self.reason)
        _require_unique_strings("signals", self.signals)
        if not isinstance(self.override_detected, bool):
            raise TypeError("override_detected must be bool")


@dataclass(frozen=True)
class ConstraintTerm:
    """One canonical state field and its immutable set of accepted/excluded values."""

    field: str
    values: tuple[ScalarValue, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string("field", self.field)
        _require_tuple("values", self.values)
        if not self.values:
            raise ValueError("values must not be empty")
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise TypeError("constraint values must be strings or finite numbers")
            if isinstance(value, str):
                _require_non_empty_string("constraint value", value)
            else:
                _require_finite("constraint value", value)
        if len(self.values) != len(set(self.values)):
            raise ValueError("constraint values must not contain duplicates")


def _validate_constraint_group(name: str, terms: tuple[ConstraintTerm, ...]) -> None:
    _require_tuple(name, terms)
    if any(not isinstance(term, ConstraintTerm) for term in terms):
        raise TypeError(f"{name} must contain ConstraintTerm values")
    field_names = [term.field for term in terms]
    if len(field_names) != len(set(field_names)):
        raise ValueError(f"{name} must not repeat a field")


@dataclass(frozen=True)
class StateSnapshot:
    """Read-only representation of ConversationState at one completed user turn."""

    schema_version: str
    turn: int
    category: str | None = None
    hard_constraints: tuple[ConstraintTerm, ...] = ()
    soft_preferences: tuple[ConstraintTerm, ...] = ()
    excluded: tuple[ConstraintTerm, ...] = ()
    no_preference: tuple[str, ...] = ()
    asked_attributes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string("schema_version", self.schema_version)
        if isinstance(self.turn, bool) or not isinstance(self.turn, int) or not 0 <= self.turn <= 10:
            raise ValueError("turn must be an integer between 0 and 10")
        if self.category is not None:
            _require_non_empty_string("category", self.category)
        _validate_constraint_group("hard_constraints", self.hard_constraints)
        _validate_constraint_group("soft_preferences", self.soft_preferences)
        _validate_constraint_group("excluded", self.excluded)
        _require_unique_strings("no_preference", self.no_preference)
        _require_unique_strings("asked_attributes", self.asked_attributes)


@dataclass(frozen=True)
class ProfileSnapshot:
    """Safe aggregate profile kept separate from current-session constraints."""

    preference_tags: tuple[str, ...] = ()
    average_prior_rating: float | None = None
    purchase_frequency: str | None = None
    rating_style: str | None = None

    def __post_init__(self) -> None:
        _require_unique_strings("preference_tags", self.preference_tags)
        if self.average_prior_rating is not None:
            _require_finite("average_prior_rating", self.average_prior_rating)
            if not 0.0 <= float(self.average_prior_rating) <= 5.0:
                raise ValueError("average_prior_rating must be between 0 and 5")
        for name, value in (
            ("purchase_frequency", self.purchase_frequency),
            ("rating_style", self.rating_style),
        ):
            if value is not None:
                _require_non_empty_string(name, value)


@dataclass(frozen=True)
class SearchRequest:
    """Fully resolved, immutable input passed from orchestration to retrieval."""

    session_id: str
    turn: int
    top_k: int
    candidate_limit: int
    route_decision: RouteDecision
    current_message: str
    raw_context: str
    base_request: str
    structured_query: str
    state: StateSnapshot
    profile: ProfileSnapshot

    def __post_init__(self) -> None:
        _require_non_empty_string("session_id", self.session_id)
        if isinstance(self.turn, bool) or not isinstance(self.turn, int) or not 1 <= self.turn <= 10:
            raise ValueError("turn must be an integer between 1 and 10")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or not 1 <= self.top_k <= 10:
            raise ValueError("top_k must be an integer between 1 and 10")
        if (
            isinstance(self.candidate_limit, bool)
            or not isinstance(self.candidate_limit, int)
            or self.candidate_limit < self.top_k
            or self.candidate_limit > 200
        ):
            raise ValueError("candidate_limit must be an integer between top_k and 200")
        if not isinstance(self.route_decision, RouteDecision):
            raise TypeError("route_decision must be a RouteDecision")
        _require_non_empty_string("current_message", self.current_message)
        for name, value in (
            ("raw_context", self.raw_context),
            ("base_request", self.base_request),
            ("structured_query", self.structured_query),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
        if not isinstance(self.state, StateSnapshot):
            raise TypeError("state must be a StateSnapshot")
        if self.state.turn != self.turn:
            raise ValueError("state.turn must equal request turn")
        if not isinstance(self.profile, ProfileSnapshot):
            raise TypeError("profile must be a ProfileSnapshot")


@dataclass(frozen=True)
class RouteEvidence:
    route_name: str
    rank: int
    score: float | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("route_name", self.route_name)
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be a positive integer")
        if self.score is not None:
            _require_finite("score", self.score)


@dataclass(frozen=True)
class Candidate:
    parent_asin: str
    evidence: tuple[RouteEvidence, ...]
    rrf_score: float

    def __post_init__(self) -> None:
        _require_non_empty_string("parent_asin", self.parent_asin)
        _require_tuple("evidence", self.evidence)
        if not self.evidence:
            raise ValueError("evidence must not be empty")
        if any(not isinstance(item, RouteEvidence) for item in self.evidence):
            raise TypeError("evidence must contain RouteEvidence values")
        route_names = [item.route_name for item in self.evidence]
        if len(route_names) != len(set(route_names)):
            raise ValueError("a candidate must not repeat a retrieval route")
        _require_finite("rrf_score", self.rrf_score)
        if self.rrf_score < 0:
            raise ValueError("rrf_score must be non-negative")


@dataclass(frozen=True)
class CandidatePool:
    """De-duplicated, pre-filter candidate pool returned by retrieval."""

    candidates: tuple[Candidate, ...]
    requested_limit: int
    route: IntentRoute
    retrieval_latency_ms: float

    def __post_init__(self) -> None:
        _require_tuple("candidates", self.candidates)
        if any(not isinstance(candidate, Candidate) for candidate in self.candidates):
            raise TypeError("candidates must contain Candidate values")
        if (
            isinstance(self.requested_limit, bool)
            or not isinstance(self.requested_limit, int)
            or not 1 <= self.requested_limit <= 200
        ):
            raise ValueError("requested_limit must be an integer between 1 and 200")
        if len(self.candidates) > self.requested_limit:
            raise ValueError("candidate pool exceeds requested_limit")
        identifiers = [candidate.parent_asin for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate pool must not contain duplicate parent_asin values")
        if not isinstance(self.route, IntentRoute):
            raise TypeError("route must be an IntentRoute")
        _require_finite("retrieval_latency_ms", self.retrieval_latency_ms)
        if self.retrieval_latency_ms < 0:
            raise ValueError("retrieval_latency_ms must be non-negative")


@dataclass(frozen=True)
class RankingExplanation:
    """Normalized, inspectable features used by a future local ranker."""

    rrf: float = 0.0
    exact_phrase: float = 0.0
    feature_overlap: float = 0.0
    category_match: float = 0.0
    hard_match: float = 0.0
    soft_match: float = 0.0
    violation_penalty: float = 0.0
    popularity: float = 0.0
    profile_alignment: float = 0.0
    semantic_similarity: float = 0.0

    def __post_init__(self) -> None:
        for field_info in fields(self):
            _require_ratio(field_info.name, getattr(self, field_info.name))


@dataclass(frozen=True)
class RankedCandidate:
    parent_asin: str
    final_score: float
    explanation: RankingExplanation

    def __post_init__(self) -> None:
        _require_non_empty_string("parent_asin", self.parent_asin)
        _require_finite("final_score", self.final_score)
        if not isinstance(self.explanation, RankingExplanation):
            raise TypeError("explanation must be a RankingExplanation")


@dataclass(frozen=True)
class RankingResult:
    candidates: tuple[RankedCandidate, ...]
    input_count: int
    filtered_count: int
    unknown_preserved_count: int
    ranking_latency_ms: float

    def __post_init__(self) -> None:
        _require_tuple("candidates", self.candidates)
        if any(not isinstance(candidate, RankedCandidate) for candidate in self.candidates):
            raise TypeError("candidates must contain RankedCandidate values")
        for name, value in (
            ("input_count", self.input_count),
            ("filtered_count", self.filtered_count),
            ("unknown_preserved_count", self.unknown_preserved_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.filtered_count > self.input_count:
            raise ValueError("filtered_count cannot exceed input_count")
        if self.unknown_preserved_count > self.input_count:
            raise ValueError("unknown_preserved_count cannot exceed input_count")
        if len(self.candidates) > self.input_count - self.filtered_count:
            raise ValueError("ranked candidates exceed the unfiltered input count")
        identifiers = [candidate.parent_asin for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ranking result must not contain duplicate parent_asin values")
        _require_finite("ranking_latency_ms", self.ranking_latency_ms)
        if self.ranking_latency_ms < 0:
            raise ValueError("ranking_latency_ms must be non-negative")


class PipelineError(Exception):
    """Base class for expected pipeline failures eligible for controlled fallback."""


class RoutingError(PipelineError):
    """The intent router could not produce a valid decision."""


class RetrievalError(PipelineError):
    """All usable retrieval routes failed to produce a candidate pool."""


class RankingError(PipelineError):
    """The ranker could not produce a valid ordered result."""


@runtime_checkable
class RetrieverProtocol(Protocol):
    def retrieve(self, request: SearchRequest) -> CandidatePool:
        """Return a de-duplicated, pre-filter candidate pool."""
        ...


@runtime_checkable
class RankerProtocol(Protocol):
    def rank(self, request: SearchRequest, pool: CandidatePool) -> RankingResult:
        """Filter and rank one immutable candidate pool."""
        ...


def _to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field_info.name: _to_primitive(getattr(value, field_info.name)) for field_info in fields(value)}
    if isinstance(value, tuple):
        return [_to_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    return value


def contract_to_dict(value: object) -> dict[str, Any]:
    """Convert one contract dataclass into a JSON-ready diagnostic dictionary."""
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("value must be a contract dataclass instance")
    result = _to_primitive(value)
    assert isinstance(result, dict)
    return result


def contract_to_json(value: object, *, indent: int | None = 2) -> str:
    """Serialize one contract dataclass for logs and reproducible test fixtures."""
    return json.dumps(contract_to_dict(value), indent=indent, sort_keys=True)


__all__ = [
    "PIPELINE_CONTRACT_VERSION",
    "Candidate",
    "CandidatePool",
    "ConstraintTerm",
    "IntentRoute",
    "PipelineError",
    "ProfileSnapshot",
    "RankedCandidate",
    "RankerProtocol",
    "RankingError",
    "RankingExplanation",
    "RankingResult",
    "RetrievalError",
    "RetrieverProtocol",
    "RouteDecision",
    "RouteEvidence",
    "RoutingError",
    "SearchRequest",
    "StateSnapshot",
    "contract_to_dict",
    "contract_to_json",
]
