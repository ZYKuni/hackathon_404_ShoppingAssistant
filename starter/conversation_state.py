"""Versioned conversation state and deterministic StatePatch merge semantics.

This module defines what a correct state update means.  It does not attempt to
extract patches from natural language; rule-based or model-based parsers should
produce ``StatePatch`` objects and be evaluated against the golden cases.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from starter.attribute_lexicons import (
    ALLOWED_ASK_ATTRIBUTES,
    DEFAULT_STRENGTH,
    MULTI_VALUE_FIELDS,
    PRODUCT_SCOPED_FIELDS,
    SCHEMA_VERSION,
    SINGLE_VALUE_FIELDS,
    STATE_FIELDS,
    ask_attribute_for,
    canonicalize,
)


class Strength(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class Operation(str, Enum):
    SET = "set"
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    CLEAR = "clear"
    EXCLUDE = "exclude"
    ALLOW = "allow"
    SET_NO_PREFERENCE = "set_no_preference"
    RESET_SCOPE = "reset_scope"


@dataclass(frozen=True)
class PatchOperation:
    op: Operation
    field: str | None = None
    value: Any = None
    strength: Strength | None = None
    evidence: str = ""
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.op == Operation.RESET_SCOPE:
            if self.field not in (None, "product_constraints"):
                raise ValueError("reset_scope only supports product_constraints")
            return
        if self.field not in STATE_FIELDS:
            raise ValueError(f"Unsupported state field: {self.field}")
        if self.op in {Operation.EXCLUDE, Operation.ALLOW} and self.field in SINGLE_VALUE_FIELDS:
            raise ValueError(f"{self.op.value} is invalid for single-value field {self.field}")
        if self.op not in {Operation.CLEAR, Operation.SET_NO_PREFERENCE} and self.value is None:
            raise ValueError(f"{self.op.value} requires a value")
        if self.op in {Operation.SET, Operation.ADD, Operation.REPLACE} and self.strength is None:
            object.__setattr__(self, "strength", Strength(DEFAULT_STRENGTH[self.field]))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PatchOperation":
        strength = payload.get("strength")
        return cls(
            op=Operation(payload["op"]),
            field=payload.get("field"),
            value=payload.get("value"),
            strength=Strength(strength) if strength is not None else None,
            evidence=str(payload.get("evidence", "")),
            confidence=float(payload.get("confidence", 1.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"op": self.op.value}
        if self.field is not None:
            result["field"] = self.field
        if self.value is not None:
            result["value"] = self.value
        if self.strength is not None:
            result["strength"] = self.strength.value
        if self.evidence:
            result["evidence"] = self.evidence
        if self.confidence != 1.0:
            result["confidence"] = self.confidence
        return result


@dataclass(frozen=True)
class StatePatch:
    operations: tuple[PatchOperation, ...]
    source_turn: int
    raw_message: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.source_turn <= 10:
            raise ValueError("source_turn must be between 1 and 10")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StatePatch":
        return cls(
            operations=tuple(PatchOperation.from_dict(item) for item in payload.get("operations", [])),
            source_turn=int(payload["source_turn"]),
            raw_message=str(payload.get("raw_message", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_turn": self.source_turn,
            "operations": [operation.to_dict() for operation in self.operations],
        }
        if self.raw_message:
            result["raw_message"] = self.raw_message
        return result


@dataclass
class ConversationState:
    schema_version: str = SCHEMA_VERSION
    category: str | None = None
    hard_constraints: dict[str, Any] = field(default_factory=dict)
    soft_preferences: dict[str, Any] = field(default_factory=dict)
    excluded: dict[str, list[Any]] = field(default_factory=dict)
    no_preference: list[str] = field(default_factory=list)
    asked_attributes: list[str] = field(default_factory=list)
    turn: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConversationState":
        state = cls(
            schema_version=str(payload.get("schema_version", SCHEMA_VERSION)),
            category=payload.get("category"),
            hard_constraints=deepcopy(payload.get("hard_constraints", {})),
            soft_preferences=deepcopy(payload.get("soft_preferences", {})),
            excluded=deepcopy(payload.get("excluded", {})),
            no_preference=list(payload.get("no_preference", [])),
            asked_attributes=list(payload.get("asked_attributes", [])),
            turn=int(payload.get("turn", 0)),
        )
        if state.category is not None:
            state.category = str(canonicalize("category", state.category))
        _normalize_state_values(state)
        return state

    def to_dict(self, include_empty: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "category": self.category,
            "hard_constraints": deepcopy(self.hard_constraints),
            "soft_preferences": deepcopy(self.soft_preferences),
            "excluded": deepcopy(self.excluded),
            "no_preference": list(self.no_preference),
            "asked_attributes": list(self.asked_attributes),
            "turn": self.turn,
        }
        if include_empty:
            return result
        return {
            key: value
            for key, value in result.items()
            if key == "schema_version" or value not in (None, {}, [], 0, "")
        }


def _deduplicate(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _as_list(field_name: str, value: Any) -> list[Any]:
    values = value if isinstance(value, list) else [value]
    return _deduplicate(canonicalize(field_name, item) for item in values)


def _normalize_state_values(state: ConversationState) -> None:
    for container in (state.hard_constraints, state.soft_preferences):
        for field_name, value in list(container.items()):
            if field_name not in STATE_FIELDS or field_name == "category":
                raise ValueError(f"Unsupported constraint field: {field_name}")
            if field_name in MULTI_VALUE_FIELDS:
                container[field_name] = _as_list(field_name, value)
            else:
                container[field_name] = canonicalize(field_name, value)
    for field_name, values in list(state.excluded.items()):
        if field_name not in STATE_FIELDS or field_name in {"category", "price_min", "price_max"}:
            raise ValueError(f"Unsupported exclusion field: {field_name}")
        state.excluded[field_name] = _as_list(field_name, values)
    state.no_preference = _deduplicate(str(item) for item in state.no_preference)
    state.asked_attributes = _deduplicate(str(item) for item in state.asked_attributes)
    invalid_no_preference = set(state.no_preference) - ALLOWED_ASK_ATTRIBUTES
    invalid_asked = set(state.asked_attributes) - ALLOWED_ASK_ATTRIBUTES
    if invalid_no_preference:
        raise ValueError(f"Invalid no_preference attributes: {sorted(invalid_no_preference)}")
    if invalid_asked:
        raise ValueError(f"Invalid asked_attributes: {sorted(invalid_asked)}")


def _clear_field(state: ConversationState, field_name: str, include_excluded: bool = False) -> None:
    if field_name == "category":
        state.category = None
    else:
        state.hard_constraints.pop(field_name, None)
        state.soft_preferences.pop(field_name, None)
    if include_excluded:
        state.excluded.pop(field_name, None)


def _reset_product_scope(state: ConversationState) -> None:
    state.category = None
    for field_name in PRODUCT_SCOPED_FIELDS:
        state.hard_constraints.pop(field_name, None)
        state.soft_preferences.pop(field_name, None)
        state.excluded.pop(field_name, None)
    state.no_preference.clear()
    state.asked_attributes.clear()


def _set_values(
    state: ConversationState,
    field_name: str,
    value: Any,
    strength: Strength,
    append: bool,
) -> None:
    if field_name == "category":
        new_category = str(canonicalize(field_name, value))
        if state.category is not None and state.category != new_category:
            _reset_product_scope(state)
        state.category = new_category
        state.no_preference = [item for item in state.no_preference if item != ask_attribute_for(field_name)]
        return

    target = state.hard_constraints if strength == Strength.HARD else state.soft_preferences
    other = state.soft_preferences if strength == Strength.HARD else state.hard_constraints
    normalized = _as_list(field_name, value) if field_name in MULTI_VALUE_FIELDS else canonicalize(field_name, value)

    if append and field_name in MULTI_VALUE_FIELDS:
        existing = target.get(field_name, [])
        target[field_name] = _deduplicate([*existing, *normalized])
        if field_name in other:
            other[field_name] = [item for item in other[field_name] if item not in normalized]
            if not other[field_name]:
                other.pop(field_name)
    else:
        other.pop(field_name, None)
        target[field_name] = normalized
    state.no_preference = [item for item in state.no_preference if item != ask_attribute_for(field_name)]

    # A newer budget bound wins over an incompatible older opposite bound.
    price_min = state.hard_constraints.get("price_min", state.soft_preferences.get("price_min"))
    price_max = state.hard_constraints.get("price_max", state.soft_preferences.get("price_max"))
    if price_min is not None and price_max is not None and price_min > price_max:
        opposite = "price_max" if field_name == "price_min" else "price_min"
        state.hard_constraints.pop(opposite, None)
        state.soft_preferences.pop(opposite, None)


def _remove_value(state: ConversationState, field_name: str, value: Any) -> None:
    if field_name == "category":
        if state.category == canonicalize(field_name, value):
            state.category = None
        return
    normalized_values = _as_list(field_name, value)
    for container in (state.hard_constraints, state.soft_preferences):
        if field_name not in container:
            continue
        if field_name in MULTI_VALUE_FIELDS:
            container[field_name] = [item for item in container[field_name] if item not in normalized_values]
            if not container[field_name]:
                container.pop(field_name)
        elif container[field_name] in normalized_values:
            container.pop(field_name)


def apply_patch(state: ConversationState, patch: StatePatch) -> ConversationState:
    """Apply a patch without mutating the input state."""
    result = deepcopy(state)
    for operation in patch.operations:
        if operation.op == Operation.RESET_SCOPE:
            _reset_product_scope(result)
            continue

        assert operation.field is not None
        field_name = operation.field
        if operation.op in {Operation.SET, Operation.REPLACE}:
            assert operation.strength is not None
            _set_values(result, field_name, operation.value, operation.strength, append=False)
        elif operation.op == Operation.ADD:
            if field_name in SINGLE_VALUE_FIELDS:
                raise ValueError(f"add is invalid for single-value field {field_name}")
            assert operation.strength is not None
            _set_values(result, field_name, operation.value, operation.strength, append=True)
        elif operation.op == Operation.REMOVE:
            _remove_value(result, field_name, operation.value)
        elif operation.op == Operation.CLEAR:
            _clear_field(result, field_name)
            result.no_preference = [
                item for item in result.no_preference if item != ask_attribute_for(field_name)
            ]
        elif operation.op == Operation.EXCLUDE:
            if field_name in {"category", "price_min", "price_max"}:
                raise ValueError(f"exclude is invalid for {field_name}")
            values = _as_list(field_name, operation.value)
            result.excluded[field_name] = _deduplicate([*result.excluded.get(field_name, []), *values])
            for value in values:
                _remove_value(result, field_name, value)
        elif operation.op == Operation.ALLOW:
            values = _as_list(field_name, operation.value)
            remaining = [item for item in result.excluded.get(field_name, []) if item not in values]
            if remaining:
                result.excluded[field_name] = remaining
            else:
                result.excluded.pop(field_name, None)
        elif operation.op == Operation.SET_NO_PREFERENCE:
            ask_attribute = ask_attribute_for(field_name)
            if ask_attribute == "budget":
                _clear_field(result, "price_min")
                _clear_field(result, "price_max")
            else:
                _clear_field(result, field_name)
            if ask_attribute not in result.no_preference:
                result.no_preference.append(ask_attribute)
        else:  # pragma: no cover - exhaustive guard for future enum additions
            raise ValueError(f"Unsupported operation: {operation.op}")

    result.turn = patch.source_turn
    result.schema_version = SCHEMA_VERSION
    _normalize_state_values(result)
    return result
