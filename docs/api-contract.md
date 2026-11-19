# API contract: Action (runner) and server

`contract_version`: **1**

This file is the written agreement between the code that runs inside the user's GitHub Action
(`action/`) and the server (`backend/`). Both sides must follow it. Owner: feature B. Features D and E
add their endpoints here when they build them.

Rules
- Additive changes (new optional fields, new endpoints) keep the version. A breaking change bumps
  `contract_version`, and the server answers 400 to an Action that sends an older one.
- The server never trusts the request body for identity. Repository, commit and event come from
  GitHub's signed token.
- All bodies are JSON. All times are UTC (ISO 8601).
- Errors look like `{"detail": "plain sentence"}`. The Action should show `detail` to the user.

Status codes the Action must handle
| Code | Meaning | What the Action does |
|---|---|---|
| 200/201 | ok | continue |
| 400 | contract version mismatch | stop, print "update the Action" |
| 401 | token missing, expired or invalid | stop, print `detail` |
| 403 | repo not added, or not public | stop, print `detail` (tell the user to add the repo) |
| 409 | run already finished | stop quietly |
| 422 | bad body, or event not analysed | stop, print `detail` |
| 502/503/504, network error | server asleep or busy (free hosting wakes in about a minute) | retry with waiting, up to about 2 minutes |

## Authentication

Two kinds of token, both sent as `Authorization: Bearer <token>`.

1. **GitHub identity token** (only for `POST /v1/runs`). The Action requests it from GitHub with the
   audience `prsentinel`. The workflow needs `permissions: id-token: write`. The server checks the
   signature, issuer `https://token.actions.githubusercontent.com`, audience, expiry, and that
   `repository_visibility` is `public`. It finds the repo by `repository_id`.
2. **Run token** (every call after the run starts). Returned by `POST /v1/runs`, valid for one run,
   `expires_in` seconds (default 3600).

## Endpoints

### `POST /v1/runs` (auth: GitHub identity token)

Starts a run. Calling it again for the same GitHub run and attempt returns the same `run_id` with
status 200 and a fresh token.

Request
```json
{
  "contract_version": 1,
  "changed_files": 2,
  "changed_functions": [
    {"file": "app/a.py", "name": "add", "change_type": "modified"},
    {"file": "app/b.py", "name": "Cart.total", "change_type": "added"}
  ]
}
```
- `change_type`: `added` | `modified` | `deleted`
- at most 500 functions; `limits.max_functions` in the response says how many the server wants
  tested (the Action should send everything it found up to 500 and test no more than the limit)

Response `201` (or `200` if it already existed)
```json
{
  "contract_version": 1,
  "run_id": "uuid",
  "run_token": "jwt",
  "expires_in": 3600,
  "repo": "owner/name",
  "limits": {"max_functions": 50}
}
```
Only these events are analysed: `push`, `pull_request`, `workflow_dispatch`. Anything else is 422.
Which branches trigger is decided in the user's workflow file, not by the server.

### `POST /v1/runs/{run_id}/finish` (auth: run token)

Ends the run and returns the PR comment text.

Request
```json
{"status": "completed", "reason": null}
```
- `status`: `completed` | `failed` | `could_not_run`
- `reason`: short plain sentence, required in practice for `failed` and `could_not_run` (max 300 chars)
- `could_not_run` means nothing was checked (for example dependency install failed). It is **never**
  shown as a pass.

Response
```json
{
  "status": "completed",
  "comment_marker": "<!-- prsentinel-comment -->",
  "comment_markdown": "<!-- prsentinel-comment -->\n### PRSentinel\n..."
}
```
The Action posts `comment_markdown` on the pull request. If a comment containing `comment_marker`
already exists, it **updates that comment** instead of adding a new one.

### Context map (feature C). Auth: run token, run must still be `running`

The runner keeps the server's code map of the repository up to date in three calls. Only facts are
sent (names, signatures, first docstring lines, imports, call names), never function bodies. The
fact format is documented at the top of `action/runner/facts.py`.

`POST /v1/runs/{run_id}/context/manifest`
```json
{"files": {"cart.py": "<sha256 hex>", "tests/test_cart.py": "<sha256 hex>"},
 "total_files": 236, "skipped_files": 0}
```
Response: `{"need": ["cart.py"], "unchanged": 1, "removed": 0, "max_batch_files": 100}`.
`files` lists every `.py` file the runner will describe (at most 500 paths, hashes are 64 hex
characters). Files the server holds that are not in the list are deleted. `skipped_files > 0` marks
the map as partial. 409 if another run is updating the same repo (a stale or finished build is taken over).

`POST /v1/runs/{run_id}/context/files`
```json
{"files": [{"path": "cart.py", "hash": "<sha256 hex>", "is_test": false, "parse_error": null,
            "symbols": [], "imports": [], "calls": [{"in": "add", "name": "len", "line": 2}],
            "dynamic_calls": 0}]}
```
At most `max_batch_files` files and 2 MB per request (413 above that). Every path must be one the
server asked for and its hash must match the manifest (422 otherwise). A batch is saved all or nothing.

`POST /v1/runs/{run_id}/context/commit` with `{"pytest_config": false}` (a hint the runner knows).
Response: `{"status": "complete", "built_from_sha": "...", "partial": false, "python_files": 12}`.
409 lists how many files are still missing. Call it only after every requested file was uploaded. The commit also rebuilds the repo's call graph
(`context_edges`) from all stored files; nothing extra is sent for that.

