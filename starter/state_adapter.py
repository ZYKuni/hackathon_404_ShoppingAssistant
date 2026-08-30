"""Pure adapters from mutable runtime state to Pipeline Contract v1 snapshots.

This module is Ethan plan phase E1 only.  It does not implement intent routing,
orchestration, retrieval, ranking, or any change to the public Agent behavior.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from starter.conversation_state import ConversationState
from starter.pipeline_contracts import (
    ConstraintTerm,
    ProfileSnapshot,
    StateSnapshot,
)


WHITESPACE_RE = re.compile(r"\s+")


def _deduplicate(values: Iterable[Any]) -> tuple[Any, ...]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _constraint_values(field_name: str, value: object) -> tuple[str | int | float, ...]:
    raw_values = value if isinstance(value, (list, tuple)) else (value,)
    normalized: list[str | int | float] = []
    for item in raw_values:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise TypeError(
                f"ConversationState field {field_name!r} must contain strings or finite numbers"
            )
        if isinstance(item, str):
            cleaned = item.strip()
            if not cleaned:
                raise ValueError(f"ConversationState field {field_name!r} contains an empty value")
            normalized.append(cleaned)
        else:
            if not math.isfinite(float(item)):
                raise ValueError(f"ConversationState field {field_name!r} contains a non-finite value")
            normalized.append(item)
    values = _deduplicate(normalized)
    if not values:
        raise ValueError(f"ConversationState field {field_name!r} must not be empty")
    return values


def _constraint_terms(constraints: dict[str, object]) -> tuple[ConstraintTerm, ...]:
    if not isinstance(constraints, dict):
        raise TypeError("constraint groups must be dictionaries")
    return tuple(
        ConstraintTerm(field=field_name, values=_constraint_values(field_name, constraints[field_name]))
        for field_name in sorted(constraints)
    )


def to_state_snapshot(state: ConversationState) -> StateSnapshot:
    """Return an immutable, deterministic snapshot without mutating ``state``."""
    if not isinstance(state, ConversationState):
        raise TypeError("state must be a ConversationState")
    return StateSnapshot(
        schema_version=state.schema_version,
        turn=state.turn,
        category=state.category,
        hard_constraints=_constraint_terms(state.hard_constraints),
        soft_preferences=_constraint_terms(state.soft_preferences),
        excluded=_constraint_terms(state.excluded),
        no_preference=_deduplicate(state.no_preference),
        asked_attributes=_deduplicate(state.asked_attributes),
    )


def _optional_profile_text(profile: dict, key: str) -> str | None:
    value = profile.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"profile {key!r} must be a string or None")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"profile {key!r} must not be empty")
    return cleaned


def to_profile_snapshot(profile: dict) -> ProfileSnapshot:
    """Copy the safe aggregate profile into its immutable contract representation."""
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dictionary")

    raw_tags = profile.get("preference_tags", ())
    if not isinstance(raw_tags, (list, tuple)):
        raise TypeError("profile 'preference_tags' must be a list or tuple")
    tags: list[str] = []
    for value in raw_tags:
        if not isinstance(value, str):
            raise TypeError("profile preference tags must be strings")
        normalized = WHITESPACE_RE.sub(" ", value.strip().lower())
        if not normalized:
            raise ValueError("profile preference tags must not be empty")
        tags.append(normalized)

    raw_rating = profile.get("average_prior_rating")
    if raw_rating is not None:
        if isinstance(raw_rating, bool) or not isinstance(raw_rating, (int, float)):
            raise TypeError("profile 'average_prior_rating' must be numeric or None")
        average_prior_rating: float | None = float(raw_rating)
    else:
        average_prior_rating = None

    return ProfileSnapshot(
        preference_tags=_deduplicate(tags),
        average_prior_rating=average_prior_rating,
        purchase_frequency=_optional_profile_text(profile, "purchase_frequency"),
        rating_style=_optional_profile_text(profile, "rating_style"),
    )


def _query_value(value: str | int | float) -> str:
    text = str(value).replace("_", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def build_structured_query(snapshot: StateSnapshot) -> str:
    """Render positive session constraints as deterministic lexical evidence.

    Exclusions, no-preference markers, asked attributes, and user-profile data are
    intentionally omitted so that negative or administrative state cannot become
    positive retrieval evidence.
    """
    if not isinstance(snapshot, StateSnapshot):
        raise TypeError("snapshot must be a StateSnapshot")

    values: list[str] = []
    if snapshot.category is not None:
        values.append(_query_value(snapshot.category))
    for term in (*snapshot.hard_constraints, *snapshot.soft_preferences):
        values.extend(_query_value(value) for value in term.values)

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return " ".join(result)


__all__ = [
    "build_structured_query",
    "to_profile_snapshot",
    "to_state_snapshot",
]
