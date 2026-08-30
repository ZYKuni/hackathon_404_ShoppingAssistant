# Submission checklist

Status as of 2026-08-31. Re-run every checked command on the final archive.

## Required contents

- [x] Python entry file exporting `starter.agent.Agent`
- [x] Required local helper modules in `starter/`
- [x] Setup and one-command reproduction instructions in `README.md`
- [x] Exact tested Python version and dependency declaration
- [x] Method/model/cost/limitations/contributions report
- [x] Latency and token-use disclosure
- [x] Reproducible multi-turn demo and captured transcript
  (`demo_session.py`, `DEMO_TRANSCRIPT.md`)

## Interface and behavior

- [x] `reset(session_id, user_profile)` isolates sessions
- [x] `respond(session_id, user_message, turn, top_k)` returns the required keys
- [x] `message` is a string
- [x] `ask_attribute` is allowed or `None`
- [x] Recommendations are ordered, unique catalog `parent_asin` values, max 10
- [x] Usage counts are non-negative (`0/0` because no LLM is called)
- [x] Turn validation enforces the 1–10 protocol

## Policy and reproducibility

- [x] Catalog is opened read-only by the Agent code path
- [x] Evaluator and public labels were not modified
- [x] No API keys, secrets, private evaluation data, or privileged access
- [x] No network or undeclared external service is required
- [x] No third-party Python package is required
- [x] Model/API cost is USD 0 and offline fallback is the primary runtime

## Final release commands

```bash
python -m compileall -q starter tests evaluator analysis demo_session.py
python -m unittest discover -s tests -v
python -m evaluator.local_evaluator --output results.json
python demo_session.py --catalog data/catalog.jsonl
git diff --check
git status --short
```

## Manual gates before upload

- [ ] Confirm team names and contribution wording in `SUBMISSION_REPORT.md`
- [ ] Confirm the organizer's required archive/repository naming convention
- [ ] Create a clean archive that excludes `data/catalog.jsonl`, `results*.json`,
  `.git`, `__pycache__`, `.env`, and organizer/private artifacts
- [ ] Test the clean archive in a fresh CPython 3.12 environment
- [ ] Record or present the demo output/video requested by the event portal
- [ ] Verify that the public repository URL and submission form are accessible
