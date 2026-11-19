# PRSentinel: Project Context

The one shared briefing for every chat and contributor working on PRSentinel.
Read this first. Then read only the feature card(s) you are working on.
Last updated: 2026-11-20 (feature E1: security scanners Bandit, pip-audit and detect-secrets with new-versus-old; link to one run page; feature F: retries, Gemini fallback, circuit breaker, summaries allowance; pinger running, C4 to C6 checked on Render; feature D done: D1 tables, D2 test generation, D3 runner runs the tests, D4 comment, D5 repair, D6 mutation, D7 cache and parity, D8 dashboard run page; D5 to D8 checked on real GitHub)

Plain words first, industry terms in (brackets).

---

## 1. What this project is (30 seconds)

PRSentinel is a live web service that watches **public GitHub repositories (Python only)**. On every push or pull request it:

1. keeps an up-to-date **project context** (a map of the code),
2. generates tests aimed at what the commit changed, runs them, and says whether a failure is a **real bug, bad test, or flaky test**,
3. runs **vulnerability checks**, and for some classes an **agent proves the problem with a real test run**,
4. reports back as a PR comment, a dashboard, and a README badge.

Core rules (do not break these):
- **Untrusted code never runs on our server.** Tests and scanners run in the user's own GitHub Actions runner. The server only parses source (`ast.parse`) and calls the AI.
- **The AI proposes. Running code decides.** Verdicts come from fixed rules, not from AI opinion.
- **Deterministic by default.** Agents only where the path is genuinely uncertain.
- **Honest reporting.** "Could not run" and "could not confirm" are never shown as "safe" or "passed".
- **Held-back evaluation data is never used for tuning.**

Master plan (full detail, roadmap, risks): Project doc `claude/prsentinel-v2-product-plan.md`. This file is the working summary; if they disagree on a decision, the plan wins until this file's decision log says otherwise.

---

## 2. Features at a glance (use these letters when asking for work)

| ID | Feature | One line |
|---|---|---|
| A | Accounts and repositories | GitHub login, add/remove public repos, setup page, caps, opt-in |
| B | The Action | GitHub Action + runner in the user's repo; finds changed functions, runs tests/scanners, posts the PR comment |
| C | Context engine | Per-repo code map (files, symbols, call graph, summaries), updated per commit, context packs |
| D | Test generation and failure diagnosis | Diff-aware tests, old/new runs, labels, reruns, rule classifier, mutation check, repair |
| E | Vulnerability module | E1 scanners, E2 AI triage, E3 Proof Agent (canary + sink interception, linter, judge) |
| F | LLM management | Router (Groq then Gemini), budgets, caching, usage table. Cross-cutting, see card F in section 10 |
| G | Dashboard | React pages: repos, runs, findings, context explorer, settings, public report, badge |
| H | Notifications | One PR comment updated in place, job summary |
| I | Admin and operations | Admin page, logs, error tracking, pinger |

Ownership is per feature, not per person. When you start a feature, set its status in section 9 and list the files you own in its card.

---

## 3. Architecture

```
 User's public repo                            Our side
 ┌───────────────────────┐   OIDC token    ┌──────────────────────────────┐
 │ GitHub Action         │ ───────────────▶│ backend/  FastAPI + uvicorn  │
 │  (action/ + runner)   │   REST calls    │  - verify identity           │
 │  - checkout + install │◀─────────────── │  - context engine            │
 │  - find changed funcs │ tests / agent   │  - LLM router (Groq→Gemini)  │
 │  - run tests, scanners│   steps         │  - classifier, vuln agent    │
 │  - post PR comment    │                 └──────────────┬───────────────┘
 └───────────────────────┘                                │
 ┌───────────────────────┐   GitHub login    ┌────────────▼───────────────┐
 │ frontend/ React + Vite│ ◀───────────────▶ │ Supabase: Postgres + Auth  │
 └───────────────────────┘                   └────────────────────────────┘
```

- **Action to server auth:** GitHub **OIDC token** (short-lived signed identity). Server verifies it against GitHub's published keys (JWKS) and reads `repository`, `sha`, `ref`, event. The Action skips fork pull requests (GitHub gives them no token); verified with a real fork on 2026-11-19.
- **Dashboard to server auth:** Supabase JWT from GitHub login.
- **No job queue in v1.** Work is short request/response steps. The Action retries while a free Render server wakes (sleeps after 15 min idle, about 1 min to wake). A pinger hits `/health` every ~10 min.
- **Action uploads the source it needs** (changed files, size-capped). The server does not depend on GitHub API limits; it only confirms a repo is public (cached).
- **Context lives in Postgres (JSONB).** No vector database at the start.
- **Server and runner talk through a versioned JSON contract** kept in `docs/api-contract.md` (also a Project doc, `claude/api-contract.md`; see section 5).

---

## 4. Repository map and where things live

Repo: `github.com/Ankxth/prsentinel` (branch `main`).

```
prsentinel/
  backend/      FastAPI + uvicorn (BUILT: skeleton with /health, settings, tests)
    app/
      main.py            create_app() factory; add new routers here
      api/               one router file per area (health.py, me.py, repos.py, runs.py, context.py exist)
      contract.py        CONTRACT_VERSION (Action <-> server)
      core/config.py     all settings (pydantic-settings); add new settings here
      core/auth.py       Supabase token check; get_claims, current_user dependencies
      core/oidc.py       GitHub identity token check; github_claims, current_repo dependencies
      core/run_token.py  our per-run token; create_run_token, current_run dependency
      services/comment.py  builds the PR comment text (untrusted text goes in code spans)
      services/github.py repo lookup on GitHub's open API (strict name check, one fixed host)
      db/                base.py (Base), session.py (engine, get_db), models/ (one file per area)
      services/context_engine/  store.py: begin_build, save_files, commit_build (the code map)
      services/llm/      __init__.py (complete, LLMResult, LLMError, get_llm), groq.py: the minimal router (F)
      services/          classifier.py (labels, rule classifier), generator.py (test-writing prompt), llm_usage.py (daily allowance), run_functions.py (D), findings.py (scanner results view, E)
      agents/            (planned) vuln_proof/
    migrations/          Alembic: env.py, versions/ (0001 empty baseline, 0002 users + repos, 0003 runs, 0004 context, 0005 edge status, 0006 run_functions + test_results, 0007 llm_usage, 0008 findings)
    alembic.ini
    tests/
    requirements.txt     pinned
    .env.example
  frontend/     React + Vite + TypeScript (BUILT: GitHub sign-in, repos page; src/lib/api.ts, src/components/)
  action/       BUILT v0: action.yml, runner/ (diff.py, facts.py, client.py, comment.py, context_sync.py, pytest_runner.py, testing_phase.py, mutation.py, scanners.py, main.py; Python standard library only), tests/ (unittest, fake server)
  bench/        evaluation data and scripts; v1 frozen under bench/v1-capstone/
  docs/         context.md (this file), api-contract.md (BUILT, Action <-> server), v1-parity.md (v1 to v2 test map), research notes
  .github/workflows/ci.yml   jobs: v1-suite, backend, frontend, action
```

`bench/v1-capstone/` is the **frozen v1 project** (996 tests passing). It is a reference and evidence archive. Do not edit it. To reuse v1 code, copy the module into the new location and write new tests there.

### Where v1 code is meant to go (reuse map)

| v1 module (in `bench/v1-capstone/src/prsentinel/`) | New home | Feature |
|---|---|---|
| `diff_extractor.py` | `action/runner/diff.py` (DONE, stdlib-only, compares git revisions) | B / D |
| `test_runner.py`, `mutation.py` | `action/runner` | D |
| `test_generator.py` | `backend/app/services` | D |
| `llm_client.py`, `config.py` | `backend/app/services` + `core` | F |
| `classifier.py` (rule + AI) | `backend/app/services/classifier` | D |
| `repair.py`, repair part of `pipeline.py` | backend orchestrates, runner executes | D |
| `pipeline.py` | split into backend run orchestration + runner loop | B / D |
| `app.py`, `static/index.html`, `github_fetch.py` | retired | n/a |

### Local setup

```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate   # Windows Git Bash: .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload          # http://127.0.0.1:8000/health
python -m pytest -q

# frontend
cd frontend && cp .env.example .env.local && npm install && npm run dev   # http://localhost:5173
```

