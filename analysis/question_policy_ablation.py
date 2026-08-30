"""Run reproducible fixed/dynamic question-policy and profile ablations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent, CONSERVATIVE_QUESTION_ORDER
from starter.question_policy import FALLBACK_ORDER


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--mode", choices=("safe", "fixed", "dynamic"), default="safe")
    parser.add_argument("--profile", choices=("on", "off"), default="on")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    result = evaluate(
        Agent(
            args.catalog,
            question_policy_mode=args.mode,
            enable_profile_context=args.profile == "on",
        ),
        samples,
        catalog_ids,
        categories,
        products,
    )
    payload = {
        "experiment": {
            "question_policy_mode": args.mode,
            "profile_context": args.profile,
            "fixed_question_order": list(CONSERVATIVE_QUESTION_ORDER),
            "dynamic_fallback_order": {
                route.value: list(order) for route, order in FALLBACK_ORDER.items()
            },
        },
        "metrics": result,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "experiment": payload["experiment"],
                "metrics": {key: value for key, value in result.items() if key != "sessions"},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
