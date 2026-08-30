"""Run the stable Question Policy + Context intent-override demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from starter.agent import Agent


FIRST_REQUEST = "I need women's running shoes under $120 and prefer lightweight options."
NEW_INFORMATION = {
    "material": "Mesh would be ideal.",
    "feature": "Breathability matters most.",
    "color": "Black would be ideal.",
    "style": "I prefer an athletic style.",
    "size": "I need size 8.",
    "use_case": "They are for road running.",
    "budget": "Please keep it under $120.",
    "brand": "I have no brand preference.",
    "other": "Comfort is the other priority.",
    None: "Comfort is the other priority.",
}
OVERRIDE_REQUEST = (
    "Actually, ignore my earlier preference. What I need is waterproof winter boots under $180."
)
DEMO_TARGET = "B08YJ9W4T4"


def _recommendations_with_titles(agent: Agent, response: dict) -> list[dict]:
    result: list[dict] = []
    for item in response["recommendations"][:3]:
        parent_asin = str(item["parent_asin"])
        row = agent.connection.execute(
            "SELECT title FROM products WHERE parent_asin = ?",
            (parent_asin,),
        ).fetchone()
        result.append(
            {
                "parent_asin": parent_asin,
                "title": str(row[0]) if row else None,
                "score": item.get("score"),
            }
        )
    return result


def _decision(agent: Agent, session_id: str) -> dict:
    decision = agent._sessions[session_id].last_question_decision
    return {
        "ask_attribute": decision.ask_attribute if decision else None,
        "question": decision.message if decision else None,
        "reason": decision.reason if decision else None,
        "score_order": [
            {"attribute": score.attribute, "value": score.value}
            for score in (decision.scores if decision else ())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output", default="analysis/question_policy_demo_output.json")
    args = parser.parse_args()

    agent = Agent(args.catalog, question_policy_mode="dynamic")
    session_id = "shierly_override_demo"
    agent.reset(
        session_id,
        {
            "preference_tags": ["comfort", "weather"],
            "rating_style": "quality-focused",
            "purchase_frequency": "occasional",
            "average_prior_rating": 4.4,
        },
    )

    first = agent.respond(session_id, FIRST_REQUEST, 1, 10)
    first_state = agent._sessions[session_id].conversation_state.to_dict(include_empty=False)
    second_message = NEW_INFORMATION[first["ask_attribute"]]
    second = agent.respond(session_id, second_message, 2, 10)
    before_override = agent._sessions[session_id].conversation_state.to_dict(include_empty=False)
    third = agent.respond(session_id, OVERRIDE_REQUEST, 3, 10)
    after_override = agent._sessions[session_id].conversation_state.to_dict(include_empty=False)

    before_ids = {str(item["parent_asin"]) for item in second["recommendations"]}
    after_ids = {str(item["parent_asin"]) for item in third["recommendations"]}
    overridden_slots = {
        "category": {
            "before": before_override.get("category"),
            "after": after_override.get("category"),
        },
        "price_max": {
            "before": before_override.get("hard_constraints", {}).get("price_max"),
            "after": after_override.get("hard_constraints", {}).get("price_max"),
        },
        "feature": {
            "before": before_override.get("soft_preferences", {}).get("feature"),
            "after": after_override.get("hard_constraints", {}).get("feature"),
        },
        "audience": {
            "before": before_override.get("hard_constraints", {}).get("audience"),
            "after": after_override.get("hard_constraints", {}).get("audience"),
        },
        "material": {
            "before": before_override.get("soft_preferences", {}).get("material"),
            "after": after_override.get("soft_preferences", {}).get("material"),
        },
    }
    after_ranked_ids = [str(item["parent_asin"]) for item in third["recommendations"]]
    target_rank = after_ranked_ids.index(DEMO_TARGET) + 1 if DEMO_TARGET in after_ranked_ids else None

    payload = {
        "scenario": "women's running shoes -> clarification -> new information -> waterproof winter boots",
        "profile_is_low_weight_only": True,
        "turns": [
            {
                "turn": 1,
                "input": FIRST_REQUEST,
                "state": first_state,
                "decision": {"ask_attribute": first["ask_attribute"], "question": first["message"]},
                "top3": _recommendations_with_titles(agent, first),
            },
            {
                "turn": 2,
                "input": second_message,
                "state": before_override,
                "decision": {"ask_attribute": second["ask_attribute"], "question": second["message"]},
                "top3": _recommendations_with_titles(agent, second),
            },
            {
                "turn": 3,
                "input": OVERRIDE_REQUEST,
                "route": "buying",
                "state": after_override,
                "overridden_slots": overridden_slots,
                "decision": _decision(agent, session_id),
                "top3": _recommendations_with_titles(agent, third),
                "demo_target": {"parent_asin": DEMO_TARGET, "rank": target_rank},
            },
        ],
        "retrieval_routes": [
            {"name": "current_scope", "weight": 1.40},
            {"name": "latest_message", "weight": 0.85},
            {"name": "category_anchor", "weight": 0.25},
        ],
        "candidate_pool_change": {
            "top10_overlap": len(before_ids & after_ids),
            "top10_new_after_override": len(after_ids - before_ids),
        },
        "fallback_triggered": len(third["recommendations"]) < 10,
        "checks": {
            "category_changed_to_boots": str(after_override.get("category") or "").endswith("boots"),
            "old_lightweight_removed": "lightweight" not in json.dumps(after_override).lower(),
            "old_material_removed": before_override.get("soft_preferences", {}).get("material")
            != after_override.get("soft_preferences", {}).get("material"),
            "override_dynamic_question_has_reason": bool(_decision(agent, session_id)["reason"]),
            "target_reaches_top3": target_rank is not None and target_rank <= 3,
            "target_was_not_in_pre_override_top10": DEMO_TARGET not in before_ids,
        },
    }
    if not all(payload["checks"].values()):
        raise RuntimeError(f"Demo check failed: {payload['checks']}")
    Path(args.output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": args.output, "checks": payload["checks"]}, indent=2))


if __name__ == "__main__":
    main()