Python 3.10+ works locally; CI uses 3.13. Node 22.

---

## 5. Interfaces between features (the contracts)

These are the seams. Change one only through the Requests board (section 11), because other features depend on it.

**5.1 Action-facing API (auth: GitHub OIDC)**
- `POST /v1/runs` start a run for (repo, sha, event); returns run id and the changed functions to test
- `POST /v1/runs/{id}/generate` tests for one function (context-aware)
- `POST /v1/runs/{id}/results` upload test results and scanner output
- `POST /v1/runs/{id}/repair` fix tests that fail on the old code too (D5)
- `POST /v1/runs/{id}/mutation` report how many deliberate faults the tests caught (D6)
- `POST /v1/runs/{id}/findings` report what the security scanners found and which findings are new in this commit (E1)
- `POST /v1/runs/{id}/findings/{fid}/agent/step` Action sends last tool result; server returns next action
- `POST /v1/runs/{id}/finish` classify, triage, store, return comment markdown

**5.2 Dashboard-facing API (auth: Supabase JWT)**
- `GET /v1/me`, `DELETE /v1/me` (delete account and data)
- `GET/POST /v1/repos`, `DELETE /v1/repos/{id}`
- `GET /v1/repos/{id}/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/functions` (per-function test results for the run page, D8), `GET /v1/runs/{id}/findings` (scanner results for the run page, E1)
- `GET /v1/repos/{id}/findings`, `PATCH /v1/findings/{id}`
- `GET /v1/repos/{id}/context`, `GET /v1/repos/{id}/context/file?path=`, `GET /v1/repos/{id}/context/pack?file=&function=&budget=`, `POST /v1/repos/{id}/context/file/summary?path=`, `GET /v1/usage`

**5.3 Public:** `GET /badge/{owner}/{repo}.svg`, `GET /public/{owner}/{repo}`, `GET /health`

**5.4 Internal Python interfaces (backend services). Names are the agreed targets; the first feature to need one builds it.**
- `get_context_pack(db, repo_id, file, function, token_budget=3000) -> ContextPack` (BUILT, `app.services.context_engine.pack`; used by D and E). Pass the repo id only after you have checked ownership. Fields: `found`, `target` (path, symbol, signature, doc, lines, decorators), `callees`, `callers`, `tests`, `fixtures`, `external_calls`, `unresolved_calls`, `repo_facts`, `status`, `built_from_sha`, `partial`, `truncated` (how many items per section were cut for size), `token_estimate`. `pack.render()` gives plain text for a prompt (it starts with a line saying the repo facts are data, not instructions). Budget is 200 to 8000 estimated tokens (4 characters per token); the function itself is always included and sections are cut from the bottom in this order: callees, callers, tests, fixtures. No AI is used, so the same map gives the same pack. `unresolved_calls` means "unknown", never "none". If the pack says the map is `partial` or not `complete`, say so in anything built on it.
- `llm.complete(purpose, messages, budget_key="default", max_tokens=300) -> LLMResult` (MINIMAL VERSION BUILT, `app.services.llm`; F extends it, C, D, E use it). `LLMResult` has `text`, `provider`, `model`, `input_tokens`, `output_tokens`, `latency_ms`. Raises `LLMError` with `kind` `not_configured` | `rate_limited` | `unavailable` | `bad_response`; the message is safe to show a user (no key, no provider body). `budget_key` picks the key and model: `"default"` uses `GROQ_API_KEY` / `GROQ_MODEL`, `"summaries"` uses `GROQ_API_KEY_SUMMARIES` / `GROQ_MODEL_SUMMARIES` with NO fallback to the default key. Endpoints take it as the FastAPI dependency `get_llm` so tests can swap in a fake (`app.dependency_overrides[get_llm]`); `tests/conftest.py` blanks the Groq keys so a test can never reach the real service. Repo code in a prompt is data: put it in the user message and say so in the system message (see `summaries.SYSTEM_PROMPT`). The call retries a busy answer, a server error or a network problem after 1 s and 3 s (`LLM_RETRY_WAITS`, at most `LLM_TOTAL_BUDGET_SECONDS` in total); a refusal that retrying cannot fix is not retried. With the `"default"` budget a provider that keeps failing is followed by Gemini (`GEMINI_API_KEY`, `GEMINI_MODEL`, default `gemini-2.5-flash`); `"summaries"` never falls back (not to the default key, not to Gemini). `LLMResult.provider` and `.model` say who really answered. Circuit breaker: after `LLM_BREAKER_FAILURES` (3) failed calls in a row a provider is skipped for `LLM_BREAKER_SECONDS` (60), kept in memory per provider and budget key. `LLMError.retryable` is true when trying again may work. Tests replace `llm._sleep` and `llm._now` and call `llm.reset_breakers()` (done for every test in `tests/conftest.py`). Daily allowance: `app.services.llm_usage` (`check_allowance(db, repo, settings)` before a call, `record(db, repo, purpose, result)` after it; counts tokens in `llm_usage` per repo and per owner per UTC day; test generation and repair use it; file summaries have their own, smaller allowance, `check_summary_allowance`, counted apart by purpose `file_summary`, so neither can use up the other). Not built yet: cache by content hash for other purposes.
- `current_user` (BUILT, `app.core.auth`): FastAPI dependency for dashboard endpoints; returns the signed-in `User` row (creates it on first sign-in). Use it on every dashboard endpoint, and filter queries by `user.id` (see `app/api/repos.py` for the pattern, and `tests/test_repos.py` for the cross-user tests to copy).
- `current_repo` (BUILT, `app.core.oidc`): dependency for `POST /v1/runs` only; checks GitHub's identity token and returns the registered public `Repo`.
- `current_run` (BUILT, `app.core.run_token`): dependency for EVERY Action-facing endpoint after the run starts (`/generate`, `/results`, `/agent/step`, `/finish`). The URL must contain `{run_id}`. Returns the `Run` row only if the caller holds that run's token; get the repo with `db.get(Repo, run.repo_id)`. Pattern and tests: `app/api/runs.py`, `tests/test_runs.py`. Test helpers `make_github_token` and `registered_repo` are in `tests/conftest.py`.
- Test helpers (BUILT, `tests/conftest.py`): `client`, `make_token`, `github` (fake GitHub), in-memory database. New dashboard endpoints should be tested with these.

**5.5 Contract file:** request/response shapes for 5.1 live in `docs/api-contract.md` with a `contract_version` number. Action and server both send/check it. Additive changes only without a version bump.

---

## 6. Data ownership

One table has one owning feature. Others read it through that feature's service or through a documented query, and ask the owner for column changes.

| Table | Owner | Notes |
|---|---|---|
| `users`, `repos`, `quotas`, `audit_log` | A | `repos.settings` JSON holds per-repo options |
| `runs` | B | status lifecycle, commit sha, event, summary JSON |
| `context_state`, `context_files`, `context_edges` | C | every row records `built_from_sha`; migration `0004` |
| `run_functions`, `test_results` | D | labels, verdicts, reruns, mutation score |
| `findings`, `finding_attempts` | E | fingerprint, triage, proof status, attempts |
| `llm_usage` | F | tokens, provider, latency, purpose |

Rules (apply everywhere):
- Migrations with **SQLAlchemy + Alembic** (decided). Put a model in `backend/app/db/models/<area>.py` and import it in `models/__init__.py`, then `alembic revision --autogenerate -m "..."` from `backend/`, read the generated file, and apply with `alembic upgrade head`. One migration per change; never edit an applied migration; keep a single head (a test checks this). Needs `DATABASE_URL`. Use Supabase's **Session pooler** connection string (the Direct string is IPv6-only by default and often fails on home networks and free hosts).
- Supabase **Row Level Security** so users only read their own repos' rows. The backend uses the service key, so **every backend query that returns user data must filter by owner explicitly and have a test proving another user cannot read it.**
- Store little source: symbols, short snippets, summaries. Not whole repos.

---

## 7. Conventions

