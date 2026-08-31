"""Evaluate one Dense x Question Policy configuration with full diagnostics."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent
from starter.dense_retrieval import DenseMode
from starter.dense_runtime import load_optional_dense_backend
from starter.question_policy import QuestionPolicyMode


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


def _peak_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return round(value / divisor, 3)


class RecordingAgent:
    """Delegate Agent calls while retaining aggregate-safe per-turn diagnostics."""

    def __init__(self, inner: Agent, scenario_order: list[str]) -> None:
        self.inner = inner
        self.scenario_order = iter(scenario_order)
        self.session_scenarios: dict[str, str] = {}
        self.turns: list[dict] = []

    def reset(self, session_id: str, user_profile: dict) -> None:
        self.session_scenarios[session_id] = next(self.scenario_order)
        self.inner.reset(session_id, user_profile)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        started = time.perf_counter()
        response = self.inner.respond(session_id, user_message, turn, top_k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        question = self.inner.question_policy_diagnostics(session_id)
        dense = self.inner.dense_diagnostics(session_id, turn)
        self.turns.append({
            "scenario": self.session_scenarios[session_id],
            "turn": turn,
            "route": question.route.value if question and question.route else None,
            "question_mode": question.mode.value if question else None,
            "selected_attribute": question.selected_attribute if question else None,
            "applied_attribute": question.applied_attribute if question else None,
            "dynamic_applied": bool(question.dynamic_applied) if question else False,
            "question_reason": question.reason if question else None,
            "gate_reason": question.gate_reason if question else None,
            "top_value": float(question.top_value) if question else 0.0,
            "value_margin": float(question.value_margin) if question else 0.0,
            "has_seen_buying_intent": bool(question.has_seen_buying_intent) if question else False,
            "candidate_count": int(question.candidate_count) if question else 0,
            "dense": asdict(dense),
            "fallbacks": list(self.inner.pipeline_fallbacks(session_id)),
            "respond_latency_ms": round(latency_ms, 3),
        })
        return response


def _counter(values) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def summarize_turns(turns: list[dict]) -> dict:
    dense_attempts = [item["dense"] for item in turns if item["dense"]["attempted"]]
    dense_latencies = [float(item["latency_ms"]) for item in dense_attempts]
    response_latencies = [float(item["respond_latency_ms"]) for item in turns]
    top_values = [float(item["top_value"]) for item in turns]
    margins = [float(item["value_margin"]) for item in turns]
    fallback_values = [value for item in turns for value in item["fallbacks"]]

    by_scenario: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in turns:
        grouped[item["scenario"]].append(item)
    for scenario, items in sorted(grouped.items()):
        by_scenario[scenario] = {
            "turns": len(items),
            "selected_attributes": _counter(item["selected_attribute"] for item in items),
            "applied_attributes": _counter(item["applied_attribute"] for item in items),
            "dynamic_applied_turns": sum(item["dynamic_applied"] for item in items),
            "gate_reasons": _counter(item["gate_reason"] for item in items if item["gate_reason"]),
        }

    return {
        "turn_count": len(turns),
        "routes": _counter(item["route"] for item in turns),
        "question_policy": {
            "selected_attributes": _counter(item["selected_attribute"] for item in turns),
            "applied_attributes": _counter(item["applied_attribute"] for item in turns),
            "dynamic_applied_turns": sum(item["dynamic_applied"] for item in turns),
            "gate_reasons": _counter(item["gate_reason"] for item in turns if item["gate_reason"]),
            "top_value_p50": _percentile(top_values, 0.50),
            "top_value_p95": _percentile(top_values, 0.95),
            "value_margin_p50": _percentile(margins, 0.50),
            "value_margin_p95": _percentile(margins, 0.95),
            "by_scenario": by_scenario,
        },
        "dense": {
            "attempted_turns": len(dense_attempts),
            "error_turns": sum(bool(item["error"]) for item in dense_attempts),
            "mean_returned": round(statistics.fmean(
                int(item["returned_count"]) for item in dense_attempts
            ), 3) if dense_attempts else 0.0,
            "mean_exclusive": round(statistics.fmean(
                int(item["exclusive_count"]) for item in dense_attempts
            ), 3) if dense_attempts else 0.0,
            "mean_contributed": round(statistics.fmean(
                int(item["contributed_count"]) for item in dense_attempts
            ), 3) if dense_attempts else 0.0,
            "latency_ms_p50": _percentile(dense_latencies, 0.50),
            "latency_ms_p95": _percentile(dense_latencies, 0.95),
            "latency_ms_max": round(max(dense_latencies), 3) if dense_latencies else None,
        },
        "respond_latency_ms": {
            "p50": _percentile(response_latencies, 0.50),
            "p95": _percentile(response_latencies, 0.95),
            "max": round(max(response_latencies), 3) if response_latencies else None,
        },
        "fallbacks": {
            "total": len(fallback_values),
            "events": _counter(fallback_values),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--dense-mode",
        choices=tuple(item.value for item in DenseMode),
        default=DenseMode.OFF.value,
    )
    parser.add_argument(
        "--question-mode",
        choices=tuple(item.value for item in QuestionPolicyMode),
        default=QuestionPolicyMode.SAFE.value,
    )
    parser.add_argument("--dense-assets")
    parser.add_argument("--dense-model")
    parser.add_argument("--conditional-min-value", type=float, default=0.10)
    parser.add_argument("--conditional-min-margin", type=float, default=0.02)
    parser.add_argument("--conditional-max-turn", type=int, default=3)
    parser.add_argument("--conditional-no-other-after-buying", action="store_true")
    parser.add_argument("--conditional-sticky-buying-safe", action="store_true")
    parser.add_argument("--semantic-weight", type=float, default=0.0)
    parser.add_argument("--include-turn-records", action="store_true")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    dense_mode = DenseMode(args.dense_mode)
    started = time.perf_counter()
    dense_load_error = None
    backend = None
    effective_dense_mode = dense_mode
    if dense_mode is not DenseMode.OFF:
        if not args.dense_assets or not args.dense_model:
            parser.error("dense assets and model are required unless dense mode is off")
        loaded = load_optional_dense_backend(
            args.catalog,
            args.dense_assets,
            args.dense_model,
            mode=dense_mode,
        )
        backend = loaded.backend
        effective_dense_mode = loaded.effective_mode
        dense_load_error = loaded.error
        if effective_dense_mode is not dense_mode or backend is None:
            raise RuntimeError(f"dense configuration failed to load: {dense_load_error}")

    inner = Agent(
        args.catalog,
        dense_mode=effective_dense_mode,
        dense_backend=backend,
        question_policy_mode=args.question_mode,
        conditional_question_min_value=args.conditional_min_value,
        conditional_question_min_margin=args.conditional_min_margin,
        conditional_question_browsing_max_turn=args.conditional_max_turn,
        conditional_question_allow_other_after_buying=not args.conditional_no_other_after_buying,
        conditional_question_sticky_buying_safe=args.conditional_sticky_buying_safe,
        semantic_rerank_weight=args.semantic_weight,
    )
    initialization_seconds = time.perf_counter() - started
    recorder = RecordingAgent(
        inner,
        [str(sample["scenario_type"]) for sample in samples],
    )
    result = evaluate(recorder, samples, catalog_ids, categories, products)
    diagnostics = summarize_turns(recorder.turns)
    payload = {
        "schema_version": "1.0.0",
        "configuration": {
            "dense_mode": dense_mode.value,
            "effective_dense_mode": effective_dense_mode.value,
            "dense_load_error": dense_load_error,
            "question_mode": args.question_mode,
            "conditional_min_value": args.conditional_min_value,
            "conditional_min_margin": args.conditional_min_margin,
            "conditional_max_turn": args.conditional_max_turn,
            "conditional_allow_other_after_buying": not args.conditional_no_other_after_buying,
            "conditional_sticky_buying_safe": args.conditional_sticky_buying_safe,
            "semantic_weight": args.semantic_weight,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "initialization_seconds": round(initialization_seconds, 3),
            "peak_rss_mb": _peak_rss_mb(),
        },
        "metrics": result,
        "diagnostics": diagnostics,
    }
    if args.include_turn_records:
        payload["turn_records"] = recorder.turns
    Path(args.output).write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "configuration": payload["configuration"],
        "environment": payload["environment"],
        "metrics": {key: value for key, value in result.items() if key != "sessions"},
        "diagnostics": diagnostics,
    }, indent=2))


if __name__ == "__main__":
    main()
