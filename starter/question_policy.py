"""Candidate-aware clarification question policy.

Question value follows the Todo's first version:

    candidate diversity × catalog coverage × route relevance × expected reduction

The implementation is deterministic and offline.  It returns diagnostics so
experiments can report the exact question ordering for a given commit.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from starter.attribute_lexicons import (
    COLOR_ALIASES,
    FEATURE_ALIASES,
    MATERIAL_ALIASES,
    USE_CASE_ALIASES,
    normalize_phrase,
)
from starter.context_distillation import PersonalizedContext
from starter.pipeline_contracts import IntentRoute


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
        # Size extraction from free-form catalog copy is noisy (model numbers,
        # pack counts, and measurements); require stronger differentiation.
        "size": 0.55,
        "material": 1.15,
        "feature": 1.05,
        "color": 0.95,
        "style": 0.85,
        "use_case": 0.75,
        # ``store`` is only a weak brand proxy until catalog normalization lands.
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
    IntentRoute.BUYING: ("material", "feature", "color", "style", "size", "use_case", "budget", "brand"),
    IntentRoute.BROWSING: ("use_case", "feature", "style", "material", "color", "size", "budget", "brand"),
}

STYLE_ALIASES = {
    "athletic": "athletic",
    "casual": "casual",
    "formal": "formal",
    "loose": "loose",
    "relaxed": "relaxed",
    "slim fit": "slim_fit",
    "vintage": "vintage",
    "long sleeve": "long_sleeve",
    "short sleeve": "short_sleeve",
}

SIZE_RE = re.compile(
    r"\b(?:size\s*)?(xxs|xs|s|m|l|xl|xxl|xxxl|small|medium|large|\d{1,2}(?:\.5)?(?:\s*(?:wide|narrow))?)\b",
    re.IGNORECASE,
)

BROWSING_RE = re.compile(
    r"\b(?:browsing|exploring|ideas?|inspiration|not sure|open to|something for)\b",
    re.IGNORECASE,
)
BUYING_RE = re.compile(
    r"\b(?:must|need|require|only|under|below|up to|size|budget|exactly|key requirement)\b|\$\s*\d",
    re.IGNORECASE,
)


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


def infer_route(
    user_message: str,
    context: PersonalizedContext,
) -> IntentRoute:
    """Small deterministic fallback until the team router is connected."""

    if context.short_term.override_detected:
        return IntentRoute.BUYING
    if BUYING_RE.search(user_message) or context.short_term.hard_fields:
        return IntentRoute.BUYING
    if BROWSING_RE.search(user_message):
        return IntentRoute.BROWSING
    return IntentRoute.BROWSING if context.short_term.category else IntentRoute.BUYING


def _phrase_values(text: str, aliases: Mapping[str, str]) -> tuple[str, ...]:
    normalized = f" {normalize_phrase(text)} "
    result: list[str] = []
    occupied: list[tuple[int, int]] = []
    for phrase in sorted(aliases, key=lambda item: (-len(item), item)):
        needle = f" {normalize_phrase(phrase)} "
        start = normalized.find(needle)
        if start < 0:
            continue
        span = (start, start + len(needle))
        if any(span[0] < other[1] and other[0] < span[1] for other in occupied):
            continue
        value = aliases[phrase]
        if value not in result:
            result.append(value)
        occupied.append(span)
    return tuple(result)


def candidate_facets_from_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, tuple[str | None, ...]]:
    """Extract conservative per-candidate facets from already-retrieved rows."""

    facets: dict[str, list[str | None]] = {
        attribute: []
        for attribute in ("material", "feature", "color", "style", "size", "use_case", "brand")
    }
    for row in rows:
        searchable = " ".join(
            str(row.get(field) or "")
            for field in ("title", "categories", "features", "details", "description")
        )
        mapping_by_attribute = {
            "material": MATERIAL_ALIASES,
            "feature": FEATURE_ALIASES,
            "color": COLOR_ALIASES,
            "style": STYLE_ALIASES,
            "use_case": USE_CASE_ALIASES,
        }
        for attribute, aliases in mapping_by_attribute.items():
            values = _phrase_values(searchable, aliases)
            facets[attribute].append("|".join(values) if values else None)
        sizes = [normalize_phrase(match).upper() for match in SIZE_RE.findall(searchable)]
        facets["size"].append("|".join(dict.fromkeys(sizes)) if sizes else None)
        brand = normalize_phrase(row.get("store") or "")
        facets["brand"].append(brand[:64] if brand else None)
    return {attribute: tuple(values) for attribute, values in facets.items()}


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
        entropy = -sum((count / len(observed)) * math.log(count / len(observed)) for count in counts.values())
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


class QuestionPolicy:
    """Select one non-repeating question while still allowing recommendations."""

    def __init__(self, *, enable_profile_hints: bool = True) -> None:
        self.enable_profile_hints = enable_profile_hints

    def choose(
        self,
        context: PersonalizedContext,
        route: IntentRoute,
        candidate_facets: Mapping[str, Sequence[str | None]],
        *,
        rounds_without_new_constraints: int = 0,
        other_used: bool = False,
        category_evidence: bool = False,
    ) -> QuestionDecision:
        short_term = context.short_term
        if short_term.turn >= 10:
            return QuestionDecision(
                ask_attribute=None,
                message="Here are the best matches based on your current preferences.",
                reason="Turn 10 must end without another clarification question.",
            )

        blocked = set(short_term.asked_attributes) | set(short_term.no_preference)
        known = set(short_term.known_ask_attributes)
        if not short_term.category and not category_evidence and "category" not in blocked:
            return QuestionDecision(
                ask_attribute="category",
                message=QUESTION_TEXT["category"],
                reason="A useful product category is not known yet.",
            )

        if rounds_without_new_constraints >= 2 and not other_used and "other" not in blocked:
            return QuestionDecision(
                ask_attribute="other",
                message=QUESTION_TEXT["other"],
                reason="Two consecutive replies added no effective constraint; using the one-time other escape hatch.",
            )

        eligible = [
            attribute
            for attribute in FALLBACK_ORDER[route]
            if attribute not in blocked and attribute not in known
        ]
        profile_hints = set(context.profile.question_hints) if self.enable_profile_hints else set()
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
                "Highest candidate-aware value: diversity × coverage × route relevance × expected reduction"
                if selected.value > 0
                else "Candidate facets were inconclusive; used the route-specific deterministic fallback order."
            )
            return QuestionDecision(
                ask_attribute=selected.attribute,
                message=QUESTION_TEXT[selected.attribute],
                reason=reason,
                scores=tuple(ranked),
            )

        return QuestionDecision(
            ask_attribute=None,
            message="Here are the best matches based on your current preferences.",
            reason="No unanswered, useful attribute remains.",
            scores=scores,
        )


__all__ = [
    "AttributeScore",
    "QuestionDecision",
    "QuestionPolicy",
    "QUESTION_TEXT",
    "candidate_facets_from_rows",
    "infer_route",
]
