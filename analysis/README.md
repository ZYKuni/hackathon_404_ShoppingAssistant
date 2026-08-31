# Catalog analysis

Run the reproducible catalog profile from the repository root:

```powershell
python analysis/catalog_analysis.py
```

The command reads `data/catalog.jsonl` and `data/public_set.jsonl`, then writes:

- `analysis/catalog_analysis_report.md`: readable findings and engineering recommendations;
- `analysis/catalog_analysis_metrics.json`: machine-readable metrics for later comparisons.

Only the Python standard library is required.

## Reproducible agent experiments

Run the registered baseline from the repository root:

```powershell
python analysis/run_agent_experiments.py --config analysis/configs/baseline.json
```

The runner leaves `evaluator/local_evaluator.py` unchanged and writes one immutable directory under `analysis/runs/` containing:

- the exact config and environment, including commit, branch, dirty state, Python, platform, dataset, and seed;
- aggregate and per-scenario metrics;
- per-session outcomes;
- a complete `result.json` that can be passed directly to a later run with `--baseline`;
- `diagnostic_traces.jsonl` with messages, state, retrieval routes, candidate pools, fallback, and public target ranks when the Agent implements the optional diagnostic contract;
- initialization time, total evaluation time, P50/P95 response latency, and measured memory;
- gained and lost sessions when `--baseline` points to an evaluator JSON result.

Every successful registered run appends one JSON object to `analysis/experiment_registry.jsonl`. Give important runs an explicit stable ID:

```powershell
python analysis/run_agent_experiments.py `
  --config analysis/configs/baseline.json `
  --experiment-id baseline_4363569
```

Compare a later configuration with the frozen baseline:

```powershell
python analysis/run_agent_experiments.py `
  --config analysis/configs/candidate.json `
  --baseline analysis/runs/<baseline-experiment-id>/result.json
```

Create the fixed seed-404 stratified five-fold assignment:

```powershell
python analysis/create_stratified_folds.py
```

Each fold contains 40 sessions: 16 Buying, 16 Browsing, 6 Intent Override, and 2 Boundary. Scenario and difficulty are both used during deterministic assignment. Run one held-out fold with:

```powershell
python analysis/run_agent_experiments.py `
  --config analysis/configs/baseline.json `
  --folds-file analysis/folds.json `
  --fold 0
```

Run all five held-out folds from one frozen Git state and write a mean/standard-deviation summary:

```powershell
python analysis/run_agent_experiments.py `
  --config analysis/configs/baseline.json `
  --folds-file analysis/folds.json `
  --all-folds `
  --experiment-id baseline_cv
```

The experiment environment records SHA-256 hashes for the config, catalog, dataset, and fold assignment so later runs can prove that they used the same artifacts.

An experiment directory is never overwritten. Use a new experiment ID for a new run. A dirty working tree is recorded rather than hidden; official before/after evidence should use committed code.

On Windows, process working set and process-lifetime peak working set include native allocations such as SQLite. Python `tracemalloc` is disabled by default because it changes the latency being measured; enable `measure_python_allocations` in a config only for a separate memory-diagnostic run.

The optional Agent-side contract is documented in `docs/diagnostic_trace_contract.md`. Diagnostics are collected after scoring and never add fields to the official `respond(...)` payload. Public target IDs and target ranks are joined by the experiment runner; they are not passed to the Agent.

The full run takes roughly two minutes on the current 50,000-product catalog because it builds an in-memory FTS5 index and runs the public-session BM25 field ablations. The report includes:

- catalog-versus-target field coverage, including percentage-point and ratio differences;
- scenario and difficulty breakdowns;
- an evaluator data-generation trace;
- hidden-constraint type and source analysis;
- initial-query and full-constraint target ranks;
- single-field and drop-one BM25 ablations for title, categories, features, and details.

You can override every path:

```powershell
python analysis/catalog_analysis.py --catalog path/to/catalog.jsonl --sessions path/to/public_set.jsonl --report report.md --metrics metrics.json
```

## A5 integration evaluation

Run the formal Agent pipeline and its guarded-rerank ablation from the repository root:

```bash
python3 analysis/integration_a5_ablation.py
python3 analysis/integration_a5_benchmark.py
```

The readable conclusions are recorded in `analysis/AARON_INTEGRATION_A5_REPORT.md`.

## Optional dense retrieval experiment

The isolated OFF/SHADOW/ON dense route, immutable embedding builder, minimal
int8 ONNX exporter, and public-set benchmark are documented in
`analysis/DENSE_RETRIEVAL_REPORT.md`. The default `Agent(catalog_path)` remains
the dependency-free MVP until generated assets are packaged and the release
environment gates pass.
