# Shopping Copilot submission

This is the early participant-submission scaffold. It exposes the required `Agent` entry point and currently delegates to the frozen offline baseline in `starter/agent.py`.

## Runtime

- Python 3.10 or later; the recorded development baseline uses Python 3.14.6.
- SQLite compiled with FTS5.
- No network access, API key, model download, or third-party Python package is required.
- `data/catalog.jsonl` must be downloaded and verified as described in the repository README.

## Install

From the repository root:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r submission\requirements.txt
```

## Import smoke test

```powershell
.\.venv\Scripts\python.exe -c "from submission.agent import Agent; agent = Agent('data/catalog.jsonl'); print(type(agent).__name__); agent.connection.close()"
```

## Official interface

```python
from submission.agent import Agent

agent = Agent("data/catalog.jsonl")
agent.reset("session", {"summary": "Anonymous preference profile"})
response = agent.respond("session", "I need running shoes", 1, 10)
```

`respond(...)` returns only the official `message`, `ask_attribute`, `recommendations`, and `usage` fields. The optional diagnostic method is a development side channel and is not required by the evaluator.

## Current packaging limitation

The scaffold currently imports `starter.agent`. Before final submission freeze, copy the selected Agent and all runtime helpers into `submission/src/`, remove repository-external imports, and verify the `submission/` directory in isolation.
