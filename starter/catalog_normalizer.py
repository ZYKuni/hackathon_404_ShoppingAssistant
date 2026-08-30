"""Compact, deterministic normalization of the frozen product catalog.

This module is deliberately independent from the public pipeline contracts: its
types are an implementation detail shared by Aaron's matcher and ranker.  It
never mutates source records and performs no network or model calls.
"""

from __future__ import annotations

import json
import math
import re
import time
import tracemalloc
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .attribute_lexicons import (
    AUDIENCE_ALIASES,
    CATEGORY_ALIASES,
    COLOR_ALIASES,
    FEATURE_ALIASES,
    MATERIAL_ALIASES,
    USE_CASE_ALIASES,
    canonicalize,
    normalize_phrase,
)


DETAIL_CONFIDENCE = 0.95
CATEGORY_CONFIDENCE = 0.85
STORE_CONFIDENCE = 0.85
FEATURE_CONFIDENCE = 0.75
TITLE_CONFIDENCE = 0.65
DESCRIPTION_CONFIDENCE = 0.50


@dataclass(frozen=True)
class ExtractedValue:
    value: str | float
    source: str
    confidence: float

    def __post_init__(self) -> None:
        if isinstance(self.value, str):
            if not self.value:
                raise ValueError("value must not be empty")
        elif isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("value must be a string or number")
        elif not math.isfinite(float(self.value)):
            raise ValueError("numeric value must be finite")
        if not self.source:
            raise ValueError("source must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class NormalizedProduct:
    parent_asin: str
    category_path: tuple[str, ...]
    leaf_categories: tuple[ExtractedValue, ...]
    audiences: tuple[ExtractedValue, ...]
    materials: tuple[ExtractedValue, ...]
    colors: tuple[ExtractedValue, ...]
    brands: tuple[ExtractedValue, ...]
    sizes: tuple[ExtractedValue, ...]
    styles: tuple[ExtractedValue, ...]
    price: ExtractedValue | None
    features: tuple[str, ...]
    average_rating: float
    rating_number: int

    def __post_init__(self) -> None:
        if not self.parent_asin:
            raise ValueError("parent_asin must not be empty")
        if not 0.0 <= self.average_rating <= 5.0:
            raise ValueError("average_rating must be between 0 and 5")
        if self.rating_number < 0:
            raise ValueError("rating_number must be non-negative")


@dataclass(frozen=True)
class CatalogNormalizationStats:
    product_count: int
    elapsed_ms: float
    peak_memory_mb: float


def _detail(details: Mapping[str, object], *names: str) -> object | None:
    wanted = {normalize_phrase(name) for name in names}
    for key, value in details.items():
        if normalize_phrase(key) in wanted and value not in (None, ""):
            return value
    return None


def _canonical_open(field: str, value: object) -> str:
    result = canonicalize(field, value)
    return str(result).strip()


def _add(
    values: dict[str, ExtractedValue], field: str, raw: object,
    source: str, confidence: float,
) -> None:
    if raw is None:
        return
    canonical = _canonical_open(field, raw)
    if not canonical:
        return
    candidate = ExtractedValue(canonical, source, confidence)
    current = values.get(canonical)
    if current is None or candidate.confidence > current.confidence:
        values[canonical] = candidate


@lru_cache(maxsize=None)
def _alias_matcher(
    entries: tuple[tuple[str, str], ...],
) -> tuple[re.Pattern[str], Mapping[str, str]]:
    """Compile one longest-first matcher instead of scanning once per alias."""
    canonical_by_phrase: dict[str, str] = {}
    alternatives: list[str] = []
    for alias, canonical in sorted(entries, key=lambda item: len(item[0]), reverse=True):
        normalized = normalize_phrase(alias)
        if not normalized or normalized in canonical_by_phrase:
            continue
        canonical_by_phrase[normalized] = canonical
        words = [re.escape(word) for word in normalized.split()]
        alternatives.append(r"[\s_-]+".join(words))
    if not alternatives:
        return re.compile(r"(?!x)x"), canonical_by_phrase
    pattern = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])",
        re.I,
    )
    return pattern, canonical_by_phrase


