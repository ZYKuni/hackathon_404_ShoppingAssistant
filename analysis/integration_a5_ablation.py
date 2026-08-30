"""Evaluate safe formal-ranker activation policies in one 200-session pass."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluator.local_evaluator import (  # noqa: E402
    MAX_TURNS,
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
)
from starter.agent import Agent  # noqa: E402


ALPHAS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _reorder(legacy: list[str], formal: list[str], alpha: float) -> list[str]:
    formal_rank = {asin: rank for rank, asin in enumerate(formal, 1)}
    legacy_rank = {asin: rank for rank, asin in enumerate(legacy, 1)}
    return sorted(
        legacy,
        key=lambda asin: (
            -(
                (1.0 - alpha) / legacy_rank[asin]
                + alpha / formal_rank.get(asin, 201)
            ),
            asin,
        ),
    )


def _summary(rows: list[dict]) -> dict:
    hit = sum(row["first_hit_turn"] is not None for row in rows) / len(rows)
    mrr = statistics.fmean(row["reciprocal_rank"] for row in rows)
    mttc = statistics.fmean(
        row["first_hit_turn"] if row["first_hit_turn"] is not None else 11 for row in rows
    )
    efficiency = max(0.0, min(1.0, (11.0 - mttc) / 10.0))
    return {
        "hit_rate_at_10": round(hit, 6),
        "mrr": round(mrr, 6),
        "mttc": round(mttc, 6),
        "technical_score": round(0.5 * hit + 0.3 * mrr + 0.2 * efficiency, 6),
    }


def main() -> None:
    catalog_path = "data/catalog.jsonl"
    samples = load_jsonl("data/public_set.jsonl")
    _, categories, products = catalog_index(catalog_path)
    agent = Agent.with_local_pipeline(catalog_path)
    rows_by_policy: dict[str, list[dict]] = {
        **{f"legacy_set_alpha_{alpha:.1f}": [] for alpha in ALPHAS},
        "formal": [],
    }
    try:
        for sample in samples:
            session_id = f"ablation_{sample['sample_id']}"
            agent.reset(session_id, sample["user_profile"])
            target = str(sample["ground_truth"]["parent_asin"])
            card, behavior = materialize_hidden_fields(sample, products)
            effective = {**sample, "intent_card": card, "behavior": behavior}
            disclosed: set[str] = set()
            boundary_used = False
            override_applied = sample["scenario_type"] != "intent_override"
            message = initial_message(effective, coarse_category(categories[target]), disclosed)
            turn_rankings: dict[str, list[list[str]]] = {key: [] for key in rows_by_policy}
            for turn in range(1, MAX_TURNS + 1):
                response = agent.respond(session_id, message, turn, TOP_K)
                state = agent._sessions[session_id]
                formal = [item["parent_asin"] for item in response["recommendations"]]
                legacy = [
                    item["parent_asin"] for item in agent._rank(state, message, TOP_K)
                ]
                turn_rankings["formal"].append(formal)
                for alpha in ALPHAS:
                    turn_rankings[f"legacy_set_alpha_{alpha:.1f}"].append(
                        _reorder(legacy, formal, alpha)
                    )
                if turn == MAX_TURNS:
                    break
                override = effective.get("behavior", {}).get("override") or {}
                if not override_applied and turn + 1 == int(override.get("turn", 3)):
                    override_applied = True
                    new_value = str(override.get("new_value", ""))
                    if new_value:
                        disclosed.add(new_value)
                    message = str(override.get("message", "Actually, ignore my earlier preference."))
                else:
                    message, boundary_used = customer_reply(
                        effective, response.get("ask_attribute"), disclosed, boundary_used
                    )
            override_turn = int((behavior.get("override") or {}).get("turn", 1))
            for policy, rankings in turn_rankings.items():
                eligible = override_turn if sample["scenario_type"] == "intent_override" else 1
                hits = [
                    (turn, ranking.index(target) + 1)
                    for turn, ranking in enumerate(rankings, 1)
                    if turn >= eligible and target in ranking
                ]
                first_turn, rank = hits[0] if hits else (None, None)
                rows_by_policy[policy].append({
                    "scenario": sample["scenario_type"],
                    "first_hit_turn": first_turn,
                    "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
                })
    finally:
        agent.connection.close()
    result = {}
    for policy, rows in rows_by_policy.items():
        result[policy] = {
            "overall": _summary(rows),
            "intent_override": _summary(
                [row for row in rows if row["scenario"] == "intent_override"]
            ),
        }
    Path("analysis/integration_a5_ablation.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
