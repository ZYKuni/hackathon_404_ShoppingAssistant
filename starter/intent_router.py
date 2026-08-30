"""Deterministic, explainable Buying/Browsing intent routing.

Override is deliberately represented as an event on ``RouteDecision`` rather
than a third route.  Callers must apply the current turn's state patch before
calling this module so every decision reflects the latest session state.
"""
"""Explainable deterministic Buying/Browsing router."""

from __future__ import annotations

import re
from typing import Iterable

from starter.pipeline_contracts import (
    IntentRoute,
    RouteDecision,
    RoutingError,
    StateSnapshot,
)


STRONG_BUYING_RE = re.compile(
    r"\b(?:must|only|under|below|no\s+more\s+than|required)\b", re.IGNORECASE
)
WEAK_BUYING_RE = re.compile(r"\b(?:need|looking\s+for|want)\b", re.IGNORECASE)
STRONG_BROWSING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("still_exploring", re.compile(r"\bstill\s+exploring\b", re.IGNORECASE)),
    ("needs_ideas", re.compile(r"\b(?:need|want|show\s+me)?\s*(?:some\s+)?ideas\b", re.IGNORECASE)),
    ("open_ended_occasion", re.compile(r"\bsomething\s+for\b", re.IGNORECASE)),
    ("not_sure", re.compile(r"\bnot\s+sure\b", re.IGNORECASE)),
    ("open_to_options", re.compile(r"\bopen\s+to\b", re.IGNORECASE)),
)

PRICE_FIELDS = {"price_min", "price_max"}
EXPLICIT_ATTRIBUTE_FIELDS = {
    "audience",
    "brand",
    "color",
    "feature",
    "material",
    "price_max",
    "price_min",
    "size",
    "style",
}


def _field_names(snapshot: StateSnapshot) -> tuple[set[str], set[str], set[str]]:
    return (
        {term.field for term in snapshot.hard_constraints},
        {term.field for term in snapshot.soft_preferences},
        {term.field for term in snapshot.excluded},
    )


def _append_unique(signals: list[str], values: Iterable[str]) -> None:
    for value in values:
        if value not in signals:
            signals.append(value)


class IntentRouter:
    """Rule-based P0 router whose explanations map directly to named signals."""

    def route(
        self,
        current_message: str,
        state: StateSnapshot,
        override_detected: bool,
    ) -> RouteDecision:
        if not isinstance(current_message, str) or not current_message.strip():
            raise RoutingError("current_message must be a non-empty string")
        if not isinstance(state, StateSnapshot):
            raise RoutingError("state must be a StateSnapshot")
        if not isinstance(override_detected, bool):
            raise RoutingError("override_detected must be bool")

        hard_fields, soft_fields, excluded_fields = _field_names(state)
        all_constraint_fields = hard_fields | soft_fields | excluded_fields
        buying_signals: list[str] = []
        browsing_signals: list[str] = []

        if hard_fields:
            buying_signals.append("has_hard_constraint")
        if (hard_fields | soft_fields) & PRICE_FIELDS:
            buying_signals.append("has_budget")
        if "size" in hard_fields | soft_fields:
            buying_signals.append("has_size")
        if excluded_fields:
            buying_signals.append("has_exclusion")
        if STRONG_BUYING_RE.search(current_message):
            buying_signals.append("strong_buying_language")
        if state.category and all_constraint_fields:
            buying_signals.append("category_with_attributes")
        if WEAK_BUYING_RE.search(current_message):
            buying_signals.append("weak_buying_language")
        if (hard_fields | soft_fields) & {"brand", "color", "material"}:
            buying_signals.append("explicit_product_attribute")

        for signal, pattern in STRONG_BROWSING_PATTERNS:
            if pattern.search(current_message):
                browsing_signals.append(signal)
        if state.category and not all_constraint_fields:
            browsing_signals.append("category_only")
        if not hard_fields:
            browsing_signals.append("no_hard_constraint")
        if "use_case" in soft_fields and not ((hard_fields | soft_fields) & EXPLICIT_ATTRIBUTE_FIELDS):
            browsing_signals.append("scenario_without_product_attribute")

        signals: list[str] = []
        if override_detected:
            signals.append("override_detected")

        # The documented precedence is intentional: a confirmed hard boundary or
        # exclusion remains Buying even if the customer also says "exploring".
        if hard_fields or excluded_fields:
            _append_unique(signals, buying_signals)
            reason = self._buying_reason(hard_fields, excluded_fields)
            confidence = min(0.97, 0.84 + 0.025 * max(0, len(buying_signals) - 1))
            route = IntentRoute.BUYING
        elif browsing_signals and any(
            signal
            in {
                "still_exploring",
                "needs_ideas",
                "open_ended_occasion",
                "not_sure",
                "open_to_options",
            }
            for signal in browsing_signals
        ):
            _append_unique(signals, browsing_signals)
            reason = "The customer used open-ended browsing language and no hard constraint is present."
            confidence = min(0.95, 0.82 + 0.02 * max(0, len(browsing_signals) - 2))
            route = IntentRoute.BROWSING
        elif "strong_buying_language" in buying_signals:
            _append_unique(signals, buying_signals)
            reason = "The current message contains explicit high-intent buying language."
            confidence = 0.80
            route = IntentRoute.BUYING
        elif browsing_signals and "category_only" in browsing_signals:
            _append_unique(signals, browsing_signals)
            reason = "Only a product category is known and no hard constraint is present."
            confidence = 0.78
            route = IntentRoute.BROWSING
        elif "category_with_attributes" in buying_signals or len(
            (hard_fields | soft_fields) & EXPLICIT_ATTRIBUTE_FIELDS
        ) >= 2:
            _append_unique(signals, buying_signals)
            reason = "A product category or multiple explicit product attributes indicate focused buying intent."
            confidence = 0.72
            route = IntentRoute.BUYING
        else:
            _append_unique(signals, browsing_signals)
            _append_unique(signals, buying_signals)
            reason = "No hard purchase boundary is known, so the safer default is diversified browsing."
            confidence = 0.60
            route = IntentRoute.BROWSING


