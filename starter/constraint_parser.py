"""Deterministic MVP parser from customer language to ``StatePatch``.

The parser intentionally covers a small, high-confidence English grammar that
matches the public simulator and the team's golden cases.  It never mutates a
``ConversationState`` directly; all updates go through the reducer in
``conversation_state.py``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from starter.attribute_lexicons import (
    AUDIENCE_ALIASES,
    CATEGORY_ALIASES,
    COLOR_ALIASES,
    FEATURE_ALIASES,
    MATERIAL_ALIASES,
    USE_CASE_ALIASES,
    canonicalize,
    normalize_phrase,
)
from starter.conversation_state import (
    ConversationState,
    Operation,
    PatchOperation,
    StatePatch,
    Strength,
)


RESET_RE = re.compile(r"\b(?:let(?:'s| us) start over|start over|start again|reset everything)\b", re.I)
OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead(?: of)?|forget\b|make that|on second thought|no longer|now)\b",
    re.I,
)
HARD_RE = re.compile(r"\b(?:must|required|only|need)\b", re.I)
REMOVE_PREFIX_RE = re.compile(r"\b(?:no longer want|remove|drop)\s+$", re.I)
NEGATIVE_PREFIX_RE = re.compile(
    r"(?:\banything\s+but|\bdefinitely\s+not|\bjust\s+not|\bnot|\bdon['’]?t\s+want|"
    r"\bavoid|\bwithout|\bno)\s+$",
    re.I,
)
NO_PREFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:i\s+)?don['’]?t\s+(?:have\s+(?:an?\s+)?(?:additional\s+)?preference\s+for|care\s+about)\s+(?:the\s+)?material\b", re.I), "material"),
    (re.compile(r"\b(?:i\s+)?don['’]?t\s+(?:have\s+(?:an?\s+)?(?:additional\s+)?preference\s+for|care\s+about)\s+(?:the\s+)?color\b", re.I), "color"),
    (re.compile(r"\bany\s+color\s+is\s+fine\b", re.I), "color"),
    (re.compile(r"\b(?:i\s+)?don['’]?t\s+(?:have\s+(?:an?\s+)?(?:additional\s+)?preference\s+for|care\s+about)\s+(?:the\s+)?brand\b", re.I), "brand"),
    (re.compile(r"\b(?:i\s+)?don['’]?t\s+(?:have\s+(?:an?\s+)?(?:additional\s+)?preference\s+for|care\s+about)\s+(?:the\s+)?size\b", re.I), "size"),
    (re.compile(r"\b(?:budget|price)\s+(?:doesn['’]?t\s+matter|is\s+flexible)\b", re.I), "price_max"),
)


@dataclass(frozen=True)
class AliasMatch:
    raw_value: str
    canonical_value: str
    start: int
    end: int


def _normalized_text(message: str) -> str:
    text = unicodedata.normalize("NFKC", message).lower()
    text = text.replace("’", "'").replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_phrase(phrase)).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.I)


def _find_aliases(text: str, field: str, aliases: Mapping[str, str]) -> list[AliasMatch]:
    """Find longest, non-overlapping aliases in source order."""
    candidates: list[AliasMatch] = []
    for phrase in sorted(aliases, key=lambda item: (-len(normalize_phrase(item)), item)):
        canonical = str(canonicalize(field, phrase))
        for match in _phrase_pattern(phrase).finditer(text):
            candidates.append(AliasMatch(phrase, canonical, match.start(), match.end()))

    result: list[AliasMatch] = []
    occupied: list[tuple[int, int]] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, -(item.end - item.start))):
        if any(candidate.start < end and start < candidate.end for start, end in occupied):
            continue
        result.append(candidate)
        occupied.append((candidate.start, candidate.end))
    return sorted(result, key=lambda item: item.start)


def _overlaps(match: AliasMatch, spans: list[tuple[int, int]]) -> bool:
    return any(match.start < end and start < match.end for start, end in spans)


def _is_negated(text: str, match: AliasMatch) -> bool:
    prefix = text[max(0, match.start - 32):match.start]
    return bool(NEGATIVE_PREFIX_RE.search(prefix))


def _is_removed(text: str, match: AliasMatch) -> bool:
    prefix = text[max(0, match.start - 28):match.start]
    return bool(REMOVE_PREFIX_RE.search(prefix))


def _is_allowed_again(text: str, match: AliasMatch) -> bool:
    suffix = text[match.end:match.end + 28]
    return bool(re.match(r"\s+is\s+(?:okay|ok|fine|acceptable)\s+now\b", suffix, re.I))


def _strength_near(text: str, match: AliasMatch, default: Strength) -> Strength:
    context = text[max(0, match.start - 28):min(len(text), match.end + 28)]
    return Strength.HARD if HARD_RE.search(context) else default


def _operation_for_existing_single(state: ConversationState, field: str, text: str) -> Operation:
    exists = field in state.hard_constraints or field in state.soft_preferences
    return Operation.REPLACE if exists and OVERRIDE_RE.search(text) else Operation.SET


def _add_operation(
    operations: list[PatchOperation],
    op: Operation,
    field: str,
    value: object | None = None,
    strength: Strength | None = None,
    evidence: str = "",
) -> None:
    candidate = PatchOperation(
        op=op,
        field=field,
        value=value,
        strength=strength,
        evidence=evidence,
    )
    signature = (candidate.op, candidate.field, repr(candidate.value), candidate.strength)
    existing = {(item.op, item.field, repr(item.value), item.strength) for item in operations}
    if signature not in existing:
        operations.append(candidate)


def _parse_no_preference(text: str, operations: list[PatchOperation]) -> None:
    for pattern, field in NO_PREFERENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            _add_operation(
                operations,
                Operation.SET_NO_PREFERENCE,
                field,
                evidence=match.group(0),
            )


def _parse_budget(text: str, state: ConversationState, operations: list[PatchOperation]) -> None:
    number = r"\$?\s*([0-9]+(?:\.[0-9]+)?)"
    between = re.search(rf"\bbetween\s+{number}\s+and\s+{number}", text, re.I)
    if between:
        _add_operation(
            operations, Operation.SET, "price_min", float(between.group(1)), Strength.HARD, between.group(0)
        )
        _add_operation(
            operations, Operation.SET, "price_max", float(between.group(2)), Strength.HARD, between.group(0)
        )
        return

    maximum = re.search(
        rf"\b(?:under|below|less\s+than|up\s+to|no\s+more\s+than)\s+{number}", text, re.I
    )
    if maximum:
        op = _operation_for_existing_single(state, "price_max", text)
        _add_operation(
            operations, op, "price_max", float(maximum.group(1)), Strength.HARD, maximum.group(0)
        )
        return

    minimum = re.search(rf"\b(?:at\s+least|over|more\s+than)\s+{number}", text, re.I)
    if minimum:
        op = _operation_for_existing_single(state, "price_min", text)
        _add_operation(
            operations, op, "price_min", float(minimum.group(1)), Strength.HARD, minimum.group(0)
        )
        return

    budget_to = re.search(
        rf"\b(?:increase|raise|change|set|make)?\s*(?:my\s+)?budget\s+(?:is\s+)?(?:now\s+)?to\s+{number}",
        text,
        re.I,
    )
    if budget_to:
        op = _operation_for_existing_single(state, "price_max", text)
        _add_operation(
            operations, op, "price_max", float(budget_to.group(1)), Strength.HARD, budget_to.group(0)
        )


def _parse_brand(
    text: str,
    state: ConversationState,
    category_matches: list[AliasMatch],
    operations: list[PatchOperation],
) -> None:
    make_that = re.search(r"\bmake\s+that\s+([a-z0-9&'.]+(?:\s+[a-z0-9&'.]+){0,2})", text, re.I)
    if make_that:
        value = make_that.group(1).strip(" .")
        _add_operation(operations, Operation.REPLACE, "brand", value, Strength.SOFT, make_that.group(0))
        return

    if not category_matches:
        return
    category_start = category_matches[0].start
    prefix = text[:category_start]
    preferred = re.search(r"\bprefer(?:red|ably)?\s+([a-z0-9&'.]+)\s*$", prefix, re.I)
    if preferred:
        value = preferred.group(1)
        op = Operation.REPLACE if "brand" in state.soft_preferences and OVERRIDE_RE.search(text) else Operation.ADD
        _add_operation(operations, op, "brand", value, Strength.SOFT, preferred.group(0))


def _parse_size(text: str, operations: list[PatchOperation]) -> None:
    size = re.search(
        r"\bsize\s+((?:[0-9]+(?:\.[0-9]+)?)(?:\s+(?:wide|narrow))?|(?:xs|s|m|l|xl|xxl))\b",
        text,
        re.I,
    )
    if size:
        _add_operation(operations, Operation.ADD, "size", size.group(1), Strength.HARD, size.group(0))


def _parse_style(text: str, operations: list[PatchOperation]) -> None:
    style = re.search(r"\b(casual|formal|vintage|sporty|classic)\s+style\b", text, re.I)
    if style:
        _add_operation(operations, Operation.ADD, "style", style.group(1), Strength.SOFT, style.group(0))


def _parse_alias_field(
    text: str,
    field: str,
    matches: list[AliasMatch],
    state: ConversationState,
    operations: list[PatchOperation],
    default_strength: Strength,
    skip_spans: list[tuple[int, int]] | None = None,
) -> None:
    skip_spans = skip_spans or []
    usable = [match for match in matches if not _overlaps(match, skip_spans)]
    if not usable:
        return

    # "Blue instead of black" is a complete replacement, not add + remove.
    instead = re.search(r"\binstead\s+of\b", text, re.I)
    if instead:
        before = [match for match in usable if match.end <= instead.start()]
        after = [match for match in usable if match.start >= instead.end()]
        if before and after:
            chosen = before[-1]
            _add_operation(
                operations,
                Operation.REPLACE,
                field,
                chosen.raw_value,
                _strength_near(text, chosen, default_strength),
                text[chosen.start:after[0].end],
            )
            return

    for match in usable:
        if _is_allowed_again(text, match) and match.canonical_value in state.excluded.get(field, []):
            _add_operation(operations, Operation.ALLOW, field, match.raw_value, evidence=text[match.start:match.end + 16])
            continue
        if _is_removed(text, match):
            _add_operation(operations, Operation.REMOVE, field, match.raw_value, evidence=text[max(0, match.start - 20):match.end])
            continue
        if _is_negated(text, match):
            _add_operation(operations, Operation.EXCLUDE, field, match.raw_value, evidence=text[max(0, match.start - 20):match.end])
            continue

        strength = _strength_near(text, match, default_strength)
        if strength == Strength.HARD and match.canonical_value in state.soft_preferences.get(field, []):
            op = Operation.SET
        elif OVERRIDE_RE.search(text) and field == "audience" and field in state.hard_constraints:
            op = Operation.REPLACE
        else:
            op = Operation.ADD
        _add_operation(operations, op, field, match.raw_value, strength, text[match.start:match.end])


def parse_message(user_message: str, state: ConversationState, turn: int) -> StatePatch:
    """Parse one English customer turn into a deterministic state patch."""
    if not isinstance(user_message, str):
        raise TypeError("user_message must be a string")
    if not isinstance(state, ConversationState):
        raise TypeError("state must be a ConversationState")
    if not 1 <= turn <= 10:
        raise ValueError("turn must be between 1 and 10")

    text = _normalized_text(user_message)
    operations: list[PatchOperation] = []
    if RESET_RE.search(text):
        return StatePatch(
            operations=(PatchOperation(op=Operation.RESET_SCOPE, field="product_constraints", evidence=user_message),),
            source_turn=turn,
            raw_message=user_message,
        )

    category_matches = _find_aliases(text, "category", CATEGORY_ALIASES)
    category_spans = [(match.start, match.end) for match in category_matches]
    if category_matches:
        # Longest non-overlapping matching leaves at most one meaningful product
        # category in the supported MVP grammar; the final mention wins.
        category = category_matches[-1]
        op = Operation.REPLACE if state.category and OVERRIDE_RE.search(text) else Operation.SET
        _add_operation(operations, op, "category", category.raw_value, Strength.HARD, text[category.start:category.end])

    # Category changes reset product-scoped state.  Restated constraints from the
    # same turn must therefore be emitted after the category operation.
    _parse_no_preference(text, operations)
    _parse_budget(text, state, operations)

    audience_matches = _find_aliases(text, "audience", AUDIENCE_ALIASES)
    _parse_alias_field(
        text, "audience", audience_matches, state, operations, Strength.HARD, skip_spans=category_spans
    )

    color_matches = _find_aliases(text, "color", COLOR_ALIASES)
    _parse_alias_field(text, "color", color_matches, state, operations, Strength.SOFT)

    material_matches = _find_aliases(text, "material", MATERIAL_ALIASES)
    _parse_alias_field(text, "material", material_matches, state, operations, Strength.SOFT)

    feature_matches = _find_aliases(text, "feature", FEATURE_ALIASES)
    _parse_alias_field(text, "feature", feature_matches, state, operations, Strength.SOFT)

    use_case_matches = _find_aliases(text, "use_case", USE_CASE_ALIASES)
    use_case_matches = [
        match
        for match in use_case_matches
        if match.canonical_value != "work"
        or re.search(r"\b(?:for|at)\s+$", text[max(0, match.start - 12):match.start], re.I)
    ]
    _parse_alias_field(
        text, "use_case", use_case_matches, state, operations, Strength.SOFT, skip_spans=category_spans
    )

    _parse_brand(text, state, category_matches, operations)
    _parse_size(text, operations)
    _parse_style(text, operations)

    return StatePatch(operations=tuple(operations), source_turn=turn, raw_message=user_message)
