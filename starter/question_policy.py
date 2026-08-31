"""Candidate-aware clarification policy for the formal Top-200 pipeline.

The production-safe mode preserves the validated fixed question order.  Shadow
mode computes the candidate-aware decision without changing the Agent response;
dynamic mode applies it explicitly for ablation and targeted QA.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .attribute_lexicons import (
    FEATURE_ALIASES,
    USE_CASE_ALIASES,
    ask_attribute_for,
    normalize_phrase,
)
from .catalog_normalizer import ExtractedValue, NormalizedProduct
from .pipeline_contracts import IntentRoute, ProfileSnapshot, RouteDecision, StateSnapshot


QUESTION_TEXT = {
    "category": "What type of product are you looking for?",
    "material": "Do you have a preferred material?",
    "feature": "Which product feature matters most to you?",
    "color": "Do you have a color preference?",
    "style": "What style or fit would you prefer?",
    "size": "Do you have a size or width requirement?",
    "use_case": "What occasion or use case is this for?",
    "budget": "What budget range should I use?",
    "brand": "Do you have a preferred brand?",
    "other": "Is there one other must-have requirement I should prioritize?",
}

ROUTE_RELEVANCE = {
    IntentRoute.BUYING: {
        "budget": 1.30,
        "material": 1.15,
        "feature": 1.05,
        "color": 0.95,
        "style": 0.85,
        "use_case": 0.75,
        # Size and store-derived brand remain conservative until category-aware
        # size parsing and stronger brand evidence are available.
        "size": 0.55,
        "brand": 0.35,
    },
    IntentRoute.BROWSING: {
        "use_case": 1.35,
        "feature": 1.30,
        "style": 1.15,
        "material": 1.00,
        "color": 0.90,
        "budget": 0.80,
        "size": 0.35,
        "brand": 0.30,
    },
}

FALLBACK_ORDER = {
    IntentRoute.BUYING: (
        "material", "feature", "color", "style", "size", "use_case", "budget", "brand"
    ),
    IntentRoute.BROWSING: (
        "use_case", "feature", "style", "material", "color", "size", "budget", "brand"
    ),
}

PROFILE_TAG_TO_ATTRIBUTE = {
    "brand": "brand",
    "budget": "budget",
    "color": "color",
    "comfort": "feature",
    "durability": "feature",
    "feature": "feature",
    "fit": "style",
    "material": "material",
    "occasion": "use_case",
    "price": "budget",
    "size": "size",
    "style": "style",
    "use case": "use_case",
    "value": "budget",
    "warmth": "feature",
    "weather": "feature",
}


class QuestionPolicyMode(str, Enum):
    SAFE = "safe"
    SHADOW = "shadow"
    DYNAMIC = "dynamic"


@dataclass(frozen=True)
class AttributeScore:
    attribute: str
    diversity: float
    coverage: float
    route_relevance: float
    expected_reduction: float
    profile_multiplier: float
    value: float


@dataclass(frozen=True)
class QuestionDecision:
    ask_attribute: str | None
    message: str
    reason: str
    scores: tuple[AttributeScore, ...] = ()


@dataclass(frozen=True)
class QuestionPolicyDiagnostics:
    mode: QuestionPolicyMode
    route: IntentRoute | None
    candidate_count: int
    selected_attribute: str | None
    applied_attribute: str | None
    reason: str
    scores: tuple[AttributeScore, ...] = ()


def _extracted_values(values: Sequence[ExtractedValue]) -> tuple[str, ...]:
    return tuple(sorted({str(item.value) for item in values if item.confidence >= 0.50}))


def _alias_values(text: str, aliases: Mapping[str, str]) -> tuple[str, ...]:
    normalized = normalize_phrase(text)
    result: list[str] = []
    for phrase in sorted(aliases, key=lambda item: (-len(normalize_phrase(item)), item)):
        needle = normalize_phrase(phrase)
        if not needle or not re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", normalized):
            continue
        value = aliases[phrase]
        if value not in result:
            result.append(value)
    return tuple(result)


def _facet_value(values: Sequence[object]) -> str | None:
    cleaned = tuple(dict.fromkeys(str(value) for value in values if value not in (None, "")))
    return "|".join(cleaned) if cleaned else None


def _price_bucket(product: NormalizedProduct) -> str | None:
    if product.price is None:
        return None
    price = float(product.price.value)
    if price < 25:
        return "under_25"
    if price < 50:
        return "25_50"
    if price < 100:
        return "50_100"
    if price < 200:
        return "100_200"
    return "200_plus"


def candidate_facets_from_products(
    products: Iterable[NormalizedProduct],
) -> dict[str, tuple[str | None, ...]]:
    """Build aligned facet values from the normalized Top-200 candidate pool."""

    facets: dict[str, list[str | None]] = {
        name: []
        for name in ("material", "feature", "color", "style", "size", "use_case", "budget", "brand")
    }
    for product in products:
        searchable_features = " ".join((*product.features, *product.category_path))
        facets["material"].append(_facet_value(_extracted_values(product.materials)))
        facets["color"].append(_facet_value(_extracted_values(product.colors)))
        facets["style"].append(_facet_value(_extracted_values(product.styles)))
        facets["size"].append(_facet_value(_extracted_values(product.sizes)))
        facets["brand"].append(_facet_value(_extracted_values(product.brands)))
        facets["feature"].append(_facet_value(_alias_values(searchable_features, FEATURE_ALIASES)))
        facets["use_case"].append(_facet_value(_alias_values(searchable_features, USE_CASE_ALIASES)))
        facets["budget"].append(_price_bucket(product))
    return {name: tuple(values) for name, values in facets.items()}


def _attribute_score(
    attribute: str,
    values: Sequence[str | None],
    route: IntentRoute,
    *,
    profile_hint: bool,
) -> AttributeScore:
    total = len(values)
    observed = [str(value) for value in values if value not in (None, "", "unknown")]
    if total == 0 or len(set(observed)) < 2:
        diversity = coverage = expected_reduction = 0.0
    else:
        counts = Counter(observed)
        coverage = len(observed) / total
        entropy = -sum(
            (count / len(observed)) * math.log(count / len(observed))
            for count in counts.values()
        )
        diversity = entropy / math.log(len(counts)) if len(counts) > 1 else 0.0
        expected_reduction = 1.0 - max(counts.values()) / len(observed)
    relevance = ROUTE_RELEVANCE[route][attribute]
    profile_multiplier = 1.08 if profile_hint else 1.0
    value = diversity * coverage * relevance * expected_reduction * profile_multiplier
    return AttributeScore(
        attribute=attribute,
        diversity=round(diversity, 6),
        coverage=round(coverage, 6),
        route_relevance=relevance,
        expected_reduction=round(expected_reduction, 6),
        profile_multiplier=profile_multiplier,
        value=round(value, 6),
    )


def _ask_attribute(field: str) -> str:
    try:
        return ask_attribute_for(field)
    except ValueError:
        return field


class QuestionPolicy:
    """Select one useful non-repeating question from normalized candidates."""

    def __init__(self, *, enable_profile_hints: bool = True) -> None:
        self.enable_profile_hints = enable_profile_hints

    def choose(
        self,
        state: StateSnapshot,
        profile: ProfileSnapshot,
        route_decision: RouteDecision,
        candidate_facets: Mapping[str, Sequence[str | None]],
        *,
        rounds_without_new_constraints: int = 0,
        other_used: bool = False,
        category_evidence: bool = False,
    ) -> QuestionDecision:
        if state.turn >= 10:
            return QuestionDecision(
                None,
                "Here are the best matches based on your current preferences.",
                "Turn 10 must end without another clarification question.",
            )

        blocked = set(state.asked_attributes) | {_ask_attribute(item) for item in state.no_preference}
        known = {
            _ask_attribute(term.field)
            for group in (state.hard_constraints, state.soft_preferences)
            for term in group
        }
        if state.category:
            known.add("category")

        if not state.category and not category_evidence and "category" not in blocked:
            return QuestionDecision(
                "category",
                QUESTION_TEXT["category"],
                "A useful product category is not known yet.",
            )

        if rounds_without_new_constraints >= 2 and not other_used and "other" not in blocked:
            return QuestionDecision(
                "other",
                QUESTION_TEXT["other"],
                "Two consecutive replies added no effective constraint; using the one-time other escape hatch.",
            )

        route = route_decision.route
        eligible = [
            attribute
            for attribute in FALLBACK_ORDER[route]
            if attribute not in blocked and attribute not in known
        ]
        profile_hints = (
            {
                PROFILE_TAG_TO_ATTRIBUTE[normalize_phrase(tag)]
                for tag in profile.preference_tags
                if normalize_phrase(tag) in PROFILE_TAG_TO_ATTRIBUTE
            }
            if self.enable_profile_hints
            else set()
        )
        scores = tuple(
            _attribute_score(
                attribute,
                candidate_facets.get(attribute, ()),
                route,
                profile_hint=attribute in profile_hints,
            )
            for attribute in eligible
        )
        order_index = {attribute: index for index, attribute in enumerate(FALLBACK_ORDER[route])}
        ranked = sorted(scores, key=lambda item: (-item.value, order_index[item.attribute]))
        if ranked:
            selected = ranked[0]
            reason = (
                "Highest Top-200 value: diversity × coverage × route relevance × expected reduction."
                if selected.value > 0
                else "Candidate facets were inconclusive; used the route-specific fallback order."
            )
            return QuestionDecision(
                selected.attribute,
                QUESTION_TEXT[selected.attribute],
                reason,
                tuple(ranked),
            )

        return QuestionDecision(
            None,
            "Here are the best matches based on your current preferences.",
            "No unanswered, useful attribute remains.",
            scores,
        )


__all__ = [
    "AttributeScore",
    "FALLBACK_ORDER",
    "QUESTION_TEXT",
    "QuestionDecision",
    "QuestionPolicy",
    "QuestionPolicyDiagnostics",
    "QuestionPolicyMode",
    "candidate_facets_from_products",
]
