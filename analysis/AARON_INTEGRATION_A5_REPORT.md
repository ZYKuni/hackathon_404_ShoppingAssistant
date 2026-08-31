# A5 — formal pipeline integration and guarded-rerank report

> Final validation: 2026-08-31
> Data: frozen 50,000-product catalog and 200 public sessions

## Outcome

The formal catalog normalizer, hybrid retriever, and local constraint ranker are
now the default `Agent(catalog_path)` runtime. They are connected through the
immutable state/profile contracts and explainable Buying/Browsing router. The
organizer-facing Agent API is unchanged.

Official mode preserves the validated lexical Top-10 set and uses the formal
ranker as a bounded 0.4 reciprocal-rank signal. This keeps candidate recall
stable while improving target position. Development mode still exposes pipeline
errors; Official mode records deterministic retrieval/ranking fallbacks.

## Integrated path

```text
ConversationState + aggregate UserProfile
  -> immutable StateSnapshot / ProfileSnapshot
  -> IntentRouter -> SearchRequest
  -> HybridRetriever (multi-route BM25 + weighted RRF, max 200)
  -> LocalConstraintRanker
  -> Official guarded Top-10 rerank
  -> Agent response
```

## Final 200-session evaluation

| Strategy | Hit@10 | MRR | MTTC | Technical Score |
| --- | ---: | ---: | ---: | ---: |
| Original validated Legacy (before override repair) | 0.840000 | 0.476401 | 4.885 | 0.685220 |
| Pure formal candidate set (earlier A5 ablation) | 0.790000 | 0.374804 | 5.290 | 0.621641 |
| Repaired Legacy ablation | **0.855000** | 0.470704 | **4.745** | 0.693811 |
| **Submitted guarded formal rerank** | **0.855000** | **0.495175** | **4.745** | **0.701152** |

The submitted strategy improves MRR by 0.024471 over the repaired Legacy
ablation without reducing Hit@10 or MTTC.

### Submitted strategy by scenario

| Scenario | Sessions | Hit@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Buying | 80 | 0.887500 | 0.506349 | 4.925000 |
| Browsing | 80 | 0.862500 | 0.488224 | 4.025000 |
| Intent Override | 30 | 0.766667 | 0.437857 | 5.766667 |
| Boundary | 10 | 0.800000 | 0.633333 | 6.000000 |

Intent Override Hit@10 rose from the original 0.666667 to 0.766667 after
compatible same-slot evidence preservation. The reset still discards unrelated
old constraints and fully switches the category when a different category is
explicitly named.

## Regression found during integration

Sharing one FTS index initially caused the safety path to inherit the formal
retriever's shorter stopword set. Conversational filler such as `preference`,
`requirement`, and `matches` then entered BM25 and reduced full-set Hit@10 to
0.795. The shared index now accepts the Legacy conversation stopword set on its
safety search path. Popularity and BM25 tie semantics were also restored, and a
unit test protects the stopword boundary.

## Clarification policy

When every one of the 200 candidate slots is occupied, the response explicitly
states that the match set is broad and asks the next unresolved allowed field.
The field order remains the empirically validated material-first policy. A
feature-first experiment reduced Hit@10 to 0.755 and was rejected.

## Tests, latency, and resources

- 93 unit/integration tests pass.
- `compileall` and `git diff --check` pass.
- No normal-path pipeline fallback was observed in the validated evaluator run.
- Cold formal Agent initialization: 27.67 seconds standalone; 39.89 seconds in
  the final instrumented benchmark.
- Final instrumented public benchmark: 164.39 seconds, approximately 507 MB peak RSS.
- 920 response calls; estimated post-initialization mean about 135 ms/call.
- Network, API keys, external model calls, tokens, and API cost: none/zero.

```bash
python -m compileall -q starter tests evaluator analysis demo_session.py
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator --output results.json
git diff --check
```

These numbers are development-machine observations. Hidden-set performance and
organizer-hardware latency remain unknown.
