from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import os
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.diagnostics import validate_diagnostic_trace


DEFAULT_CONFIG = ROOT / "analysis" / "configs" / "integrated_guarded_rerank.json"
DEFAULT_REGISTRY = ROOT / "analysis" / "experiment_registry.jsonl"


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _round_ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 3)


@dataclass
class InstrumentedAgent:
    inner: Any
    sample_contexts: list[dict[str, Any]] = field(default_factory=list)
    respond_events: list[dict[str, Any]] = field(default_factory=list)
    reset_seconds: list[float] = field(default_factory=list)
    session_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)
    reset_index: int = 0

    def reset(self, session_id: str, user_profile: dict) -> None:
        if self.reset_index < len(self.sample_contexts):
            self.session_contexts[session_id] = self.sample_contexts[self.reset_index]
        self.reset_index += 1
        started = time.perf_counter()
        try:
            self.inner.reset(session_id, user_profile)
        finally:
            self.reset_seconds.append(time.perf_counter() - started)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        started = time.perf_counter()
        failed = False
        try:
            return self.inner.respond(session_id, user_message, turn, top_k)
        except Exception:
            failed = True
            raise
        finally:
            context = self.session_contexts.get(session_id, {})
            self.respond_events.append({
                "session_id": session_id,
                "sample_id": context.get("sample_id"),
                "scenario_type": context.get("scenario_type"),
                "turn": turn,
                "latency_ms": _round_ms(time.perf_counter() - started),
                "failed": failed,
            })

    def collect_diagnostic_traces(self) -> list[dict[str, Any]]:
        provider = getattr(self.inner, "get_diagnostic_trace", None)
        traces: list[dict[str, Any]] = []
        for session_id, context in self.session_contexts.items():
            if not callable(provider):
                traces.append({
                    "diagnostics_available": False,
                    "runtime_session_id": session_id,
                    "evaluation_context": context,
                    "turns": [],
                })
                continue
            try:
                trace = provider(session_id)
                validate_diagnostic_trace(trace)
                annotated = annotate_diagnostic_trace(trace, context)
                annotated["diagnostics_available"] = True
            except Exception as error:
                annotated = {
                    "diagnostics_available": False,
                    "runtime_session_id": session_id,
                    "evaluation_context": context,
                    "diagnostic_error": f"{type(error).__name__}: {error}",
                    "turns": [],
                }
            traces.append(annotated)
        return traces


def _target_rank(items: list[Any], target: str) -> int | None:
    for rank, item in enumerate(items, start=1):
        parent_asin = item.get("parent_asin") if isinstance(item, dict) else item
        if str(parent_asin) == target:
            return rank
    return None


def annotate_diagnostic_trace(
    trace: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    annotated = json.loads(json.dumps(trace))
    annotated["runtime_session_id"] = annotated.pop("session_id")
    annotated["evaluation_context"] = dict(context)
    target = str(context.get("target_parent_asin", ""))
    for turn in annotated.get("turns", []):
        ranking = turn["ranking"]
        for route in ranking.get("routes", []):
            route["target_rank"] = _target_rank(route.get("candidate_ids", []), target)
        ranking["candidate_pool_target_rank"] = _target_rank(
            ranking.get("candidate_pool", []), target
        )
        ranking["recommendation_target_rank"] = _target_rank(
            ranking.get("recommendations", []), target
        )
    return annotated


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def git_metadata() -> dict[str, Any]:
    status = _git_value("status", "--porcelain")
    return {
        "commit": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("branch", "--show-current"),
        "dirty": bool(status),
        "dirty_files": status.splitlines() if status else [],
    }


def _windows_memory() -> dict[str, int] | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    )
    if ok:
        return {
            "working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
        }

    # Some Windows/Python combinations do not expose the legacy PSAPI alias.
    # PowerShell reads the same process counters without adding a dependency.
    command = (
        f"$p = Get-Process -Id {os.getpid()}; "
        "Write-Output $p.WorkingSet64; Write-Output $p.PeakWorkingSet64"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
        values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
    if len(values) != 2:
        return None
    return {"working_set_bytes": values[0], "peak_working_set_bytes": values[1]}


def _expand_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**replacements)
    if isinstance(value, list):
        return [_expand_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _expand_placeholders(item, replacements) for key, item in value.items()}
    return value


