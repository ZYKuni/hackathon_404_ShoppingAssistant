"""Reproduce Aaron A3 CandidatePool Recall@200 and latency measurements.

This is offline analysis only.  It uses evaluator-generated intent cards to
simulate fully disclosed constraints and must never be imported by runtime code.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluator.local_evaluator import (
    catalog_index,
    coarse_category,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
)
from starter.pipeline_contracts import (
    IntentRoute,
    ProfileSnapshot,
    RouteDecision,
    SearchRequest,
    StateSnapshot,
)
from starter.retrieval import HybridRetriever


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[int(probability * (len(ordered) - 1))]


def benchmark(catalog_path: str, dataset_path: str, mode: str) -> dict:
    samples = load_jsonl(dataset_path)
    _, categories, products = catalog_index(catalog_path)
    init_started = time.perf_counter()
    retriever = HybridRetriever(catalog_path)
    init_ms = (time.perf_counter() - init_started) * 1000.0

    hits: dict[str, list[bool]] = defaultdict(list)
    target_ranks: list[int] = []
    latencies: list[float] = []
    for sample in samples:
        target = str(sample["ground_truth"]["parent_asin"])
        card, behavior = materialize_hidden_fields(sample, products)
        category = coarse_category(categories[target])
        if mode == "initial":
            effective = {**sample, "intent_card": card, "behavior": behavior}
            query = initial_message(effective, category, set())
        else:
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
            route_decision=RouteDecision(route, 0.8, f"{mode}-card benchmark"),
            current_message=query,
            raw_context=query,
            base_request=category,
            structured_query=query,
            state=StateSnapshot(schema_version="0.1.0", turn=1),
            profile=ProfileSnapshot(),
        )
        pool = retriever.retrieve(request)
        ranked = [candidate.parent_asin for candidate in pool.candidates]
        hit = target in ranked
        hits["overall"].append(hit)
        hits[scenario].append(hit)
        target_ranks.append(ranked.index(target) + 1 if hit else 201)
        latencies.append(pool.retrieval_latency_ms)

    return {
        "mode": mode,
        "sample_count": len(samples),
        "recall_at_200": {
            name: round(sum(values) / len(values), 6)
            for name, values in sorted(hits.items())
        },
        "target_rank": {
            "median": statistics.median(target_ranks),
            "p95": percentile([float(value) for value in target_ranks], 0.95),
        },
        "initialization_ms": round(init_ms, 3),
        "retrieval_latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--mode", choices=("initial", "full"), default="full")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = benchmark(args.catalog, args.dataset, args.mode)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