def _known_values(texts: Iterable[object], aliases: Mapping[str, str]) -> set[str]:
    joined = " \n ".join(str(text) for text in texts if text)
    if not joined:
        return set()
    matcher, canonical_by_phrase = _alias_matcher(tuple(aliases.items()))
    found = {
        canonical_by_phrase[normalize_phrase(match.group(0))]
        for match in matcher.finditer(joined)
    }
    if "faux_leather" in found:
        # A compound phrase is evidence for faux leather, not genuine leather.
        without_compounds = re.sub(
            r"(?i)\b(?:faux|synthetic)\s+leather\b", " ", joined
        )
        if not re.search(r"(?i)\b(?:genuine\s+)?leather\b", without_compounds):
            found.discard("leather")
    return found


def _parse_price(raw: object) -> ExtractedValue | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
        if math.isfinite(number) and number >= 0:
            return ExtractedValue(number, "price:number", 0.95)
        return None
    text = str(raw).strip()
    if not text or re.match(r"(?i)^\s*from\b", text):
        return None
    match = re.fullmatch(r"\s*\$?\s*(\d+(?:\.\d{1,2})?)\s*", text)
    if not match:
        return None
    number = float(match.group(1))
    return ExtractedValue(number, "price:string", 0.90)


def _number(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, bool):
        return default
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def normalize_product(product: Mapping[str, object]) -> NormalizedProduct:
    """Return a normalized copy of one catalog row without modifying ``product``."""
    asin = str(product.get("parent_asin") or "").strip()
    if not asin:
        raise ValueError("catalog product is missing parent_asin")

    raw_categories = product.get("categories") or ()
    categories = tuple(str(value) for value in raw_categories) if isinstance(
        raw_categories, (list, tuple)
    ) else (str(raw_categories),)
    category_path = tuple(
        _canonical_open("category", value) for value in categories if normalize_phrase(value)
    )
    leaf: dict[str, ExtractedValue] = {}
    if categories:
        _add(leaf, "category", categories[-1], "categories:leaf", CATEGORY_CONFIDENCE)
    else:
        title_categories = _known_values((product.get("title") or "",), CATEGORY_ALIASES)
        for value in title_categories:
            _add(leaf, "category", value, "title", TITLE_CONFIDENCE)

    details_obj = product.get("details") or {}
    details = details_obj if isinstance(details_obj, Mapping) else {}
    title = str(product.get("title") or "")
    features_raw = product.get("features") or ()
    raw_features = tuple(str(v) for v in features_raw) if isinstance(
        features_raw, (list, tuple)
    ) else (str(features_raw),)
    descriptions_raw = product.get("description") or ()
    descriptions = tuple(str(v) for v in descriptions_raw) if isinstance(
        descriptions_raw, (list, tuple)
    ) else (str(descriptions_raw),)

    audiences: dict[str, ExtractedValue] = {}
    materials: dict[str, ExtractedValue] = {}
    colors: dict[str, ExtractedValue] = {}
    brands: dict[str, ExtractedValue] = {}
    sizes: dict[str, ExtractedValue] = {}
    styles: dict[str, ExtractedValue] = {}

    # Explicit structured fields have priority and preserve unknown open values.
    for target, field, names in (
        (materials, "material", ("Material", "Fabric Type")),
        (colors, "color", ("Color",)),
        (audiences, "audience", ("Department",)),
        (brands, "brand", ("Brand",)),
        (sizes, "size", ("Size",)),
        (styles, "style", ("Style",)),
    ):
        value = _detail(details, *names)
        if value is not None:
            _add(target, field, value, f"details:{names[0]}", DETAIL_CONFIDENCE)
            component_aliases = {
                "material": MATERIAL_ALIASES,
                "color": COLOR_ALIASES,
                "audience": AUDIENCE_ALIASES,
            }.get(field)
            if component_aliases is not None:
                for component in _known_values((value,), component_aliases):
                    _add(
                        target, field, component,
                        f"details:{names[0]}", DETAIL_CONFIDENCE,
                    )

    # Category paths are strong audience evidence.
    for value in categories:
        normalized = normalize_phrase(value)
        if normalized in AUDIENCE_ALIASES:
            _add(audiences, "audience", value, "categories:path", CATEGORY_CONFIDENCE)

    store = product.get("store") or _detail(details, "Manufacturer")
    if store:
        _add(brands, "brand", store, "store", STORE_CONFIDENCE)

    sources = (
        (raw_features, "features", FEATURE_CONFIDENCE),
        ((title,), "title", TITLE_CONFIDENCE),
        (descriptions, "description", DESCRIPTION_CONFIDENCE),
    )
    for texts, source, confidence in sources:
        for value in _known_values(texts, MATERIAL_ALIASES):
            _add(materials, "material", value, source, confidence)
        for value in _known_values(texts, COLOR_ALIASES):
            _add(colors, "color", value, source, confidence)
        for value in _known_values(texts, AUDIENCE_ALIASES):
            _add(audiences, "audience", value, source, confidence)

    # Preserve normalized source feature text for open-vocabulary ranking.  Known
    # aliases are added as compact canonical tags alongside it.
    normalized_features: set[str] = {
        text for value in raw_features if (text := normalize_phrase(value))
    }
    for texts, _source, _confidence in sources:
        normalized_features.update(_known_values(texts, FEATURE_ALIASES))
        normalized_features.update(_known_values(texts, USE_CASE_ALIASES))

    def ordered(values: Mapping[str, ExtractedValue]) -> tuple[ExtractedValue, ...]:
        return tuple(sorted(values.values(), key=lambda item: (-item.confidence, str(item.value))))

    rating = min(5.0, max(0.0, _number(product.get("average_rating"))))
    rating_number = max(0, int(_number(product.get("rating_number"))))
    return NormalizedProduct(
        parent_asin=asin,
        category_path=category_path,
        leaf_categories=ordered(leaf),
        audiences=ordered(audiences),
        materials=ordered(materials),
        colors=ordered(colors),
        brands=ordered(brands),
        sizes=ordered(sizes),
        styles=ordered(styles),
        price=_parse_price(product.get("price")),
        features=tuple(sorted(normalized_features)),
        average_rating=rating,
        rating_number=rating_number,
    )


