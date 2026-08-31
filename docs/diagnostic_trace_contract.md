# Diagnostic Trace Contract

`Agent.reset(...)` and `Agent.respond(...)` remain the only official scoring interface. Diagnostics are an optional, development-only side channel:

```python
trace = agent.get_diagnostic_trace(session_id)
```

Implementations must return a detached snapshot. Reading or modifying the returned object must not change live Agent state.

## Stable schema

```json
{
  "schema_version": "1.0.0",
  "session_id": "runtime session identifier",
  "turns": [
    {
      "turn": 1,
      "user_message": "customer input",
      "state": {},
      "ranking": {
        "routes": [
          {
            "name": "active_context",
            "weight": 1.4,
            "candidate_ids": ["B000..."]
          }
        ],
        "candidate_pool": ["B000..."],
        "recommendations": [{"parent_asin": "B000...", "score": 0.01}]
      },
      "response": {
        "message": "Do you have a preferred material?",
        "ask_attribute": "material",
        "recommendations": ["B000..."]
      },
      "fallback": {"used": false, "reason": null, "added_ids": []},
      "timing_ms": {"state_update": 0.1, "ranking": 20.0, "question_policy": 0.1}
    }
  ]
}
```

The stable core is validated by `starter.diagnostics.validate_diagnostic_trace`. Modules may add fields but must preserve the documented fields.

## Evaluation annotations

An Agent must never receive or store the hidden target. The experiment runner joins public evaluation context after a session finishes and writes `diagnostic_traces.jsonl`. It may add:

- `sample_id` and `scenario_type`;
- the public target `parent_asin`;
- target rank in every retrieval route;
- target rank in the merged candidate pool and final recommendations.

These annotations belong only in analysis artifacts, never in `submission/` runtime logic.

## Safety and compatibility

- Never store API keys, authorization headers, raw private user identifiers, or undisclosed evaluation labels.
- Diagnostics must not change ranking, dialogue, token usage, or official response shape.
- Agents without this optional method remain valid; the runner records that diagnostics are unavailable.
- Candidate lists may be truncated, but each trace must state the candidates actually exposed to downstream stages.
