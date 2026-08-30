"""Explainable deterministic Buying/Browsing router."""

from __future__ import annotations

import re

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
            signals=tuple(dict.fromkeys(signals)),
            override_detected=bool(override_detected),
        )


__all__ = ["IntentRouter"]
