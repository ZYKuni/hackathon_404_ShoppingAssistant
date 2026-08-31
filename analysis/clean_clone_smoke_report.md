# Baseline clean-clone smoke test

## Outcome

**PASS.** A local clone created from commit `816f430d113f64e657c14ce5550fcc0248937940` installed and ran without using files from the source working tree other than the intentionally Git-ignored competition catalog. The full 200-session baseline reproduced the frozen metrics exactly, with no gained or lost sessions.

## Reproduction environment

| Item | Observed value |
|---|---|
| Source commit | `816f430d113f64e657c14ce5550fcc0248937940` |
| Branch | `feature/lyt` |
| Git dirty at experiment start | `false` |
| Python | 3.14.6, 64-bit |
| pip | 26.1.2 |
| SQLite | 3.53.2 with FTS5 enabled |
| Catalog SHA-256 | `b74446b8074ca4f9f83a6041673377ba24af93d853c907bfe92e9a405c40c7b0` |
| Public-set SHA-256 | `571359a8a69014c43fc30d39c996c4a28e875dccc249dffc707358757beb16c0` |

## Checks

| Check | Result | Evidence |
|---|---|---|
| Fresh virtual environment and requirements install | PASS | Python 3.14 environment; `submission/requirements.txt` installed |
| Complete unit test suite | PASS | 31 tests, 0 failures |
| Official submission entry point with real catalog | PASS | Valid response with 10 unique-shaped recommendations |
| Full public baseline | PASS | 200 sessions; 945 respond calls; 0 failed calls |
| Frozen-result comparison | PASS | 200 shared sessions; 0 gained; 0 lost |
| Diagnostic trace coverage | PASS | 200 available traces out of 200 sessions |

## Reproduced metrics

| Metric | Clean clone | Frozen baseline | Difference |
|---|---:|---:|---:|
| Hit Rate@10 | 0.840000 | 0.840000 | 0 |
| MRR | 0.476401 | 0.476401 | 0 |
| MTTC | 4.885000 | 4.885000 | 0 |
| Efficiency | 0.611500 | 0.611500 | 0 |
| Recommended technical score | 0.685220 | 0.685220 | 0 |

## Evidence and limitations

- Registered experiment: `baseline_diagnostic_816f430_cleanclone`.
- Machine-readable environment, comparison, metrics, sessions, runtime events, and diagnostic traces are stored under `analysis/runs/baseline_diagnostic_816f430_cleanclone/`.
- The catalog is intentionally excluded from Git and must be supplied by the competition bundle before running the smoke test.
- The current `submission/` entry point delegates to `starter.agent.Agent`; therefore this proves a clean **whole-repository** clone works. It does not yet prove that copying `submission/` alone is a self-contained final delivery bundle.
- The new environment needed pip initialization outside the managed Windows sandbox because the sandbox denied temporary-directory writes. This was an execution-sandbox permission issue; dependency installation and all project checks subsequently passed.