from .pipeline_contracts import IntentRoute, RouteDecision, StateSnapshot


BROWSING_RE = re.compile(
    r"\b(?:still exploring|ideas?|not sure|open to|something for|just browsing)\b", re.I
)
STRONG_BUYING_RE = re.compile(
    r"\b(?:must|only|under|below|no more than|at most|exactly)\b", re.I
)
WEAK_BUYING_RE = re.compile(r"\b(?:looking for|need|want)\b", re.I)


class IntentRouter:
    def route(
        self, current_message: str, state: StateSnapshot, override_detected: bool = False
    ) -> RouteDecision:
        signals: list[str] = []
        hard_fields = {term.field for term in state.hard_constraints}
        if hard_fields:
            signals.append("has_hard_constraint")
        if hard_fields & {"price_min", "price_max"}:
            signals.append("has_budget")
        if "size" in hard_fields:
            signals.append("has_size")
        if state.excluded:
            signals.append("has_exclusion")
        if STRONG_BUYING_RE.search(current_message):
            signals.append("strong_buying_language")
        if BROWSING_RE.search(current_message):
            signals.append("browsing_language")
        if WEAK_BUYING_RE.search(current_message):
            signals.append("weak_buying_language")
        if state.category:
            signals.append("has_category")

        strong_buying = bool(hard_fields or state.excluded or "strong_buying_language" in signals)
        if strong_buying:
            route = IntentRoute.BUYING
            confidence = 0.9 if hard_fields or state.excluded else 0.78
            reason = "Explicit hard constraints or exclusion signals require precision retrieval."
        elif "browsing_language" in signals:
            route = IntentRoute.BROWSING
            confidence = 0.88
            reason = "The customer is explicitly exploring without a hard constraint."
        elif state.category and len(signals) <= 2:
            route = IntentRoute.BROWSING
            confidence = 0.72
            reason = "Only a category and no hard constraint are currently known."
        elif "weak_buying_language" in signals and state.category:
            route = IntentRoute.BUYING
            confidence = 0.65
            reason = "A product category and weak purchase language indicate buying intent."
        else:
            route = IntentRoute.BROWSING
            confidence = 0.6
            reason = "No reliable high-intent constraint is currently available."
        return RouteDecision(
            route=route,
            confidence=confidence,
            reason=reason,
            signals=tuple(signals),
            override_detected=override_detected,
        )

    @staticmethod
    def _buying_reason(hard_fields: set[str], excluded_fields: set[str]) -> str:
        if hard_fields and excluded_fields:
            return "Confirmed hard constraints and explicit exclusions require high-precision buying retrieval."
        if excluded_fields:
            return "Explicit exclusions require high-precision buying retrieval."
        if hard_fields & PRICE_FIELDS:
            return "A confirmed budget boundary requires high-precision buying retrieval."
        if "size" in hard_fields:
            return "A confirmed size requirement requires high-precision buying retrieval."
        return "Confirmed hard constraints require high-precision buying retrieval."


def route(
    current_message: str,
    state: StateSnapshot,
    override_detected: bool = False,
) -> RouteDecision:
    """Convenience entry point for callers that do not need dependency injection."""
    return IntentRouter().route(current_message, state, override_detected)


__all__ = ["IntentRouter", "route"]
            signals=tuple(dict.fromkeys(signals)),
            override_detected=bool(override_detected),
        )


__all__ = ["IntentRouter"]