def load_agent(config: dict[str, Any], catalog_path: Path) -> Any:
    agent_config = config["agent"]
    module = importlib.import_module(agent_config["module"])
    agent_class = getattr(module, agent_config.get("class", "Agent"))
    kwargs = _expand_placeholders(
        agent_config.get("kwargs", {}), {"catalog": str(catalog_path.resolve())}
    )
    return agent_class(**kwargs)


def summarize_runtime(
    instrumented: InstrumentedAgent,
    initialization_seconds: float,
    evaluation_seconds: float,
    python_peak_bytes: int | None,
    memory_before: dict[str, int] | None,
    memory_after: dict[str, int] | None,
) -> dict[str, Any]:
    latencies = [event["latency_ms"] for event in instrumented.respond_events]
    return {
        "initialization_ms": _round_ms(initialization_seconds),
        "evaluation_ms": _round_ms(evaluation_seconds),
        "respond_calls": len(latencies),
        "respond_latency_ms": {
            "p50": None if not latencies else round(percentile(latencies, 0.50) or 0.0, 3),
            "p95": None if not latencies else round(percentile(latencies, 0.95) or 0.0, 3),
            "max": None if not latencies else round(max(latencies), 3),
        },
        "failed_respond_calls": sum(bool(event["failed"]) for event in instrumented.respond_events),
        "python_tracemalloc_peak_bytes": python_peak_bytes,
        "process_memory_before": memory_before,
        "process_memory_after": memory_after,
        "memory_measurement_note": (
            "Windows working-set values include native memory and the process lifetime peak. "
            "tracemalloc is disabled by default because it distorts latency."
        ),
    }


def compare_sessions(current: list[dict], baseline: list[dict] | None) -> dict[str, Any]:
    if baseline is None:
        return {"baseline_available": False, "gained_sessions": [], "lost_sessions": []}
    before = {item["sample_id"]: item for item in baseline}
    after = {item["sample_id"]: item for item in current}
    shared = sorted(before.keys() & after.keys())
    gained = [sample_id for sample_id in shared if not before[sample_id]["hit"] and after[sample_id]["hit"]]
    lost = [sample_id for sample_id in shared if before[sample_id]["hit"] and not after[sample_id]["hit"]]
    return {
        "baseline_available": True,
        "shared_session_count": len(shared),
        "gained_sessions": gained,
        "lost_sessions": lost,
    }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _load_baseline_sessions(path: Path | None) -> list[dict] | None:
    if path is None:
        return None
    payload = _read_json(path)
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError(f"baseline file does not contain a sessions list: {path}")
    return sessions


def load_fold_sample_ids(
    path: Path, fold_index: int, expected_dataset_sha256: str | None = None
) -> set[str]:
    payload = _read_json(path)
    if (
        expected_dataset_sha256 is not None
        and payload.get("dataset_sha256") != expected_dataset_sha256
    ):
        raise ValueError(
            f"fold file dataset hash does not match the selected dataset: {path}"
        )
    folds = payload.get("folds")
    if not isinstance(folds, list):
        raise ValueError(f"fold file does not contain a folds list: {path}")
    matching = [item for item in folds if item.get("fold") == fold_index]
    if len(matching) != 1:
        raise ValueError(f"expected exactly one fold {fold_index} in {path}")
    sample_ids = matching[0].get("sample_ids")
    if not isinstance(sample_ids, list) or not all(isinstance(item, str) for item in sample_ids):
        raise ValueError(f"fold {fold_index} has invalid sample_ids in {path}")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError(f"fold {fold_index} contains duplicate sample IDs")
    return set(sample_ids)


