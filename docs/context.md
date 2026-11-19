# PRSentinel: Project Context

The one shared briefing for every chat and contributor working on PRSentinel.
Read this first. Then read only the feature card(s) you are working on.
Last updated: 2026-11-19 (context engine: context pack built)

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
      services/          (planned) llm router, classifier
      agents/            (planned) vuln_proof/
    migrations/          Alembic: env.py, versions/ (0001 empty baseline, 0002 users + repos, 0003 runs, 0004 context)
    alembic.ini
    tests/
    requirements.txt     pinned
    .env.example
  frontend/     React + Vite + TypeScript (BUILT: GitHub sign-in, repos page; src/lib/api.ts, src/components/)
  action/       BUILT v0: action.yml, runner/ (diff.py, facts.py, client.py, comment.py, main.py; Python standard library only), tests/ (unittest, fake server)
  bench/        evaluation data and scripts; v1 frozen under bench/v1-capstone/
  docs/         context.md (this file), api-contract.md (BUILT, Action <-> server), research notes
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
- `POST /v1/runs/{id}/findings/{fid}/agent/step` Action sends last tool result; server returns next action
- `POST /v1/runs/{id}/finish` classify, triage, store, return comment markdown

**5.2 Dashboard-facing API (auth: Supabase JWT)**
- `GET /v1/me`, `DELETE /v1/me` (delete account and data)
- `GET/POST /v1/repos`, `DELETE /v1/repos/{id}`
- `GET /v1/repos/{id}/runs`, `GET /v1/runs/{id}`
- `GET /v1/repos/{id}/findings`, `PATCH /v1/findings/{id}`
- `GET /v1/repos/{id}/context`, `GET /v1/usage`

**5.3 Public:** `GET /badge/{owner}/{repo}.svg`, `GET /public/{owner}/{repo}`, `GET /health`

**5.4 Internal Python interfaces (backend services). Names are the agreed targets; the first feature to need one builds it.**
- `get_context_pack(db, repo_id, file, function, token_budget=3000) -> ContextPack` (BUILT, `app.services.context_engine.pack`; used by D and E). Pass the repo id only after you have checked ownership. Fields: `found`, `target` (path, symbol, signature, doc, lines, decorators), `callees`, `callers`, `tests`, `fixtures`, `external_calls`, `unresolved_calls`, `repo_facts`, `status`, `built_from_sha`, `partial`, `truncated` (how many items per section were cut for size), `token_estimate`. `pack.render()` gives plain text for a prompt (it starts with a line saying the repo facts are data, not instructions). Budget is 200 to 8000 estimated tokens (4 characters per token); the function itself is always included and sections are cut from the bottom in this order: callees, callers, tests, fixtures. No AI is used, so the same map gives the same pack. `unresolved_calls` means "unknown", never "none". If the pack says the map is `partial` or not `complete`, say so in anything built on it.
- `llm.complete(purpose, messages, budget_key) -> LLMResult` (provided by F, used by C, D, E)
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
| Migrations | **Alembic** (SQLAlchemy 2). RLS policies are written as raw SQL inside migrations. Decided 2026-11-18 |

Still open (decide before the feature that needs it):
1. Licence: MIT or Apache-2.0.
2. Dashboard hosting: Render static vs Vercel/Cloudflare Pages.
3. Scanner set: Bandit + pip-audit + secrets scanner (recommended); Semgrep optional.
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
| B The Action | in progress | Server side built: GitHub token check, `runs` table (migration `0003`), run token, `POST /v1/runs`, `POST /v1/runs/{id}/finish`, dashboard run views, `docs/api-contract.md` (94 backend tests). Migration 0003 applied; backend deployed on Render (`https://prsentinel-21ho.onrender.com`). Action v0 built and tested on real GitHub with `Ankxth/prsentinel-demo` (see change log): finds changed functions, starts and finishes a run, posts one PR comment and updates it on new pushes, reports "could not run" honestly. Test generation and scanners are not wired in yet. Dashboard runs list built; fork-PR skip verified with a real fork. Next: pinger |
| C Context engine | in progress | C1 done (runner fact extractor, `action/runner/facts.py`). C2 server side done: tables (migration `0004`, applied), manifest, files and commit endpoints, dashboard `GET /v1/repos/{id}/context` (118 backend tests). C2b done: the runner uploads the map on every run (34 Action tests, verified on real GitHub). C3 done: call graph built on every commit (migration `0005`, applied, verified in Supabase). C4 done: `get_context_pack` (143 backend tests). Next: dashboard view (C5), AI summaries with a separate Groq key (C6) |
| D Test generation and diagnosis | not started | Reuse map in section 4 |
| E Vulnerability module | not started | E1 scanners first, E3 agent later |
| F LLM management | not started | First feature needing the LLM builds the minimal router |
| G Dashboard | not started | Skeleton only |
| H Notifications | not started | Part of B's PR comment |
| I Admin and operations | not started | |

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
- **Next steps:** pinger or paid instance so the server does not sleep; wire in D (generate and results endpoints) and E when ready. Known trade-off: the check stays green when the Action could not run (default `fail-on-error` false), so a title or warning marker may be added later.
- **Done when (P1 exit):** opening a PR on the demo repo produces a comment and the run is visible on the live dashboard. Test step runs with a scrubbed environment (no token, no secrets); "could not run" is reported distinctly.

