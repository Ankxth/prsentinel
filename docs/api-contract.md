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

### Reserved for other features (not built yet)

| Endpoint | Owner | Purpose |
|---|---|---|
| `POST /v1/runs/{run_id}/generate` | D | tests for one function (context-aware) |
| `POST /v1/runs/{run_id}/results` | D, E | upload test results and scanner output |
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
