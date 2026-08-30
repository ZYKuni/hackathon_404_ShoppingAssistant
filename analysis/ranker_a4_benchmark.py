"""Offline fixed-CandidatePool benchmark for Aaron A4.

Ground truth is used only to measure ranks and filter survival.  Runtime
Retriever/Ranker code never receives the target ASIN.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluator.local_evaluator import (  # noqa: E402
    catalog_index,
    coarse_category,
    load_jsonl,
    materialize_hidden_fields,
)
from starter.catalog_normalizer import CatalogNormalizer  # noqa: E402
from starter.constraint_parser import parse_message  # noqa: E402
from starter.conversation_state import ConversationState, apply_patch  # noqa: E402
from starter.pipeline_contracts import (  # noqa: E402
    ConstraintTerm,
    IntentRoute,
    ProfileSnapshot,
    RouteDecision,
    SearchRequest,
    StateSnapshot,
)
from starter.ranker import LocalConstraintRanker  # noqa: E402
from starter.retrieval import HybridRetriever  # noqa: E402


def _term(field: str, value: object) -> ConstraintTerm:
    values = value if isinstance(value, list) else [value]
    return ConstraintTerm(field, tuple(values))


def _snapshot(query: str, base_category: str) -> StateSnapshot:
    previous = ConversationState()
    parsed = apply_patch(previous, parse_message(query, previous, 1))
    category_state = ConversationState()
    category_state = apply_patch(
        category_state, parse_message(base_category, category_state, 1)
    )
    # This benchmark supplies an artificial, trusted SearchRequest to Aaron's
    # module.  Product prose may contain other categories or dimensions and must
    # not masquerade as an intent override or budget.
    parsed.category = category_state.category
    if not re.search(r"(?i)\bbudget\s+around\s+\$\s*\d", query):
        parsed.hard_constraints.pop("price_min", None)
        parsed.hard_constraints.pop("price_max", None)
    return StateSnapshot(
        schema_version=parsed.schema_version,
        turn=parsed.turn,
        category=parsed.category,
        hard_constraints=tuple(
            _term(field, value) for field, value in sorted(parsed.hard_constraints.items())
        ),
        soft_preferences=tuple(
            _term(field, value) for field, value in sorted(parsed.soft_preferences.items())
        ),
        excluded=tuple(
            _term(field, value) for field, value in sorted(parsed.excluded.items())
        ),
        no_preference=tuple(parsed.no_preference),
        asked_attributes=tuple(parsed.asked_attributes),
    )


def _profile(value: dict) -> ProfileSnapshot:
    average = value.get("average_prior_rating")
    return ProfileSnapshot(
        preference_tags=tuple(str(item) for item in value.get("preference_tags", [])),
        average_prior_rating=float(average) if average is not None else None,
        purchase_frequency=value.get("purchase_frequency"),
        rating_style=value.get("rating_style"),
    )


def _p95(values: list[float]) -> float:
    return sorted(values)[int(0.95 * (len(values) - 1))]


def benchmark(catalog_path: str, dataset_path: str) -> dict:
    samples = load_jsonl(dataset_path)
    _, categories, products = catalog_index(catalog_path)

    init_started = time.perf_counter()
    normalized = CatalogNormalizer.from_jsonl(catalog_path)
    normalizer_ms = (time.perf_counter() - init_started) * 1000.0
    retrieval_started = time.perf_counter()
    retriever = HybridRetriever(catalog_path)
    retriever_init_ms = (time.perf_counter() - retrieval_started) * 1000.0
    ranker = LocalConstraintRanker(catalog=normalized)

    by_scenario: dict[str, list[dict]] = defaultdict(list)
    rrf_reciprocals: list[float] = []
    ranker_reciprocals: list[float] = []
    rrf_top10 = 0
    ranker_top10 = 0
    recalled_targets = 0
    survived_targets = 0
    filtered_counts: list[int] = []
    unknown_counts: list[int] = []
    ranker_latencies: list[float] = []
    target_filter_failures: list[dict] = []

    for sample in samples:
        target = str(sample["ground_truth"]["parent_asin"])
        card, _ = materialize_hidden_fields(sample, products)
        category = coarse_category(categories[target])
        query = " ".join((
            category,
            *map(str, card.get("hard_constraints", [])),
            *map(str, card.get("soft_preferences", [])),
        ))
        scenario = str(sample["scenario_type"])
        route = (
            IntentRoute.BROWSING
            if scenario in {"browsing", "boundary"}
            else IntentRoute.BUYING
        )
        request = SearchRequest(
            session_id=str(sample["sample_id"]),
            turn=1,
            top_k=10,
            candidate_limit=200,
            route_decision=RouteDecision(route, 0.8, "full-card A4 benchmark"),
            current_message=query,
            raw_context=query,
            base_request=category,
            structured_query=query,
            state=_snapshot(query, category),
            profile=_profile(sample.get("user_profile") or {}),
        )
        pool = retriever.retrieve(request)
        rrf_ids = [candidate.parent_asin for candidate in pool.candidates]
        result = ranker.rank(request, pool)
        ranked_ids = [candidate.parent_asin for candidate in result.candidates]

        rrf_rank = rrf_ids.index(target) + 1 if target in rrf_ids else None
        ranker_rank = ranked_ids.index(target) + 1 if target in ranked_ids else None
        if rrf_rank is not None and ranker_rank is None:
            target_product = normalized.get(target)
            assert target_product is not None
            target_evaluation = ranker.matcher.evaluate(request.state, target_product)
            target_filter_failures.append({
                "sample_id": sample["sample_id"],
                "scenario": scenario,
                "target": target,
                "state": {
                    "category": request.state.category,
                    "hard": [
                        {"field": term.field, "values": term.values}
                        for term in request.state.hard_constraints
                    ],
                    "excluded": [
                        {"field": term.field, "values": term.values}
                        for term in request.state.excluded
                    ],
                },
                "mismatches": [
                    {
                        "field": item.field,
                        "expected": item.expected,
                        "confidence": item.confidence,
                        "reason": item.reason,
                    }
                    for item in target_evaluation.hard + target_evaluation.excluded
                    if item.state.value == "mismatch"
                ],
            })
        recalled_targets += rrf_rank is not None
        survived_targets += ranker_rank is not None
        rrf_reciprocals.append(0.0 if rrf_rank is None else 1.0 / rrf_rank)
        ranker_reciprocals.append(0.0 if ranker_rank is None else 1.0 / ranker_rank)
        rrf_top10 += rrf_rank is not None and rrf_rank <= 10
        ranker_top10 += ranker_rank is not None and ranker_rank <= 10
        filtered_counts.append(result.filtered_count)
        unknown_counts.append(result.unknown_preserved_count)
        ranker_latencies.append(result.ranking_latency_ms)
        by_scenario[scenario].append({"rrf_rank": rrf_rank, "ranker_rank": ranker_rank})

    count = len(samples)
    scenario_metrics = {}
    for scenario, rows in sorted(by_scenario.items()):
        scenario_metrics[scenario] = {
            "sample_count": len(rows),
            "rrf_mrr": round(statistics.fmean(
                0.0 if row["rrf_rank"] is None else 1.0 / row["rrf_rank"] for row in rows
            ), 6),
            "ranker_mrr": round(statistics.fmean(
                0.0 if row["ranker_rank"] is None else 1.0 / row["ranker_rank"] for row in rows
            ), 6),
        }
    return {
        "sample_count": count,
        "candidate_recall_at_200": round(recalled_targets / count, 6),
        "target_filter_survival": round(
            survived_targets / recalled_targets if recalled_targets else 0.0, 6
        ),
        "rrf": {
            "mrr": round(statistics.fmean(rrf_reciprocals), 6),
            "top10_hit_rate": round(rrf_top10 / count, 6),
        },
        "local_ranker": {
            "mrr": round(statistics.fmean(ranker_reciprocals), 6),
            "top10_hit_rate": round(ranker_top10 / count, 6),
        },
        "scenario_mrr": scenario_metrics,
        "mean_filtered_count": round(statistics.fmean(filtered_counts), 3),
        "mean_unknown_preserved_count": round(statistics.fmean(unknown_counts), 3),
        "initialization_ms": {
            "normalizer": round(normalizer_ms, 3),
            "retriever": round(retriever_init_ms, 3),
        },
        "ranker_latency_ms": {
            "mean": round(statistics.fmean(ranker_latencies), 3),
            "p95": round(_p95(ranker_latencies), 3),
            "max": round(max(ranker_latencies), 3),
        },
        "target_filter_failures": target_filter_failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = benchmark(args.catalog, args.dataset)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
