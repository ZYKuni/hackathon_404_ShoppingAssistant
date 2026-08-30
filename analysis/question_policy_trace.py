"""Write reproducible per-turn traces for question-policy review.

This is an analysis-only tool.  Ground-truth rank is inspected after the Agent
responds and is never passed back into the Agent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
    normalize_recommendations,
)
from starter.agent import Agent


def trace_session(agent: Agent, sample: dict, catalog_ids: set[str], categories: dict, products: dict) -> dict:
    session_id = f"trace_{sample['sample_id']}"
    agent.reset(session_id, sample["user_profile"])
    target = str(sample["ground_truth"]["parent_asin"])
    intent_card, behavior = materialize_hidden_fields(sample, products)
    effective_sample = {**sample, "intent_card": intent_card, "behavior": behavior}
    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    user_message = initial_message(
        effective_sample,
        coarse_category(categories.get(target, [])),
        disclosed,
    )
    turns: list[dict] = []

    for turn in range(1, MAX_TURNS + 1):
        response = agent.respond(session_id, user_message, turn, TOP_K)
        ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
        state = agent._sessions[session_id]
        decision = state.last_question_decision
        target_rank = ranked.index(target) + 1 if override_applied and target in ranked else None
        turns.append(
            {
                "turn": turn,
                "user_message": user_message,
                "ask_attribute": response.get("ask_attribute"),
                "question": response.get("message"),
                "decision_reason": decision.reason if decision else None,
                "score_order": [
                    {"attribute": score.attribute, "value": score.value}
                    for score in (decision.scores if decision else ())
                ],
                "target_rank": target_rank,
                "override_applied": override_applied,
            }
        )
        if target_rank is not None or turn == MAX_TURNS:
            break

        override = effective_sample.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            user_message = str(override.get("message", "Actually, please ignore my earlier preference."))
        else:
            user_message, boundary_used = customer_reply(
                effective_sample,
                response.get("ask_attribute"),
                disclosed,
                boundary_used,
            )

    return {
        "sample_id": sample["sample_id"],
        "scenario_type": sample["scenario_type"],
        "target": target,
        "question_order": [turn["ask_attribute"] for turn in turns],
        "first_hit_turn": next((turn["turn"] for turn in turns if turn["target_rank"] is not None), None),
        "turns": turns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--mode", choices=("safe", "fixed", "dynamic"), default="dynamic")
    parser.add_argument("--profile", choices=("on", "off"), default="on")
    parser.add_argument("--sample-id", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    if args.sample_id:
        selected = set(args.sample_id)
        samples = [sample for sample in samples if sample["sample_id"] in selected]
    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(
        args.catalog,
        question_policy_mode=args.mode,
        enable_profile_context=args.profile == "on",
    )
    traces = [trace_session(agent, sample, catalog_ids, categories, products) for sample in samples]
    Path(args.output).write_text(json.dumps(traces, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": args.mode, "session_count": len(traces), "output": args.output}, indent=2))


if __name__ == "__main__":
    main()
