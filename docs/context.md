# PRSentinel: Project Context

The one shared briefing for every chat and contributor working on PRSentinel.
Read this first. Then read only the feature card(s) you are working on.
Last updated: 2026-11-18 (sign-in tested, delete account added)

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

- **Action to server auth:** GitHub **OIDC token** (short-lived signed identity). Server verifies it against GitHub's published keys (JWKS) and reads `repository`, `sha`, `ref`, event. Fork behaviour must be verified while building.
- **Dashboard to server auth:** Supabase JWT from GitHub login.
- **No job queue in v1.** Work is short request/response steps. The Action retries while a free Render server wakes (sleeps after 15 min idle, about 1 min to wake). A pinger hits `/health` every ~10 min.
- **Action uploads the source it needs** (changed files, size-capped). The server does not depend on GitHub API limits; it only confirms a repo is public (cached).
- **Context lives in Postgres (JSONB).** No vector database at the start.
- **Server and runner talk through a versioned JSON contract** kept in `docs/api-contract.md` (to be created by feature B; see section 5).

---

## 4. Repository map and where things live

Repo: `github.com/Ankxth/prsentinel` (branch `main`).

```
prsentinel/
  backend/      FastAPI + uvicorn (BUILT: skeleton with /health, settings, tests)
    app/
      main.py            create_app() factory; add new routers here
      api/               one router file per area (health.py, me.py, repos.py exist)
      core/config.py     all settings (pydantic-settings); add new settings here
      core/auth.py       Supabase token check; get_claims, current_user dependencies
      services/github.py repo lookup on GitHub's open API (strict name check, one fixed host)
      db/                base.py (Base), session.py (engine, get_db), models/ (one file per area)
      services/          (planned) context engine, llm router, classifier, runs
      agents/            (planned) vuln_proof/
    migrations/          Alembic: env.py, versions/ (0001 empty baseline, 0002 users + repos)
    alembic.ini
    tests/
    requirements.txt     pinned
    .env.example
  frontend/     React + Vite + TypeScript (BUILT: GitHub sign-in, repos page; src/lib/api.ts, src/components/)
  action/       (planned) action.yml + runner/ (Python package run in the user's repo)
  bench/        evaluation data and scripts; v1 frozen under bench/v1-capstone/
  docs/         context.md (this file), api-contract.md (planned), research notes
  .github/workflows/ci.yml   jobs: v1-suite, backend, frontend
```

`bench/v1-capstone/` is the **frozen v1 project** (996 tests passing). It is a reference and evidence archive. Do not edit it. To reuse v1 code, copy the module into the new location and write new tests there.

### Where v1 code is meant to go (reuse map)

| v1 module (in `bench/v1-capstone/src/prsentinel/`) | New home | Feature |
|---|---|---|
| `diff_extractor.py` | `action/runner` | B / D |
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
- `get_context_pack(repo_id, file, function, token_budget) -> ContextPack` (provided by C, used by D and E)
- `llm.complete(purpose, messages, budget_key) -> LLMResult` (provided by F, used by C, D, E)
- `current_user` (BUILT, `app.core.auth`): FastAPI dependency for dashboard endpoints; returns the signed-in `User` row (creates it on first sign-in). Use it on every dashboard endpoint, and filter queries by `user.id` (see `app/api/repos.py` for the pattern, and `tests/test_repos.py` for the cross-user tests to copy).
- `current_repo(auth) -> Repo` auth dependency for Action calls (OIDC); to be provided by B in `core/oidc.py`.
- Test helpers (BUILT, `tests/conftest.py`): `client`, `make_token`, `github` (fake GitHub), in-memory database. New dashboard endpoints should be tested with these.

**5.5 Contract file:** request/response shapes for 5.1 live in `docs/api-contract.md` with a `contract_version` number. Action and server both send/check it. Additive changes only without a version bump.

---

## 6. Data ownership

One table has one owning feature. Others read it through that feature's service or through a documented query, and ask the owner for column changes.

| Table | Owner | Notes |
|---|---|---|
| `users`, `repos`, `quotas`, `audit_log` | A | `repos.settings` JSON holds per-repo options |
| `runs` | B | status lifecycle, commit sha, event, summary JSON |
| `context_files`, `context_edges` | C | every row records `built_from_sha` |
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
| Foundation (repo layout, CI, skeletons) | done | v1 frozen in `bench/v1-capstone`; backend `/health`, frontend skeleton, CI jobs added. Not pushed to GitHub yet |
| A Accounts and repositories | in progress | Backend done: tables applied to Supabase, token check, `GET /v1/me`, `GET/POST/DELETE /v1/repos` with tests (55 backend tests pass). Frontend sign-in and repos pages work end to end (user tested: sign in, add, remove, rows appear and disappear in Supabase). Delete-my-account built (59 backend tests). Left: setup-page workflow snippet (waits for B), cache the public check |
| B The Action | not started | Needs OIDC verification and `docs/api-contract.md` |
| C Context engine | not started | Needs `context_files` and `context_edges` tables |
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
- **Next steps:** setup-page workflow snippet (needs the Action's final shape from B); cache the public check.
- **Done when:** a user signs in locally, adds a public repo, sees the workflow snippet, deletes the repo and their data; tests prove one user cannot read another's repos.

### B. The Action
- **Purpose:** the GitHub Action and runner that executes inside the user's repo.
- **Depends on:** A (`repos`), contract file; OIDC verification on the server.
- **Provides:** `runs` table; `current_repo` dependency (OIDC); the five endpoints in 5.1; `action/action.yml`; `action/runner/`; `docs/api-contract.md`; PR comment posting (H).
- **Key files:** `backend/app/core/oidc.py`, `backend/app/api/runs.py`, `backend/app/services/run_service.py`, `action/`.
- **First steps:** OIDC verify with tests (including fork behaviour); `POST /v1/runs` returning changed functions; Action v0 that calls it from the demo repo and prints the result; then comment posting; retry logic for a waking server.
- **Done when (P1 exit):** opening a PR on the demo repo produces a comment and the run is visible on the live dashboard. Test step runs with a scrubbed environment (no token, no secrets); "could not run" is reported distinctly.

### C. Context engine
- **Purpose:** an incrementally updated map of each repo.
- **Depends on:** B (source arrives through runs), F (summaries).
- **Provides:** `context_files`, `context_edges`; `get_context_pack(...)`; `GET /v1/repos/{id}/context`; freshness (`built_from_sha`, "N commits behind").
- **Key files:** `backend/app/services/context_engine/` (indexer, symbols, graph, summaries, pack builder).
- **First steps:** file index with hashes; symbols via `ast`; import graph then call graph (best-effort, unresolved links flagged); context pack with token budget; module summaries regenerated only on hash change; dependents marked stale.
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
