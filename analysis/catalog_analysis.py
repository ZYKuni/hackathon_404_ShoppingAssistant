"""Profile the competition catalog and generate a readable Markdown report.

The script intentionally uses only the Python standard library so every team
member can run it in the same environment as the starter agent.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


EXPECTED_FIELDS = (
    "parent_asin",
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
SEARCH_FIELDS = ("title", "categories", "features", "details", "store", "description")
COVERAGE_FIELDS = ("title", "features", "description", "price", "categories", "details", "store")
BASELINE_WEIGHTS = {
    "title": 6.0,
    "categories": 4.0,
    "features": 2.5,
    "details": 2.5,
    "store": 1.5,
    "description": 1.0,
}
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


class BM25Index:
    """In-memory FTS5 index matching the starter agent's tokenizer and weights."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = sqlite3.connect(":memory:")
        columns = ", ".join(SEARCH_FIELDS)
        self.connection.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            f"parent_asin UNINDEXED, {columns}, tokenize='unicode61 remove_diacritics 2')"
        )
        placeholders = ", ".join("?" for _ in range(len(SEARCH_FIELDS) + 1))
        batch = [
            (str(row["parent_asin"]), *(_text(row.get(field)) for field in SEARCH_FIELDS))
            for row in rows
        ]
        self.connection.executemany(f"INSERT INTO products VALUES ({placeholders})", batch)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def ranked_ids(self, query: str, included_fields: tuple[str, ...], limit: int) -> list[str]:
        from starter.agent import _terms

        unique_terms = list(dict.fromkeys(_terms(query)))[:40]
        if not unique_terms or not included_fields:
            return []
        terms = " OR ".join(f'"{term}"' for term in unique_terms)
        fields = " ".join(included_fields)
        expression = f"{{{fields}}} : ({terms})"
        weights = ", ".join(str(BASELINE_WEIGHTS[field]) for field in SEARCH_FIELDS)
        sql = (
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            f"ORDER BY bm25(products, 0.0, {weights}) LIMIT ?"
        )
        return [str(row[0]) for row in self.connection.execute(sql, (expression, limit))]

    def target_rank(self, query: str, target: str, included_fields: tuple[str, ...]) -> int | None:
        """Return deterministic BM25 rank without materializing a large Top-N list."""
        from starter.agent import _terms

        unique_terms = list(dict.fromkeys(_terms(query)))[:40]
        if not unique_terms or not included_fields:
            return None
        terms = " OR ".join(f'"{term}"' for term in unique_terms)
        fields = " ".join(included_fields)
        expression = f"{{{fields}}} : ({terms})"
        weights = ", ".join(str(BASELINE_WEIGHTS[field]) for field in SEARCH_FIELDS)
        target_sql = (
            "SELECT rowid, bm25(products, 0.0, " + weights + ") "
            "FROM products WHERE products MATCH ? AND parent_asin = ?"
        )
        target_row = self.connection.execute(target_sql, (expression, target)).fetchone()
        if target_row is None:
            return None
        target_rowid, target_score = target_row
        rank_sql = (
            "WITH scored AS MATERIALIZED ("
            "SELECT rowid, bm25(products, 0.0, " + weights + ") AS score "
            "FROM products WHERE products MATCH ?"
            ") SELECT 1 + COUNT(*) FROM scored "
            "WHERE score < ? OR (score = ? AND rowid < ?)"
        )
        return int(
            self.connection.execute(
                rank_sql, (expression, target_score, target_score, target_rowid)
            ).fetchone()[0]
        )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} of {path} is not a JSON object")
            rows.append(value)
    return rows


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def percentile(sorted_values: list[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def normalize_title(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def numeric_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    sorted_values = sorted(float(value) for value in values)
    return {
        "count": len(sorted_values),
        "min": percentile(sorted_values, 0),
        "p25": percentile(sorted_values, 0.25),
        "median": percentile(sorted_values, 0.5),
        "p75": percentile(sorted_values, 0.75),
        "p95": percentile(sorted_values, 0.95),
        "max": percentile(sorted_values, 1),
    }


def profile_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row_count = len(rows)
    all_fields = sorted(set(EXPECTED_FIELDS).union(*(row.keys() for row in rows)))
    field_profile: dict[str, dict[str, Any]] = {}
    for field in all_fields:
        missing = sum(is_empty(row.get(field)) for row in rows)
        types = Counter(type_name(row.get(field)) for row in rows)
        field_profile[field] = {
            "missing_count": missing,
            "missing_rate": missing / row_count if row_count else 0,
            "types": dict(types.most_common()),
        }

    asins = [str(row.get("parent_asin") or "") for row in rows]
    asin_counts = Counter(asins)
    duplicate_asins = {key: count for key, count in asin_counts.items() if key and count > 1}
    invalid_asins = [value for value in asins if not ASIN_RE.fullmatch(value)]

    normalized_titles = [normalize_title(row.get("title")) for row in rows]
    normalized_title_counts = Counter(title for title in normalized_titles if title)
    repeated_title_rows = sum(count for count in normalized_title_counts.values() if count > 1)

    numeric_prices = [
        float(row["price"])
        for row in rows
        if isinstance(row.get("price"), (int, float)) and not isinstance(row.get("price"), bool)
    ]
    string_prices = [row["price"] for row in rows if isinstance(row.get("price"), str)]
    parseable_string_prices = [
        value for value in string_prices if re.fullmatch(r"(?:from\s+)?\d+(?:\.\d+)?", value.strip(), re.I)
    ]
    price_summary = numeric_summary(numeric_prices)
    price_summary.update(
        {
            "numeric_coverage_rate": len(numeric_prices) / row_count if row_count else 0,
            "string_count": len(string_prices),
            "parseable_string_count": len(parseable_string_prices),
            "invalid_string_count": len(string_prices) - len(parseable_string_prices),
            "invalid_string_examples": list(dict.fromkeys(string_prices))[:10],
            "non_positive_count": sum(value <= 0 for value in numeric_prices),
            "above_1000_count": sum(value > 1000 for value in numeric_prices),
        }
    )

    ratings = [row.get("average_rating") for row in rows]
    rating_numbers = [row.get("rating_number") for row in rows]
    invalid_ratings = sum(
        not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 5
        for value in ratings
    )
    invalid_rating_numbers = sum(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in rating_numbers
    )

    categories = Counter()
    leaf_categories = Counter()
    category_depths: list[float] = []
    details_keys = Counter()
    for row in rows:
        values = row.get("categories") or []
        if isinstance(values, list):
            cleaned = [str(value).strip() for value in values if str(value).strip()]
            categories.update(cleaned)
            if cleaned:
                leaf_categories[cleaned[-1]] += 1
                category_depths.append(float(len(cleaned)))
        details = row.get("details") or {}
        if isinstance(details, dict):
            details_keys.update(str(key) for key in details)

    search_coverage = {}
    for field in SEARCH_FIELDS:
        populated = sum(not is_empty(row.get(field)) for row in rows)
        search_coverage[field] = {
            "populated_count": populated,
            "coverage_rate": populated / row_count if row_count else 0,
        }
    any_search_text = sum(
        any(not is_empty(row.get(field)) for field in SEARCH_FIELDS) for row in rows
    )

    return {
        "row_count": row_count,
        "column_count": len(all_fields),
        "fields": field_profile,
        "primary_key": {
            "unique_count": len(asin_counts),
            "duplicate_key_count": len(duplicate_asins),
            "duplicate_row_count": sum(count - 1 for count in duplicate_asins.values()),
            "invalid_format_count": len(invalid_asins),
            "duplicate_examples": list(duplicate_asins.items())[:10],
            "invalid_examples": invalid_asins[:10],
        },
        "titles": {
            "normalized_distinct_count": len(normalized_title_counts),
            "repeated_normalized_title_rows": repeated_title_rows,
            "repeated_normalized_title_rate": repeated_title_rows / row_count if row_count else 0,
        },
        "price": price_summary,
        "ratings": {
            "invalid_average_rating_count": invalid_ratings,
            "invalid_rating_number_count": invalid_rating_numbers,
            "zero_rating_number_count": sum(value == 0 for value in rating_numbers),
        },
        "categories": {
            "distinct_count": len(categories),
            "leaf_distinct_count": len(leaf_categories),
            "depth": numeric_summary(category_depths),
            "top_all": categories.most_common(15),
            "top_leaf": leaf_categories.most_common(15),
        },
        "details": {
            "distinct_key_count": len(details_keys),
            "top_keys": details_keys.most_common(20),
        },
        "search_coverage": {
            "fields": search_coverage,
            "any_search_text_count": any_search_text,
            "any_search_text_rate": any_search_text / row_count if row_count else 0,
        },
    }


def profile_sessions(samples: list[dict[str, Any]], catalog_ids: set[str]) -> dict[str, Any]:
    scenario_counts = Counter(str(row.get("scenario_type") or "missing") for row in samples)
    difficulty_counts = Counter(str(row.get("difficulty_bucket") or "missing") for row in samples)
    scenario_difficulty_counts = Counter(
        (str(row.get("scenario_type") or "missing"), str(row.get("difficulty_bucket") or "missing"))
        for row in samples
    )
    targets = [str((row.get("ground_truth") or {}).get("parent_asin") or "") for row in samples]
    missing_targets = [target for target in targets if target not in catalog_ids]
    return {
        "row_count": len(samples),
        "scenario_counts": dict(scenario_counts.most_common()),
        "difficulty_counts": dict(difficulty_counts.most_common()),
        "scenario_difficulty_counts": [
            {"scenario_type": scenario, "difficulty_bucket": difficulty, "count": count}
            for (scenario, difficulty), count in scenario_difficulty_counts.most_common()
        ],
        "distinct_target_count": len(set(targets)),
        "missing_target_count": len(missing_targets),
        "missing_target_examples": missing_targets[:10],
    }


def coverage_rows(rows: list[dict[str, Any]], baseline: dict[str, float]) -> list[dict[str, Any]]:
    count = len(rows)
    result = []
    for field in COVERAGE_FIELDS:
        populated = sum(not is_empty(row.get(field)) for row in rows)
        coverage = populated / count if count else 0.0
        base = baseline[field]
        result.append(
            {
                "field": field,
                "sample_count": count,
                "populated_count": populated,
                "coverage_rate": coverage,
                "catalog_coverage_rate": base,
                "difference_percentage_points": (coverage - base) * 100,
                "coverage_multiple": coverage / base if base else None,
            }
        )
    return result


def profile_target_coverage(
    samples: list[dict[str, Any]], products: dict[str, dict[str, Any]], catalog_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline = {
        field: sum(not is_empty(row.get(field)) for row in catalog_rows) / len(catalog_rows)
        for field in COVERAGE_FIELDS
    }

    def target_rows(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [products[str(row["ground_truth"]["parent_asin"])] for row in selected]

    overall = coverage_rows(target_rows(samples), baseline)
    by_scenario = {
        scenario: coverage_rows(
            target_rows([row for row in samples if row.get("scenario_type") == scenario]), baseline
        )
        for scenario in ("buying", "browsing", "intent_override", "boundary")
    }
    by_difficulty = {
        difficulty: coverage_rows(
            target_rows([row for row in samples if row.get("difficulty_bucket") == difficulty]), baseline
        )
        for difficulty in ("easy", "medium", "hard")
    }
    return {
        "definition": "non-empty value; null, empty string, empty list, and empty object are absent",
        "catalog_coverage": baseline,
        "overall_targets": overall,
        "by_scenario": by_scenario,
        "by_difficulty": by_difficulty,
    }


def _flatten_with_source(value: Any, source: str) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [
            (f"{key}: {item}", source)
            for key, item in value.items()
            if item not in (None, "", [])
        ]
    if isinstance(value, list):
        return [(str(item), source) for item in value if item not in (None, "")]
    return [(str(value), source)] if value not in (None, "") else []


def _regex_source(product: dict[str, Any], pattern: re.Pattern[str]) -> str:
    evaluator_order = ("title", "features", "details", "description", "categories", "store")
    for field in evaluator_order:
        if pattern.search(_text(product.get(field))):
            return field
    return "unknown"


def intent_card_with_provenance(product: dict[str, Any], limit: int = 180) -> dict[str, Any]:
    """Reproduce evaluator.intent_card while retaining each constraint's source."""
    from evaluator.local_evaluator import COLOR_RE, MATERIAL_RE, _clean_constraint, intent_card, searchable_text

    title = _clean_constraint(str(product.get("title") or "product"), limit)
    candidates = [
        *_flatten_with_source(product.get("features"), "features"),
        *_flatten_with_source(product.get("details"), "details"),
    ]
    corpus = searchable_text(product)
    material = MATERIAL_RE.search(corpus)
    color = COLOR_RE.search(corpus)
    if material:
        candidates.insert(0, (material.group(1).lower(), f"material_regex:{_regex_source(product, MATERIAL_RE)}"))
    if color:
        candidates.insert(1, (f"color: {color.group(1).lower()}", f"color_regex:{_regex_source(product, COLOR_RE)}"))
    if product.get("price") not in (None, ""):
        candidates.append((f"budget around ${product['price']}", "price"))

    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value, source in candidates:
        normalized = _clean_constraint(value, limit)
        if normalized and normalized not in seen:
            seen.add(normalized)
            cleaned.append((normalized, source))
    if not cleaned:
        cleaned = [(title, "title_fallback")]

    selected = cleaned[:4]
    derived = {
        "target_category": title,
        "hard_constraints": [
            {"value": value, "source": source} for value, source in selected[:2]
        ],
        "soft_preferences": [
            {"value": value, "source": source}
            for value, source in (selected[2:4] or selected[:1])
        ],
    }
    plain = {
        "target_category": derived["target_category"],
        "hard_constraints": [item["value"] for item in derived["hard_constraints"]],
        "soft_preferences": [item["value"] for item in derived["soft_preferences"]],
    }
    derived["matches_evaluator"] = plain == intent_card(product, limit)
    return derived


def profile_hidden_constraints(
    samples: list[dict[str, Any]], products: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    from evaluator.local_evaluator import classify_constraint

    records: list[dict[str, str]] = []
    mismatches = 0
    for sample in samples:
        target = str(sample["ground_truth"]["parent_asin"])
        card = intent_card_with_provenance(products[target])
        mismatches += int(not card["matches_evaluator"])
        for role in ("hard_constraints", "soft_preferences"):
            for item in card[role]:
                records.append(
                    {
                        "sample_id": str(sample["sample_id"]),
                        "scenario_type": str(sample["scenario_type"]),
                        "difficulty_bucket": str(sample["difficulty_bucket"]),
                        "role": "hard" if role == "hard_constraints" else "soft",
                        "constraint_type": classify_constraint(item["value"]),
                        "source": item["source"],
                    }
                )

    type_counts = Counter(record["constraint_type"] for record in records)
    source_counts = Counter(record["source"] for record in records)
    role_counts = Counter(record["role"] for record in records)
    source_type = Counter((record["source"], record["constraint_type"]) for record in records)
    return {
        "constraint_count": len(records),
        "evaluator_reproduction_mismatch_count": mismatches,
        "type_counts": dict(type_counts.most_common()),
        "source_counts": dict(source_counts.most_common()),
        "role_counts": dict(role_counts.most_common()),
        "source_type_counts": [
            {"source": source, "constraint_type": constraint_type, "count": count}
            for (source, constraint_type), count in source_type.most_common()
        ],
        "by_scenario": {
            scenario: dict(
                Counter(
                    record["constraint_type"]
                    for record in records
                    if record["scenario_type"] == scenario
                ).most_common()
            )
            for scenario in ("buying", "browsing", "intent_override", "boundary")
        },
        "by_difficulty": {
            difficulty: dict(
                Counter(
                    record["constraint_type"]
                    for record in records
                    if record["difficulty_bucket"] == difficulty
                ).most_common()
            )
            for difficulty in ("easy", "medium", "hard")
        },
    }


def rank_summary(ranks: list[int | None], max_rank: int) -> dict[str, Any]:
    found = [rank for rank in ranks if rank is not None]
    return {
        "sample_count": len(ranks),
        "evaluated_to_rank": max_rank,
        "top_10_rate": sum(rank is not None and rank <= 10 for rank in ranks) / len(ranks) if ranks else 0,
        "top_50_rate": sum(rank is not None and rank <= 50 for rank in ranks) / len(ranks) if ranks else 0,
        "top_100_rate": sum(rank is not None and rank <= 100 for rank in ranks) / len(ranks) if ranks else 0,
        "top_1000_rate": sum(rank is not None and rank <= 1000 for rank in ranks) / len(ranks) if ranks else 0,
        "mrr_at_limit": sum(0 if rank is None else 1 / rank for rank in ranks) / len(ranks) if ranks else 0,
        "median_found_rank": percentile([float(rank) for rank in sorted(found)], 0.5),
        "not_found_within_limit": len(ranks) - len(found),
    }


def _query_records(
    samples: list[dict[str, Any]], products: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    from evaluator.local_evaluator import coarse_category, initial_message, materialize_hidden_fields

    records = []
    for sample in samples:
        target = str(sample["ground_truth"]["parent_asin"])
        product = products[target]
        card, behavior = materialize_hidden_fields(sample, products)
        effective = {**sample, "intent_card": card, "behavior": behavior}
        category = coarse_category([str(value) for value in product.get("categories") or []])
        initial = initial_message(effective, category, set())
        full = " ".join(
            [category, *[str(value) for value in card.get("hard_constraints", [])],
             *[str(value) for value in card.get("soft_preferences", [])]]
        )
        records.append(
            {
                "sample_id": str(sample["sample_id"]),
                "scenario_type": str(sample["scenario_type"]),
                "difficulty_bucket": str(sample["difficulty_bucket"]),
                "target": target,
                "initial_query": initial,
                "oracle_query": full,
            }
        )
    return records


def _rank_queries(
    index: BM25Index,
    records: list[dict[str, str]],
    query_field: str,
    included_fields: tuple[str, ...],
    max_rank: int,
) -> list[dict[str, Any]]:
    results = []
    for record in records:
        ranked = index.ranked_ids(record[query_field], included_fields, max_rank)
        rank = ranked.index(record["target"]) + 1 if record["target"] in ranked else None
        results.append({**record, "rank": rank})
    return results


def _segment_rank_summaries(records: list[dict[str, Any]], max_rank: int) -> dict[str, Any]:
    return {
        "overall": rank_summary([record["rank"] for record in records], max_rank),
        "by_scenario": {
            scenario: rank_summary(
                [record["rank"] for record in records if record["scenario_type"] == scenario], max_rank
            )
            for scenario in ("buying", "browsing", "intent_override", "boundary")
        },
        "by_difficulty": {
            difficulty: rank_summary(
                [record["rank"] for record in records if record["difficulty_bucket"] == difficulty], max_rank
            )
            for difficulty in ("easy", "medium", "hard")
        },
    }


def profile_bm25(
    catalog_rows: list[dict[str, Any]], samples: list[dict[str, Any]], products: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    records = _query_records(samples, products)
    index = BM25Index(catalog_rows)
    try:
        rank_limit = 100
        initial = _rank_queries(index, records, "initial_query", SEARCH_FIELDS, rank_limit)
        ablation_fields = ("title", "categories", "features", "details")
        configurations: dict[str, tuple[str, ...]] = {"all_fields": SEARCH_FIELDS}
        configurations.update({f"only_{field}": (field,) for field in ablation_fields})
        configurations.update(
            {
                f"without_{field}": tuple(value for value in SEARCH_FIELDS if value != field)
                for field in ablation_fields
            }
        )
        ablation = {}
        oracle = []
        for name, included_fields in configurations.items():
            ranked = _rank_queries(index, records, "oracle_query", included_fields, rank_limit)
            if name == "all_fields":
                oracle = ranked
            ablation[name] = {
                "included_fields": list(included_fields),
                **rank_summary([record["rank"] for record in ranked], rank_limit),
            }
    finally:
        index.close()
    return {
        "query_definitions": {
            "initial": "exact evaluator first message for each public session",
            "oracle": "coarse category plus all hidden hard and soft constraints",
            "rank_limit": "all target ranks and ablations are observed to Top 100",
        },
        "initial_all_fields": _segment_rank_summaries(initial, rank_limit),
        "oracle_all_fields": _segment_rank_summaries(oracle, rank_limit),
        "oracle_field_ablation": ablation,
        "session_ranks": [
            {
                "sample_id": initial_record["sample_id"],
                "scenario_type": initial_record["scenario_type"],
                "difficulty_bucket": initial_record["difficulty_bucket"],
                "initial_rank": initial_record["rank"],
                "oracle_rank": oracle_record["rank"],
            }
            for initial_record, oracle_record in zip(initial, oracle)
        ],
    }


def format_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def format_rate(value: float) -> str:
    return f"{value:.1%}"


def portable_source_path(path: Path) -> str:
    """Return report-safe provenance without publishing a contributor's local path."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def render_report(
    catalog: dict[str, Any],
    sessions: dict[str, Any],
    target_coverage: dict[str, Any],
    constraints: dict[str, Any],
    bm25: dict[str, Any],
    catalog_path: Path,
    sessions_path: Path,
) -> str:
    fields = catalog["fields"]
    price = catalog["price"]
    key = catalog["primary_key"]
    title = catalog["titles"]
    description_missing = fields["description"]["missing_rate"]
    price_missing = fields["price"]["missing_rate"]
    feature_missing = fields["features"]["missing_rate"]

    field_rows = []
    for field in EXPECTED_FIELDS:
        item = fields[field]
        field_rows.append([
            f"`{field}`",
            format_number(item["missing_count"]),
            format_rate(item["missing_rate"]),
            ", ".join(f"{name}: {count}" for name, count in item["types"].items()),
        ])

    search_coverage_rows = [
        [f"`{field}`", format_number(value["populated_count"]), format_rate(value["coverage_rate"])]
        for field, value in catalog["search_coverage"]["fields"].items()
    ]
    category_rows = [[name.replace("|", "\\|"), format_number(count)] for name, count in catalog["categories"]["top_leaf"]]
    detail_rows = [[name.replace("|", "\\|"), format_number(count)] for name, count in catalog["details"]["top_keys"]]
    scenario_rows = [[name, count, format_rate(count / sessions["row_count"])] for name, count in sessions["scenario_counts"].items()]
    difficulty_rows = [[name, count, format_rate(count / sessions["row_count"])] for name, count in sessions["difficulty_counts"].items()]
    scenario_difficulty_rows = [
        [item["scenario_type"], item["difficulty_bucket"], item["count"]]
        for item in sessions["scenario_difficulty_counts"]
    ]

    def comparison_rows(values: list[dict[str, Any]], segment: str | None = None) -> list[list[Any]]:
        output = []
        for item in values:
            row = [] if segment is None else [segment]
            multiple = item["coverage_multiple"]
            row.extend(
                [
                    f"`{item['field']}`",
                    format_rate(item["catalog_coverage_rate"]),
                    format_rate(item["coverage_rate"]),
                    f"{item['difference_percentage_points']:+.1f} pp",
                    "—" if multiple is None else f"{multiple:.2f}×",
                ]
            )
            output.append(row)
        return output

    overall_coverage_rows = comparison_rows(target_coverage["overall_targets"])
    scenario_coverage_rows = []
    for segment, values in target_coverage["by_scenario"].items():
        scenario_coverage_rows.extend(comparison_rows(values, segment))
    difficulty_coverage_rows = []
    for segment, values in target_coverage["by_difficulty"].items():
        difficulty_coverage_rows.extend(comparison_rows(values, segment))

    constraint_type_rows = [
        [name, count, format_rate(count / constraints["constraint_count"])]
        for name, count in constraints["type_counts"].items()
    ]
    constraint_source_rows = [
        [name, count, format_rate(count / constraints["constraint_count"])]
        for name, count in constraints["source_counts"].items()
    ]
    source_type_rows = [
        [item["source"], item["constraint_type"], item["count"]]
        for item in constraints["source_type_counts"][:20]
    ]

    def rank_segment_rows(dimension: str) -> list[list[Any]]:
        output = []
        initial_values = bm25["initial_all_fields"][dimension]
        oracle_values = bm25["oracle_all_fields"][dimension]
        for segment in initial_values:
            for stage, values in (("首轮", initial_values[segment]), ("完整约束", oracle_values[segment])):
                output.append(
                    [
                        segment,
                        stage,
                        format_rate(values["top_10_rate"]),
                        format_rate(values["top_50_rate"]),
                        format_rate(values["top_100_rate"]),
                        format_number(values["median_found_rank"]),
                    ]
                )
        return output

    scenario_rank_rows = rank_segment_rows("by_scenario")
    difficulty_rank_rows = rank_segment_rows("by_difficulty")
    initial_overall = bm25["initial_all_fields"]["overall"]
    oracle_overall = bm25["oracle_all_fields"]["overall"]
    ablation = bm25["oracle_field_ablation"]
    all_fields = ablation["all_fields"]
    only_rows = []
    without_rows = []
    for field in ("title", "categories", "features", "details"):
        only = ablation[f"only_{field}"]
        without = ablation[f"without_{field}"]
        only_rows.append(
            [
                field,
                format_rate(only["top_10_rate"]),
                format_rate(only["top_50_rate"]),
                format_rate(only["top_100_rate"]),
                f"{only['mrr_at_limit']:.4f}",
            ]
        )
        without_rows.append(
            [
                field,
                format_rate(without["top_10_rate"]),
                f"{(without['top_10_rate'] - all_fields['top_10_rate']) * 100:+.1f} pp",
                format_rate(without["top_100_rate"]),
                f"{(without['top_100_rate'] - all_fields['top_100_rate']) * 100:+.1f} pp",
                f"{without['mrr_at_limit']:.4f}",
            ]
        )

    price_target = next(item for item in target_coverage["overall_targets"] if item["field"] == "price")
    top_constraint_types = list(constraints["type_counts"].items())[:2]
    constraint_summary = "、".join(
        f"{name} {format_rate(count / constraints['constraint_count'])}"
        for name, count in top_constraint_types
    )
    catalog_source = portable_source_path(catalog_path)
    sessions_source = portable_source_path(sessions_path)

    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f"""# 商品目录数据质量与检索可用性分析

生成时间：{generated_at}

## 技术摘要

- 50,000 件商品的 `parent_asin` 无重复、无格式异常，200 个公开目标商品全部能连接到目录；但目标集并非目录的缩小版，例如价格覆盖率从全目录的 {price_target['catalog_coverage_rate']:.1%} 升至目标集的 {price_target['coverage_rate']:.1%}（{price_target['difference_percentage_points']:+.1f} 个百分点，{price_target['coverage_multiple']:.2f}×）。
- 评测器为 200 个目标生成 {constraints['constraint_count']} 条硬约束或软偏好，主要类型为 {constraint_summary}。因此字段优先级应由约束生成机制和消融实验共同决定，而不能只看目录非空率。
- 使用全部字段时，真实首轮消息的 BM25 Top 10 目标覆盖率为 {initial_overall['top_10_rate']:.1%}；拼接全部隐藏约束后的 oracle 查询为 {oracle_overall['top_10_rate']:.1%}。两者差距衡量“获得完整需求”对词法检索的潜在提升，不等同于最终 Agent 分数。
- 商品目录仍存在明显稀疏性：`description` 缺失 {description_missing:.1%}、`features` 缺失 {feature_missing:.1%}，数值价格覆盖仅 {price['numeric_coverage_rate']:.1%}。这些问题影响泛化，但公开目标集的覆盖情况必须单独判断。

## 分析范围与指标定义

- **全目录：** 50,000 个商品行，每行粒度为一个 `parent_asin`。
- **目标集：** `public_set.jsonl` 中 200 个不同目标 ASIN。
- **字段覆盖率：** 字段不为 `null`、空字符串、空列表或空对象的商品占比。
- **百分点差：** 目标组覆盖率减去全目录覆盖率；`+10 pp` 表示高10个百分点。
- **覆盖倍数：** 目标组覆盖率除以全目录覆盖率；它不是概率提升或因果效果。
- **首轮查询：** 本地评测器实际生成的第一条用户消息。
- **完整约束查询：** 粗粒度类目加该目标全部隐藏硬约束和软偏好，是诊断检索上限的 oracle，不代表 Agent 在首轮可以获得这些信息。
- **目标排名：** 使用与 Starter 相同的 FTS5 分词和 BM25 权重；首轮、完整约束和字段消融都观察到 Top 100。

## 目标集与全目录存在明显覆盖差异

{markdown_table(['字段', '全目录覆盖率', '目标集覆盖率', '差异', '倍数'], overall_coverage_rows)}

**解释：** 目标集的 `features`、`details`、`store` 均为100%覆盖，而价格也远高于全目录。全目录缺失率仍决定私有集泛化风险，但公开集上的模块优先级不能直接由全目录平均值推出。

### 不同场景的字段覆盖

{markdown_table(['场景', '字段', '全目录覆盖率', '场景目标覆盖率', '差异', '倍数'], scenario_coverage_rows)}

**解释：** 每个场景的样本量不同，尤其 Boundary 只有10个样本，因此其覆盖倍数只用于描述，不应当作稳定规律。

### 不同难度的字段覆盖

{markdown_table(['难度', '字段', '全目录覆盖率', '难度组目标覆盖率', '差异', '倍数'], difficulty_coverage_rows)}

**解释：** 难度标签来自公开集设定。本表能显示元数据完整性是否与难度相关，但不能证明缺失字段导致了难度。

公开集中场景与难度高度绑定：

{markdown_table(['场景', '难度', '样本数'], scenario_difficulty_rows)}

因此，easy/medium/hard 的差异不能与场景效应分离：easy 全部是 Buying，hard 全部是 Intent Override，medium 由 Browsing 和 Boundary 构成。后续不应把“难度组差异”解释成独立的难度因果效应。

## 评测器数据生成链路

```text
public_set 中的 ground_truth.parent_asin
  → 回查 catalog 目标商品
  → categories 最后两级生成粗粒度初始品类
  → title / features / details / description / categories / store
       检测 material 与 color
  → features 条目 + details 键值 + 可选 price
       按固定顺序选前2条硬约束、后2条软偏好
  → scenario 决定首轮透露方式
       Buying：品类 + 第一条硬约束
       Browsing / Boundary：只给品类
       Intent Override：先给旧偏好，第3或4轮再覆盖为新约束
  → Agent 的 ask_attribute 决定下一条可透露约束
  → Agent 返回最多10个 parent_asin
  → 精确 ID 相等才命中
```

本脚本独立复现了公开目标的 intent card，并与评测器输出逐项对照；不一致样本数为 **{constraints['evaluator_reproduction_mismatch_count']}**。这项分析只用于理解公开评测机制，Agent 运行时不会收到目标商品或隐藏约束。

## 隐藏需求主要由 feature 与 material 构成

{markdown_table(['约束类型', '数量', '占比'], constraint_type_rows)}

{markdown_table(['约束来源', '数量', '占比'], constraint_source_rows)}

下表展示数量最多的“来源—类型”组合：

{markdown_table(['来源', '约束类型', '数量'], source_type_rows)}

**解释：** `material_regex:字段名` 和 `color_regex:字段名` 表示评测器在合并文本中正则命中，并由脚本追溯到最早出现该词的商品字段。其余来源表示约束直接来自 features、details、price 或标题回退。

## 完整需求显著改变 BM25 目标排名

### 按场景观察

{markdown_table(['场景', '查询阶段', 'Top 10', 'Top 50', 'Top 100', '命中样本中位排名'], scenario_rank_rows)}

**注意：** Intent Override 在新意图到达前禁止转化，因此它的首轮排名只是诊断旧偏好造成的偏移，不能与 Buying 的首轮转化能力直接比较。

### 按难度观察

{markdown_table(['难度', '查询阶段', 'Top 10', 'Top 50', 'Top 100', '命中样本中位排名'], difficulty_rank_rows)}

**解释：** 若完整约束后目标仍未进入 Top 100，主要瓶颈更可能是词法召回、字段噪声或同词商品竞争；若已进入 Top 100 但不在 Top 10，则更适合优先改进重排。

## BM25字段消融揭示单字段能力与边际贡献

所有消融都使用完整约束查询，并使用原 Starter 对应字段权重。`only_*` 只允许指定字段参与匹配，`without_*` 从全字段索引中移除一个字段；结果观察至 Top 100。

全字段基准：Top 10 = {all_fields['top_10_rate']:.1%}，Top 50 = {all_fields['top_50_rate']:.1%}，Top 100 = {all_fields['top_100_rate']:.1%}，MRR@100 = {all_fields['mrr_at_limit']:.4f}。

### 单字段能力

{markdown_table(['仅使用字段', 'Top 10', 'Top 50', 'Top 100', 'MRR@100'], only_rows)}

### 移除单字段后的变化

{markdown_table(['移除字段', 'Top 10', '相对全字段', 'Top 100', '相对全字段', 'MRR@100'], without_rows)}

**解释：** Single-field 衡量独立可召回性，drop-one 衡量在其他字段已存在时的边际贡献。字段之间高度重复，因此两类结果不应混为一谈；这些结果也只适用于当前公开集和固定查询构造。

**重要限制：** 评测器本身主要从目标商品 `features` 抽取约束，完整约束查询又包含这些原文，因此 features 的优势部分来自数据生成机制的直接耦合。它是本赛题公开评测的重要信号，但不能外推为真实电商数据中的普遍字段价值。

## 目录主键可靠，但稀疏字段会影响过滤

本分析以一行一个 `parent_asin` 为商品粒度。空值同时包含 JSON `null`、空字符串、空列表和空对象。

{markdown_table(['字段', '空值数', '空值率', '观测类型'], field_rows)}

**检索影响：** `parent_asin` 可安全用于评分和连接；价格与描述只能作为部分覆盖信号。`details` 虽然非空，但键名自由变化，需要先规范化才能用于结构化过滤。

## 多字段文本召回是必要条件

下表展示每个 Starter 搜索字段的非空覆盖率。这里的“覆盖”只表示字段有内容，不表示内容一定包含用户所需属性。

{markdown_table(['检索字段', '非空商品数', '覆盖率'], search_coverage_rows)}

**说明：** 非空覆盖只能说明字段可用，不代表字段有助于排名。具体字段优先级应以上面的 single-field 与 drop-one 消融为准。

## 价格信号覆盖不足且存在边界值

{markdown_table(['指标', '值'], [
    ['有数值价格的商品', format_number(price['count'])],
    ['字符串价格', format_number(price['string_count'])],
    ['可解析字符串价格', format_number(price['parseable_string_count'])],
    ['不可解析字符串价格', format_number(price['invalid_string_count'])],
    ['最小值', '$' + format_number(price['min'])],
    ['25 分位', '$' + format_number(price['p25'])],
    ['中位数', '$' + format_number(price['median'])],
    ['75 分位', '$' + format_number(price['p75'])],
    ['95 分位', '$' + format_number(price['p95'])],
    ['最大值', '$' + format_number(price['max'])],
    ['非正价格', format_number(price['non_positive_count'])],
    ['高于 $1,000', format_number(price['above_1000_count'])],
])}

**风险：** 如果预算过滤只保留数值价格，会一次性丢掉 {1 - price['numeric_coverage_rate']:.1%} 的目录，显著降低目标召回率。字符串值中有 {price['invalid_string_count']} 个无法可靠转换，另有 {price['parseable_string_count']} 个形如 `from 12.99` 的下限价格。报告只识别统计异常，不断言高价商品一定错误。

## 商品类目呈长尾分布

目录共有 {catalog['categories']['distinct_count']:,} 个不同类目标签和 {catalog['categories']['leaf_distinct_count']:,} 个不同末级类目。类目路径中位深度为 {catalog['categories']['depth']['median']:.1f}。

{markdown_table(['高频末级类目', '商品数'], category_rows)}

**检索影响：** 不宜只维护少量固定品类枚举。建议同时保存完整类目路径和末级类目，并为高频同义词建立轻量映射。

## `details` 键名丰富，需要规范化属性层

`details` 中共出现 {catalog['details']['distinct_key_count']:,} 种键名，最常见的键如下。

{markdown_table(['Details 键', '出现次数'], detail_rows)}

**建议：** 优先映射 `Department`、`Manufacturer`、尺寸/材质相关键；日期、包装尺寸等字段可保留在搜索文本中，但不应默认当作用户购买约束。

## 公开会话构成与连接完整性

{markdown_table(['场景', '样本数', '占比'], scenario_rows)}

{markdown_table(['难度', '样本数', '占比'], difficulty_rows)}

共有 {sessions['distinct_target_count']} 个不同目标 ASIN，目录连接缺失数为 {sessions['missing_target_count']}。这说明公开集可用于目标商品回查和离线错误分析。

## 方法、限制与稳健性

- 数据源：`{catalog_source}` 与 `{sessions_source}`。
- 所有统计直接读取 JSONL，不对目录内容做修补。隐藏 intent card 由公开评测器逻辑在本地重新生成，不读取任何私有文件。
- 标题重复采用小写字母数字规范化后精确匹配；它只能发现明显重复，不能识别语义近似商品。
- 价格分布只统计 JSON 数值；脚本单独识别可解析与不可解析字符串，不推断币种，也不把极端值自动判定为错误。
- BM25 使用 Starter 的分词方式、字段权重与 OR 查询。排名只计算到设定截断值，未出现的目标只能解释为“未进入 Top N”，不是完整目录中的精确名次。
- 完整约束查询是诊断 oracle，不能作为真实在线表现；所有公开集结论仍可能对800个私有会话过拟合。

## 建议的下一步

1. 根据 drop-one 消融结果选择第一轮字段权重实验，不再仅按字段非空率设权重。
2. 将每个会话的首轮排名、完整约束排名和最终 Agent 结果连接，形成“召回失败 / 重排失败 / 对话效率失败”分类。
3. 从四种场景各抽样目标商品，人工核对“用户表达—商品字段—可抽取槽位”。
4. 建立规范化商品文档与属性层，再用相同查询集复跑本报告，比较 Top 10、Top 100 和 MRR@100。
5. 将 intent card 复现一致性、主键唯一性、目标连接完整性和字段消融基准加入持续测试。

## 仍需回答的问题

- 完整约束后仍在 Top 100 之外的目标，主要失败模式是同义词、类目噪声还是同款竞争？
- 不同 ask_attribute 顺序能够为候选池带来多少信息增益？
- 当前公开集得到的字段贡献是否能在留出子集或未来私有评测中稳定复现？
"""


def analyze(catalog_path: Path, sessions_path: Path) -> dict[str, Any]:
    catalog_rows = load_jsonl(catalog_path)
    session_rows = load_jsonl(sessions_path)
    catalog_profile = profile_catalog(catalog_rows)
    products = {str(row.get("parent_asin") or ""): row for row in catalog_rows}
    catalog_ids = set(products)
    session_profile = profile_sessions(session_rows, catalog_ids)
    target_coverage = profile_target_coverage(session_rows, products, catalog_rows)
    hidden_constraints = profile_hidden_constraints(session_rows, products)
    bm25 = profile_bm25(catalog_rows, session_rows, products)
    return {
        "catalog": catalog_profile,
        "sessions": session_profile,
        "target_coverage": target_coverage,
        "hidden_constraints": hidden_constraints,
        "bm25": bm25,
    }


def main() -> None:
    project_root = PROJECT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=project_root / "data" / "catalog.jsonl")
    parser.add_argument("--sessions", type=Path, default=project_root / "data" / "public_set.jsonl")
    parser.add_argument("--report", type=Path, default=project_root / "analysis" / "catalog_analysis_report.md")
    parser.add_argument("--metrics", type=Path, default=project_root / "analysis" / "catalog_analysis_metrics.json")
    args = parser.parse_args()

    result = analyze(args.catalog, args.sessions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_report(
            result["catalog"],
            result["sessions"],
            result["target_coverage"],
            result["hidden_constraints"],
            result["bm25"],
            args.catalog,
            args.sessions,
        ),
        encoding="utf-8",
    )
    args.metrics.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report written to {args.report}")
    print(f"Metrics written to {args.metrics}")


if __name__ == "__main__":
    main()
