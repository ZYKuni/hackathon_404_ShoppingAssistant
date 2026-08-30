# Shierly: Question Policy and Personalized Context

## Outcome

This branch implements and tunes the P0 Question Policy and context boundary.
The tuned dynamic experiment now improves public Hit@10, MRR, and Technical
Score; the safe mode remains the default until MTTC and cross-validation meet
the full promotion gate.

Delivered files:

- `starter/question_policy.py`: deterministic candidate-aware scoring and question selection;
- `starter/context_distillation.py`: separate short-term state and low-weight long-term profile context;
- `starter/question_policy_cases.jsonl`: 20 hand-authored golden cases;
- `tests/test_question_policy.py`: policy, context, boundary, override, and Agent integration tests;
- `analysis/question_policy_ablation.py`: reproducible fixed/dynamic and profile on/off evaluation runner;
- `analysis/question_policy_trace.py`: exact per-session question order, reason, score, and rank trace;
- `analysis/question_policy_demo.py`: reproducible running-shoes-to-winter-boots Override demo.

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
| `dynamic` | Candidate-aware policy with conservative explicit-Buying guard | Experiments and targeted QA |

Explicit Buying keeps the validated order until the normalized Top-200 pool can
provide reliable hard-conflict signals. Browsing and Override use candidate-aware
scores. Long-tail category evidence prevents semantically repeated category
questions, and the first customer request no longer counts as an ineffective
reply toward the one-time `other` trigger.

`safe` remains the default because dynamic MTTC is still `0.045` turns above the
main baseline and above the Todo target of `4.0`. Hit@10, MRR, and Technical
Score now pass their non-regression gate.

## Public-set ablation

Dataset: 200 public sessions. No external model, API key, token cost, or network
dependency was added.

| Policy | Profile | Hit@10 | MRR | MTTC | Technical score |
|---|---|---:|---:|---:|---:|
| Main baseline / `safe` | on | 0.840 | 0.476401 | 4.885 | 0.685220 |
| Tuned dynamic | on | 0.855 | 0.484141 | 4.930 | 0.694142 |
| Tuned dynamic | off | 0.855 | 0.480808 | 4.915 | 0.693442 |

Relative to `safe`, tuned dynamic changes Hit@10 by `+0.015`, MRR by
`+0.007740`, and Technical Score by `+0.008922`; MTTC is `+0.045` turns slower.
Profile on/off keeps Hit identical and changes MRR by only `0.003333`, consistent
with the profile's deliberately low-weight role.

| Scenario (dynamic on) | Hit@10 | MRR | MTTC |
|---|---:|---:|---:|
| Boundary | 0.800000 | 0.750000 | 6.600000 |
| Browsing | 0.850000 | 0.479673 | 4.300000 |
| Buying | 0.887500 | 0.491533 | 4.925000 |
| Intent Override | 0.800000 | 0.387725 | 6.066667 |

Overall promotion is still blocked by MTTC and the need for validation beyond
the public set, not by Hit/MRR regression.

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
python3 -m analysis.question_policy_trace \
  --mode dynamic --sample-id public_0001 --output question_trace.json
python3 -m analysis.question_policy_demo
```

The demo output records state before/after Override, cleared/replaced slots,
three retrieval routes, Top-10 pool change, exact question reason, and a real
waterproof boot target reaching rank 3. All six demo checks must pass.

## Known risks and next integration points

- Candidate facets currently come from the returned Top-10 FTS rows, not Aaron's normalized Top-200 pool.
- Route inference is a deterministic fallback until Ethan's `RouteDecision` is wired into the Agent.
- Dynamic facet extraction adds latency and should be cached or supplied by the normalizer.
- Brand evidence currently uses `store` as a weak signal and must not be treated as ground truth.
- Price is not present in the FTS row, so candidate-aware budget value is limited until catalog normalization lands.
- Dynamic promotion still requires MTTC optimization toward `<= 4.0`, the normalized candidate pool, and cross-validation beyond the public set.