### `POST /v1/runs/{run_id}/generate` (auth: run token, run must still be `running`)

Writes pytest tests for one changed function. The server never runs them and never stores source;
the runner sends the function's source.

Request: `{"file": "app/pricing.py", "name": "total" | "Cart.total", "old_code": "..." | null, "new_code": "..."}`
(`old_code` is null for an added function; each code field is at most 20000 characters).

Response 200: `{"function_id": "...", "status": "generated" | "could_not_generate", "reason": null | "...",
"test_code": "..." | null, "cached": false, "import_line": "from app.pricing import total"}`.
Tests import the real module, so the runner must run them from the repo root of the new code.
`could_not_generate` covers every AI-side problem (repo has not opted in, daily allowance used, model busy,
unusable answer); `reason` is plain text and safe to show. It is never a pass, and the runner should report
"no result" for that function and not retry. `cached: true` means the same old code, new code, context and
model were seen before in this repo and the stored tests were reused (no model call, no allowance used).

Errors: 404 the function is not one of this run's changes, 409 run already finished, 422 the function was deleted.

### `POST /v1/runs/{run_id}/results` (auth: run token, run must still be `running`)

Reports what happened when the runner ran the tests that `/generate` returned for one function. The
server labels the results with fixed rules (no AI): `CATCHES_CHANGE` (passes on the old code, fails on
the new), `TEST_WRONG_ON_BEFORE` (fails on both), `NO_SIGNAL` (passes on both), `ODD` (fails on the old,
passes on the new). A test that fails on the new code also gets a diagnosis: `REAL_BUG`, `BAD_TEST` or
`FLAKY` (the reruns passed at least once and failed at least once).

Request: `{"file": "a.py", "name": "f", "status": "tested" | "could_not_test", "reason": null | "...",
"attempt": 0, "tests": [{"name": "test_f", "before": "passed", "after": "failed", "rerun_passes": 0,
"rerun_fails": 3, "message": "assert 2 == 1" | null}]}`
- results are `passed` | `failed` | `error` | `skipped`; anything else counts as `error`, never as a pass
- `before` is the result on the old code. For an `added` function there is no old code: the server stores
  `skipped`, labels every test `NO_SIGNAL` and never calls a failure `REAL_BUG` (it says `BAD_TEST`, with a
  reason that the function is new)
- `rerun_*` count reruns of a failing test on the new code (the runner uses 3)
- `message` is the failure text from the new-code run; the server removes file paths and cuts it to 1500
  characters. It is untrusted text: show it in a code span
- `could_not_test` (or an empty `tests` list) is stored as "no result", never as a pass; `reason` is at most 300 characters
- `attempt` is the repair round (0 = the first tests); sending the same attempt again replaces it. At most 200 tests

Response 200: `{"function_id": "...", "status": "tested" | "could_not_test", "reason": null | "...",
"tests": [{"name": "test_f", "label": "CATCHES_CHANGE", "diagnosis": "REAL_BUG", "reason": "..."}]}`.
Errors: 404 not one of this run's changes, 409 run finished or no tests were generated for that function,
422 bad body.

### Reserved for other features (not built yet)

| Endpoint | Owner | Purpose |
|---|---|---|
| `POST /v1/runs/{run_id}/results` (scanner output) | E | extended later for scanner output |
| `POST /v1/runs/{run_id}/findings/{fid}/agent/step` | E | proof-agent ping-pong |

All of them authenticate with `Depends(current_run)` (see `backend/app/core/run_token.py`), which
returns the `Run` row only if the caller holds that run's token. Add each endpoint to this file with
its request and response shapes when you build it.

## Limits

- A run token lasts one hour. The Action should finish a run well before that.
- Request bodies are small by design. Upload source text only for the functions being tested, not
  whole repositories.

## Dashboard endpoints (browser only, not for the Action)

`GET /v1/repos/{id}/runs` (latest 50), `GET /v1/runs/{id}` and `GET /v1/repos/{id}/context`
(status `empty` | `building` | `complete`, `built_from_sha`, `latest_run_sha`, `partial`, `repo_facts`,
`edges` = number of calls per state `{resolved, external, unresolved}`, up to 1000 files with symbol and call counts). Auth is the user's Supabase sign-in,
and only the owner's runs are visible (others get 404).

`GET /v1/repos/{id}/context/pack?file=&function=&budget=3000` previews the context pack (see
`get_context_pack` in `context.md` section 5.4) for one function: `{found, pack, text}`; 404 if the repo is
not the caller's or the function is not in the map.

`GET /v1/repos/{id}/context/file?path=` lists one file's symbols (`name`, `kind`, `signature`, `doc`, `lines`) from the
code map, plus `summary` (text or null) and `summary_current` (false when the file changed after the summary was
written); 404 if the repo is not the caller's or the file is not in the map.

`POST /v1/repos/{id}/context/file/summary?path=` returns the file's AI summary `{path, summary, current, generated}`,
writing it first when there is none or the file changed (`generated: true`). Asking again for an unchanged file
costs nothing. Errors: 404 repo or file not found, 403 AI switched off for the repo, 422 nothing to summarize
(no readable functions or classes), 429 AI service busy, 502 AI service failed or sent an unreadable answer,
503 summaries not switched on for this server (no `GROQ_API_KEY_SUMMARIES`).
