"""Three-state product constraint matching for Aaron's local ranker.

Missing or weak catalog evidence is UNKNOWN, never an implicit mismatch.  Only
high-confidence hard/excluded mismatches are eligible for filtering.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

from .attribute_lexicons import STATE_FIELDS, canonicalize, normalize_phrase
from .catalog_normalizer import ExtractedValue, NormalizedProduct
from .pipeline_contracts import ConstraintTerm, ScalarValue, StateSnapshot


DEFAULT_HARD_FILTER_THRESHOLD = 0.85


class MatchState(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConstraintMatch:
    field: str
    expected: tuple[ScalarValue, ...]
    state: MatchState
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("field must not be empty")
        if not self.expected:
            raise ValueError("expected must not be empty")
        if not isinstance(self.state, MatchState):
            raise TypeError("state must be a MatchState")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.reason:
            raise ValueError("reason must not be empty")


@dataclass(frozen=True)
class ProductConstraintEvaluation:
    parent_asin: str
    hard: tuple[ConstraintMatch, ...]
    soft: tuple[ConstraintMatch, ...]
    excluded: tuple[ConstraintMatch, ...]
    hard_filter_threshold: float = DEFAULT_HARD_FILTER_THRESHOLD

    def __post_init__(self) -> None:
        if not self.parent_asin:
            raise ValueError("parent_asin must not be empty")
        if not 0.0 <= self.hard_filter_threshold <= 1.0:
            raise ValueError("hard_filter_threshold must be between 0 and 1")

    @property
    def should_filter(self) -> bool:
        decisive = self.hard + self.excluded
        return any(
            item.state is MatchState.MISMATCH
            and item.confidence >= self.hard_filter_threshold
            for item in decisive
        )

    @property
    def unknown_count(self) -> int:
        return sum(
            item.state is MatchState.UNKNOWN
            for item in self.hard + self.soft + self.excluded
        )


_ATTRIBUTE_FIELDS = {
    "audience": "audiences",
    "material": "materials",
    "color": "colors",
    "brand": "brands",
    "size": "sizes",
    "style": "styles",
}


def _canonical_expected(field: str, values: tuple[ScalarValue, ...]) -> tuple[ScalarValue, ...]:
    canonical: list[ScalarValue] = []
    for value in values:
        converted = canonicalize(field, value)
        if converted not in canonical:
            canonical.append(converted)  # type: ignore[arg-type]
    return tuple(canonical)


def _unknown(field: str, expected: tuple[ScalarValue, ...], reason: str) -> ConstraintMatch:
    return ConstraintMatch(field, expected, MatchState.UNKNOWN, 0.0, reason)


def _match_values(
    field: str,
    expected: tuple[ScalarValue, ...],
    observed: tuple[ExtractedValue, ...],
    *,
    excluded: bool,
    decisive_threshold: float,
) -> ConstraintMatch:
    if not observed:
        return _unknown(field, expected, f"product has no reliable {field} value")

    expected_set = set(expected)
    matching = [item for item in observed if item.value in expected_set]
    if matching:
        confidence = max(item.confidence for item in matching)
        if excluded:
            if confidence < decisive_threshold:
                return ConstraintMatch(
                    field, expected, MatchState.UNKNOWN, confidence,
                    f"excluded {field} appears only in weak evidence",
                )
            return ConstraintMatch(
                field, expected, MatchState.MISMATCH, confidence,
                f"product explicitly contains excluded {field}",
            )
        return ConstraintMatch(
            field, expected, MatchState.MATCH, confidence,
            f"product contains an accepted {field}",
        )

    confidence = max(item.confidence for item in observed)
    if confidence < decisive_threshold:
        return ConstraintMatch(
            field, expected, MatchState.UNKNOWN, confidence,
            f"only weak non-matching {field} evidence is available",
        )
    return ConstraintMatch(
        field,
        expected,
        MatchState.MATCH if excluded else MatchState.MISMATCH,
        confidence,
        (
            f"product has a reliable non-excluded {field}"
            if excluded
            else f"product has a reliable conflicting {field}"
        ),
    )


def _contains_phrase(text: str, expected: str) -> bool:
    words = [re.escape(word) for word in normalize_phrase(expected).split()]
    if not words:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + r"\s+".join(words) + r"(?![a-z0-9])", text))


class ConstraintMatcher:
    def __init__(self, hard_filter_threshold: float = DEFAULT_HARD_FILTER_THRESHOLD) -> None:
        if not 0.0 <= hard_filter_threshold <= 1.0:
            raise ValueError("hard_filter_threshold must be between 0 and 1")
        self.hard_filter_threshold = hard_filter_threshold

    def match_term(
        self,
        term: ConstraintTerm,
        product: NormalizedProduct,
        *,
        excluded: bool = False,
    ) -> ConstraintMatch:
        field = term.field
        if field not in STATE_FIELDS:
            raise ValueError(f"unsupported constraint field: {field}")
        expected = _canonical_expected(field, term.values)

        if field in {"price_min", "price_max"}:
            if len(expected) != 1:
                raise ValueError(f"{field} requires exactly one boundary")
            return self._match_price(field, expected, product.price)
        if field == "category":
            return self._match_category(expected, product, excluded=excluded)
        if field in {"feature", "use_case"}:
            return self._match_features(field, expected, product, excluded=excluded)
        observed = getattr(product, _ATTRIBUTE_FIELDS[field])
        return _match_values(
            field,
            expected,
            observed,
            excluded=excluded,
            decisive_threshold=self.hard_filter_threshold,
        )

    def evaluate(
        self, state: StateSnapshot, product: NormalizedProduct
    ) -> ProductConstraintEvaluation:
        hard_terms = list(state.hard_constraints)
        if state.category is not None and not any(term.field == "category" for term in hard_terms):
            hard_terms.insert(0, ConstraintTerm("category", (state.category,)))
        hard = tuple(self.match_term(term, product) for term in hard_terms)
        soft = tuple(self.match_term(term, product) for term in state.soft_preferences)
        excluded = tuple(
            self.match_term(term, product, excluded=True) for term in state.excluded
        )
        return ProductConstraintEvaluation(
            parent_asin=product.parent_asin,
            hard=hard,
            soft=soft,
            excluded=excluded,
            hard_filter_threshold=self.hard_filter_threshold,
        )

    @staticmethod
    def _match_price(
        field: str,
        expected: tuple[ScalarValue, ...],
        observed: ExtractedValue | None,
    ) -> ConstraintMatch:
        if observed is None:
            return _unknown(field, expected, "product price is unknown")
        boundary = float(expected[-1])
        price = float(observed.value)
        matched = price >= boundary if field == "price_min" else price <= boundary
        return ConstraintMatch(
            field,
            expected,
            MatchState.MATCH if matched else MatchState.MISMATCH,
            observed.confidence,
            f"product price {price:g} {'satisfies' if matched else 'violates'} {field} {boundary:g}",
        )

    @staticmethod
    def _match_category(
        expected: tuple[ScalarValue, ...],
        product: NormalizedProduct,
        *,
        excluded: bool,
    ) -> ConstraintMatch:
        if not product.category_path and not product.leaf_categories:
            return _unknown("category", expected, "product category is unknown")
        expected_set = {str(value) for value in expected}
        leaf_set = {str(item.value) for item in product.leaf_categories}
        path_set = set(product.category_path)
        matched = bool(expected_set & (leaf_set | path_set))
        confidence = max(
            (item.confidence for item in product.leaf_categories), default=0.85
        )
        if matched:
            return ConstraintMatch(
                "category",
                expected,
                MatchState.MISMATCH if excluded else MatchState.MATCH,
                confidence,
                "product category path contains the requested category",
            )
        return ConstraintMatch(
            "category",
            expected,
            MatchState.MATCH if excluded else MatchState.MISMATCH,
            confidence,
            "product category path conflicts with the requested category",
        )

    @staticmethod
    def _match_features(
        field: str,
        expected: tuple[ScalarValue, ...],
        product: NormalizedProduct,
        *,
        excluded: bool,
    ) -> ConstraintMatch:
        if not product.features:
            return _unknown(field, expected, f"product has no {field} evidence")
        normalized_features = tuple(normalize_phrase(value) for value in product.features)
        matched = any(
            _contains_phrase(text, str(value))
            for value in expected
            for text in normalized_features
        )
        confidence = 0.75
        if matched and excluded:
            return ConstraintMatch(
                field, expected, MatchState.UNKNOWN, confidence,
                f"excluded {field} appears only in feature text",
            )
        if matched:
            return ConstraintMatch(
                field, expected, MatchState.MATCH, confidence,
                f"feature text contains requested {field}",
            )
        return ConstraintMatch(
            field, expected, MatchState.UNKNOWN, confidence,
            f"feature text does not provide decisive {field} evidence",
        )
