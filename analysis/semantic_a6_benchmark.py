"""Ablation for Todo 4.4 vector retrieval and 4.6 Top-30 semantic ranking."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.ranker_a4_benchmark import _profile, _snapshot  # noqa: E402
from evaluator.local_evaluator import (  # noqa: E402
    catalog_index,
    coarse_category,
    load_jsonl,
    materialize_hidden_fields,
)
from starter.catalog_normalizer import CatalogNormalizer  # noqa: E402
from starter.pipeline_contracts import (  # noqa: E402
    IntentRoute,
    RouteDecision,
    SearchRequest,
)
from starter.ranker import LocalConstraintRanker, RankerWeights  # noqa: E402
from starter.retrieval import HybridRetriever, SQLiteCatalogSearchIndex  # noqa: E402
from starter.vector_index import InMemoryTfidfIndex  # noqa: E402


def _rank(ids: list[str], target: str) -> int | None:
    return ids.index(target) + 1 if target in ids else None


def _summary(ranks: list[int | None]) -> dict:
    count = len(ranks)
    return {
        "recall_at_200": round(sum(rank is not None for rank in ranks) / count, 6),
        "top10_hit_rate": round(sum(rank is not None and rank <= 10 for rank in ranks) / count, 6),
        "mrr": round(statistics.fmean(0.0 if rank is None else 1.0 / rank for rank in ranks), 6),
    }


def _latency(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "mean": round(statistics.fmean(values), 3),
        "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
        "max": round(max(values), 3),
    }


def benchmark(catalog_path: str, dataset_path: str) -> dict:
    samples = load_jsonl(dataset_path)
    _, categories, products = catalog_index(catalog_path)

    started = time.perf_counter()
    lexical_backend = SQLiteCatalogSearchIndex(catalog_path)
    fts_ms = (time.perf_counter() - started) * 1000.0
    tracemalloc.start()
    vector = InMemoryTfidfIndex(catalog_path)
    _, vector_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    started = time.perf_counter()
    catalog = CatalogNormalizer.from_jsonl(catalog_path)
    normalizer_ms = (time.perf_counter() - started) * 1000.0

    lexical_retriever = HybridRetriever(
        backend=lexical_backend, enable_vector=False
    )
    vector_retriever = HybridRetriever(
        backend=lexical_backend, vector_backend=vector
    )
    local_ranker = LocalConstraintRanker(catalog=catalog)
    semantic_weights = (0.02, 0.04, 0.06, 0.08, 0.10, 0.12)
    semantic_rankers = {
        weight: LocalConstraintRanker(
            catalog=catalog,
            semantic_scorer=vector,
            weights=RankerWeights(semantic_similarity=weight),
        )
        for weight in semantic_weights
    }

    ranks: dict[str, list[int | None]] = defaultdict(list)
    scenario_semantic: dict[str, list[int | None]] = defaultdict(list)
    latencies: dict[str, list[float]] = defaultdict(list)
    diversity_counts: list[int] = []
    semantic_fallbacks = {weight: 0 for weight in semantic_weights}

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
        route = IntentRoute.BROWSING if scenario in {"browsing", "boundary"} else IntentRoute.BUYING
        request = SearchRequest(
            session_id=str(sample["sample_id"]), turn=1, top_k=10, candidate_limit=200,
            route_decision=RouteDecision(route, 0.8, "4.4/4.6 full-card ablation"),
            current_message=query, raw_context=query, base_request=category,
            structured_query=query, state=_snapshot(query, category),
            profile=_profile(sample.get("user_profile") or {}),
        )
        lexical_pool = lexical_retriever.retrieve(request)
        vector_pool = vector_retriever.retrieve(request)
        local_result = local_ranker.rank(request, vector_pool)
        semantic_results = {
            weight: ranker.rank(request, vector_pool)
            for weight, ranker in semantic_rankers.items()
        }
        for weight, ranker in semantic_rankers.items():
            semantic_fallbacks[weight] += ranker.last_semantic_fallback

        lexical_ids = [item.parent_asin for item in lexical_pool.candidates]
        vector_ids = [item.parent_asin for item in vector_pool.candidates]
        local_ids = [item.parent_asin for item in local_result.candidates]
        ranks["bm25_hybrid_rrf"].append(_rank(lexical_ids, target))
        ranks["plus_tfidf_vector"].append(_rank(vector_ids, target))
        ranks["plus_local_ranker"].append(_rank(local_ids, target))
        for weight, result in semantic_results.items():
            semantic_ids = [item.parent_asin for item in result.candidates]
            semantic_rank = _rank(semantic_ids, target)
            ranks[f"plus_top30_semantic_{weight:.2f}"].append(semantic_rank)
            scenario_semantic[f"{scenario}@{weight:.2f}"].append(semantic_rank)
        latencies["lexical_retrieval_ms"].append(lexical_pool.retrieval_latency_ms)
        latencies["vector_retrieval_ms"].append(vector_pool.retrieval_latency_ms)
        latencies["local_ranker_ms"].append(local_result.ranking_latency_ms)
        selected_ranker = semantic_rankers[0.04]
        selected_result = semantic_results[0.04]
        latencies["semantic_ranker_0.04_ms"].append(selected_result.ranking_latency_ms)
        latencies["semantic_only_0.04_ms"].append(selected_ranker.last_semantic_latency_ms)
        if route is IntentRoute.BROWSING:
            diversity_counts.append(len({
                vector.category_key(item.parent_asin)
                for item in vector_pool.candidates[:30]
            }))

    return {
        "sample_count": len(samples),
        "ablation": {name: _summary(values) for name, values in ranks.items()},
        "semantic_by_scenario": {
            name: _summary(values) for name, values in sorted(scenario_semantic.items())
        },
        "initialization_ms": {
            "fts": round(fts_ms, 3),
            "tfidf_vector": round(vector.stats.initialization_ms, 3),
            "normalizer": round(normalizer_ms, 3),
        },
        "vector_index": {
            "document_count": vector.stats.document_count,
            "vocabulary_size": vector.stats.vocabulary_size,
            "posting_count": vector.stats.posting_count,
            "estimated_posting_mib": round(vector.stats.estimated_posting_bytes / 2**20, 3),
            "tracemalloc_peak_mib": round(vector_peak / 2**20, 3),
        },
        "latency": {name: _latency(values) for name, values in latencies.items()},
        "mean_unique_categories_in_browsing_top30": round(
            statistics.fmean(diversity_counts), 3
        ),
        "semantic_fallback_count": {
            f"{weight:.2f}": count for weight, count in semantic_fallbacks.items()
        },
        "external_tokens": 0,
        "external_cost": 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="analysis/semantic_a6_metrics.json")
    args = parser.parse_args()
    result = benchmark(args.catalog, args.dataset)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
