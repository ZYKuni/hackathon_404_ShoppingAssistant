"""Pure adapters from mutable session state to immutable pipeline contracts."""

from __future__ import annotations

from .conversation_state import ConversationState
from .pipeline_contracts import ConstraintTerm, ProfileSnapshot, StateSnapshot


def _terms(values: dict[str, object]) -> tuple[ConstraintTerm, ...]:
    result = []
    for field in sorted(values):
        value = values[field]
        items = value if isinstance(value, list) else [value]
        result.append(ConstraintTerm(field, tuple(items)))
    return tuple(result)


def to_state_snapshot(state: ConversationState) -> StateSnapshot:
    return StateSnapshot(
        schema_version=state.schema_version,
        turn=state.turn,
        category=state.category,
        hard_constraints=_terms(state.hard_constraints),
        soft_preferences=_terms(state.soft_preferences),
        excluded=_terms(state.excluded),
        no_preference=tuple(state.no_preference),
        asked_attributes=tuple(state.asked_attributes),
    )


def to_profile_snapshot(profile: dict) -> ProfileSnapshot:
    average = profile.get("average_prior_rating")
    return ProfileSnapshot(
        preference_tags=tuple(str(item) for item in profile.get("preference_tags", [])),
        average_prior_rating=float(average) if average is not None else None,
        purchase_frequency=(
            str(profile["purchase_frequency"])
            if profile.get("purchase_frequency") is not None else None
        ),
        rating_style=str(profile["rating_style"]) if profile.get("rating_style") is not None else None,
    )


def build_structured_query(snapshot: StateSnapshot) -> str:
    values: list[str] = []
    if snapshot.category:
        values.append(snapshot.category.replace("_", " "))
    for group in (snapshot.hard_constraints, snapshot.soft_preferences):
        for term in group:
            values.extend(str(value).replace("_", " ") for value in term.values)
    return " ".join(dict.fromkeys(value for value in values if value))


__all__ = ["build_structured_query", "to_profile_snapshot", "to_state_snapshot"]
