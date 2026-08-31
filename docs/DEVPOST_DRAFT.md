# Shopping Copilot

> **Tagline:** A stateful, fully offline shopping agent that turns evolving needs into better-ranked products.

## Inspiration

Online shopping rarely begins with a perfect query. Customers reveal constraints one
turn at a time, reject attributes, or change their mind halfway through a search.
Traditional keyword search treats each message as an isolated string and can retain
stale requirements after an intent change.

We built Shopping Copilot to behave more like a helpful in-store conversation: return
useful products immediately, ask one focused follow-up question, remember confirmed
requirements, and rebuild the search cleanly when the customer's intent changes.

## What it does

Shopping Copilot searches a frozen catalog of 50,000 Amazon Clothing, Shoes & Jewelry
products. On every turn it returns up to ten exact `parent_asin` recommendations and
may ask one clarification question. It supports Buying, Browsing, Intent Override,
and Boundary interactions over a maximum of ten turns.

The submitted runtime is deterministic and fully offline. It needs no API key, makes
no network request, uses zero model tokens, and has zero per-query model cost.

## How it works

1. A deterministic parser converts the current message into a structured state patch.
2. The reducer maintains isolated hard constraints, soft preferences, exclusions,
   no-preference fields, asked attributes, and intent-override scope.
3. An explainable router selects the Buying or Browsing retrieval track.
4. Route-specific SQLite FTS5/BM25 searches retrieve a formal Top-200 pool from active
   context, the current turn, the category anchor, structured constraints, or use case.
5. A local matcher and ranker evaluate normalized product attributes and safe profile
   aggregates.
6. Official mode keeps the validated lexical Top-10 set and applies a bounded `0.4`
   guarded rerank signal to improve ordering without losing candidate recall.
7. The SAFE dialogue policy returns the next validated unanswered attribute.

## Candidate-aware clarification

We also built a candidate-aware Question Policy that scores unanswered attributes by
candidate diversity, catalog coverage, Buying/Browsing relevance, expected candidate
reduction, and an optional profile hint capped at `1.08×`.

The policy has three rollout modes:

- SAFE preserves the validated question order and is the submitted default.
- SHADOW computes and records candidate-aware decisions without changing output.
- DYNAMIC applies the candidate-aware decision for ablation and adversarial QA.

DYNAMIC reduced MTTC to `4.305` and achieved a composite score of `0.702058`, but it
also reduced Hit@10 to `0.850000` and MRR to `0.477192`. We therefore kept SAFE as the
formal default under a non-regression rule.

## Why conversation matters

Catalog analysis shows that the first message alone places the hidden target in a
lexical Top 10 only `18.5%` of the time. An offline oracle query containing all hidden
constraints raises lexical Top-10 coverage to `87.0%`. This is diagnostic evidence—not
an online feature—that the agent must efficiently uncover and retain useful details.

## Results

All results use the organizer's unchanged deterministic evaluator and the released
200-session development set.

| Configuration | Hit@10 | MRR | MTTC | Technical Score |
| --- | ---: | ---: | ---: | ---: |
| Released weak starter | 0.125000 | — | — | — |
| Frozen legacy BM25/RRF | 0.840000 | 0.476401 | 4.885 | 0.685220 |
| **Final integrated SAFE** | **0.855000** | **0.495175** | **4.745** | **0.701152** |

The final system gains three sessions and loses none relative to the frozen legacy
baseline. Scenario Hit@10 is `0.8875` for Buying, `0.8625` for Browsing, `0.7667` for
Intent Override, and `0.8000` for Boundary.

Fixed seed-404 five-fold analysis reports:

- Hit@10: `0.855000 ± 0.097468`
- MRR: `0.495174 ± 0.097534`
- MTTC: `4.745 ± 0.488109`
- Technical Score: `0.701152 ± 0.085414`

These are public-development results and are not predictions of the hidden 800-session
score.

## What the failures taught us

The final system misses 29 of 200 sessions, down from 32 for the legacy baseline.
Diagnostic coverage is 29/29: 25 misses are assigned to the Top-10 rerank/selection
stage, three to intent override, and one to recall. Twelve missed targets reach merged
pool positions 11–20, which makes cutoff-aware reranking a higher priority than adding
broad new retrieval routes.

## Runtime and cost

The clean-clone Python 3.14.6 Windows run recorded a `62.534 s` cold initialization,
`348.570 s` for the complete evaluation, P50/P95 Agent latency of
`234.838/1160.173 ms`, and approximately `502 MiB` peak working set. There were no
failed Agent calls, no external services, zero model tokens, and USD 0 API/model cost.

## Challenges

Catalog metadata is sparse and inconsistent: descriptions and numeric prices are often
missing, while free-form details use many different keys. Treating every missing value
as a hard mismatch destroys recall, so the matcher distinguishes match, mismatch, and
unknown.

Intent Override is another difficult case. The agent must clear stale product scope
without discarding compatible evidence from the new request. We made these transitions
deterministic and regression-tested rather than relying on opaque conversation memory.

## Accomplishments

- Raised released-starter Hit@10 from `0.125` to `0.855`.
- Improved the frozen legacy system on Hit@10, MRR, MTTC, and composite score with no
  lost legacy hits on the public set.
- Built structured multi-turn state, Buying/Browsing routing, hybrid lexical retrieval,
  constraint-aware ranking, safe fallbacks, and intent-override recovery.
- Added a candidate-aware clarification policy with SAFE/SHADOW/DYNAMIC rollout gates.
- Added fixed stratified folds, immutable experiment records, diagnostic traces, and a
  complete 29-miss failure analysis.
- Kept the final runtime deterministic, offline, credential-free, and zero-cost.

## Limitations and what's next

The final system still has incomplete long-tail aliases, open-text sizing, expensive
cold initialization, and lexical weakness on unseen paraphrases. Candidate-aware
questions remain opt-in until they pass per-scenario non-regression gates. The next
technical priority is cutoff-aware reranking for the 12 near-miss targets, followed by
category-aware size normalization and a packaged offline semantic route only if it
passes latency, memory, licensing, and release gates.

## Demo and repository

- YouTube demo: `[PUBLIC_YOUTUBE_URL]`
- Public GitHub repository: `[PUBLIC_GITHUB_URL]`

## Team

- Aaron — catalog normalization, constraint matching, hybrid retrieval, ranking, and component analysis.
- Ethan — intent routing, immutable state adapters, orchestration contracts, and fallback integration.
- Shierly — candidate-aware Question Policy, Top-200 diagnostics, profile hints, golden QA, and ablations.
- Lydia — reproducible baseline/experiment framework, fixed folds, diagnostic trace contract, failure analysis, clean-clone verification, and submission evidence.
- `[VERIFY REMAINING DISPLAY NAMES AND FINAL CONTRIBUTION WORDING]`
