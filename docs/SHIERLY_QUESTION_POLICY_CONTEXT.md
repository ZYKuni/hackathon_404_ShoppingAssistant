# Shierly: Question Policy and Personalized Context

## Outcome

This branch implements the P0 Question Policy and context boundary without
silently promoting an experiment that regresses the current public baseline.

Delivered files:

- `starter/question_policy.py`: deterministic candidate-aware scoring and question selection;
- `starter/context_distillation.py`: separate short-term state and low-weight long-term profile context;
- `starter/question_policy_cases.jsonl`: 20 hand-authored golden cases;
- `tests/test_question_policy.py`: policy, context, boundary, override, and Agent integration tests;
- `analysis/question_policy_ablation.py`: reproducible fixed/dynamic and profile on/off evaluation runner.

## Candidate-aware policy

For each unanswered attribute, the dynamic policy computes:

```text
candidate diversity
× candidate coverage
× Buying/Browsing route relevance
× expected candidate reduction
× optional low-weight profile hint (1.08 maximum)
```

It enforces these lifecycle rules before scoring:

1. Turn 10 never asks another question.
2. A field in `no_preference` is not asked again.
3. An already asked field is not asked again in the same product scope.
4. A field already known from current hard/soft constraints is not asked again.
5. Recommendations and one clarification question may be returned together.
6. Buying weights likely hard-conflict fields more strongly.
7. Browsing weights use case, feature, and style more strongly.
8. After two replies add no effective constraint, `other` may be used once.
9. An Intent Override resets the `other` allowance and question scope.

The selected question text comes from the same `QUESTION_TEXT` mapping as the
returned `ask_attribute`, preventing response/attribute mismatches.

## Context boundary

Short-term context contains only the current category, hard constraints, soft
preferences, exclusions, asked/no-preference bookkeeping, turn, and latest
override signal.

Long-term context accepts only these aggregate profile fields:

- `preference_tags`;
- `rating_style`;
- `purchase_frequency`;
- `average_prior_rating`.

The free-form profile `summary` is deliberately excluded. Long-term context:

- can add at most a `1.08` question-score multiplier;
- never becomes a hard constraint;
- never enters the lexical retrieval query;
- never overrides a current explicit value, exclusion, or no-preference answer;
- can be disabled for a profile on/off ablation.

## Rollout modes

`Agent(..., question_policy_mode=...)` supports:

| Mode | Behavior | Intended use |
|---|---|---|
| `safe` | Current validated fixed question order | Default production behavior |
| `fixed` | Explicit fixed-order ablation | Baseline reproduction |
| `dynamic` | Candidate-aware policy | Experiments and targeted QA |

`safe` remains the default because the current dynamic experiment regresses the
overall public baseline. The implementation is ready for further tuning and
integration with the team's normalized Top-200 candidate pool and frozen router.

## Public-set ablation

Dataset: 200 public sessions. No external model, API key, token cost, or network
dependency was added.

| Policy | Profile | Hit@10 | MRR | MTTC | Technical score |
|---|---|---:|---:|---:|---:|
| Main baseline / `safe` | on | 0.840 | 0.476401 | 4.885 | 0.685220 |
| Full dynamic | on | 0.835 | 0.466712 | 5.045 | 0.676614 |
| Full dynamic | off | 0.835 | 0.466712 | 5.050 | 0.676514 |

The dynamic experiment produced one useful stratified signal:

| Intent Override metric | Main baseline | Full dynamic |
|---|---:|---:|
| Hit@10 | 0.666667 | 0.866667 |
| MRR | 0.419537 | 0.433095 |
| MTTC | 6.700 | 6.000 |

This is promising but insufficient to promote the policy because overall
Browsing and Boundary results regress. The correct next action is stratified
tuning, not public-set-only question-order selection.

## Golden QA coverage

`starter/question_policy_cases.jsonl` contains five cases in each required
scenario:

- Buying;
- Browsing;
- Intent Override;
- Boundary.

Every case includes messages, expected route/state, expected ask attributes,
must-not-ask attributes, expected behavior, and a business reason. These are
policy acceptance cases, not public-target hand tuning.

## Reproduce

```bash
python3 -m unittest discover -v
python3 -m analysis.question_policy_ablation \
  --mode safe --profile on --output results_question_safe.json
python3 -m analysis.question_policy_ablation \
  --mode dynamic --profile on --output results_question_dynamic.json
python3 -m analysis.question_policy_ablation \
  --mode dynamic --profile off --output results_question_dynamic_profile_off.json
```

## Known risks and next integration points

- Candidate facets currently come from the returned Top-10 FTS rows, not Aaron's normalized Top-200 pool.
- Route inference is a deterministic fallback until Ethan's `RouteDecision` is wired into the Agent.
- Dynamic facet extraction adds latency and should be cached or supplied by the normalizer.
- Brand evidence currently uses `store` as a weak signal and must not be treated as ground truth.
- Price is not present in the FTS row, so candidate-aware budget value is limited until catalog normalization lands.
- Dynamic promotion requires overall Hit/MRR non-regression, Boundary loop checks, and cross-validation beyond the public set.
