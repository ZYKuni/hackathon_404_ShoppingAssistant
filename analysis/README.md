# Catalog analysis

Run the reproducible catalog profile from the repository root:

```powershell
python analysis/catalog_analysis.py
```

The command reads `data/catalog.jsonl` and `data/public_set.jsonl`, then writes:

- `analysis/catalog_analysis_report.md`: readable findings and engineering recommendations;
- `analysis/catalog_analysis_metrics.json`: machine-readable metrics for later comparisons.

Only the Python standard library is required.

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

## Vector retrieval and semantic Top-N ranking

Run the Todo 4.4/4.6 fixed-pool ablation with:

```bash
python3 analysis/semantic_a6_benchmark.py
```

Results and the default-on/default-off decision are documented in
`analysis/AARON_VECTOR_SEMANTIC_A6_REPORT.md`.
