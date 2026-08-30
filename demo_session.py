"""Run a small, deterministic multi-turn demonstration of the submitted Agent."""

from __future__ import annotations

import argparse
import json
import time

from starter.agent import Agent


DEMO_MESSAGES = (
    "I'm looking for women's running shoes, but I'm still exploring.",
    "Lightweight and breathable matter most, preferably cotton.",
    "Actually, ignore my earlier preference. What I need is a black waterproof hiking boot.",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    args = parser.parse_args()

    started = time.perf_counter()
    agent = Agent(args.catalog)
    print(json.dumps({
        "event": "initialized",
        "seconds": round(time.perf_counter() - started, 3),
    }))
    session_id = "submission-demo"
    agent.reset(
        session_id,
        {
            "preference_tags": ["comfort", "outdoor"],
            "purchase_frequency": "medium",
            "rating_style": "selective",
        },
    )
    try:
        for turn, user_message in enumerate(DEMO_MESSAGES, 1):
            turn_started = time.perf_counter()
            response = agent.respond(session_id, user_message, turn, 10)
            print(json.dumps({
                "turn": turn,
                "user": user_message,
                "assistant": response["message"],
                "ask_attribute": response["ask_attribute"],
                "recommendations": [
                    item["parent_asin"] for item in response["recommendations"]
                ],
                "fallbacks": list(agent.pipeline_fallbacks(session_id)),
                "usage": response["usage"],
                "latency_ms": round((time.perf_counter() - turn_started) * 1000.0, 3),
            }))
    finally:
        agent.connection.close()


if __name__ == "__main__":
    main()
