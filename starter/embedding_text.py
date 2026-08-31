"""Deterministic text schemas for offline product and query embeddings."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping

from .pipeline_contracts import SearchRequest


TEXT_SCHEMA_VERSION = "product-query-text-v1"
HTML_TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DETAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("material", "material"),
    ("fabric type", "fabric"),
    ("color", "color"),
    ("department", "department"),
    ("style", "style"),
    ("fit type", "fit"),
    ("sole material", "sole"),
    ("outer material", "outer material"),
    ("closure type", "closure"),
    ("neck style", "neck"),
    ("sleeve type", "sleeve"),
    ("occasion", "occasion"),
)


def _clean(value: object, *, limit: int | None = None) -> str:
    text = html.unescape(str(value or ""))
    text = HTML_TAG_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip(" ,.;|")
    if limit is not None:
        text = text[:limit].rstrip()
    return text


def _items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _category_values(value: object) -> tuple[str, ...]:
    categories: list[str] = []
    for item in _items(value):
        categories.extend(_clean(part) for part in str(item).split(","))
    return _unique(categories)[-4:]


def _details(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    normalized = {_clean(key).lower(): item for key, item in value.items()}
    result: list[str] = []
    for source_key, label in DETAIL_FIELDS:
        if source_key not in normalized:
            continue
        detail_values = _unique(
            _clean(item, limit=120) for item in _items(normalized[source_key])
        )
        if detail_values:
            result.append(f"{label} {'; '.join(detail_values)}")
    return tuple(result[:8])


def build_product_embedding_text(product: Mapping[str, object]) -> str:
    """Build one compact, priority-ordered product document."""
    parts: list[str] = []
    title = _clean(product.get("title"), limit=200)
    if title:
        parts.append(f"Product: {title}.")

    categories = _category_values(product.get("categories"))
    if categories:
        parts.append(f"Category: {' > '.join(categories)}.")

    features = _unique(
        _clean(item, limit=160) for item in _items(product.get("features"))
    )[:6]
    if features:
        parts.append(f"Features: {'; '.join(features)}.")

    details = _details(product.get("details"))
    if details:
        parts.append(f"Details: {'; '.join(details)}.")

    description = " ".join(
        _clean(item) for item in _items(product.get("description")) if _clean(item)
    )
    description = _clean(description, limit=350)
    if description:
        parts.append(f"Description: {description}.")
    return " ".join(parts)


def _term_values(terms, *, field: str | None = None) -> tuple[str, ...]:
    values: list[str] = []
    for term in terms:
        if field is not None and term.field != field:
            continue
        if term.field in {"price_min", "price_max", "size"}:
            continue
        values.extend(_clean(value).replace("_", " ") for value in term.values)
    return _unique(values)


def _redact_excluded(text: str, request: SearchRequest) -> str:
    result = text
    excluded = _term_values(request.state.excluded)
    for value in sorted(excluded, key=len, reverse=True):
        if not value:
            continue
        result = re.sub(
            rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])",
            " ",
            result,
            flags=re.IGNORECASE,
        )
    return _clean(result)


def build_query_embedding_text(request: SearchRequest) -> str:
    """Build positive Browsing evidence without hard numeric/negative terms."""
    parts: list[str] = []
    if request.state.category:
        parts.append(f"Looking for {request.state.category.replace('_', ' ')}.")

    use_cases = _term_values(request.state.soft_preferences, field="use_case")
    if use_cases:
        parts.append(f"Use: {'; '.join(use_cases)}.")

    preferences = _term_values(request.state.soft_preferences)
    preferences = tuple(value for value in preferences if value not in use_cases)
    if preferences:
        parts.append(f"Preferences: {'; '.join(preferences)}.")

    raw = _redact_excluded(
        request.raw_context.strip() or request.current_message,
        request,
    )
    if raw:
        parts.append(f"Request: {_clean(raw, limit=500)}.")
    return " ".join(parts)


__all__ = [
    "DETAIL_FIELDS",
    "TEXT_SCHEMA_VERSION",
    "build_product_embedding_text",
    "build_query_embedding_text",
]
