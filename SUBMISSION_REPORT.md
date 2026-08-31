# Shopping Copilot submission report

## Method and architecture

The submitted `Agent(catalog_path)` is an offline conversational search system.
It parses each turn into a deterministic patch, reduces that patch into an
isolated session state, and routes the request as Buying or Browsing using
explainable signals. It retrieves a maximum of 200 candidates from weighted
active-context, current-turn, category-anchor, structured-constraint, and
use-case BM25 routes. A local ranker evaluates normalized hard constraints,
soft preferences, exclusions, route evidence, popularity, and safe aggregate
profile fields.

Official mode uses a guarded rerank: the best validated lexical Top-10 set is
retained, while the formal ranker contributes a 0.4 reciprocal-rank signal to
reorder that set. This prevents a new semantic signal from reducing candidate
recall before enough conversational constraints have accumulated.

Intent overrides reset superseded product constraints. If the new value
confirms one value from a prior multi-value slot, compatible same-slot detail is
retained only as a soft preference; unrelated evidence is discarded. Boundary
replies such as “no preference” are stored structurally and are excluded from
positive retrieval context.

## Model choice and external services

- Generative LLM: none in the submitted runtime
- Embedding API/vector database: none
- Ranker: deterministic local normalized-field constraint ranker
- Retrieval: in-memory SQLite FTS5/BM25 plus weighted RRF
- Network requirement: none
- Credentials/environment variables: none
- Prompt/completion tokens: 0/0
- Estimated model/API cost: USD 0

This choice makes the final bundle reproducible if the organizer disables
network access. Neural or LLM semantic reranking remains a documented future
experiment, not an undeclared dependency.

## Public development results

| Strategy | Hit@10 | MRR | MTTC | Technical Score |
| --- | ---: | ---: | ---: | ---: |
| Legacy ablation | 0.855000 | 0.470704 | 4.745 | 0.693811 |
| Submitted guarded rerank | **0.855000** | **0.495175** | **4.745** | **0.701152** |

Submitted strategy by scenario:

| Scenario | N | Hit@10 | MRR | MTTC |
| --- | ---: | ---: | ---: | ---: |
| Buying | 80 | 0.887500 | 0.506349 | 4.925000 |
| Browsing | 80 | 0.862500 | 0.488224 | 4.025000 |
| Intent Override | 30 | 0.766667 | 0.437857 | 5.766667 |
| Boundary | 10 | 0.800000 | 0.633333 | 6.000000 |

All figures come from the unchanged public evaluator and frozen 200-session
set. Ground truth is never used as an Agent feature.

## Latency and resource disclosure

Development machine observations on 2026-08-31 (Apple Silicon, CPython 3.12.11):

- cold `Agent` initialization: 27.67 seconds standalone and 39.89 seconds in
  the final instrumented benchmark;
- complete final 200-session benchmark: 164.39 seconds and approximately
  507 MB peak RSS;
- 920 `respond` calls; estimated mean after subtracting the benchmark's measured
  initialization: approximately 135 ms per call;
- fallback events during the validated run: zero.

These are local observations rather than organizer hardware guarantees.

## Demonstrated multi-turn session

`demo_session.py` was run against the frozen catalog. It starts with a broad
women's running-shoe request, accumulates lightweight/breathable/cotton
preferences, then explicitly overrides the category to a black waterproof
hiking boot. All three turns returned ten IDs, no fallback event, a structured
clarification field, and `0/0` token usage. The captured customer/Agent exchange
and abbreviated recommendations are in `DEMO_TRANSCRIPT.md`.

## Limitations and risks

- The alias lexicon does not cover every long-tail catalog expression.
- Size remains open text rather than category-specific numeric normalization.
- Clarification becomes proactive when the Top-200 pool saturates, but the
  follow-up field uses a validated fixed priority rather than learned value of
  information; a feature-first alternative reduced public Hit@10 to 0.755.
- Cold initialization scans and normalizes the full catalog.
- Lexical retrieval can miss unseen paraphrases; adding an offline neural
  reranker requires a separately licensed, packaged model and memory benchmark.
- Public-set optimization may not transfer perfectly to the hidden split.

## Team contribution record

- Aaron: catalog normalization, constraint matcher, hybrid retrieval, local
  ranker, and A1–A5 component analysis.
- Ethan: intent routing, immutable state adapters, orchestration contracts,
  official/development fallback behavior, and pipeline integration.
- Yikun Zhao: baseline Agent, conversation state/parser integration, override
  handling, clarification behavior, performance/regression repair, evaluation,
  and submission/demo documentation.

Before submission, the team should verify names and contribution wording.