**Code**
- Backend: Python, FastAPI, routers in `app/api/`, logic in `app/services/`, settings only through `core/config.py`. Type hints on public functions.
- No secret in code, logs, or error messages. Secrets are `SecretStr` and live in `.env` locally and in Render/Supabase settings in production.
- Frontend: React + TypeScript (strict), API base URL from `VITE_API_URL`. Only `VITE_` values reach the browser, so no secrets there.
- Treat AI output and scanner output as untrusted: escape when rendering; AI output only runs as a linted test in the user's runner.
- Dependencies pinned in `requirements.txt`; add a dependency only with a reason.

**Tests**
- Every feature ships tests. The CI jobs `backend` and `frontend` must stay green.
- LLM calls are mocked in tests. A live-LLM check is a separate, clearly named script.
- Test new code with its own tests; do not rely on `bench/v1-capstone` tests for new code.

**Git**
- Small commits with plain messages. Do not commit `.env`, `node_modules/`, `dist/`.
- Tag `v1-capstone` marks the pre-v2 state.

**Evaluation (bench/)**
- The held-back split is declared once and never used for tuning. Every reported number cites the saved file it came from. Report negative results.

**Wording rules in user-facing text**
- Generated tests: "Tests are AI-generated. A catching test is a hint, not proof."
- Verified findings: "reproduced by a test run", never "proven secure". "Could not confirm" is never "safe".

---

## 8. Decisions

Settled:

| Topic | Decision |
|---|---|
| Scope at launch | Public repos only, Python only, GitHub login |
| Where tests run | User's GitHub Actions runner, never our server |
| Backend | FastAPI served by uvicorn |
| Frontend | React + Vite + TypeScript |
| Database and auth | Supabase Postgres and GitHub login (Neon as fallback) |
| LLM | Groq primary, Gemini fallback |
| Hosting | Free tiers first (Render + Supabase), pinger, $7/mo always-on later. Do not use Render free Postgres |
| Layout | `backend/`, `frontend/`, `action/`, `bench/`, `docs/`; v1 frozen in `bench/v1-capstone/` |
| Proof Agent | Canary + sink interception, linter, deterministic judge, at most 5 attempts per finding |
| Scanners (E1) | Bandit, pip-audit and detect-secrets, installed by the runner in a throwaway virtual environment (pinned versions), run on the new code and on the old code. The PR comment lists only new findings and counts the old ones. Semgrep is not used. Decided 2026-11-20 |
| Migrations | **Alembic** (SQLAlchemy 2). RLS policies are written as raw SQL inside migrations. Decided 2026-11-18 |
| Where results show | The GitHub Action log, the job summary and the PR comment stay as they are. The dashboard gets a run page showing the same per-function results (tests, labels, repairs, mutation), so a user can follow a run from the frontend. Decided 2026-11-20; design agreed the same day (labels and diagnoses only, no test code sent to the browser; the page asks again every few seconds while the run is going; built as D8) |
| v1 parity | Not a requirement. Feature D is judged against its own card, not against the 996 v1 tests. Decided 2026-11-20 |

Still open (decide before the feature that needs it):
1. Licence: MIT or Apache-2.0.
2. Dashboard hosting: Render static vs Vercel/Cloudflare Pages.
3. Scanner set: settled 2026-11-20 (see the table above).
4. Agent oracle: sink interception (recommended) vs real execution.
5. Fix-check in v1 or future work.
6. Attempt limit: 5 (recommended).
7. Name: keep "PRSentinel" or rename.

---

## 9. Status board

Update your row when you start, finish, or get blocked. Statuses: `not started`, `in progress`, `done`, `blocked`.