def iter_catalog(path: str | Path) -> Iterator[Mapping[str, object]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"catalog line {line_number} is not an object")
            yield value


class CatalogNormalizer:
    """Build and own the compact ASIN-to-normalized-product index."""

    def __init__(self, products: Iterable[Mapping[str, object]]) -> None:
        index: dict[str, NormalizedProduct] = {}
        for raw in products:
            normalized = normalize_product(raw)
            if normalized.parent_asin in index:
                raise ValueError(f"duplicate parent_asin: {normalized.parent_asin}")
            index[normalized.parent_asin] = normalized
        self._index = index

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "CatalogNormalizer":
        return cls(iter_catalog(path))

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, parent_asin: str) -> bool:
        return parent_asin in self._index

    def get(self, parent_asin: str) -> NormalizedProduct | None:
        return self._index.get(parent_asin)

    @property
    def asins(self) -> tuple[str, ...]:
        return tuple(self._index)


def benchmark_catalog(path: str | Path) -> tuple[CatalogNormalizer, CatalogNormalizationStats]:
    """Build the full index and report wall time plus Python peak allocations."""
    tracemalloc.start()
    started = time.perf_counter()
    try:
        normalizer = CatalogNormalizer.from_jsonl(path)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return normalizer, CatalogNormalizationStats(
        product_count=len(normalizer),
        elapsed_ms=elapsed_ms,
        peak_memory_mb=peak / (1024 * 1024),
    )
