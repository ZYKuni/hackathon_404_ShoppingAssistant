"""Official public-set benchmark for the integrated formal Agent pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl  # noqa: E402
from starter.agent import Agent  # noqa: E402


def benchmark(catalog_path: str, dataset_path: str) -> dict:
    samples = load_jsonl(dataset_path)
    catalog_ids, categories, products = catalog_index(catalog_path)
    started = time.perf_counter()
    agent = Agent.with_local_pipeline(catalog_path)
    initialization_ms = (time.perf_counter() - started) * 1000.0
    try:
        result = evaluate(agent, samples, catalog_ids, categories, products)
        fallbacks = Counter(
            event
            for session_events in agent._pipeline_fallbacks.values()
            for event in session_events
        )
    finally:
        agent.connection.close()
    result["formal_pipeline_initialization_ms"] = round(initialization_ms, 3)
    result["fallback_events"] = dict(sorted(fallbacks.items()))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="analysis/integration_a5_metrics.json")
    args = parser.parse_args()
    result = benchmark(args.catalog, args.dataset)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()
