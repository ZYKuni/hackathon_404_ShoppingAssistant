# Shopping Copilot technical report — draft

## Method

The current frozen baseline is a fully offline conversational retrieval system using SQLite FTS5 BM25 and weighted reciprocal-rank fusion over active-session context, the current turn, and the category anchor. It applies lightweight multi-turn memory, intent-override handling, deterministic clarification questions, and a popularity fallback.

## Frozen public baseline

| Metric | Result |
|---|---:|
| Hit Rate@10 | 0.840000 |
| MRR | 0.476401 |
| MTTC | 4.885 |
| Technical Score | 0.685220 |

The fixed seed-404 five-fold result is Hit Rate `0.840000 ± 0.082158`, MRR `0.476401 ± 0.084888`, and MTTC `4.885 ± 0.360815`. These are development results on the 200 released sessions and are not estimates from the hidden 800-session evaluation set.

## Runtime and cost

The recorded Python 3.14.6 baseline uses no LLM or external API, reports zero tokens and zero API cost, and works without network access. The clean full evaluation recorded P50/P95 Agent response latency of approximately 170/391 ms and a process-lifetime peak working set of approximately 329 MiB on the development machine.

## Fallback

If retrieval produces too few candidates, the baseline fills remaining result positions with catalog products ordered by rating-count-weighted popularity. It does not require an external service fallback.

## Limitations

- The current submission scaffold still imports the implementation from `starter/` and is not yet an isolated final bundle.
- Dialogue state is not yet connected to the structured `ConversationState` protocol.
- The fixed clarification order is not candidate-uncertainty aware.
- Intent Override has the weakest scenario Hit Rate in the public baseline.
- Runtime varies materially across Python and SQLite builds.

## Required final updates

- Replace the delegated baseline with the selected integrated system.
- Add complete ablation, failure-analysis, and fallback results.
- Update dependency, model, API, latency, memory, and cost disclosures.
- Add public repository URL, demo video URL, and final team contributions.