def make_experiment_id(config: dict[str, Any], commit: str | None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_commit = (commit or "nogit")[:7]
    safe_name = "".join(character if character.isalnum() else "-" for character in config["name"])
    return f"{timestamp}_{safe_name}_{short_commit}"


def append_registry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def run_experiment(
    config_path: Path,
    catalog_path: Path,
    dataset_path: Path,
    output_root: Path,
    registry_path: Path,
    baseline_path: Path | None = None,
    folds_path: Path | None = None,
    fold_index: int | None = None,
    experiment_id: str | None = None,
    register: bool = True,
    metadata_override: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    config = _read_json(config_path)
    metadata = dict(metadata_override) if metadata_override is not None else git_metadata()
    experiment_id = experiment_id or make_experiment_id(config, metadata["commit"])
    output_dir = output_root / experiment_id
    if output_dir.exists():
        raise FileExistsError(f"experiment output already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    seed = int(config.get("seed", 404))
    random.seed(seed)
    config_hash = file_sha256(config_path)
    catalog_hash = file_sha256(catalog_path)
    dataset_hash = file_sha256(dataset_path)
    samples = load_jsonl(dataset_path)
    if (folds_path is None) != (fold_index is None):
        raise ValueError("--folds-file and --fold must be provided together")
    if folds_path is not None and fold_index is not None:
        selected_ids = load_fold_sample_ids(folds_path, fold_index, dataset_hash)
        available_ids = {str(sample["sample_id"]) for sample in samples}
        missing = sorted(selected_ids - available_ids)
        if missing:
            raise ValueError(f"fold {fold_index} references missing sample IDs: {missing[:5]}")
        samples = [sample for sample in samples if str(sample["sample_id"]) in selected_ids]
        if len(samples) != len(selected_ids):
            raise ValueError("dataset contains duplicate sample IDs selected by the fold")
    catalog_ids, categories, products = catalog_index(catalog_path)
    sample_contexts = [
        {
            "sample_id": str(sample["sample_id"]),
            "scenario_type": str(sample["scenario_type"]),
            "target_parent_asin": str(sample["ground_truth"]["parent_asin"]),
        }
        for sample in samples
    ]

    memory_before = _windows_memory()
    measure_python_allocations = bool(config.get("measure_python_allocations", False))
    if measure_python_allocations:
        tracemalloc.start()
    init_started = time.perf_counter()
    inner_agent = load_agent(config, catalog_path)
    initialization_seconds = time.perf_counter() - init_started
    agent = InstrumentedAgent(inner_agent, sample_contexts=sample_contexts)

    evaluation_started = time.perf_counter()
    try:
        result = evaluate(agent, samples, catalog_ids, categories, products)
    finally:
        connection = getattr(inner_agent, "connection", None)
        if connection is not None:
            connection.close()
    evaluation_seconds = time.perf_counter() - evaluation_started
    python_peak_bytes = None
    if measure_python_allocations:
        _, python_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    memory_after = _windows_memory()

    sessions = result.pop("sessions")
    diagnostic_traces = agent.collect_diagnostic_traces()
    runtime = summarize_runtime(
        agent,
        initialization_seconds,
        evaluation_seconds,
        python_peak_bytes,
        memory_before,
        memory_after,
    )
    comparison = compare_sessions(sessions, _load_baseline_sessions(baseline_path))
    environment = {
        **metadata,
        "python": sys.version,
        "platform": platform.platform(),
        "config_path": display_path(config_path),
        "config_sha256": config_hash,
        "catalog_path": display_path(catalog_path),
        "catalog_sha256": catalog_hash,
        "dataset_path": display_path(dataset_path),
        "dataset_sha256": dataset_hash,
        "seed": seed,
        "folds_path": None if folds_path is None else display_path(folds_path),
        "folds_sha256": None if folds_path is None else file_sha256(folds_path),
        "fold": fold_index,
        "sqlite_version": sqlite3.sqlite_version,
    }
    entry = {
        "experiment_id": experiment_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "name": config["name"],
        "description": config.get("description", ""),
        "commit": metadata["commit"],
        "branch": metadata["branch"],
        "dirty": metadata["dirty"],
        "config": config,
        "metrics": result,
        "runtime": runtime,
        "comparison": comparison,
        "diagnostics": {
            "trace_count": len(diagnostic_traces),
            "available_count": sum(
                bool(trace.get("diagnostics_available")) for trace in diagnostic_traces
            ),
        },
        "environment": environment,
        "known_risks": config.get("known_risks", []),
    }

    _write_json(output_dir / "config.json", config)
    _write_json(output_dir / "metrics.json", result)
    _write_json(output_dir / "result.json", {**result, "sessions": sessions})
    _write_json(output_dir / "runtime.json", runtime)
    _write_json(output_dir / "environment.json", environment)
    _write_json(output_dir / "comparison.json", comparison)
    _write_jsonl(output_dir / "sessions.jsonl", sessions)
    _write_jsonl(output_dir / "runtime_events.jsonl", agent.respond_events)
    _write_jsonl(output_dir / "diagnostic_traces.jsonl", diagnostic_traces)
    if register:
        append_registry(registry_path, entry)
    return output_dir, entry


def aggregate_cross_validation(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        raise ValueError("cannot aggregate an empty cross-validation run")

    def summarize(values: list[float]) -> dict[str, float]:
        return {
            "mean": round(statistics.fmean(values), 6),
            "std": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
        }

    metric_names = (
        "hit_rate_at_10", "mrr", "mttc", "efficiency", "recommended_technical_score"
    )
    overall = {
        name: summarize([float(entry["metrics"][name]) for entry in entries])
        for name in metric_names
    }
    scenario_names = sorted(entries[0]["metrics"]["scenario_metrics"])
    scenario_metrics: dict[str, dict[str, dict[str, float]]] = {}
    for scenario in scenario_names:
        scenario_metrics[scenario] = {
            name: summarize([
                float(entry["metrics"]["scenario_metrics"][scenario][name])
                for entry in entries
            ])
            for name in ("hit_rate_at_10", "mrr", "mttc")
        }
    return {
        "fold_count": len(entries),
        "experiment_ids": [entry["experiment_id"] for entry in entries],
        "commit": entries[0]["commit"],
        "dirty": entries[0]["dirty"],
        "overall": overall,
        "scenario_metrics": scenario_metrics,
    }


def fold_count(path: Path) -> int:
    payload = _read_json(path)
    count = payload.get("n_splits")
    if not isinstance(count, int) or count < 2:
        raise ValueError(f"fold file has an invalid n_splits value: {path}")
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and register a reproducible Agent experiment")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.jsonl")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data" / "public_set.jsonl")
    parser.add_argument("--output-root", type=Path, default=ROOT / "analysis" / "runs")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--folds-file", type=Path)
    parser.add_argument("--fold", type=int)
    parser.add_argument("--all-folds", action="store_true")
    parser.add_argument("--experiment-id")
    parser.add_argument("--no-register", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.all_folds:
        if args.folds_file is None or args.fold is not None:
            raise ValueError("--all-folds requires --folds-file and cannot be combined with --fold")
        config = _read_json(args.config)
        frozen_metadata = git_metadata()
        base_id = args.experiment_id or f"{make_experiment_id(config, frozen_metadata['commit'])}_cv"
        entries: list[dict[str, Any]] = []
        output_dirs: list[str] = []
        for current_fold in range(fold_count(args.folds_file)):
            output_dir, entry = run_experiment(
                config_path=args.config,
                catalog_path=args.catalog,
                dataset_path=args.dataset,
                output_root=args.output_root,
                registry_path=args.registry,
                baseline_path=args.baseline,
                folds_path=args.folds_file,
                fold_index=current_fold,
                experiment_id=f"{base_id}_fold{current_fold}",
                register=not args.no_register,
                metadata_override=frozen_metadata,
            )
            entries.append(entry)
            output_dirs.append(str(output_dir))
        summary = aggregate_cross_validation(entries)
        summary_path = args.output_root / f"{base_id}_summary.json"
        _write_json(summary_path, summary)
        print(json.dumps({
            "cross_validation_id": base_id,
            "output_dirs": output_dirs,
            "summary_path": str(summary_path),
            "summary": summary,
        }, indent=2))
        return

    output_dir, entry = run_experiment(
        config_path=args.config,
        catalog_path=args.catalog,
        dataset_path=args.dataset,
        output_root=args.output_root,
        registry_path=args.registry,
        baseline_path=args.baseline,
        folds_path=args.folds_file,
        fold_index=args.fold,
        experiment_id=args.experiment_id,
        register=not args.no_register,
    )
    summary = {
        "experiment_id": entry["experiment_id"],
        "output_dir": str(output_dir),
        "commit": entry["commit"],
        "dirty": entry["dirty"],
        "metrics": entry["metrics"],
        "runtime": entry["runtime"],
        "comparison": entry["comparison"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
