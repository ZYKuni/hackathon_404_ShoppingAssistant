from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_seed(seed: int, *parts: str) -> int:
    value = "\0".join([str(seed), *parts]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number} of {path}") from error
            rows.append(row)
    return rows


def _counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    scenarios = Counter(str(row["scenario_type"]) for row in rows)
    difficulties = Counter(str(row.get("difficulty_bucket", "unknown")) for row in rows)
    strata = Counter(
        f"{row['scenario_type']}::{row.get('difficulty_bucket', 'unknown')}" for row in rows
    )
    return {
        "scenario_type": dict(sorted(scenarios.items())),
        "difficulty_bucket": dict(sorted(difficulties.items())),
        "scenario_difficulty": dict(sorted(strata.items())),
    }


def build_folds(rows: list[dict[str, Any]], n_splits: int, seed: int) -> list[dict[str, Any]]:
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    sample_ids = [str(row.get("sample_id", "")) for row in rows]
    if any(not sample_id for sample_id in sample_ids):
        raise ValueError("every row must contain a non-empty sample_id")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id values must be unique")

    by_scenario: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        scenario = str(row["scenario_type"])
        difficulty = str(row.get("difficulty_bucket", "unknown"))
        by_scenario[scenario][difficulty].append(row)

    assigned: list[list[dict[str, Any]]] = [[] for _ in range(n_splits)]
    for scenario in sorted(by_scenario):
        position = 0
        for difficulty in sorted(by_scenario[scenario]):
            group = sorted(by_scenario[scenario][difficulty], key=lambda row: str(row["sample_id"]))
            random.Random(stable_seed(seed, scenario, difficulty)).shuffle(group)
            for row in group:
                assigned[position % n_splits].append(row)
                position += 1

    folds: list[dict[str, Any]] = []
    for fold_index, fold_rows in enumerate(assigned):
        fold_rows.sort(key=lambda row: str(row["sample_id"]))
        folds.append({
            "fold": fold_index,
            "sample_ids": [str(row["sample_id"]) for row in fold_rows],
            "counts": _counts(fold_rows),
        })
    validate_folds(rows, folds, n_splits)
    return folds


def validate_folds(
    rows: list[dict[str, Any]], folds: list[dict[str, Any]], n_splits: int
) -> None:
    if len(folds) != n_splits:
        raise ValueError(f"expected {n_splits} folds, found {len(folds)}")
    expected_ids = {str(row["sample_id"]) for row in rows}
    assigned_ids = [sample_id for fold in folds for sample_id in fold["sample_ids"]]
    if len(assigned_ids) != len(set(assigned_ids)):
        raise ValueError("a sample is assigned to more than one fold")
    if set(assigned_ids) != expected_ids:
        raise ValueError("fold assignments do not cover the dataset exactly")

    scenarios = sorted({str(row["scenario_type"]) for row in rows})
    for scenario in scenarios:
        counts = [fold["counts"]["scenario_type"].get(scenario, 0) for fold in folds]
        if max(counts) - min(counts) > 1:
            raise ValueError(f"scenario {scenario} is not balanced across folds: {counts}")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def assignment_sha256(folds: list[dict[str, Any]]) -> str:
    assignment = {
        str(fold["fold"]): fold["sample_ids"]
        for fold in folds
    }
    encoded = json.dumps(assignment, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create deterministic stratified evaluation folds")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data" / "public_set.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis" / "folds.json")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=404)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_jsonl(args.dataset)
    folds = build_folds(rows, args.folds, args.seed)
    payload = {
        "schema_version": 1,
        "seed": args.seed,
        "n_splits": args.folds,
        "stratify_by": ["scenario_type", "difficulty_bucket"],
        "dataset_path": display_path(args.dataset),
        "dataset_sha256": file_sha256(args.dataset),
        "assignment_sha256": assignment_sha256(folds),
        "folds": folds,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": display_path(args.output),
        "dataset_sha256": payload["dataset_sha256"],
        "assignment_sha256": payload["assignment_sha256"],
        "fold_counts": [fold["counts"] for fold in folds],
    }, indent=2))


if __name__ == "__main__":
    main()
