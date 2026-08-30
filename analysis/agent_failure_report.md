# Agent failure report

This report is populated after the experiment runner captures candidate-stage and dialogue-state traces.

## Failure taxonomy

- Recall failure
- Filter failure
- Rerank failure
- Dialogue failure
- Override failure
- Boundary failure
- Efficiency failure
- Runtime/fallback failure

## Current baseline

The registered baseline experiment is the source of truth for aggregate and per-scenario metrics. Do not copy metrics into this report until the experiment is linked by experiment ID and commit.

## Representative failures

For every reviewed failure, record:

- experiment ID and `sample_id`;
- primary and optional secondary label;
- target product;
- message history and final state;
- candidate-stage target ranks;
- root cause;
- proposed next action.
