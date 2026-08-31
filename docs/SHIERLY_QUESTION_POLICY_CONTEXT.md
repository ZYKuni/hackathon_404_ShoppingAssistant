# Shierly: Question Policy on the formal pipeline

## Outcome

This implementation rebases the Question Policy work onto `main` commit
`eaf36af`. It does not reuse the old Agent fork. Instead it consumes the current
team interfaces:

- Ethan's immutable `RouteDecision` and `SearchRequest`;
- Aaron's normalized catalog and formal Top-200 `CandidatePool`;
- the existing `ProfileSnapshot`, which remains separate from live constraints;
- the validated Official-mode Top-10 guard and deterministic fallback.

The default `Agent(catalog_path)` remains behaviorally unchanged.

## Policy value

For every unanswered attribute, the dynamic policy computes:

```text
candidate diversity
× catalog coverage in the formal candidate pool
× Buying/Browsing route relevance
× expected candidate reduction
× optional profile hint (maximum 1.08)
```

Candidate facets come from normalized Top-200 products, not public labels and
not the final Top-10. The policy supports material, feature, color, style, size,
use case, budget bucket, and brand.

## Lifecycle rules

1. Turn 10 does not ask another question.
2. Asked fields and no-preference fields are not asked again.
3. Fields already known from current hard/soft constraints are not asked again.
4. A long-tail category already present in raw customer evidence is not re-asked.
5. Recommendations and one clarification question may be returned together.
6. The official Router decides Buying versus Browsing relevance weights.
7. After two ineffective replies, `other` may be used once per product scope.
8. Override clears question scope and the `other` allowance.
9. Aggregate profile tags only apply a low-weight question multiplier; they never
   become hard constraints or retrieval text.

## Rollout modes

`Agent(..., question_policy_mode=...)` accepts:

| Mode | Applied question | Candidate-aware diagnostics | Intended use |
| --- | --- | --- | --- |
| `safe` | Existing fixed priority | No scoring overhead | Default and official evaluation |
| `shadow` | Existing fixed priority | Yes, over formal Top-200 | Integration validation |
| `dynamic` | Candidate-aware decision | Yes | Ablation and adversarial QA only |

Shadow mode is the merge safety mechanism: it exercises the new Router,
Top-200 facet, scoring, context, and diagnostics path while preserving the exact
existing `message`, `ask_attribute`, recommendations, and usage output.

## Golden QA and diagnostics

`starter/question_policy_cases.jsonl` contains 20 hand-authored cases: five
each for Buying, Browsing, Intent Override, and Boundary. Every case records the
messages, expected route/state, allowed question attributes, prohibited
attributes, expected behavior, and business reason.

`Agent.question_policy_diagnostics(session_id)` exposes the latest:

- formal route;
- formal candidate count;
- candidate-aware selected field;
- field actually applied after rollout guard;
- exact reason and ordered attribute scores.

`analysis/question_policy_trace.py` records this information per turn without
feeding target labels into runtime decisions.

## Public-set non-regression gate

Frozen dataset: 200 public sessions. Ground truth is read only by the unchanged
evaluator. No model, API, network dependency, or token cost is added.

| Configuration | Hit@10 | MRR | MTTC | Technical score |
| --- | ---: | ---: | ---: | ---: |
| `main` baseline (`eaf36af`) | 0.855000 | 0.495175 | 4.745 | 0.701152 |
| Rebased default `safe` | 0.855000 | 0.495175 | 4.745 | 0.701152 |
| Rebased `shadow` | 0.855000 | 0.495175 | 4.745 | 0.701152 |
| Opt-in `dynamic` experiment | 0.850000 | 0.477192 | 4.305 | 0.702058 |

Both safe and shadow exactly reproduce the baseline core metrics. Dynamic asks
fewer questions and raises the composite score slightly, but lowers Hit@10 and
MRR. It therefore remains an explicitly opt-in experiment and is not eligible
to replace the default policy under the team's non-regression requirement.

## Reproduce

```bash
python -m compileall -q starter tests evaluator analysis demo_session.py
python -m unittest discover -s tests -v
python -m analysis.question_policy_ablation \
  --mode safe --profile on --output results_question_safe.json
python -m analysis.question_policy_ablation \
  --mode shadow --profile on --output results_question_shadow.json
python -m analysis.question_policy_ablation \
  --mode dynamic --profile on --output results_question_dynamic.json
python -m analysis.question_policy_trace \
  --mode shadow --sample-id public_0001 --output question_trace.json
git diff --check
```

## Promotion gate and remaining risk

`dynamic` must remain opt-in until it passes:

- overall and per-scenario Hit/MRR/MTTC non-regression;
- the 20 golden cases and Boundary no-loop tests;
- repeated-question and turn-10 checks;
- adversarial long-tail category and noisy size/brand cases;
- latency/memory measurement on organizer-like hardware.

The default system deliberately keeps the fixed validated order. Merging this
branch makes the candidate-aware policy available and observable without
silently changing the current submission behavior.
