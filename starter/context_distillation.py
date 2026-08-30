"""Safe short-term and long-term context for question selection.

The current conversation remains authoritative.  Historical profile fields are
distilled into low-weight hints only; they are never emitted as hard constraints
and must not override an explicit current-session value or exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from starter.attribute_lexicons import ask_attribute_for, normalize_phrase
from starter.conversation_state import ConversationState


PROFILE_TAG_TO_ASK_ATTRIBUTE = {
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


@dataclass(frozen=True)
class ProfileContext:
    """Validated long-term aggregate data, kept separate from live constraints."""

    preference_tags: tuple[str, ...] = ()
    rating_style: str | None = None
    purchase_frequency: str | None = None
    average_prior_rating: float | None = None
    question_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShortTermContext:
    """Compact, immutable view of the current shopping conversation."""

    category: str | None
    hard_fields: tuple[str, ...]
    soft_fields: tuple[str, ...]
    excluded_fields: tuple[str, ...]
    no_preference: tuple[str, ...]
    asked_attributes: tuple[str, ...]
    turn: int
    override_detected: bool = False

    @property
    def known_ask_attributes(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.category:
            result.append("category")
        for field_name in (*self.hard_fields, *self.soft_fields):
            attribute = ask_attribute_for(field_name)
            if attribute not in result:
                result.append(attribute)
        return tuple(result)


@dataclass(frozen=True)
class PersonalizedContext:
    short_term: ShortTermContext
    profile: ProfileContext


def _clean_optional_text(value: object) -> str | None:
    if value in (None, ""):
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def distill_profile(user_profile: Mapping[str, Any] | None) -> ProfileContext:
    """Normalize only the allowed aggregate profile fields.

    Free-form ``summary`` text is deliberately excluded so that historical text
    cannot silently become a live hard constraint or lexical retrieval query.
    """

    payload = user_profile or {}
    raw_tags = payload.get("preference_tags")
    tags: list[str] = []
    if isinstance(raw_tags, (list, tuple)):
        for raw_tag in raw_tags:
            tag = normalize_phrase(raw_tag)
            if tag and tag not in tags:
                tags.append(tag)

    hints: list[str] = []
    for tag in tags:
        hint = PROFILE_TAG_TO_ASK_ATTRIBUTE.get(tag)
        if hint and hint not in hints:
            hints.append(hint)

    raw_rating = payload.get("average_prior_rating")
    rating: float | None = None
    if raw_rating not in (None, "") and not isinstance(raw_rating, bool):
        try:
            candidate = float(raw_rating)
        except (TypeError, ValueError):
            candidate = -1.0
        if 0.0 <= candidate <= 5.0:
            rating = candidate

    return ProfileContext(
        preference_tags=tuple(tags),
        rating_style=_clean_optional_text(payload.get("rating_style")),
        purchase_frequency=_clean_optional_text(payload.get("purchase_frequency")),
        average_prior_rating=rating,
        question_hints=tuple(hints),
    )


def distill_short_term(
    state: ConversationState,
    *,
    override_detected: bool = False,
) -> ShortTermContext:
    return ShortTermContext(
        category=state.category,
        hard_fields=tuple(sorted(state.hard_constraints)),
        soft_fields=tuple(sorted(state.soft_preferences)),
        excluded_fields=tuple(sorted(state.excluded)),
        no_preference=tuple(state.no_preference),
        asked_attributes=tuple(state.asked_attributes),
        turn=state.turn,
        override_detected=override_detected,
    )


def distill_context(
    state: ConversationState,
    profile: ProfileContext,
    *,
    override_detected: bool = False,
) -> PersonalizedContext:
    return PersonalizedContext(
        short_term=distill_short_term(state, override_detected=override_detected),
        profile=profile,
    )


def constraint_signature(state: ConversationState) -> tuple[object, ...]:
    """Return the effective product constraints, excluding QA bookkeeping."""

    def freeze_mapping(mapping: Mapping[str, Any]) -> tuple[tuple[str, object], ...]:
        frozen: list[tuple[str, object]] = []
        for field_name, value in sorted(mapping.items()):
            normalized = tuple(value) if isinstance(value, list) else value
            frozen.append((field_name, normalized))
        return tuple(frozen)

    return (
        state.category,
        freeze_mapping(state.hard_constraints),
        freeze_mapping(state.soft_preferences),
        freeze_mapping(state.excluded),
    )


__all__ = [
    "PersonalizedContext",
    "ProfileContext",
    "ShortTermContext",
    "constraint_signature",
    "distill_context",
    "distill_profile",
    "distill_short_term",
]
