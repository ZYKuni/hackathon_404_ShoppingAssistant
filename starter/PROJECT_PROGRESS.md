# Shopping Copilot current progress

> Updated: 2026-08-31
> Branch: `feature/ykzhao0829_02`

## Current status

The required offline MVP pipeline is integrated behind the organizer's default
construction path, `Agent(catalog_path)`. The previously duplicated merge
fragments in Agent, router, orchestrator, state adapter, and their tests were
removed. The implementation now compiles, passes 93 tests, and completes the
unchanged 200-session public evaluator.

| Metric | Current formal Agent |
| --- | ---: |
| Hit Rate@10 | 0.855000 |
| MRR | 0.495175 |
| MTTC | 4.745 |
| Technical Score | 0.701152 |

## Completed MVP layers

1. Deterministic natural-language parser to `StatePatch`.
2. Immutable reducer semantics and 30 golden state transitions.
3. Per-session memory, no-preference handling, and category/constraint override.
4. Product normalization for category, price, audience, color, material, brand,
   style, use case, feature, and open-vocabulary text.
5. Hard-constraint three-state matcher (`match`, `mismatch`, `unknown`).
6. Explainable Buying/Browsing route selection.
7. Multi-route in-memory BM25 retrieval and weighted RRF Top-200 fusion.
8. Local structured ranker with constraint, profile, route, and popularity features.
9. Official guarded Top-10 rerank, deterministic fallback, and overload prompt.
10. Setup README, short submission report, checklist, and runnable multi-turn demo.

## Important implementation decision

The formal ranker does not replace the validated lexical candidate set in
Official mode. It reorders the same Top 10 at weight 0.4. This retains Hit@10
and MTTC while improving MRR. Development mode can expose formal retrieval or
ranking failures directly; Official mode records and applies safe fallbacks.

The shared SQLite index must keep two tokenization policies: the formal routes
use their compact search stopword set, while the guarded Legacy path uses its
conversation-specific stopwords. Accidentally sharing the former reduced
Hit@10 from 0.855 to 0.795 and is now regression-tested.

## Remaining work, ordered by release priority

### P0 — human release gates

- Confirm team names/contributions in `SUBMISSION_REPORT.md`.
- Confirm portal-specific archive name, repository URL, and video requirements.
- Produce a clean archive and test it without the untracked catalog/results.
- Record the demo if the organizer expects a video rather than a live command.

### P1 — robustness before hidden evaluation

- Add more long-tail category/material/color aliases from error analysis only.
- Add category-aware size normalization.
- Benchmark the archive under the organizer's actual CPU/memory timeout.
- Run several deterministic reruns and record checksums for release artifacts.

### P2 — post-MVP experiments

- Package an optional offline neural/LLM semantic reranker and compare it by
  ablation; it must never reduce the guarded candidate set or become an
  undeclared network dependency.
- Replace fixed question priority only if a candidate-distribution/value-of-
  information policy beats the public and adversarial regression suites.
- Cache a versioned normalized-catalog artifact if the rules permit small local
  assets; observed cold initialization is approximately 28–40 seconds.

## Reproduce

```bash
python -m compileall -q starter tests evaluator analysis demo_session.py
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator --output results.json
python demo_session.py --catalog data/catalog.jsonl
git diff --check
```

The catalog and evaluator remain frozen. Ground truth is used only by the
evaluator and offline metrics, never in runtime ranking features.
