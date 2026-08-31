# Shopping Copilot technical report

## Final submitted method

The submitted `Agent(catalog_path)` is a fully offline, deterministic conversational
retrieval system. Each turn is parsed into a structured state update, routed as
Buying or Browsing, and retrieved from a route-specific BM25 candidate pool. The
formal local ranker uses normalized constraints, exclusions, route evidence,
popularity, and safe aggregate profile fields.

Official mode applies a guarded Top-10 rerank: the validated lexical Top-10 candidate
set is retained, while the formal ranker contributes a bounded `0.4` reciprocal-rank
signal. This improves ordering without sacrificing the proven candidate set. Explicit
intent overrides replace stale product scope, and “no preference” replies are stored
structurally rather than added to positive retrieval text.

The candidate-aware Question Policy is available in SAFE, SHADOW, and DYNAMIC modes.
The submitted default is SAFE: it preserves the validated applied question order.
SHADOW computes candidate-aware decisions without changing output; DYNAMIC remains an
opt-in experiment because it reduced Hit@10 and MRR despite lowering MTTC.

## Reproducible public-development results

All figures below use the organizer's unchanged evaluator, the frozen 50,000-product
catalog, and the released 200-session development set. Ground truth is used only by
the evaluator and offline analysis, never by the Agent.

| Configuration | Hit@10 | MRR | MTTC | Technical Score |
| --- | ---: | ---: | ---: | ---: |
| Frozen legacy BM25/RRF | 0.840000 | 0.476401 | 4.885 | 0.685220 |
| **Submitted integrated SAFE** | **0.855000** | **0.495175** | **4.745** | **0.701152** |

The integrated system gains three sessions (`public_0052`, `public_0071`, and
`public_0084`) and loses none relative to the frozen legacy result.

### Submitted strategy by scenario

| Scenario | N | Hit@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Buying | 80 | 0.887500 | 0.506349 | 4.925000 |
| Browsing | 80 | 0.862500 | 0.488224 | 4.025000 |
| Intent Override | 30 | 0.766667 | 0.437857 | 5.766667 |
| Boundary | 10 | 0.800000 | 0.633333 | 6.000000 |

### Fixed seed-404 five-fold stability

The same frozen configuration was evaluated over the existing five deterministic,
stratified folds. Each fold contains 40 sessions and together they partition the
released set.

| Metric | Mean | Sample standard deviation |
| --- | ---: | ---: |
| Hit@10 | 0.855000 | 0.097468 |
| MRR | 0.495174 | 0.097534 |
| MTTC | 4.745 | 0.488109 |
| Technical Score | 0.701152 | 0.085414 |

These folds describe public-set variability; they are not an estimate of the hidden
800-session evaluation result.

## Failure analysis

The submitted system misses 29 of 200 sessions, down from 32 for the frozen legacy
baseline. Diagnostic traces cover all 29 misses.

| Primary diagnostic label | Misses | Share |
| --- | ---: | ---: |
| Rerank failure | 25 | 86.2% |
| Override failure | 3 | 10.3% |
| Recall failure | 1 | 3.4% |

Twelve targets are near the final cutoff with their best merged-pool rank between 11
and 20. Only one target is never retrieved, so global recall expansion is not the
first optimization priority. The generic trace currently emphasizes the guarded
lexical routes; it cannot isolate formal filtering as a separate primary label, so
the rerank category should be interpreted as a first-pass Top-10 selection diagnosis.

## Runtime, memory, and cost

The clean-clone Python 3.14.6 Windows run reports:

- cold Agent initialization: `62.534 s`;
- complete 200-session evaluation: `348.570 s`;
- 920 `respond` calls with P50/P95 latency of `234.838/1160.173 ms`;
- process-lifetime peak working set: approximately `502 MiB`;
- failed Agent calls: `0`;
- prompt/completion tokens: `0/0`;
- external API/model cost: `USD 0`.

Initialization scans, indexes, and normalizes the full catalog. Runtime figures are
development-machine observations, not guarantees for organizer hardware.

## Fallback and offline behavior

The submitted path uses no generative LLM, external embedding API, vector database,
network service, API key, or environment secret. Official mode has deterministic
routing, retrieval, ranking, legacy safety, empty-result, and popularity fallbacks.
Dense retrieval remains OFF in the submitted configuration.

## Limitations

- Long-tail catalog aliases and category-specific size normalization remain incomplete.
- The SAFE default retains fixed applied clarification order; candidate-aware questions
  are not promoted until they pass overall and per-scenario non-regression gates.
- Lexical retrieval remains weaker on paraphrases absent from catalog text.
- Cold initialization and P95 latency are materially higher than the legacy baseline.
- Public-set optimization may not generalize to the hidden evaluation set.
- The `submission/` scaffold still delegates to the repository's `starter` package and
  must be packaged with the required local modules for final delivery.

## Reproducibility sources

- Config: `analysis/configs/integrated_guarded_rerank.json`
- Clean-clone full run: `analysis/runs/integrated_guarded_safe_4e9be3c_cleanclone/`
- Five-fold summary: `analysis/runs/integrated_guarded_safe_4e9be3c_cv_summary.json`
- Failure report: `analysis/integrated_agent_failure_report.md`
- Demo: `python demo_session.py --catalog data/catalog.jsonl`

The clean-clone environment records commit `4e9be3c`, `dirty=false`, matching catalog
and dataset hashes, 200/200 diagnostic traces, and zero failed Agent calls. Its core
metrics exactly reproduce the current `main` report.

## Final administrative placeholders

- Public repository URL: `[PUBLIC_GITHUB_URL]`
- Public demo video URL: `[PUBLIC_YOUTUBE_URL]`
- Final verified team display names and contribution wording: `[VERIFY BEFORE SUBMISSION]`
