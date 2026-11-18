# PRSentinel

An agentic AI system that watches public GitHub repositories. On every push or
pull request it keeps an up-to-date map of the project, writes tests aimed at the
change, tells a real bug from a bad or flaky test, and scans for vulnerabilities,
using an agent that tries to prove each finding with a real test run.

**Status:** v2 is being built. Nothing in `backend/`, `frontend/` or `action/` is
usable yet. The finished v1 prototype is kept, working and frozen, in
`bench/v1-capstone/`.

## Layout

| Folder | What it is |
|---|---|
| `backend/` | FastAPI service, served with uvicorn (the AI, context engine, database) |
| `frontend/` | React + Vite dashboard |
| `action/` | The GitHub Action and the runner that executes tests and scanners inside a user's repository |
| `bench/` | Evaluation: the frozen v1 project with its data and results, plus future benchmarks |
| `docs/` | Plan and design notes |

## v1 (frozen)

`bench/v1-capstone/` is the capstone prototype exactly as it was, with its own
README, tests, evaluation data and results. It is tagged `v1-capstone` in git.
To run its test suite:

```bash
cd bench/v1-capstone
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest -q
```
