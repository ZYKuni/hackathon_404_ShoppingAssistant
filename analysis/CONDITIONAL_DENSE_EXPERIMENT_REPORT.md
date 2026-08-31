# Conditional clarification and dense retrieval experiment

Updated: 2026-08-31. All results below use the unchanged 200-session public evaluator. The output JSON files beside this report are reproducible machine-readable records; labels are never passed to the runtime.

## Decision

Keep `Agent(..., question_policy_mode="safe", dense_mode="off")` as the release default. The leading experiment is **Dense OFF × Conditional**: Hit@10 `0.865000`, MRR `0.503494`, MTTC `4.115000`, and Technical Score `0.721248`. It is a reproducible experiment, not yet a replacement for SAFE, because its Buying MRR (`0.497148`) is below the validated SAFE Buying MRR (`0.506349`).

## Reproducibility

The saved baseline is `analysis/joint_conditional_off_metrics.json`. A second fresh run matched its configuration, aggregate metrics, turn count, routes, fallbacks, selected/applied question counts, dynamic-question count, and gate counts exactly. Timing and peak RSS are intentionally excluded from that equality check.

```bash
python -m analysis.joint_experiment \
  --dense-mode off --question-mode conditional \
  --output /tmp/conditional-off-repro.json
```

Dense runs use the optional local asset/model directories and the isolated runtime declared in `requirements-dense-runtime.txt`:

```bash
/tmp/shopping-copilot-dense-runtime/bin/python -m analysis.joint_experiment \
  --dense-mode on \
  --dense-assets /tmp/shopping-copilot-minilm-assets \
  --dense-model /tmp/shopping-copilot-minilm-onnx-release \
  --question-mode conditional --semantic-weight 0 \
  --output /tmp/dense-conditional.json
```

## Experiment matrix

| Configuration | Hit@10 | MRR | MTTC | Technical score | Buying MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stable OFF × SAFE | 0.855000 | 0.495175 | 4.745000 | 0.701152 | 0.506349 |
| OFF × Conditional | **0.865000** | 0.503494 | **4.115000** | **0.721248** | 0.497148 |
| Conditional + no `other` after Buying history | 0.865000 | 0.503425 | 4.270000 | 0.718128 | 0.496974 |
| Conditional + sticky Buying SAFE | 0.865000 | 0.503425 | 4.270000 | 0.718128 | 0.496974 |
| Dense ON × Conditional, semantic weight 0 | 0.860000 | **0.505466** | 4.190000 | 0.717840 | 0.507148 |
| Dense ON × Conditional, semantic weight 0.10 | 0.860000 | **0.506591** | 4.190000 | 0.718177 | 0.508953 |
| Dense ON × Conditional, value threshold 0.35 | 0.860000 | **0.505466** | 4.185000 | 0.717940 | 0.507148 |
| Dense ON × Conditional, value threshold 0.50 | 0.860000 | 0.504883 | 4.195000 | 0.717565 | **0.515064** |

The requested joint gate is therefore not met by the tested configurations: Hit@10 >= `0.865`, MRR >= `0.503494`, and MTTC <= `4.115`; the denser candidate pool increases conditional dynamic questions from 197 to 265 at the base threshold. Raising the value threshold reduces dynamic questions but does not recover Hit@10 or MTTC enough.

## Implemented safeguards and semantic feature

- `session_has_seen_buying_intent` is session-local and set as soon as the formal router returns Buying. Conditional mode now exposes two auditable ablations: block the `other` escape hatch after that point, or permanently choose SAFE after that point. Neither is enabled by default.
- `RankingExplanation.semantic_similarity` records the normalized MiniLM cosine score from the existing `dense_semantic` retrieval evidence. Its Official-mode fusion weight is explicit, default `0.0`, capped at `0.25`, and is applied only after the validated Legacy Top-10 guard. This feature therefore cannot widen the final recommendation set or affect ordering outside that set.

## Next evidence-driven iteration

Semantic weight `0.10` improves MRR but leaves Hit@10 and MTTC unchanged, so it is not promoted. The next targeted experiment should hold the clarification candidate distribution constant while comparing lexical versus Dense recommendation ranking. This separates the beneficial Buying MRR gain from the Dense-induced candidate-pool saturation that changes the dialog path.
