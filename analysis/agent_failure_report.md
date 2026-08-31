# Baseline failure analysis

## Technical summary

The baseline misses **32 of 200 sessions** (Hit Rate@10 = **84.00%**). This first-pass diagnosis uses evaluator outcomes plus per-turn retrieval/ranking traces; primary labels are mutually exclusive so counts reconcile exactly to the miss total.

## Primary failure counts

| Primary failure | Misses | Share of misses |
|---|---:|---:|
| Rerank failure | 25 | 78.1% |
| Override failure | 6 | 18.8% |
| Recall failure | 1 | 3.1% |

## Key findings

- Rerank failures account for 25/32 misses; 12 are near-cutoff targets with a best merged-pool rank of 11-20.
- Override failures account for 6/32 misses and should be isolated before broad rank-weight tuning.
- Only 1/32 misses never retrieve the target, so adding recall routes is not the first global priority.

## Scenario × primary failure

| Scenario | Primary failure | Misses |
|---|---|---:|
| boundary | Recall failure | 1 |
| boundary | Rerank failure | 1 |
| browsing | Rerank failure | 11 |
| buying | Rerank failure | 9 |
| intent_override | Override failure | 6 |
| intent_override | Rerank failure | 4 |

## Interpretation and next actions

- **Recall failures:** change retrieval/query construction first; reranking cannot rescue an absent target.
- **Rerank failures:** inspect target rank versus the Top-10 cutoff and tune route fusion or add attribute-aware scoring.
- **Override failures:** reset/rebuild active constraints at the explicit override and protect with scenario regressions.
- **Boundary context:** remains a secondary label so it does not double-count the retrieval/ranking root cause.
- **Filter failures:** are not separately observable because the current baseline has no filter stage.

## Failure details

| Sample | Scenario | Primary | Secondary | Turns | Best route rank | Best pool rank | Override turn |
|---|---|---|---|---:|---:|---:|---:|
| public_0002 | intent_override | Rerank failure | Override context | 10 | 77 | 96 | 3 |
| public_0015 | browsing | Rerank failure | — | 10 | 6 | 12 | — |
| public_0016 | browsing | Rerank failure | — | 10 | 20 | 29 | — |
| public_0020 | buying | Rerank failure | — | 10 | 117 | 117 | — |
| public_0028 | buying | Rerank failure | — | 10 | 47 | 25 | — |
| public_0035 | boundary | Rerank failure | Boundary context | 10 | 20 | 21 | — |
| public_0040 | browsing | Rerank failure | — | 10 | 25 | 37 | — |
| public_0052 | intent_override | Override failure | — | 10 | 7 | 5 | 3 |
| public_0058 | buying | Rerank failure | — | 10 | 17 | 18 | — |
| public_0071 | intent_override | Override failure | — | 10 | 1 | 3 | 4 |
| public_0076 | browsing | Rerank failure | — | 10 | 16 | 16 | — |
| public_0080 | intent_override | Rerank failure | Override context | 10 | 5 | 11 | 4 |
| public_0083 | buying | Rerank failure | — | 10 | 65 | 84 | — |
| public_0084 | intent_override | Override failure | — | 10 | 1 | 1 | 4 |
| public_0087 | browsing | Rerank failure | — | 10 | 58 | 83 | — |
| public_0092 | browsing | Rerank failure | — | 10 | 18 | 19 | — |
| public_0096 | intent_override | Override failure | — | 10 | 19 | 9 | 3 |
| public_0120 | browsing | Rerank failure | — | 10 | 14 | 16 | — |
| public_0126 | browsing | Rerank failure | — | 10 | 45 | 49 | — |
| public_0127 | browsing | Rerank failure | — | 10 | 11 | 13 | — |
| public_0137 | browsing | Rerank failure | — | 10 | 17 | 17 | — |
| public_0144 | intent_override | Rerank failure | Override context | 10 | 75 | 91 | 4 |
| public_0145 | buying | Rerank failure | — | 10 | 14 | 20 | — |
| public_0159 | buying | Rerank failure | — | 10 | 11 | 15 | — |
| public_0161 | buying | Rerank failure | — | 10 | 16 | 16 | — |
| public_0172 | browsing | Rerank failure | — | 10 | 9 | 13 | — |
| public_0174 | buying | Rerank failure | — | 10 | 55 | 80 | — |
| public_0177 | intent_override | Override failure | — | 10 | 2 | 2 | 4 |
| public_0183 | intent_override | Override failure | — | 10 | 5 | 4 | 4 |
| public_0187 | boundary | Recall failure | Boundary context | 10 | — | — | — |
| public_0194 | buying | Rerank failure | — | 10 | 27 | 27 | — |
| public_0198 | intent_override | Rerank failure | Override context | 10 | 79 | 129 | 4 |

## Scope, method, and limitations

- Result source: `analysis/runs/baseline_diagnostic_816f430_cleanclone/result.json`
- Trace source: `analysis/runs/baseline_diagnostic_816f430_cleanclone/diagnostic_traces.jsonl`
- Trace coverage for misses: 32/32.
- Verified labels are based on recorded target ranks, not semantic guesses from product text.
- Override attribution is conservative: it requires an explicit override phrase, pre-override target viability, and no post-override recommendation.
- This public 200-session set is diagnostic evidence, not an unbiased hidden-test estimate.
- The JSON companion contains per-turn evidence, final state, confidence, reason, and proposed next action for every miss.