| Feature | Status | Notes |
|---|---|---|
| Foundation (repo layout, CI, skeletons) | done | v1 frozen in `bench/v1-capstone`; backend `/health`, frontend skeleton, CI jobs added; pushed to GitHub |
| A Accounts and repositories | done | Backend done: tables applied to Supabase, token check, `GET /v1/me`, `GET/POST/DELETE /v1/repos` with tests (55 backend tests pass). Frontend sign-in and repos pages work end to end (user tested: sign in, add, remove, rows appear and disappear in Supabase). Delete-my-account built (59 backend tests). Setup snippet and per-repo runs list built (user verified in the dashboard). Left (small): cache the public check |
| B The Action | in progress | Server side built: GitHub token check, `runs` table (migration `0003`), run token, `POST /v1/runs`, `POST /v1/runs/{id}/finish`, dashboard run views, `docs/api-contract.md` (94 backend tests). Migration 0003 applied; backend deployed on Render (`https://prsentinel-21ho.onrender.com`). Action v0 built and tested on real GitHub with `Ankxth/prsentinel-demo` (see change log): finds changed functions, starts and finishes a run, posts one PR comment and updates it on new pushes, reports "could not run" honestly. Test generation (D) and the security scanners (E1) are wired in. Dashboard runs list built; fork-PR skip verified with a real fork. Pinger running (UptimeRobot, see I). Test generation is wired in (feature D) |
| C Context engine | in progress | C1 done (runner fact extractor, `action/runner/facts.py`). C2 server side done: tables (migration `0004`, applied), manifest, files and commit endpoints, dashboard `GET /v1/repos/{id}/context` (118 backend tests). C2b done: the runner uploads the map on every run (34 Action tests, verified on real GitHub). C3 done: call graph built on every commit (migration `0005`, applied, verified in Supabase). C4 done: `get_context_pack` (143 backend tests). C5 done: dashboard "Code map" view (145 backend tests). C6 done: AI file summaries written on request, with the minimal LLM router (181 backend tests; checked with real Groq from a local backend on 2026-11-19: the summary of the demo repo's `calculator.py` matched the file). Checked on the Render deployment on 2026-11-20 (demo repo): Code map "Ready" and current, AI summary of `calculator.py` correct, context pack for `divide` shown. Nothing left for the map itself; open: summaries are not part of the context pack |
| D Test generation and diagnosis | done | D1 done: tables `run_functions` and `test_results` (migration `0006`), labels and the rule classifier on the server, rows created at run start (205 backend tests). D2 done: `POST /v1/runs/{id}/generate`, test-writing prompt with the context pack, cache by hash, daily token allowance, table `llm_usage` (migration `0007`) (234 backend tests). D3 done: the runner generates, runs the tests on the old and the new code with reruns and reports to `POST /v1/runs/{id}/results` (checked on the real demo repo: the `break-divide` PR gives `CATCHES_CHANGE` for the zero-division test). Prompt `d2-2` aims the tests at what changed between old and new. D4 done: the PR comment shows per-function test results (checked live on the demo repo). D5 done: `POST /v1/runs/{id}/repair` and the runner repair loop for tests that fail on the old code too (277 backend tests, 60 Action tests); the repair step is covered by automated tests only, because the AI's tests passed first time in the live check and a repair cannot be forced. D6 done: the runner mutates the old code and `POST /v1/runs/{id}/mutation` stores the counts (287 backend tests, 72 Action tests); checked live (mutation check: 3 killed, 0 survived, 0 equivalent). D7 done: repaired tests are stored so the cache starts from them, and `docs/v1-parity.md` maps all 996 v1 tests (293 backend tests, 97 Action tests). D8 done: the dashboard run page (`GET /v1/runs/{id}/functions`, `frontend/src/components/RunView.tsx`; 304 backend tests). Checked live on 2026-11-20 on the demo repo (PR #7, `divide` changed to round to 2 decimals): the Action log, the PR comment and the dashboard run page gave the same counts (8 tests match the new behaviour, 5 found no difference), and the mutation check caught 3 of 3 faults. Nothing left for D except the optional v1 parity gaps |
| E Vulnerability module | in progress | E1 done: Bandit, pip-audit and detect-secrets run in the user's runner on the new and the old code; table `findings` (migration `0008`); `POST /v1/runs/{id}/findings`; "Security checks" section in the PR comment (only new findings listed, old ones counted); findings on the dashboard run page (`GET /v1/runs/{id}/findings`). 367 backend tests, 126 Action tests (29 new, 2 of them use the real tools when installed). Checked live on 2026-11-20 on the demo repo (`risky-shell` PR: Bandit B602 and B404 shown as new; a pinned `requests==2.19.0` gave 5 pip-audit findings). Not built: E2 AI triage, E3 proof agent, dismissing findings, a repo-wide findings list |
| F LLM management | done (first version) | Router in `backend/app/services/llm/`: Groq with retries (1 s, 3 s), Gemini fallback for the `default` budget (test generation) when Groq keeps failing, circuit breaker (3 failed calls in a row skip a provider for 60 s), one key and model per `budget_key`, errors mapped to safe messages. Daily token allowance per repo and per owner (`llm_usage`) for test generation and repair, plus a separate smaller allowance for file summaries (40000 per repo, 80000 per owner). 342 backend tests (38 new, all with fake providers). Not yet checked live: the Gemini fallback (needs `GEMINI_API_KEY` on Render). Not built: cache by content hash for other purposes |
| G Dashboard | in progress | Repos page, runs list, code map and a run page (D8): "Details" on a run shows each changed function with its tests, labels, repairs and mutation check, refreshing itself while the run is going. A run now has its own address (`#/runs/<id>`) with a Copy link button; the PR comment links to it when `PUBLIC_DASHBOARD_URL` is set. The run page also shows the scanner findings (E1). Not built: a repo-wide findings list, settings, public report, badge |
| H Notifications | not started | Part of B's PR comment |
| I Admin and operations | started | Pinger done (2026-11-20): UptimeRobot free plan checks `https://prsentinel-21ho.onrender.com/health` every 5 minutes, so the free Render server does not sleep, and it emails the owner if the server is down. Not built: admin page, error tracking, log view |

Roadmap order (from the plan): P0 foundation (done) → P1 walking skeleton (OIDC, run lifecycle, Action v0, PR comment, deploy) → P2 context engine → P3 vulnerability module → P4 polish → P5 hardening and launch → P6 scale.

---

## 10. Feature cards

Each card: purpose, depends on, provides, key files, first steps, done when.

### A. Accounts and repositories
- **Purpose:** users sign in with GitHub, add/remove public repos, see setup instructions.
- **Depends on:** Supabase project (Auth + Postgres); Alembic is already set up.
- **Provides:** `users`, `repos` tables; `current_user` dependency; `GET/POST /v1/repos`, `DELETE /v1/repos/{id}`; repo cap and LLM opt-in flag; setup page data (workflow file text).
- **Key files:** `backend/app/api/repos.py`, `backend/app/core/auth.py`, `backend/app/db/`, `frontend/src` login and repos pages.
- **Done so far:** `users`/`repos` tables, Supabase token verification, `/v1/me`, repo endpoints with public check.
- **Also done:** frontend GitHub sign-in (supabase-js) and repos page (`frontend/src/components/`, `frontend/src/lib/api.ts` adds the token to every backend call; other features' pages should reuse `apiFetch`). `DELETE /v1/me` deletes the user and everything under them.
- **Rule for every feature:** any table that holds user data must cascade from `users` (directly or through `repos`), so account deletion removes it. Add a test when you add a table (see `tests/test_delete_account.py`).
- **Also done:** per-repo panel ("Runs and setup") with the copyable workflow file and the latest runs (`frontend/src/components/RepoPanel.tsx`, `listRuns` in `api.ts`). The snippet uses `VITE_PUBLIC_API_URL` (default the Render URL).
- **Next steps:** cache the public check.
- **Done when:** a user signs in locally, adds a public repo, sees the workflow snippet, deletes the repo and their data; tests prove one user cannot read another's repos.

### B. The Action
- **Purpose:** the GitHub Action and runner that executes inside the user's repo.
- **Depends on:** A (`repos`), contract file; OIDC verification on the server.
- **Provides:** `runs` table; `current_repo` dependency (OIDC); the five endpoints in 5.1; `action/action.yml`; `action/runner/`; `docs/api-contract.md`; PR comment posting (H).
- **Key files:** `backend/app/core/oidc.py`, `backend/app/api/runs.py`, `backend/app/services/run_service.py`, `action/`.
- **Done so far (server):** GitHub token check, `runs` table, run token, start and finish endpoints, dashboard run views, contract doc.
- **Done so far (Action v0):** `action/action.yml` (composite; inputs `api-url`, `audience`, `github-token`, `fail-on-error`), stdlib-only runner, 11 tests (`python -m unittest discover -s action/tests`), CI job `action`. Verified on the demo repo: push run writes the job summary, pull request run posts one comment, a later push updates that same comment, and a repo that is not added gives a clear "could not run, nothing was checked" message.
- **Next steps:** none for E1 (wired in). The scanners are installed on every run (about 30 to 60 seconds); an Action input `run-scanners` (default true) switches them off. Known trade-off: the check stays green when the Action could not run (default `fail-on-error` false), so a title or warning marker may be added later.
- **Done when (P1 exit):** opening a PR on the demo repo produces a comment and the run is visible on the live dashboard. Test step runs with a scrubbed environment (no token, no secrets); "could not run" is reported distinctly.

### C. Context engine
- **Purpose:** an incrementally updated map of each repo.
- **Depends on:** B (source arrives through runs), F (summaries).
- **Provides:** `context_files`, `context_edges`; `get_context_pack(...)`; `GET /v1/repos/{id}/context`; freshness (`built_from_sha`, "N commits behind").
- **Key files:** `backend/app/services/context_engine/` (indexer, symbols, graph, summaries, pack builder).
- **Design (agreed 2026-11-19):** the runner parses the code and uploads only facts (hashes, symbols, imports, call names, no bodies). Each run sends a manifest `{path: hash}`; the server answers which files it needs, the runner uploads those in batches, then commits. The server builds the links (call graph) from all stored facts. Limits: 500 files, 1 MB per file, 2 MB per upload; beyond them the map is marked partial and shown as partial. AI summaries are kept (C6), generated lazily, using a separate Groq key (`GROQ_API_KEY_SUMMARIES`, added in C6).
- **Done so far:** C1 `action/runner/facts.py` (29 Action tests). C2 server: `app/db/models/context.py`, `app/services/context_engine/store.py`, `app/api/context.py`, `tests/test_context.py`. Endpoints are in `docs/api-contract.md`.
- **Also done:** C2b `action/runner/context_sync.py` (manifest, upload only needed files in batches, commit; called from `main.py` before finish; any failure is a warning and never fails the run). `facts.config_hints` sends `pytest_config`.
- **Also done (C3):** `app/services/context_engine/graph.py` turns stored facts into links; `store.rebuild_edges` runs inside `commit_build` (whole graph rebuilt each commit, since a change in one file can change links in another). Each link is `resolved` (one place in the repo), `external` (built-in, standard library, installed package) or `unresolved` (never guessed). It resolves same-file names, `self.method()` including inherited methods, imports (relative, aliased, re-exported through `__init__.py`). It does NOT resolve calls on variables (`obj.method()`), the type is unknown from names alone. Migration `0005` adds `status` and `line` to `context_edges`. Accuracy test: `tests/test_graph.py` (22 hand-labelled calls: 21 correct, 1 missed, 0 wrong). Coverage on this repo's `backend/` (1001 calls): 270 resolved, 347 external, 384 unresolved. Callers of a function = edges with that `dst_path`/`dst_symbol`.
- **Also done (C4):** `app/services/context_engine/pack.py` (`get_context_pack`, `ContextPack.render`), preview endpoint `GET /v1/repos/{id}/context/pack`, `tests/test_pack.py`. A class pack includes the calls made by its methods; callers that are test files are listed under `tests`; fixtures come from the covering test files and every `conftest.py`.
- **Also done (C5):** dashboard "Code map" section in the repo panel (`frontend/src/components/ContextView.tsx`, calls in `lib/api.ts`): status and partial badges, freshness (built commit vs latest run), file list, click a file to see its symbols, click a symbol to see the exact context pack text. New endpoint `GET /v1/repos/{id}/context/file?path=`. "N commits behind" is not shown (it would need the GitHub API); the view says when a newer run has not finished updating the map.
- **Also done (C6):** `app/services/context_engine/summaries.py` and `app/services/llm/`. `POST /v1/repos/{id}/context/file/summary?path=` (owner-only; 403 if `llm_opt_in` is false; 404 unknown file; 422 nothing to summarize; 429 / 502 / 503 for model trouble) returns the stored summary or writes one. A summary is stored in `context_files.summary` with `summary_hash` = the `content_hash` it was written for; `GET .../context/file` returns `summary` and `summary_current` (false once the file changed). The model sees only names, signatures, first docstring lines and import names (never source), capped at about 6000 characters; the answer is cut to 600 characters. A failed call stores nothing and keeps any older summary. The dashboard writes a summary when a file is opened and none is current. No migration (columns existed since `0004`). Tests: `tests/test_llm.py`, `tests/test_summaries.py` (fake model). Not done: summaries are not yet part of the context pack; summaries now have their own daily allowance (feature F; a stored current summary stays free to read, a refused one answers 429 "today's allowance for file summaries has been used up").
- **Checked live (C6):** local backend + real Groq (`openai/gpt-oss-20b`), dashboard summary of the demo repo's `calculator.py` named the right functions and left out a deleted one. First attempt failed on a key pasted twice (112 characters instead of 56, Groq 401), then on a retired model (404 `model_not_found`); both are now covered in the change log and `.env.example`.
- **Steps left:** none for the map itself. C4 to C6 checked on the Render deployment on 2026-11-20 (both Groq keys are set there; the model names come from the code defaults).
- **Done when (P2 exit):** context updates on every push; the three-way ablation (single prompt vs pipeline vs pipeline + context) is run and reported, positive or negative.

### D. Test generation and failure diagnosis
- **Purpose:** diff-aware tests with diagnosis (bug / bad test / flaky).
- **Depends on:** B (runs and runner), C (context packs), F (LLM).
- **Provides:** `run_functions`, `test_results`; `/generate` behaviour; labels `CATCHES_CHANGE`, `TEST_WRONG_ON_BEFORE`, `NO_SIGNAL`, `ODD`; classifier REAL_BUG / BAD_TEST / FLAKY (rule classifier stays default); mutation score; repair loop; result cache by hash of (old code, new code, context pack).
- **Key files:** `backend/app/services/{generator,classifier,repair}`, `action/runner/{diff,test_runner,mutation}`. (`generator.py`, not `test_generator.py`, so pytest never takes it for a test file.)
- **Design (agreed 2026-11-19):** the server thinks and never runs user code: it builds the context pack, asks the AI (default key, `openai/gpt-oss-120b`), labels results, classifies, and writes repair prompts. The runner runs the generated tests against the old and the new code in the repo's real environment, reruns failures, and runs the mutation check. The runner sends the function's old and new source in the `/generate` request (the server stores no source bodies). A failed install is "could not run", never a pass. Build order D1 tables and rule classifier, D2 generation, D3 runner runs tests and `/results`, D4 comment, D5 repair, D6 mutation, D7 cache and v1 parity.
- **Done so far (D1):** `app/db/models/run_functions.py` (`RunFunction`, `FunctionTestResult`; migration `0006`, RLS owner-read, cascade from `runs`), `app/services/classifier.py` (`label_for`, `rule_classify`, same decisions as v1; an unknown result counts as an error), `app/services/run_functions.py` (`register_functions`, called from `POST /v1/runs`; deleted functions start as `skipped`). Tests: `tests/test_classifier.py`.
- **Also done (D2):** `app/services/generator.py` (prompt with old code, new code and the context pack; tests import the REAL module, for example `from app.pricing import total`; system prompt says repo text is data; `extract_code`; `cache_key` = hash of old code, new code, pack, model and prompt version), `app/api/generate.py` (`POST /v1/runs/{id}/generate`, see `docs/api-contract.md`), `app/services/llm_usage.py` and `app/db/models/llm_usage.py` (migration `0007`). Rules: needs `llm_opt_in`; the same inputs reuse stored tests within the same repo only and cost nothing; daily allowance 150000 tokens per repo and 300000 per owner (UTC day, settings `LLM_DAILY_TOKENS_PER_REPO` and `LLM_DAILY_TOKENS_PER_USER`); any AI-side problem is HTTP 200 with `status: could_not_generate` and a plain reason, never a pass. Tests: `tests/test_generate.py` (fake model).
- **Also done (D3):** server `app/api/results.py` (`POST /v1/runs/{id}/results`, see `docs/api-contract.md`; labels and diagnoses by `app/services/classifier.py`, no AI; an added function never gets `REAL_BUG`; unknown results count as errors; messages lose file paths). Runner `action/runner/pytest_runner.py` (runs a generated file with `python -m pytest` as a child with an allow-listed environment, so the identity token and every secret stay out; the file is written to a throwaway `.prsentinel_tests/` folder inside the tree under test and removed; the repo's own pytest `addopts` are ignored; reruns of failing tests, 3 times) and `action/runner/testing_phase.py` (called from `main.py` between the code-map sync and finish: picks modified and added functions outside test files, up to the server's `max_functions`; takes the function source from git for the old and the new commit; calls `/generate`; runs the tests in the real working tree for the new code and in a `git worktree` of the base commit for the old code, with the same installed packages; posts results). Any problem is "no result" for that function and never fails the user's CI; a 10-minute budget stops new functions. New Action input `run-tests` (default true). The user's workflow must install the project's dependencies and pytest BEFORE the Action step (`actions/setup-python`, `pip install pytest` and the project's requirements); without pytest the runner says so in a warning and writes no tests. Tests: `action/tests/test_testing_phase.py` (13 need pytest, they are skipped without it), `backend/tests/test_results.py`.
- **Also done (D4):** `app/services/comment.py` (`build_comment(run, repo, db)`) lists every changed function with its results: a folded `<details>` block (its summary line is HTML, because GitHub does not render Markdown inside `<summary>`), only the newest repair round, the fixed sentence "Tests are AI-generated. A catching test is a hint, not proof.", and functions with no result shown as "not tested" or "no test result was reported", never as a pass. Test names, reasons and failure texts are escaped (code spans or HTML escaping). Tests: `tests/test_comment.py`.
- **Also done (D5):** server `app/api/repair.py` (`POST /v1/runs/{id}/repair`, see `docs/api-contract.md`), prompt `generator.build_repair_messages` and `generator.check_repaired`. Runner `testing_phase._repair`: after the first results, only tests labelled `TEST_WRONG_ON_BEFORE` (fail on the old code too) are sent, up to 2 rounds per function. A test that fails on the old code and passes on the new one (`ODD`) is NOT repaired: it shows that the behaviour changed. A repair is used only if every test that caught the change before still catches it (`lost_catches`); otherwise the first results stay. A failed repair never changes the function's earlier results. Same daily allowance as `/generate` (purpose `test_repair`). The server keeps no repaired file, so a cache hit in a later run starts from the first tests again (D7 may store the repaired version). The comment also says when tests were repaired and words an `ODD` test as "matches the new behaviour: check that this change is intended". Tests: `backend/tests/test_repair.py`, `action/tests/test_testing_phase.py`.
- **Also done (D6):** runner `action/runner/mutation.py` (port of v1 `mutation.py`: the same 18 faults in the same fixed order, cap 20 faults per function, 30 s per run, about 90 s per function) and server `app/api/mutation.py` (`POST /v1/runs/{id}/mutation`, see `docs/api-contract.md`). After the first results and any repair, `testing_phase._mutation` makes one fault at a time in the OLD version of the function, inside the throwaway checkout of the old commit (never the user's tree; the file is put back byte for byte), and runs the FINAL tests against each fault. Only tests that pass on the plain old code count; a fault is killed when one of them fails. Equivalent faults, timeouts and faults that cannot run are counted apart and stay out of the score; the server works out `killed / (killed + survived)` and stores it in `run_functions.mutation` (no migration, the column existed since `0006`). Only `modified` functions are checked (an added function has no old code). Nothing scored means "not measured" with a reason, never a good score. The comment adds one line per function ("caught K of N small deliberate faults"), the faults that survived, and always says the score is a lower bound. A problem in this step changes nothing else. Tests: `backend/tests/test_mutation.py`, `action/tests/test_mutation.py`, `action/tests/test_testing_phase.py`.
- **Also done (D7):** the stored tests of a function now follow its repairs: the runner sends the repaired file back as optional `test_code` with the repaired `/results` (attempt 1 or 2), and the server keeps it (only if it still has the import line and a test), so a cache hit in `/generate` starts from the repaired tests. Cache key and rules are unchanged (same old code, new code, context, model and prompt version, within one repo). `docs/v1-parity.md` maps every v1 test file to its v2 home: retired 111, bench 495, waits for F 45, product behaviour 345 (covered 69, partly 209, gap 67), and lists the gaps to port. Ported now: `action/tests/test_diff.py` (14) and the mutation operator and kill tests (`action/tests/test_mutation.py`, 21). Tests: `backend/tests/test_results.py`, `backend/tests/test_repair.py`.
- **Also done (D8):** the dashboard run page. `app/services/run_view.py` builds the data from `run_functions` and `test_results` using the comment's own bucket rules (`_latest_attempt`, `_kind`), so the page and the PR comment cannot disagree; `app/api/run_view.py` serves `GET /v1/runs/{id}/functions` (owner-only, see `docs/api-contract.md`). It sends names, results, labels, diagnoses, short failure texts and the mutation counts, never the generated test code. Frontend: `frontend/src/components/RunView.tsx` (opened by the "Details" button on a run in `RepoPanel.tsx`; one card per function; polls every 4 seconds while the run is `running`, stops after 20 minutes with a note and a Refresh button; all repo text is rendered as plain text by React); types and `getRunFunctions` in `frontend/src/lib/api.ts`. No migration, no Action change. Tests: `backend/tests/test_run_view.py` (11, including another user getting 404 and the test code never being sent). The Action log, job summary and PR comment are unchanged. A link straight to one run was added afterwards (see the change log, 2026-11-20 | G, B).
- **Done when (decided 2026-11-20):** feature D works as this card describes: generated tests run in the real repo environment, results are labelled and diagnosed, repaired and mutation-checked, shown in the PR comment and in the dashboard run page, and every comment keeps the fixed sentence. The v1 test map (`docs/v1-parity.md`) is a reference only; matching the 996 v1 tests is NOT required and closing its gaps is optional.

### E. Vulnerability module
- **Purpose:** scanners, AI triage, and the Proof Agent.
- **Depends on:** B (runner executes scanners and proof tests), C (context packs), F (LLM).
- **Provides:** `findings`, `finding_attempts`; `agent/step` endpoint; scanner wrapper in `action/runner`; test linter; deterministic judge.
- **Key files:** `backend/app/agents/vuln_proof/`, `backend/app/services/findings.py`, `action/runner/scanners/`, `action/runner/test_linter.py`.
- **Done so far (E1, 2026-11-20):** runner `action/runner/scanners.py` (stdlib only): makes a throwaway virtual environment outside the repo, installs the pinned tools (`bandit==1.9.4`, `pip-audit==2.10.1`, `detect-secrets==1.5.0`), runs each on the new code and on a `git worktree` of the base commit (`testing_phase._checkout_old`), runs the tools in a scrubbed environment (no tokens or secrets), and posts the result to `POST /v1/runs/{id}/findings`. Bandit skips test files; detect-secrets skips lock files and virtual environments; pip-audit audits only the `==`-pinned lines of `requirements*.txt` files (at most 5, depth 3, copied out of the repo first) and says how many unpinned lines it did not check. Fingerprint = SHA-256 of tool, rule, file, text of the line (a hash for secrets, name and version for dependencies) and a repeat counter, so moved code stays "existing" and an edited line is "new". If the old code cannot be scanned, nothing is called new (`unknown`); on the first commit (empty base) everything is new. If the old code has no dependency files at all, every pip-audit finding is new (an old tree with a requirements file that cannot be audited stays unknown); checked live on 2026-11-20. A scanner that fails, or cannot be installed, is "could not run" with a reason, never a pass. Limits: 150 s per scan, 600 s in total, 500 findings sent. Server: `app/db/models/findings.py`, migration `0008` (RLS owner-read, cascade from `runs`), `app/services/findings.py`, `app/api/findings.py`, the "Security checks" section in `app/services/comment.py` (only new or unknown findings listed, 10 at most, old ones counted, fixed sentence that scanners find only some problems). Scanner rows are kept in `runs.summary["scanners"]`. Dashboard: `FindingsSection` in `frontend/src/components/RunView.tsx`, types and `getRunFindings` in `lib/api.ts`. Stored: rule, severity, place, a message of at most 400 characters, CWE, fingerprint, origin; never source code and never a secret value. Tests: `backend/tests/test_findings.py` (22), `action/tests/test_scanners.py` (29). CI job `action` installs bandit and detect-secrets so the real-tool tests run.
- **Order:** E1 scanners with diff-aware fingerprints (introduced vs existing) → E2 AI triage (text only) → E3 proof agent for command injection, SQL injection, path traversal → eval/exec and unsafe deserialization → fix-check (stretch).
- **Judge verdicts:** VERIFIED (lint passed, fails on current code with the canary signal, same result in 3 reruns), NOT_REPRODUCED, LIKELY_FALSE_POSITIVE (AI-judged, cites sanitising line), NEEDS_REVIEW.
- **Done when (P3b exit):** on a hand-made vulnerable sample repo each in-scope class is VERIFIED on the vulnerable version and not VERIFIED on the fixed version.

### F. LLM management (cross-cutting)
- **Provides:** `llm.complete(purpose, messages, budget_key)`; Groq then Gemini fallback, retry with backoff, circuit breaker; per-repo and per-user daily token caps; `llm_usage` table; cache by content hash.
- **Rules:** when a budget is exhausted the run ends with an explicit "no result, allowance used", never a fake pass. Repo code is data, not instructions.
- **Who builds it:** the first feature that needs the LLM builds the minimal version in `backend/app/services/llm/` and records it in the change log; later work extends it.
- **Done (first version, 2026-11-20):** `gemini.py` (REST `generateContent`, key in the `x-goog-api-key` header, model name checked before use, thinking switched off for 2.5 Flash models because it can use up the output limit, thinking tokens counted), retries and circuit breaker in `services/llm/__init__.py` (see 5.4), `retryable` flag on `LLMError`, summaries allowance in `llm_usage.py` and `api/context.py`. New settings in `core/config.py` and `.env.example`: `GEMINI_MODEL`, `LLM_RETRY_WAITS`, `LLM_TOTAL_BUDGET_SECONDS`, `LLM_BREAKER_FAILURES`, `LLM_BREAKER_SECONDS`, `LLM_DAILY_SUMMARY_TOKENS_PER_REPO`, `LLM_DAILY_SUMMARY_TOKENS_PER_USER`. No migration (`llm_usage` stores the model name, which tells the provider). Tests: `tests/test_llm_router.py`, more in `tests/test_summaries.py`. Privacy note: Google says free-tier Gemini content may be used to improve its products; only public repositories that opted in reach the AI, and Gemini is used only when Groq fails. A test cached from Gemini is stored under the Groq model's cache key, so a later run reuses it.
- **Done so far (minimal, built for C6):** `backend/app/services/llm/` with `complete(purpose, messages, budget_key, max_tokens)`, `LLMResult`, `LLMError`, the `get_llm` dependency and a Groq provider (`groq.py`). One key and model per `budget_key` (`default`, `summaries`); errors become safe messages; calls are logged without prompts or keys. Tests: `tests/test_llm.py`. Also built with D2: `app/services/llm_usage.py` and table `llm_usage` (migration `0007`), per-repo and per-owner daily token allowance. Still to build: cache by content hash for other purposes, and a live check of the Gemini fallback.

### G, H, I
Dashboard, notifications and operations are built alongside the features that feed them (each feature adds its own dashboard page and API). See the master plan sections G to I.

---

## 11. Requests board

When your feature needs something from another feature (a column, an endpoint field, a contract change), add a line here instead of editing their files. The owner resolves it and moves it to the change log.

Format: `- [open] 2026-11-18 | from <feature> to <feature> | what and why`

(none yet)

---

## 12. Change log

Newest first. Add a line when you change a contract, a table, a convention, or finish a milestone.

- 2026-11-20 | E, B, G | E1: security scanners. New table `findings` (migration `0008`; apply it only AFTER the new code is pushed), endpoints `POST /v1/runs/{id}/findings` (run token) and `GET /v1/runs/{id}/findings` (owner-only), shapes in `docs/api-contract.md`. New runner file `action/runner/scanners.py`, new Action input `run-scanners` (default `true`, env `PRSENTINEL_RUN_SCANNERS`); the Action installs the scanners itself, so the user's workflow needs no change. `build_comment` adds a "Security checks" section. The `/finish` response has the same shape; no contract version bump. Dashboard run page shows the findings. FakeServer in `action/tests/test_action.py` has `/findings` and its default env sets `PRSENTINEL_RUN_SCANNERS=false`. Feature E2 and E3: findings are rows in `findings` (`fingerprint`, `origin`); add triage and proof columns or tables with a new migration.
- 2026-11-20 | G, B | Link to one run page. Dashboard: the address `<dashboard>/#/runs/<run id>` opens that run's page after sign-in (hash address, so it works on any hosting and survives a refresh; `frontend/src/lib/route.ts`, `App.tsx`; the link is remembered across the GitHub sign-in trip); "Copy link" button on the run page. Backend: new optional setting `PUBLIC_DASHBOARD_URL` (default empty = no link); when set, `build_comment(run, repo, db, dashboard_url)` adds "View full details in the dashboard" under the commit line (only plain http/https addresses are accepted, `comment.dashboard_link`). Still owner-only: another user gets "not found". No migration, no contract change (the `/finish` response has the same shape). The dashboard is not hosted yet (open decision 2), so set `PUBLIC_DASHBOARD_URL` on Render once it is.
- 2026-11-20 | F, C, D | F: LLM router upgrade. Retries with waits, Gemini fallback for the `default` budget, circuit breaker, separate daily allowance for summaries (summaries no longer count in the test-generation allowance, and the other way round). `LLMError` has `retryable`; `llm_usage.used_today` now leaves out `file_summary`. New settings listed on card F. Feature D and E: nothing to change, keep calling `llm.complete(...)`; the result's `provider` and `model` may now be Gemini.
- 2026-11-20 | C, I | C4 to C6 checked on Render with the demo repo (Code map Ready and current, summary and context pack correct). Pinger set up with UptimeRobot (every 5 minutes on `/health`, email alert). No code change.
- 2026-11-20 | D | D5 to D8 accepted as checked after a live run on the demo repo (PR #7, `divide` now rounds to 2 decimals): Action log, PR comment and dashboard run page agree; mutation 3 of 3 caught. Repair (D5, D7) is covered by automated tests only. Lesson: a demo change that matches `main` gives "No changed Python functions", which is correct.
- 2026-11-20 | D, G | D8: dashboard run page. New owner-only endpoint `GET /v1/runs/{id}/functions` (shape in `docs/api-contract.md`), `app/services/run_view.py`, `app/api/run_view.py`, `frontend/src/components/RunView.tsx`, new types and `getRunFunctions` in `frontend/src/lib/api.ts`, "Details" button in `RepoPanel.tsx`, some CSS. No migration, no Action change. Other features: to show your own results on the run page, extend `run_view.build_run_view` and `RunView.tsx`.
- 2026-11-20 | D, G | Decisions: (1) the "done when" of feature D is reworded (no need to match the 996 v1 tests; `docs/v1-parity.md` is a reference), (2) the dashboard will show each run's per-function test results next to the PR comment and Action log, which all stay. Design of the run page to be agreed before building.
- 2026-11-20 | D | D7: `/results` accepts optional `test_code` (the repaired file, attempt 1 or 2) and stores it as the function's tests, so the `/generate` cache reuses repaired tests. Additive, no contract version bump, no migration. New file `docs/v1-parity.md` (v1 to v2 test map; also a Project doc). New tests: `action/tests/test_diff.py`, more in `action/tests/test_mutation.py`.
- 2026-11-20 | D | D6: `POST /v1/runs/{id}/mutation` (run token; shapes in `docs/api-contract.md`), runner file `action/runner/mutation.py`, called from `testing_phase.py` after the repair step. Stored in the existing `run_functions.mutation` column, no migration. The comment shows the counts and says the score is a lower bound. Only `modified` functions are checked, and only tests that pass on the plain old code count. FakeServer in `action/tests/test_action.py` has `/mutation`.
- 2026-11-19 | D | D5: `POST /v1/runs/{id}/repair` (run token; shapes in `docs/api-contract.md`), runner loop in `action/runner/testing_phase.py`, results for later rounds are posted to `/results` with `attempt` 1 or 2. New LLM purpose `test_repair` in `llm_usage`. No migration. Comment wording: an `ODD` test now reads "matches the new behaviour", and repaired tests are announced. FakeServer in `action/tests/test_action.py` now labels results like the server and has `/repair`.
- 2026-11-19 | D | D4: the PR comment (`app/services/comment.py`, `build_comment(run, repo, db)`) now lists each changed function with its test results: one summary per function (catches a change, unstable, unreliable, found no difference), details folded in `<details>`, only the newest repair round, the fixed sentence "Tests are AI-generated. A catching test is a hint, not proof.", and functions without a result shown as "not tested" or "no test result was reported", never as a pass. Names, reasons and messages go in code spans. No contract change (the `/finish` response has the same shape), no migration. Generator prompt is now `d2-2` (changed functions: write a test aimed at the old/new difference and at special-input promises of the old code).
- 2026-11-19 | D | D3: `POST /v1/runs/{id}/results` (run token; shapes in `docs/api-contract.md`), runner files `pytest_runner.py` and `testing_phase.py`, new Action input `run-tests` (default `true`, env `PRSENTINEL_RUN_TESTS`). No migration. The workflow that uses the Action must run `actions/setup-python`, then `pip install pytest` and the project's own dependencies, before the Action step. CI job `action` now installs pytest. Feature E: scanner output will extend `/results` or get its own endpoint; ask before changing the body.
- 2026-11-19 | D, F | D2: `POST /v1/runs/{id}/generate` (run token; shapes in `docs/api-contract.md`), `app/services/generator.py`, daily token allowance and table `llm_usage` (migration `0007`; apply it only AFTER the new code is pushed). New settings `LLM_DAILY_TOKENS_PER_REPO` (150000) and `LLM_DAILY_TOKENS_PER_USER` (300000). `GROQ_MODEL` default is now `openai/gpt-oss-120b` (test generation, the `default` budget key); summaries keep `GROQ_MODEL_SUMMARIES` = `openai/gpt-oss-20b`. Feature E: use `llm_usage.check_allowance` / `record` around your own model calls. Set `GROQ_API_KEY` on Render for generation.
- 2026-11-19 | D | D1: tables `run_functions` (one row per changed function of a run: status, test_code, cache_key, mutation) and `test_results` (per test: before/after result, label, rerun counts, diagnosis, message), migration `0006` (apply only AFTER the new code is pushed). `POST /v1/runs` now creates the `run_functions` rows. Labels and the rule classifier live in `app/services/classifier.py`.
- 2026-11-19 | C, F | C6: AI file summaries and the minimal LLM router. New package `backend/app/services/llm/` (`complete`, `LLMResult`, `LLMError`, `get_llm`; Groq only; see 5.4). New settings `GROQ_API_KEY_SUMMARIES` (separate key; no fallback to `GROQ_API_KEY`; without it summaries answer 503 "not switched on"), `GROQ_MODEL` and `GROQ_MODEL_SUMMARIES` (default `openai/gpt-oss-20b`; the first choice `llama-3.1-8b-instant` was retired by Groq, found on 2026-11-19 when the live call answered 404 `model_not_found`. Reasoning models get `reasoning_effort` low and their thinking hidden, and the output limit is 700 tokens because thinking can use part of it), `LLM_TIMEOUT_SECONDS` (20). New endpoint `POST /v1/repos/{id}/context/file/summary?path=`; `GET /v1/repos/{id}/context/file` now also returns `summary` and `summary_current`. No migration. Features D and E: call `llm.complete(...)` through a `get_llm` dependency, add your own `budget_key` in `app/services/llm/__init__.py` (own key and model per feature), and keep repo code in the user message only. Set `GROQ_API_KEY_SUMMARIES` in `backend/.env` and on Render; never commit it.
- 2026-11-19 | C | C5: dashboard "Code map" view and `GET /v1/repos/{id}/context/file?path=` (owner-only, symbols of one file). Other features: add your own sections to the repo panel next to `ContextView` in `RepoPanel.tsx`.
- 2026-11-19 | C | C4: `get_context_pack` (service, see 5.4) and the owner-only preview `GET /v1/repos/{id}/context/pack`. No table or contract change for the Action. Features D and E: call `get_context_pack` instead of reading `context_*` tables yourselves.
- 2026-11-19 | C | C3: call graph. `context_edges` gets `status` (`resolved` | `external` | `unresolved`) and `line` (migration `0005`; apply it only AFTER the new code is pushed). Edges are rebuilt from all stored files on every context commit (`store.rebuild_edges`). `kind` is `call` or `import`; `src_symbol` is the qualified enclosing function (`Class.method`, `<module>` for module level). `GET /v1/repos/{id}/context` now also returns `edges` (call counts per status). Features D and E: to find the callers of a function query `context_edges` where `dst_path` and `dst_symbol` match; unresolved links mean "unknown", not "none".
- 2026-11-19 | C | C2b: the runner keeps the server's code map up to date on every run (`action/runner/context_sync.py`: manifest, then facts for only the files the server needs in batches of at most `max_batch_files` and about 1.5 MB, then commit). A map failure (for example 409 or server error) prints a warning and the run still completes. Needs the server deployed with migration 0004 applied; until then runs show a warning and still work. Feature D and E: no contract change.
- 2026-11-19 | C | Context map server side (C2). Tables `context_state` (one row per repo: status empty/building/complete, built_from_sha, partial, repo_facts, pending_manifest), `context_files` (unique repo_id+path; content_hash, is_test, parse_error, symbols/imports/calls JSON, summary + summary_hash for C6) and `context_edges` (src, kind import/call, target_name, dst, resolved), migration `0004`, all cascade from `repos`, RLS owner-read. Endpoints `POST /v1/runs/{id}/context/{manifest,files,commit}` (run token) and `GET /v1/repos/{id}/context` (dashboard); shapes in `docs/api-contract.md`. New settings `MAX_CONTEXT_FILES` (500), `MAX_CONTEXT_BATCH_FILES` (100), `MAX_CONTEXT_UPLOAD_BYTES` (2000000). Features D and E: read the map through the `context_*` tables or, once built (C4), `get_context_pack`; do not write to them. Apply migration 0004 only AFTER the new code is pushed (Render runs `alembic upgrade head` at start).
- 2026-11-19 | C | C1: `action/runner/facts.py` (stdlib only) extracts per-file facts and a manifest; limits 500 files / 1 MB per file mark the scan partial. On this repo: 236 files, 1933 symbols, 6388 calls (672 unnameable), about 1 MB of facts, 4 s.
- 2026-11-19 | B | Fork pull requests verified on real GitHub: the job is green, a notice says the PR was skipped, no comment and no run are created. The check being green for a skipped PR is a known trade-off.
- 2026-11-19 | A | Dashboard: each repo has a "Runs and setup" panel (latest 50 runs from `GET /v1/repos/{id}/runs`, copyable workflow file). New optional setting `VITE_PUBLIC_API_URL`. `frontend/package-lock.json` regenerated on Linux (it was missing optional `@emnapi/*` entries, which made `npm ci` fail in CI). Other features: add run details to the panel by extending `RunsList` in `RepoPanel.tsx`.
- 2026-11-19 | B | Action v0 built and verified on real GitHub (`Ankxth/prsentinel-demo`). Use it with `uses: Ankxth/prsentinel/action@main`, `api-url: https://prsentinel-21ho.onrender.com`, workflow `permissions: id-token: write, contents: read, pull-requests: write`, and `actions/checkout` with `fetch-depth: 0`. Runner files: `action/runner/{diff,client,comment,main}.py`. Pull requests compare against the PR base commit, pushes against the `before` commit (first push falls back to the previous commit or the empty tree). `change_type` sent to the server uses `deleted` (v1 said `removed`). The runner retries 502/503/504 and network errors for about 150 seconds. Feature D and E: add your calls inside `runner/main.py` between start and finish, and finish with `could_not_run` and a reason when nothing could be checked.
- 2026-11-18 | B | Run lifecycle on the server. Table `runs` (migration `0003`, RLS owner-read; cascades from `repos`): repo_id, github_run_id, run_attempt (unique together per repo), sha, ref, event, status (`running` | `completed` | `failed` | `could_not_run`), started_at, finished_at, `summary` JSON (changed_files, changed_functions, reason; D and E may add keys). Endpoints `POST /v1/runs` (GitHub token), `POST /v1/runs/{id}/finish` (run token), `GET /v1/repos/{id}/runs`, `GET /v1/runs/{id}` (dashboard). Shapes in `docs/api-contract.md` (contract_version 1). Repo is found by GitHub's numeric `repository_id`; a renamed repo is updated automatically. New settings: `RUN_TOKEN_SECRET` (required for runs; generate a long random string), `RUN_TOKEN_TTL_SECONDS`, `GITHUB_OIDC_AUDIENCE` (default `prsentinel`), `MAX_FUNCTIONS_PER_RUN`. Feature D and E: add your endpoints to `docs/api-contract.md` and protect them with `current_run`.
- 2026-11-18 | A | `DELETE /v1/me` returns `{data_deleted, login_removed}`; deletes the user row (repos and anything cascading from them go too) and, if the optional `SUPABASE_SECRET_KEY` is set, the Supabase login record. Frontend "Delete my account" section. Rule: user-data tables must cascade from `users`. Dashboard dark-mode CSS fixed; Vite upgraded to 8 (0 audit warnings, needs Node 20.19+ or 22.12+).
- 2026-11-18 | A | Frontend: GitHub sign-in, header with sign-out, repos page (add with AI opt-in checkbox, list, remove). Needs `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` in `frontend/.env.local`. New dependency `@supabase/supabase-js`. Shared helper: `apiFetch` in `frontend/src/lib/api.ts`.
- 2026-11-18 | A | Login and repos backend. `GET /v1/me`; `GET/POST /v1/repos`, `GET/DELETE /v1/repos/{id}`. POST body is `{full_name, llm_opt_in}` and `llm_opt_in` must be true. Repo must be public (checked on GitHub's open API), one GitHub repo can belong to only one account, cap `MAX_REPOS_PER_USER` (default 5), other users' repos answer 404. New settings: `SUPABASE_URL` (set), `SUPABASE_JWT_SECRET` (only for old HS256 projects), `GITHUB_TOKEN` (optional, raises GitHub lookup limit), `MAX_REPOS_PER_USER`. New dependency: `pyjwt[crypto]`. Tokens are verified by signature, issuer, audience and expiry against Supabase's public keys.
- 2026-11-18 | A | Migration `0002` applied to the Supabase project. Supabase GitHub provider setup in progress by the user.
- 2026-11-18 | A | Tables `users` (id = Supabase auth id, github_login, github_id, avatar_url, is_admin) and `repos` (owner_user_id FK cascade, github_id unique, github_owner, github_name, default_branch, is_public, llm_opt_in default false, settings JSON) added in migration `0002` with RLS (own-rows read policies). Models in `app/db/models/accounts.py`. Other features: a repo row is found from an Action run through `repos.github_id` or `github_owner`/`github_name`.
- 2026-11-18 | Foundation | Supabase project created; baseline `0001` applied to it. Connection string convention changed to the Session pooler.
- 2026-11-18 | Foundation | Alembic chosen and set up: `app/db/{base,session}.py`, `migrations/` with empty baseline `0001`, `alembic.ini`, 7 migration tests; added sqlalchemy, alembic, psycopg to `requirements.txt`. No tables yet.
- 2026-11-18 | Foundation | Backend skeleton (`/health`, settings, CORS, 6 tests), frontend skeleton (Vite + React + TS calling `/health`), CI jobs `backend` and `frontend`, shared `context.md` created.
- 2026-11-18 | Foundation | v1 frozen under `bench/v1-capstone/` (996 tests pass); tag `v1-capstone`; v2 root layout and READMEs added; conflict markers removed from `.gitignore`.

---

## 13. How to use this file in a new chat

Paste or attach this file, then say which feature you are working on, for example: "Work on feature B. Read context.md first." Work from that feature's card, respect the contracts in section 5 and the data ownership in section 6, and before finishing: update the status board (9), the change log (12), and add any cross-feature needs to the requests board (11). Because this file is stored in the Project and in the repo (`docs/context.md`), keep both copies identical.
