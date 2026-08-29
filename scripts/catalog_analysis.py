"""Profile the frozen catalog and create a deterministic manual-audit sample.

The script intentionally uses only the Python standard library.  It turns the
business-side catalog review into reproducible artifacts that can be refreshed
whenever normalization rules change.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from starter.attribute_lexicons import (
    CATEGORY_ALIASES,
    COLOR_ALIASES,
    FEATURE_ALIASES,
    MATERIAL_ALIASES,
    USE_CASE_ALIASES,
    normalize_phrase,
)


PROFILE_FIELDS = (
    "title",
    "features",
    "description",
    "price",
    "categories",
    "details",
    "average_rating",
    "rating_number",
    "store",
)

LEXICONS = {
    "category": CATEGORY_ALIASES,
    "color": COLOR_ALIASES,
    "material": MATERIAL_ALIASES,
    "use_case": USE_CASE_ALIASES,
    "feature": FEATURE_ALIASES,
}

AUDIT_COLUMNS = (
    "parent_asin",
    "title",
    "raw_leaf_category",
    "raw_category_path",
    "raw_price",
    "raw_store",
    "details_department",
    "details_color",
    "details_material",
    "details_brand",
    "details_size",
    "features_excerpt",
    "description_excerpt",
    "review_category",
    "review_audience",
    "review_color",
    "review_material",
    "review_brand",
    "review_size",
    "review_use_case",
    "review_feature",
    "review_data_issues",
    "review_notes",
)


def _is_missing(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _flatten(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [f"{key} {item}" for key, item in value.items()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def searchable_text(product: dict[str, Any]) -> str:
    parts: list[str] = []
    for field_name in ("title", "features", "description", "categories", "details", "store"):
        parts.extend(_flatten(product.get(field_name)))
    return f" {normalize_phrase(' '.join(parts))} "


def _contains_alias(text: str, aliases: Iterable[str]) -> bool:
    """Match normalized phrases without treating substrings as full values."""
    for alias in aliases:
        normalized = normalize_phrase(alias)
        if normalized and f" {normalized} " in text:
            return True
    return False


def _ranked(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def analyze_catalog(catalog_path: str | Path, top_n: int = 20) -> dict[str, Any]:
    """Return a deterministic catalog profile without modifying the source data."""
    path = Path(catalog_path)
    field_present = Counter()
    field_missing = Counter()
    field_types = {field_name: Counter() for field_name in PROFILE_FIELDS}
    identifiers: set[str] = set()
    duplicate_identifiers = 0
    numeric_prices: list[float] = []
    text_prices = Counter()
    leaf_categories = Counter()
    detail_keys = Counter()
    stores = Counter()
    lexicon_hits = Counter()
    row_count = 0

    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            product = json.loads(raw_line)
            row_count += 1
            parent_asin = str(product.get("parent_asin", "")).strip()
            if parent_asin in identifiers:
                duplicate_identifiers += 1
            identifiers.add(parent_asin)

            for field_name in PROFILE_FIELDS:
                value = product.get(field_name)
                field_types[field_name][_json_type(value)] += 1
                if _is_missing(value):
                    field_missing[field_name] += 1
                else:
                    field_present[field_name] += 1

            price = product.get("price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                numeric_prices.append(float(price))
            elif price not in (None, ""):
                text_prices[str(price)] += 1

            categories = product.get("categories") or []
            if categories:
                leaf_categories[str(categories[-1])] += 1
            for key in (product.get("details") or {}):
                detail_keys[str(key)] += 1
            if product.get("store"):
                stores[str(product["store"])] += 1

            normalized_text = searchable_text(product)
            for lexicon_name, mapping in LEXICONS.items():
                if _contains_alias(normalized_text, mapping):
                    lexicon_hits[lexicon_name] += 1

    field_stats = {}
    for field_name in PROFILE_FIELDS:
        missing = field_missing[field_name]
        field_stats[field_name] = {
            "present": field_present[field_name],
            "missing_or_empty": missing,
            "missing_rate": round(missing / row_count, 6) if row_count else 0.0,
            "type_counts": dict(sorted(field_types[field_name].items())),
        }

    price_stats: dict[str, Any] = {
        "numeric_count": len(numeric_prices),
        "text_count": sum(text_prices.values()),
        "text_examples": _ranked(text_prices, 10),
    }
    if numeric_prices:
        price_stats.update({
            "minimum": min(numeric_prices),
            "median": statistics.median(numeric_prices),
            "mean": round(statistics.fmean(numeric_prices), 6),
            "maximum": max(numeric_prices),
        })

    return {
        "source": path.as_posix(),
        "row_count": row_count,
        "unique_parent_asin_count": len(identifiers),
        "duplicate_parent_asin_count": duplicate_identifiers,
        "field_stats": field_stats,
        "price_stats": price_stats,
        "top_leaf_categories": _ranked(leaf_categories, top_n),
        "top_detail_keys": _ranked(detail_keys, top_n),
        "top_stores": _ranked(stores, top_n),
        "lexicon_coverage": {
            name: {
                "matched_products": lexicon_hits[name],
                "coverage_rate": round(lexicon_hits[name] / row_count, 6) if row_count else 0.0,
            }
            for name in LEXICONS
        },
    }


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_markdown(profile: dict[str, Any]) -> str:
    """Render the machine-readable profile as a short team-facing report."""
    rows = profile["row_count"]
    lines = [
        "# Catalog Data Profile",
        "",
        "> Generated by `python -m scripts.catalog_analysis`. Do not edit the measured values by hand.",
        "",
        "## Dataset identity",
        "",
        f"- Products: **{rows:,}**",
        f"- Unique `parent_asin`: **{profile['unique_parent_asin_count']:,}**",
        f"- Duplicate identifiers: **{profile['duplicate_parent_asin_count']:,}**",
        "",
        "## Field completeness",
        "",
        "| Field | Present | Missing / empty | Missing rate |",
        "|---|---:|---:|---:|",
    ]
    for field_name, stats in profile["field_stats"].items():
        lines.append(
            f"| `{field_name}` | {stats['present']:,} | {stats['missing_or_empty']:,} | "
            f"{_percent(stats['missing_rate'])} |"
        )

    price = profile["price_stats"]
    lines.extend([
        "",
        "## Price quality",
        "",
        f"- Numeric prices: **{price['numeric_count']:,}** "
        f"({_percent(price['numeric_count'] / rows if rows else 0.0)})",
        f"- Non-empty text prices: **{price['text_count']:,}**",
    ])
    if price.get("numeric_count"):
        lines.append(
            f"- Numeric range: **{price['minimum']:.2f}–{price['maximum']:.2f}**; "
            f"median **{price['median']:.2f}**, mean **{price['mean']:.2f}**"
        )

    lines.extend([
        "",
        "## Current shared-lexicon coverage",
        "",
        "Coverage means at least one current alias appears somewhere in the searchable product text. "
        "It is a recall diagnostic, not a guarantee that the extracted value is correct.",
        "",
        "| Attribute | Matched products | Coverage |",
        "|---|---:|---:|",
    ])
    for name, stats in profile["lexicon_coverage"].items():
        lines.append(
            f"| `{name}` | {stats['matched_products']:,} | {_percent(stats['coverage_rate'])} |"
        )

    def add_ranked(title: str, key: str) -> None:
        lines.extend(["", f"## {title}", "", "| Value | Products |", "|---|---:|"])
        for item in profile[key]:
            safe_value = str(item["value"]).replace("|", "\\|")
            lines.append(f"| {safe_value} | {item['count']:,} |")

    add_ranked("Most common leaf categories", "top_leaf_categories")
    add_ranked("Most common `details` keys", "top_detail_keys")
    add_ranked("Most common stores", "top_stores")

    lines.extend([
        "",
        "## Business implications",
        "",
        "- `parent_asin` is complete and unique; it is the only scored identifier.",
        "- Category data is complete, but leaf labels still require semantic quality checks.",
        "- Price is missing for most products. Budget filtering must use PASS / FAIL / UNKNOWN rather than deleting unknown-price items.",
        "- `details` is heterogeneous. Normalize keys case-insensitively and treat exact structured values as stronger evidence than free text.",
        "- `store` is useful brand evidence, but it is not guaranteed to be the canonical brand.",
        "- Features and descriptions are useful for material, use-case, and feature extraction, but missing values must never be interpreted as negative evidence.",
        "",
    ])
    return "\n".join(lines)


def _detail_value(details: object, *candidate_keys: str) -> str:
    if not isinstance(details, dict):
        return ""
    normalized = {str(key).strip().lower(): value for key, value in details.items()}
    for key in candidate_keys:
        value = normalized.get(key.lower())
        if value not in (None, "", []):
            return str(value)
    return ""


def _excerpt(value: object, limit: int = 240) -> str:
    text = " ".join(_flatten(value))
    return " ".join(text.split())[:limit]


def _audit_row(product: dict[str, Any]) -> dict[str, str]:
    categories = [str(item) for item in product.get("categories") or []]
    details = product.get("details") or {}
    raw_price = product.get("price")
    return {
        "parent_asin": str(product.get("parent_asin", "")),
        "title": str(product.get("title") or ""),
        "raw_leaf_category": categories[-1] if categories else "",
        "raw_category_path": " > ".join(categories),
        "raw_price": "" if raw_price is None else str(raw_price),
        "raw_store": str(product.get("store") or ""),
        "details_department": _detail_value(details, "Department"),
        "details_color": _detail_value(details, "Color"),
        "details_material": _detail_value(details, "Material"),
        "details_brand": _detail_value(details, "Brand", "Brand Name", "Manufacturer"),
        "details_size": _detail_value(details, "Size"),
        "features_excerpt": _excerpt(product.get("features")),
        "description_excerpt": _excerpt(product.get("description")),
        **{column: "" for column in AUDIT_COLUMNS if column.startswith("review_")},
    }


def build_audit_sample(
    catalog_path: str | Path,
    profile: dict[str, Any],
    sample_size: int = 50,
    category_count: int = 10,
    seed: int = 20260829,
) -> list[dict[str, str]]:
    """Select a stable, category-balanced sample without altering the catalog."""
    if sample_size <= 0 or category_count <= 0:
        return []
    categories = [item["value"] for item in profile["top_leaf_categories"][:category_count]]
    if not categories:
        return []
    base, remainder = divmod(sample_size, len(categories))
    limits = {category: base + (index < remainder) for index, category in enumerate(categories)}
    heaps: dict[str, list[tuple[int, str, dict[str, Any]]]] = {category: [] for category in categories}

    with Path(catalog_path).open(encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            product = json.loads(raw_line)
            product_categories = product.get("categories") or []
            leaf = str(product_categories[-1]) if product_categories else ""
            limit = limits.get(leaf, 0)
            if not limit:
                continue
            parent_asin = str(product.get("parent_asin", ""))
            digest = hashlib.sha256(f"{seed}:{parent_asin}".encode()).digest()
            score = int.from_bytes(digest[:8], "big")
            item = (-score, parent_asin, product)
            heap = heaps[leaf]
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif score < -heap[0][0]:
                heapq.heapreplace(heap, item)

    result: list[dict[str, str]] = []
    for category in categories:
        selected = sorted(heaps[category], key=lambda item: (-item[0], item[1]))
        result.extend(_audit_row(product) for _, _, product in selected)
    return result


def write_audit_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile the frozen catalog and create an audit sample")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--json-output", default="docs/catalog_profile.json")
    parser.add_argument("--markdown-output", default="docs/catalog_analysis.md")
    parser.add_argument("--audit-output", default="docs/catalog_manual_audit_sample.csv")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--audit-size", type=int, default=50)
    parser.add_argument("--audit-categories", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260829)
    args = parser.parse_args()

    profile = analyze_catalog(args.catalog, top_n=args.top_n)
    json_output = Path(args.json_output)
    markdown_output = Path(args.markdown_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_output.write_text(render_markdown(profile), encoding="utf-8")
    audit_rows = build_audit_sample(
        args.catalog,
        profile,
        sample_size=args.audit_size,
        category_count=args.audit_categories,
        seed=args.seed,
    )
    write_audit_csv(args.audit_output, audit_rows)
    print(json.dumps({
        "products": profile["row_count"],
        "unique_parent_asin": profile["unique_parent_asin_count"],
        "audit_rows": len(audit_rows),
        "json_output": args.json_output,
        "markdown_output": args.markdown_output,
        "audit_output": args.audit_output,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
