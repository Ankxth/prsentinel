# v1 parity map (feature D7)

Last updated: 2026-11-20

The plan's "done when" for feature D says the 996-test v1 behaviour must be reproduced by new tests.
This file checks that file by file. The frozen v1 suite (`bench/v1-capstone/tests`, 996 tests) is
still run unchanged by the CI job `v1-suite`, so nothing is lost; this map is about what the **new**
code (`backend/`, `action/`) proves on its own.

How the status was decided: I read each v1 test file's own description and compared it with the v2
tests that exist today. It is a file-level judgement, not a test-by-test match, so treat the
"covered" rows as "the behaviour is tested", not "every old assertion has a twin".

Statuses
- **covered**: the behaviour exists in v2 and has its own tests.
- **partly**: some of it is tested in v2; the gap is named.
- **gap**: product behaviour with no v2 tests yet. These are the real to-do list.
- **waits for F**: needs the LLM router work (retries, daily-limit stop, Gemini fallback) that is not built.
- **bench**: evaluation harness. It belongs to `bench/`, stays frozen, and is not product behaviour.
- **retired**: the module it tests is gone in v2.

| v1 test file | v1 tests | Status | v2 home and notes |
|---|---:|---|---|
| `test_app.py` | 57 | retired | the v1 web app is replaced by the FastAPI backend and dashboard |
| `test_app_github.py` | 16 | retired | same |
| `test_github_fetch.py` | 38 | retired | v2 gets files from the user's own checkout; the public check is `services/github.py` (tested in `test_repos.py`) |
| `test_diff_extractor.py` | 22 | covered | `action/tests/test_diff.py` (14 tests: both v1 example rounds, comment/whitespace-only edits, changed default/docstring/parameter, added/removed/method/nested names, syntax errors, real git revisions). The v1 command-line tests are retired with the CLI |
| `test_mutation.py` | 32 | covered | `action/tests/test_mutation.py` (21 tests: every one of the 18 faults, the cap, left-alone cases, kills, could-not-run) and `backend/tests/test_mutation.py` (10). Not carried over: the per-test "dud" list and the v1 text report |
| `test_test_generator.py` | 15 | covered | `backend/tests/test_generate.py` (23) |
| `test_llm_client.py` | 16 | partly | `backend/tests/test_llm.py` (11): Groq call, safe errors, per-purpose key and model. No Gemini fallback yet (waits for F) |
| `test_classifier.py` | 82 | partly | rule classifier: `backend/tests/test_classifier.py` (14) and `test_results.py`. The v1 AI classifier is not built in v2 (the rule classifier is the default); those tests have no twin |
| `test_repair.py`, `test_repair_e2e.py` | 53 + 5 | partly | repair rules: `backend/tests/test_repair.py` (13), `action/tests/test_testing_phase.py` (repair rounds, lost catches, unusable repairs, end to end). v1 rewrote the user's file; v2 never does, so the "real file untouched" tests are replaced by "old code is only touched in a throwaway checkout" tests |
| `test_pipeline.py` | 53 | partly | v1's one-command pipeline is split: runs, results, comment and finish in `backend/tests/test_runs.py`, `test_results.py`, `test_comment.py`; the runner loop in `action/tests/test_action.py` and `test_testing_phase.py` |
| `test_test_runner.py` | 67 | **gap** | the runner is `action/runner/pytest_runner.py`. v2 tests exist for the child environment, junit reading, repo `addopts` ignored and the end-to-end runs, but the v1 file's breadth (timeouts, collection errors, skipped tests, reruns, odd report shapes) is not yet ported |
| `test_classifier_cases.py` | 102 | bench | answer-key data in `examples/classifier_cases`; could become a v2 evaluation set later (feature D ablation) |
| `test_classifier_only.py`, `test_classifier_repeats.py`, `test_classifier_providers.py` | 36 + 25 + 17 | bench | options and reporting of the v1 evaluation script `classifier_eval` |
| `test_daily_limit.py` | 32 | waits for F | v1 stops a run when a provider is out for the day. v2 has a per-repo and per-owner token allowance (tested in `test_generate.py` and `test_repair.py`) but no provider-level daily-limit stop or retries |
| `test_pipeline_no_fallback.py` | 13 | waits for F | the `--no-fallback` switch needs the Gemini fallback to exist |
| `test_heldback_guard.py`, `test_heldback_once.py`, `test_prompt_freeze.py`, `test_real_cases.py` | 41 + 48 + 29 + 101 | bench | guards of the kept-back evaluation. They stay frozen and keep running in `v1-suite` |
| `test_repeat_runner.py`, `test_repeat_runs.py` | 10 + 14 | bench | v1 repeat-run evaluation scripts |
| `test_workflows.py` | 72 | bench | guards on v1's own evaluation workflows and their comment |
| **Total** | **996** | | |

Roll-up (v1 tests): retired 111, bench 495 (evaluation harness and answer-key data), waits for F 45,
product behaviour 345 (covered 69, partly 209, gap 67). 111 + 495 + 45 + 345 = 996.

## What is left to port (the real gaps, in order)

1. `test_test_runner.py` breadth into `action/tests/test_pytest_runner.py` (67 tests).
2. The remaining rule-classifier cases from `test_classifier.py` into `backend/tests/test_classifier.py`.
3. Repair edge cases from `test_repair.py` that still apply (a real bug or a flaky test is never repaired away: in v2 only `TEST_WRONG_ON_BEFORE` tests are sent, so check each of those v1 rules against that).
4. After feature F: retries, daily-limit stop, Gemini fallback tests.

## Decision on the "done when" of feature D (2026-11-20)

Reproducing all 996 tests is not a target: 111 test a retired app and 495 test the evaluation harness. The
project owner decided that this map is a **reference only**. Feature D is done when it works as its card in
`context.md` describes; the gaps above are optional work, to be picked up when a behaviour looks at risk.
`v1-suite` stays green on the frozen archive.
