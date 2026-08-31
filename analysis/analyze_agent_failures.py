from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead|ignore (?:my )?(?:earlier|previous)|changed? my mind|what i (?:really )?need)\b",
    re.IGNORECASE,
)


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
    return rows


def _minimum(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _override_turn(turns: list[dict[str, Any]]) -> int | None:
    for turn in turns:
        if OVERRIDE_RE.search(str(turn.get("user_message", ""))):
            value = turn.get("turn")
            return value if isinstance(value, int) else None
    return None


def _turn_evidence(turn: dict[str, Any]) -> dict[str, Any]:
    ranking = turn.get("ranking") if isinstance(turn.get("ranking"), dict) else {}
    routes = ranking.get("routes") if isinstance(ranking.get("routes"), list) else []
    route_ranks = {
        str(route.get("name", "unknown")): route.get("target_rank")
        for route in routes
        if isinstance(route, dict) and isinstance(route.get("target_rank"), int)
    }
    response = turn.get("response") if isinstance(turn.get("response"), dict) else {}
    return {
        "turn": turn.get("turn"),
        "user_message": str(turn.get("user_message", "")),
        "route_target_ranks": route_ranks,
        "candidate_pool_target_rank": ranking.get("candidate_pool_target_rank"),
        "recommendation_target_rank": ranking.get("recommendation_target_rank"),
        "ask_attribute": response.get("ask_attribute"),
    }


def classify_miss(session: dict[str, Any], trace: dict[str, Any] | None) -> dict[str, Any]:
    sample_id = str(session["sample_id"])
    scenario = str(session["scenario_type"])
    context = trace.get("evaluation_context", {}) if isinstance(trace, dict) else {}
    target = str(context.get("target_parent_asin", ""))
    turns = trace.get("turns", []) if isinstance(trace, dict) else []
    turns = turns if isinstance(turns, list) else []
    evidence = [_turn_evidence(turn) for turn in turns if isinstance(turn, dict)]

    route_rank = _minimum(
        rank
        for item in evidence
        for rank in item["route_target_ranks"].values()
    )
    pool_rank = _minimum(item["candidate_pool_target_rank"] for item in evidence)
    recommendation_rank = _minimum(
        item["recommendation_target_rank"] for item in evidence
    )
    override_turn = _override_turn(turns)

    before_override = [
        item for item in evidence
        if override_turn is not None and isinstance(item["turn"], int) and item["turn"] < override_turn
    ]
    after_override = [
        item for item in evidence
        if override_turn is not None and isinstance(item["turn"], int) and item["turn"] >= override_turn
    ]
    target_before_override = any(
        item["route_target_ranks"] or item["candidate_pool_target_rank"] is not None
        for item in before_override
    )
    target_after_override = any(
        item["route_target_ranks"] or item["candidate_pool_target_rank"] is not None
        for item in after_override
    )
    recommended_before_override = any(
        item["recommendation_target_rank"] is not None for item in before_override
    )
    recommended_after_override = any(
        item["recommendation_target_rank"] is not None for item in after_override
    )

    diagnostics_available = bool(trace and trace.get("diagnostics_available"))
    if not diagnostics_available or not evidence:
        primary = "Runtime/fallback failure"
        confidence = "high"
        reason = "Diagnostic trace is unavailable or empty, so the miss cannot be assigned to a ranking stage."
        next_action = "Repair trace/runtime coverage, reproduce this session, then reclassify it."
    elif (
        scenario == "intent_override"
        and override_turn is not None
        and (
            (recommended_before_override and not recommended_after_override)
            or (target_before_override and not target_after_override)
        )
    ):
        primary = "Override failure"
        confidence = "high"
        reason = "The target was viable before the explicit override but was never recommended afterwards, when the evaluator begins counting hits."
        next_action = "Rebuild active constraints at the override turn and add an override-specific regression test."
    elif route_rank is None and pool_rank is None:
        primary = "Recall failure"
        confidence = "high"
        reason = "The target never appeared in any retrieval route or merged candidate pool."
        next_action = "Improve query normalization, category/attribute expansion, or add a complementary recall route."
    elif recommendation_rank is None:
        primary = "Rerank failure"
        confidence = "high"
        reason = "The target entered a route or merged pool but never reached the final Top-10."
        next_action = "Inspect route weights and target-versus-cutoff scores; add attribute-aware reranking."
    else:
        primary = "Dialogue failure"
        confidence = "medium"
        reason = "The trace contains a target recommendation even though the evaluator records a miss."
        next_action = "Reconcile evaluator and trace session mapping before changing the model."

    secondary: list[str] = []
    if scenario == "intent_override" and primary != "Override failure":
        secondary.append("Override context")
    if scenario == "boundary":
        secondary.append("Boundary context")
    if any(bool(turn.get("fallback", {}).get("used")) for turn in turns if isinstance(turn, dict)):
        secondary.append("Fallback used")

    final_state = {}
    if turns and isinstance(turns[-1], dict) and isinstance(turns[-1].get("state"), dict):
        final_state = turns[-1]["state"]

    return {
        "sample_id": sample_id,
        "scenario_type": scenario,
        "target_parent_asin": target,
        "primary_failure": primary,
        "secondary_labels": secondary,
        "confidence": confidence,
        "reason": reason,
        "next_action": next_action,
        "turn_count": len(evidence),
        "override_turn": override_turn,
        "target_before_override": target_before_override,
        "target_after_override": target_after_override,
        "recommended_before_override": recommended_before_override,
        "recommended_after_override": recommended_after_override,
        "ever_in_route": route_rank is not None,
        "ever_in_candidate_pool": pool_rank is not None,
        "ever_in_recommendations": recommendation_rank is not None,
        "best_route_rank": route_rank,
        "best_candidate_pool_rank": pool_rank,
        "best_recommendation_rank": recommendation_rank,
        "final_state": final_state,
        "turn_evidence": evidence,
    }


def analyze(result: dict[str, Any], traces: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = result.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("result must contain a sessions list")
    trace_by_sample: dict[str, dict[str, Any]] = {}
    for trace in traces:
        context = trace.get("evaluation_context", {}) if isinstance(trace, dict) else {}
        sample_id = context.get("sample_id") if isinstance(context, dict) else None
        if isinstance(sample_id, str):
            if sample_id in trace_by_sample:
                raise ValueError(f"duplicate diagnostic trace for {sample_id}")
            trace_by_sample[sample_id] = trace

    misses = [session for session in sessions if not bool(session.get("hit"))]
    details = [
        classify_miss(session, trace_by_sample.get(str(session["sample_id"])))
        for session in misses
    ]
    primary_counts = Counter(item["primary_failure"] for item in details)
    scenario_counts: dict[str, Counter[str]] = defaultdict(Counter)
    rerank_pool_rank_buckets: Counter[str] = Counter()
    for item in details:
        scenario_counts[item["scenario_type"]][item["primary_failure"]] += 1
        if item["primary_failure"] == "Rerank failure":
            rank = item["best_candidate_pool_rank"]
            if rank is None:
                rerank_pool_rank_buckets["route-only/no merged-pool rank"] += 1
            elif rank <= 10:
                rerank_pool_rank_buckets["1-10"] += 1
            elif rank <= 20:
                rerank_pool_rank_buckets["11-20"] += 1
            elif rank <= 50:
                rerank_pool_rank_buckets["21-50"] += 1
            else:
                rerank_pool_rank_buckets[">50"] += 1

    expected_misses = len(sessions) - round(float(result["hit_rate_at_10"]) * len(sessions))
    if expected_misses != len(misses):
        raise ValueError(
            f"session misses ({len(misses)}) do not match aggregate hit rate ({expected_misses})"
        )

    return {
        "schema_version": "1.0.0",
        "sample_count": len(sessions),
        "miss_count": len(misses),
        "hit_rate_at_10": result.get("hit_rate_at_10"),
        "trace_count": len(traces),
        "matched_trace_count": sum(
            str(session["sample_id"]) in trace_by_sample for session in misses
        ),
        "primary_failure_counts": dict(sorted(primary_counts.items())),
        "scenario_failure_counts": {
            scenario: dict(sorted(counts.items()))
            for scenario, counts in sorted(scenario_counts.items())
        },
        "rerank_best_pool_rank_buckets": dict(rerank_pool_rank_buckets),
        "taxonomy_notes": {
            "Recall failure": "Target never enters any retrieval route or merged pool.",
            "Rerank failure": "Target is retrieved but never enters final Top-10.",
            "Override failure": "Target is viable before an explicit override but never recommended in the evaluator-eligible post-override turns.",
            "Dialogue failure": "Trace/evaluator disagree about whether target was recommended.",
            "Runtime/fallback failure": "Trace is absent or empty; ranking-stage attribution is impossible.",
            "Filter failure": "Not separately measurable because this baseline has no filter stage.",
            "Boundary failure": "Recorded as context, not a competing primary stage label.",
        },
        "failures": details,
    }


def markdown_report(report: dict[str, Any], result_path: Path, traces_path: Path) -> str:
    miss_count = int(report["miss_count"])
    lines = [
        "# Baseline failure analysis",
        "",
        "## Technical summary",
        "",
        f"The baseline misses **{miss_count} of {report['sample_count']} sessions** "
        f"(Hit Rate@10 = **{report['hit_rate_at_10']:.2%}**). This first-pass diagnosis "
        "uses evaluator outcomes plus per-turn retrieval/ranking traces; primary labels are "
        "mutually exclusive so counts reconcile exactly to the miss total.",
        "",
        "## Primary failure counts",
        "",
        "| Primary failure | Misses | Share of misses |",
        "|---|---:|---:|",
    ]
    for label, count in sorted(
        report["primary_failure_counts"].items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"| {label} | {count} | {count / miss_count:.1%} |")

    lines.extend([
        "",
        "## Key findings",
        "",
        f"- Rerank failures account for {report['primary_failure_counts'].get('Rerank failure', 0)}/{miss_count} misses; "
        f"{report['rerank_best_pool_rank_buckets'].get('11-20', 0)} are near-cutoff targets with a best merged-pool rank of 11-20.",
        f"- Override failures account for {report['primary_failure_counts'].get('Override failure', 0)}/{miss_count} misses and should be isolated before broad rank-weight tuning.",
        f"- Only {report['primary_failure_counts'].get('Recall failure', 0)}/{miss_count} misses never retrieve the target, so adding recall routes is not the first global priority.",
        "",
        "## Scenario × primary failure",
        "",
        "| Scenario | Primary failure | Misses |",
        "|---|---|---:|",
    ])
    for scenario, counts in report["scenario_failure_counts"].items():
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| {scenario} | {label} | {count} |")

    lines.extend([
        "",
        "## Interpretation and next actions",
        "",
        "- **Recall failures:** change retrieval/query construction first; reranking cannot rescue an absent target.",
        "- **Rerank failures:** inspect target rank versus the Top-10 cutoff and tune route fusion or add attribute-aware scoring.",
        "- **Override failures:** reset/rebuild active constraints at the explicit override and protect with scenario regressions.",
        "- **Boundary context:** remains a secondary label so it does not double-count the retrieval/ranking root cause.",
        "- **Filter failures:** are not separately observable because the current baseline has no filter stage.",
        "",
        "## Failure details",
        "",
        "| Sample | Scenario | Primary | Secondary | Turns | Best route rank | Best pool rank | Override turn |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ])
    for item in report["failures"]:
        secondary = ", ".join(item["secondary_labels"]) or "—"
        values = [
            item["sample_id"], item["scenario_type"], item["primary_failure"], secondary,
            str(item["turn_count"]), str(item["best_route_rank"] or "—"),
            str(item["best_candidate_pool_rank"] or "—"), str(item["override_turn"] or "—"),
        ]
        lines.append("| " + " | ".join(values) + " |")

    lines.extend([
        "",
        "## Scope, method, and limitations",
        "",
        f"- Result source: `{result_path.as_posix()}`",
        f"- Trace source: `{traces_path.as_posix()}`",
        f"- Trace coverage for misses: {report['matched_trace_count']}/{miss_count}.",
        "- Verified labels are based on recorded target ranks, not semantic guesses from product text.",
        "- Override attribution is conservative: it requires an explicit override phrase, pre-override target viability, and no post-override recommendation.",
        "- This public 200-session set is diagnostic evidence, not an unbiased hidden-test estimate.",
        "- The JSON companion contains per-turn evidence, final state, confidence, reason, and proposed next action for every miss.",
        "",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify baseline misses from diagnostic traces")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, default=ROOT / "analysis" / "agent_failure_report.json")
    parser.add_argument("--output-md", type=Path, default=ROOT / "analysis" / "agent_failure_report.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = analyze(read_json(args.result), read_jsonl(args.traces))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown_report(report, args.result, args.traces), encoding="utf-8")
    print(json.dumps({
        "miss_count": report["miss_count"],
        "primary_failure_counts": report["primary_failure_counts"],
        "output_json": str(args.output_json),
        "output_md": str(args.output_md),
    }, indent=2))


if __name__ == "__main__":
    main()