### C. Context engine
- **Purpose:** an incrementally updated map of each repo.
- **Depends on:** B (source arrives through runs), F (summaries).
- **Provides:** `context_files`, `context_edges`; `get_context_pack(...)`; `GET /v1/repos/{id}/context`; freshness (`built_from_sha`, "N commits behind").
- **Key files:** `backend/app/services/context_engine/` (indexer, symbols, graph, summaries, pack builder).
- **Design (agreed 2026-11-19):** the runner parses the code and uploads only facts (hashes, symbols, imports, call names, no bodies). Each run sends a manifest `{path: hash}`; the server answers which files it needs, the runner uploads those in batches, then commits. The server builds the links (call graph) from all stored facts. Limits: 500 files, 1 MB per file, 2 MB per upload; beyond them the map is marked partial and shown as partial. AI summaries are kept (C6), generated lazily, using a separate Groq key (`GROQ_API_KEY_SUMMARIES`, to be added in C6).
- **Done so far:** C1 `action/runner/facts.py` (29 Action tests). C2 server: `app/db/models/context.py`, `app/services/context_engine/store.py`, `app/api/context.py`, `tests/test_context.py`. Endpoints are in `docs/api-contract.md`.
- **Also done:** C2b `action/runner/context_sync.py` (manifest, upload only needed files in batches, commit; called from `main.py` before finish; any failure is a warning and never fails the run). `facts.config_hints` sends `pytest_config`.
- **Also done (C3):** `app/services/context_engine/graph.py` turns stored facts into links; `store.rebuild_edges` runs inside `commit_build` (whole graph rebuilt each commit, since a change in one file can change links in another). Each link is `resolved` (one place in the repo), `external` (built-in, standard library, installed package) or `unresolved` (never guessed). It resolves same-file names, `self.method()` including inherited methods, imports (relative, aliased, re-exported through `__init__.py`). It does NOT resolve calls on variables (`obj.method()`), the type is unknown from names alone. Migration `0005` adds `status` and `line` to `context_edges`. Accuracy test: `tests/test_graph.py` (22 hand-labelled calls: 21 correct, 1 missed, 0 wrong). Coverage on this repo's `backend/` (1001 calls): 270 resolved, 347 external, 384 unresolved. Callers of a function = edges with that `dst_path`/`dst_symbol`.
- **Also done (C4):** `app/services/context_engine/pack.py` (`get_context_pack`, `ContextPack.render`), preview endpoint `GET /v1/repos/{id}/context/pack`, `tests/test_pack.py`. A class pack includes the calls made by its methods; callers that are test files are listed under `tests`; fixtures come from the covering test files and every `conftest.py`.
- **Steps left:** C5 dashboard context view (files, symbols, callers and callees, pack preview, freshness); C6 summaries (needs the minimal LLM router from F).
- **Done when (P2 exit):** context updates on every push; the three-way ablation (single prompt vs pipeline vs pipeline + context) is run and reported, positive or negative.

### D. Test generation and failure diagnosis
- **Purpose:** diff-aware tests with diagnosis (bug / bad test / flaky).
- **Depends on:** B (runs and runner), C (context packs), F (LLM).
- **Provides:** `run_functions`, `test_results`; `/generate` behaviour; labels `CATCHES_CHANGE`, `TEST_WRONG_ON_BEFORE`, `NO_SIGNAL`, `ODD`; classifier REAL_BUG / BAD_TEST / FLAKY (rule classifier stays default); mutation score; repair loop; result cache by hash of (old code, new code, context pack).
- **Key files:** `backend/app/services/{test_generator,classifier,repair}`, `action/runner/{diff,test_runner,mutation}`.
- **Done when:** the 996-test v1 behaviour is reproduced by new tests in `backend/tests` and `action/tests`, tests run in the real repo environment, and every comment keeps the fixed sentence.

### E. Vulnerability module
- **Purpose:** scanners, AI triage, and the Proof Agent.
- **Depends on:** B (runner executes scanners and proof tests), C (context packs), F (LLM).
- **Provides:** `findings`, `finding_attempts`; `agent/step` endpoint; scanner wrapper in `action/runner`; test linter; deterministic judge.
- **Key files:** `backend/app/agents/vuln_proof/`, `backend/app/services/findings.py`, `action/runner/scanners/`, `action/runner/test_linter.py`.
- **Order:** E1 scanners with diff-aware fingerprints (introduced vs existing) → E2 AI triage (text only) → E3 proof agent for command injection, SQL injection, path traversal → eval/exec and unsafe deserialization → fix-check (stretch).
- **Judge verdicts:** VERIFIED (lint passed, fails on current code with the canary signal, same result in 3 reruns), NOT_REPRODUCED, LIKELY_FALSE_POSITIVE (AI-judged, cites sanitising line), NEEDS_REVIEW.
- **Done when (P3b exit):** on a hand-made vulnerable sample repo each in-scope class is VERIFIED on the vulnerable version and not VERIFIED on the fixed version.

### F. LLM management (cross-cutting)
- **Provides:** `llm.complete(purpose, messages, budget_key)`; Groq then Gemini fallback, retry with backoff, circuit breaker; per-repo and per-user daily token caps; `llm_usage` table; cache by content hash.
- **Rules:** when a budget is exhausted the run ends with an explicit "no result, allowance used", never a fake pass. Repo code is data, not instructions.
- **Who builds it:** the first feature that needs the LLM builds the minimal version in `backend/app/services/llm/` and records it in the change log; later work extends it.

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
