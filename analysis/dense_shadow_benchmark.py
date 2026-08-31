"""Run the public evaluator with optional SHADOW or ON dense retrieval."""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent
from starter.dense_assets import DenseAssetManifest
from starter.dense_retrieval import DenseMode
from starter.numpy_dense_backend import (
    NumpyDenseSearchIndex,
    SentenceTransformerQueryEncoder,
)
from starter.onnx_query_encoder import OnnxMiniLMQueryEncoder
from starter.orchestrator import RuntimeMode


class RecordingAgent:
    """Record aggregate per-turn diagnostics without retaining customer text."""

    def __init__(self, inner: Agent) -> None:
        self.inner = inner
        self.diagnostics: list[dict] = []

    def reset(self, session_id: str, user_profile: dict) -> None:
        self.inner.reset(session_id, user_profile)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        try:
            return self.inner.respond(session_id, user_message, turn, top_k)
        finally:
            diagnostic = self.inner.dense_diagnostics(session_id, turn)
            self.diagnostics.append(asdict(diagnostic))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


def summarize(diagnostics: list[dict]) -> dict:
    attempted = [item for item in diagnostics if item["attempted"]]
    latencies = [float(item["latency_ms"]) for item in attempted]
    return {
        "turns_observed": len(diagnostics),
        "attempted_turns": len(attempted),
        "error_turns": sum(bool(item["error"]) for item in attempted),
        "mean_returned": round(statistics.fmean(
            int(item["returned_count"]) for item in attempted
        ), 3) if attempted else 0.0,
        "mean_exclusive": round(statistics.fmean(
            int(item["exclusive_count"]) for item in attempted
        ), 3) if attempted else 0.0,
        "mean_contributed": round(statistics.fmean(
            int(item["contributed_count"]) for item in attempted
        ), 3) if attempted else 0.0,
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_ms_max": round(max(latencies), 3) if latencies else None,
    }


def baseline_comparison(result: dict, baseline_path: Path | None) -> dict | None:
    if baseline_path is None:
        return None
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    keys = (
        "sample_count",
        "hit_rate_at_10",
        "mrr",
        "mttc",
        "recommended_technical_score",
        "scenario_metrics",
        "sessions",
    )
    differences = [key for key in keys if result.get(key) != baseline.get(key)]
    return {
        "baseline": str(baseline_path),
        "exact_match": not differences,
        "different_fields": differences,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--assets", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline", default="analysis/integration_a5_metrics.json")
    parser.add_argument(
        "--encoder",
        choices=("sentence-transformer", "onnx"),
        default="sentence-transformer",
    )
    parser.add_argument("--model-dir")
    parser.add_argument(
        "--mode",
        choices=(DenseMode.SHADOW.value, DenseMode.ON.value),
        default=DenseMode.SHADOW.value,
    )
    parser.add_argument(
        "--runtime-mode",
        choices=tuple(item.value for item in RuntimeMode),
        default=RuntimeMode.OFFICIAL.value,
    )
    args = parser.parse_args()

    manifest = DenseAssetManifest.load(args.assets)
    if args.encoder == "onnx":
        if not args.model_dir:
            parser.error("--model-dir is required when --encoder=onnx")
        encoder = OnnxMiniLMQueryEncoder(
            args.model_dir,
            dimension=manifest.embedding_dimension,
            max_sequence_length=manifest.max_sequence_length,
        )
    else:
        encoder = SentenceTransformerQueryEncoder(
            manifest.model_id,
            manifest.model_revision,
            manifest.max_sequence_length,
        )
    backend = NumpyDenseSearchIndex(
        args.assets,
        args.catalog,
        encoder=encoder,
    )
    recorder = RecordingAgent(Agent(
        args.catalog,
        runtime_mode=RuntimeMode(args.runtime_mode),
        dense_mode=DenseMode(args.mode),
        dense_backend=backend,
    ))
    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    result = evaluate(recorder, samples, catalog_ids, categories, products)
    result["dense_route"] = {
        "mode": args.mode,
        "runtime_mode": args.runtime_mode,
        "encoder": args.encoder,
        **summarize(recorder.diagnostics),
    }
    result["baseline_comparison"] = baseline_comparison(
        result,
        Path(args.baseline) if args.baseline else None,
    )
    Path(args.output).write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "hit_rate_at_10": result["hit_rate_at_10"],
        "mrr": result["mrr"],
        "mttc": result["mttc"],
        "recommended_technical_score": result["recommended_technical_score"],
        "dense_route": result["dense_route"],
        "baseline_comparison": result["baseline_comparison"],
    }, indent=2))


if __name__ == "__main__":
    main()
