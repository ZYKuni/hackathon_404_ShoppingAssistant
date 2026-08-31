# Integrated Agent clean-clone verification

## Outcome

**PASS.** A fresh clone of remote branch `lyt0830` at commit
`4e9be3cd5bf858a5c57ebdb066b9cdad85547652` completed the formal 200-session
evaluation with `dirty=false` and reproduced the submitted metrics exactly.

## Environment

| Item | Observed value |
| --- | --- |
| Branch | `lyt0830` |
| Commit | `4e9be3cd5bf858a5c57ebdb066b9cdad85547652` |
| Git dirty at experiment start and finish | `false` |
| Python | `3.14.6`, 64-bit |
| SQLite | `3.53.2`, FTS5 enabled |
| Catalog SHA-256 | `b74446b8074ca4f9f83a6041673377ba24af93d853c907bfe92e9a405c40c7b0` |
| Public-set SHA-256 | `571359a8a69014c43fc30d39c996c4a28e875dccc249dffc707358757beb16c0` |

The experiment output and registry were written outside the clone so generated
artifacts could not change its Git state.

## Verification checks

| Check | Result |
| --- | --- |
| Remote HEAD equals requested `lyt0830` commit | PASS |
| Catalog and dataset hashes match frozen artifacts | PASS |
| SQLite FTS5 available | PASS |
| Formal-path core tests | PASS — 42/42 |
| Public sessions evaluated | PASS — 200 |
| Diagnostic trace coverage | PASS — 200/200 |
| Failed Agent calls | PASS — 0 |
| Legacy comparison coverage | PASS — 200 shared sessions |
| Regression comparison | PASS — 3 gained, 0 lost |

## Reproduced metrics

| Metric | Clean clone | Submitted result | Difference |
| --- | ---: | ---: | ---: |
| Hit@10 | 0.855000 | 0.855000 | 0 |
| MRR | 0.495175 | 0.495175 | 0 |
| MTTC | 4.745 | 4.745 | 0 |
| Efficiency | 0.625500 | 0.625500 | 0 |
| Technical Score | 0.701152 | 0.701152 | 0 |

## Runtime disclosure

- initialization: `62.534 s`;
- evaluation: `348.570 s`;
- respond calls: `920`;
- P50/P95/max respond latency: `234.838/1160.173/2207.014 ms`;
- peak working set: `526,868,480 bytes` (approximately `502 MiB`);
- reported model tokens: `0`.

## Evidence

Machine-readable config, environment, metrics, comparison, sessions, runtime events,
and diagnostic traces are stored in:

`analysis/runs/integrated_guarded_safe_4e9be3c_cleanclone/`

This verifies the whole-repository clone. The `submission/` folder still delegates to
the repository's `starter` package, so a separate submission-only archive smoke test
remains necessary if the organizer requires an isolated bundle.
