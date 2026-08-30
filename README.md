# Shopping Copilot — Conversational Search MVP

An offline, reproducible shopping agent for the TechJam Conversational
E-Commerce Search Challenge. The official `Agent(catalog_path)` entry point
combines multi-turn state, Buying/Browsing routing, multi-route BM25 retrieval,
structured constraint matching, and a guarded local reranker.

## Public-set result

Measured on the frozen 200-session public set on 2026-08-31:

| Metric | Legacy ablation | Submitted `Agent` |
| --- | ---: | ---: |
| Hit Rate@10 | 0.855000 | **0.855000** |
| MRR | 0.470704 | **0.495175** |
| MTTC | 4.745 | **4.745** |
| Technical Score | 0.693811 | **0.701152** |

The guarded reranker preserves the proven Legacy Top-10 candidate set and
improves its ordering. These are development results, not a claim about the
organizer's hidden 800 sessions.

## Architecture

```text
user message + aggregate profile
  -> deterministic StatePatch -> isolated ConversationState
  -> explainable Buying/Browsing router
  -> active-context/current-turn/category/constraint retrieval routes
  -> weighted RRF Top-200 candidate pool
  -> hard-constraint three-state matching + soft/profile scoring
  -> guarded Top-10 rerank -> Agent response
```

Important behavior:

- state accumulates separately for every `session_id`;
- explicit intent overrides clear superseded constraints while preserving only
  compatible same-slot evidence;
- negated values never become positive BM25 terms;
- candidate overload produces an explicit clarification prompt;
- retrieval/ranking failures have deterministic official-mode fallbacks;
- the frozen catalog is read only and output IDs are catalog `parent_asin`s.

## Requirements and setup

- Supported: CPython 3.10 or newer
- Reproducibility environment: CPython **3.12.11**
- Python packages: none; the implementation uses the standard library and
  SQLite FTS5
- Network/API key: not required

The official catalog is intentionally not committed. Download
`catalog.jsonl.gz` from the competition release, verify its published SHA256,
then place the decompressed file at `data/catalog.jsonl`:

```bash
gzip -dk catalog.jsonl.gz
mv catalog.jsonl data/catalog.jsonl
python -m pip install -r requirements.txt
```

## Reproduce

The organizer-compatible evaluation command is:

```bash
python -m evaluator.local_evaluator \
  --catalog data/catalog.jsonl \
  --dataset data/public_set.jsonl \
  --output results.json
```

Run validation:

```bash
python -m compileall -q starter tests evaluator analysis
python -m unittest discover -s tests -v
git diff --check
```

Run the demonstrated three-turn session:

```bash
python demo_session.py --catalog data/catalog.jsonl
```

Cold initialization scans and normalizes all 50,000 products. On the current
Apple Silicon development machine, observed cold starts ranged from 27.67 to
39.89 seconds. The final instrumented public benchmark took 164.39 seconds,
made 920 `respond` calls (about 135 ms per call after subtracting its measured
initialization), and peaked around 507 MB. Hardware and OS load change these
figures.

## Required Agent interface

The official harness imports `Agent` from `starter.agent` and constructs it as
`Agent(args.catalog)`:

```python
from starter.agent import Agent

agent = Agent("data/catalog.jsonl")
agent.reset("session-1", {"preference_tags": ["comfort"]})
response = agent.respond("session-1", "I need running shoes", 1, 10)
```

Every response contains a string `message`, an allowed `ask_attribute` or
`None`, up to 10 ordered unique recommendations, and non-negative `usage`.

## Model, cost, and limitations

The submitted path uses no generative LLM or external embedding API. Ranking is
deterministic local semantic/constraint scoring over normalized catalog fields.
Reported token usage and estimated model/API cost are therefore zero, and the
agent works with network access disabled.

Known limitations include incomplete long-tail aliases, open-text sizing, a
fixed clarification priority after overload detection, cold-start catalog
normalization, and lexical weakness on paraphrases not represented in the
catalog. An optional neural/LLM reranker is a post-MVP experiment, not a hidden
runtime dependency.

## Submission files

- `starter/agent.py` — required entry point
- `starter/*.py` — local pipeline helpers
- `requirements.txt` — dependency declaration
- `SUBMISSION_REPORT.md` — method, model, cost, latency, limitations, contributions
- `SUBMISSION_CHECKLIST.md` — rule-by-rule release gate
- `demo_session.py` — reproducible multi-turn demonstration
- `DEMO_TRANSCRIPT.md` — captured three-turn demonstration
- `analysis/AARON_INTEGRATION_A5_REPORT.md` — detailed ablation evidence

Competition rules and API details remain in `docs/competition_specification.md`,
`docs/submission_rules.md`, and `docs/agent_api_contract.json`.

## Data attribution

The catalog and sessions are derived from Amazon Reviews 2023 by McAuley Lab,
UCSD. See `DATA_ATTRIBUTION.md` before redistributing data.
